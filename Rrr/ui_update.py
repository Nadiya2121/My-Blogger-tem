import sys
import os
import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
from aiogram import Router, types, BaseMiddleware
import urllib.parse

# মেইন ফাইল থেকে ভেরিয়েবলগুলো টেনে আনা
main_module = sys.modules.get('__main__')
TMDB_API_KEY = getattr(main_module, 'TMDB_API_KEY', os.getenv("TMDB_API_KEY", ""))
db = getattr(main_module, 'db', None)
app = getattr(main_module, 'app', None)
dp = getattr(main_module, 'dp', None)

web_router = APIRouter()

# ==========================================
# ১. ফ্রন্টএন্ড এবং অ্যাডমিন UI ইনজেকশন
# ==========================================

USER_UI_INJECTION = """
<style>
/* নিয়ন ক্যাটাগরি বাটন CSS */
.neon-grid {
    display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; padding: 10px 15px; margin-top: 10px;
}
.neon-btn {
    background: transparent; color: white; padding: 8px 14px; border-radius: 20px;
    font-size: 11px; font-weight: 700; border: 2px solid #ff007f; text-transform: uppercase;
    box-shadow: 0 0 8px rgba(255, 0, 127, 0.4), inset 0 0 5px rgba(255, 0, 127, 0.2);
    cursor: pointer; transition: 0.3s; font-family: 'Poppins', sans-serif;
}
.neon-btn.active { background: #ff007f; box-shadow: 0 0 15px #ff007f; }

/* চ্যাপ্টা স্লাইডার CSS */
.wide-slider-container {
    display: flex; overflow-x: auto; scroll-snap-type: x mandatory; gap: 15px; padding: 10px 20px 20px; scrollbar-width: none;
}
.wide-slider-container::-webkit-scrollbar { display: none; }
.slide-item {
    min-width: 95%; scroll-snap-align: center; position: relative; border-radius: 16px; cursor: pointer;
}
.slide-item img {
    width: 100%; height: 200px; object-fit: cover; border-radius: 16px; border: 2px solid #ff007f; box-shadow: 0 0 15px rgba(255, 0, 127, 0.5);
}
.slide-content {
    position: absolute; bottom: 0; left: 0; width: 100%; background: linear-gradient(to top, rgba(0,0,0,0.95), transparent);
    padding: 25px 15px 15px; border-radius: 0 0 16px 16px; display: flex; align-items: center; justify-content: space-between;
}
.slide-title { font-size: 15px; font-weight: 800; color: white; width: 65%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* নতুন সার্চ বার CSS */
.new-search-container { padding: 15px 20px 5px; }
.new-search-wrapper { position: relative; }
.new-search-box {
    width: 100%; background: #1a1d24; border: 1px solid #333; padding: 14px 45px 14px 20px;
    border-radius: 30px; color: white; outline: none; font-size: 14px;
}
.new-search-icon { position: absolute; right: 20px; top: 50%; transform: translateY(-50%); color: #00bfff; font-size: 18px; }
</style>

<script>
document.addEventListener("DOMContentLoaded", function() {
    const homeTab = document.getElementById("home-tab");
    if(homeTab) {
        // পুরনো ক্যাটাগরি, হিরো ব্যানার এবং সার্চ বার লুকিয়ে ফেলা
        const oldCatScroll = document.getElementById("categoryList");
        const oldHero = document.getElementById("heroBanner");
        const searchTabBox = document.querySelector("#search-tab .search-box");
        if(oldCatScroll) oldCatScroll.style.display = 'none';
        if(oldHero) oldHero.style.display = 'none';
        if(searchTabBox) searchTabBox.style.display = 'none'; // পুরনো সার্চ হাইড

        // নতুন সার্চ বার তৈরি
        const searchDiv = document.createElement("div");
        searchDiv.className = "new-search-container";
        searchDiv.innerHTML = `
            <div class="new-search-wrapper">
                <input type="text" class="new-search-box" id="topSearchInput" placeholder="Search movies...">
                <i class="fa-solid fa-search new-search-icon"></i>
            </div>
        `;
        homeTab.insertBefore(searchDiv, homeTab.firstChild);

        // সার্চ ইভেন্ট লিংক করা
        document.getElementById("topSearchInput").addEventListener("input", (e) => {
            const val = e.target.value;
            const origSearch = document.getElementById("searchInput");
            if(origSearch) {
                origSearch.value = val;
                switchTab('search');
                origSearch.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });

        // নিয়ন ক্যাটাগরি তৈরি
        const neonCatDiv = document.createElement("div");
        neonCatDiv.className = "neon-grid";
        neonCatDiv.id = "neonCategoryList";
        homeTab.insertBefore(neonCatDiv, searchDiv.nextSibling);

        // চ্যাপ্টা স্লাইডার তৈরি
        const sliderDiv = document.createElement("div");
        sliderDiv.className = "wide-slider-container";
        sliderDiv.id = "wideSliderList";
        homeTab.insertBefore(sliderDiv, neonCatDiv.nextSibling);

        // ডাটা লোড করা
        loadCustomCategories();
        loadWideSlider();
    }
});

async function loadCustomCategories() {
    try {
        const res = await fetch('/api/plugin/get_categories');
        const cats = await res.json();
        const container = document.getElementById("neonCategoryList");
        
        let html = `<button class="neon-btn active" onclick="filterNeonGenre('All', this)">HOME</button>`;
        cats.forEach(c => {
            html += `<button class="neon-btn" onclick="filterNeonGenre('${c}', this)">${c}</button>`;
        });
        container.innerHTML = html;
    } catch(e) {}
}

function filterNeonGenre(genre, btn) {
    document.querySelectorAll('.neon-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    if(typeof filterGenre === 'function') filterGenre(genre);
}

async function loadWideSlider() {
    try {
        const res = await fetch('/api/plugin/get_slider');
        const movies = await res.json();
        const container = document.getElementById("wideSliderList");
        
        container.innerHTML = movies.map(m => `
            <div class="slide-item" onclick="openQualitySheet('${m.title.replace(/'/g, "\\\\'")}')">
                <img src="${m.cover.startsWith('http') ? m.cover : '/api/image/'+m.cover}">
                <div class="slide-content">
                    <div class="slide-title">${m.title}</div>
                    <button style="background:#e11d48; color:white; border:none; padding:8px 14px; border-radius:20px; font-size:12px; font-weight:bold;"><i class="fa-solid fa-play"></i> Watch</button>
                </div>
            </div>
        `).join('');
    } catch(e) {}
}
</script>
"""

ADMIN_UI_INJECTION = """
<script>
document.addEventListener("DOMContentLoaded", function() {
    const settingsTab = document.getElementById("settings");
    if(settingsTab) {
        const catBox = document.createElement("div");
        catBox.className = "glass-panel col-span-2 mt-6";
        catBox.innerHTML = `
            <h2 class="text-xl font-bold text-gray-200 mb-4"><i class="fa-solid fa-layer-group text-pink-400"></i> Manage Neon Categories</h2>
            <div class="flex gap-2 mb-4">
                <input type="text" id="newNeonCat" placeholder="Enter Category Name (e.g. BANGLA DUBBED)">
                <button class="btn-action bg-pink-600" onclick="addNeonCat()">Add Category</button>
            </div>
            <div id="neonCatAdminList" class="flex flex-wrap gap-2"></div>
        `;
        settingsTab.querySelector('.grid').appendChild(catBox);
        loadAdminNeonCats();
    }
});

async function loadAdminNeonCats() {
    const res = await fetch('/api/plugin/get_categories');
    const cats = await res.json();
    document.getElementById("neonCatAdminList").innerHTML = cats.map(c => `
        <div class="bg-gray-800 border border-pink-500 text-pink-400 px-3 py-1 rounded-full flex items-center gap-2 text-sm font-bold">
            ${c} <i class="fa-solid fa-times cursor-pointer text-red-500 hover:text-white" onclick="delNeonCat('${c}')"></i>
        </div>
    `).join('');
}

async function addNeonCat() {
    const val = document.getElementById('newNeonCat').value.trim();
    if(!val) return;
    await fetch('/api/plugin/add_category', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name: val}) });
    document.getElementById('newNeonCat').value = '';
    loadAdminNeonCats();
}

async function delNeonCat(name) {
    await fetch('/api/plugin/del_category?name=' + encodeURIComponent(name), { method:'DELETE' });
    loadAdminNeonCats();
}
</script>
"""

class UIUpdateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        
        if "text/html" in content_type:
            body = b""
            async for chunk in response.body_iterator: body += chunk
            html = body.decode("utf-8")

            if "<title>MovieZone Premium</title>" in html:
                html = html.replace("</body>", USER_UI_INJECTION + "\n</body>")
            elif "<title>MovieZone Super Admin</title>" in html:
                html = html.replace("</body>", ADMIN_UI_INJECTION + "\n</body>")

            headers = dict(response.headers)
            headers.pop("content-length", None)
            return HTMLResponse(content=html, status_code=response.status_code, headers=headers)
        return response

if app:
    app.add_middleware(UIUpdateMiddleware)
    app.include_router(web_router)


# ==========================================
# ২. API রাউটস (ক্যাটাগরি এবং স্লাইডার)
# ==========================================

class CatModel(BaseModel):
    name: str

@web_router.get("/api/plugin/get_categories")
async def get_categories():
    if db is not None:
        cfg = await db.settings.find_one({"id": "neon_categories"})
        if cfg and "list" in cfg:
            return cfg["list"]
    return ["BANGLA", "ENGLISH", "HINDI", "K DRAMA", "18+ ADULT", "ANIME"]

@web_router.post("/api/plugin/add_category")
async def add_category(data: CatModel):
    if db is not None:
        await db.settings.update_one({"id": "neon_categories"}, {"$addToSet": {"list": data.name.upper()}}, upsert=True)
    return {"ok": True}

@web_router.delete("/api/plugin/del_category")
async def del_category(name: str):
    if db is not None:
        await db.settings.update_one({"id": "neon_categories"}, {"$pull": {"list": name}})
    return {"ok": True}

@web_router.get("/api/plugin/get_slider")
async def get_slider():
    slider_movies = []
    if db is not None:
        async for m in db.movies.find({"cover_id": {"$exists": True}}).sort("_id", -1).limit(6):
            slider_movies.append({"title": m["title"], "cover": m["cover_id"]})
    return slider_movies


# ==========================================
# ৩. বটের মেইন কমান্ড হাইজ্যাক (Monkey Patching & Middleware)
# ==========================================

# (A) TMDB ফাংশন মডিফাই করা (মেইন ফাইলের ফাংশনটি রিপ্লেস হয়ে যাবে)
async def patched_fetch_and_send_tmdb_details(m, tmdb_id, media_type, state, user_id):
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
                backdrop_path = res.get("backdrop_path")
                
                if not poster_path: 
                    return await main_module.bot.send_message(user_id, "⚠️ এই মুভির কোনো পোস্টার নেই!")
                
                poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
                cover_url = f"https://image.tmdb.org/t/p/w780{backdrop_path}" if backdrop_path else poster_url
                genres = [g["name"] for g in res.get("genres", [])]
                
                try:
                    await main_module.bot.send_photo(chat_id=user_id, photo=poster_url, caption=f"✅ <b>{title}</b>\n(স্লাইডারের চ্যাপ্টা ছবিও অটোমেটিক সেট হয়ে গেছে!)", parse_mode="HTML")
                    await state.update_data(title=title, photo_id=poster_url, cover_id=cover_url, genres=genres)
                    await state.set_state(main_module.AdvancedUpload.waiting_for_language)
                    await main_module.bot.send_message(user_id, "🗣 <b>এই মুভিটির ভাষা সিলেক্ট করুন:</b>", reply_markup=main_module.get_language_keyboard(), parse_mode="HTML")
                except Exception:
                    pass
            else:
                await main_module.bot.send_message(user_id, "⚠️ TMDB থেকে ডাটা আনতে সমস্যা হয়েছে!")

# মেইন ফাইলের ফাংশন ওভাররাইট করে দিলাম!
main_module.fetch_and_send_tmdb_details = patched_fetch_and_send_tmdb_details


# (B) ম্যানুয়াল আপলোড মডিফাই করার জন্য বটের মিডলওয়্যার
class AdvancedUploadInterceptor(BaseMiddleware):
    async def __call__(self, handler, event, data):
        state = data.get("state")
        if not state:
            return await handler(event, data)
            
        current_state = await state.get_state()
        
        # ১. ম্যানুয়াল আপলোডে লম্বা ছবির পর চ্যাপ্টা ছবি চাওয়া
        if isinstance(event, types.Message) and event.photo:
            if current_state == "AdvancedUpload:waiting_for_manual_photo":
                # মেইন ফাইলের কাজ হাইজ্যাক করা হলো
                await state.update_data(photo_id=event.photo[-1].file_id)
                await state.set_state("waiting_for_wide_cover")
                await event.answer("✅ লম্বা পোস্টার পেয়েছি!\nএবার স্লাইডারের জন্য <b>চ্যাপ্টা কভার (Wide Photo)</b> সেন্ড করুন:", parse_mode="HTML")
                return # এখানেই আটকে দিলাম, মেইন কোড রান হবে না
                
            elif current_state == "waiting_for_wide_cover":
                # চ্যাপ্টা ছবি নিয়ে মেইন কোডের টাইটেলে পাঠিয়ে দিলাম
                await state.update_data(cover_id=event.photo[-1].file_id)
                await state.set_state(main_module.AdvancedUpload.waiting_for_manual_title)
                await event.answer("✅ চ্যাপ্টা কভার পেয়েছি!\nএবার <b>মুভির নাম</b> লিখে পাঠান:", parse_mode="HTML")
                return

        # ২. ফাইল আপলোডের পর চ্যাপ্টা ছবি ডেটাবেসে আপডেট করা
        result = await handler(event, data) # আগে মেইন কোডের ফাইল সেভ হতে দিলাম
        
        if current_state == "AdvancedUpload:waiting_for_files" and isinstance(event, types.Message) and (event.video or event.document):
            state_data = await state.get_data()
            cover_id = state_data.get("cover_id")
            title = state_data.get("title")
            if cover_id and title:
                file_id = event.video.file_id if event.video else event.document.file_id
                # ফাইল সেভ হওয়ার পর ডেটাবেসে গিয়ে cover_id টা যুক্ত করে দিলাম!
                await main_module.db.movies.update_one(
                    {"title": title, "file_id": file_id},
                    {"$set": {"cover_id": cover_id}}
                )
                
        return result

# মিডলওয়্যার যুক্ত করে দিলাম, যাতে সে মেইন কোডগুলোকে কন্ট্রোল করতে পারে
if dp:
    dp.message.middleware(AdvancedUploadInterceptor())
