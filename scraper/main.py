import os
import boto3
import config
from scraper import get_jobs
from telegram_bot import send_message

# Initialize DynamoDB client and table reference
dynamodb = boto3.resource('dynamodb', region_name='eu-central-1') 
table = dynamodb.Table(config.DYNAMODB_TABLE)

def load_seen_jobs():
    try:
        response = table.scan(ProjectionExpression="job_id")
        return {item['job_id'] for item in response.get('Items', [])}
    except Exception as e:
        print(f"Error reading DB: {e}")
        return set()

def lambda_handler(event, context):
    seen_job_ids = load_seen_jobs()
    current_jobs = get_jobs()
    new_jobs = [job for job in current_jobs if job["id"] not in seen_job_ids]
    
    if not new_jobs:
        msg = "Finished! No new jobs to report."
        send_message("✅ Scrape complete: No new jobs found.")
        return {'statusCode': 200, 'body': msg}

    message = f"🚨 <b>Found {len(new_jobs)} New Jobs!</b> 🚨\n\n"
    for job in new_jobs:
        message += f"▪️ <b>{job['title']}</b> at {job['company']}\n<a href='{job['link']}'>Apply Here</a>\n\n"

    if send_message(message):
        try:
            with table.batch_writer() as batch:
                for job in new_jobs:
                    batch.put_item(Item={'job_id': str(job["id"])})
        except Exception as e:
            print(f"Error saving to DynamoDB: {e}")
            
    return {'statusCode': 200, 'body': f"Sent {len(new_jobs)} jobs."}