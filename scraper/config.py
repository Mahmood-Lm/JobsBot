import os
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE", "LinkedInJobs-V2") 
SEARCH_URL = "https://www.linkedin.com/jobs/search/?keywords=Python%20Developer&location=Italy&f_TPR=r86400"