import os
import sys
import json
import boto3
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shared.logging import get_logger

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
sqs = boto3.client('sqs')

logger = get_logger('dispatcher')

TABLE_NAME = os.getenv("SUBSCRIPTIONS_TABLE", "Subscriptions-V2")
QUEUE_URL = os.getenv("SQS_QUEUE_URL")

table = dynamodb.Table(TABLE_NAME)

def lambda_handler(event, context):
    logger.info("Dispatcher starting")
    now = int(datetime.now(timezone.utc).timestamp())
    
    # 1. Grab all active subscriptions from DynamoDB
    try:
        response = table.scan()
        subscriptions = response.get('Items', [])
    except Exception as e:
        logger.error("Error reading DynamoDB", extra={"error": str(e)})
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
            logger.info("Triggering scrape", extra={"subscription_id": sub_id, "chat_id": chat_id, "frequency_minutes": freq_mins})
            
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
            
    logger.info("Dispatcher finished", extra={"tasks_sent": tasks_sent})
    return {"statusCode": 200, "body": f"Dispatched {tasks_sent} tasks"}