# -*- coding: utf-8 -*-

# 🔥 PYTHON 3.13 ASYNCIO FIX (MAGIC BYPASS) 🔥
# এই কোডটির কারণে motor ডাটাবেস আর কখনো ক্র্যাশ করবে না
import asyncio
if not hasattr(asyncio, 'coroutine'):
    asyncio.coroutine = lambda f: f

import os
import io
import re
import importlib
import pkgutil
import json
import time
import logging
import random
import string
import base64
import datetime
import aiohttp
import requests 
import urllib3 
import numpy as np 
import cv2 
from threading import Thread

# --- Third-party Library Imports ---
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, Message,
    CallbackQuery
)
from flask import Flask
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient 

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# SSL Warnings বন্ধ করা
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load environment variables
load_dotenv()

# ---- CONFIGURATION ----
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

# 🔥 ADMIN & DB CONFIG
MONGO_URL = os.getenv("MONGO_URL") 
OWNER_ID = int(os.getenv("OWNER_ID", 0)) 
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "admin") 
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", 0))

# 🔥 ফাইল স্টোর চ্যানেল (অবশ্যই -100 দিয়ে শুরু হতে হবে)
DB_CHANNEL_ID = int(os.getenv("DB_CHANNEL_ID", 0)) 
# --- WORKER GLOBAL VARIABLE ---
worker_client = None
# Check Variables
if not all([BOT_TOKEN, API_ID, API_HASH, TMDB_API_KEY, MONGO_URL]):
    logger.critical("❌ FATAL ERROR: Variables missing in .env file!")
    exit(1)

# ====================================================================
# 🔥 DATABASE CONNECTION (MONGODB)
# ====================================================================
try:
    mongo_client = AsyncIOMotorClient(MONGO_URL)
    db = mongo_client["movie_bot_db"]
    users_col = db["users"]
    settings_col = db["settings"]
    user_settings_col = db["user_settings"]
    posts_col = db["posts"] 
    logger.info("✅ MongoDB Connected Successfully!")
except Exception as e:
    logger.critical(f"❌ MongoDB Connection Failed: {e}")
    exit(1)

# ---- DEFAULT SETTINGS ----
DEFAULT_OWNER_AD_LINKS =[
    "https://www.google.com",
    "https://www.bing.com"
]
DEFAULT_USER_AD_LINKS =["https://www.google.com", "https://www.bing.com"] 

user_conversations = {}

# 🔥 BATCH UPLOAD QUEUE LIMITER (সার্ভার লোড এবং Flood Wait রোধ করতে)
upload_semaphore = asyncio.Semaphore(2)

# ---- DATABASE FUNCTIONS ----
async def add_user(user_id, name):
    if not await users_col.find_one({"_id": user_id}):
        await users_col.insert_one({
            "_id": user_id, 
            "name": name,
            "authorized": False, 
            "banned": False,
            "joined_date": datetime.datetime.now()
        })

async def is_authorized(user_id):
    if user_id == OWNER_ID:
        return True
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return False
    return user.get("authorized", False) and not user.get("banned", False)

async def is_banned(user_id):
    user = await users_col.find_one({"_id": user_id})
    return user and user.get("banned", False)

async def get_owner_ads():
    data = await settings_col.find_one({"_id": "main_config"})
    return data.get("owner_ads", DEFAULT_OWNER_AD_LINKS) if data else DEFAULT_OWNER_AD_LINKS

async def set_owner_ads_db(links):
    await settings_col.update_one(
        {"_id": "main_config"}, 
        {"$set": {"owner_ads": links}}, 
        upsert=True
    )

async def get_auto_delete_timer():
    data = await settings_col.find_one({"_id": "main_config"})
    return data.get("auto_delete_seconds", 600) if data else 600

async def set_auto_delete_timer_db(seconds):
    await settings_col.update_one(
        {"_id": "main_config"}, 
        {"$set": {"auto_delete_seconds": int(seconds)}}, 
        upsert=True
    )

async def auto_delete_task(client, chat_id, message_ids, delay):
    if delay <= 0:
        return
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, message_ids)
    except Exception as e:
        logger.error(f"Auto Delete Error: {e}")

async def get_admin_share():
    data = await settings_col.find_one({"_id": "main_config"})
    return data.get("admin_share_percent", 20) if data else 20

async def set_admin_share_db(percent):
    await settings_col.update_one(
        {"_id": "main_config"}, 
        {"$set": {"admin_share_percent": int(percent)}}, 
        upsert=True
    )

async def get_user_ads(user_id):
    data = await user_settings_col.find_one({"_id": user_id})
    return data.get("ad_links", DEFAULT_USER_AD_LINKS) if data else DEFAULT_USER_AD_LINKS

async def save_user_ads(user_id, links):
    await user_settings_col.update_one(
        {"_id": user_id}, 
        {"$set": {"ad_links": links}}, 
        upsert=True
    )

async def get_all_users_count():
    return await users_col.count_documents({})
# --- WORKER DB & INIT FUNCTIONS ---
async def get_worker_session():
    data = await settings_col.find_one({"_id": "worker_config"})
    return data.get("session_string") if data else None

async def set_worker_session_db(session_string):
    await settings_col.update_one({"_id": "worker_config"}, {"$set": {"session_string": session_string}}, upsert=True)

async def start_worker():
    global worker_client
    session = await get_worker_session()
    if session:
        try:
            worker_client = Client("worker_session", session_string=session, api_id=int(API_ID), api_hash=API_HASH)
            await worker_client.start()
            logger.info("✅ Worker Session Started!")
        except Exception as e:
            logger.error(f"❌ Worker Error: {e}")
            worker_client = None
# 🔥 DYNAMIC API KEY MANAGER
async def get_server_api(server_name):
    data = await settings_col.find_one({"_id": "api_keys"})
    return data.get(server_name) if data else None

async def set_server_api(server_name, api_key):
    await settings_col.update_one(
        {"_id": "api_keys"}, 
        {"$set": {server_name: api_key}}, 
        upsert=True
    )

def generate_short_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

async def save_post_to_db(post_data, links):
    pid = post_data.get("post_id")
    if not pid:
        pid = generate_short_id()
        post_data["post_id"] = pid
    
    save_data = {
        "_id": pid,
        "details": post_data,
        "links": links,
        "updated_at": datetime.datetime.now()
    }
    await posts_col.replace_one({"_id": pid}, save_data, upsert=True)
    return pid

URL_FONT = "https://raw.githubusercontent.com/mahabub81/bangla-fonts/master/Kalpurush.ttf"
URL_MODEL = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"

async def fetch_url(url, method="GET", data=None, headers=None, json_data=None):
    async with aiohttp.ClientSession() as session:
        try:
            if method == "GET":
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        return await resp.json() if "application/json" in resp.headers.get("Content-Type", "") else await resp.read()
            elif method == "POST":
                async with session.post(url, data=data, json=json_data, headers=headers, ssl=False, timeout=15) as resp:
                    return await resp.text()
        except Exception as e:
            logger.error(f"HTTP Error: {e}")
            return None
    return None

# ====================================================================
# 🔥 AUTO MIRROR UPLOAD FUNCTIONS (8 ADVANCED MULTI-SERVERS)
# ====================================================================

async def upload_to_gofile(file_path):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.gofile.io/servers") as resp:
                data = await resp.json()
                server = data['data']['servers'][0]['name']
            
            url = f"https://{server}.gofile.io/contents/uploadfile"
            with open(file_path, 'rb') as f:
                form = aiohttp.FormData()
                form.add_field('file', f, filename=os.path.basename(file_path))
                async with session.post(url, data=form) as upload_resp:
                    result = await upload_resp.json()
                    if result['status'] == 'ok':
                        return result['data']['downloadPage']
    except Exception as e:
        logger.error(f"GoFile Error: {e}")
    return None

async def upload_to_fileditch(file_path):
    try:
        url = "https://up1.fileditch.com/upload.php"
        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                form = aiohttp.FormData()
                form.add_field('files[]', f, filename=os.path.basename(file_path))
                async with session.post(url, data=form) as resp:
                    result = await resp.json()
                    return result['files'][0]['url']
    except Exception as e:
        logger.error(f"FileDitch Error: {e}")
    return None

async def upload_to_tmpfiles(file_path):
    try:
        url = "https://tmpfiles.org/api/v1/upload"
        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                form = aiohttp.FormData()
                form.add_field('file', f, filename=os.path.basename(file_path))
                async with session.post(url, data=form) as resp:
                    result = await resp.json()
                    if result.get('status') == 'success':
                        return result['data']['url'].replace("api/v1/download/", "")
    except Exception as e:
        logger.error(f"TmpFiles Error: {e}")
    return None

async def upload_to_pixeldrain(file_path):
    try:
        url = "https://pixeldrain.com/api/file"
        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                form = aiohttp.FormData()
                form.add_field('file', f, filename=os.path.basename(file_path))
                async with session.post(url, data=form) as resp:
                    result = await resp.json()
                    if result.get('success'):
                        return f"https://pixeldrain.com/u/{result['id']}"
    except Exception as e:
        logger.error(f"PixelDrain Error: {e}")
    return None

async def upload_to_doodstream(file_path):
    api_key = await get_server_api("doodstream")
    if not api_key:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://doodapi.com/api/upload/server?key={api_key}") as resp:
                data = await resp.json()
                if data.get('msg') != 'OK':
                    return None
                upload_url = data['result']
            
            with open(file_path, 'rb') as f:
                form = aiohttp.FormData()
                form.add_field('file', f, filename=os.path.basename(file_path))
                form.add_field('api_key', api_key)
                async with session.post(upload_url, data=form) as upload_resp:
                    result = await upload_resp.json()
                    if result.get('msg') == 'OK':
                        return result['result'][0]['protected_embed']
    except Exception as e:
        logger.error(f"DoodStream Error: {e}")
    return None

async def upload_to_streamtape(file_path):
    api_credentials = await get_server_api("streamtape")
    if not api_credentials:
        return None 
    try:
        login_id, api_key = api_credentials.split(":")
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.streamtape.com/file/ul?login={login_id}&key={api_key}") as resp:
                data = await resp.json()
                upload_url = data['result']['url']
            
            with open(file_path, 'rb') as f:
                form = aiohttp.FormData()
                form.add_field('file', f, filename=os.path.basename(file_path))
                async with session.post(upload_url, data=form) as upload_resp:
                    result = await upload_resp.json()
                    if result.get('status') == 200:
                        return result['result']['url']
    except Exception as e:
        logger.error(f"Streamtape Error: {e}")
    return None

async def upload_to_filemoon(file_path):
    api_key = await get_server_api("filemoon")
    if not api_key:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://filemoonapi.com/api/upload/server?key={api_key}") as resp:
                data = await resp.json()
                if data.get('msg') != 'OK':
                    return None
                upload_url = data['result']
            
            with open(file_path, 'rb') as f:
                form = aiohttp.FormData()
                form.add_field('file', f, filename=os.path.basename(file_path))
                form.add_field('api_key', api_key)
                async with session.post(upload_url, data=form) as upload_resp:
                    result = await upload_resp.json()
                    if result.get('msg') == 'OK':
                        return f"https://filemoon.sx/e/{result['result'][0]['filecode']}"
    except Exception as e:
        logger.error(f"Filemoon Error: {e}")
    return None

async def upload_to_mixdrop(file_path):
    api_credentials = await get_server_api("mixdrop")
    if not api_credentials or ":" not in api_credentials:
        return None 
    try:
        email, api_key = api_credentials.split(":")
        url = "https://api.mixdrop.co/upload"
        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                form = aiohttp.FormData()
                form.add_field('file', f, filename=os.path.basename(file_path))
                form.add_field('email', email)
                form.add_field('key', api_key)
                async with session.post(url, data=form) as resp:
                    result = await resp.json()
                    if result.get('success'):
                        return result['result']['embedurl']
    except Exception as e:
        logger.error(f"MixDrop Error: {e}")
    return None

# ---- FLASK KEEP-ALIVE ----
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Ultimate SPA Bot Running (With Background Uploading)"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive_pinger():
    while True:
        try:
            requests.get("http://localhost:8080")
            time.sleep(600) 
        except:
            time.sleep(600)

def setup_resources():
    font_name = "kalpurush.ttf"
    if not os.path.exists(font_name):
        try:
            r = requests.get(URL_FONT)
            with open(font_name, "wb") as f:
                f.write(r.content)
        except Exception as e:
            logger.error(f"Font Download Error: {e}")

    model_name = "haarcascade_frontalface_default.xml"
    if not os.path.exists(model_name):
        try:
            r = requests.get(URL_MODEL)
            with open(model_name, "wb") as f:
                f.write(r.content)
        except Exception as e:
            logger.error(f"Model Download Error: {e}")

setup_resources()

def get_font(size=60, bold=False):
    try:
        if os.path.exists("kalpurush.ttf"):
            return ImageFont.truetype("kalpurush.ttf", size)
        font_file = "Poppins-Bold.ttf" if bold else "Poppins-Regular.ttf"
        if os.path.exists(font_file):
            return ImageFont.truetype(font_file, size)
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()

def upload_image_core(file_content):
    try:
        url = "https://catbox.moe/user/api.php"
        data = {"reqtype": "fileupload", "userhash": ""}
        files = {"fileToUpload": ("image.png", file_content, "image/png")}
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.post(url, data=data, files=files, headers=headers, timeout=10, verify=False)
        if response.status_code == 200:
            return response.text.strip()
    except:
        pass

    try:
        url = "https://graph.org/upload"
        files = {'file': ('image.jpg', file_content, 'image/jpeg')}
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.post(url, files=files, headers=headers, timeout=8, verify=False)
        if response.status_code == 200:
            json_data = response.json()
            return "https://graph.org" + json_data[0]["src"]
    except:
        pass

    return None

def upload_to_catbox_bytes(img_bytes):
    try:
        if hasattr(img_bytes, 'read'):
            img_bytes.seek(0)
            data = img_bytes.read()
        else:
            data = img_bytes
        return upload_image_core(data)
    except:
        return None

def upload_to_catbox(file_path):
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        return upload_image_core(data)
    except:
        return None

def extract_tmdb_id(text):
    tmdb_match = re.search(r'themoviedb\.org/(movie|tv)/(\d+)', text)
    if tmdb_match:
        return tmdb_match.group(1), tmdb_match.group(2)
    
    imdb_url_match = re.search(r'imdb\.com/title/(tt\d+)', text)
    if imdb_url_match:
        return "imdb", imdb_url_match.group(1)
    
    imdb_id_match = re.search(r'(tt\d{6,})', text)
    if imdb_id_match:
        return "imdb", imdb_id_match.group(1)
    
    return None, None

async def search_tmdb(query):
    try:
        match = re.search(r'(.+?)\s*\(?(\d{4})\)?$', query)
        name = match.group(1).strip() if match else query.strip()
        year = match.group(2) if match else None
        
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={name}&include_adult=true"
        if year:
            url += f"&year={year}"
        
        data = await fetch_url(url)
        if not data:
            return[]
        return[r for r in data.get("results", []) if r.get("media_type") in["movie", "tv"]][:15]
    except:
        return[]

async def get_tmdb_details(media_type, media_id):
    url = f"https://api.themoviedb.org/3/{media_type}/{media_id}?api_key={TMDB_API_KEY}&append_to_response=credits,similar,images,videos&include_image_language=en,null"
    return await fetch_url(url)

async def create_paste_link(content):
    if not content:
        return None
    url = "https://dpaste.com/api/"
    data = {"content": content, "syntax": "html", "expiry_days": 14, "title": "Movie Post Code"}
    headers = {'User-Agent': 'Mozilla/5.0'}
    link = await fetch_url(url, method="POST", data=data, headers=headers)
    if link and "dpaste.com" in link:
        return link.strip()
    return None

def get_smart_badge_position(pil_img):
    try:
        cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        cascade_path = "haarcascade_frontalface_default.xml"
        
        if not os.path.exists(cascade_path):
            return int(pil_img.height * 0.40) 

        face_cascade = cv2.CascadeClassifier(cascade_path)
        faces = face_cascade.detectMultiScale(gray, 1.1, 4)
        
        if len(faces) > 0:
            lowest_y = 0
            for (x, y, w, h) in faces:
                bottom_of_face = y + h
                if bottom_of_face > lowest_y:
                    lowest_y = bottom_of_face
            
            target_y = lowest_y + 40 
            if target_y > (pil_img.height - 130):
                return 80 
            return target_y
        else:
            return int(pil_img.height * 0.40) 
    except:
        return 200

def apply_badge_to_poster(poster_bytes, text):
    try:
        base_img = Image.open(io.BytesIO(poster_bytes)).convert("RGBA")
        width, height = base_img.size
        font = get_font(size=70) 
        pos_y = get_smart_badge_position(base_img)
        draw = ImageDraw.Draw(base_img)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        
        padding_x, padding_y = 40, 20
        box_w = text_w + (padding_x * 2)
        box_h = text_h + (padding_y * 2)
        pos_x = (width - box_w) // 2
        
        overlay = Image.new('RGBA', base_img.size, (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)
        draw_overlay.rectangle([pos_x, pos_y, pos_x + box_w, pos_y + box_h], fill=(0, 0, 0, 150))
        base_img = Image.alpha_composite(base_img, overlay)
        
        draw = ImageDraw.Draw(base_img)
        cx = pos_x + padding_x
        cy = pos_y + padding_y - 12
        colors =["#FFEB3B", "#FF5722"]
        words = text.split()
        
        if len(words) >= 2:
            draw.text((cx, cy), words[0], font=font, fill=colors[0])
            w1 = draw.textlength(words[0], font=font)
            draw.text((cx + w1 + 15, cy), " ".join(words[1:]), font=font, fill=colors[1])
        else:
            draw.text((cx, cy), text, font=font, fill=colors[0])

        img_buffer = io.BytesIO()
        base_img.save(img_buffer, format="PNG")
        img_buffer.seek(0)
        return img_buffer
    except Exception as e:
        logger.error(f"Badge Error: {e}")
        return io.BytesIO(poster_bytes)

# ============================================================================
# 🔥 ADVANCED HTML GENERATOR (UPDATED WITH 18+ NSFW BLUR EFFECT)
# ============================================================================
def generate_html_code(data, links, user_ad_links_list, owner_ad_links_list, admin_share_percent=20):
    title = data.get("title") or data.get("name")
    overview = data.get("overview", "No plot available.")
    poster = data.get('manual_poster_url') or f"https://image.tmdb.org/t/p/w500{data.get('poster_path')}"
    BTN_TELEGRAM = "https://i.ibb.co/kVfJvhzS/photo-2025-12-23-12-38-56-7587031987190235140.jpg"

    # 🔞 18+ Check Logic
    is_adult = data.get('adult', False) or data.get('force_adult', False)

    # 🔥 Theme CSS Switcher Logic
    theme = data.get("theme", "netflix")
    if theme == "netflix":
        root_css = "--bg-color: #0f0f13; --box-bg: #1a1a24; --text-main: #ffffff; --text-muted: #d1d1d1; --primary: #E50914; --accent: #00d2ff; --border: #2a2a35; --btn-grad: linear-gradient(90deg, #E50914 0%, #ff5252 100%); --btn-shadow: 0 4px 15px rgba(229, 9, 20, 0.4);"
    elif theme == "prime":
        root_css = "--bg-color: #0f171e; --box-bg: #1b2530; --text-main: #ffffff; --text-muted: #8197a4; --primary: #00A8E1; --accent: #00A8E1; --border: #2c3e50; --btn-grad: linear-gradient(90deg, #00A8E1 0%, #00d2ff 100%); --btn-shadow: 0 4px 15px rgba(0, 168, 225, 0.4);"
    elif theme == "light":
        root_css = "--bg-color: #f4f4f9; --box-bg: #ffffff; --text-main: #333333; --text-muted: #555555; --primary: #6200ea; --accent: #6200ea; --border: #dddddd; --btn-grad: linear-gradient(90deg, #6200ea 0%, #b388ff 100%); --btn-shadow: 0 4px 15px rgba(98, 0, 234, 0.4);"
    else:
        root_css = "--bg-color: #0f0f13; --box-bg: #1a1a24; --text-main: #ffffff; --text-muted: #d1d1d1; --primary: #E50914; --accent: #00d2ff; --border: #2a2a35; --btn-grad: linear-gradient(90deg, #E50914 0%, #ff5252 100%); --btn-shadow: 0 4px 15px rgba(229, 9, 20, 0.4);"

    # Extract all necessary movie data
    lang_str = data.get('custom_language', 'Dual Audio').strip()
    if data.get('is_manual'):
        genres_str = "Custom / Unknown" 
        year = "N/A"
        rating = "N/A"
        runtime_str = "N/A"
        cast_names = "N/A"
    else:
        genres_list =[g['name'] for g in data.get('genres',[])]
        genres_str = ", ".join(genres_list) if genres_list else "Movie"
        year = str(data.get("release_date") or data.get("first_air_date") or "----")[:4]
        rating = f"{data.get('vote_average', 0):.1f}/10"
        
        runtime = data.get('runtime') or (data.get('episode_run_time',[0])[0] if data.get('episode_run_time') else "N/A")
        runtime_str = f"{runtime} min" if runtime != "N/A" else "N/A"
        
        cast_list = data.get('credits', {}).get('cast',[])
        cast_names = ", ".join([c['name'] for c in cast_list[:4]]) if cast_list else "Unknown"

    # 🔥 Adult Poster Logic
    if is_adult:
        poster_html = f'''
        <div class="nsfw-container" onclick="revealNSFW(this)">
            <img src="{poster}" alt="{title} Poster" class="nsfw-blur">
            <div class="nsfw-warning">🔞 18+<br><small style="font-size:10px;">Click to Reveal</small></div>
        </div>'''
    else:
        poster_html = f'<img src="{poster}" alt="{title} Poster">'

    # 🔥 Trailer Auto-Fetcher
    trailer_key = ""
    videos = data.get('videos', {}).get('results',[])
    for v in videos:
        if v.get('type') == 'Trailer' and v.get('site') == 'YouTube':
            trailer_key = v.get('key')
            break
            
    trailer_html = ""
    if trailer_key:
        trailer_html = f'''
        <div class="section-title">🎬 Official Trailer</div>
        <div class="video-container">
            <iframe src="https://www.youtube.com/embed/{trailer_key}" allowfullscreen></iframe>
        </div>
        '''

    # 🔥 Screenshots Auto-Fetcher & Adult Blur Logic
    screenshots = data.get('manual_screenshots',[])
    if not screenshots and not data.get('is_manual'):
        backdrops = data.get('images', {}).get('backdrops',[])
        screenshots =[f"https://image.tmdb.org/t/p/w780{b['file_path']}" for b in backdrops[:6]] 
        
    ss_html = ""
    if screenshots:
        if is_adult:
            ss_imgs = "".join([f'<div class="nsfw-container" onclick="revealNSFW(this)"><img src="{img}" alt="Screenshot" class="nsfw-blur"><div class="nsfw-warning"><small>🔞 Tap to View</small></div></div>' for img in screenshots])
        else:
            ss_imgs = "".join([f'<img src="{img}" alt="Screenshot">' for img in screenshots])
            
        ss_html = f'''
        <div class="section-title">📸 Screenshots</div>
        <div class="screenshot-grid">
            {ss_imgs}
        </div>
        '''

    # 🔥 NEW EMBED PLAYER & SERVER SWITCHER LOGIC 🔥
    embed_links =[]
    for link in links:
        if link.get("is_grouped"):
            if link.get('filemoon_url'):
                embed_links.append({'name': '🎬 Filemoon HD', 'url': link['filemoon_url']})
            if link.get('mixdrop_url'):
                m_url = link['mixdrop_url']
                if m_url.startswith("//"): m_url = "https:" + m_url
                embed_links.append({'name': '⚡ MixDrop HD', 'url': m_url})

    embed_html = ""
    if embed_links:
        default_embed = embed_links[0]['url']
        server_btns = ""
        for i, el in enumerate(embed_links):
            b64_url = base64.b64encode(el['url'].encode('utf-8')).decode('utf-8')
            active_class = 'active' if i == 0 else ''
            server_btns += f'<button class="server-tab {active_class}" onclick="changeServer(\'{b64_url}\', this)">📺 {el["name"]}</button>'
            
        embed_html = f'''
        <div class="section-title">🍿 Watch Online (Live Player)</div>
        <div class="embed-container">
            <iframe id="main-embed-player" src="{default_embed}" allowfullscreen="true" frameborder="0"></iframe>
        </div>
        <div class="server-switcher">
            {server_btns}
        </div>
        <hr style="border-top: 1px dashed var(--border); margin: 20px 0;">
        '''

    # 🔥 GENERATE SERVER LIST (GROUPED BY QUALITY/EPISODE) 🔥
    server_list_html = ""
    grouped_links = {}
    for link in links:
        lbl = link.get('label', 'Download Link')
        if lbl not in grouped_links:
            grouped_links[lbl] = []
        grouped_links[lbl].append(link)

    for lbl, grp in grouped_links.items():
        server_list_html += f'<div class="quality-title">📺 {lbl}</div>\n<div class="server-grid">\n'
        for link in grp:
            if link.get("is_grouped"):
                if link.get('filemoon_url'):
                    fm_b64 = base64.b64encode(link['filemoon_url'].encode('utf-8')).decode('utf-8')
                    server_list_html += f'<button class="final-server-btn stream-btn" onclick="goToLink(\'{fm_b64}\')" style="background: #673AB7;">🎬 Watch on Filemoon</button>'
                if link.get('mixdrop_url'):
                    md_b64 = base64.b64encode(link['mixdrop_url'].encode('utf-8')).decode('utf-8')
                    server_list_html += f'<button class="final-server-btn stream-btn" onclick="goToLink(\'{md_b64}\')" style="background: #FFC107; color: #000;">⚡ MixDrop HD</button>'
                if link.get('dood_url'):
                    dood_b64 = base64.b64encode(link['dood_url'].encode('utf-8')).decode('utf-8')
                    server_list_html += f'<button class="final-server-btn stream-btn" onclick="goToLink(\'{dood_b64}\')" style="background: #F57C00;">🎬 DoodStream</button>'
                if link.get('stape_url'):
                    stape_b64 = base64.b64encode(link['stape_url'].encode('utf-8')).decode('utf-8')
                    server_list_html += f'<button class="final-server-btn stream-btn" onclick="goToLink(\'{stape_b64}\')" style="background: #E91E63;">🎥 Streamtape</button>'
                if link.get('gofile_url'):
                    go_b64 = base64.b64encode(link['gofile_url'].encode('utf-8')).decode('utf-8')
                    server_list_html += f'<button class="final-server-btn stream-btn" onclick="goToLink(\'{go_b64}\')">▶️ GoFile Fast</button>'
                
                tg_b64 = base64.b64encode(link['tg_url'].encode('utf-8')).decode('utf-8')
                server_list_html += f'<button class="final-server-btn tg-btn" onclick="goToLink(\'{tg_b64}\')">✈️ Telegram Fast</button>'
                
                if link.get('fileditch_url'):
                    fd_b64 = base64.b64encode(link['fileditch_url'].encode('utf-8')).decode('utf-8')
                    server_list_html += f'<button class="final-server-btn cloud-btn" onclick="goToLink(\'{fd_b64}\')" style="background: #009688;">☁️ Direct Cloud</button>'
                if link.get('tmpfiles_url'):
                    tmp_b64 = base64.b64encode(link['tmpfiles_url'].encode('utf-8')).decode('utf-8')
                    server_list_html += f'<button class="final-server-btn cloud-btn" onclick="goToLink(\'{tmp_b64}\')" style="background: #6A1B9A;">🚀 High-Speed</button>'
                if link.get('pixel_url'):
                    px_b64 = base64.b64encode(link['pixel_url'].encode('utf-8')).decode('utf-8')
                    server_list_html += f'<button class="final-server-btn cloud-btn" onclick="goToLink(\'{px_b64}\')" style="background: #2E7D32;">⚡ Fast Server 2</button>'
            else:
                url_str = link.get('url', '')
                encoded_url = base64.b64encode(url_str.encode('utf-8')).decode('utf-8')
                server_list_html += f'<button class="final-server-btn tg-btn" onclick="goToLink(\'{encoded_url}\')">📥 Download Link</button>'
        server_list_html += '</div>\n'

    # 🔥 REVENUE SHARE LOGIC 🔥
    weighted_ad_list =[]
    if not user_ad_links_list:
        weighted_ad_list = owner_ad_links_list if owner_ad_links_list else["https://google.com"]
    elif not owner_ad_links_list:
        weighted_ad_list = user_ad_links_list
    else:
        total_slots = 100
        admin_slots = int(admin_share_percent)
        user_slots = total_slots - admin_slots
        for _ in range(admin_slots):
            weighted_ad_list.append(random.choice(owner_ad_links_list))
        for _ in range(user_slots):
            weighted_ad_list.append(random.choice(user_ad_links_list))
            
    random.shuffle(weighted_ad_list) 

    style_html = f"""
    <style>
        :root {{ {root_css} }}
        .app-wrapper {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: var(--bg-color); border: 1px solid var(--border); border-radius: 12px; max-width: 650px; margin: 20px auto; padding: 20px; color: var(--text-main); box-sizing: border-box; overflow: hidden; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }}
        .app-wrapper * {{ box-sizing: border-box; }}
        
        .movie-title {{ color: var(--accent); font-size: 24px; font-weight: bold; text-align: center; margin-bottom: 20px; line-height: 1.4; text-shadow: 1px 1px 5px rgba(0,0,0,0.3); }}
        
        .info-box {{ display: flex; flex-direction: row; background: var(--box-bg); border: 1px solid var(--border); border-radius: 12px; padding: 15px; gap: 20px; margin-bottom: 20px; align-items: center; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }}
        @media (max-width: 480px) {{ .info-box {{ flex-direction: column; text-align: center; }} }}
        
        .info-poster img {{ width: 150px; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.5); border: 2px solid var(--border); }}
        
        .info-text {{ flex: 1; text-align: left; font-size: 14px; color: var(--text-muted); line-height: 1.7; }}
        .info-text span {{ color: var(--primary); font-weight: bold; }}
        
        .section-title {{ font-size: 18px; color: var(--text-main); margin: 20px 0 10px; border-bottom: 2px solid var(--primary); display: inline-block; padding-bottom: 5px; font-weight: bold; }}
        
        .plot-box {{ background: rgba(0,0,0,0.05); padding: 15px; border-left: 4px solid var(--primary); border-radius: 4px; font-size: 14px; color: var(--text-muted); margin-bottom: 20px; line-height: 1.6; text-align: justify; border: 1px solid var(--border); }}
        
        .video-container {{ position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden; border-radius: 10px; margin-bottom: 20px; border: 1px solid var(--border); }}
        .video-container iframe {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none; }}
        
        .screenshot-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 25px; }}
        .screenshot-grid img {{ width: 100%; border-radius: 8px; border: 1px solid var(--border); transition: transform 0.3s; box-shadow: 0 2px 8px rgba(0,0,0,0.4); }}
        .screenshot-grid img:hover {{ transform: scale(1.05); z-index: 10; cursor: pointer; }}
        
        .action-grid {{ display: flex; flex-direction: column; gap: 15px; margin-top: 20px; }}
        .main-btn {{ width: 100%; padding: 16px; font-size: 16px; font-weight: bold; text-transform: uppercase; color: #fff; border: none; border-radius: 8px; cursor: pointer; transition: 0.3s; display: flex; justify-content: center; align-items: center; gap: 10px; letter-spacing: 1px; }}
        .btn-watch {{ background: var(--btn-grad); box-shadow: var(--btn-shadow); }}
        .btn-download {{ background: linear-gradient(90deg, #00C9FF 0%, #92FE9D 100%); color: #000; box-shadow: 0 4px 15px rgba(0, 201, 255, 0.4); }}
        .main-btn:disabled {{ filter: grayscale(1); cursor: not-allowed; opacity: 0.8; }}
        
        #view-links {{ display: none; background: var(--box-bg); padding: 20px; border-radius: 10px; border: 1px solid var(--border); text-align: center; animation: fadeIn 0.5s ease-in-out; }}
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        .success-title {{ color: #00e676; font-size: 18px; margin-bottom: 15px; border-bottom: 1px dashed var(--border); padding-bottom: 10px; font-weight: bold; }}
        
        /* 🔥 NEW QUALITY & SERVER GRID STYLE */
        .quality-title {{ font-size: 16px; font-weight: bold; color: var(--accent); margin-top: 20px; margin-bottom: 10px; background: rgba(0,0,0, 0.1); padding: 8px 12px; border-radius: 6px; text-align: left; border-left: 3px solid var(--accent); border: 1px solid var(--border); }}
        .server-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 15px; }}

        .server-list {{ display: flex; flex-direction: column; gap: 12px; margin-top: 15px; }}
        .final-server-btn {{ width: 100%; padding: 14px; font-size: 14px; font-weight: 600; color: #fff; border: none; border-radius: 6px; cursor: pointer; transition: 0.2s; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }}
        .stream-btn {{ background: var(--primary); }}
        .tg-btn {{ background: #0088cc; }}
        .cloud-btn {{ background: #4caf50; }}
        .final-server-btn:hover {{ filter: brightness(1.2); transform: scale(1.02); }}
        
        /* 🔥 EMBED PLAYER STYLES */
        .embed-container {{ position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden; border-radius: 10px; border: 2px solid var(--border); margin-bottom: 15px; background: #000; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }}
        .embed-container iframe {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none; }}
        .server-switcher {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; justify-content: center; }}
        .server-tab {{ background: var(--bg-color); color: var(--text-main); border: 1px solid var(--border); padding: 8px 15px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: bold; transition: 0.3s; box-shadow: 0 2px 5px rgba(0,0,0,0.2); }}
        .server-tab:hover, .server-tab.active {{ background: var(--primary); color: #fff; border-color: var(--primary); }}

        .promo-box {{ margin-top: 25px; text-align: center; }}
        .promo-box img {{ width: 100%; max-width: 300px; border-radius: 20px; border: 1px solid var(--border); }}

        /* 🔞 NSFW CSS STYLES */
        .nsfw-container {{ position: relative; display: inline-block; cursor: pointer; overflow: hidden; border-radius: 8px; width: 100%; height: 100%; }}
        .nsfw-blur {{ filter: blur(25px) !important; transform: scale(1.1); transition: filter 0.5s ease, transform 0.5s ease; width: 100%; height: 100%; display: block; }}
        .nsfw-warning {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); background: rgba(0,0,0,0.85); color: #ff5252; padding: 10px; border-radius: 8px; font-weight: bold; text-align: center; border: 2px solid #ff5252; text-shadow: 1px 1px 5px #000; box-shadow: 0 4px 10px rgba(0,0,0,0.6); z-index: 5; pointer-events: none; }}
    </style>
    """

    script_html = f"""
    <script>
    const AD_LINKS = {json.dumps(weighted_ad_list)};
    
    function startUnlock(btn, type) {{
        let randomAd = AD_LINKS[Math.floor(Math.random() * AD_LINKS.length)];
        window.open(randomAd, '_blank'); 
        
        let buttons = document.querySelectorAll('.main-btn');
        buttons.forEach(b => b.disabled = true);
        
        let timeLeft = 5;
        let timer = setInterval(function() {{
            btn.innerHTML = "⏳ Please Wait... " + timeLeft + "s";
            timeLeft--;
            
            if (timeLeft < 0) {{
                clearInterval(timer);
                btn.innerHTML = "✅ Unlocked Successfully!";
                document.getElementById('view-details').style.display = 'none';
                document.getElementById('view-links').style.display = 'block';
                window.scrollTo({{top: 0, behavior: 'smooth'}});
            }}
        }}, 1000); 
    }}
    
    function goToLink(b64Url) {{
        let realUrl = atob(b64Url);
        window.location.href = realUrl;
    }}
    
    function changeServer(b64Url, btn) {{
        let realUrl = atob(b64Url);
        document.getElementById('main-embed-player').src = realUrl;
        
        let tabs = document.querySelectorAll('.server-tab');
        tabs.forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
    }}

    /* 🔞 NSFW Reveal Script */
    function revealNSFW(container) {{
        let img = container.querySelector('.nsfw-blur');
        if(img) {{
            img.classList.remove('nsfw-blur');
            img.style.transform = 'scale(1)'; /* Reset scale after removing blur */
        }}
        let warning = container.querySelector('.nsfw-warning');
        if(warning) warning.style.display = 'none';
        
        /* Remove cursor pointer so user knows it's already revealed */
        container.style.cursor = 'default';
        container.onclick = null;
    }}
    </script>
    """

    return f"""
    <!-- ADVANCED SINGLE PAGE APP BY BOT -->
    {style_html}
    <div class="app-wrapper">
        <div id="view-details">
            
            <div class="movie-title">{title} ({year})</div>
            
            <!-- Movie Information Box -->
            <div class="info-box">
                <div class="info-poster">
                    {poster_html}
                </div>
                <div class="info-text">
                    <div><span>⭐ Rating:</span> {rating}</div>
                    <div><span>🎭 Genre:</span> {genres_str}</div>
                    <div><span>🗣️ Language:</span> {lang_str}</div>
                    <div><span>⏱️ Runtime:</span> {runtime_str}</div>
                    <div><span>📅 Release:</span> {year}</div>
                    <div><span>👥 Cast:</span> {cast_names}</div>
                </div>
            </div>
            
            <!-- Storyline / Plot -->
            <div class="section-title">📖 Storyline</div>
            <div class="plot-box">
                {overview}
            </div>
            
            <!-- Trailer Section -->
            {trailer_html}

            <!-- Screenshots Section -->
            {ss_html}
            
            <!-- Download Section -->
            <div class="section-title">📥 Links & Player</div>
            <div style="background: rgba(0,0,0,0.1); padding: 12px; border-radius: 6px; font-size: 13px; text-align: center; margin-bottom: 15px; color: var(--text-muted); border: 1px solid var(--border);">
                ℹ️ <b>How to Watch/Download:</b> Click any button below, wait 5 seconds, and the Live Player & Server List will unlock automatically.
            </div>
            
            <div class="action-grid">
                <button class="main-btn btn-watch" onclick="startUnlock(this, 'watch')">
                    ▶️ WATCH ONLINE (LIVE PLAYER)
                </button>
                <button class="main-btn btn-download" onclick="startUnlock(this, 'download')">
                    📥 DOWNLOAD FILES & LINKS
                </button>
            </div>
            
        </div>
        
        <!-- Unlocked Links & Player Area -->
        <div id="view-links">
            <div class="success-title">✅ Successfully Unlocked!</div>
            
            <!-- 🔥 NEW EMBED PLAYER SECTION 🔥 -->
            {embed_html}
            
            <div class="section-title">📥 Download Links</div>
            <p style="font-size: 14px; color: var(--text-muted); margin-bottom: 15px;">Please select a high-speed server or episode below to download.</p>
            
            <div class="server-list">
                {server_list_html}
            </div>
        </div>
        
        <!-- Promotional Content -->
        <div class="promo-box">
            <a href="https://t.me/+6hvCoblt6CxhZjhl" target="_blank"><img src="{BTN_TELEGRAM}"></a>
        </div>
    </div>
    {script_html}
    """
# ---- IMAGE & CAPTION GENERATOR ----
def generate_formatted_caption(data, pid=None):
    title = data.get("title") or data.get("name") or "N/A"
    is_adult = data.get('adult', False) or data.get('force_adult', False)
    
    if data.get('is_manual'):
        year = "Custom"
        rating = "⭐ N/A"
        genres = "Custom"
        language = "N/A"
    else:
        year = (data.get("release_date") or data.get("first_air_date") or "----")[:4]
        rating = f"⭐ {data.get('vote_average', 0):.1f}/10"
        genres = ", ".join([g["name"] for g in data.get("genres",[])] or["N/A"])
        language = data.get('custom_language', '').title()
    
    overview = data.get("overview", "No plot available.")
    caption = f"🎬 **{title} ({year})**\n"
    if pid:
        caption += f"🆔 **ID:** `{pid}` (Use to Edit)\n\n"
    
    if is_adult:
        caption += "⚠️ **WARNING: 18+ Content.**\n_Suitable for mature audiences only._\n\n"
    
    if not data.get('is_manual'):
        caption += f"**🎭 Genres:** {genres}\n**🗣️ Language:** {language}\n**⭐ Rating:** {rating}\n\n"
        
    caption += f"**📝 Plot:** _{overview[:300]}..._\n\n⚠️ _Disclaimer: Informational post only._"
    return caption

def generate_image(data):
    try:
        if data.get('manual_poster_url'):
            poster_url = data.get('manual_poster_url')
        else:
            poster_url = f"https://image.tmdb.org/t/p/w500{data['poster_path']}" if data.get('poster_path') else None
        
        if not poster_url:
            return None, None
            
        poster_bytes = requests.get(poster_url, timeout=10, verify=False).content
        is_adult = data.get('adult', False) or data.get('force_adult', False)
        
        if data.get('badge_text'):
            badge_io = apply_badge_to_poster(poster_bytes, data['badge_text'])
            poster_bytes = badge_io.getvalue()

        poster_img = Image.open(io.BytesIO(poster_bytes)).convert("RGBA").resize((400, 600))
        if is_adult:
            poster_img = poster_img.filter(ImageFilter.GaussianBlur(20))

        bg_img = Image.new('RGBA', (1280, 720), (10, 10, 20))
        backdrop = None
        
        if data.get('backdrop_path') and not data.get('is_manual'):
            try:
                bd_url = f"https://image.tmdb.org/t/p/w1280{data['backdrop_path']}"
                bd_bytes = requests.get(bd_url, timeout=10, verify=False).content
                backdrop = Image.open(io.BytesIO(bd_bytes)).convert("RGBA").resize((1280, 720))
            except:
                pass
        
        if not backdrop:
            backdrop = poster_img.resize((1280, 720))
            
        backdrop = backdrop.filter(ImageFilter.GaussianBlur(10))
        bg_img = Image.alpha_composite(backdrop, Image.new('RGBA', (1280, 720), (0, 0, 0, 150))) 
        bg_img.paste(poster_img, (50, 60), poster_img)
        draw = ImageDraw.Draw(bg_img)
        
        f_bold = get_font(size=36, bold=True)
        f_reg = get_font(size=24, bold=False)

        title = data.get("title") or data.get("name")
        year = (data.get("release_date") or data.get("first_air_date") or "----")[:4]
        
        if data.get('is_manual'):
            year = ""
        if is_adult:
            title += " (18+)"

        draw.text((480, 80), f"{title} {year}", font=f_bold, fill="white", stroke_width=1, stroke_fill="black")
        
        if not data.get('is_manual'):
            draw.text((480, 140), f"⭐ {data.get('vote_average', 0):.1f}/10", font=f_reg, fill="#00e676")
            if is_adult:
                draw.text((480, 180), "⚠️ RESTRICTED CONTENT", font=get_font(18), fill="#FF5252")
            else:
                draw.text((480, 180), " | ".join([g["name"] for g in data.get("genres",[])]), font=get_font(18), fill="#00bcd4")
        
        overview = data.get("overview", "")
        lines =[overview[i:i+80] for i in range(0, len(overview), 80)][:6]
        y_text = 250
        for line in lines:
            draw.text((480, y_text), line, font=f_reg, fill="#E0E0E0")
            y_text += 30
            
        img_buffer = io.BytesIO()
        img_buffer.name = "poster.png"
        bg_img.save(img_buffer, format="PNG")
        img_buffer.seek(0)
        
        return img_buffer, poster_bytes 
    except Exception as e:
        logger.error(f"Generate Image Error: {e}")
        return None, None

# ---- BOT INIT ----
try:
    bot = Client("moviebot", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN)
except Exception as e:
    logger.critical(f"Bot Init Error: {e}")
    exit(1)

def generate_file_caption(details):
    title = details.get("title") or details.get("name") or "Unknown"
    year = (details.get("release_date") or details.get("first_air_date") or "----")[:4]
    rating = f"{details.get('vote_average', 0):.1f}/10"
    
    if details.get('is_manual'):
        genres = "Movie/Series"
        lang = details.get("custom_language") or "N/A"
    else:
        genres = ", ".join([g['name'] for g in details.get('genres', [])][:3])
        lang = details.get("custom_language") or "Dual Audio"
        
    return f"🎬 **{title} ({year})**\n━━━━━━━━━━━━━━━━━━━━━━━\n⭐ Rating: {rating}\n🎭 Genre: {genres}\n🔊 Language: {lang}\n\n🤖 Join: @{(bot.me).username}"

@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message):
    uid = message.from_user.id
    name = message.from_user.first_name
    await add_user(uid, name) 
    
    if len(message.command) > 1:
        payload = message.command[1]
        if payload.startswith("get-"):
            if await is_banned(uid):
                return await message.reply_text("🚫 **Access Denied:** You are banned.")
                
            try:
                msg_id = int(payload.split("-")[1])
                temp_msg = await message.reply_text("🔍 **Searching File...**")
                
                post = await posts_col.find_one({"links.tg_url": {"$regex": f"get-{msg_id}"}})
                if not post:
                    post = await posts_col.find_one({"links.url": {"$regex": f"get-{msg_id}"}})
                    
                if post and "details" in post:
                    final_caption = generate_file_caption(post["details"])
                else:
                    final_caption = f"🎥 **Here is your file!**\n\n🤖 Powered by {client.me.mention}"
                
                file_msg = await client.copy_message(
                    chat_id=uid, 
                    from_chat_id=DB_CHANNEL_ID, 
                    message_id=msg_id, 
                    caption=final_caption, 
                    protect_content=False
                )
                await temp_msg.delete()

                timer = await get_auto_delete_timer()
                if timer > 0:
                    time_str = f"{timer//60} মিনিট" if timer >= 60 else f"{timer} সেকেন্ড"
                    warning_msg = await message.reply_text(
                        f"⚠️ **সতর্কবার্তা:** কপিরাইট এড়াতে এই ফাইলটি **{time_str}** পর ডিলিট হয়ে যাবে!\n\n📥 দয়া করে এখনই ফাইলটি Save করে রাখুন।", 
                        quote=True
                    )
                    asyncio.create_task(auto_delete_task(client, uid,[file_msg.id, warning_msg.id], timer))
                return 
            except Exception as e:
                return await message.reply_text("❌ **File Not Found!**")

    user_conversations.pop(uid, None)
    
    if not await is_authorized(uid):
        return await message.reply_text(
            "⚠️ **অ্যাক্সেস নেই**\n\nএই বটটি ব্যবহার করতে এডমিনের অনুমতির প্রয়োজন।", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{OWNER_USERNAME}")]])
        )

    welcome_text = (
        f"👋 **স্বাগতম {name}!**\n\n"
        "🎬 **Movie & Series Bot (v42 Advanced)**-এ আপনাকে স্বাগতম।\n"
        "📌 **কিভাবে ব্যবহার করবেন?**\n"
        "👉 `/post <নাম>` - অটোমেটিক পোস্ট করতে\n"
        "👉 `/manual` - ম্যানুয়াল পোস্ট করতে\n"
        "👉 `/setapi <server> <key>` - আর্নিং সাইট সেট করতে (Only Admin)\n"
        "👉 `/setadlink <লিংক>` - নিজের অ্যাড লিংক সেট করতে\n"
        "👉 `/mysettings` - নিজের সেটিংস ও লিংক দেখতে\n"
        "👉 `/cancel` - কোনো কাজ বাতিল করতে\n"
        "👉 `/edit <নাম বা ID>` - পোস্ট এডিট করতে"
    )
    await message.reply_text(welcome_text)

# --- CANCEL COMMAND ---
@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, message):
    uid = message.from_user.id
    if uid in user_conversations:
        user_conversations.pop(uid, None)
        await message.reply_text("✅ সব চলমান প্রসেস বাতিল করা হয়েছে। নতুন কমান্ড দিন।")
    else:
        await message.reply_text("⚠️ বাতিল করার মতো কোনো কাজ চলমান নেই।")

# --- ADMIN COMMANDS ---
@bot.on_message(filters.command("auth") & filters.user(OWNER_ID))
async def auth_user(client, message):
    try:
        target_id = int(message.command[1])
        await users_col.update_one({"_id": target_id}, {"$set": {"authorized": True, "banned": False}}, upsert=True)
        await message.reply_text(f"✅ User {target_id} is now AUTHORIZED.")
    except:
        await message.reply_text("❌ Usage: `/auth 123456789`")

@bot.on_message(filters.command("ban") & filters.user(OWNER_ID))
async def ban_user(client, message):
    try:
        target_id = int(message.command[1])
        await users_col.update_one({"_id": target_id}, {"$set": {"banned": True}})
        await message.reply_text(f"🚫 User {target_id} is now BANNED.")
    except:
        await message.reply_text("❌ Usage: `/ban 123456789`")

@bot.on_message(filters.command("setownerads") & filters.user(OWNER_ID))
async def set_owner_ads_cmd(client, message):
    if len(message.command) > 1:
        raw_links = message.text.split(None, 1)[1].split()
        valid =[l if l.startswith("http") else "https://" + l for l in raw_links]
        if valid:
            await set_owner_ads_db(valid)
            await message.reply_text(f"✅ Owner Ads Updated! ({len(valid)} links)")
        else:
            await message.reply_text("❌ No valid links found.")
    else:
        await message.reply_text("⚠️ Usage: `/setownerads link1 link2`")

@bot.on_message(filters.command("setshare") & filters.user(OWNER_ID))
async def set_share_cmd(client, message):
    try:
        percent = int(message.command[1])
        if 0 <= percent <= 100:
            await set_admin_share_db(percent)
            await message.reply_text(f"✅ Share Updated: Admin **{percent}%**")
    except:
        await message.reply_text("⚠️ Usage: `/setshare 20`")

@bot.on_message(filters.command("setdel") & filters.user(OWNER_ID))
async def set_auto_delete_cmd(client, message):
    try:
        seconds = int(message.command[1])
        await set_auto_delete_timer_db(seconds)
        await message.reply_text(f"✅ Timer Updated: **{seconds} seconds**")
    except:
        await message.reply_text("⚠️ Usage: `/setdel 600`")

@bot.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast_msg(client, message):
    if not message.reply_to_message:
        return await message.reply_text("⚠️ Reply to a message.")
    
    msg = await message.reply_text("⏳ Broadcasting...")
    count = 0
    
    async for user in users_col.find({}):
        try:
            await message.reply_to_message.copy(user["_id"])
            count += 1
            await asyncio.sleep(0.1) 
        except:
            pass
            
    await msg.edit_text(f"✅ Broadcast Sent to **{count}** users.")

# 🔥 API KEY MANAGER COMMAND
@bot.on_message(filters.command("setapi") & filters.user(OWNER_ID))
async def set_api_command(client, message):
    try:
        parts = message.text.split(maxsplit=2)
        if len(parts) < 3:
            return await message.reply_text(
                "⚠️ **Format:** `/setapi <server_name> <api_key>`\n"
                "**Supported Servers:** `doodstream`, `streamtape`, `filemoon`, `mixdrop`\n"
                "For Streamtape & MixDrop use format: `email:api_key`"
            )
        
        server_name = parts[1].lower()
        api_key = parts[2].strip()
        
        if server_name not in["doodstream", "streamtape", "filemoon", "mixdrop"]:
            return await message.reply_text("❌ Unsupported server.")
            
        await set_server_api(server_name, api_key)
        await message.reply_text(f"✅ **{server_name.title()}** API Key Saved successfully!")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")
# --- WORKER COMMANDS ---
@bot.on_message(filters.command("setworker") & filters.user(OWNER_ID))
async def set_worker_cmd(client, message):
    global worker_client
    if len(message.command) < 2:
        return await message.reply_text("⚠️ **Format:** `/setworker SESSION_STRING`")
    session_string = message.text.split(None, 1)[1]
    await set_worker_session_db(session_string)
    await message.reply_text("⏳ সেশন সেভ হয়েছে, ওয়ার্কার রিস্টার্ট হচ্ছে...")
    if worker_client:
        try: await worker_client.stop()
        except: pass
    try:
        worker_client = Client("worker_session", session_string=session_string, api_id=int(API_ID), api_hash=API_HASH)
        await worker_client.start()
        await message.reply_text("✅ **Worker Session** সফলভাবে কানেক্ট হয়েছে!")
    except Exception as e:
        await message.reply_text(f"❌ কানেকশন ফেইলড: {e}")

@bot.on_message(filters.command("workerinfo") & filters.user(OWNER_ID))
async def worker_info(client, message):
    if worker_client and worker_client.is_connected:
        me = await worker_client.get_me()
        await message.reply_text(f"🤖 **Worker Status:** Active\n👤 **Name:** {me.first_name}\n🆔 **ID:** `{me.id}`")
    else:
        await message.reply_text("❌ Worker Session কানেক্টেড নেই।")
# --- USER COMMANDS ---
@bot.on_message(filters.command("stats") & filters.user(OWNER_ID))
async def bot_stats(client, message):
    total = await get_all_users_count()
    total_posts = await posts_col.count_documents({})
    admin_share = await get_admin_share()
    await message.reply_text(
        f"📊 **BOT STATS**\n"
        f"👥 Users: {total}\n"
        f"📁 Posts: {total_posts}\n"
        f"💰 Admin Share: {admin_share}%"
    )

# --- MYSETTINGS COMMAND ---
@bot.on_message(filters.command("mysettings") & filters.private)
async def my_settings_cmd(client, message):
    uid = message.from_user.id
    if not await is_authorized(uid):
        return await message.reply_text("🚫 **অ্যাক্সেস নেই**")
        
    user_ads = await get_user_ads(uid)
    ads_text = "\n".join([f"🔗 {ad}" for ad in user_ads]) if user_ads else "❌ কোনো লিংক সেট করা নেই। (Owner Ads ব্যবহার হচ্ছে)"
    
    text = (
        f"⚙️ **Your Settings**\n\n"
        f"👤 **Name:** {message.from_user.first_name}\n"
        f"🆔 **ID:** `{uid}`\n\n"
        f"📢 **Your Ad Links:**\n{ads_text}\n\n"
        f"💡 _Use /setadlink to update your ads._"
    )
    await message.reply_text(text, disable_web_page_preview=True)

@bot.on_message(filters.command("setadlink") & filters.private)
async def set_ad(client, message):
    uid = message.from_user.id
    if not await is_authorized(uid):
        return
        
    if len(message.command) > 1:
        raw_links = message.text.split(None, 1)[1].split()
        valid_links =[l if l.startswith("http") else "https://" + l for l in raw_links]
        if valid_links:
            await save_user_ads(uid, valid_links)
            await message.reply_text("✅ Ad Links Saved!")
    else:
        await message.reply_text("⚠️ Usage: `/setadlink site.com`")

@bot.on_message(filters.command("manual") & filters.private)
async def manual_post_cmd(client, message):
    uid = message.from_user.id
    if not await is_authorized(uid):
        return
        
    user_conversations[uid] = {
        "details": {"is_manual": True, "manual_screenshots":[]},
        "links":[],
        "state": "manual_title"
    }
    await message.reply_text("✍️ **Manual Post Started**\n\nপ্রথমে **টাইটেল (Title)** লিখুন:\n_(যেকোনো মুহূর্তে বাতিল করতে /cancel কমান্ড দিন)_")

@bot.on_message(filters.command("history") & filters.private)
async def history_cmd(client, message):
    uid = message.from_user.id
    if not await is_authorized(uid):
        return
        
    posts = await posts_col.find({}).sort("updated_at", -1).limit(10).to_list(10)
    if not posts:
        return await message.reply_text("❌ No history found.")
        
    text = "📜 **Last 10 Posts:**\n\n"
    for p in posts:
        text += f"🎬 {p['details'].get('title', 'Unknown')} (ID: `{p['_id']}`)\n"
    await message.reply_text(text)

@bot.on_message(filters.command("edit") & filters.private)
async def edit_post_cmd(client, message):
    uid = message.from_user.id
    if not await is_authorized(uid):
        return
        
    if len(message.command) < 2:
        return await message.reply_text("⚠️ Usage: `/edit <Name OR ID>`")
        
    query = message.text.split(" ", 1)[1].strip()
    msg = await message.reply_text("🔍 Searching...")
    
    post = await posts_col.find_one({"_id": query})
    if not post:
        results = await posts_col.find({"details.title": {"$regex": query, "$options": "i"}}).to_list(10)
        if not results:
            results = await posts_col.find({"details.name": {"$regex": query, "$options": "i"}}).to_list(10)
        
        if not results:
            return await msg.edit_text("❌ Not found.")
            
        if len(results) > 1:
            btns = [[InlineKeyboardButton(f"{r['details'].get('title')} ({r['_id']})", callback_data=f"forcedit_{r['_id']}_{uid}")] for r in results]
            return await msg.edit_text("👇 **Select Post:**", reply_markup=InlineKeyboardMarkup(btns))
            
        post = results[0] 
        
    await msg.delete() 
    await start_edit_session(uid, post, message)

async def start_edit_session(uid, post, message):
    user_conversations[uid] = {
        "details": post["details"],
        "links": post.get("links",[]),
        "state": "edit_mode",
        "post_id": post["_id"]
    }
    
    btns = [[InlineKeyboardButton("➕ Add New Link", callback_data=f"add_lnk_edit_{uid}")],[InlineKeyboardButton("✅ Generate New Code", callback_data=f"gen_edit_{uid}")]
    ]
    txt = f"📝 **Editing:** {post['details'].get('title')}\n🆔 `{post['_id']}`\n\n👇 **What to do?**"
    
    if isinstance(message, Message):
        await message.reply_text(txt, reply_markup=InlineKeyboardMarkup(btns))
    else:
        await message.edit_text(txt, reply_markup=InlineKeyboardMarkup(btns))

@bot.on_callback_query(filters.regex("^forcedit_"))
async def force_edit_cb(client, cb):
    try:
        _, pid, uid = cb.data.split("_")
        uid = int(uid)
    except:
        return
        
    post = await posts_col.find_one({"_id": pid})
    if post:
        await start_edit_session(uid, post, cb.message)

@bot.on_message(filters.command("post") & filters.private)
async def post_cmd(client, message):
    uid = message.from_user.id
    if not await is_authorized(uid):
        return
        
    if len(message.command) < 2:
        return await message.reply_text("⚠️ Usage:\n`/post Avatar`")
        
    query = message.text.split(" ", 1)[1].strip()
    msg = await message.reply_text(f"🔎 Processing `{query}`...")
    m_type, m_id = extract_tmdb_id(query)

    if m_type and m_id:
        if m_type == "imdb":
            data = await fetch_url(f"https://api.themoviedb.org/3/find/{m_id}?api_key={TMDB_API_KEY}&external_source=imdb_id")
            res = data.get("movie_results",[]) + data.get("tv_results",[])
            if res:
                m_type, m_id = res[0]['media_type'], res[0]['id']
            else:
                return await msg.edit_text("❌ IMDb ID not found.")
                
        details = await get_tmdb_details(m_type, m_id)
        if not details:
            return await msg.edit_text("❌ Details not found.")
            
        user_conversations[uid] = { "details": details, "links":[], "state": "wait_lang" }
        return await msg.edit_text(f"✅ Found: **{details.get('title') or details.get('name')}**\n\n🗣️ Enter **Language** (e.g. Hindi):")

    results = await search_tmdb(query)
    if not results:
        return await msg.edit_text("❌ No results found.")
        
    buttons = [[InlineKeyboardButton(f"{r.get('title') or r.get('name')} ({str(r.get('release_date','----'))[:4]})", callback_data=f"sel_{r['media_type']}_{r['id']}")] for r in results]
    await msg.edit_text("👇 **Select Content:**", reply_markup=InlineKeyboardMarkup(buttons))

@bot.on_callback_query(filters.regex("^sel_"))
async def on_select(client, cb):
    try:
        _, m_type, m_id = cb.data.split("_")
        details = await get_tmdb_details(m_type, m_id)
        if not details:
            return await cb.message.edit_text("❌ Details not found.")
            
        user_conversations[cb.from_user.id] = { "details": details, "links":[], "state": "wait_lang" }
        await cb.message.edit_text(f"✅ Selected: **{details.get('title') or details.get('name')}**\n\n🗣️ Enter **Language**:")
    except Exception as e:
        logger.error(f"Select error: {e}")

async def down_progress(current, total, status_msg, start_time, last_update_time):
    now = time.time()
    if now - last_update_time[0] >= 3.0 or current == total:
        last_update_time[0] = now
        percent = (current / total) * 100 if total > 0 else 0
        speed = current / (now - start_time) if (now - start_time) > 0 else 1
        eta = (total - current) / speed if speed > 0 else 0
        
        def hbytes(size):
            for unit in['B', 'KB', 'MB', 'GB']:
                if size < 1024.0: return f"{size:.2f} {unit}"
                size /= 1024.0
            return f"{size:.2f} TB"
            
        filled = int(percent / 10)
        bar = "█" * filled + "░" * (10 - filled)
        try:
            await status_msg.edit_text(f"⏳ **২/৩: বট সার্ভারে ডাউনলোড হচ্ছে...**\n\n📊 {bar} {percent:.1f}%\n💾 {hbytes(current)} / {hbytes(total)}\n🚀 স্পিড: {hbytes(speed)}/s | ⏱️ সময় বাকি: {int(eta)}s")
        except:
            pass

# 🔥 BACKGROUND ASYNC UPLOAD (ALLOWS MULTIPLE AT ONCE)
async def process_file_upload(client, message, uid, temp_name):
    convo = user_conversations.get(uid)
    if not convo: return
    
    convo["pending_uploads"] = convo.get("pending_uploads", 0) + 1
    status_msg = await message.reply_text(f"🕒 **সারির অপেক্ষায়...**\n({temp_name})", quote=True)
    
    # ওয়ার্কার চেক: ওয়ার্কার থাকলে সেটা দিয়ে ডাউনলোড হবে, নাহলে মেইন বোট দিয়ে
    uploader = worker_client if (worker_client and worker_client.is_connected) else client
    
    try:
        async with upload_semaphore:
            await status_msg.edit_text(f"⏳ **১/৩: ডাটাবেসে সেভ হচ্ছে...**\n(By: {'Worker' if uploader == worker_client else 'Bot'})")
            copied_msg = await message.copy(chat_id=DB_CHANNEL_ID)
            bot_username = (await client.get_me()).username
            tg_link = f"https://t.me/{bot_username}?start=get-{copied_msg.id}"
            
            start_time = time.time()
            last_update_time =[start_time]
            
            # মিডিয়া ডাউনলোড (ওয়ার্কার বা বোট ব্যবহার করে)
            file_path = await uploader.download_media(
                message, 
                progress=down_progress, 
                progress_args=(status_msg, start_time, last_update_time)
            )

            await status_msg.edit_text(f"⏳ **৩/৩: মাল্টি-সার্ভারে আপলোড হচ্ছে...**")
            
            # প্যারালাল আপলোড
            results = await asyncio.gather(
                upload_to_gofile(file_path), upload_to_fileditch(file_path), upload_to_tmpfiles(file_path),
                upload_to_pixeldrain(file_path), upload_to_doodstream(file_path), upload_to_streamtape(file_path),
                upload_to_filemoon(file_path), upload_to_mixdrop(file_path), return_exceptions=True
            )

            if os.path.exists(file_path): os.remove(file_path)
            
            convo["links"].append({
                "label": temp_name, "tg_url": tg_link, 
                "gofile_url": results[0] if not isinstance(results[0], Exception) else None,
                "fileditch_url": results[1] if not isinstance(results[1], Exception) else None,
                "tmpfiles_url": results[2] if not isinstance(results[2], Exception) else None,
                "pixel_url": results[3] if not isinstance(results[3], Exception) else None,
                "dood_url": results[4] if not isinstance(results[4], Exception) else None,
                "stape_url": results[5] if not isinstance(results[5], Exception) else None,
                "filemoon_url": results[6] if not isinstance(results[6], Exception) else None,
                "mixdrop_url": results[7] if not isinstance(results[7], Exception) else None,
                "is_grouped": True
            })
            await status_msg.edit_text(f"✅ **আপলোড সম্পন্ন:** {temp_name}")
            
    except Exception as e:
        logger.error(f"Upload Error: {e}")
        await status_msg.edit_text(f"❌ Failed: {e}")
    finally:
        convo["pending_uploads"] = max(0, convo.get("pending_uploads", 0) - 1)
    convo = user_conversations.get(uid)
    if not convo:
        return
        
    # Track pending uploads so we can block the user from generating post before completion
    convo["pending_uploads"] = convo.get("pending_uploads", 0) + 1
    
    status_msg = await message.reply_text(f"🕒 **সারির অপেক্ষায় (Queued)...**\n({temp_name})", quote=True)
    
    try:
        async with upload_semaphore:
            await status_msg.edit_text(f"⏳ **১/৩: টেলিগ্রাম ডাটাবেসে সেভ হচ্ছে...**\n({temp_name})")
            copied_msg = await message.copy(chat_id=DB_CHANNEL_ID)
            bot_username = (await client.get_me()).username
            tg_link = f"https://t.me/{bot_username}?start=get-{copied_msg.id}"
            
            start_time = time.time()
            last_update_time =[start_time]
            file_path = await message.download(progress=down_progress, progress_args=(status_msg, start_time, last_update_time))

            await status_msg.edit_text(f"⏳ **৩/৩: এক্সটার্নাল মাল্টি-সার্ভারে আপলোড হচ্ছে...**\n({temp_name})\n_(যেসকল API Key দেওয়া আছে, সেগুলোতেও প্যারালাল আপলোড হচ্ছে)_")
            
            gofile_url, fileditch_url, tmpfiles_url, pixeldrain_url, dood_url, stape_url, filemoon_url, mixdrop_url = await asyncio.gather(
                upload_to_gofile(file_path),
                upload_to_fileditch(file_path),
                upload_to_tmpfiles(file_path),
                upload_to_pixeldrain(file_path),
                upload_to_doodstream(file_path),
                upload_to_streamtape(file_path),
                upload_to_filemoon(file_path),
                upload_to_mixdrop(file_path)
            )

            if os.path.exists(file_path):
                os.remove(file_path)
                
            convo["links"].append({
                "label": temp_name,
                "tg_url": tg_link,
                "gofile_url": gofile_url,
                "fileditch_url": fileditch_url,
                "tmpfiles_url": tmpfiles_url,
                "pixel_url": pixeldrain_url,
                "dood_url": dood_url,
                "stape_url": stape_url,
                "filemoon_url": filemoon_url,
                "mixdrop_url": mixdrop_url,
                "is_grouped": True
            })

            await status_msg.edit_text(f"✅ **আপলোড সম্পন্ন:** {temp_name}")
            
    except Exception as e:
        logger.error(f"Upload Error: {e}")
        await status_msg.edit_text(f"❌ Failed: {e}")
    finally:
        convo["pending_uploads"] = max(0, convo.get("pending_uploads", 0) - 1)


@bot.on_message(filters.private & (filters.text | filters.video | filters.document | filters.photo) & ~filters.command(["start", "post", "manual", "edit", "history", "setadlink", "mysettings", "auth", "ban", "stats", "broadcast", "setownerads", "setshare", "setdel", "setapi", "cancel", "repost", "setup", "myconfig", "delsetup"]))
async def text_handler(client, message):
    uid = message.from_user.id
    if uid not in user_conversations:
        return
    
    convo = user_conversations[uid]
    state = convo.get("state")
    text = message.text.strip() if message.text else ""
    
    if state == "manual_title":
        convo["details"]["title"] = text
        convo["state"] = "manual_plot"
        await message.reply_text("📝 এবার মুভির **গল্প/Plot** লিখুন:")
        
    elif state == "manual_plot":
        convo["details"]["overview"] = text
        convo["state"] = "manual_poster"
        await message.reply_text("🖼️ এবার একটি **পোস্টার (Photo)** সেন্ড করুন:")
        
    elif state == "manual_poster":
        if not message.photo:
            return await message.reply_text("⚠️ দয়া করে ছবি পাঠান।")
            
        msg = await message.reply_text("⏳ Processing Poster...")
        try:
            photo_path = await message.download()
            img_url = upload_to_catbox(photo_path) 
            os.remove(photo_path)
            
            if img_url:
                convo["details"]["manual_poster_url"] = img_url
                convo["state"] = "ask_screenshots"
                await msg.edit_text("✅ Poster Uploaded!\n\n📸 **Add Custom Screenshots?**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📸 Add", callback_data=f"ss_yes_{uid}"), InlineKeyboardButton("⏭️ Skip", callback_data=f"ss_no_{uid}")]]))
            else:
                await msg.edit_text("❌ Upload Failed.")
        except:
            await msg.edit_text("❌ Error uploading.")

    elif state == "wait_screenshots":
        if not message.photo:
            return await message.reply_text("⚠️ Please send PHOTO.")
            
        msg = await message.reply_text("⏳ Uploading SS...")
        try:
            photo_path = await message.download()
            ss_url = upload_to_catbox(photo_path)
            os.remove(photo_path)
            
            if ss_url:
                if "manual_screenshots" not in convo["details"]:
                    convo["details"]["manual_screenshots"] =[]
                convo["details"]["manual_screenshots"].append(ss_url)
                await msg.edit_text(f"✅ Screenshot Added!\nSend another or click DONE.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ DONE", callback_data=f"ss_done_{uid}")]]))
        except:
            pass

    elif state == "wait_lang":
        convo["details"]["custom_language"] = text
        convo["state"] = "wait_quality"
        await message.reply_text("💿 Enter **Quality**:")
        
    elif state == "wait_quality":
        convo["details"]["custom_quality"] = text
        convo["state"] = "ask_links"
        await message.reply_text("🔗 Add Download Links?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Links", callback_data=f"lnk_yes_{uid}"), InlineKeyboardButton("🏁 Finish", callback_data=f"lnk_no_{uid}")]]))
        
    elif state == "wait_link_name_custom":
        convo["temp_name"] = text
        convo["state"] = "wait_link_url"
        await message.reply_text(f"✅ নাম সেট: **{text}**\n\n🔗 এবার **URL** দিন অথবা **ভিডিও ফাইলটি** ফরোয়ার্ড করুন:")
        
    elif state == "wait_link_url":
        if message.video or message.document:
            # We use the async background task so we don't have to wait!
            asyncio.create_task(process_file_upload(client, message, uid, convo["temp_name"]))

            if convo.get("post_id"):
                 convo["state"] = "edit_mode"
                 await message.reply_text(
                    f"✅ **{convo['temp_name']}** ব্যাকগ্রাউন্ডে আপলোড শুরু হয়েছে!\nআপনি চাইলে আপলোড শেষ হওয়ার আগেই আরেকটি ফাইল অ্যাড করতে পারেন।", 
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Another Link", callback_data=f"add_lnk_edit_{uid}"), InlineKeyboardButton("✅ Finish", callback_data=f"gen_edit_{uid}")]]))
            else:
                convo["state"] = "ask_links"
                await message.reply_text(
                    f"✅ **{convo['temp_name']}** ব্যাকগ্রাউন্ডে আপলোড শুরু হয়েছে!\nআপনি চাইলে আপলোড শেষ হওয়ার আগেই আরেকটি ফাইল অ্যাড করতে পারেন।", 
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Another", callback_data=f"lnk_yes_{uid}"), InlineKeyboardButton("🏁 Finish", callback_data=f"lnk_no_{uid}")]]))

        elif text.startswith("http"):
            convo["links"].append({"label": convo["temp_name"], "url": text, "is_grouped": False})
            if convo.get("post_id"):
                 convo["state"] = "edit_mode"
                 await message.reply_text(f"✅ Saved! Link: `{text}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Link", callback_data=f"add_lnk_edit_{uid}"), InlineKeyboardButton("✅ Finish", callback_data=f"gen_edit_{uid}")]]))
            else:
                convo["state"] = "ask_links"
                await message.reply_text(f"✅ Saved! Total: {len(convo['links'])}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Another", callback_data=f"lnk_yes_{uid}"), InlineKeyboardButton("🏁 Finish", callback_data=f"lnk_no_{uid}")]]))
        else:
            await message.reply_text("⚠️ Invalid Input. URL or File required.")

    # 🔥 NEW BATCH HANDLER
    elif state == "wait_batch_files":
        if text.lower() == "/done":
            if convo.get("post_id"):
                 convo["state"] = "edit_mode"
                 await message.reply_text(f"✅ **Batch Files Accepted!**\nঅপেক্ষা করুন, আপলোড শেষ হলে Finish এ ক্লিক করবেন।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Link", callback_data=f"add_lnk_edit_{uid}"), InlineKeyboardButton("✅ Finish", callback_data=f"gen_edit_{uid}")]]))
            else:
                convo["state"] = "ask_links"
                await message.reply_text(f"✅ **Batch Files Accepted!**\nঅপেক্ষা করুন, আপলোড শেষ হলে Finish এ ক্লিক করবেন।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Another", callback_data=f"lnk_yes_{uid}"), InlineKeyboardButton("🏁 Finish", callback_data=f"lnk_no_{uid}")]]))
        elif message.video or message.document:
            file_name = getattr(message.video, "file_name", None) or getattr(message.document, "file_name", None)
            if not file_name:
                file_name = f"Episode {len(convo.get('links',[])) + convo.get('pending_uploads', 0) + 1}"
            
            asyncio.create_task(process_file_upload(client, message, uid, file_name))
        else:
            await message.reply_text("⚠️ দয়া করে ভিডিও/ফাইল দিন অথবা শেষ হলে /done লিখুন।")

    elif state == "wait_badge_text":
        convo["details"]["badge_text"] = text
        await message.reply_text("🛡️ **Safety Check:**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Safe", callback_data=f"safe_yes_{uid}"), InlineKeyboardButton("🔞 18+", callback_data=f"safe_no_{uid}")]]))

@bot.on_callback_query(filters.regex("^ss_"))
async def ss_cb(client, cb):
    try:
        action, uid = cb.data.rsplit("_", 1)
        uid = int(uid)
    except:
        return
        
    if action == "ss_yes":
        user_conversations[uid]["state"] = "wait_screenshots"
        user_conversations[uid]["details"]["manual_screenshots"] =[]
        await cb.message.edit_text("📸 **Send Screenshots now.**")
    else:
        user_conversations[uid]["state"] = "wait_lang"
        await cb.message.edit_text("🗣️ Enter **Language** (e.g. Hindi):")

@bot.on_callback_query(filters.regex("^lnk_"))
async def link_cb(client, cb):
    try:
        action, uid = cb.data.rsplit("_", 1)
        uid = int(uid)
    except:
        return
        
    if action == "lnk_yes":
        user_conversations[uid]["state"] = "wait_link_name"
        btns = [[InlineKeyboardButton("🎬 1080p", callback_data=f"setlname_1080p_{uid}"),
             InlineKeyboardButton("🎬 720p", callback_data=f"setlname_720p_{uid}"),
             InlineKeyboardButton("🎬 480p", callback_data=f"setlname_480p_{uid}")],[InlineKeyboardButton("✍️ Custom", callback_data=f"setlname_custom_{uid}"), 
             InlineKeyboardButton("📁 Default", callback_data=f"setlname_telegram_{uid}")],[InlineKeyboardButton("📦 Batch Upload (Series)", callback_data=f"setlname_batch_{uid}")]
        ]
        await cb.message.edit_text("👇 বাটনের ধরন বা কোয়ালিটি সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(btns))
    else:
        # Check if uploads are still processing
        if user_conversations.get(uid, {}).get("pending_uploads", 0) > 0:
            return await cb.answer("⏳ ফাইল আপলোড শেষ হওয়া পর্যন্ত অপেক্ষা করুন...", show_alert=True)
            
        user_conversations[uid]["state"] = "wait_badge_text"
        await cb.message.edit_text("🖼️ **Badge Text?**\n\nলিখে পাঠান অথবা Skip করুন:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Skip", callback_data=f"skip_badge_{uid}")]]))

@bot.on_callback_query(filters.regex("^add_lnk_edit_"))
async def add_lnk_edit(client, cb):
    uid = int(cb.data.split("_")[-1])
    if uid in user_conversations:
        user_conversations[uid]["state"] = "wait_link_name"
        btns = [[InlineKeyboardButton("🎬 1080p", callback_data=f"setlname_1080p_{uid}"),
             InlineKeyboardButton("🎬 720p", callback_data=f"setlname_720p_{uid}"),
             InlineKeyboardButton("🎬 480p", callback_data=f"setlname_480p_{uid}")],[InlineKeyboardButton("✍️ Custom", callback_data=f"setlname_custom_{uid}"), 
             InlineKeyboardButton("📁 Default", callback_data=f"setlname_telegram_{uid}")],[InlineKeyboardButton("📦 Batch Upload (Series)", callback_data=f"setlname_batch_{uid}")]
        ]
        await cb.message.edit_text("👇 বাটনের ধরন বা কোয়ালিটি সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(btns))

@bot.on_callback_query(filters.regex("^setlname_"))
async def set_lname_cb(client, cb):
    try:
        _, action, uid = cb.data.split("_")
        uid = int(uid)
    except:
        return
        
    if action in["1080p", "720p", "480p"]:
        user_conversations[uid]["temp_name"] = action
        user_conversations[uid]["state"] = "wait_link_url"
        await cb.message.edit_text(f"✅ কোয়ালিটি সেট: **{action}**\n\n🔗 এবার **URL** বা **ভিডিও ফাইল** দিন:")
    elif action == "custom":
        user_conversations[uid]["state"] = "wait_link_name_custom"
        await cb.message.edit_text("📝 কাস্টম বাটনের নাম লিখুন (যেমন: 4K, 1080p 60fps বা Ep-01):")
    elif action == "batch":
        user_conversations[uid]["state"] = "wait_batch_files"
        await cb.message.edit_text("📦 **Batch Mode:**\n\nআপনার সিরিজের সব ফাইল বা এপিসোড একসাথে ফরোয়ার্ড করুন।\nফাইলের নামগুলোই এপিসোড নাম হিসেবে সেট হবে।\nসব দেওয়া হলে টাইপ করুন: `/done`")
    else:
        user_conversations[uid]["temp_name"] = "Telegram Files"
        user_conversations[uid]["state"] = "wait_link_url"
        await cb.message.edit_text("✅ বাটন সেট। 🔗 এবার **URL** বা **ভিডিও ফাইল** দিন:")

@bot.on_callback_query(filters.regex("^gen_edit_"))
async def gen_edit_finish(client, cb):
    uid = int(cb.data.split("_")[-1])
    if uid in user_conversations:
        # Check if uploads are still processing
        if user_conversations[uid].get("pending_uploads", 0) > 0:
            return await cb.answer("⏳ ফাইল আপলোড শেষ হওয়া পর্যন্ত অপেক্ষা করুন...", show_alert=True)
            
        await cb.answer("⏳ Generating...", show_alert=False)
        await generate_final_post(client, uid, cb.message)

@bot.on_callback_query(filters.regex("^skip_badge_"))
async def skip_badge_cb(client, cb):
    uid = int(cb.data.split("_")[-1])
    if uid in user_conversations:
        user_conversations[uid]["details"]["badge_text"] = None
        await cb.message.edit_text("🛡️ **Safety Check:**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Safe", callback_data=f"safe_yes_{uid}"), InlineKeyboardButton("🔞 18+", callback_data=f"safe_no_{uid}")]]))

# 🔥 THEME SELECTION & SAFETY CHECK OVERRIDE
@bot.on_callback_query(filters.regex("^safe_"))
async def safety_cb(client, cb):
    try:
        action, uid = cb.data.rsplit("_", 1)
        uid = int(uid)
    except:
        return
        
    user_conversations[uid]["details"]["force_adult"] = True if action == "safe_no" else False
    
    # Ask for Theme before Generating Post
    btns = [[InlineKeyboardButton("🔴 Netflix (Dark)", callback_data=f"theme_netflix_{uid}")],[InlineKeyboardButton("🔵 Prime (Blue)", callback_data=f"theme_prime_{uid}")],[InlineKeyboardButton("⚪ Anime (Light)", callback_data=f"theme_light_{uid}")]
    ]
    await cb.message.edit_text("🎨 **ওয়েবসাইটের থিম (Theme) সিলেক্ট করুন:**", reply_markup=InlineKeyboardMarkup(btns))

@bot.on_callback_query(filters.regex("^theme_"))
async def theme_cb(client, cb):
    try:
        _, theme_name, uid = cb.data.split("_")
        uid = int(uid)
    except:
        return
    
    user_conversations[uid]["details"]["theme"] = theme_name
    await generate_final_post(client, uid, cb.message)

async def generate_final_post(client, uid, message):
    convo = user_conversations.get(uid)
    if not convo:
        return await message.edit_text("❌ Session expired.")
        
    status_msg = await message.edit_text("⏳ **Generating Final Post...**")

    try:
        pid = await save_post_to_db(convo["details"], convo["links"])
        loop = asyncio.get_running_loop()
        img_io, poster_bytes = await loop.run_in_executor(None, generate_image, convo["details"])

        if convo["details"].get("badge_text") and poster_bytes:
            new_poster = await loop.run_in_executor(None, upload_to_catbox_bytes, poster_bytes)
            if new_poster:
                convo["details"]["manual_poster_url"] = new_poster 
        
        html = generate_html_code(convo["details"], convo["links"], await get_user_ads(uid), await get_owner_ads(), await get_admin_share())
        caption = generate_formatted_caption(convo["details"], pid)
        convo["final"] = {"html": html}
        
        btns = [[InlineKeyboardButton("📄 Get Blogger Code", callback_data=f"get_code_{uid}")]]
        
        if img_io:
            await client.send_photo(message.chat.id, img_io, caption=caption, reply_markup=InlineKeyboardMarkup(btns))
            await status_msg.delete()
        else:
            await client.send_message(message.chat.id, caption, reply_markup=InlineKeyboardMarkup(btns))
            await status_msg.delete()
            
    except Exception as e:
        await status_msg.edit_text(f"❌ **Error:** `{e}`")

@bot.on_callback_query(filters.regex("^get_code_"))
async def get_code(client, cb):
    try:
        _, _, uid = cb.data.rsplit("_", 2)
        uid = int(uid)
    except:
        return
        
    data = user_conversations.get(uid)
    if not data or "final" not in data:
        return await cb.answer("Expired.", show_alert=True)
    
    await cb.answer("⏳ Generating Code...", show_alert=False)
    link = await create_paste_link(data["final"]["html"])
    
    if link:
        await cb.message.reply_text(f"✅ **Code Ready!**\n\n👇 Copy:\n{link}", disable_web_page_preview=True)
    else:
        file = io.BytesIO(data["final"]["html"].encode())
        file.name = "post.html"
        await client.send_document(cb.message.chat.id, file, caption="⚠️ Link failed. Download File.")

# --- PLUGIN LOADER FUNCTION ---
async def load_plugins():
    """plugins ফোল্ডার থেকে অটোমেটিক সব মডিউল লোড করবে"""
    # কারেন্ট ডিরেক্টরিতে plugins নামে ফোল্ডার খুঁজবে
    plugins_path = os.path.join(os.path.dirname(__file__), "plugins")
    
    # যদি ফোল্ডার না থাকে তবে তৈরি করবে
    if not os.path.exists(plugins_path):
        os.makedirs(plugins_path)
        return

    print("🔌 Loading plugins...")
    # plugins ফোল্ডারের ভেতরকার সব .py ফাইল চেক করবে
    for loader, module_name, is_pkg in pkgutil.iter_modules([plugins_path]):
        try:
            # মডিউলটি ইম্পোর্ট করছে
            module = importlib.import_module(f"plugins.{module_name}")
            
            # যদি ঐ ফাইলে register নামে কোনো ফাংশন থাকে, সেটা কল করবে
            if hasattr(module, "register"):
                await module.register(bot)
            print(f"✅ Plugin Loaded: {module_name}")
        except Exception as e:
            print(f"❌ Failed to load plugin {module_name}: {e}")

# --- UPDATED MAIN FUNCTION ---
async def main():
    # ১. প্রথমে মেইন বট স্টার্ট হবে
    await bot.start()
    
    # ২. এরপর আপনার সব প্লাগইন/ফিচার লোড হবে
    await load_plugins()
    
    # ৩. এরপর ওয়ার্কার স্টার্ট হবে
    await start_worker() 
    
    print("✅ Bot and Worker are Online with Plugin Support!")
    
    # বটকে চালু রাখার জন্য ওয়েট করবে
    await asyncio.Event().wait()

# --- ENTRY POINT ---
if __name__ == "__main__":
    # Flask এবং Pinger আগের মতোই থাকবে
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    ping_thread = Thread(target=keep_alive_pinger)
    ping_thread.daemon = True
    ping_thread.start()
    
    print("🚀 Ultimate SPA Bot is Starting with Plugin System...")

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
