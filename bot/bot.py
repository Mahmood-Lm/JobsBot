import asyncio
import os
import boto3
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Load environment variables and initialize bot
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Connect to AWS Lambda in eu-central-1
lambda_client = boto3.client('lambda', region_name='eu-central-1')

@dp.message(CommandStart())
async def send_welcome(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Scrape Jobs Now", callback_data="trigger_scrape")]
    ])
    await message.answer("👋 Welcome to JobBot V2!\n\nWhat would you like to do?", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == 'trigger_scrape')
async def process_scrape_click(callback_query: types.CallbackQuery):
    await callback_query.message.answer("🚀 Waking up the AWS Lambda scraper...")
    try:
        # Asynchronously trigger the Lambda scraper without waiting for it to finish
        lambda_client.invoke(
            FunctionName='linkedin-scraper-function-v2',
            InvocationType='Event'
        )
    except Exception as e:
        await callback_query.message.answer(f"❌ Error starting scraper: {e}")
    await callback_query.answer()

# Main entry point to start the bot
async def main():
    print("Bot Brain is waking up...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())