import os
import re
import uuid
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime
from backend.svk import SVKScraper
from backend.archive import InternetArchiveScraper
from backend.manasu import ManasuFoundationScraper
from backend.ttd import TTDScraper
from fastapi import FastAPI, Request, Depends
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware

# 2. అన్ని ఇంపోర్ట్స్ అయ్యాక వెంటనే దీన్ని కాల్ చేయాలి
load_dotenv()

# డేటాబేస్ కనెక్షన్ ఫంక్షన్
def get_db_connection():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Users Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            name TEXT,
            picture TEXT,
            last_login TEXT,
            is_active BOOLEAN DEFAULT FALSE
        )
    ''')
    
    # ఒకవేళ పాత టేబుల్ ఇప్పటికే ఉంటే, దానికి ఈ కొత్త కాలమ్ యాడ్ చేయడానికి
    cursor.execute('''
        ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT FALSE;
    ''')
    
    # Activities Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS activities (
            id SERIAL PRIMARY KEY,
            email TEXT,
            name TEXT,
            picture TEXT,
            action TEXT,
            details TEXT,
            time TEXT,
            status TEXT DEFAULT 'Success'
        )
    ''')
    
    # పాత డేటాబేస్ ఉన్నట్లయితే దానికి status కాలమ్ యాడ్ చేయడానికి
    cursor.execute('''
        ALTER TABLE activities ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'Success';
    ''')
    
    conn.commit()
    conn.close()

init_db()
# --------------------------------------------------

app = FastAPI()

# 1. FastAPI యాప్ క్రియేట్ చేసిన వెంటనే (app = FastAPI() కింద) ఈ సెషన్ మిడిల్‌వేర్ యాడ్ చేయాలి
# సెషన్ మిడిల్‌వేర్‌ని మరింత స్థిరంగా కాన్ఫిగర్ చేయడం
app.add_middleware(
    SessionMiddleware, 
    secret_key=os.getenv("SESSION_SECRET_KEY", "your-secret-key"),
    same_site="lax",
    https_only=False  # లోకల్ హోస్ట్ లో హెచ్‌టీటీపీ (HTTP) వాడటానికి ఇది చాలా ముఖ్యం
)

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

# యాక్టివిటీని డేటాబేస్‌లో పర్మినెంట్‌గా సేవ్ చేసే ఫంక్షన్
def log_user_activity(request: Request, action_type: str, details: str, status: str = "Success"):
    user = request.session.get('user')
    if user:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # 1. యాక్టివిటీని రికార్డ్ చేయడం (status ని కూడా ఇక్కడ యాడ్ చేశాం)
            cursor.execute('''
                INSERT INTO activities (email, name, picture, action, details, time, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (
                user.get("email"),
                user.get("name"),
                user.get("picture"),
                action_type,
                details,
                datetime.now().strftime("%d-%m-%Y %I:%M %p"),
                status
            ))
            
            # 2. యూజర్ యాక్టివ్‌గా ఉన్నాడు కాబట్టి స్టేటస్‌ను Online (TRUE) చేయడం
            cursor.execute('''
                UPDATE users SET is_active = TRUE WHERE email = %s
            ''', (user.get("email"),))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print("DB Error logging activity:", e)

# 3. గూగుల్ లాగిన్ రౌట్
@app.get('/login/google')
async def login(request: Request):
    redirect_uri = request.url_for('auth')  # ఆథరైజేషన్ తర్వాత రీడైరెక్ట్ అయ్యే లింక్
    return await oauth.google.authorize_redirect(request, redirect_uri)

# 4. గూగుల్ కాల్‌బ్యాక్ రౌట్ (లాగిన్ అయ్యాక ఇక్కడికి వస్తుంది)
# 4. గూగుల్ కాల్‌బ్యాక్ రౌట్ (లాగిన్ అయ్యాక ఇక్కడికి వస్తుంది)
@app.get('/auth/callback')
async def auth(request: Request):
    try:
        # గూగుల్ నుండి టోకెన్ పొందడం
        token = await oauth.google.authorize_access_token(request)
        
        # గూగుల్ వెర్షన్ బట్టి యూజర్ ఇన్ఫో రెండు విధాలుగా రావచ్చు, రెండింటినీ చెక్ చేద్దాం
        user_info = token.get('userinfo') or token.get('user_info')
        
        # ఒకవేళ టోకెన్‌లోనే యూజర్ ఇన్ఫో రాకపోతే, అఫిషియల్ API ద్వారా తెచ్చుకోవడం
        if not user_info:
            resp = await oauth.google.get('https://www.googleapis.com/oauth2/v3/userinfo', token=token)
            user_info = resp.json()
        
        if user_info and user_info.get('email'):
            user_dict = {
                "name": user_info.get("name"),
                "email": user_info.get("email"),
                "picture": user_info.get("picture")
            }
            # సెషన్‌లో యూజర్ డేటాను సేవ్ చేయడం
            request.session['user'] = user_dict
            
            # పర్మినెంట్ డేటాబేస్‌లో యూజర్‌ని సేవ్ చేయడం
            # (మీ /auth/callback రౌట్ లోపల యూజర్ డేటా సేవ్ చేసే చోట)
            conn = get_db_connection()
            cursor = conn.cursor()
            # Postgres లో డేటా ఉంటే అప్‌డేట్ చేయడానికి (ON CONFLICT) వాడతాం
            cursor.execute('''
                INSERT INTO users (email, name, picture, last_login)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (email) 
                DO UPDATE SET name = EXCLUDED.name, picture = EXCLUDED.picture, last_login = EXCLUDED.last_login
            ''', (
                user_dict.get('email'),
                user_dict.get('name'),
                user_dict.get('picture'),
                datetime.now().strftime("%d-%m-%Y %I:%M %p")
            ))
            conn.commit()
            conn.close()
                
            # 🌟 అడ్మిన్ లాగిన్ అయితే నేరుగా అడ్మిన్ ప్యానెల్‌కి పంపడం 🌟
            admin_email = "rpsarma9247@gmail.com"
            if user_dict.get('email') == admin_email:
                return RedirectResponse(url='/admin', status_code=303)
                
    except Exception as e:
        print(f"OAuth Error: {e}")
        
    # సాధారణ యూజర్ అయితే హోమ్‌పేజీకి వెళ్తారు
    return RedirectResponse(url='/', status_code=303)

# అడ్మిన్ డ్యాష్‌బోర్డ్ HTML పేజీని చూపించే ఎండ్‌పాయింట్
@app.get('/admin')
def admin_dashboard(request: Request):
    user = request.session.get('user')
    admin_email = "rpsarma9247@gmail.com"
    
    # యూజర్ లాగిన్ అవ్వకపోతే గూగుల్ లాగిన్‌కి పంపడం
    if not user:
        return RedirectResponse(url='/login/google')
        
    # ఒకవేళ లాగిన్ అయిన యూజర్ అడ్మిన్ కాకపోతే సాధారణ హోమ్‌పేజీకి పంపడం
    if user.get('email') != admin_email:
        return RedirectResponse(url='/')
        
    admin_page = os.path.join(FRONTEND_DIR, "admin.html")
    if os.path.exists(admin_page):
        return FileResponse(admin_page)
    return {"error": "admin.html not found"}



# అడ్మిన్ పేజీకి యూజర్ల డేటాను పంపించే API
@app.get('/admin/users-json')
def get_admin_users_json(request: Request):
    user = request.session.get('user')
    admin_email = os.getenv("ADMIN_EMAIL", "rpsarma9247@gmail.com")
    
    if not user or user.get('email') != admin_email:
        return {"error": "Unauthorized Access"}
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor) # డేటాను డిక్షనరీలా ఇస్తుంది
    
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    
    cursor.execute("SELECT * FROM activities ORDER BY id DESC")
    activities = cursor.fetchall()
    
    conn.close()
    
    total_searches = sum(1 for act in activities if act['action'] == 'Search')
    total_downloads = sum(1 for act in activities if act['action'] == 'Download')
    
    return {
        "total_users": len(users), 
        "users": users,
        "activities": activities,
        "stats": {
            "searches": total_searches,
            "downloads": total_downloads,
            "total_actions": len(activities)
        }
    }
    
# ==========================================
# యూజర్ ప్రొఫైల్ & హిస్టరీ రౌట్స్
# ==========================================

# 1. ప్రొఫైల్ పేజీని ఓపెన్ చేయడానికి
@app.get('/profile')
def user_profile(request: Request):
    user = request.session.get('user')
    if not user:
        # లాగిన్ లేకపోతే నేరుగా లాగిన్ పేజీకి పంపడం
        return RedirectResponse(url='/', status_code=303)
        
    profile_page = os.path.join(FRONTEND_DIR, "profile.html")
    if os.path.exists(profile_page):
        return FileResponse(profile_page)
    return {"error": "profile.html not found"}

# 2. యూజర్ యాక్టివిటీ డేటాను పంపడానికి
@app.get('/user/activity-json')
def get_user_activity(request: Request):
    user = request.session.get('user')
    if not user:
        return {"error": "Not logged in"}
    
    email = user.get('email')
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # యూజర్ యాక్టివిటీని లేటెస్ట్ నుండి పాతవాటికి తెచ్చుకోవడం
    cursor.execute("SELECT * FROM activities WHERE email = %s ORDER BY id DESC", (email,))
    activities = cursor.fetchall()
    
    conn.close()
    
    # సెర్చ్‌లు మరియు డౌన్‌లోడ్స్ కౌంట్
    total_searches = sum(1 for act in activities if act['action'] == 'Search')
    total_downloads = sum(1 for act in activities if act['action'] == 'Download' and act['status'] == 'Success')
    
    return {
        "user": user,
        "activities": activities,
        "stats": {
            "searches": total_searches,
            "downloads": total_downloads
        }
    }


@app.get('/api/current-user')
def get_current_user(request: Request):
    user = request.session.get('user')
    if not user:
        return {"logged_in": False}
    
    admin_email = "rpsarma9247@gmail.com"
    is_admin = (user.get("email") == admin_email)
    
    return {
        "logged_in": True,
        "name": user.get("name"),
        "email": user.get("email"),
        "picture": user.get("picture"),
        "is_admin": is_admin
    }

@app.get('/logout')
def logout(request: Request):
    user = request.session.get('user')
    if user:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET is_active = FALSE WHERE email = %s
            ''', (user.get('email'),))
            conn.commit()
            conn.close()
        except Exception as e:
            print("DB Error on logout:", e)
            
    request.session.clear()
    return RedirectResponse(url='/')

@app.post('/logout-beacon')
def logout_beacon(request: Request):
    user = request.session.get('user')
    if user:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET is_active = FALSE WHERE email = %s
            ''', (user.get('email'),))
            conn.commit()
            conn.close()
        except Exception as e:
            print("Beacon logout error:", e)
    request.session.clear()
    return {"status": "logged_out"}

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
    # యూజర్ సెర్చ్ చేసిన పదాన్ని రికార్డ్ చేయడం (ఇది సరైనది)
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
    # 🌟 పుస్తకం పేరు చాలా పెద్దదిగా ఉంటే కేవలం మొదటి 40 అక్షరాలకు ట్రిమ్ చేయడం (File name too long ఎర్రర్ రాకుండా)
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()[:40]
    if not safe_title:
        safe_title = "book"
        
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
            
        # 1. సక్సెస్ అయితే డేటాబేస్‌లో Success అని సేవ్ అవుతుంది
        log_user_activity(request, "Download", f"డౌన్‌లోడ్: '{title}' ({source})", "Success")
            
        return {
            "status": "success", 
            "message": f"Saved to {filepath}", 
            "file_url": f"/downloads/{filename}"
        }
    except Exception as e:
        error_str = str(e)
        if error_str.startswith("GDRIVE_RESTRICTED|"):
            drive_url = error_str.split("|")[1]
            # 2. గూగుల్ డ్రైవ్ లాగిన్ అడిగితే Failed అని సేవ్ అవుతుంది
            log_user_activity(request, "Download", f"డ్రైవ్ లాగిన్ అడిగింది: '{title}'", "Failed")
            return {
                "status": "gdrive_restricted", 
                "message": "ఈ ఫైల్‌‌ను డౌన్‌లోడ్ చేయడానికి Google లాగిన్ అవసరం.", 
                "url": drive_url
            }
            
        # 3. Timeout లాంటి ఇతర ఎర్రర్స్ వస్తే, ఆ ఎర్రర్ వివరాలతో Failed అని సేవ్ అవుతుంది
        log_user_activity(request, "Download", f"విఫలం: '{title}' | ఎర్రర్: {error_str}", "Failed")
        print(f"Download error: {error_str}")
        return {"status": "error", "message": error_str}    


@app.post("/reset-app")
def reset_application():
    try:
        return {"status": "success", "message": "App reset successfully, downloads preserved."}
    except Exception as e:
        return {"status": "error", "message": str(e)}