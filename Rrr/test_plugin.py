from aiogram import Router, types
from aiogram.filters import Command
from fastapi import APIRouter

# ==========================================
# ১. রাউটার তৈরি (এগুলো পরিবর্তন করবেন না)
# ==========================================
# টেলিগ্রাম বটের কমান্ড হ্যান্ডেল করার জন্য
bot_router = Router()

# ওয়েবসাইট বা অ্যাপের API হ্যান্ডেল করার জন্য
web_router = APIRouter()


# ==========================================
# ২. টেলিগ্রাম বটের নতুন কমান্ড বা ফিচার (Bot Section)
# ==========================================

# যখন কেউ বটে /testplugin লিখে সেন্ড করবে
@bot_router.message(Command("testplugin"))
async def my_test_command(message: types.Message):
    text = (
        f"👋 হ্যালো <b>{message.from_user.first_name}</b>!\n\n"
        "✅ <b>আপনার প্লাগিন সিস্টেমটি ১০০% সফলভাবে কাজ করছে!</b>\n"
        "<i>এই মেসেজটি main.py থেকে নয়, বরং plugins ফোল্ডার থেকে এসেছে!</i>"
    )
    await message.answer(text, parse_mode="HTML")


# ==========================================
# ৩. ওয়েবসাইট বা অ্যাপের নতুন API (Web/API Section)
# ==========================================

# যখন কেউ আপনার ওয়েবসাইটের /api/testplugin লিংকে যাবে
@web_router.get("/api/testplugin")
async def my_test_api():
    # এটি JSON ডাটা রিটার্ন করবে (যা অ্যাপ বা ওয়েবসাইটে ব্যবহার করা যায়)
    return {
        "ok": True,
        "message": "প্লাগিন API সফলভাবে লোড হয়েছে!",
        "author": "আপনার নাম বা বটের নাম"
    }
