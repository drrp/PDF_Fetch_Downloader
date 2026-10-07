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
import fitz  # PyMuPDF
import time
import pytz
from google import genai
from authlib.integrations.starlette_client import OAuth
from google.cloud import vision
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

load_dotenv(override=True)

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
    
    cursor.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT FALSE;')
    
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
    
    cursor.execute('ALTER TABLE activities ADD COLUMN IF NOT EXISTS status TEXT DEFAULT "Success";')
    
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

app = FastAPI()

app.add_middleware(
    SessionMiddleware, 
    secret_key=os.getenv("SESSION_SECRET_KEY", "your-secret-key"),
    same_site="lax",
    https_only=False
)

oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

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
            ''', (user.get("email"), user.get("name"), user.get("picture"), action_type, details, current_time, status))
            cursor.execute('UPDATE users SET is_active = TRUE WHERE email = %s', (user.get("email"),))
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
            ''', (user_dict.get('email'), user_dict.get('name'), user_dict.get('picture'), login_time))
            conn.commit()
            conn.close()
                
            admin_email = "rpsarma9247@gmail.com"
            if user_dict.get('email') == admin_email:
                return RedirectResponse(url='/admin', status_code=303)
                
    except Exception as e:
        print(f"OAuth Error: {e}")
        
    return RedirectResponse(url='/', status_code=303)

@app.get('/profile')
def user_profile(request: Request):
    user = request.session.get('user')
    if not user:
        return RedirectResponse(url='/', status_code=303)
    profile_page = os.path.join(FRONTEND_DIR, "profile.html")
    return FileResponse(profile_page) if os.path.exists(profile_page) else {"error": "profile.html not found"}

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

@app.get('/ocr-hub')
def ocr_hub_page(request: Request):
    user = request.session.get('user')
    if not user:
        return RedirectResponse(url='/login/google', status_code=303)
    ocr_page = os.path.join(FRONTEND_DIR, "ocr.html")
    return FileResponse(ocr_page) if os.path.exists(ocr_page) else {"error": "ocr.html not found"}

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
    return {"error": "index.html not found"}

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
        return {"status": "success", "message": "పుస్తకం విజయవంతంగా డౌన్‌లోడ్ చేయబడింది!", "file_url": f"/downloads/{filename}"}
    except Exception as e:
        log_user_activity(request, "Download", f"విఫలం: '{title}' | ఎర్రర్: {str(e)}", "Failed")
        return {"status": "error", "message": str(e)}

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
        except:
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
            pix = page.get_pixmap(dpi=200) # మెమరీ ఆప్టిమైజేషన్ కోసం DPI తగ్గించబడింది
            image_bytes = pix.tobytes("png")            
            image = vision.Image(content=image_bytes)
            
            response = client.document_text_detection(image=image, image_context=image_context)
            if response.error.message: continue

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
                                    word_boxes.append({
                                        "word": word_str, "x0": norm_vertices[0].x, "y0": norm_vertices[0].y,
                                        "x2": norm_vertices[2].x, "y2": norm_vertices[2].y, "confidence": round(word.confidence, 2)
                                    })
            
            if page_text:
                cursor.execute('''
                    INSERT INTO pdf_text_index (book_title, filename, page_number, extracted_text, word_boxes)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (book_title, filename, page_num + 1, page_text, json.dumps(word_boxes, ensure_ascii=False)))
                
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"'{book_title}' పుస్తకానికి OCR పూర్తయింది!"}

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
        if result: return {"status": "success", "text": result[0]}
        return {"status": "error", "message": "డేటా కనుగొనబడలేదు."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/update-ocr-text")
def update_ocr_text(filename: str = Form(...), page_number: int = Form(...), new_text: str = Form(...)):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE pdf_text_index SET extracted_text = %s WHERE filename = %s AND page_number = %s', (new_text, filename, page_number))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "మార్పులు సేవ్ చేయబడ్డాయి!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def generate_ai_summary(query, snippets_list):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key: return "⚠️ API కీ లభించలేదు."
    if not snippets_list: return "సమాచారం లభించలేదు."

    try:
        client = genai.Client(api_key=api_key)
        
        # పూర్తి స్నిప్పెట్స్‌ను కంబైన్ చేయడం
        combined_text = "\n".join([f"- గ్రంథం: {s['book_title']} (పేజీ {s['page_number']}): {s['snippet']}" for s in snippets_list])
        
        prompt = f"""కింద ఇవ్వబడిన అన్ని గ్రంథాల (బాలవ్యాకరణం, ప్రౌఢవ్యాకరణం తదితర అన్నీ) పూర్తి సమాచారం ఆధారంగా, యూజర్ అడిగిన ఈ ప్రశ్నకు ('{query}') స్పష్టమైన, సమగ్రమైన, విశ్లేషణాత్మకమైన సమాధానాన్ని అచ్చతెలుగులో ఇవ్వండి. 

        ⚠️ అత్యంత ముఖ్యం: జవాబులో ఎట్టి పరిస్థితుల్లోనూ లటెక్ (LaTeX) కోడింగ్ లేదా గణిత చిహ్నాలు ($\text{{...}}$) వాడవద్దు. సంధి రూపాలను లేదా పదాల కూర్పును ఎప్పుడూ సాధారణ తెలుగు అక్షరాలతో మాత్రమే రాయాలి. (ఉదాహరణకు: "నిర్జి + ఇంచు = నిర్జించు" అని మాత్రమే రాయండి).

        ఇవ్వవలసిన పద్ధతి:
        1. **సమాధానం/సారాంశం:** (ప్రశ్నకు నేరుగా సమాధానం)
        2. **గ్రంథాల ఆధారాలు:** (ఏ గ్రంథంలో, ఏ పేజీలో వివరాలు దొరికాయి)
        3. **వివరణ:** (లోతైన విశ్లేషణ మరియు సూత్రాల విశేషాలు)

        గ్రంథాల పూర్తి సమాచారం:
        {combined_text}"""

        chat = client.chats.create(model="gemini-3.8-flash")
        response = chat.send_message(prompt)
        
        return response.text if response and response.text else "AI సేవలు అందుబాటులో లేవు."
    except Exception as e:
        print("Gemini AI Error:", str(e))
        return "సారాంశం రూపొందించడంలో సాంకేతిక లోపం ఏర్పడింది."

@app.get("/search-pdf-highlight")
def search_pdf_highlight(request: Request, query: str = Query(...)):
    """బాలవ్యాకరణం, ప్రౌఢవ్యాకరణం సహా అన్ని పుస్తకాల మొత్తం OCR డేటా నుండి సమగ్రంగా శోధించడం"""
    user = request.session.get('user')
    if not user: return {"error": "Unauthorized"}
    
    cleaned_query = query.strip()
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    matches = []
    
    # చిన్న స్టాప్ వర్డ్స్ తొలగించి, అసలైన కీలక పదాలను వేరు చేయడం
    stop_words = ["లో", "కి", "కు", "ను", "ని", "యొక్క", "అంటే", "ఏమిటి", "ఇవ్వు", "గురించి"]
    words = [w for w in cleaned_query.split() if len(w) > 1 and w not in stop_words]
    
    if not words:
        words = [cleaned_query]

    # 🌟 1. యూజర్ ఇచ్చిన పదాలు 'extracted_text' లో లేదా 'book_title' లో ఉన్నాయా అని అన్ని పుస్తకాల్లో ఏకకాలంలో వెతకడం
    if words:
        # ప్రతి పదానికి both extracted_text మరియు book_title లో చెక్ చేసేలా కండిషన్
        conditions = " OR ".join(["extracted_text ILIKE %s OR book_title ILIKE %s" for _ in words])
        params = []
        for w in words:
            params.extend([f"%{w}%", f"%{w}%"])
        
        # ఎలాంటి లిమిట్స్ లేకుండా మొత్తం డేటాబేస్‌ను స్కాన్ చేయడం (బాలవ్యాకరణం & ప్రౌఢవ్యాకరణం రెండూ వస్తాయి)
        cursor.execute(f'''
            SELECT book_title, filename, page_number, extracted_text, word_boxes 
            FROM pdf_text_index 
            WHERE {conditions}
            ORDER BY id DESC
        ''', tuple(params))
        matches = cursor.fetchall()
        
    # 🌟 2. ఒకవేళ విడి పదాలతో రాకపోతే, మొత్తం క్వెరీతో వెతకడం
    if not matches:
        cursor.execute('''
            SELECT book_title, filename, page_number, extracted_text, word_boxes 
            FROM pdf_text_index 
            WHERE extracted_text ILIKE %s OR book_title ILIKE %s
            ORDER BY id DESC
        ''', (f'%{cleaned_query}%', f'%{cleaned_query}%'))
        matches = cursor.fetchall()

    # 🌟 3. అత్యంత ముఖ్యం: ఒకవేళ అప్పటికీ మ్యాచెస్ రాకపోతే, నేరుగా "బాలవ్యాకరణ" మరియు "ప్రౌఢవ్యాకరణ" పుస్తకాలను టార్గెట్ చేసి డేటా లాగడం
    if not matches:
        cursor.execute('''
            SELECT book_title, filename, page_number, extracted_text, word_boxes 
            FROM pdf_text_index 
            WHERE book_title ILIKE %s OR book_title ILIKE %s
            ORDER BY id DESC
            LIMIT 50
        ''', ('%బాలవ్యాకరణ%', '%ప్రౌఢవ్యాకరణ%'))
        matches = cursor.fetchall()
        
    conn.close()
    
    results = []
    # దొరికిన అన్ని మ్యాచింగ్ ఫలితాలను AI విశ్లేషణ కోసం పంపడం
    for m in matches[:50]:  
        text = m['extracted_text'] or ""
        
        pos = -1
        matched_w = cleaned_query
        for w in words:
            pos = text.find(w)
            if pos != -1:
                matched_w = w
                break
        if pos == -1: pos = 0
            
        snippet = text[max(0, pos - 120):min(len(text), pos + 350)].replace('\n', ' ')
        
        filename = m.get('filename')
        highlighted_img_b64 = ""
        
        if not filename and m.get('book_title'):
            for f in os.listdir(DOWNLOAD_DIR):
                if m['book_title'][:10] in f and f.endswith('.pdf'):
                    filename = f
                    break
                    
        # మెమరీ మరియు స్పీడ్ కోసం మొదటి 4 ఫలితాలకు మాత్రమే PDF పేజీ ఇమేజ్ రెండర్ చేయడం
        if filename and len(results) < 4:
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.exists(filepath):
                doc = None
                try:
                    doc = fitz.open(filepath)
                    page = doc[m['page_number'] - 1]
                    pix = page.get_pixmap(dpi=100)
                    img_bytes = pix.tobytes("png")
                    highlighted_img_b64 = base64.b64encode(img_bytes).decode('utf-8')
                except: pass
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
        
    ai_summary = generate_ai_summary(cleaned_query, results) if results else "మీరు అడిగిన అంశానికి సంబంధించిన సమాచారం ప్రస్తుత గ్రంథాల OCR డేటాలో లభించలేదు."
    
    return {
        "query": cleaned_query,
        "ai_summary": ai_summary,
        "results": results
    }

@app.get("/view-pdf")
def view_pdf(filename: str, page: int = 1):
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type='application/pdf')
    return {"error": "File not found"}