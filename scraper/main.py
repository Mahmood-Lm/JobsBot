import os
import time
import json
import boto3
import config
from google import genai
from scraper import get_jobs, get_job_description
from telegram_bot import send_message

# 1. Initialize AWS and AI
dynamodb = boto3.resource('dynamodb', region_name='eu-central-1') 
jobs_table = dynamodb.Table(config.DYNAMODB_TABLE)
users_table = dynamodb.Table(os.getenv("USERS_TABLE", "Users-V2"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

def is_job_seen(user_job_id):
    try:
        response = jobs_table.get_item(Key={'user_job_id': user_job_id})
        return 'Item' in response
    except Exception:
        return False

def get_user_cv(chat_id):
    """Fetches the distilled CV profile from the Users table."""
    try:
        response = users_table.get_item(Key={'chat_id': str(chat_id)})
        return response.get('Item', {}).get('distilled_cv_profile')
    except Exception as e:
        print(f"Error fetching CV: {e}")
        return None

def lambda_handler(event, context):
    for record in event.get('Records', []):
        body = json.loads(record['body'])
        chat_id = str(body['chat_id'])
        search_url = body['search_url']
        
        cv_profile = get_user_cv(chat_id)
        current_jobs = get_jobs(search_url)
        new_jobs = []
        
        for job in current_jobs:
            user_job_id = f"{chat_id}_{job['id']}"
            if not is_job_seen(user_job_id):
                new_jobs.append((job, user_job_id))
        
        if not new_jobs:
            print("No new jobs found. Sleeping.")
            continue 

        # --- BATCHING INITIALIZATION ---
        final_message = f"🚨 <b>{len(new_jobs)} New Jobs Found!</b> 🚨\n\n"
        
        for job, user_job_id in new_jobs:
            job_segment = f"▪️ <b>{job['title']}</b> at {job['company']}\n"
            
            if cv_profile:
                print(f"DEBUG - Deep scraping {job['title']} for AI analysis...")
                job_desc = get_job_description(job['link'])
                
                time.sleep(12) # Prevent Rate Limits
                
                prompt = f"""
                You are an expert technical recruiter. Evaluate this job match.
                
                CANDIDATE PROFILE:
                {cv_profile}
                
                JOB DESCRIPTION:
                {job_desc}
                
                1. Give a Match Score from 1 to 10.
                2. Write a 1-2 sentence summary explaining WHY it is a good or bad match.
                
                Format EXACTLY like this:
                Score: [Number]/10
                Summary: [Your 1-2 sentences]
                """
                
                try:
                    response = ai_client.models.generate_content(
                        model='gemma-4-31b-it',
                        contents=prompt
                    )
                    # Wrap the AI output in Telegram Blockquotes
                    job_segment += f"🤖 <b>AI Match Analysis:</b>\n<blockquote>{response.text.strip()}</blockquote>\n"
                except Exception as e:
                    print(f"AI Generation Failed: {e}")
                    job_segment += "🤖 <i>AI Match Analysis currently unavailable.</i>\n"

            job_segment += f"<a href='{job['link']}'>Apply Here</a>\n\n"
            final_message += job_segment

        # --- SEND BATCHED MESSAGE & SAVE TO DB ---
        if send_message(chat_id, final_message.strip()):
            print(f"Sent batched alert for {len(new_jobs)} jobs. Saving to DB...")
            for _, user_job_id in new_jobs:
                jobs_table.put_item(Item={'user_job_id': user_job_id})
                
    return {'statusCode': 200, 'body': "Scrape completed successfully"}