import os
import sys
import time
import json
import boto3
import config
import urllib.parse
from google import genai
from playwright.sync_api import sync_playwright # <--- We moved Playwright here
from scraper import get_jobs, get_job_description
from telegram_bot import send_message
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shared.logging import get_logger

logger = get_logger('scraper')

# Initialize AWS and AI
dynamodb = boto3.resource('dynamodb', region_name='eu-central-1') 
jobs_table = dynamodb.Table(config.DYNAMODB_TABLE)
users_table = dynamodb.Table(os.getenv("USERS_TABLE", "Users-V2"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

def is_job_seen(user_job_id):
    try:
        response = jobs_table.get_item(Key={'user_job_id': user_job_id})
        return 'Item' in response
    except Exception as e:
        logger.error("Error checking job in DB", extra={"error": str(e), "user_job_id": user_job_id})
        return False

def get_user_cv(chat_id):
    try:
        response = users_table.get_item(Key={'chat_id': str(chat_id)})
        return response.get('Item', {}).get('distilled_cv_profile')
    except Exception as e:
        logger.error("Error fetching CV", extra={"error": str(e), "chat_id": str(chat_id)})
        return None

def lambda_handler(event, lambda_context):
    
    # 1. Boot up the single Master Browser for this Lambda execution
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--single-process", "--no-zygote"]
        ) 
        playwright_context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # 2. Process all incoming job alerts from the SQS queue
        for record in event.get('Records', []):
            body = json.loads(record['body'])
            chat_id = str(body['chat_id'])
            search_url = body['search_url']
            
            cv_profile = get_user_cv(chat_id)
            
            # Pass the open browser context to get_jobs!
            current_jobs = get_jobs(playwright_context, search_url) 
            new_jobs = []
            
            for job in current_jobs:
                user_job_id = f"{chat_id}_{job['id']}"
                if not is_job_seen(user_job_id):
                    new_jobs.append((job, user_job_id))
            
            if not new_jobs:
                logger.info("No new jobs found", extra={"chat_id": chat_id})
                continue 

            # --- SAFETY VALVE: THE BACKLOG SLICER ---
            MAX_JOBS = 10 # Max jobs to process in one batch to avoid Lambda timeouts and AI overuse
            if len(new_jobs) > MAX_JOBS:
                logger.warning(f"Backlog detected! Processing top {MAX_JOBS}, silently ignoring the remaining {len(new_jobs) - MAX_JOBS}.", extra={"chat_id": chat_id, "total_jobs": len(new_jobs), "processing_count": MAX_JOBS})
                # Save the ignored jobs to DynamoDB so they don't haunt us next time
                for _, user_job_id in new_jobs[MAX_JOBS:]: #TODO: could save the jobs to table without sending alerts if an error occurs
                    jobs_table.put_item(Item={'user_job_id': user_job_id})
                
                # Truncate the list for processing
                new_jobs = new_jobs[:MAX_JOBS]
            
            # Log all new jobs as structured event
            jobs_for_logging = [job for job, _ in new_jobs]
            logger.info("New jobs found", extra={
                "event": "new_jobs_found",
                "count": len(jobs_for_logging),
                "jobs": jobs_for_logging,
                "chat_id": chat_id
            })

            # --- PHASE 1: SCRAPING ---
            jobs_with_descriptions = []
            for job, user_job_id in new_jobs:
                # Check time safety valve before scraping
                time_left_ms = lambda_context.get_remaining_time_in_millis()
                if time_left_ms < 30000:
                    logger.critical(f"Insufficient Lambda time remaining. Stopping scraping to preserve time for message send.", extra={"time_remaining_ms": time_left_ms, "chat_id": chat_id, "user_job_id": user_job_id})
                    break

                scrape_start_time = time.time()
                job_desc = get_job_description(playwright_context, job['link'])
                scrape_end_time = time.time()
                
                logger.info(f"Job scraped successfully", extra={"user_job_id": user_job_id, "company": job['company'], "duration_seconds": round(scrape_end_time - scrape_start_time, 2), "chat_id": chat_id})
                
                jobs_with_descriptions.append({
                    'job': job,
                    'user_job_id': user_job_id,
                    'description': job_desc
                })
            
            # If no jobs were scraped, skip to next record
            if not jobs_with_descriptions:
                logger.warning("No jobs were scraped before timeout. Skipping AI evaluation.", extra={"chat_id": chat_id})
                continue

            # Extract search criteria from the URL to personalize the alert header
            parsed_url = urllib.parse.urlparse(search_url)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            search_keywords = query_params.get('keywords', [''])[0]
            search_location = query_params.get('location', [''])[0]

            header_text = f"{len(jobs_with_descriptions)} new jobs found" + (f" for {search_keywords}" if search_keywords else "") + (f" in {search_location}" if search_location else "")
            final_message = f"🚨 <b>{header_text}</b> 🚨\n\n"

            # --- PHASE 2: CONSOLIDATED AI REQUEST ---
            ai_results = {}  # Maps job index to {rating, summary}
            
            if cv_profile:
                # Build a consolidated prompt with all jobs
                jobs_for_ai = []
                for idx, item in enumerate(jobs_with_descriptions):
                    jobs_for_ai.append({
                        "title": item['job']['title'],
                        "company": item['job']['company'],
                        "description": item['description']
                    })
                
                consolidated_prompt = f"""
                Evaluate each of these job listings for the candidate independently. For each job, assess it based solely on how well it matches the candidate's profile, without comparing it to other jobs in the list.
                
                Return ONLY a JSON array where each object (in the same order as the input) has:
                - "rating": an integer from 1 to 10
                - "summary": a brief explanation explaining WHY it is a good or bad match (max 40 words) using a second-person tone ("You...")
                
                CANDIDATE PROFILE:
                {cv_profile}
                
                JOBS TO EVALUATE:
                {json.dumps(jobs_for_ai, indent=2)}
                """
                
                # Retry logic: attempt up to 2 times on quota/billing errors
                retry_count = 0
                max_retries = 1
                
                while retry_count <= max_retries:
                    try:
                        ai_start_time = time.time()
                        response = ai_client.models.generate_content(
                            model='gemini-3.1-flash-lite-preview',
                            contents=consolidated_prompt,
                            config={'response_mime_type': 'application/json'}
                        )
                        ai_end_time = time.time()
                        logger.info(f"AI generation completed", extra={"job_count": len(jobs_with_descriptions), "duration_seconds": round(ai_end_time - ai_start_time, 2), "chat_id": chat_id})
                        
                        # Parse the response into a dict indexed by job position
                        ai_response_array = json.loads(response.text)
                        for idx, result in enumerate(ai_response_array):
                            ai_results[idx] = {
                                'rating': result.get('rating', 'N/A'),
                                'summary': result.get('summary', 'Analysis unavailable.')
                            }
                        
                        break  # Success, exit retry loop
                        
                    except Exception as e:
                        error_msg = str(e).lower()
                        logger.error(f"AI generation failed", extra={"attempt": retry_count + 1, "error": error_msg, "chat_id": chat_id, "job_count": len(jobs_with_descriptions)})
                        
                        # Check if it's a quota/billing error
                        is_quota_error = "quota" in error_msg or "billing" in error_msg or "429" in error_msg
                        
                        if is_quota_error and retry_count < max_retries:
                            logger.info(f"Quota/billing error detected. Retrying...", extra={"attempt": retry_count + 1, "max_retries": max_retries, "chat_id": chat_id})
                            retry_count += 1
                            time.sleep(2)  # Brief delay before retry
                        else:
                            # Final failure - no more retries or not a quota error
                            logger.critical("AI evaluation failed and retries exhausted. Skipping AI for all jobs.", extra={"chat_id": chat_id, "job_count": len(jobs_with_descriptions)})
                            break

            # --- PHASE 3: MESSAGE FORMATTING ---
            for idx, item in enumerate(jobs_with_descriptions):
                job = item['job']
                user_job_id = item['user_job_id']
                
                job_segment = f"💼 <b>{job['title']}</b>\n🏢 {job['company']}\n"
                
                # Add AI results if available for this job
                if idx in ai_results:
                    rating = ai_results[idx]['rating']
                    summary = ai_results[idx]['summary']
                    job_segment += f"⭐ <b>Rating: {rating}/10</b>\n<blockquote>{summary}</blockquote>\n"
                elif cv_profile:
                    # AI was attempted but no result for this job
                    job_segment += "🤖 <i>AI Match Analysis currently unavailable.</i>\n"
                
                job_segment += f"🔗 <a href='{job['link']}'>Apply Here</a>\n\n"
                final_message += job_segment

            if send_message(chat_id, final_message.strip()):
                logger.info(f"Sent batched alert and saved jobs to DB", extra={"job_count": len(jobs_with_descriptions), "chat_id": chat_id})
                for item in jobs_with_descriptions:
                    jobs_table.put_item(Item={'user_job_id': item['user_job_id']})
                    
        # 3. Cleanly shut down the master browser
        browser.close()
                
    return {'statusCode': 200, 'body': "Scrape completed successfully"}