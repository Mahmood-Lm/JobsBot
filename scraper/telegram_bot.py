import requests
import config

def send_message(chat_id, message):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id, 
        "text": message, 
        "parse_mode": "HTML",
        "disable_web_page_preview": True # Keeps the chat clean from massive link previews
    }
    response = requests.post(url, json=payload)
    if not response.ok:
        print(f"ERROR - Telegram API Error: {response.status_code} - {response.text}")
    return response.ok