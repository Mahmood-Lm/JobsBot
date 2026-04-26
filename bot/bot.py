import asyncio
import os
import uuid
import boto3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUBSCRIPTIONS_TABLE = os.getenv("SUBSCRIPTIONS_TABLE", "Subscriptions-V2")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Connect to the exact database our Dispatcher reads from
dynamodb = boto3.resource('dynamodb', region_name='eu-central-1')
table = dynamodb.Table(SUBSCRIPTIONS_TABLE)

# 1. Define our State Machine (The bot's short-term memory)
class SetupLink(StatesGroup):
    waiting_for_url = State()
    waiting_for_frequency = State()

# 2. The Main Menu
@dp.message(CommandStart())
async def send_welcome(message: types.Message, state: FSMContext):
    await state.clear() # Clear any stuck memory
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add New Search Link", callback_data="add_link")],
        [InlineKeyboardButton(text="📋 My Subscriptions", callback_data="view_links")]
    ])
    await message.answer("👋 Welcome to JobBot SaaS!\n\nWhat would you like to do?", reply_markup=keyboard)

# 3. Step One: User clicks "Add Link"
@dp.callback_query(F.data == 'add_link')
async def process_add_link(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    
    # Put the bot into the "waiting for URL" state
    await state.set_state(SetupLink.waiting_for_url)
    await callback_query.message.answer(
        "🔗 **Please paste the LinkedIn Job Search URL.**\n\n"
        "*(Tip: Go to LinkedIn, set your filters like location and 'Past 24 Hours', then copy the URL here.)*",
        parse_mode="Markdown"
    )

# 4. Step Two: User pastes the URL
@dp.message(SetupLink.waiting_for_url)
async def capture_url(message: types.Message, state: FSMContext):
    if not message.text.startswith("http"):
        await message.answer("❌ That doesn't look like a valid URL. Please try again or type /cancel.")
        return

    # Save the URL into the bot's temporary memory
    await state.update_data(search_url=message.text)
    
    # Move to the next state
    await state.set_state(SetupLink.waiting_for_frequency)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Every 1 Hour", callback_data="freq_60")],
        [InlineKeyboardButton(text="🚶 Every 4 Hours", callback_data="freq_240")],
        [InlineKeyboardButton(text="🐢 Once a Day", callback_data="freq_1440")]
    ])
    await message.answer("⏱️ How often should I check this link for new jobs?", reply_markup=keyboard)

# 5. Step Three: User clicks the Frequency -> Save to DB!
@dp.callback_query(SetupLink.waiting_for_frequency, F.data.startswith('freq_'))
async def capture_freq(callback_query: types.CallbackQuery, state: FSMContext):
    # Extract the minutes from the button's callback_data (e.g., "freq_60" -> 60)
    minutes = int(callback_query.data.split('_')[1])
    
    # Retrieve the URL from the temporary memory
    data = await state.get_data()
    search_url = data['search_url']
    chat_id = str(callback_query.message.chat.id)
    
    # Generate a completely unique ID for this subscription
    sub_id = str(uuid.uuid4())
    
    try:
        # Write it to DynamoDB
        table.put_item(Item={
            'subscription_id': sub_id,
            'chat_id': chat_id,
            'search_url': search_url,
            'frequency_minutes': minutes,
            'last_scraped_timestamp': 0 # Setting this to 0 forces the Dispatcher to scrape it IMMEDIATELY
        })
        await callback_query.message.answer(
            "✅ **Link Successfully Added!**\n\nThe cloud dispatcher will pick this up within the next 60 seconds and run your first scrape.", 
            parse_mode="Markdown"
        )
    except Exception as e:
        await callback_query.message.answer(f"❌ Database error: {e}")
        
    await state.clear() # Clear the memory so the user can start over
    await callback_query.answer()

# 6. View Active Subscriptions
@dp.callback_query(F.data == 'view_links')
async def view_links(callback_query: types.CallbackQuery):
    chat_id = str(callback_query.message.chat.id)
    try:
        # Ask DynamoDB for all links belonging to this specific user
        response = table.scan(
            FilterExpression="chat_id = :c",
            ExpressionAttributeValues={":c": chat_id}
        )
        items = response.get('Items', [])
        
        if not items:
            await callback_query.message.answer("You don't have any active tracking links.")
        else:
            msg = f"📋 **Your Active Links ({len(items)}):**\n\n"
            for i, item in enumerate(items, 1):
                msg += f"{i}. Every {item['frequency_minutes']}m: [View Search]({item['search_url']})\n"
            await callback_query.message.answer(msg, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        await callback_query.message.answer("❌ Error fetching links from the database.")
        
    await callback_query.answer()

# Universal Cancel Command
@dp.message(Command("cancel"))
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🚫 Cancelled. Type /start to open the menu.")

async def main():
    print("Bot Brain is waking up and listening to Telegram...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())