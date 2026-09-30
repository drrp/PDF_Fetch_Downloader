import os
import re
import uuid
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime

# --- స్క్రాపర్ క్లాస్‌ల ఇంపోర్ట్స్ (Shodhganga తొలగించబడింది) ---
from backend.svk import SVKScraper
from backend.archive import InternetArchiveScraper
from backend.manasu import ManasuFoundationScraper
from backend.ttd import TTDScraper
from fastapi import FastAPI, Request, Depends
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

app = FastAPI()

# 1. FastAPI యాప్ క్రియేట్ చేసిన వెంటనే (app = FastAPI() కింద) ఈ సెషన్ మిడిల్‌వేర్ యాడ్ చేయాలి
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET_KEY"))

# 2. Google OAuth సెటప్
oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# లాగిன் అయిన యూజర్లను తాత్కాలికంగా స్టోర్ చేయడానికి లిస్ట్
logged_in_users = []

app_activities = []

def log_user_activity(request: Request, action_type: str, details: str):
    user = request.session.get('user')
    if user:
        app_activities.append({
            "email": user.get("email"),
            "name": user.get("name"),
            "picture": user.get("picture"),
            "action": action_type,    
            "details": details,       
            "time": datetime.now().strftime("%d-%m-%Y %I:%M %p")
        })

# 3. గూగుల్ లాగిన్ రౌట్
@app.get('/login/google')
async def login(request: Request):
    redirect_uri = request.url_for('auth')  # ఆథరైజేషన్ తర్వాత రీడైరెక్ట్ అయ్యే లింక్
    return await oauth.google.authorize_redirect(request, redirect_uri)

# 4. గూగుల్ కాల్‌బ్యాక్ రౌట్ (లాగిన్ అయ్యాక ఇక్కడికి వస్తుంది)
@app.get('/auth/callback')
async def auth(request: Request):
    try:
        # గూగుల్ నుండి టోకెన్ మరియు యూజర్ ఇన్ఫో పొందడం
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get('userinfo')
        
        if user_info:
            # సెషన్‌లో యూజర్ డేటాను సేవ్ చేయడం
            request.session['user'] = {
                "name": user_info.get("name"),
                "email": user_info.get("email"),
                "picture": user_info.get("picture")
            }
            
            # అడ్మిన్ ట్రాకింగ్ కోసం లిస్ట్‌లో యాడ్ చేయడం
            user_data = request.session['user']
            if user_data not in logged_in_users:
                logged_in_users.append(user_data)
                
    except Exception as e:
        print(f"OAuth Error: {e}")
        
    # లాగిన్ విజయవంతంగా ముగిశాక నేరుగా హోమ్‌పేజీకి పంపడం
    return RedirectResponse(url='/', status_code=303)

# అడ్మిన్ డ్యాష్‌బోర్డ్ HTML పేజీని చూపించే ఎండ్‌పాయింట్
@app.get('/admin')
def admin_dashboard(request: Request):
    user = request.session.get('user')
    admin_email = "rpsarma9247@gmail.com" # మీ అడ్మిన్ ఈమెయిల్
    
    # అడ్మిన్ కాకపోతే హోమ్‌పేజీకి పంపించేయాలి
    if not user or user.get('email') != admin_email:
        return RedirectResponse(url='/')
        
    admin_page = os.path.join(FRONTEND_DIR, "admin.html")
    if os.path.exists(admin_page):
        return FileResponse(admin_page)
    return {"error": "admin.html not found"}



# అడ్మిన్ పేజీకి యూజర్ల డేటాను పంపించే API
@app.get('/admin/users-json')
def get_admin_users_json(request: Request):
    user = request.session.get('user')
    admin_email = "rpsarma9247@gmail.com"
    
    if not user or user.get('email') != admin_email:
        return {"error": "Unauthorized Access"}
    
    # స్టాటిస్టిక్స్ లెక్కించడం
    total_searches = sum(1 for act in app_activities if act.get('action') == 'Search')
    total_downloads = sum(1 for act in app_activities if act.get('action') == 'Download')
    
    return {
        "total_users": len(logged_in_users), 
        "users": logged_in_users,
        "activities": app_activities[::-1],
        "stats": {
            "searches": total_searches,
            "downloads": total_downloads,
            "total_actions": len(app_activities)
        }
    }


@app.get('/api/current-user')
def get_current_user(request: Request):
    user = request.session.get('user')
    if not user:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "name": user.get("name"),
        "email": user.get("email"),
        "picture": user.get("picture")
    }

@app.get('/logout')
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url='/')

# పాత్ కాన్ఫిగరేషన్
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
FRONTEND_INDEX = os.path.join(FRONTEND_DIR, "index.html")
ROOT_INDEX = os.path.join(BASE_DIR, "index.html")

DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

app.mount("/downloads", StaticFiles(directory=DOWNLOAD_DIR), name="downloads")


@app.get("/")
def read_root(request: Request):
    # సెషన్ లో యూజర్ ఉన్నారో లేదో చెక్ చేయడం
    user = request.session.get('user')
    
    if not user:
        # యూజర్ లాగిన్ అవ్వకపోతే login.html కి పంపడం
        login_page = os.path.join(FRONTEND_DIR, "login.html")
        if os.path.exists(login_page):
            return FileResponse(login_page)
        # ఒకవేళ login.html ఫైల్ లేకపోతే నేరుగా గూగుల్ లాగిన్ కి పంపడం
        return RedirectResponse(url='/login/google')
        
    # యూజర్ లాగిన్ అయితే డ్యాష్‌బోర్డ్ (index.html) చూపించడం
    if os.path.exists(FRONTEND_INDEX):
        return FileResponse(FRONTEND_INDEX)
    if os.path.exists(ROOT_INDEX):
        return FileResponse(ROOT_INDEX)
    
    return {"error": "index.html not found"}


# --- స్క్రాపర్ ఆబ్జెక్ట్స్ ఇనిషియలైజేషన్ ---
svk_scraper = SVKScraper()
ia_scraper = InternetArchiveScraper()
manasu_scraper = ManasuFoundationScraper()
ttd_scraper = TTDScraper()


@app.get("/search")
def search(
    request: Request,
    query: str = Query(..., description="Search term"),
    use_svk: bool = Query(True, description="Fetch from SVK"),
    use_ia: bool = Query(True, description="Fetch from Internet Archive"),
    use_manasu: bool = Query(True, description="Fetch from Manasu Foundation"),
    use_ttd: bool = Query(True, description="Fetch from TTD Ebooks"),
    page: int = Query(1, description="Page number"),
    limit: int = Query(10, description="Items per page")
):
    # యూజర్ సెర్చ్ చేసిన పదాన్ని రికార్డ్ చేయడం
    log_user_activity(request, "Search", f"వెతికిన పదం: '{query}'")
    results = []
    
    if use_svk:
        results.extend(svk_scraper.search(query))
    if use_ia:
        results.extend(ia_scraper.search(query))
    if use_manasu:
        results.extend(manasu_scraper.search(query))
    if use_ttd:
        results.extend(ttd_scraper.search(query))
        
    total_items = len(results)
    
    # పెర్ పేజ్ 10 ఐటమ్స్ ప్రకారం స్లైస్ చేయడం
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    paginated_results = results[start_idx:end_idx]
    
    total_pages = (total_items + limit - 1) // limit if total_items > 0 else 1
    
    return {
        "results": paginated_results, 
        "total": total_items,
        "page": page,
        "total_pages": total_pages
    }


@app.get("/download-book")
def download_book(request: Request, book_page_url: str = Query(...), title: str = Query(...), source: str = Query("SVK")):
    # యూజర్ డౌన్‌లోడ్ చేసిన పుస్తకాన్ని రికార్డ్ చేయడం
    log_user_activity(request, "Download", f"డౌన్‌లోడ్: '{title}' ({source})")
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
    
    unique_id = uuid.uuid4().hex[:6]
    filename = f"{safe_title}_{unique_id}.pdf"
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    
    try:
        if source == "Internet Archive":
            ia_scraper.download(book_page_url, title, filepath)
        elif source == "Manasu Foundation":
            manasu_scraper.download(book_page_url, title, filepath)
        elif source == "TTD Ebooks":
            ttd_scraper.download(book_page_url, title, filepath)
        else:
            svk_scraper.download(book_page_url, title, filepath)
            
        return {
            "status": "success", 
            "message": f"Saved to {filepath}", 
            "file_url": f"/downloads/{filename}"
        }
    except Exception as e:
        error_str = str(e)
        if error_str.startswith("GDRIVE_RESTRICTED|"):
            drive_url = error_str.split("|")[1]
            return {
                "status": "gdrive_restricted", 
                "message": "ఈ ఫైల్‌‌ను డౌన్‌లోడ్ చేయడానికి Google లాగిన్ అవసరం.", 
                "url": drive_url
            }
            
        print(f"Download error: {error_str}")
        return {"status": "error", "message": error_str}    


@app.post("/reset-app")
def reset_application():
    try:
        return {"status": "success", "message": "App reset successfully, downloads preserved."}
    except Exception as e:
        return {"status": "error", "message": str(e)}