import requests
import config

def send_message(message):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": config.CHAT_ID, "text": message, "parse_mode": "HTML"}
    response = requests.post(url, json=payload)
    return response.ok