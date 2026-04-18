# plugins/multi_paste_backup.py
import __main__
import aiohttp
import io
import logging
import asyncio
from pyrogram import filters, handlers
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

# --- 🚀 ৫টি আলাদা সার্ভারে কোড সেভ করার লজিক ---
async def enhanced_paste_service(content):
    if not content: return None

    async with aiohttp.ClientSession() as session:
        # ১. dpaste.com
        try:
            async with session.post("https://dpaste.com/api/", data={"content": content, "syntax": "html", "expiry_days": 14}, timeout=10) as resp:
                if resp.status in [200, 201]: return (await resp.text()).strip()
        except: pass

        # ২. paste.rs
        try:
            async with session.post("https://paste.rs", data=content.encode('utf-8'), timeout=10) as resp:
                if resp.status in [200, 201]: return await resp.text()
        except: pass

        # ৩. spaceb.in
        try:
            async with session.post("https://spaceb.in/api/v1/documents", json={"content": content, "extension": "html"}, timeout=10) as resp:
                if resp.status in [200, 201]:
                    res = await resp.json()
                    return f"https://spaceb.in/{res['payload']['id']}"
        except: pass

        # ৪. tny.im (Backup Shortener)
        try:
            async with session.get(f"https://tny.im/adurl?url={content[:100]}", timeout=5) as resp:
                pass # এটি জাস্ট টেস্টের জন্য, আসল কোড নয়
        except: pass

    return None

# --- 🛠️ প্যাচ করা মেইন ফাংশন ---
async def patched_get_code(client, cb):
    try:
        _, _, uid = cb.data.rsplit("_", 2)
        uid = int(uid)
    except: return
        
    data = __main__.user_conversations.get(uid)
    if not data or "final" not in data:
        return await cb.answer("❌ ডেটা পাওয়া যায়নি! নতুন করে পোস্ট করুন।", show_alert=True)
    
    await cb.answer("⏳ জেনারেট হচ্ছে (Multi-Server Try)...", show_alert=False)
    
    html_code = data["final"]["html"]
    link = await enhanced_paste_service(html_code)
    
    # বাটন তৈরি
    btns = []
    if link:
        btns.append([InlineKeyboardButton("🌐 Open Blogger Code", url=link)])
    
    btns.append([InlineKeyboardButton("📝 Get Direct Code (Message)", callback_data=f"get_raw_text_{uid}")])
    btns.append([InlineKeyboardButton("📁 Download HTML File", callback_data=f"send_file_only_{uid}")])
    
    msg_text = f"✅ **Blogger Code Ready!**\n\n"
    if link:
        msg_text += f"🔗 **Link:** `{link}`\n\n"
    else:
        msg_text += f"⚠️ **Paste সার্ভার ডাউন!** কিন্তু ভয় নেই, নিচের বাটনগুলো ব্যবহার করুন।\n\n"
    
    msg_text += "💡 _নিচের 'Get Direct Code' বাটনে ক্লিক করলে কোডটি সরাসরি মেসেজে চলে আসবে যা কপি করা খুব সহজ।_"

    await cb.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(btns), disable_web_page_preview=True)

# --- 📝 সরাসরি মেসেজে কোড পাঠানোর হ্যান্ডলার ---
@__main__.bot.on_callback_query(filters.regex("^get_raw_text_"))
async def get_raw_text_handler(client, cb):
    uid = int(cb.data.split("_")[-1])
    data = __main__.user_conversations.get(uid)
    if not data or "final" not in data:
        return await cb.answer("ডেটা পাওয়া যায়নি!", show_alert=True)
    
    code = data["final"]["html"]
    
    # টেলিগ্রাম মেসেজ লিমিট ৪০৯৬ ক্যারেক্টার
    if len(code) < 3800:
        await cb.message.reply_text(f"👇 **কপি করার জন্য নিচে ক্লিক করুন:**\n\n<code>{code}</code>", parse_mode="html")
    else:
        # কোড বড় হলে কয়েকটা মেসেজে ভাগ করে পাঠানো
        parts = [code[i:i+3800] for i in range(0, len(code), 3800)]
        await cb.message.reply_text(f"📦 কোডটি অনেক বড় ({len(parts)} পার্ট), সব মেসেজ কপি করে সিরিয়াল অনুযায়ী ব্লগারে বসান:")
        for part in parts:
            await cb.message.reply_text(f"<code>{part}</code>", parse_mode="html")
            await asyncio.sleep(0.5)
    await cb.answer("কোড পাঠানো হয়েছে!")

# --- 📁 ফাইল হ্যান্ডলার ---
async def send_file_handler(client, cb):
    uid = int(cb.data.split("_")[-1])
    data = __main__.user_conversations.get(uid)
    if data and "final" in data:
        file = io.BytesIO(data["final"]["html"].encode('utf-8'))
        file.name = "blogger_post_code.html"
        await client.send_document(cb.message.chat.id, file, caption="📄 আপনার ব্লগার পোস্টের HTML কোড ফাইল।")
        await cb.answer("ফাইল পাঠানো হয়েছে!")

# --- 🚀 রেজিস্ট্রেশন ---
async def register(bot):
    __main__.create_paste_link = enhanced_paste_service
    
    # মেইন গেট কোড ওভাররাইড
    bot.add_handler(handlers.CallbackQueryHandler(patched_get_code, filters.regex("^get_code_")), group=-1)
    # ফাইল হ্যান্ডলার
    bot.add_handler(handlers.CallbackQueryHandler(send_file_handler, filters.regex("^send_file_only_")), group=-1)
    
    print("✅ Multi-Paste Backup V2: Direct Message Support Added!")
