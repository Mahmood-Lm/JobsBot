import os
import sys
import time
import json
import asyncio

import boto3
import config
import urllib.parse
from google import genai
from playwright.async_api import async_playwright
from scraper import get_jobs_async, get_job_description_async
from telegram_bot import send_message
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shared.logging import get_logger

logger = get_logger('scraper')

BROWSER_ARGS = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--single-process", "--no-zygote"]
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DESCRIPTION_TIMEOUT_MS = 5000
DESCRIPTION_CONCURRENCY = 2
GEMINI_TIMEOUT_SECONDS = 30

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

async def _scrape_description_job(semaphore, context, idx, job, user_job_id, timeout_ms):
    async with semaphore:
        start_time = time.time()
        try:
            description, timed_out = await asyncio.wait_for(
                get_job_description_async(context, job['link'], timeout_ms=timeout_ms),
                timeout=(timeout_ms / 1000.0)
            )
        except asyncio.TimeoutError:
            description, timed_out = "Description not available.", True
        except Exception as e:
            logger.error("Description scrape failed", extra={"error": str(e), "user_job_id": user_job_id})
            description, timed_out = "Description not available.", False

        duration_seconds = round(time.time() - start_time, 2)
        if timed_out:
            logger.warning("Description scrape timed out", extra={"user_job_id": user_job_id, "company": job.get("company"), "duration_seconds": duration_seconds})
        else:
            logger.info("Job description scraped", extra={"user_job_id": user_job_id, "company": job.get("company"), "duration_seconds": duration_seconds})

        return idx, job, user_job_id, description, timed_out

def lambda_handler(event, lambda_context):
    return asyncio.run(_lambda_handler_async(event, lambda_context))


async def _lambda_handler_async(event, lambda_context):
    # 1. Boot up the single Master Browser for this Lambda execution
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=BROWSER_ARGS
        )
        playwright_context = await browser.new_context(
            user_agent=USER_AGENT
        )
        
        # 2. Process all incoming job alerts from the SQS queue
        for record in event.get('Records', []):
            body = json.loads(record['body'])
            chat_id = str(body['chat_id'])
            search_url = body['search_url']
            
            cv_profile = get_user_cv(chat_id)
            
            # Pass the open browser context to get_jobs!
            current_jobs = await get_jobs_async(playwright_context, search_url) 
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
            time_left_ms = lambda_context.get_remaining_time_in_millis()
            if time_left_ms < 30000:
                logger.critical("Insufficient Lambda time remaining. Skipping description scraping to preserve time for message send.", extra={"time_remaining_ms": time_left_ms, "chat_id": chat_id})
                for job, user_job_id in new_jobs:
                    jobs_with_descriptions.append({
                        'job': job,
                        'user_job_id': user_job_id,
                        'description': None,
                        'description_available': False,
                        'description_timed_out': False,
                        'rating_value': None,
                        'summary': None
                    })
            else:
                worker_count = min(DESCRIPTION_CONCURRENCY, len(new_jobs))
                semaphore = asyncio.Semaphore(worker_count)

                tasks = [
                    asyncio.create_task(
                        _scrape_description_job(semaphore, playwright_context, idx, job, user_job_id, DESCRIPTION_TIMEOUT_MS)
                    )
                    for idx, (job, user_job_id) in enumerate(new_jobs)
                ]

                results = [None] * len(new_jobs)
                for task in asyncio.as_completed(tasks):
                    try:
                        idx, job, user_job_id, description, timed_out = await task
                    except Exception as e:
                        logger.error("Description task failed", extra={"error": str(e), "chat_id": chat_id})
                        continue

                    description_available = bool(description) and description != "Description not available." and not timed_out
                    results[idx] = {
                        'job': job,
                        'user_job_id': user_job_id,
                        'description': description if description_available else None,
                        'description_available': description_available,
                        'description_timed_out': timed_out,
                        'rating_value': None,
                        'summary': None
                    }

                for idx, item in enumerate(results):
                    if item is not None:
                        continue
                    job, user_job_id = new_jobs[idx]
                    results[idx] = {
                        'job': job,
                        'user_job_id': user_job_id,
                        'description': None,
                        'description_available': False,
                        'description_timed_out': False,
                        'rating_value': None,
                        'summary': None
                    }

                jobs_with_descriptions = results

                timed_out_count = sum(1 for item in jobs_with_descriptions if item.get('description_timed_out'))
                if timed_out_count:
                    logger.warning("Description timeouts detected", extra={"count": timed_out_count, "chat_id": chat_id})
            
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
            ai_results = {}  # Maps job index to {rating_value, summary, rating_text}
            
            if cv_profile:
                # Build a consolidated prompt with all jobs that have descriptions
                jobs_for_ai = []
                ai_job_indices = []
                for idx, item in enumerate(jobs_with_descriptions):
                    if not item.get('description_available'):
                        continue
                    ai_job_indices.append(idx)
                    jobs_for_ai.append({
                        "title": item['job']['title'],
                        "company": item['job']['company'],
                        "description": item['description']
                    })

                if not jobs_for_ai:
                    logger.warning("No job descriptions available for AI evaluation", extra={"chat_id": chat_id})
                else:
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
                            response = await asyncio.wait_for(
                                asyncio.to_thread(
                                    ai_client.models.generate_content,
                                    model='gemini-3.1-flash-lite',
                                    contents=consolidated_prompt,
                                    config={'response_mime_type': 'application/json'}
                                ),
                                timeout=GEMINI_TIMEOUT_SECONDS
                            )
                            ai_end_time = time.time()
                            logger.info(f"AI generation completed", extra={"job_count": len(jobs_for_ai), "duration_seconds": round(ai_end_time - ai_start_time, 2), "chat_id": chat_id})
                            
                            # Parse the response into a dict indexed by job position
                            ai_response_array = json.loads(response.text)
                            for idx, result in enumerate(ai_response_array):
                                rating = result.get('rating', 'N/A')
                                rating_value = None
                                if isinstance(rating, (int, float)):
                                    rating_value = int(rating)
                                elif isinstance(rating, str) and rating.isdigit():
                                    rating_value = int(rating)

                                original_idx = ai_job_indices[idx] if idx < len(ai_job_indices) else None
                                if original_idx is None:
                                    continue

                                ai_results[original_idx] = {
                                    'rating_value': rating_value,
                                    'rating_text': rating,
                                    'summary': result.get('summary', 'Analysis unavailable.')
                                }
                            
                            break  # Success, exit retry loop
                            
                        except asyncio.TimeoutError:
                            logger.error("AI generation timed out", extra={"timeout_seconds": GEMINI_TIMEOUT_SECONDS, "chat_id": chat_id, "job_count": len(jobs_for_ai)})
                            break
                        except Exception as e:
                            error_msg = str(e).lower()
                            logger.error(f"AI generation failed", extra={"attempt": retry_count + 1, "error": error_msg, "chat_id": chat_id, "job_count": len(jobs_for_ai)})
                            
                            # Check if it's a quota/billing error
                            is_quota_error = "quota" in error_msg or "billing" in error_msg or "429" in error_msg
                            
                            if is_quota_error and retry_count < max_retries:
                                logger.info(f"Quota/billing error detected. Retrying...", extra={"attempt": retry_count + 1, "max_retries": max_retries, "chat_id": chat_id})
                                retry_count += 1
                                time.sleep(2)  # Brief delay before retry
                            else:
                                # Final failure - no more retries or not a quota error
                                logger.critical("AI evaluation failed and retries exhausted. Skipping AI for all jobs.", extra={"chat_id": chat_id, "job_count": len(jobs_for_ai)})
                                break

            # --- PHASE 3: MESSAGE FORMATTING ---
            for idx, item in enumerate(jobs_with_descriptions):
                if idx in ai_results:
                    item['rating_value'] = ai_results[idx]['rating_value']
                    item['summary'] = ai_results[idx]['summary']
                    item['rating_text'] = ai_results[idx]['rating_text']

            jobs_for_message = sorted(
                jobs_with_descriptions,
                key=lambda item: item.get('rating_value') if item.get('rating_value') is not None else -1,
                reverse=True
            )

            for item in jobs_for_message:
                job = item['job']
                user_job_id = item['user_job_id']
                
                job_segment = f"💼 <b>{job['title']}</b>\n🏢 {job['company']}\n"
                
                # Add AI results if available for this job
                if item.get('rating_value') is not None and item.get('summary'):
                    rating = item['rating_value']
                    summary = item['summary']
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
        await browser.close()
                
    return {'statusCode': 200, 'body': "Scrape completed successfully"}