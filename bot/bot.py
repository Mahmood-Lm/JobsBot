import asyncio
import os
import time
import uuid
import boto3
import urllib.parse
import io
import PyPDF2
from google import genai
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

# --- NEW WEBHOOK IMPORTS ---
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUBSCRIPTIONS_TABLE = os.getenv("SUBSCRIPTIONS_TABLE", "Subscriptions-V2")
USERS_TABLE = os.getenv("USERS_TABLE", "Users-V2")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# --- PROMETHEUS METRICS ---
# This counts every time a user hits the /start menu
START_COMMAND_COUNTER = Counter('bot_start_commands_total', 'Total /start commands received')
# This counts every time a CV is uploaded
CV_UPLOAD_COUNTER = Counter('bot_cv_uploads_total', 'Total CVs uploaded for distillation')

# --- WEBHOOK CONFIGURATION ---
WEBHOOK_PATH = f"/webhook/{TELEGRAM_TOKEN}"
# The ALB DNS name injected by Terraform + the secret path
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "") + WEBHOOK_PATH

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
dynamodb = boto3.resource('dynamodb', region_name=os.getenv("AWS_DEFAULT_REGION", "eu-central-1"))
users_table = dynamodb.Table(USERS_TABLE)
subs_table = dynamodb.Table(SUBSCRIPTIONS_TABLE)

class SetupLink(StatesGroup):
    waiting_for_url = State()
    waiting_for_job_title = State()
    waiting_for_location = State()
    waiting_for_frequency = State()

# --- HELPER KEYBOARDS ---
def main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Add New Search", callback_data="add_search")],
        [InlineKeyboardButton(text="⚙️ Manage Subscriptions", callback_data="manage_links")]
    ])

def search_type_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Paste a LinkedIn Link", callback_data="choose_link")],
        [InlineKeyboardButton(text="🧙‍♂️ Step-by-Step Wizard", callback_data="choose_wizard")],
        [InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="back_to_main")]
    ])

def freq_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱️ Every 1 Hour", callback_data="freq_60")],
        [InlineKeyboardButton(text="⏱️ Every 4 Hours", callback_data="freq_240")],
        [InlineKeyboardButton(text="🌅 Once a Day", callback_data="freq_1440")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="back_to_main")]
    ])

def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="back_to_main")]
    ])

# --- 1. THE MAIN MENU ---
@dp.message(CommandStart())
async def send_welcome(message: types.Message, state: FSMContext):
    START_COMMAND_COUNTER.inc()  # Increment the /start command counter
    print(f"INFO - User {message.chat.id} started the bot. Welcome menu sent.", flush=True)
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
    
    try: await message.delete()
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
    
    try: await message.delete()
    except Exception: pass
    
    job_title = message.text.strip()
    await state.update_data(job_title=job_title)
    await state.set_state(SetupLink.waiting_for_location)
    
    await bot.edit_message_text(
        chat_id=message.chat.id, message_id=menu_msg_id,
        text=f"✅ **Got it! Title: {job_title}**\n\nWhere are you looking? *(e.g., Remote, Brazil, London)*",
        parse_mode="Markdown",
        reply_markup=back_keyboard()
    )

@dp.message(SetupLink.waiting_for_location)
async def capture_location(message: types.Message, state: FSMContext):
    data = await state.get_data()
    menu_msg_id = data.get('menu_msg_id')
    job_title = data.get('job_title')
    location = message.text.strip()
    
    try: await message.delete()
    except Exception: pass
    
    params = {
        "keywords": job_title,
        "location": location,
        "f_TPR": "r86400" 
    }
    encoded_params = urllib.parse.urlencode(params)
    generated_url = f"https://www.linkedin.com/jobs/search/?{encoded_params}"
    
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
        subs_table.put_item(Item={
            'subscription_id': str(uuid.uuid4()),
            'chat_id': str(callback_query.message.chat.id),
            'search_url': data['search_url'],
            'frequency_minutes': minutes,
            'last_scraped_timestamp': 0 
        })
        
        print(f"SUCCESS - User {callback_query.message.chat.id} created a new subscription (Freq: {minutes}m): {data['search_url']}", flush=True)
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
        response = subs_table.scan(
            FilterExpression="chat_id = :c",
            ExpressionAttributeValues={":c": chat_id}
        )
        items = response.get('Items', [])
        
        if not items:
            await callback_query.message.edit_text(
                "❌ You don't have any active tracking links.",
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
            
        keyboard_buttons.append([InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="back_to_main")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=keyboard)
        
    except Exception as e:
        await callback_query.message.edit_text(f"❌ Error fetching links.", reply_markup=back_keyboard())
        
    await callback_query.answer()

@dp.callback_query(F.data.startswith('del_'))
async def process_delete(callback_query: types.CallbackQuery):
    sub_id = callback_query.data.split('_', 1)[1]
    try:
        subs_table.delete_item(Key={'subscription_id': sub_id})
        await manage_links(callback_query) 
    except Exception as e:
        await callback_query.message.edit_text(f"❌ Error deleting link: {e}", reply_markup=back_keyboard())
        await callback_query.answer()

# --- 4. CV UPLOAD & AI DISTILLATION FLOW ---
@dp.message(F.document)
async def handle_cv_upload(message: types.Message):
    CV_UPLOAD_COUNTER.inc()  # Increment the CV upload counter
    if not message.document.file_name.lower().endswith('.pdf'):
        await message.answer("❌ Please upload your CV as a PDF file.")
        return
        
    processing_msg = await message.answer("⏳ Downloading and reading your CV...")
    
    try:
        file_info = await bot.get_file(message.document.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        
        print(f"INFO - Received CV upload from {message.chat.id}, parsing PDF...", flush=True)
        
        pdf_reader = PyPDF2.PdfReader(downloaded_file)
        raw_text = ""
        for page in pdf_reader.pages:
            raw_text += page.extract_text() or ""
            
        if not raw_text.strip():
            print(f"WARN - User {message.chat.id} uploaded an empty or unparseable PDF.", flush=True)
            await processing_msg.edit_text("❌ I couldn't extract any text from this PDF. It might be an image-based scan.")
            return
            
        await processing_msg.edit_text("⏳ Distilling your profile with AI...")
        
        prompt = f"""
        You are an expert tech recruiter. Read the following raw extracted text from a candidate's CV.
        Distill this into a dense, highly structured 300-word 'Candidate Profile'. 
        Focus strictly on: 
        - Total years of experience
        - Core technical skills and languages
        - Highest education
        - The specific types of roles they are best suited for.
        Do not include fluff or personal hobbies.
        
        Raw CV Text:
        {raw_text}
        """
        cv_start_time = time.time()
        response = ai_client.models.generate_content(
            model='gemma-4-31b-it',
            contents=prompt
        )
        cv_end_time = time.time()
        print(f"DEBUG - CV distillation took {cv_end_time - cv_start_time:.2f} seconds. (User: {message.chat.id})", flush=True)

        distilled_profile = response.text
        
        users_table.put_item(
            Item={
                'chat_id': str(message.chat.id),
                'distilled_cv_profile': distilled_profile
            }
        )
        
        await processing_msg.edit_text(
            "✅ **CV Successfully Processed & Saved!**\n\nI will use it to score your future job matches.",
            parse_mode="Markdown"
        )
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "Quota exceeded" in error_msg:
            await processing_msg.edit_text(
                "⚠️ **AI is cooling down!**\n\nPlease wait 60 seconds and upload your CV again.", 
                parse_mode="Markdown"
            )
        else:
            await processing_msg.edit_text(f"❌ An error occurred while processing your CV: {e}")


# --- STARTUP HOOK ---
async def on_startup(bot: Bot):
    print(f"Registering Webhook URL: {WEBHOOK_URL}")
    await bot.set_webhook(url=WEBHOOK_URL)


# --- MAIN WEB SERVER LOOP ---
def main():
    print("Bot Brain is waking up and starting Webhook Server...")
    
    # Register the startup hook
    dp.startup.register(on_startup)
    
    app = web.Application()

    # --- THE AWS ALB HEALTH CHECK ---
    # The Load balancer will ping this every 30 seconds to ensure the bot is alive.
    async def health_check(request):
        return web.Response(text="200 OK - Bot is alive!", status=200)
    
    # Attach the health check to the root path
    app.router.add_get('/', health_check)

    # 2. Prometheus Metrics Endpoint
    async def metrics_handler(request):
        return web.Response(
            body=generate_latest(), 
            headers={"Content-Type": CONTENT_TYPE_LATEST}
        )
    
    app.router.add_get('/metrics', metrics_handler)
    
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    
    # Register the handler
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    
    # Start server on Port 80
    web.run_app(app, host="0.0.0.0", port=80)

if __name__ == "__main__":
    main()