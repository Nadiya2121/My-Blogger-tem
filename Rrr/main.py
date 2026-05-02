import os
import asyncio
import datetime
import uvicorn
import time
import aiohttp
import hmac
import hashlib
import urllib.parse
import secrets
import json
import copy
import re
import importlib

try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from fastapi import FastAPI, Body, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from pydantic import BaseModel
from typing import Optional

# ==========================================
# 1. Configuration & Global Variables
# ==========================================
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URI")
OWNER_ID = int(os.getenv("ADMIN_ID", "0"))
APP_URL = os.getenv("APP_URL")

CHANNEL_ID = os.getenv("CHANNEL_ID", "") 
DUMP_CHANNEL_ID = os.getenv("DUMP_CHANNEL_ID", "") 

ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123") 
BOT_USERNAME = "NetfilxProMaxbot"
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "") 

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()
security = HTTPBasic()
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

client = AsyncIOMotorClient(MONGO_URL)
db = client['movie_database']

admin_cache = set([OWNER_ID]) 
banned_cache = set() 

CACHE_DATA = {
    "trending": {"time": 0, "data": []},
    "list": {}
}
USER_REQUEST_RATES = {}
CACHE_TTL = 120 

class AdminStates(StatesGroup):
    waiting_for_bcast = State()
    waiting_for_reply = State()
    waiting_for_photo = State()
    waiting_for_title = State()
    waiting_for_quality = State() 

class AdvancedUpload(StatesGroup):
    waiting_for_tmdb_query = State()
    waiting_for_manual_photo = State()
    waiting_for_manual_title = State()
    waiting_for_language = State() 
    waiting_for_custom_language = State() 
    selecting_qualities = State()
    waiting_for_custom_quality = State()
    waiting_for_files = State()

async def load_admins():
    admin_cache.clear()
    admin_cache.add(OWNER_ID)
    async for admin in db.admins.find():
        admin_cache.add(admin["user_id"])

async def load_banned_users():
    banned_cache.clear()
    async for b_user in db.banned.find():
        banned_cache.add(b_user["user_id"])

async def init_db():
    await db.movies.create_index([("title", "text")])
    await db.movies.create_index("title")
    await db.movies.create_index("created_at")
    await db.auto_delete.create_index("delete_at")
    await db.payments.create_index("trx_id", unique=True)
    await db.user_unlocks.create_index("user_id")

def validate_tg_data(init_data: str) -> bool:
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        hash_val = parsed_data.pop('hash', None)
        auth_date = int(parsed_data.get('auth_date', 0))
        if not hash_val or time.time() - auth_date > 86400: return False
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return calculated_hash == hash_val
    except Exception: return False

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASS)
    if not (correct_username and correct_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password", headers={"WWW-Authenticate": "Basic"})
    return True

async def auto_delete_worker():
    while True:
        try:
            now = datetime.datetime.utcnow()
            now_ts = time.time()
            expired_rates = [u for u, t in USER_REQUEST_RATES.items() if now_ts - t > 10]
            for u in expired_rates:
                del USER_REQUEST_RATES[u]
            
            expired_msgs = db.auto_delete.find({"delete_at": {"$lte": now}})
            async for msg in expired_msgs:
                try: await bot.delete_message(chat_id=msg["chat_id"], message_id=msg["message_id"])
                except Exception: pass
                await db.auto_delete.delete_one({"_id": msg["_id"]})
        except Exception: pass
        await asyncio.sleep(60)

async def upload_to_telegraph(file_id: str) -> str:
    # Bypass Telegraph for permanent stability
    return None

# ==========================================
# Telegram Bot Commands
# ==========================================
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if uid in banned_cache: 
        return await message.answer("🚫 <b>আপনাকে এই বট থেকে স্থায়ীভাবে ব্যান করা হয়েছে।</b>", parse_mode="HTML")
        
    await state.clear()
    now = datetime.datetime.utcnow()
    user = await db.users.find_one({"user_id": uid})
    
    if not user:
        args = message.text.split(" ")
        if len(args) > 1 and args[1].startswith("ref_"):
            try:
                referrer_id = int(args[1].split("_")[1])
                if referrer_id != uid:
                    await db.users.update_one({"user_id": referrer_id}, {"$inc": {"refer_count": 1}})
                    ref_user = await db.users.find_one({"user_id": referrer_id})
                    if ref_user and ref_user.get("refer_count", 0) % 1 == 0:
                        current_vip = max(ref_user.get("vip_until", now), now)
                        await db.users.update_one({"user_id": referrer_id}, {"$set": {"vip_until": current_vip + datetime.timedelta(days=1)}})
                        try: await bot.send_message(referrer_id, "🎉 <b>অভিনন্দন!</b> আপনার রেফার লিংকে ১ জন জয়েন করেছে। আপনাকে ২৪ ঘণ্টার জন্য <b>VIP</b> দেওয়া হয়েছে!", parse_mode="HTML")
                        except: pass
            except Exception: pass

        await db.users.insert_one({
            "user_id": uid, "first_name": message.from_user.first_name, "joined_at": now,
            "refer_count": 0, "coins": 0, "streak": 0, "last_checkin": now - datetime.timedelta(days=2), "last_quiz": now - datetime.timedelta(days=2), "vip_until": now - datetime.timedelta(days=1)
        })
    else:
        await db.users.update_one({"user_id": uid}, {"$set": {"first_name": message.from_user.first_name}})
    
    if uid in admin_cache:
        kb = [[types.InlineKeyboardButton(text="🎬 Watch Now (ওপেন অ্যাপ)", web_app=types.WebAppInfo(url=APP_URL))]]
        markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
        text = (
            "👋 <b>হ্যালো অ্যাডমিন!</b>\n\n"
            "⚙️ <b>কমান্ড:</b>\n"
            "🔸 <b>স্মার্ট আপলোড (TMDB):</b> <code>/addmovie</code> 🔥\n"
            "🔸 প্যানেল: <code>/addadmin ID</code> | <code>/deladmin ID</code> | <code>/adminlist</code>\n"
            "🔸 ডাইরেক্ট লিংক: <code>/addlink লিংক</code> | <code>/dellink লিংক</code> | <code>/seelinks</code>\n"
            "🔸 ভাসমান লিংক: <code>/settg লিংক</code> | <code>/set18 লিংক</code>\n"
            "🔸 পেমেন্ট নাম্বার সেট: <code>/setbkash নাম্বার</code> | <code>/setnagad নাম্বার</code>\n"
            "🔸 প্রোটেকশন: <code>/protect on</code> বা <code>/protect off</code>\n"
            "🔸 অটো-ডিলিট টাইম: <code>/settime [মিনিট]</code>\n"
            "🔸 স্ট্যাটাস: <code>/stats</code> | ব্রডকাস্ট: <code>/cast</code>\n"
            "🔸 মুভি ডিলিট: <code>/delmovie মুভির নাম</code>\n"
            "🔸 ব্যান: <code>/ban ID</code> | আনব্যান: <code>/unban ID</code>\n"
            "🔸 VIP দিন: <code>/addvip ID দিন</code> | VIP বাতিল: <code>/removevip ID</code>\n\n"
            f"🌐 <b>ওয়েব অ্যাডমিন প্যানেল:</b> <a href='{APP_URL}/admin'>এখানে ক্লিক করুন</a>\n"
            "<i>লগিন: admin / admin123</i>\n\n"
            "📥 <b>কুইক আপলোড: মুভি অ্যাড করতে সরাসরি ভিডিও বা ফাইল পাঠান।</b>"
        )
        await message.answer(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
    else: 
        TUTORIAL_VIDEO_LINK = "https://t.me/HowtoDowlnoad/41" 
        WELCOME_IMAGE_URL = "https://files.catbox.moe/micrnz.jpg"
        kb = [
            [types.InlineKeyboardButton(text="🎬 Watch Now (অ্যাপ ওপেন করুন)", web_app=types.WebAppInfo(url=APP_URL))],
            [types.InlineKeyboardButton(text="📖 কিভাবে মুভি দেখবেন? (Tutorial)", url=TUTORIAL_VIDEO_LINK)]
        ]
        markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
        caption = (
            f"👋 <b>স্বাগতম {message.from_user.first_name}!</b>\n\n"
            "<b>👇 কিভাবে এই বটটি ব্যবহার করবেন?</b>\n\n"
            "🔸 <b>ধাপ ১:</b> নিচের <b>'🎬 Watch Now'</b> বাটনে ক্লিক করুন।\n"
            "🔸 <b>ধাপ ২:</b> অ্যাপ থেকে আপনার পছন্দের মুভিটি সার্চ বা সিলেক্ট করুন।\n"
            "🔸 <b>ধাপ ৩:</b> মুভির কোয়ালিটি সিলেক্ট করে Download বাটনে ক্লিক করুন।\n"
            "🔸 <b>ধাপ ৪:</b> ১০ সেকেন্ডের এড দেখে আনলক করলেই মুভি সরাসরি আপনার ইনবক্সে চলে আসবে!\n\n"
            "💡 <i>বিস্তারিত বুঝতে নিচের <b>'কিভাবে মুভি দেখবেন?'</b> বাটনে ক্লিক করে ভিডিওটি দেখে নিন।</i>"
        )
        try: await message.answer_photo(photo=WELCOME_IMAGE_URL, caption=caption, reply_markup=markup, parse_mode="HTML")
        except Exception: await message.answer(caption, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)

@dp.message(lambda m: m.chat.type == "private" and m.from_user.id not in admin_cache)
async def forward_to_admin(m: types.Message):
    try:
        builder = InlineKeyboardBuilder()
        builder.button(text="✍️ রিপ্লাই দিন", callback_data=f"reply_{m.from_user.id}")
        for ad_id in admin_cache:
            try: await bot.send_message(ad_id, f"📩 <b>New Message from <a href='tg://user?id={m.from_user.id}'>{m.from_user.first_name}</a></b>:\n\n{m.text or 'Media file'}", parse_mode="HTML", reply_markup=builder.as_markup())
            except Exception: pass
    except Exception: pass

# ==========================================
# Smart Batch Uploading & TMDB
# ==========================================
def get_language_keyboard():
    builder = InlineKeyboardBuilder()
    langs = ["🇧🇩 Bangla", "🇮🇳 Hindi", "🇺🇸 English", "🎙 Bangla Dubbed", "🎙 Hindi Dubbed", "✍️ Custom"]
    for l in langs: builder.button(text=l, callback_data=f"lang_{l}")
    builder.adjust(2)
    return builder.as_markup()

def get_batch_keyboard(selected_list: list):
    builder = InlineKeyboardBuilder()
    options = ["480p", "720p", "1080p", "4K", "Dual Audio", "Ep 01", "Ep 02", "Ep 03", "Ep 04", "Ep 05", "Ep 06", "Ep 07", "Ep 08", "Ep 09", "Ep 10", "✍️ Custom"]
    for opt in options:
        mark = "✅ " if opt in selected_list else ""
        builder.button(text=f"{mark}{opt}", callback_data=f"bup_{opt}")
    builder.button(text="✅ ডান (Done)", callback_data="bup_DONE")
    builder.adjust(3)
    return builder.as_markup()

@dp.message(Command("addmovie"))
async def start_advanced_upload(m: types.Message, state: FSMContext):
    if m.from_user.id not in admin_cache: return
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 TMDB থেকে (অটো)", callback_data="up_tmdb")
    builder.button(text="📁 Manual (ম্যানুয়াল)", callback_data="up_manual")
    await m.answer("🎬 <b>আপনি কিভাবে মুভি আপলোড করতে চান?</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("up_"))
async def choose_upload_method(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id not in admin_cache: return
    await c.answer()
    method = c.data.split("_")[1]
    if method == "tmdb":
        if not TMDB_API_KEY: return await c.answer("⚠️ TMDB API Key কনফিগার করা নেই!", show_alert=True)
        await state.set_state(AdvancedUpload.waiting_for_tmdb_query)
        await c.message.edit_text("🔍 <b>মুভি/সিরিজের নাম অথবা TMDB/IMDB লিংক লিখে পাঠান:</b>", parse_mode="HTML")
    else:
        await state.set_state(AdvancedUpload.waiting_for_manual_photo)
        await c.message.edit_text("🖼 <b>প্রথমে মুভির একটি সুন্দর পোস্টার (Photo) সেন্ড করুন:</b>", parse_mode="HTML")

async def fetch_and_send_tmdb_details(m: types.Message, tmdb_id: str, media_type: str, state: FSMContext, user_id: int):
    url = f"https://api.tmdb.org/3/{media_type}/{tmdb_id}?api_key={TMDB_API_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                res = await resp.json()
                title = res.get("title") or res.get("name")
                release_date = res.get("release_date") or res.get("first_air_date") or ""
                year = release_date[:4] if release_date else ""
                if year: title = f"{title} ({year})"
                poster_path = res.get("poster_path")
                genres = [g["name"] for g in res.get("genres", [])]
                if not poster_path: return await bot.send_message(user_id, "⚠️ এই মুভির কোনো পোস্টার নেই!")
                poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
                try:
                    await bot.send_photo(chat_id=user_id, photo=poster_url, caption=f"✅ <b>{title}</b>\nক্যাটাগরি: {', '.join(genres)}")
                    await state.update_data(title=title, photo_id=poster_url, genres=genres)
                    await state.set_state(AdvancedUpload.waiting_for_language)
                    await bot.send_message(user_id, "🗣 <b>এই মুভিটির ভাষা (Language) সিলেক্ট করুন:</b>", reply_markup=get_language_keyboard(), parse_mode="HTML")
                except Exception: await bot.send_message(user_id, "⚠️ পোস্টার ডাউনলোড করতে সমস্যা হচ্ছে।")
            else: await bot.send_message(user_id, "⚠️ TMDB থেকে ডাটা আনতে সমস্যা হয়েছে!")

@dp.message(AdvancedUpload.waiting_for_tmdb_query)
async def process_tmdb_query(m: types.Message, state: FSMContext):
    query = m.text.strip()
    tmdb_match = re.search(r'themoviedb\.org/(movie|tv)/(\d+)', query)
    imdb_match = re.search(r'tt\d+', query)
    
    if tmdb_match: return await fetch_and_send_tmdb_details(m, tmdb_match.group(2), tmdb_match.group(1), state, m.from_user.id)
        
    async with aiohttp.ClientSession() as session:
        if imdb_match:
            url = f"https://api.tmdb.org/3/find/{imdb_match.group(0)}?api_key={TMDB_API_KEY}&external_source=imdb_id"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("movie_results"): return await fetch_and_send_tmdb_details(m, data["movie_results"][0]["id"], "movie", state, m.from_user.id)
                    elif data.get("tv_results"): return await fetch_and_send_tmdb_details(m, data["tv_results"][0]["id"], "tv", state, m.from_user.id)
                    else: return await m.answer("⚠️ IMDB আইডি দিয়ে TMDB তে কিছু পাওয়া যায়নি!")
        
        url = f"https://api.tmdb.org/3/search/multi?api_key={TMDB_API_KEY}&query={urllib.parse.quote(query)}"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("results", [])
                if not results: return await m.answer("⚠️ কিছু পাওয়া যায়নি!")
                builder = InlineKeyboardBuilder()
                for res in results[:5]:
                    title = res.get("title") or res.get("name")
                    if not title: continue
                    year = res.get("release_date", res.get("first_air_date", ""))[:4]
                    builder.button(text=f"{title} ({year})", callback_data=f"tmdbid_{res['id']}_{res['media_type']}")
                builder.adjust(1)
                await m.answer("👇 <b>সঠিক মুভিটি সিলেক্ট করুন:</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("tmdbid_"))
async def process_tmdb_selection(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id not in admin_cache: return
    await c.answer()
    _, tmdb_id, media_type = c.data.split("_")
    await c.message.edit_text("⏳ ডাটা লোড হচ্ছে...")
    await fetch_and_send_tmdb_details(c.message, tmdb_id, media_type, state, c.from_user.id)

@dp.message(AdvancedUpload.waiting_for_manual_photo, F.photo)
async def process_manual_photo(m: types.Message, state: FSMContext):
    msg = await m.answer("⏳ <i>পোস্টার আপলোড হচ্ছে...</i>", parse_mode="HTML")
    img_url = m.photo[-1].file_id 
    await state.update_data(photo_id=img_url)
    await state.set_state(AdvancedUpload.waiting_for_manual_title)
    await msg.edit_text("✅ <b>মুভির নাম</b> লিখে পাঠান:", parse_mode="HTML")

@dp.message(AdvancedUpload.waiting_for_manual_title, F.text)
async def process_manual_title(m: types.Message, state: FSMContext):
    await state.update_data(title=m.text.strip(), genres=["All"])
    await state.set_state(AdvancedUpload.waiting_for_language)
    await m.answer("🗣 <b>ভাষা (Language) সিলেক্ট করুন:</b>", reply_markup=get_language_keyboard(), parse_mode="HTML")

@dp.callback_query(AdvancedUpload.waiting_for_language, F.data.startswith("lang_"))
async def process_language_selection(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id not in admin_cache: return
    await c.answer()
    lang = c.data.replace("lang_", "")
    if lang == "✍️ Custom":
        await state.set_state(AdvancedUpload.waiting_for_custom_language)
        await c.message.edit_text("✍️ <b>কাস্টম ভাষার নাম লিখে পাঠান:</b>", parse_mode="HTML")
    else:
        clean_lang = lang.replace("🇧🇩 ", "").replace("🇮🇳 ", "").replace("🇺🇸 ", "").replace("🎙 ", "")
        await state.update_data(language=clean_lang, selected_qualities=[])
        await state.set_state(AdvancedUpload.selecting_qualities)
        await c.message.edit_text(f"✅ ভাষা: <b>{clean_lang}</b>\n\n👇 <b>কোয়ালিটি সিলেক্ট করুন:</b>", reply_markup=get_batch_keyboard([]), parse_mode="HTML")

@dp.message(AdvancedUpload.waiting_for_custom_language, F.text)
async def process_custom_language(m: types.Message, state: FSMContext):
    await state.update_data(language=m.text.strip(), selected_qualities=[])
    await state.set_state(AdvancedUpload.selecting_qualities)
    await m.answer(f"✅ ভাষা: <b>{m.text.strip()}</b>\n\n👇 <b>কোয়ালিটি সিলেক্ট করুন:</b>", reply_markup=get_batch_keyboard([]), parse_mode="HTML")

@dp.callback_query(AdvancedUpload.selecting_qualities, F.data.startswith("bup_"))
async def process_quality_selection(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id not in admin_cache: return
    await c.answer()
    action = c.data[4:]
    data = await state.get_data()
    selected = data.get("selected_qualities", [])
    
    if action == "DONE":
        if not selected: return await c.answer("⚠️ কোয়ালিটি সিলেক্ট করুন!", show_alert=True)
        await state.update_data(current_index=0)
        await state.set_state(AdvancedUpload.waiting_for_files)
        await c.message.edit_text(f"✅ আপনি <b>{len(selected)}</b> টি ফাইল সিলেক্ট করেছেন।\n\n👉 <b>{selected[0]}</b> এর ফাইলটি সেন্ড করুন:", parse_mode="HTML")
    elif action == "✍️ Custom":
        await state.set_state(AdvancedUpload.waiting_for_custom_quality)
        await c.message.answer("✍️ <b>কাস্টম কোয়ালিটি/এপিসোডের নাম লিখুন:</b>", parse_mode="HTML")
    else:
        if action in selected: selected.remove(action)
        else: selected.append(action)
        await state.update_data(selected_qualities=selected)
        await c.message.edit_reply_markup(reply_markup=get_batch_keyboard(selected))

@dp.message(AdvancedUpload.waiting_for_custom_quality, F.text)
async def process_custom_quality(m: types.Message, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_qualities", [])
    selected.append(m.text.strip())
    await state.update_data(selected_qualities=selected)
    await state.set_state(AdvancedUpload.selecting_qualities)
    await m.answer("✅ যোগ করা হয়েছে! আরও লাগলে সিলেক্ট করুন, নয়তো <b>ডান</b> দিন:", reply_markup=get_batch_keyboard(selected), parse_mode="HTML")

@dp.message(AdvancedUpload.waiting_for_files, F.video | F.document)
async def process_batch_file(m: types.Message, state: FSMContext):
    if m.from_user.id not in admin_cache: return
    try:
        data = await state.get_data()
        selected = data.get("selected_qualities", [])
        idx = data.get("current_index", 0)
        if not selected or idx >= len(selected): return 
        
        title = data.get("title", "Unknown")
        file_id = m.video.file_id if m.video else m.document.file_id
        
        await db.movies.insert_one({
            "title": title, "quality": selected[idx], "photo_id": data.get("photo_id"), 
            "file_id": file_id, "file_type": "video" if m.video else "document",
            "genres": data.get("genres", ["All"]), "movie_lang": data.get("language", "N/A"), 
            "clicks": 0, "created_at": datetime.datetime.utcnow()
        })
        CACHE_DATA["list"].clear(); CACHE_DATA["trending"]["time"] = 0

        if DUMP_CHANNEL_ID:
            try:
                cap = f"#BACKUP\n🎬 {title}\n🏷 {selected[idx]}\n🗣 {data.get('language')}"
                if m.video: await bot.send_video(DUMP_CHANNEL_ID, file_id, caption=cap)
                else: await bot.send_document(DUMP_CHANNEL_ID, file_id, caption=cap)
            except Exception: pass
        
        next_idx = idx + 1
        if next_idx < len(selected):
            await state.update_data(current_index=next_idx)
            await m.answer(f"✅ <b>{selected[idx]}</b> সেভ হয়েছে!\n👉 এবার <b>{selected[next_idx]}</b> এর ফাইলটি সেন্ড করুন:", parse_mode="HTML")
        else:
            await state.clear()
            await m.answer(f"🎉 <b>{title}</b> সফলভাবে আপলোড হয়েছে!", parse_mode="HTML")
            if CHANNEL_ID and CHANNEL_ID != "-100XXXXXXXXXX":
                try:
                    old_post = await db.channel_posts.find_one({"title": title})
                    if old_post:
                        try: await bot.delete_message(CHANNEL_ID, old_post["message_id"])
                        except Exception: pass
                    bot_info = await bot.get_me()
                    
                    # ডেটাগুলো সুন্দরভাবে সাজানো হচ্ছে
                    genres_list = data.get("genres", ["All"])
                    genres_str = ", ".join(genres_list) if isinstance(genres_list, list) else genres_list
                    qualities_str = ", ".join(data.get("selected_qualities", []))
                    lang_str = data.get("language", "N/A")
                    
                    # সুন্দর নোটিফিকেশন টেমপ্লেট
                    cap = (
                        "🎉 <b>নতুন মুভি/সিরিজ যুক্ত হয়েছে!</b> 🎬\n\n"
                        f"📌 <b>নাম:</b> <code>{title}</code>\n"
                        f"🎭 <b>ক্যাটাগরি:</b> {genres_str}\n"
                        f"🗣 <b>ভাষা:</b> {lang_str}\n"
                        f"💿 <b>কোয়ালিটি:</b> {qualities_str}\n\n"
                        "🍿 <b>মুভিটি দেখতে বা ডাউনলোড করতে নিচের বাটনে ক্লিক করুন 👇</b>"
                    )
                    
                    kb = [[types.InlineKeyboardButton(text="🎬 Watch / Download Now", url=f"https://t.me/{bot_info.username}?start=new")]]
                    sent_msg = await bot.send_photo(CHANNEL_ID, data.get("photo_id"), caption=cap, parse_mode="HTML", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb))
                    await db.channel_posts.update_one({"title": title}, {"$set": {"message_id": sent_msg.message_id}}, upsert=True)
                except Exception: pass
    except Exception as e: await m.answer(f"⚠️ Error: {e}")

# ==========================================
# Admin Commands (ALL RESTORED PERFECTLY)
# ==========================================
def format_views(n):
    if n >= 1000000: return f"{n/1000000:.1f}M".replace(".0M", "M")
    if n >= 1000: return f"{n/1000:.1f}K".replace(".0K", "K")
    return str(n)

@dp.message(Command("addlink"))
async def add_direct_link(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        url = m.text.split(" ", 1)[1].strip()
        await db.settings.update_one({"id": "direct_links"}, {"$addToSet": {"links": url}}, upsert=True)
        await m.answer(f"✅ Link Added:\n<code>{url}</code>", parse_mode="HTML")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/addlink https://example.com</code>", parse_mode="HTML")

@dp.message(Command("dellink"))
async def del_direct_link(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        url = m.text.split(" ", 1)[1].strip()
        result = await db.settings.update_one({"id": "direct_links"}, {"$pull": {"links": url}})
        if result.modified_count > 0: await m.answer(f"❌ লিংকটি ডিলিট করা হয়েছে:\n<code>{url}</code>", parse_mode="HTML")
        else: await m.answer("⚠️ লিংকটি ডাটাবেসে পাওয়া যায়নি।")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/dellink https://example.com</code>", parse_mode="HTML")

@dp.message(Command("seelinks"))
async def see_direct_links(m: types.Message):
    if m.from_user.id not in admin_cache: return
    dl_cfg = await db.settings.find_one({"id": "direct_links"})
    links = dl_cfg.get("links", []) if dl_cfg else []
    if not links: return await m.answer("⚠️ কোনো ডাইরেক্ট লিংক অ্যাড করা নেই।")
    text = "🔗 <b>বর্তমান ডাইরেক্ট লিংক সমূহ:</b>\n\n"
    for i, link in enumerate(links, 1): text += f"{i}. <code>{link}</code>\n"
    await m.answer(text, parse_mode="HTML", disable_web_page_preview=True)

@dp.message(Command("settg"))
async def set_tg_link(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        url = m.text.split(" ")[1]
        await db.settings.update_one({"id": "link_tg"}, {"$set": {"url": url}}, upsert=True)
        await m.answer(f"✅ Telegram লিংক সেট করা হয়েছে:\n<code>{url}</code>", parse_mode="HTML")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/settg https://t.me/yourchannel</code>", parse_mode="HTML")

@dp.message(Command("set18"))
async def set_18_link(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        url = m.text.split(" ")[1]
        await db.settings.update_one({"id": "link_18"}, {"$set": {"url": url}}, upsert=True)
        await m.answer(f"✅ 18+ লিংক সেট করা হয়েছে:\n<code>{url}</code>", parse_mode="HTML")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/set18 https://t.me/your18channel</code>", parse_mode="HTML")

@dp.message(Command("addadmin"))
async def add_admin_cmd(m: types.Message):
    if m.from_user.id != OWNER_ID: return await m.answer("⚠️ শুধুমাত্র মেইন Owner অ্যাডমিন অ্যাড করতে পারবে!")
    try:
        target_uid = int(m.text.split()[1])
        await db.admins.update_one({"user_id": target_uid}, {"$set": {"user_id": target_uid}}, upsert=True)
        admin_cache.add(target_uid)
        await m.answer(f"✅ Admin Added: <code>{target_uid}</code>", parse_mode="HTML")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/addadmin ইউজার_আইডি</code>", parse_mode="HTML")

@dp.message(Command("deladmin"))
async def del_admin_cmd(m: types.Message):
    if m.from_user.id != OWNER_ID: return await m.answer("⚠️ শুধুমাত্র মেইন Owner অ্যাডমিন রিমুভ করতে পারবে!")
    try:
        target_uid = int(m.text.split()[1])
        if target_uid == OWNER_ID: return await m.answer("⚠️ Main Owner কে ডিলিট করা সম্ভব নয়!")
        await db.admins.delete_one({"user_id": target_uid})
        admin_cache.discard(target_uid)
        await m.answer(f"❌ ইউজার <code>{target_uid}</code> কে রিমুভ করা হয়েছে!", parse_mode="HTML")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/deladmin ইউজার_আইডি</code>", parse_mode="HTML")

@dp.message(Command("adminlist"))
async def list_admin_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    text = f"👑 <b>মেইন Owner:</b>\n▪️ <code>{OWNER_ID}</code>\n\n👮‍♂️ <b>অন্যান্য অ্যাডমিনগণ:</b>\n"
    async for a in db.admins.find(): text += f"▪️ <code>{a['user_id']}</code>\n"
    await m.answer(text, parse_mode="HTML")

@dp.message(Command("delmovie"))
async def del_movie_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        title = m.text.split(" ", 1)[1].strip()
        result = await db.movies.delete_many({"title": title})
        CACHE_DATA["list"].clear(); CACHE_DATA["trending"]["time"] = 0
        await m.answer(f"✅ '{title}' - {result.deleted_count} files deleted!")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/delmovie মুভির নাম</code>", parse_mode="HTML")

@dp.message(Command("stats"))
async def stats_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    uc = await db.users.count_documents({})
    mc = await db.movies.count_documents({})
    now = datetime.datetime.utcnow()
    new_users_today = await db.users.count_documents({"joined_at": {"$gte": datetime.datetime(now.year, now.month, now.day)}})
    
    top_movies = await db.movies.aggregate([{"$group": {"_id": "$title", "clicks": {"$sum": "$clicks"}}}, {"$sort": {"clicks": -1}}, {"$limit": 5}]).to_list(5)
    top_movies_text = "".join(f"{idx}. {mv['_id'][:20]}... - <b>{format_views(mv['clicks'])} views</b>\n" for idx, mv in enumerate(top_movies, 1))
    
    await m.answer(f"📊 <b>অ্যাডভান্সড স্ট্যাটাস:</b>\n\n👥 মোট ইউজার: <code>{uc}</code>\n🟢 আজকের নতুন ইউজার: <code>{new_users_today}</code>\n🎬 মোট ফাইল আপলোড: <code>{mc}</code>\n\n🔥 <b>টপ ৫ মুভি:</b>\n{top_movies_text}", parse_mode="HTML")

@dp.message(Command("ban"))
async def ban_user_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        target_uid = int(m.text.split()[1])
        if target_uid in admin_cache: return await m.answer("⚠️ অ্যাডমিনকে ব্যান করা যাবে্বা না!")
        await db.banned.update_one({"user_id": target_uid}, {"$set": {"user_id": target_uid}}, upsert=True)
        banned_cache.add(target_uid)
        await m.answer(f"🚫 ইউজার <code>{target_uid}</code> ব্যান হয়েছে!", parse_mode="HTML")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/ban ইউজার_আইডি</code>", parse_mode="HTML")

@dp.message(Command("unban"))
async def unban_user_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        target_uid = int(m.text.split()[1])
        await db.banned.delete_one({"user_id": target_uid})
        banned_cache.discard(target_uid)
        await m.answer(f"✅ ইউজার <code>{target_uid}</code> আনব্যান হয়েছে!", parse_mode="HTML")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/unban ইউজার_আইডি</code>", parse_mode="HTML")

@dp.message(Command("protect"))
async def protect_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        state = m.text.split(" ")[1].lower()
        await db.settings.update_one({"id": "protect_content"}, {"$set": {"status": state == "on"}}, upsert=True)
        await m.answer(f"✅ ফরোয়ার্ড প্রোটেকশন {'চালু' if state == 'on' else 'বন্ধ'} করা হয়েছে।")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/protect on</code> বা <code>off</code>")

@dp.message(Command("settime"))
async def set_del_time(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        await db.settings.update_one({"id": "del_time"}, {"$set": {"minutes": int(m.text.split(" ")[1])}}, upsert=True)
        await m.answer("✅ অটো-ডিলিট টাইম সেট করা হয়েছে।")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/settime 60</code> (মিনিট)")

@dp.message(Command("setbkash"))
async def set_bkash(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        num = m.text.split(" ")[1]
        await db.settings.update_one({"id": "bkash_no"}, {"$set": {"number": num}}, upsert=True)
        await m.answer(f"✅ বিকাশ নাম্বার সেট করা হয়েছে: <b>{num}</b>", parse_mode="HTML")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/setbkash 017XXXXXXX</code>")

@dp.message(Command("setnagad"))
async def set_nagad(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        num = m.text.split(" ")[1]
        await db.settings.update_one({"id": "nagad_no"}, {"$set": {"number": num}}, upsert=True)
        await m.answer(f"✅ নগদ নাম্বার সেট করা হয়েছে: <b>{num}</b>", parse_mode="HTML")
    except Exception: await m.answer("⚠️ সঠিক নিয়ম: <code>/setnagad 017XXXXXXX</code>")

@dp.message(Command("addvip"))
async def add_vip_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        args = m.text.split()
        target_uid = int(args[1])
        days = int(args[2]) if len(args) > 2 else 30 
        now = datetime.datetime.utcnow()
        user = await db.users.find_one({"user_id": target_uid})
        if not user: return await m.answer("⚠️ User not found.")
        current_vip = max(user.get("vip_until", now), now)
        await db.users.update_one({"user_id": target_uid}, {"$set": {"vip_until": current_vip + datetime.timedelta(days=days)}})
        await m.answer(f"✅ VIP Given ({days} Days)!")
    except Exception: pass

@dp.callback_query(F.data.startswith("trx_"))
async def handle_trx_approval(c: types.CallbackQuery):
    if c.from_user.id not in admin_cache: return
    action, _, pay_id = c.data.split("_")
    payment = await db.payments.find_one({"_id": ObjectId(pay_id)})
    if not payment or payment["status"] != "pending": return await c.answer("Processed!", show_alert=True)
    if action == "approve":
        now = datetime.datetime.utcnow()
        user = await db.users.find_one({"user_id": payment["user_id"]})
        current_vip = max(user.get("vip_until", now) if user else now, now)
        await db.users.update_one({"user_id": payment["user_id"]}, {"$set": {"vip_until": current_vip + datetime.timedelta(days=payment["days"])}})
        await db.payments.update_one({"_id": ObjectId(pay_id)}, {"$set": {"status": "approved"}})
        await c.message.edit_text(c.message.text + "\n\n✅ <b>Approved!</b>", parse_mode="HTML")
    else:
        await db.payments.update_one({"_id": ObjectId(pay_id)}, {"$set": {"status": "rejected"}})
        await c.message.edit_text(c.message.text + "\n\n❌ <b>Rejected!</b>", parse_mode="HTML")

@dp.callback_query(F.data.startswith("req_"))
async def handle_request_approval(c: types.CallbackQuery):
    if c.from_user.id not in admin_cache: return
    action, _, req_id = c.data.split("_")
    req = await db.requests.find_one({"_id": ObjectId(req_id)})
    if not req: return await c.answer("Already processed!", show_alert=True)
    if action == "acc": await c.message.edit_text(c.message.text + "\n\n✅ <b>Approved!</b>", parse_mode="HTML")
    elif action == "rej": await c.message.edit_text(c.message.text + "\n\n❌ <b>Rejected!</b>", parse_mode="HTML")
    await db.requests.delete_one({"_id": ObjectId(req_id)})

@dp.message(StateFilter(None), F.video | F.document)
async def receive_movie_file(m: types.Message, state: FSMContext):
    if m.from_user.id not in admin_cache: return
    fid = m.video.file_id if m.video else m.document.file_id
    await state.set_state(AdminStates.waiting_for_photo)
    await state.update_data(file_id=fid, file_type="video" if m.video else "document")
    await m.answer("✅ ফাইল পেয়েছি! এবার মুভির <b>পোস্টার (Photo)</b> সেন্ড করুন।", parse_mode="HTML")

@dp.message(AdminStates.waiting_for_photo, F.photo)
async def receive_movie_photo(m: types.Message, state: FSMContext):
    msg = await m.answer("⏳ <i>আপলোড হচ্ছে...</i>", parse_mode="HTML")
    img_url = m.photo[-1].file_id 
    await state.update_data(photo_id=img_url)
    await state.set_state(AdminStates.waiting_for_title)
    await msg.edit_text("✅ <b>মুভি বা ওয়েব সিরিজের নাম</b> লিখে পাঠান।", parse_mode="HTML")

@dp.message(AdminStates.waiting_for_title, F.text)
async def receive_movie_title(m: types.Message, state: FSMContext):
    await state.update_data(title=m.text.strip())
    await state.set_state(AdminStates.waiting_for_quality)
    await m.answer("✅ এবার <b>কোয়ালিটি বা এপিসোড নাম্বার</b> দিন।", parse_mode="HTML")

@dp.message(AdminStates.waiting_for_quality, F.text)
async def receive_movie_quality(m: types.Message, state: FSMContext):
    data = await state.get_data()
    title = data["title"]
    quality = m.text.strip()
    photo_id = data["photo_id"]
    await state.clear()
    
    await db.movies.insert_one({
        "title": title, "quality": quality, "photo_id": photo_id, 
        "file_id": data["file_id"], "file_type": data["file_type"],
        "genres": ["All"], "movie_lang": "N/A", "clicks": 0, "created_at": datetime.datetime.utcnow()
    })
    CACHE_DATA["list"].clear(); CACHE_DATA["trending"]["time"] = 0
    await m.answer(f"🎉 <b>{title}</b> আপলোড হয়েছে!", parse_mode="HTML")
    
    if CHANNEL_ID and CHANNEL_ID != "-100XXXXXXXXXX":
        try:
            bot_info = await bot.get_me()
            cap = (
                "🎉 <b>নতুন মুভি/সিরিজ যুক্ত হয়েছে!</b> 🎬\n\n"
                f"📌 <b>নাম:</b> <code>{title}</code>\n"
                f"💿 <b>কোয়ালিটি:</b> {quality}\n\n"
                "🍿 <b>মুভিটি দেখতে বা ডাউনলোড করতে নিচের বাটনে ক্লিক করুন 👇</b>"
            )
            kb = [[types.InlineKeyboardButton(text="🎬 Watch / Download Now", url=f"https://t.me/{bot_info.username}?start=new")]]
            sent_msg = await bot.send_photo(CHANNEL_ID, photo_id, caption=cap, parse_mode="HTML", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb))
            await db.channel_posts.update_one({"title": title}, {"$set": {"message_id": sent_msg.message_id}}, upsert=True)
        except Exception: pass

@dp.message(Command("cast"))
async def broadcast_prep(m: types.Message, state: FSMContext):
    if m.from_user.id not in admin_cache: return
    await state.set_state(AdminStates.waiting_for_bcast)
    await m.answer("📢 যে মেসেজটি ব্রডকাস্ট করতে চান সেটি পাঠান।")

@dp.message(AdminStates.waiting_for_bcast)
async def execute_broadcast(m: types.Message, state: FSMContext):
    await state.clear()
    msg = await m.answer("⏳ ব্রডকাস্ট শুরু হয়েছে...")
    markup = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="🎬 ওপেন মুভি অ্যাপ", web_app=types.WebAppInfo(url=APP_URL))]])
    users = await db.users.find().to_list(length=None)
    success = failed = 0
    
    async def send_msg(user_id):
        try:
            await m.copy_to(chat_id=user_id, reply_markup=markup)
            return True
        except: return False

    for i in range(0, len(users), 30):
        tasks = [send_msg(u['user_id']) for u in users[i:i+30]]
        res = await asyncio.gather(*tasks)
        success += sum(1 for r in res if r)
        failed += sum(1 for r in res if not r)
        await asyncio.sleep(1) 
    await msg.edit_text(f"✅ ব্রডকাস্ট সম্পন্ন!\nসফল: {success}\nব্যর্থ: {failed}")

@dp.callback_query(F.data.startswith("reply_"))
async def process_reply_cb(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id not in admin_cache: return
    await state.set_state(AdminStates.waiting_for_reply)
    await state.update_data(target_uid=int(c.data.split("_")[1]))
    await c.message.reply("✍️ <b>রিপ্লাই লিখে পাঠান:</b>", parse_mode="HTML")
    await c.answer()

@dp.message(AdminStates.waiting_for_reply)
async def send_reply(m: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    try:
        if m.text: await bot.send_message(data.get("target_uid"), f"📩 <b>অ্যাডমিন রিপ্লাই:</b>\n\n{m.text}", parse_mode="HTML")
        await m.answer("✅ রিপ্লাই পাঠানো হয়েছে!")
    except: await m.answer("⚠️ এরর!")

# ==========================================
# FastAPI Routes (Web App & APIs) using Jinja2
# ==========================================
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def web_ui(request: Request):
    ad_cfg = await db.settings.find_one({"id": "ad_config"})
    tg_cfg = await db.settings.find_one({"id": "link_tg"})
    b18_cfg = await db.settings.find_one({"id": "link_18"})
    bkash_cfg = await db.settings.find_one({"id": "bkash_no"})
    nagad_cfg = await db.settings.find_one({"id": "nagad_no"})
    dl_cfg = await db.settings.find_one({"id": "direct_links"})
    
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "ZONE_ID": ad_cfg['zone_id'] if ad_cfg else "10916755",
            "TG_LINK": tg_cfg['url'] if tg_cfg else "https://t.me/MovieeBD",
            "LINK_18": b18_cfg['url'] if b18_cfg else "https://t.me/MovieeBD",
            "BOT_USER": BOT_USERNAME,
            "BKASH_NO": bkash_cfg['number'] if bkash_cfg else "Not Set",
            "NAGAD_NO": nagad_cfg['number'] if nagad_cfg else "Not Set",
            "DIRECT_LINKS": json.dumps(dl_cfg.get('links', []) if dl_cfg else [])
        }
    )

@app.get("/admin", response_class=HTMLResponse)
async def web_admin_panel(request: Request, auth: bool = Depends(verify_admin)):
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"request": request}
    )

@app.get("/api/admin/data")
async def get_admin_data(auth: bool = Depends(verify_admin)):
    uc = await db.users.count_documents({})
    now = datetime.datetime.utcnow()
    new_users = await db.users.count_documents({"joined_at": {"$gte": datetime.datetime(now.year, now.month, now.day)}})
    pipeline = [{"$group": {"_id": "$title", "clicks": {"$sum": "$clicks"}, "file_count": {"$sum": 1}, "created_at": {"$max": "$created_at"}, "files": {"$push": {"id": {"$toString": "$_id"}, "quality": {"$ifNull": ["$quality", "Main File"]}}}}}, {"$sort": {"created_at": -1}}, {"$limit": 100}]
    movies = await db.movies.aggregate(pipeline).to_list(100)
    return {"total_users": uc, "total_groups": len(movies), "new_users_today": new_users, "movies": movies}

@app.delete("/api/admin/movie/{title}")
async def delete_movie_api(title: str, auth: bool = Depends(verify_admin)):
    await db.movies.delete_many({"title": title})
    CACHE_DATA["list"].clear(); CACHE_DATA["trending"]["time"] = 0
    return {"ok": True}

class RenameMovieModel(BaseModel): old_title: str; new_title: str
@app.post("/api/admin/movie/rename")
async def rename_movie_api(data: RenameMovieModel, auth: bool = Depends(verify_admin)):
    await db.movies.update_many({"title": data.old_title}, {"$set": {"title": data.new_title}})
    CACHE_DATA["list"].clear(); CACHE_DATA["trending"]["time"] = 0
    return {"ok": True}

@app.delete("/api/admin/movie/file/{file_id}")
async def delete_single_file_api(file_id: str, auth: bool = Depends(verify_admin)):
    await db.movies.delete_one({"_id": ObjectId(file_id)})
    CACHE_DATA["list"].clear(); CACHE_DATA["trending"]["time"] = 0
    return {"ok": True}

@app.get("/api/admin/settings_data")
async def get_settings_data(auth: bool = Depends(verify_admin)):
    bkash = await db.settings.find_one({"id": "bkash_no"})
    nagad = await db.settings.find_one({"id": "nagad_no"})
    dtime = await db.settings.find_one({"id": "del_time"})
    dlinks = await db.settings.find_one({"id": "direct_links"})
    tg_cfg = await db.settings.find_one({"id": "link_tg"})
    b18_cfg = await db.settings.find_one({"id": "link_18"})
    unlock_cfg = await db.settings.find_one({"id": "unlock_duration"})
    return {
        "bkash": bkash["number"] if bkash else "",
        "nagad": nagad["number"] if nagad else "",
        "del_time": dtime["minutes"] if dtime else 60,
        "tg_link": tg_cfg["url"] if tg_cfg else "",
        "link_18": b18_cfg["url"] if b18_cfg else "",
        "unlock_duration": unlock_cfg["hours"] if unlock_cfg else 24,
        "links": dlinks["links"] if dlinks and "links" in dlinks else []
    }

class SettingUpdateModel(BaseModel): type: str; value: str
@app.post("/api/admin/update_setting")
async def update_setting_api(data: SettingUpdateModel, auth: bool = Depends(verify_admin)):
    if data.type == "bkash": await db.settings.update_one({"id": "bkash_no"}, {"$set": {"number": data.value}}, upsert=True)
    if data.type == "nagad": await db.settings.update_one({"id": "nagad_no"}, {"$set": {"number": data.value}}, upsert=True)
    if data.type == "time": await db.settings.update_one({"id": "del_time"}, {"$set": {"minutes": int(data.value)}}, upsert=True)
    if data.type == "tg_link": await db.settings.update_one({"id": "link_tg"}, {"$set": {"url": data.value}}, upsert=True)
    if data.type == "link_18": await db.settings.update_one({"id": "link_18"}, {"$set": {"url": data.value}}, upsert=True)
    if data.type == "unlock_time": await db.settings.update_one({"id": "unlock_duration"}, {"$set": {"hours": int(data.value)}}, upsert=True)
    return {"ok": True}

class LinkModel(BaseModel): link: str
@app.post("/api/admin/links")
async def add_link_api(data: LinkModel, auth: bool = Depends(verify_admin)):
    await db.settings.update_one({"id": "direct_links"}, {"$addToSet": {"links": data.link}}, upsert=True)
    return {"ok": True}

@app.delete("/api/admin/links")
async def del_link_api(link: str, auth: bool = Depends(verify_admin)):
    await db.settings.update_one({"id": "direct_links"}, {"$pull": {"links": link}})
    return {"ok": True}

class UserActionModel(BaseModel): user_id: int; action: str
@app.post("/api/admin/user_action")
async def admin_user_action(data: UserActionModel, auth: bool = Depends(verify_admin)):
    uid = data.user_id
    if data.action == "vip":
        now = datetime.datetime.utcnow()
        user = await db.users.find_one({"user_id": uid})
        if not user: return {"msg": "User not found!"}
        current_vip = max(user.get("vip_until", now), now)
        await db.users.update_one({"user_id": uid}, {"$set": {"vip_until": current_vip + datetime.timedelta(days=30)}})
        return {"msg": f"VIP given to {uid}!"}
    elif data.action == "ban":
        if uid in admin_cache: return {"msg": "Cannot ban an admin!"}
        await db.banned.update_one({"user_id": uid}, {"$set": {"user_id": uid}}, upsert=True)
        banned_cache.add(uid)
        return {"msg": "User Banned!"}
    elif data.action == "unban":
        await db.banned.delete_one({"user_id": uid}); banned_cache.discard(uid)
        return {"msg": "User Unbanned!"}

# --- Standard App APIs ---
@app.get("/api/user/{uid}")
async def get_user_info(uid: int):
    user = await db.users.find_one({"user_id": uid})
    if not user: return {"vip": False, "is_admin": False, "refer_count": 0, "vip_expiry": None, "coins": 0, "streak": 0, "can_checkin": False, "can_quiz": False}
    vip_until = user.get("vip_until")
    now = datetime.datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    is_vip = vip_until and vip_until > now
    vip_expiry_str = vip_until.strftime("%d %b %Y") if is_vip else None
    
    last_checkin = user.get("last_checkin", now - datetime.timedelta(days=2))
    last_quiz = user.get("last_quiz", now - datetime.timedelta(days=2))
    return {"vip": is_vip, "is_admin": uid in admin_cache, "refer_count": user.get("refer_count", 0), "vip_expiry": vip_expiry_str, "coins": user.get("coins", 0), "streak": user.get("streak", 0), "can_checkin": last_checkin < today_start, "can_quiz": last_quiz < today_start}

class CheckinModel(BaseModel): uid: int; action: str; initData: str
@app.post("/api/checkin")
async def handle_checkin(data: CheckinModel):
    if not validate_tg_data(data.initData): return {"ok": False}
    user = await db.users.find_one({"user_id": data.uid})
    if not user: return {"ok": False}
    now = datetime.datetime.utcnow()
    yesterday_start = (now - datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if data.action == "claim":
        last_checkin = user.get("last_checkin", now - datetime.timedelta(days=2))
        streak = user.get("streak", 0)
        if last_checkin >= today_start: return {"ok": False, "msg": "Already claimed!"}
        streak = streak + 1 if last_checkin >= yesterday_start else 1
        coins = 10 if streak==1 else (15 if streak==2 else (20 if streak==3 else (25 if streak==4 else (30 if streak==5 else (40 if streak==6 else 50)))))
        
        got_vip = False
        update_data = {"$inc": {"coins": coins}, "$set": {"last_checkin": now, "streak": streak}}
        if streak % 7 == 0:
            got_vip = True
            update_data["$set"]["vip_until"] = max(user.get("vip_until", now), now) + datetime.timedelta(days=1)
        await db.users.update_one({"user_id": data.uid}, update_data)
        return {"ok": True, "coins": coins, "streak": streak, "got_vip": got_vip}
        
    elif data.action == "convert":
        if user.get("coins", 0) < 50: return {"ok": False, "msg": "Need 50 coins"}
        await db.users.update_one({"user_id": data.uid}, {"$inc": {"coins": -50}, "$set": {"vip_until": max(user.get("vip_until", now), now) + datetime.timedelta(days=1)}})
        return {"ok": True}

class QuizModel(BaseModel): uid: int; initData: str
@app.post("/api/quiz_reward")
async def quiz_reward(data: QuizModel):
    if not validate_tg_data(data.initData): return {"ok": False}
    user = await db.users.find_one({"user_id": data.uid})
    now = datetime.datetime.utcnow()
    if user.get("last_quiz", now - datetime.timedelta(days=2)) >= now.replace(hour=0, minute=0, second=0, microsecond=0): return {"ok": False}
    await db.users.update_one({"user_id": data.uid}, {"$inc": {"coins": 10}, "$set": {"last_quiz": now}})
    return {"ok": True}

class PaymentModel(BaseModel): uid: int; method: str; trx_id: str; days: int; price: int; initData: str
@app.post("/api/payment/submit")
async def submit_payment(data: PaymentModel):
    if not validate_tg_data(data.initData): return {"ok": False}
    if await db.payments.find_one({"trx_id": data.trx_id}): return {"ok": False, "msg": "TrxID used!"}
    res = await db.payments.insert_one({"user_id": data.uid, "method": data.method, "trx_id": data.trx_id, "amount": data.price, "days": data.days, "status": "pending", "created_at": datetime.datetime.utcnow()})
    try:
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Approve", callback_data=f"trx_approve_{res.inserted_id}")
        kb.button(text="❌ Reject", callback_data=f"trx_reject_{res.inserted_id}")
        for ad_id in admin_cache: await bot.send_message(ad_id, f"💰 <b>New Payment!</b>\nUser: {data.uid}\nTrx: {data.trx_id}\nPkg: {data.days} Days", parse_mode="HTML", reply_markup=kb.as_markup())
    except: pass
    return {"ok": True}

@app.get("/api/trending")
async def trending_movies(uid: int = 0):
    if uid in banned_cache: return {"error": "banned"}
    cfg = await db.settings.find_one({"id": "unlock_duration"})
    unlocked_ids = []
    if uid != 0:
        async for u in db.user_unlocks.find({"user_id": uid, "unlocked_at": {"$gt": datetime.datetime.utcnow() - datetime.timedelta(hours=cfg["hours"] if cfg else 24)}}): unlocked_ids.append(u["movie_id"])
    now_ts = time.time()
    if now_ts - CACHE_DATA["trending"]["time"] < CACHE_TTL and CACHE_DATA["trending"]["data"]: raw_movies = copy.deepcopy(CACHE_DATA["trending"]["data"])
    else:
        raw_movies = await db.movies.aggregate([{"$group": {"_id": "$title", "photo_id": {"$first": "$photo_id"}, "genres": {"$first": "$genres"}, "language": {"$first": "$movie_lang"}, "clicks": {"$sum": "$clicks"}, "files": {"$push": {"id": {"$toString": "$_id"}, "quality": {"$ifNull": ["$quality", "Main File"]}}}}}, {"$sort": {"clicks": -1}}, {"$limit": 10}]).to_list(10)
        CACHE_DATA["trending"] = {"time": now_ts, "data": copy.deepcopy(raw_movies)}
    for m in raw_movies:
        for f in m["files"]: f["is_unlocked"] = f["id"] in unlocked_ids
    return raw_movies

@app.get("/api/list")
async def list_movies(page: int = 1, q: str = "", uid: int = 0, genre: str = "All"):
    if uid in banned_cache: return {"error": "banned"}
    limit = 16; skip = (page - 1) * limit
    cfg = await db.settings.find_one({"id": "unlock_duration"})
    unlocked_ids = []
    if uid != 0:
        async for u in db.user_unlocks.find({"user_id": uid, "unlocked_at": {"$gt": datetime.datetime.utcnow() - datetime.timedelta(hours=cfg["hours"] if cfg else 24)}}): unlocked_ids.append(u["movie_id"])

    cache_key = f"{page}_{q}_{genre}"; now_ts = time.time()
    if cache_key in CACHE_DATA["list"] and (now_ts - CACHE_DATA["list"][cache_key]["time"]) < CACHE_TTL:
        raw_movies = copy.deepcopy(CACHE_DATA["list"][cache_key]["data"]); total_pages = CACHE_DATA["list"][cache_key]["total_pages"]
    else:
        match_stage = {"title": {"$regex": q, "$options": "i"}} if q else {}
        if genre and genre != "All": match_stage["genres"] = genre
        c_res = await db.movies.aggregate([{"$match": match_stage}, {"$group": {"_id": "$title"}}, {"$count": "total"}]).to_list(1)
        total_pages = (c_res[0]["total"] + limit - 1) // limit if c_res else 0
        raw_movies = await db.movies.aggregate([{"$match": match_stage}, {"$group": {"_id": "$title", "photo_id": {"$first": "$photo_id"}, "genres": {"$first": "$genres"}, "language": {"$first": "$movie_lang"}, "clicks": {"$sum": "$clicks"}, "created_at": {"$max": "$created_at"}, "files": {"$push": {"id": {"$toString": "$_id"}, "quality": {"$ifNull": ["$quality", "Main File"]}}}}}, {"$sort": {"created_at": -1}}, {"$skip": skip}, {"$limit": limit}]).to_list(limit)
        CACHE_DATA["list"][cache_key] = {"time": now_ts, "data": copy.deepcopy(raw_movies), "total_pages": total_pages}

    for m in raw_movies:
        for f in m["files"]: f["is_unlocked"] = f["id"] in unlocked_ids
    return {"movies": raw_movies, "total_pages": total_pages}

@app.get("/api/image/{photo_id}")
async def get_image(photo_id: str):
    try:
        cache = await db.file_cache.find_one({"photo_id": photo_id})
        now = datetime.datetime.utcnow()
        if cache and cache.get("expires_at", now) > now: file_path = cache["file_path"]
        else:
            file_path = (await bot.get_file(photo_id)).file_path
            await db.file_cache.update_one({"photo_id": photo_id}, {"$set": {"file_path": file_path, "expires_at": now + datetime.timedelta(minutes=50)}}, upsert=True)
        async def stream_image():
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}") as resp:
                    async for chunk in resp.content.iter_chunked(1024): yield chunk
        return StreamingResponse(stream_image(), media_type="image/jpeg")
    except Exception as e: 
        print("Image load error:", e)
        return {"error": "not found"}

class SendRequestModel(BaseModel): userId: int; movieId: str; initData: str
@app.post("/api/send")
async def send_file(d: SendRequestModel):
    if d.userId == 0 or d.userId in banned_cache or not validate_tg_data(d.initData): return {"ok": False}
    now_ts = time.time()
    if d.userId in USER_REQUEST_RATES and (now_ts - USER_REQUEST_RATES[d.userId]) < 5: return {"ok": False, "msg": "Too fast"}
    USER_REQUEST_RATES[d.userId] = now_ts
    
    try:
        m = await db.movies.find_one({"_id": ObjectId(d.movieId)})
        if m:
            now = datetime.datetime.utcnow(); user_data = await db.users.find_one({"user_id": d.userId})
            is_vip = user_data and user_data.get("vip_until", now) > now
            del_minutes = (await db.settings.find_one({"id": "del_time"}) or {}).get('minutes', 60)
            is_protected = (await db.settings.find_one({"id": "protect_content"}) or {}).get('status', True)
            
            caption = f"🎥 <b>{m['title']} [{m.get('quality', '')}]</b>\n\n" + ("🌟 <b>VIP Access:</b> File permanently saved." if is_vip else f"⏳ <b>Warning:</b> File will auto-delete in {del_minutes} minutes.")
            if m.get("file_type") == "video": sent_msg = await bot.send_video(d.userId, m['file_id'], caption=caption, parse_mode="HTML", protect_content=is_protected)
            else: sent_msg = await bot.send_document(d.userId, m['file_id'], caption=caption, parse_mode="HTML", protect_content=is_protected)
            
            await db.movies.update_one({"_id": ObjectId(d.movieId)}, {"$inc": {"clicks": 1}})
            await db.user_unlocks.update_one({"user_id": d.userId, "movie_id": d.movieId}, {"$set": {"unlocked_at": now}}, upsert=True)
            if sent_msg and not is_vip: await db.auto_delete.insert_one({"chat_id": d.userId, "message_id": sent_msg.message_id, "delete_at": now + datetime.timedelta(minutes=del_minutes)})
    except: pass
    return {"ok": True}

class AdRewardModel(BaseModel): uid: int; initData: str
@app.post("/api/reward_ad")
async def reward_ad(data: AdRewardModel):
    if not validate_tg_data(data.initData): return {"ok": False}
    await db.users.update_one({"user_id": data.uid}, {"$inc": {"coins": 5}})
    return {"ok": True}

@app.get("/api/leaderboard")
async def get_leaderboard():
    users = await db.users.find().sort("refer_count", -1).limit(10).to_list(10)
    return [{"name": u.get("first_name", "User"), "refers": u.get("refer_count", 0)} for u in users]

@app.get("/api/requests")
async def get_requests():
    reqs = await db.requests.find().sort("votes", -1).limit(20).to_list(20)
    return [{"id": str(r["_id"]), "movie": r["movie"], "uname": r.get("uname", "User"), "votes": r.get("votes", 1), "voters": r.get("voters", [])} for r in reqs]

class ReqModel(BaseModel): uid: int; uname: str; movie: str; initData: str
@app.post("/api/request")
async def handle_request(data: ReqModel):
    if data.uid in banned_cache or not validate_tg_data(data.initData): return {"ok": False}
    existing = await db.requests.find_one({"movie": {"$regex": f"^{data.movie}$", "$options": "i"}})
    if existing:
        if data.uid not in existing.get("voters", []): await db.requests.update_one({"_id": existing["_id"]}, {"$inc": {"votes": 1}, "$push": {"voters": data.uid}})
    else:
        await db.requests.insert_one({"user_id": data.uid, "uname": data.uname, "movie": data.movie, "votes": 1, "voters": [data.uid], "created_at": datetime.datetime.utcnow()})
    return {"ok": True}

class VoteModel(BaseModel): uid: int; req_id: str; initData: str
@app.post("/api/requests/vote")
async def vote_request(data: VoteModel):
    if not validate_tg_data(data.initData): return {"ok": False}
    req = await db.requests.find_one({"_id": ObjectId(data.req_id)})
    if not req or data.uid in req.get("voters", []): return {"ok": False}
    await db.requests.update_one({"_id": ObjectId(data.req_id)}, {"$inc": {"votes": 1}, "$push": {"voters": data.uid}})
    return {"ok": True}

@app.delete("/api/requests/{req_id}")
async def delete_request(req_id: str, initData: str):
    if not validate_tg_data(initData): return {"ok": False}
    parsed = dict(urllib.parse.parse_qsl(initData))
    try:
        user_data = json.loads(parsed.get('user', '{}'))
        if user_data.get('id') not in admin_cache: return {"ok": False}
    except: return {"ok": False}
    await db.requests.delete_one({"_id": ObjectId(req_id)})
    return {"ok": True}

@app.get("/api/reviews")
async def get_reviews(movie: str):
    revs = await db.reviews.find({"movie_title": movie}).sort("created_at", -1).to_list(50)
    return {"average": round(sum(r.get("rating", 5) for r in revs) / len(revs), 1) if revs else 0, "count": len(revs), "reviews": [{"id": str(r["_id"]), "uname": r.get("uname", "User"), "rating": r.get("rating", 5), "text": r.get("text", "")} for r in revs]}

class ReviewPostModel(BaseModel): uid: int; uname: str; movie: str; rating: int; text: str; initData: str
@app.post("/api/review")
async def post_review(data: ReviewPostModel):
    if not validate_tg_data(data.initData) or not (1 <= data.rating <= 5): return {"ok": False}
    existing = await db.reviews.find_one({"movie_title": data.movie, "user_id": data.uid})
    if existing: await db.reviews.update_one({"_id": existing["_id"]}, {"$set": {"rating": data.rating, "text": data.text, "created_at": datetime.datetime.utcnow()}})
    else: await db.reviews.insert_one({"movie_title": data.movie, "user_id": data.uid, "uname": data.uname, "rating": data.rating, "text": data.text, "created_at": datetime.datetime.utcnow()})
    return {"ok": True}

@app.delete("/api/review/{rev_id}")
async def delete_review(rev_id: str, initData: str):
    if not validate_tg_data(initData): return {"ok": False}
    parsed = dict(urllib.parse.parse_qsl(initData))
    try:
        user_data = json.loads(parsed.get('user', '{}'))
        if user_data.get('id') not in admin_cache: return {"ok": False}
    except: return {"ok": False}
    await db.reviews.delete_one({"_id": ObjectId(rev_id)})
    return {"ok": True}

# ==========================================
# 🚀 Dynamic Plugin Loader System
# ==========================================
PLUGIN_DIR = "plugins"

if not os.path.exists(PLUGIN_DIR):
    try:
        os.makedirs(PLUGIN_DIR)
    except Exception:
        pass

if os.path.exists(PLUGIN_DIR):
    for filename in os.listdir(PLUGIN_DIR):
        if filename.endswith(".py") and not filename.startswith("__"):
            module_name = f"{PLUGIN_DIR}.{filename[:-3]}"
            try:
                module = importlib.import_module(module_name)
                # Load Bot Plugins (Aiogram Routers)
                if hasattr(module, "bot_router"):
                    dp.include_router(module.bot_router)
                    print(f"✅ Bot Plugin Loaded: {filename}")
                # Load Web/API Plugins (FastAPI Routers)
                if hasattr(module, "web_router"):
                    app.include_router(module.web_router)
                    print(f"✅ Web API Plugin Loaded: {filename}")
            except Exception as e:
                print(f"❌ Failed to load plugin {filename}: {e}")

# ==========================================
# Start Server
# ==========================================
async def start():
    await init_db()
    await load_admins()
    await load_banned_users()
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="asyncio")
    server = uvicorn.Server(config)
    asyncio.create_task(auto_delete_worker())
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(server.serve(), dp.start_polling(bot))

if __name__ == "__main__": 
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start())
