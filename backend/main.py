import warnings
warnings.filterwarnings("ignore", message=".*httpx module is deprecated.*")
warnings.filterwarnings("ignore", module="authlib")

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
import pytz
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
load_dotenv(override=True)

# డేటాబేస్ కనెక్షన్ ఫంక్షన్
def get_db_connection():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # AI Research History Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_research_history (
            id SERIAL PRIMARY KEY,
            email TEXT,
            query TEXT,
            ai_summary TEXT,
            sources_json TEXT,
            created_at TEXT
        )
    ''')
    
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
            IST = pytz.timezone('Asia/Kolkata')
            current_time = datetime.now(IST).strftime("%d-%m-%Y %I:%M %p")
            
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO activities (email, name, picture, action, details, time, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (
                user.get("email"), user.get("name"), user.get("picture"),
                action_type, details, current_time, status
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
            IST = pytz.timezone('Asia/Kolkata')
            login_time = datetime.now(IST).strftime("%d-%m-%Y %I:%M %p")
            cursor.execute('''
                INSERT INTO users (email, name, picture, last_login)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (email) 
                DO UPDATE SET name = EXCLUDED.name, picture = EXCLUDED.picture, last_login = EXCLUDED.last_login
            ''', (
                user_dict.get('email'), user_dict.get('name'),
                user_dict.get('picture'), login_time
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

@app.get("/api/downloaded-files")
def get_downloaded_files():
    files = []
    if os.path.exists(DOWNLOAD_DIR):
        file_names = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.pdf')]
        conn = get_db_connection()
        cursor = conn.cursor()
        for filename in file_names:
            cursor.execute("SELECT 1 FROM pdf_text_index WHERE filename = %s LIMIT 1", (filename,))
            is_done = cursor.fetchone() is not None
            files.append({"filename": filename, "ocr_done": is_done})
        conn.close()
    return {"files": files}

@app.post("/update-ocr-text")
def update_ocr_text(filename: str = Form(...), page_number: int = Form(...), new_text: str = Form(...)):
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
        if source == "Internet Archive": 
            ia_scraper.download(book_page_url, title, filepath)
        elif source == "Manasu Foundation": 
            manasu_scraper.consult_download(book_page_url, title, filepath) if hasattr(manasu_scraper, 'consult_download') else manasu_scraper.download(book_page_url, title, filepath)
        elif source == "TTD Ebooks": 
            ttd_scraper.download(book_page_url, title, filepath)
        else: 
            svk_scraper.download(book_page_url, title, filepath)
            
        log_user_activity(request, "Download", f"డౌన్‌లోడ్: '{title}' ({source})", "Success")
        
        return {
            "status": "success", 
            "message": "పుస్తకం విజయవంతంగా డౌన్‌లోడ్ చేయబడింది!", 
            "file_url": f"/downloads/{filename}"
        }
    except Exception as e:
        log_user_activity(request, "Download", f"విఫలం: '{title}' | ఎర్రర్: {str(e)}", "Failed")
        return {"status": "error", "message": str(e)}

@app.post("/reset-app")
def reset_application():
    return {"status": "success", "message": "App reset successfully, downloads preserved."}

@app.get("/list-downloaded-books")
def list_downloaded_books(request: Request):
    user = request.session.get('user')
    if not user: return {"error": "Unauthorized"}
    files = []
    if os.path.exists(DOWNLOAD_DIR):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            for f in os.listdir(DOWNLOAD_DIR):
                if f.endswith('.pdf'):
                    cursor.execute("SELECT 1 FROM pdf_text_index WHERE filename = %s LIMIT 1", (f,))
                    is_done = cursor.fetchone() is not None
                    files.append({"filename": f, "ocr_done": is_done})
            conn.close()
        except Exception as e:
            for f in os.listdir(DOWNLOAD_DIR):
                if f.endswith('.pdf'):
                    files.append({"filename": f, "ocr_done": False})
    return {"files": files}

@app.post("/run-special-ocr")
def run_special_ocr(request: Request, filename: str = Query(...)):
    user = request.session.get('user')
    if not user: return {"error": "Unauthorized"}
    
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(filepath):
        return {"status": "error", "message": "ఫైల్ కనుగొనబడలేదు."}
        
    doc = None
    try:
        client = vision.ImageAnnotatorClient()
        doc = fitz.open(filepath)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        book_title = filename.rsplit('_', 1)[0]
        cursor.execute("DELETE FROM pdf_text_index WHERE filename = %s", (filename,))
        image_context = vision.ImageContext(language_hints=["te", "en"])

        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=300)
            image_bytes = pix.tobytes("png")            
            image = vision.Image(content=image_bytes)
            
            response = client.document_text_detection(image=image, image_context=image_context)
            if response.error.message:
                continue

            page_text = response.full_text_annotation.text if response.full_text_annotation else ""
            word_boxes = []
            
            if response.full_text_annotation:
                for page_obj in response.full_text_annotation.pages:
                    for block in page_obj.blocks:
                        for paragraph in block.paragraphs:
                            for word in paragraph.words:
                                word_str = "".join([symbol.text for symbol in word.symbols])
                                norm_vertices = word.bounding_box.normalized_vertices
                                if len(norm_vertices) >= 4:
                                    box = {
                                        "word": word_str, "x0": norm_vertices[0].x, "y0": norm_vertices[0].y,
                                        "x2": norm_vertices[2].x, "y2": norm_vertices[2].y, "confidence": round(word.confidence, 2)
                                    }
                                    word_boxes.append(box)
            
            if page_text:
                cursor.execute('''
                    INSERT INTO pdf_text_index (book_title, filename, page_number, extracted_text, word_boxes)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (book_title, filename, page_num + 1, page_text, json.dumps(word_boxes, ensure_ascii=False)))
                
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"'{book_title}' పుస్తకానికి OCR విజయవంతంగా పూర్తి చేయబడింది!"}

    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        if doc is not None:
            try: doc.close()
            except: pass
        
@app.get("/get-ocr-text")
def get_ocr_text(filename: str, page_number: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT extracted_text FROM pdf_text_index WHERE filename = %s AND page_number = %s", (filename, page_number))
        result = cursor.fetchone()
        conn.close()
        if result:
            return {"status": "success", "text": result[0]}
        else:
            return {"status": "error", "message": "ఈ పేజీకి సంబంధించిన OCR డేటా కనుగొనబడలేదు."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/update-ocr-text")
def update_ocr_text(filename: str = Form(...), page_number: int = Form(...), new_text: str = Form(...)):
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
        return "⚠️ హెచ్చరిక: `.env` ఫైల్‌లో 'GEMINI_API_KEY' లభించలేదు."

    if not snippets_list:
        return "మీరు అడిగిన ప్రశ్నకు సంబంధించిన సమాచారం ప్రస్తుత గ్రంథాలలో లభించలేదు."

    try:
        client = genai.Client(api_key=api_key)
        combined_text = "\n".join([f"- గ్రంథం: {s['book_title']} (పేజీ {s['page_number']}): {s['snippet']}" for s in snippets_list])
        
        prompt = f"""కింద ఇవ్వబడిన గ్రంథాలలోని సమాచారం ఆధారంగా, యూజర్ అడిగిన ఈ ప్రశ్నకు లేదా అంశానికి ('{query}') స్పష్టమైన, విశ్లేషణాత్మకమైన సమాధానాన్ని అచ్చతెలుగులో ఇవ్వండి. 
        
        యూజర్ ప్రశ్న/అంశం: {query}
        
        గమనిక: కేవలం లభ్యమైన గ్రంథాల సమాచారం ఆధారంగా మాత్రమే సమాధానం రాయండి. బయటి అంశాలను చేర్చవద్దు.

        ఈ క్రింది పద్ధతిలో విశ్లేషణ ఇవ్వండి:
        1. **సమాధానం/సారాంశం:** (ప్రశ్నకు నేరుగా సమాధానం)
        2. **గ్రంథాల ఆధారాలు:** (ఏ గ్రంథంలో, ఏ పేజీలో ఈ వివరాలు దొరికాయి)
        3. **వివరణ:** (మరింత లోతైన విశ్లేషణ లేదా విశేషాలు)

        గ్రంథాల సమాచారం (Snippets):
        {combined_text}"""

        chat = client.chats.create(model="gemini-3.8-flash")
        response = chat.send_message(prompt)
        
        if response and response.text:
            return response.text
                
        return "ప్రస్తుతం గూగుల్ AI సేవలు అందుబాటులో లేవు."

    except Exception as e:
        print("Gemini AI Error Exception:", str(e))
        return "సారాంశం రూపొందించడంలో సాంకేతిక లోపం ఏర్పడింది."

@app.get("/search-pdf-highlight")
def search_pdf_highlight(request: Request, query: str = Query(...)):
    """అన్ని పుస్తకాల నుండి ఎటువంటి పరిమితులు లేకుండా పూర్తి OCR డేటాను సేకరించి AI విశ్లేషణ చేయడం"""
    user = request.session.get('user')
    if not user: return {"error": "Unauthorized"}
    
    cleaned_query = query.strip()
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    matches = []
    
    # యూజర్ అడిగిన పదం/ప్రశ్న నుండి చిన్న స్టాప్ వర్డ్స్ తొలగించడం
    stop_words = ["లో", "కి", "కు", "ను", "ని", "యొక్క", "అంటే", "ఏమిటి", "ఇవ్వు"]
    words = [w for w in cleaned_query.split() if len(w) > 1 and w not in stop_words]
    
    if not words:
        words = [cleaned_query]

    # 1. విడి విడి పదాలతో లేదా పూర్తి క్వెరీతో డేటాబేస్‌లోని మొత్తం పుస్తకాల OCR టెక్స్ట్ నుండి వెతకడం
    if words:
        conditions = " OR ".join(["extracted_text ILIKE %s" for _ in words])
        params = [f"%{w}%" for w in words]
        
        # 🌟 ఎటువంటి LIMIT లేకుండా అన్ని పుస్తకాల నుండి పూర్తి ఫలితాలను లాగడం
        cursor.execute(f'''
            SELECT book_title, filename, page_number, extracted_text, word_boxes 
            FROM pdf_text_index 
            WHERE {conditions}
            ORDER BY id DESC
        ''', tuple(params))
        matches = cursor.fetchall()
        
    # ఒకవేళ విడి పదాలతో రాకపోతే, మొత్తం వాక్యంతో వెతకడం
    if not matches:
        cursor.execute('''
            SELECT book_title, filename, page_number, extracted_text, word_boxes 
            FROM pdf_text_index 
            WHERE extracted_text ILIKE %s
            ORDER BY id DESC
        ''', (f'%{cleaned_query}%',))
        matches = cursor.fetchall()
        
    conn.close()
    
    results = []
    # 🌟 అత్యంత ముఖ్యం: దొరికిన అన్ని మ్యాచెస్‌ను (గరిష్టంగా 100 వరకు) పూర్తిగా ప్రాసెస్ చేసి AI కి పంపడం
    for m in matches[:100]:  
        text = m['extracted_text'] or ""
        
        pos = -1
        matched_w = cleaned_query
        for w in words:
            pos = text.find(w)
            if pos != -1:
                matched_w = w
                break
        if pos == -1: pos = 0
            
        # ప్రతి పేజీ నుండి తగినంత పెద్ద స్నిప్పెట్ తీసుకుని AI కి ఇవ్వడం
        snippet = text[max(0, pos - 150):min(len(text), pos + 450)].replace('\n', ' ')
        
        filename = m.get('filename')
        highlighted_img_b64 = ""
        
        if not filename and m.get('book_title'):
            for f in os.listdir(DOWNLOAD_DIR):
                if m['book_title'][:10] in f and f.endswith('.pdf'):
                    filename = f
                    break
                    
        # బ్రౌజర్ వేగం కోసం మొదటి 8 ఫలితాలకు మాత్రమే PDF పేజీ ఇమేజ్ రెండర్ చేయడం
        if filename and len(results) < 8:
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.exists(filepath):
                doc = None
                try:
                    doc = fitz.open(filepath)
                    page = doc[m['page_number'] - 1]
                    pix = page.get_pixmap(dpi=110)
                    img_bytes = pix.tobytes("png")
                    highlighted_img_b64 = base64.b64encode(img_bytes).decode('utf-8')
                except Exception as ex:
                    print("PDF Render Error:", ex)
                finally:
                    if doc is not None:
                        try: doc.close()
                        except: pass

        results.append({
            "book_title": m['book_title'],
            "filename": filename or "",
            "page_number": m['page_number'],
            "snippet": f"...{snippet}...",
            "word_boxes": m.get('word_boxes'),
            "query": matched_w,
            "page_image": highlighted_img_b64
        })
        
    # 🌟 అన్ని పుస్తకాల పూర్తి డేటాతో AI సారాంశాన్ని ఉత్పత్తి చేయడం
    ai_summary = generate_ai_summary(cleaned_query, results) if results else "మీరు అడిగిన అంశానికి సంబంధించిన సమాచారం ప్రస్తుత గ్రంథాల OCR డేటాలో లభించలేదు."
    
    # డేటాబేస్‌లో రీసెర్చ్ హిస్టరీని శాశ్వతంగా సేవ్ చేయడం
    user_email = user.get("email") if user else "anonymous"
    if ai_summary and "లభించలేదు" not in ai_summary and "లోపం" not in ai_summary:
        try:
            IST = pytz.timezone('Asia/Kolkata')
            save_time = datetime.now(IST).strftime("%d-%m-%Y %I:%M %p")
            
            conn_db = get_db_connection()
            cur_db = conn_db.cursor()
            cur_db.execute('''
                INSERT INTO ai_research_history (email, query, ai_summary, sources_json, created_at)
                VALUES (%s, %s, %s, %s, %s)
            ''', (user_email, cleaned_query, ai_summary, json.dumps(results[:15], ensure_ascii=False), save_time))
            conn_db.commit()
            conn_db.close()
        except Exception as db_err:
            print("Error saving AI history to DB:", db_err)
        
    return {
        "query": cleaned_query,
        "ai_summary": ai_summary,
        "results": results
    }

@app.get("/api/ai-history")
def get_ai_history(request: Request):
    user = request.session.get('user')
    if not user: return {"error": "Unauthorized"}
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM ai_research_history WHERE email = %s ORDER BY id DESC LIMIT 50", (user.get('email'),))
    history = cursor.fetchall()
    conn.close()
    return {"history": history}

@app.delete("/api/ai-history/{history_id}")
def delete_ai_history(request: Request, history_id: int):
    user = request.session.get('user')
    if not user: return {"error": "Unauthorized"}
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ai_research_history WHERE id = %s AND email = %s", (history_id, user.get('email')))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "విజయవంతంగా తొలగించబడింది."}

@app.get("/view-pdf")
def view_pdf(filename: str, page: int = 1):
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type='application/pdf')
    return {"error": "File not found"}