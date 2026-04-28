import asyncio
import os
import uuid
import boto3
import urllib.parse
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUBSCRIPTIONS_TABLE = os.getenv("SUBSCRIPTIONS_TABLE", "Subscriptions-V2")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

dynamodb = boto3.resource('dynamodb', region_name='eu-central-1')
table = dynamodb.Table(SUBSCRIPTIONS_TABLE)

# Expanded FSM to include the Wizard steps
class SetupLink(StatesGroup):
    waiting_for_url = State()
    waiting_for_job_title = State()
    waiting_for_location = State()
    waiting_for_frequency = State()

# --- HELPER KEYBOARDS ---
def main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add New Search", callback_data="add_search")], # Renamed button
        [InlineKeyboardButton(text="📋 Manage Subscriptions", callback_data="manage_links")]
    ])

def search_type_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Paste a LinkedIn Link", callback_data="choose_link")],
        [InlineKeyboardButton(text="🪄 Step-by-Step Wizard", callback_data="choose_wizard")],
        [InlineKeyboardButton(text="🔙 Back to Menu", callback_data="back_to_main")]
    ])

def freq_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Every 1 Hour", callback_data="freq_60")],
        [InlineKeyboardButton(text="🚶 Every 4 Hours", callback_data="freq_240")],
        [InlineKeyboardButton(text="🐢 Once a Day", callback_data="freq_1440")],
        [InlineKeyboardButton(text="🔙 Cancel", callback_data="back_to_main")]
    ])

def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back to Menu", callback_data="back_to_main")]
    ])

# --- 1. THE MAIN MENU ---
@dp.message(CommandStart())
async def send_welcome(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Welcome to JobBot SaaS!\n\nWhat would you like to do?", 
        reply_markup=main_menu_keyboard()
    )

@dp.callback_query(F.data == 'back_to_main')
async def back_to_main(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.message.edit_text(
        "👋 Welcome back to the main menu!\n\nWhat would you like to do?",
        reply_markup=main_menu_keyboard()
    )
    await callback_query.answer()

# --- 2. THE CHOICE MENU ---
@dp.callback_query(F.data == 'add_search')
async def process_add_search(callback_query: types.CallbackQuery, state: FSMContext):
    # Save the menu message ID here so all branches can use it
    await state.update_data(menu_msg_id=callback_query.message.message_id)
    await callback_query.message.edit_text(
        "How would you like to set up your job search?",
        reply_markup=search_type_keyboard()
    )
    await callback_query.answer()

# --- BRANCH A: PASTE A LINK ---
@dp.callback_query(F.data == 'choose_link')
async def process_choose_link(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(SetupLink.waiting_for_url)
    await callback_query.message.edit_text(
        "🔗 **Please paste the LinkedIn Job Search URL.**\n\n"
        "*(Tip: Set your filters on LinkedIn, then copy the URL here.)*",
        parse_mode="Markdown",
        reply_markup=back_keyboard()
    )
    await callback_query.answer()

@dp.message(SetupLink.waiting_for_url)
async def capture_url(message: types.Message, state: FSMContext):
    data = await state.get_data()
    menu_msg_id = data.get('menu_msg_id')
    chat_id = message.chat.id
    
    try: await message.delete() # Ghost delete
    except Exception: pass
        
    if not message.text.startswith("http"):
        await bot.edit_message_text(
            chat_id=chat_id, message_id=menu_msg_id,
            text="❌ That doesn't look like a valid URL. Please try pasting it again.",
            reply_markup=back_keyboard()
        )
        return

    await state.update_data(search_url=message.text)
    await state.set_state(SetupLink.waiting_for_frequency)
    
    await bot.edit_message_text(
        chat_id=chat_id, message_id=menu_msg_id,
        text="⏱️ How often should I check this link for new jobs?", 
        reply_markup=freq_keyboard()
    )

# --- BRANCH B: THE WIZARD ---
@dp.callback_query(F.data == 'choose_wizard')
async def process_choose_wizard(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(SetupLink.waiting_for_job_title)
    await callback_query.message.edit_text(
        "💼 **What is the Job Title you are looking for?**\n\n*(e.g., Python Developer, Marketing Manager)*",
        parse_mode="Markdown",
        reply_markup=back_keyboard()
    )
    await callback_query.answer()

@dp.message(SetupLink.waiting_for_job_title)
async def capture_job_title(message: types.Message, state: FSMContext):
    data = await state.get_data()
    menu_msg_id = data.get('menu_msg_id')
    
    try: await message.delete() # Ghost delete
    except Exception: pass
    
    job_title = message.text.strip()
    await state.update_data(job_title=job_title)
    await state.set_state(SetupLink.waiting_for_location)
    
    await bot.edit_message_text(
        chat_id=message.chat.id, message_id=menu_msg_id,
        text=f"📍 **Got it! Title: {job_title}**\n\nWhere are you looking? *(e.g., Remote, Brazil, London)*",
        parse_mode="Markdown",
        reply_markup=back_keyboard()
    )

@dp.message(SetupLink.waiting_for_location)
async def capture_location(message: types.Message, state: FSMContext):
    data = await state.get_data()
    menu_msg_id = data.get('menu_msg_id')
    job_title = data.get('job_title')
    location = message.text.strip()
    
    try: await message.delete() # Ghost delete
    except Exception: pass
    
    # === THE MAGIC: BUILD THE URL ===
    # We add f_TPR=r86400 to ensure we only get jobs posted in the last 24 hours
    params = {
        "keywords": job_title,
        "location": location,
        "f_TPR": "r86400" 
    }
    encoded_params = urllib.parse.urlencode(params)
    generated_url = f"https://www.linkedin.com/jobs/search/?{encoded_params}"
    
    # Save the generated URL exactly as if they had pasted it
    await state.update_data(search_url=generated_url)
    await state.set_state(SetupLink.waiting_for_frequency)
    
    await bot.edit_message_text(
        chat_id=message.chat.id, message_id=menu_msg_id,
        text=f"✅ **Search configured!**\n\nTitle: {job_title}\nLocation: {location}\n\n⏱️ How often should I check for new jobs?",
        parse_mode="Markdown",
        reply_markup=freq_keyboard()
    )

# --- THE SHARED FINAL STEP ---
@dp.callback_query(SetupLink.waiting_for_frequency, F.data.startswith('freq_'))
async def capture_freq(callback_query: types.CallbackQuery, state: FSMContext):
    minutes = int(callback_query.data.split('_')[1])
    data = await state.get_data()
    
    try:
        table.put_item(Item={
            'subscription_id': str(uuid.uuid4()),
            'chat_id': str(callback_query.message.chat.id),
            'search_url': data['search_url'],
            'frequency_minutes': minutes,
            'last_scraped_timestamp': 0 
        })
        await callback_query.message.edit_text(
            "✅ **Link Successfully Added!**\n\nJobs will begin arriving shortly.", 
            parse_mode="Markdown",
            reply_markup=back_keyboard() 
        )
    except Exception as e:
        await callback_query.message.edit_text(f"❌ Database error: {e}", reply_markup=back_keyboard())
        
    await state.clear()
    await callback_query.answer()

# --- 3. MANAGE & DELETE FLOW ---
@dp.callback_query(F.data == 'manage_links')
async def manage_links(callback_query: types.CallbackQuery):
    chat_id = str(callback_query.message.chat.id)
    
    try:
        response = table.scan(
            FilterExpression="chat_id = :c",
            ExpressionAttributeValues={":c": chat_id}
        )
        items = response.get('Items', [])
        
        if not items:
            await callback_query.message.edit_text(
                "📋 You don't have any active tracking links.",
                reply_markup=back_keyboard()
            )
            return

        msg = f"📋 **Your Active Subscriptions ({len(items)}):**\n\n"
        keyboard_buttons = []
        
        for i, item in enumerate(items, 1):
            short_url = item['search_url'][:25] + "..."
            freq = item['frequency_minutes']
            msg += f"{i}. Every {freq}m: [View Link]({item['search_url']})\n"
            keyboard_buttons.append([
                InlineKeyboardButton(text=f"🗑️ Delete #{i} ({freq}m)", callback_data=f"del_{item['subscription_id']}")
            ])

        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="back_to_main")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=keyboard)
        
    except Exception as e:
        await callback_query.message.edit_text(f"❌ Error fetching links.", reply_markup=back_keyboard())
        
    await callback_query.answer()

@dp.callback_query(F.data.startswith('del_'))
async def process_delete(callback_query: types.CallbackQuery):
    sub_id = callback_query.data.split('_', 1)[1]
    try:
        table.delete_item(Key={'subscription_id': sub_id})
        await manage_links(callback_query) 
    except Exception as e:
        await callback_query.message.edit_text(f"❌ Error deleting link: {e}", reply_markup=back_keyboard())
        await callback_query.answer()

async def main():
    print("Bot Brain is waking up and listening to Telegram...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())