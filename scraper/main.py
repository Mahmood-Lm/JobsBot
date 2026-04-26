import json
import boto3
import config
from scraper import get_jobs
from telegram_bot import send_message

dynamodb = boto3.resource('dynamodb', region_name='eu-central-1') 
table = dynamodb.Table(config.DYNAMODB_TABLE)

def is_job_seen(user_job_id):
    """Checks if this specific user has already been alerted about this specific job."""
    try:
        response = table.get_item(Key={'user_job_id': user_job_id})
        return 'Item' in response
    except Exception as e:
        print(f"DynamoDB Read Error: {e}")
        return False

def lambda_handler(event, context):
    # SQS sends batches of messages in the 'Records' array
    for record in event.get('Records', []):
        # 1. Parse the JSON from the Dispatcher
        body = json.loads(record['body'])
        chat_id = str(body['chat_id'])
        search_url = body['search_url']
        
        print(f"Processing Scrape -> Chat ID: {chat_id} | URL: {search_url}")

        # 2. Get the jobs
        current_jobs = get_jobs(search_url)
        new_jobs = []
        
        # 3. Check for new jobs unique to THIS user
        for job in current_jobs:
            # The Magic Key: Combine them so User A and User B don't overlap!
            user_job_id = f"{chat_id}_{job['id']}"
            
            if not is_job_seen(user_job_id):
                new_jobs.append((job, user_job_id))
        
        if not new_jobs:
            print("No new jobs found for this subscription.")
            continue # Move to the next SQS record (if any)

        # 4. Format and send the message
        message = f"🚨 <b>Found {len(new_jobs)} New Jobs!</b> 🚨\n\n"
        for job, _ in new_jobs:
            message += f"▪️ <b>{job['title']}</b> at {job['company']}\n<a href='{job['link']}'>Apply Here</a>\n\n"

        if send_message(chat_id, message):
            print("Telegram message sent successfully. Saving to DB...")
            # 5. Save the unique user_job_id to DynamoDB
            try:
                with table.batch_writer() as batch:
                    for _, user_job_id in new_jobs:
                        batch.put_item(Item={'user_job_id': user_job_id})
            except Exception as e:
                print(f"DynamoDB Write Error: {e}")
                
    return {'statusCode': 200, 'body': "Scrape completed successfully"}