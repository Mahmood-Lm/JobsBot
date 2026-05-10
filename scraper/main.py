import os
import time
import json
import boto3
import config
import urllib.parse
from google import genai
from playwright.sync_api import sync_playwright # <--- We moved Playwright here
from scraper import get_jobs, get_job_description
from telegram_bot import send_message

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
        print(f"ERROR - Error checking job in DB: {e}")
        return False

def get_user_cv(chat_id):
    try:
        response = users_table.get_item(Key={'chat_id': str(chat_id)})
        return response.get('Item', {}).get('distilled_cv_profile')
    except Exception as e:
        print(f"ERROR - Error fetching CV: {e}")
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
                print("No new jobs found. Sleeping.")
                continue 

            # --- SAFETY VALVE: THE BACKLOG SLICER ---
            MAX_JOBS = 10 # Max jobs to process in one batch to avoid Lambda timeouts and AI overuse
            if len(new_jobs) > MAX_JOBS:
                print(f"Backlog detected! Processing top {MAX_JOBS}, silently ignoring the remaining {len(new_jobs) - MAX_JOBS}.")
                # Save the ignored jobs to DynamoDB so they don't haunt us next time
                for _, user_job_id in new_jobs[MAX_JOBS:]: #TODO: could save the jobs to table without sending alerts if an error occurs
                    jobs_table.put_item(Item={'user_job_id': user_job_id})
                
                # Truncate the list for processing
                new_jobs = new_jobs[:MAX_JOBS]
            
            # flag to track AI health
            ai_credits_exhausted = False

            # Extract search criteria from the URL to personalize the alert header
            parsed_url = urllib.parse.urlparse(search_url)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            search_keywords = query_params.get('keywords', [''])[0]
            search_location = query_params.get('location', [''])[0]

            header_text = f"{len(new_jobs)} new jobs found" + (f" for {search_keywords}" if search_keywords else "") + (f" in {search_location}" if search_location else "")
            final_message = f"🚨 <b>{header_text}</b> 🚨\n\n"
            
            for job, user_job_id in new_jobs:
                # --- SAFETY VALVE 2: THE TIME-AWARE LOOP ---
                time_left_ms = lambda_context.get_remaining_time_in_millis()
                if time_left_ms < 30000:
                    print(f"CRITICAL: Only {time_left_ms}ms left! Bailing out to send message before timeout.")
                    final_message += "⚠️ <i>Execution time running out. Remaining jobs skipped in this alert.</i>\n"
                    break # Break the loop, send what we have!

                job_segment = f"💼 <b>{job['title']}</b>\n🏢 {job['company']}\n"
                
                if cv_profile and not ai_credits_exhausted:
                    
                    scrape_start_time = time.time()
                    # Pass the open browser context to get_job_description!
                    job_desc = get_job_description(playwright_context, job['link'])
                    scrape_end_time = time.time()

                    print(f"DEBUG - Scraping {user_job_id} at {job['company']} took {scrape_end_time - scrape_start_time:.2f} seconds.")
                    
                    time.sleep(5) # AI rate limit safety net
                    
                    prompt = f"""
                    Evaluate this job match for the candidate. Return ONLY a JSON object with:
                    - "rating": an integer from 1 to 10.
                    - "summary": a brief explanation explaining WHY it is a good or bad match (max 40 words) using a second-person tone ("You...").
                    
                    CANDIDATE PROFILE:
                    {cv_profile}
                    
                    JOB DESCRIPTION:
                    {job_desc}
                    """
                    
                    try:
                        ai_start_time = time.time()
                        response = ai_client.models.generate_content(
                            model='gemini-3.1-flash-lite-preview',
                            contents=prompt,
                            config={'response_mime_type': 'application/json'}
                        )
                        ai_end_time = time.time()
                        print(f"DEBUG - AI generation took {ai_end_time - ai_start_time:.2f} seconds.")

                        ai_data = json.loads(response.text)
                        rating = ai_data.get('rating', 'N/A')
                        summary = ai_data.get('summary', 'Analysis unavailable.')

                        job_segment += f"⭐ <b>Rating: {rating}/10</b>\n<blockquote>{summary}</blockquote>\n"
                    except Exception as e:
                        error_msg = str(e).lower()
                        print(f"ERROR - AI Generation Failed: {error_msg}")
                        job_segment += "🤖 <i>AI Match Analysis currently unavailable.</i>\n"
                        
                        # If it's a billing/quota error, flip the switch to save the rest of the batch!
                        if "quota" in error_msg or "billing" in error_msg or "429" in error_msg:
                            print("CRITICAL: AI Credits empty! Bypassing AI for remaining jobs.")
                            ai_credits_exhausted = True

                elif ai_credits_exhausted:
                    # If credits are dead, skip the deep scrape and AI entirely to save time
                    job_segment += "🤖 <i>AI Match Analysis skipped (API Credits Exhausted).</i>\n"

                job_segment += f"🔗 <a href='{job['link']}'>Apply Here</a>\n\n"
                final_message += job_segment

            if send_message(chat_id, final_message.strip()):
                print(f"Sent batched alert for {len(new_jobs)} jobs to {chat_id}. Saving to DB...")
                for _, user_job_id in new_jobs:
                    jobs_table.put_item(Item={'user_job_id': user_job_id})
                    
        # 3. Cleanly shut down the master browser
        browser.close()
                
    return {'statusCode': 200, 'body': "Scrape completed successfully"}