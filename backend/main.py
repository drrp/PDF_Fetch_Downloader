import os
import requests
import re
import uuid
import sqlite3
import psycopg2
import json
import base64
import fitz  # PyMuPDF (PDF ని పేజీలుగా మార్చడానికి)
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google import genai
from authlib.integrations.starlette_client import OAuth
from google.cloud import vision  # Google Cloud Vision API కోసం
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from fastapi import Form
from fastapi import FastAPI, Query, Request, Depends
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime
from backend.svk import SVKScraper
from backend.archive import InternetArchiveScraper
from backend.manasu import ManasuFoundationScraper
from backend.ttd import TTDScraper
from starlette.middleware.sessions import SessionMiddleware

# 1. Environment Variables లోడ్ చేయడం
load_dotenv(override=True) # 🌟 పాత కీని క్లియర్ చేసి కొత్త కీని ఫోర్స్ చేస్తుంది

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
    
    cursor.execute('''
        ALTER TABLE activities ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'Success';
    ''')
    
    # PDF Full-Text Search Index Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pdf_text_index (
            id SERIAL PRIMARY KEY,
            book_title TEXT,
            filename TEXT,
            page_number INT,
            extracted_text TEXT,
            word_boxes JSONB
        )
    ''');
    
    # ఒకవేళ పాత టేబుల్ ఉండి ఈ కాలమ్స్ లేకపోతే ఆటోమేటిక్‌గా యాడ్ చేయడానికి:
    cursor.execute('ALTER TABLE pdf_text_index ADD COLUMN IF NOT EXISTS filename TEXT;')
    cursor.execute('ALTER TABLE pdf_text_index ADD COLUMN IF NOT EXISTS word_boxes JSONB;')
    
    conn.commit()
    conn.close()

init_db()
# --------------------------------------------------

app = FastAPI()

app.add_middleware(
    SessionMiddleware, 
    secret_key=os.getenv("SESSION_SECRET_KEY", "your-secret-key"),
    same_site="lax",
    https_only=False
)

# Google OAuth సెటప్
oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# యాక్టివిటీని డేటాబేస్‌లో సేవ్ చేసే ఫంక్షన్
def log_user_activity(request: Request, action_type: str, details: str, status: str = "Success"):
    user = request.session.get('user')
    if user:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO activities (email, name, picture, action, details, time, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (
                user.get("email"), user.get("name"), user.get("picture"),
                action_type, details, datetime.now().strftime("%d-%m-%Y %I:%M %p"), status
            ))
            cursor.execute('''
                UPDATE users SET is_active = TRUE WHERE email = %s
            ''', (user.get("email"),))
            conn.commit()
            conn.close()
        except Exception as e:
            print("DB Error logging activity:", e)

@app.get('/login/google')
async def login(request: Request):
    redirect_uri = request.url_for('auth')
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get('/auth/callback')
async def auth(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get('userinfo') or token.get('user_info')
        if not user_info:
            resp = await oauth.google.get('https://www.googleapis.com/oauth2/v3/userinfo', token=token)
            user_info = resp.json()
        
        if user_info and user_info.get('email'):
            user_dict = {
                "name": user_info.get("name"),
                "email": user_info.get("email"),
                "picture": user_info.get("picture")
            }
            request.session['user'] = user_dict
            
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (email, name, picture, last_login)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (email) 
                DO UPDATE SET name = EXCLUDED.name, picture = EXCLUDED.picture, last_login = EXCLUDED.last_login
            ''', (
                user_dict.get('email'), user_dict.get('name'),
                user_dict.get('picture'), datetime.now().strftime("%d-%m-%Y %I:%M %p")
            ))
            conn.commit()
            conn.close()
                
            admin_email = "rpsarma9247@gmail.com"
            if user_dict.get('email') == admin_email:
                return RedirectResponse(url='/admin', status_code=303)
                
    except Exception as e:
        print(f"OAuth Error: {e}")
        
    return RedirectResponse(url='/', status_code=303)

@app.get('/admin')
def admin_dashboard(request: Request):
    user = request.session.get('user')
    admin_email = "rpsarma9247@gmail.com"
    if not user or user.get('email') != admin_email:
        return RedirectResponse(url='/')
    admin_page = os.path.join(FRONTEND_DIR, "admin.html")
    return FileResponse(admin_page) if os.path.exists(admin_page) else {"error": "admin.html not found"}

@app.get('/admin/users-json')
def get_admin_users_json(request: Request):
    user = request.session.get('user')
    if not user or user.get('email') != "rpsarma9247@gmail.com":
        return {"error": "Unauthorized Access"}
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    cursor.execute("SELECT * FROM activities ORDER BY id DESC")
    activities = cursor.fetchall()
    conn.close()
    
    return {
        "total_users": len(users), 
        "users": users,
        "activities": activities,
        "stats": {
            "searches": sum(1 for a in activities if a['action'] == 'Search'),
            "downloads": sum(1 for a in activities if a['action'] == 'Download'),
            "total_actions": len(activities)
        }
    }

@app.get('/profile')
def user_profile(request: Request):
    user = request.session.get('user')
    if not user:
        return RedirectResponse(url='/', status_code=303)
    profile_page = os.path.join(FRONTEND_DIR, "profile.html")
    return FileResponse(profile_page) if os.path.exists(profile_page) else {"error": "profile.html not found"}


# గూగుల్ డ్రైవ్ ఆథెంటికేషన్ & అప్‌లోడ్ ఫంక్షన్
SCOPES = ['https://www.googleapis.com/auth/drive.file']
SERVICE_ACCOUNT_FILE = 'credentials.json'

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)

def upload_pdf_to_drive(file_path, filename):
    try:
        service = get_drive_service()
        
        # మీ షేర్డ్ డ్రైవ్ ఐడీని ఇక్కడ ఇవ్వండి
        shared_drive_id = "0APbexQ8RCU0WUk9PVA" 
        
        file_metadata = {
            'name': filename,
            'parents': [shared_drive_id] # షేర్డ్ డ్రైవ్ ఐడీ పేరెంట్ అవుతుంది
        }
        
        media = MediaFileUpload(file_path, mimetype='application/pdf')
        
        # supportsAllDrives=True అనేది షేర్డ్ డ్రైవ్‌కి అప్‌లోడ్ చేయడానికి అత్యంత ముఖ్యం
        file = service.files().create(
            body=file_metadata, 
            media_body=media, 
            supportsAllDrives=True,
            fields='id, webContentLink, webViewLink'
        ).execute()
        
        return file.get('webViewLink')
    except Exception as e:
        print(f"Drive Upload Error: {e}")
        return None


# ప్రత్యేక OCR పేజీని ఓపెన్ చేయడానికి రౌట్
@app.get('/ocr-hub')
def ocr_hub_page(request: Request):
    user = request.session.get('user')
    if not user:
        return RedirectResponse(url='/login/google', status_code=303)
        
    ocr_page = os.path.join(FRONTEND_DIR, "ocr.html")
    if os.path.exists(ocr_page):
        return FileResponse(ocr_page)
    return {"error": "ocr.html not found"}

@app.get('/user/activity-json')
def get_user_activity(request: Request):
    user = request.session.get('user')
    if not user:
        return {"error": "Not logged in"}
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM activities WHERE email = %s ORDER BY id DESC", (user.get('email'),))
    activities = cursor.fetchall()
    conn.close()
    
    return {
        "user": user,
        "activities": activities,
        "stats": {
            "searches": sum(1 for a in activities if a['action'] == 'Search'),
            "downloads": sum(1 for a in activities if a['action'] == 'Download' and a['status'] == 'Success')
        }
    }
    
import os
from fastapi import APIRouter, Request

@app.get("/api/downloaded-files")
def get_downloaded_files():
    """డౌన్‌లోడ్ అయిన పుస్తకాలను మరియు వాటి OCR స్టేటస్‌ను పంపే రౌట్"""
    files = []
    if os.path.exists(DOWNLOAD_DIR):
        file_names = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.pdf')]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        for filename in file_names:
            # 🌟 డేటాబేస్‌లో ఈ ఫైల్‌‌కు OCR పూర్తయిందా లేదా చెక్ చేయడం
            cursor.execute("SELECT 1 FROM pdf_text_index WHERE filename = %s LIMIT 1", (filename,))
            is_done = cursor.fetchone() is not None
            
            files.append({
                "filename": filename,
                "ocr_done": is_done
            })
        conn.close()
        
    return {"files": files}
    
@app.post("/update-ocr-text")
def update_ocr_text(
    filename: str = Form(...), 
    page_number: int = Form(...), 
    new_text: str = Form(...)
):
    """డేటాబేస్‌లోని OCR టెక్స్ట్‌ను మాన్యువల్‌గా సరిదిద్దడానికి"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE pdf_text_index 
            SET extracted_text = %s 
            WHERE filename = %s AND page_number = %s
        ''', (new_text, filename, page_number))
        
        conn.commit()
        conn.close()
        
        return {"status": "success", "message": f"పేజీ {page_number} టెక్స్ట్ విజయవంతంగా అప్‌డేట్ చేయబడింది!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get('/api/current-user')
def get_current_user(request: Request):
    user = request.session.get('user')
    if not user:
        return {"logged_in": False}
    return {
        "logged_in": True, "name": user.get("name"), "email": user.get("email"),
        "picture": user.get("picture"), "is_admin": (user.get("email") == "rpsarma9247@gmail.com")
    }

@app.get('/logout')
def logout(request: Request):
    user = request.session.get('user')
    if user:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET is_active = FALSE WHERE email = %s', (user.get('email'),))
            conn.commit()
            conn.close()
        except: pass
    request.session.clear()
    return RedirectResponse(url='/')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
# 🌟 ఫ్రంట్-ఎండ్ ఫోల్డర్‌లోనే ఇండెక్స్ ఫైల్ ఉండేలా సెట్ చేయడం
FRONTEND_INDEX = os.path.join(FRONTEND_DIR, "index.html")
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.mount("/downloads", StaticFiles(directory=DOWNLOAD_DIR), name="downloads")

@app.get("/")
def read_root(request: Request):
    user = request.session.get('user')
    if not user:
        login_page = os.path.join(FRONTEND_DIR, "login.html")
        return FileResponse(login_page) if os.path.exists(login_page) else RedirectResponse(url='/login/google')
    
    # 🌟 రూట్ లో కాకుండా 'frontend' ఫోల్డర్ లోని index.html ని రిటర్న్ చేయడం
    if os.path.exists(FRONTEND_INDEX):
        return FileResponse(FRONTEND_INDEX)
    
    return {"error": "index.html not found in frontend folder"}

# స్క్రాపర్స్
svk_scraper = SVKScraper()
ia_scraper = InternetArchiveScraper()
manasu_scraper = ManasuFoundationScraper()
ttd_scraper = TTDScraper()

@app.get("/search")
def search(
    request: Request, query: str = Query(...),
    use_svk: bool = Query(True), use_ia: bool = Query(True),
    use_manasu: bool = Query(True), use_ttd: bool = Query(True),
    page: int = Query(1), limit: int = Query(10)
):
    log_user_activity(request, "Search", f"వెతికిన పదం: '{query}'")
    results = []
    if use_svk: results.extend(svk_scraper.search(query))
    if use_ia: results.extend(ia_scraper.search(query))
    if use_manasu: results.extend(manasu_scraper.search(query))
    if use_ttd: results.extend(ttd_scraper.search(query))
        
    total_items = len(results)
    start_idx = (page - 1) * limit
    paginated_results = results[start_idx:start_idx + limit]
    
    return {
        "results": paginated_results, "total": total_items,
        "page": page, "total_pages": (total_items + limit - 1) // limit if total_items > 0 else 1
    }

@app.get("/download-book")
def download_book(request: Request, book_page_url: str = Query(...), title: str = Query(...), source: str = Query("SVK")):
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()[:40] or "book"
    filename = f"{safe_title}_{uuid.uuid4().hex[:6]}.pdf"
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    
    try:
        if source == "Internet Archive": ia_scraper.download(book_page_url, title, filepath)
        elif source == "Manasu Foundation": 
            manasu_scraper.consult_download(book_page_url, title, filepath) if hasattr(manasu_scraper, 'consult_download') else manasu_scraper.download(book_page_url, title, filepath)
        elif source == "TTD Ebooks": ttd_scraper.download(book_page_url, title, filepath)
        else: svk_scraper.download(book_page_url, title, filepath)
            
        # 🌟 ఇక్కడ డౌన్‌లోడ్ అయిన వెంటనే గూగుల్ డ్రైవ్‌కి పంపడం
        drive_link = None
        if os.path.exists(filepath):
            drive_link = upload_pdf_to_drive(filepath, filename)
            print(f"Google Drive Permanent Link: {drive_link}")

        log_user_activity(request, "Download", f"డౌన్‌లోడ్ & డ్రైవ్ సేవ్: '{title}' ({source})", "Success")
        
        return {
            "status": "success", 
            "message": f"Saved and uploaded to Drive!", 
            "file_url": f"/downloads/{filename}",
            "drive_link": drive_link
        }
    except Exception as e:
        log_user_activity(request, "Download", f"విఫలం: '{title}' | ఎర్రర్: {str(e)}", "Failed")
        return {"status": "error", "message": str(e)}    

@app.post("/reset-app")
def reset_application():
    return {"status": "success", "message": "App reset successfully, downloads preserved."}


# ==========================================
# 🌟 ప్రత్యేక OCR & హైలైటింగ్ ప్యానెల్ API లు
# ==========================================

@app.get("/list-downloaded-books")
def list_downloaded_books(request: Request):
    """డౌన్‌లోడ్ చేసిన PDFల జాబితాను ప్యానెల్ కోసం చూపించడం"""
    user = request.session.get('user')
    if not user: return {"error": "Unauthorized"}
    
    files = []
    print(f"Checking download directory: {DOWNLOAD_DIR}") # టెర్మినల్‌లో ఫోల్డర్ పాత్ ప్రింట్ అవుతుంది
    
    if os.path.exists(DOWNLOAD_DIR):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            for f in os.listdir(DOWNLOAD_DIR):
                if f.endswith('.pdf'):
                    # డేటాబేస్‌లో ఈ ఫైల్‌కి OCR పూర్తయిందో లేదో చెక్ చేయడం
                    cursor.execute("SELECT 1 FROM pdf_text_index WHERE filename = %s LIMIT 1", (f,))
                    is_done = cursor.fetchone() is not None
                    
                    files.append({
                        "filename": f,
                        "ocr_done": is_done
                    })
            conn.close()
        except Exception as e:
            print(f"Database error in list-downloaded-books: {e}")
            for f in os.listdir(DOWNLOAD_DIR):
                if f.endswith('.pdf'):
                    files.append({"filename": f, "ocr_done": False})
    else:
        print(f"Warning: Download directory does not exist at {DOWNLOAD_DIR}")

    return {"files": files}

@app.post("/run-special-ocr")
def run_special_ocr(request: Request, filename: str = Query(...)):
    """యూజర్ కోరిన నిర్దిష్ట పుస్తకానికి మాత్రమే మాన్యువల్‌గా OCR మరియు Bounding Boxes ఇండెక్స్ చేయడం"""
    user = request.session.get('user')
    if not user: return {"error": "Unauthorized"}
    
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(filepath):
        return {"status": "error", "message": "ఫైల్ కనుగొనబడలేదు."}
        
    try:
        client = vision.ImageAnnotatorClient()
        doc = fitz.open(filepath)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        book_title = filename.rsplit('_', 1)[0]
        
        # పాత రికార్డ్స్ ఉంటే క్లియర్ చేయడం
        cursor.execute("DELETE FROM pdf_text_index WHERE filename = %s", (filename,))
        
        for page_num in range(len(doc)):
            print(f"Processing page {page_num + 1} of {len(doc)}...") # 🌟 ప్రోగ్రెస్ చూడటానికి
            page = doc[page_num]
            pix = page.get_pixmap(dpi=150)
            image_bytes = pix.tobytes("png")            
            image = vision.Image(content=image_bytes)
            response = client.document_text_detection(image=image)
            
            page_text = response.full_text_annotation.text if response.full_text_annotation else ""
            
            # పదాల గుర్తింపు మరియు వాటి కోఆర్డినేట్స్ (Bounding Boxes) కలెక్ట్ చేయడం
            word_boxes = []
            if response.full_text_annotation:
                for page_obj in response.full_text_annotation.pages:
                    for block in page_obj.blocks:
                        for paragraph in block.paragraphs:
                            for word in paragraph.words:
                                word_str = "".join([symbol.text for symbol in word.symbols])
                                vertices = word.bounding_box.vertices
                                box = {
                                    "word": word_str,
                                    "x0": vertices[0].x, "y0": vertices[0].y,
                                    "x2": vertices[2].x, "y2": vertices[2].y
                                }
                                word_boxes.append(box)
            
            if page_text:
                cursor.execute('''
                    INSERT INTO pdf_text_index (book_title, filename, page_number, extracted_text, word_boxes)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (book_title, filename, page_num + 1, page_text, json.dumps(word_boxes)))
                
        conn.commit()
        conn.close()

        # 🌟 ఇక్కడ గూగుల్ డ్రైవ్‌కి అప్‌లోడ్ చేసి పర్మినెంట్ లింక్ పొందవచ్చు
        drive_link = upload_pdf_to_drive(filepath, filename)
        print(f"Google Drive Permanent Link: {drive_link}")

        return {
            "status": "success", 
            "message": f"'{book_title}' పుస్తకానికి OCR పూర్తి అయింది మరియు డ్రైవ్‌కి భద్రపరచబడింది!",
            "drive_link": drive_link
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
from fastapi import Form

@app.get("/get-ocr-text")
def get_ocr_text(filename: str, page_number: int):
    """డేటాబేస్ నుండి నిర్దిష్ట పేజీ యొక్క OCR టెక్స్ట్ తీసుకురావడానికి"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT extracted_text FROM pdf_text_index WHERE filename = %s AND page_number = %s", (filename, page_number))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {"status": "success", "text": result[0]}
        else:
            return {"status": "error", "message": "ఈ పేజీకి సంబంధించిన OCR డేటా కనుగొనబడలేదు. ముందుగా OCR రన్ చేశారో లేదో చెక్ చేయండి."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/update-ocr-text")
def update_ocr_text(filename: str = Form(...), page_number: int = Form(...), new_text: str = Form(...)):
    """ఎడిట్ చేసిన కొత్త టెక్స్ట్‌ను డేటాబేస్‌లో అప్‌డేట్ చేయడానికి"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE pdf_text_index 
            SET extracted_text = %s 
            WHERE filename = %s AND page_number = %s
        ''', (new_text, filename, page_number))
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"పేజీ {page_number} లోని టెక్స్ట్ విజయవంతంగా సేవ్ చేయబడింది!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def generate_ai_summary(query, snippets_list):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "⚠️ హెచ్చరిక: `.env` ఫైల్‌లో 'GEMINI_API_KEY' లభించలేదు. API కీ ని అమర్చండి."

    if not snippets_list:
        return "మీరు అడిగిన ప్రశ్నకు సంబంధించిన సమాచారం ప్రస్తుత గ్రంథాలలో లభించలేదు."

    try:
        client = genai.Client(api_key=api_key)
        
        combined_text = "\n".join([f"- గ్రంథం: {s['book_title']} (పేజీ {s['page_number']}): {s['snippet']}" for s in snippets_list])
        
        # 🌟 ప్రాంప్ట్: కేవలం పదం గురించి కాకుండా, అడిగిన ప్రశ్నకు సమాధానం ఇచ్చేలా మార్పు
        prompt = f"""కింద ఇవ్వబడిన గ్రంథాలలోని సమాచారం (Snippets) ఆధారంగా, యూజర్ అడిగిన ఈ ప్రశ్నకు లేదా అంశానికి ('{query}') స్పష్టమైన, విశ్లేషణాత్మకమైన సమాధానాన్ని అచ్చతెలుగులో ఇవ్వండి. 
        
        యూజర్ ప్రశ్న/అంశం: {query}
        
        గమనిక: కేవలం లభ్యమైన గ్రంథాల సమాచారం ఆధారంగా మాత్రమే సమాధానం రాయండి. బయటి అంశాలను చేర్చవద్దు.

        ఈ క్రింది పద్ధతిలో విశ్లేషణ ఇవ్వండి:
        1. **సమాధానం/సారాంశం:** (ప్రశ్నకు నేరుగా సమాధానం)
        2. **గ్రంథాల ఆధారాలు:** (ఏ గ్రంథంలో, ఏ పేజీలో ఈ వివరాలు దొరికాయి మరియు అందులోని ప్రయోగాలు)
        3. **వివరణ:** (మరింత లోతైన విశ్లేషణ లేదా విశేషాలు)

        గ్రంథాల సమాచారం (Snippets):
        {combined_text}"""

        # గూగుల్ సూచించిన వేగవంతమైన మోడల్
        models_to_try = ['gemini-3.8-flash']
        
        for model_name in models_to_try:
            for attempt in range(2):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                    )
                    if response and response.text:
                        return response.text
                except Exception as sub_e:
                    print(f"Gemini failed: model={model_name}, attempt={attempt+1}, error={str(sub_e)}")
                    import time
                    time.sleep(1.5)
                
        return "ప్రస్తుతం గూగుల్ AI సేవలు అందుబాటులో లేవు. దయచేసి నెట్‌వర్క్ లేదా API కీ ని సరిచూసుకోండి."

    except Exception as e:
        print("Gemini AI Error Exception:", str(e))
        return "సారాంశం రూపొందించడంలో సాంకేతిక లోపం ఏర్పడింది."

@app.get("/search-pdf-highlight")
def search_pdf_highlight(request: Request, query: str = Query(...)):
    """ఏకైక పరిమితులు (Limits) లేకుండా అన్ని OCR పుస్తకాల నుండి సమగ్ర శోధన చేయడం"""
    user = request.session.get('user')
    if not user: return {"error": "Unauthorized"}
    
    cleaned_query = query.strip()
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    matches = []
    
    # ప్రశ్నలోని ముఖ్యమైన పదాలను వేరు చేయడం
    stop_words = ["గ్రంథంలో", "గ్రంథాలలో", "మాత్రమే", "ఉన్న", "ఇవ్వు", "అంటే", "ఏమిటి", "గురించి", "రాయండి", "తెలుపుము", "రూపాలను", "రూపాలు", "ఒకసారి", "యొక్క", "లో", "కి", "కు", "ను", "ని"]
    words = [w for w in cleaned_query.split() if len(w) > 2 and w not in stop_words]
    
    if not words:
        words = [w for w in cleaned_query.split() if len(w) > 3]

    # అన్ని పుస్తకాల నుండి ఏ విధమైన లగ్జరీ లిమిట్స్ లేకుండా డేటాను సేకరించడం
    if words:
        conditions = " OR ".join(["extracted_text ILIKE %s" for _ in words])
        params = [f"%{w}%" for w in words]
        
        # 🌟 ఇక్కడ ఎలాంటి LIMIT లేదు - అందిన అన్ని పుస్తకాల పేజీలు వస్తాయి
        cursor.execute(f'''
            SELECT book_title, filename, page_number, extracted_text, word_boxes 
            FROM pdf_text_index 
            WHERE {conditions}
            ORDER BY id DESC
        ''', tuple(params))
        matches = cursor.fetchall()
        
    # ఒకవేళ దొరకకపోతే జనరల్ వ్యాకరణం/సంధి పదాలతో వెతకడం (ఇక్కడ కూడా లిమిట్ లేదు)
    if not matches:
        cursor.execute('''
            SELECT book_title, filename, page_number, extracted_text, word_boxes 
            FROM pdf_text_index 
            WHERE extracted_text ILIKE %s OR extracted_text ILIKE %s
            ORDER BY id DESC
        ''', ('%సంధి%', '%వ్యాకరణం%'))
        matches = cursor.fetchall()
        
    conn.close()
    
    results = []
    for m in matches:
        text = m['extracted_text'] or ""
        
        pos = -1
        matched_w = cleaned_query
        for w in words:
            pos = text.find(w)
            if pos != -1:
                matched_w = w
                break
        if pos == -1: pos = 0
            
        snippet = text[max(0, pos - 80):min(len(text), pos + 250)].replace('\n', ' ')
        
        filename = m.get('filename')
        highlighted_img_b64 = ""
        
        if not filename and m.get('book_title'):
            for f in os.listdir(DOWNLOAD_DIR):
                if m['book_title'][:10] in f and f.endswith('.pdf'):
                    filename = f
                    break
                    
        if filename:
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.exists(filepath):
                try:
                    doc = fitz.open(filepath)
                    page = doc[m['page_number'] - 1]
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    highlighted_img_b64 = base64.b64encode(img_bytes).decode('utf-8')
                except Exception as ex:
                    print("PDF Render Error:", ex)

        results.append({
            "book_title": m['book_title'],
            "filename": filename or "",
            "page_number": m['page_number'],
            "snippet": f"...{snippet}...",
            "word_boxes": m.get('word_boxes'),
            "query": matched_w,
            "page_image": highlighted_img_b64
        })
        
    ai_summary = generate_ai_summary(cleaned_query, results) if results else "మీ ప్రశ్నకు తగిన సమాచారం లభించలేదు."
        
    return {
        "query": cleaned_query,
        "ai_summary": ai_summary,
        "results": results
    }


@app.get("/view-pdf")
def view_pdf(filename: str, page: int = 1):
    """డౌన్‌లోడ్స్ నుండి PDF ని నిర్దిష్ట పేజీతో ఓపెన్ చేయడానికి"""
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(file_path):
        # బ్రౌజర్ నేరుగా పేజీకి వెళ్లేలా ఫైల్ రెస్పాన్స్ పంపడం
        return FileResponse(file_path, media_type='application/pdf')
    return {"error": "File not found"}