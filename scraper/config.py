import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
# We will tell Terraform to pass the new SeenJobs-V2 table name here
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE", "SeenJobs-V2")