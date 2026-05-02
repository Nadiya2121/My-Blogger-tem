import sys
import os
import aiohttp
import urllib.parse
import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from bson import ObjectId
from pydantic import BaseModel

# মেইন ফাইল থেকে ভেরিয়েবলগুলো টেনে আনা
main_module = sys.modules.get('__main__')
TMDB_API_KEY = getattr(main_module, 'TMDB_API_KEY', os.getenv("TMDB_API_KEY", ""))
db = getattr(main_module, 'db', None)
app = getattr(main_module, 'app', None)

bot_router = getattr(main_module, 'Router', lambda: None)()
web_router = APIRouter()

# ==========================================
# ১. ইউজারের পেজের জন্য ইনজেকশন (মোবাইল ফ্রেন্ডলি)
# ==========================================

INDEX_INJECTION = """
<style>
/* মোবাইল ওয়েভ-ভিউ এর জন্য কাস্টম সিএসএস */
.compact-req-box {
    padding: 10px;
    margin: 0 auto;
    max-width: 100%;
    box-sizing: border-box;
}
.horizontal-scroll {
    display: flex;
    overflow-x: auto;
    gap: 12px;
    padding-bottom: 10px;
    scrollbar-width: none; /* Firefox */
}
.horizontal-scroll::-webkit-scrollbar {
    display: none; /* Chrome/Safari */
}
.public-req-card {
    min-width: 90px;
    width: 90px;
    background: var(--surface);
    border-radius: 8px;
    padding: 5px;
    text-align: center;
    border: 1px solid var(--surface-light);
}
.public-req-card img {
    width: 100%;
    height: 110px;
    object-fit: cover;
    border-radius: 6px;
    background: #222;
}
.public-req-title {
    font-size: 10px;
    color: white;
    font-weight: bold;
    margin-top: 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.public-req-user {
    font-size: 9px;
    color: var(--text-muted);
}
.mobile-input {
    width: 100%;
    padding: 10px 10px 10px 35px;
    border-radius: 8px;
    background: var(--surface);
    border: 1px solid var(--surface-light);
    color: white;
    outline: none;
    font-size: 13px;
    box-sizing: border-box;
}
</style>

<script>
document.addEventListener("DOMContentLoaded", function() {
    // টপ রিকোয়েস্ট বার (একটু ছোট করা হয়েছে)
    const topBar = document.createElement("div");
    topBar.className = "top-request-bar";
    topBar.style.cssText = "background: linear-gradient(90deg, #e11d48, #be123c); color: white; padding: 10px 15px; font-size: 12px; font-weight: bold; display: flex; justify-content: space-between; align-items: center; cursor: pointer; box-shadow: 0 4px 10px rgba(225, 29, 72, 0.3); margin: 10px; border-radius: 8px;";
    topBar.innerHTML = '<span><i class="fa-solid fa-magnifying-glass-plus"></i> মুভি রিকোয়েস্ট করুন</span><i class="fa-solid fa-chevron-right"></i>';
    
    const header = document.querySelector("header");
    if(header) header.parentNode.insertBefore(topBar, header.nextSibling);

    const reqModal = document.getElementById("reqModal");
    if(reqModal) {
        const bottomSheet = reqModal.querySelector(".bottom-sheet");
        if(bottomSheet) {
            const title = bottomSheet.querySelector("h2");
            if(title) title.innerHTML = '<i class="fa-solid fa-bolt text-red-500"></i> স্মার্ট মুভি রিকোয়েস্ট';
            
            const oldInputDiv = bottomSheet.querySelector("div[style*='display:flex']");
            if(oldInputDiv) {
                oldInputDiv.outerHTML = `
                    <div class="compact-req-box">
                        <!-- ট্যাব বাটন -->
                        <div style="display:flex; gap:10px; margin-bottom:12px;">
                            <button id="btnAutoSearch" onclick="toggleReqMode('auto')" style="flex:1; padding:8px; background:var(--primary); color:white; border-radius:6px; font-size:12px; font-weight:bold; border:none;"><i class="fa-solid fa-search"></i> অটোমেটিক</button>
                            <button id="btnManualReq" onclick="toggleReqMode('manual')" style="flex:1; padding:8px; background:var(--surface-light); color:var(--text-muted); border-radius:6px; font-size:12px; font-weight:bold; border:none;"><i class="fa-solid fa-keyboard"></i> ম্যানুয়াল</button>
                        </div>

                        <!-- Auto Search Section -->
                        <div id="autoSearchSection">
                            <div style="position:relative; margin-bottom:10px;">
                                <input type="text" id="tmdbSearchInput" class="mobile-input" placeholder="🔍 ইংলিশে মুভির নাম লিখুন...">
                                <i class="fa-solid fa-search" style="position:absolute; left:12px; top:50%; transform:translateY(-50%); color:#888; font-size:12px;"></i>
                            </div>
                            <div id="tmdbResults" style="width:100%; max-height:25vh; overflow-y:auto; margin-bottom:10px;"></div>
                        </div>

                        <!-- Manual Search Section (No Poster) -->
                        <div id="manualSearchSection" style="display:none; margin-bottom:10px;">
                            <div style="position:relative; margin-bottom:10px;">
                                <input type="text" id="manualMovieName" class="mobile-input" placeholder="🎬 মুভির সঠিক নাম লিখুন...">
                                <i class="fa-solid fa-clapperboard" style="position:absolute; left:12px; top:50%; transform:translateY(-50%); color:#888; font-size:12px;"></i>
                            </div>
                            <textarea id="manualReqMessage" placeholder="💬 মেসেজ (যেমন: বাংলা ডাবিং লাগবে)..." style="width:100%; padding:10px; border-radius:8px; background:var(--surface); border:1px solid var(--surface-light); color:white; outline:none; font-size:12px; height:60px; resize:none; margin-bottom:10px; box-sizing:border-box;"></textarea>
                            <button onclick="submitManualRequest()" style="width:100%; padding:10px; background:var(--primary); color:white; font-size:13px; font-weight:bold; border-radius:8px; border:none;"><i class="fa-solid fa-paper-plane"></i> রিকোয়েস্ট পাঠান</button>
                        </div>

                        <!-- Public Requests Board -->
                        <div style="margin-top:15px; border-top:1px solid var(--surface-light); padding-top:10px;">
                            <div style="font-size:12px; font-weight:bold; color:#ccc; margin-bottom:10px;"><i class="fa-solid fa-fire text-orange-500"></i> অন্যান্য ইউজারদের রিকোয়েস্ট</div>
                            <div id="publicRequestsContainer" class="horizontal-scroll">
                                <span style="color:#666; font-size:11px;">লোড হচ্ছে...</span>
                            </div>
                        </div>
                    </div>
                `;
            }

            // ট্যাব লজিক
            window.toggleReqMode = function(mode) {
                document.getElementById('autoSearchSection').style.display = (mode === 'auto') ? 'block' : 'none';
                document.getElementById('manualSearchSection').style.display = (mode === 'manual') ? 'block' : 'none';
                
                document.getElementById('btnAutoSearch').style.background = (mode === 'auto') ? 'var(--primary)' : 'var(--surface-light)';
                document.getElementById('btnAutoSearch').style.color = (mode === 'auto') ? 'white' : 'var(--text-muted)';
                
                document.getElementById('btnManualReq').style.background = (mode === 'manual') ? 'var(--primary)' : 'var(--surface-light)';
                document.getElementById('btnManualReq').style.color = (mode === 'manual') ? 'white' : 'var(--text-muted)';
            };

            // অটো সার্চ লজিক
            let tmdbTimeout = null;
            document.getElementById("tmdbSearchInput").addEventListener("input", (e) => {
                clearTimeout(tmdbTimeout);
                const q = e.target.value.trim();
                const resDiv = document.getElementById("tmdbResults");
                if(!q) { resDiv.innerHTML = ""; return; }
                
                resDiv.innerHTML = '<p style="text-align:center; color:gray; padding:10px; font-size:12px;"><i class="fa-solid fa-spinner fa-spin"></i> খোঁজা হচ্ছে...</p>';
                
                tmdbTimeout = setTimeout(() => {
                    fetch('/api/plugin/tmdb_search?q=' + encodeURIComponent(q))
                    .then(r => r.json())
                    .then(data => {
                        if(!data.results || data.results.length === 0) {
                            resDiv.innerHTML = '<p style="text-align:center; color:#e11d48; padding:10px; font-size:12px;">পাওয়া যায়নি! ম্যানুয়াল ট্যাবে গিয়ে নাম লিখুন।</p>';
                            return;
                        }
                        resDiv.innerHTML = data.results.map(m => `
                            <div style="display: flex; align-items: center; background: var(--bg); padding: 8px; border-radius: 8px; margin-bottom: 8px; border: 1px solid var(--surface-light);">
                                <img src="${m.poster || 'https://via.placeholder.com/40x60/252933/FFFFFF?text=No'}" style="width: 40px; height: 60px; border-radius: 4px; object-fit: cover; margin-right: 10px;">
                                <div style="flex: 1; overflow: hidden;">
                                    <div style="font-size: 13px; font-weight: bold; color: white; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${m.title}</div>
                                    <div style="font-size: 10px; color: var(--text-muted); margin-bottom:4px;">${m.year}</div>
                                    <input type="text" id="msg_${m.id}" placeholder="মেসেজ (ঐচ্ছিক)..." style="width:90%; padding:4px 6px; border-radius:4px; border:1px solid #444; background:#111; color:white; font-size:10px;">
                                </div>
                                <button style="background: var(--primary); color: white; padding: 6px 10px; border-radius: 6px; border: none; font-weight: bold; font-size: 11px; margin-left:5px;" onclick="submitFinalRequest('${m.title.replace(/'/g, "\\\\'")}', '${m.poster}', document.getElementById('msg_${m.id}').value)">Request</button>
                            </div>
                        `).join('');
                    });
                }, 500);
            });
        }
    }

    // പাবলিক রিকোয়েস্ট ফেচ করা
    window.loadPublicRequests = async function() {
        try {
            const res = await fetch('/api/plugin/public_requests');
            const data = await res.json();
            const container = document.getElementById('publicRequestsContainer');
            if(data.length === 0) {
                container.innerHTML = '<span style="color:#666; font-size:11px;">এখনো কেউ রিকোয়েস্ট করেনি।</span>';
                return;
            }
            container.innerHTML = data.map(r => `
                <div class="public-req-card">
                    <img src="${r.poster || 'https://via.placeholder.com/90x110/252933/FFFFFF?text=Movie'}" onerror="this.src='https://via.placeholder.com/90x110/252933/FFFFFF?text=No+Img';">
                    <div class="public-req-title">${r.movie}</div>
                    <div class="public-req-user"><i class="fa-solid fa-user"></i> ${r.uname.substring(0,8)}..</div>
                </div>
            `).join('');
        } catch(e) {}
    };

    topBar.addEventListener("click", () => {
        openModal('reqModal');
        loadRequests();
        loadPublicRequests(); // পপ-আপ খুললেই অন্যদের রিকোয়েস্ট লোড হবে
    });

    // সাবমিট রিকোয়েস্ট
    window.submitFinalRequest = async function(title, posterUrl, message) {
        const uname = document.getElementById('uName').innerText || "User";
        try {
            const res = await fetch('/api/request', { 
                method: 'POST', headers: {'Content-Type': 'application/json'}, 
                body: JSON.stringify({
                    uid: typeof uid !== 'undefined' ? uid : '0', 
                    uname: uname, 
                    movie: title, 
                    initData: typeof INIT_DATA !== 'undefined' ? INIT_DATA : ''
                })
            });
            const data = await res.json();
            if(data.ok) {
                await fetch('/api/plugin/update_req_meta', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ movie: title, poster: posterUrl, message: message })
                });

                showToast("✅ সফলভাবে রিকোয়েস্ট পাঠানো হয়েছে!");
                
                document.getElementById('tmdbSearchInput').value = '';
                document.getElementById('tmdbResults').innerHTML = '';
                document.getElementById('manualMovieName').value = '';
                document.getElementById('manualReqMessage').value = '';
                
                loadRequests();
                loadPublicRequests(); // সাথে সাথে পাবলিক বোর্ড আপডেট হবে
            }
        } catch(e) {
            showToast("❌ সমস্যা হয়েছে, আবার চেষ্টা করুন।");
        }
    };

    window.submitManualRequest = function() {
        const title = document.getElementById('manualMovieName').value.trim();
        const msg = document.getElementById('manualReqMessage').value.trim();
        if(!title) { alert("মুভির নাম লিখুন!"); return; }
        // ম্যানুয়ালে পোস্টার নেই, তাই ফাঁকা স্ট্রিং যাবে
        submitFinalRequest(title, '', msg);
    };
});
</script>
"""

# ==========================================
# ২. অ্যাডমিন পেজের জন্য ইনজেকশন 
# ==========================================

ADMIN_INJECTION = """
<script>
document.addEventListener("DOMContentLoaded", function() {
    const navContainer = document.querySelector(".w-64");
    if(navContainer) {
        const reqBtn = document.createElement("div");
        reqBtn.className = "nav-btn";
        reqBtn.innerHTML = '<i class="fa-solid fa-code-pull-request"></i> Movie Requests';
        reqBtn.onclick = function() {
            switchTab('admin_requests_tab', this);
            loadAdminRequests();
        };
        navContainer.appendChild(reqBtn);
    }

    const mainContainer = document.querySelector(".flex-1");
    if(mainContainer) {
        const reqTab = document.createElement("div");
        reqTab.id = "admin_requests_tab";
        reqTab.className = "tab-section";
        reqTab.innerHTML = `
            <div class="glass-panel">
                <h2 class="text-xl font-bold text-gray-200 mb-4"><i class="fa-solid fa-bullhorn text-red-400"></i> User Movie Requests</h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm whitespace-nowrap">
                        <thead class="bg-gray-800 text-gray-300">
                            <tr>
                                <th class="p-4 rounded-tl-lg">Poster</th>
                                <th class="p-4">Movie & Message</th>
                                <th class="p-4">Requested By</th>
                                <th class="p-4">Votes</th>
                                <th class="p-4 rounded-tr-lg text-right">Action</th>
                            </tr>
                        </thead>
                        <tbody id="adminReqTable"><tr><td colspan="5" class="text-center p-8">Loading requests...</td></tr></tbody>
                    </table>
                </div>
            </div>
        `;
        mainContainer.appendChild(reqTab);
    }

    window.loadAdminRequests = async function() {
        try {
            const res = await fetch('/api/plugin/admin_get_requests');
            const data = await res.json();
            let html = '';
            if(data.length === 0) html = '<tr><td colspan="5" class="text-center p-8 text-gray-400">No pending requests found.</td></tr>';
            else {
                data.forEach(r => {
                    const posterImg = r.poster ? r.poster : 'https://via.placeholder.com/50x75/252933/FFFFFF?text=No+Img';
                    const userMsg = r.message ? `<div class="text-xs text-yellow-300 mt-2" style="white-space:normal; max-width:250px;"><i class="fa-solid fa-comment-dots"></i> ${r.message}</div>` : '';
                    
                    html += `
                    <tr class="border-b border-gray-800 hover:bg-gray-800 transition">
                        <td class="p-4"><img src="${posterImg}" onerror="this.onerror=null; this.src='https://via.placeholder.com/50x75/252933/FFFFFF?text=No+Img';" style="width:50px; height:75px; border-radius:6px; object-fit:cover; background:#222;"></td>
                        <td class="p-4">
                            <div class="font-bold text-white text-base">${r.movie}</div>
                            ${userMsg}
                        </td>
                        <td class="p-4 text-gray-400"><i class="fa-solid fa-user-astronaut"></i> ${r.uname}</td>
                        <td class="p-4 text-yellow-400 font-bold"><i class="fa-solid fa-caret-up"></i> ${r.votes}</td>
                        <td class="p-4 text-right">
                            <button onclick="deleteReqAdmin('${r.id}')" class="bg-red-900 bg-opacity-40 text-red-400 hover:bg-red-600 hover:text-white px-3 py-1 rounded-lg transition">
                                <i class="fa-solid fa-trash-can"></i> Done
                            </button>
                        </td>
                    </tr>`;
                });
            }
            document.getElementById('adminReqTable').innerHTML = html;
        } catch(e) {}
    };

    window.deleteReqAdmin = async function(id) {
        if(!confirm("Are you sure you want to complete/delete this request?")) return;
        try {
            await fetch('/api/plugin/admin_del_req/' + id, { method: 'DELETE' });
            loadAdminRequests();
        } catch(e) {}
    };
});
</script>
"""

class DOMInjectorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        
        if "text/html" in content_type:
            if hasattr(response, "body"):
                html = response.body.decode("utf-8")
            else:
                body = b""
                async for chunk in response.body_iterator:
                    body += chunk
                html = body.decode("utf-8")

            if "<title>MovieZone Premium</title>" in html:
                html = html.replace("</body>", INDEX_INJECTION + "\n</body>")
            elif "<title>MovieZone Super Admin</title>" in html:
                html = html.replace("</body>", ADMIN_INJECTION + "\n</body>")

            headers = dict(response.headers)
            headers.pop("content-length", None) 
            return HTMLResponse(content=html, status_code=response.status_code, headers=headers)
            
        return response

if app:
    app.add_middleware(DOMInjectorMiddleware)
    app.include_router(web_router)


# ==========================================
# ৩. API রাউটস
# ==========================================

@web_router.get("/api/plugin/tmdb_search")
async def tmdb_search(q: str):
    if not TMDB_API_KEY:
        return {"results": []}
    url = f"https://api.tmdb.org/3/search/multi?api_key={TMDB_API_KEY}&query={urllib.parse.quote(q)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = []
                    for item in data.get("results", [])[:8]:
                        title = item.get("title") or item.get("name")
                        if not title: continue
                        date = item.get("release_date") or item.get("first_air_date", "")
                        year = date[:4] if date else "Unknown"
                        poster = f"https://image.tmdb.org/t/p/w200{item['poster_path']}" if item.get("poster_path") else ""
                        # আইডির সাহায্যে মেসেজ ইনপুট আলাদা করতে
                        results.append({"id": item.get("id", 0), "title": title, "year": year, "poster": poster})
                    return {"results": results}
    except Exception:
        pass
    return {"results": []}

class ReqMeta(BaseModel):
    movie: str
    poster: str = ""
    message: str = ""

@web_router.post("/api/plugin/update_req_meta")
async def update_req_meta(data: ReqMeta):
    if db is not None:
        try:
            await asyncio.sleep(1) # মেইন সিস্টেমের ডাটাবেসে সেভ হওয়ার জন্য অপেক্ষা
            latest_req = await db.requests.find_one({"movie": data.movie}, sort=[("_id", -1)])
            if latest_req:
                await db.requests.update_one(
                    {"_id": latest_req["_id"]},
                    {"$set": {"poster": data.poster, "message": data.message}}
                )
        except Exception as e:
            pass
    return {"ok": True}

# নতুন API: পাবলিক রিকোয়েস্ট ফেচ করার জন্য
@web_router.get("/api/plugin/public_requests")
async def public_requests():
    reqs = []
    if db is not None:
        try:
            # সর্বশেষ ১০ টি রিকোয়েস্ট দেখাবে
            async for r in db.requests.find().sort("_id", -1).limit(10):
                reqs.append({
                    "movie": r.get("movie", "Unknown"),
                    "poster": r.get("poster", ""),
                    "uname": r.get("uname", "User")
                })
        except Exception:
            pass
    return reqs

@web_router.get("/api/plugin/admin_get_requests")
async def admin_get_requests():
    reqs = []
    if db is not None:
        try:
            async for r in db.requests.find().sort("_id", -1):
                reqs.append({
                    "id": str(r["_id"]),
                    "movie": r.get("movie", "Unknown"),
                    "poster": r.get("poster", ""),
                    "message": r.get("message", ""),
                    "uname": r.get("uname", "User"),
                    "votes": r.get("votes", 1)
                })
        except Exception:
            pass
    return reqs

@web_router.delete("/api/plugin/admin_del_req/{req_id}")
async def admin_del_req(req_id: str):
    if db is not None:
        try:
            await db.requests.delete_one({"_id": ObjectId(req_id)})
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "Database error"}
