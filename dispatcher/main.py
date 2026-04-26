import os
import json
import boto3
from datetime import datetime, timezone

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
sqs = boto3.client('sqs')

TABLE_NAME = os.getenv("SUBSCRIPTIONS_TABLE", "Subscriptions-V2")
QUEUE_URL = os.getenv("SQS_QUEUE_URL")

table = dynamodb.Table(TABLE_NAME)

def lambda_handler(event, context):
    print("Dispatcher waking up...")
    now = int(datetime.now(timezone.utc).timestamp())
    
    # 1. Grab all active subscriptions from DynamoDB
    try:
        response = table.scan()
        subscriptions = response.get('Items', [])
    except Exception as e:
        print(f"Error reading DynamoDB: {e}")
        return {"statusCode": 500, "body": "DB Error"}
    
    tasks_sent = 0
    
    # 2. Loop through users and see who is due for a scrape
    for sub in subscriptions:
        sub_id = sub.get('subscription_id')
        chat_id = sub.get('chat_id')
        url = sub.get('search_url')
        freq_mins = int(sub.get('frequency_minutes', 60))
        last_scraped = int(sub.get('last_scraped_timestamp', 0))
        
        # If current time is greater than or equal to their next scheduled scrape time...
        if now >= (last_scraped + (freq_mins * 60)):
            print(f"Triggering scrape for Subscription: {sub_id} (User: {chat_id})")
            
            # A. Send the task to the SQS Queue
            message_body = {
                "subscription_id": sub_id,
                "chat_id": chat_id,
                "search_url": url
            }
            sqs.send_message(
                QueueUrl=QUEUE_URL,
                MessageBody=json.dumps(message_body)
            )
            tasks_sent += 1
            
            # B. Update their last_scraped_timestamp so we don't scrape again immediately
            table.update_item(
                Key={'subscription_id': sub_id},
                UpdateExpression="SET last_scraped_timestamp = :now",
                ExpressionAttributeValues={':now': now}
            )
            
    print(f"Dispatcher finished. Sent {tasks_sent} tasks to SQS.")
    return {"statusCode": 200, "body": f"Dispatched {tasks_sent} tasks"}