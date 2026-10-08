import warnings
warnings.filterwarnings("ignore", message=".*httpx module is deprecated.*")
warnings.filterwarnings("ignore", module="authlib")

import os
import requests
import re
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import base64
import fitz  # PyMuPDF
import time
import pytz
from google import genai
from authlib.integrations.starlette_client import OAuth
from google.cloud import vision
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

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
FRONTEND_INDEX = os.path.join(FRONTEND_DIR, "index.html")
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")

# Render Environment Variable నుండి Supabase కనెక్షన్ లింక్ తీసుకుంటుంది
DB_URL = os.getenv("DATABASE_URL")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def get_db_connection():
    if not DB_URL:
        raise Exception("DATABASE_URL environment variable is not set!")
    return psycopg2.connect(DB_URL)

def init_db():
    if not DB_URL:
        print("Warning: DATABASE_URL is not set.")
        return
        
    try:
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
        
        # PDF Full-Text Search Index Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pdf_text_index (
                id SERIAL PRIMARY KEY,
                book_title TEXT,
                filename TEXT,
                page_number INTEGER,
                extracted_text TEXT,
                word_boxes TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing DB: {e}")

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
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            IST = pytz.timezone('Asia/Kolkata')
            login_time = datetime.now(IST).strftime("%d-%m-%Y %I:%M %p")
            
            cursor.execute('SELECT email FROM users WHERE email = %s', (user_dict.get('email'),))
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute('''
                    UPDATE users SET name = %s, picture = %s, last_login = %s WHERE email = %s
                ''', (user_dict.get('name'), user_dict.get('picture'), login_time, user_dict.get('email')))
            else:
                cursor.execute('''
                    INSERT INTO users (email, name, picture, last_login, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                ''', (user_dict.get('email'), user_dict.get('name'), user_dict.get('picture'), login_time))
            
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
    users = [dict(row) for row in cursor.fetchall()]
    cursor.execute("SELECT * FROM activities ORDER BY id DESC")
    activities = [dict(row) for row in cursor.fetchall()]
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
                if f.lower().endswith('.pdf'):
                    cursor.execute("SELECT 1 FROM pdf_text_index WHERE filename = %s LIMIT 1", (f,))
                    is_done = cursor.fetchone() is not None
                    files.append({"filename": f, "ocr_done": is_done})
            conn.close()
        except Exception as e:
            print("Error listing files with DB:", e)
            for f in os.listdir(DOWNLOAD_DIR):
                if f.lower().endswith('.pdf'):
                    files.append({"filename": f, "ocr_done": False})
                    
    return {"files": files}

@app.post("/run-special-ocr")
def run_special_ocr(request: Request, filename: str = Query(...), start_page: int = Query(None), end_page: int = Query(None)):
    user = request.session.get('user')
    if not user: return {"error": "Unauthorized"}
    
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(filepath):
        return {"status": "error", "message": "ఫైల్ కనుగొనబడలేదు."}
        
    doc = None
    try:
        client = vision.ImageAnnotatorClient()
        doc = fitz.open(filepath)
        total_pages = len(doc)
        
        start_idx = 0
        end_idx = total_pages - 1
        
        if start_page is not None and start_page >= 1:
            start_idx = start_page - 1
        if end_page is not None and end_page <= total_pages:
            end_idx = end_page - 1
            
        if start_idx > end_idx or start_idx >= total_pages:
            return {"status": "error", "message": "దయచేసి సరైన పేజీ నంబర్లను ఎంచుకోండి."}
            
        conn = get_db_connection()
        cursor = conn.cursor()
        book_title = filename.rsplit('_', 1)[0]
        image_context = vision.ImageContext(language_hints=["te", "en"])

        for p_num in range(start_idx + 1, end_idx + 2):
            cursor.execute("DELETE FROM pdf_text_index WHERE filename = %s AND page_number = %s", (filename, p_num))

        for page_num in range(start_idx, end_idx + 1):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=200)
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
        
        range_msg = f" (పేజీలు {start_idx + 1} నుండి {end_idx + 1} వరకు)" if (start_page or end_page) else " (పూర్తి పుస్తకం)"
        return {"status": "success", "message": f"'{book_title}' కు OCR పూర్తయింది! {range_msg}"}

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
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT extracted_text FROM pdf_text_index WHERE filename = %s AND page_number = %s", (filename, page_number))
        result = cursor.fetchone()
        conn.close()
        if result: return {"status": "success", "text": result["extracted_text"]}
        return {"status": "error", "message": "డేటా కనుగొనబడలేదు."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
@app.get("/get-indexed-books")
def get_indexed_books(request: Request):
    user = request.session.get('user')
    if not user: return {"error": "Unauthorized"}
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT DISTINCT book_title FROM pdf_text_index")
        books = [row['book_title'] for row in cursor.fetchall()]
        conn.close()
        return {"status": "success", "books": books}
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
        combined_text = "\n".join([f"- గ్రంథం: {s['book_title']} (పేజీ {s['page_number']}): {s['snippet']}" for s in snippets_list])
        
        prompt = f"""You are an expert assistant on Telugu traditional
                grammar, grounded strictly in the ('{query}') from the text వ్యాకరణము.
        యూజర్ అడిగిన ('{query}') ఓసీఆర్ చేసిన డేటా ఆధారంగా మాత్రమే సమగ్రమైన సమాధానాన్ని తెలుగులో ఇవ్వండి.
        ⚠️ అత్యంత ముఖ్యం: జవాబులో ఎట్టి పరిస్థితుల్లోనూ లటెక్ (LaTeX) కోడింగ్ లేదా గణిత చిహ్నాలు ($\text{{...}}$) వాడవద్దు. 
        సంధి రూపాలను లేదా పదాల కూర్పును ఎప్పుడూ సాధారణ తెలుగు అక్షరాలతో మాత్రమే రాయాలి.
        (ఉదాహరణకు: "నిర్జి + ఇంచు = నిర్జించు" అని మాత్రమే రాయాలి).
        ('{query}') అనే ప్రశ్నలో ముఖ్యమైన విషయం ఏ ఏ సూత్రాలు, వృత్తి, ఉదాహరణలు, వ్యాఖ్యాత వివరణల్లో ఉందో గ్రహించి, వాటిని వాడి సమాధానం ఇవ్వాలి.
        Never invent a సూత్రము, rule, or ఉదాహరణ. సూత్ర సంఖ్యను బట్టి పూర్తి సూత్రాన్ని గుర్తించి, ఉన్నది ఉన్నట్టు గానే ఇవ్వాలి.
        పూర్తి సూత్రం - OCR Data లో ఉన్నది ఉన్నట్టుగా ఇవ్వాలి. సామాన్య భాషలోకి మార్చకూడదు.
        
        ఓసీఆర్ డేటాలో ఏది పూర్తి సూత్రం; ఏవి వృత్తి, ఉదాహరణలు; ఏది వ్యాఖ్యానము; ఏవి విశేషాలు; తెలుసుకోడానికి ఉదాహరణలు:

        "టవర్ణకం బాడ్వాదుల కగు నగుచో నంత లోపంబగు. 7" (పూర్తి సూత్రం)
        "ఆడు... ఆట; ఊరు... ఊట; ఊఱడు... ఊఱట; ఏఁకరు... ఏంకట;
        ఓడు...ఓట; తివురు.... తివుట; తేరు... తేట; త్రిమ్మరు.... త్రిమ్మట; పండు....పంట;
        పాడు...పాట; మండు. మట; వండు.... వంట; వేసఱు....వేసట.
        అనుప్రయుక్తంబయిన యాట శబ్దంబు ప్రాయికంబుగాఁ గ్లీబసమంబగు. కోలాటము,
        పోరాటము, మండ్రాటము - ఇత్యాదులు." (వృత్తి, ఉదాహరణలు)
        -
        "వ్యా. ఆడు - మొదలగు ధాతువులకు ట వర్ణకమగును. అగుచోఁ దుది
        యక్షరమునకు లోపమగును. ఆడు+ట = ఆట. తత్సమ 39చే - ఆట+ము. 57చే
        ఆట. ఇట్లే తక్కినవి. అనుప్రయుక్తమగు ఆట శబ్దము తఱచుగాఁ గ్లీబసమమగును. కోల+ఆట.
        కోలాట. తత్సమ 39చే - కోలాటము. ఇట్లే పోరాడు+ట = పోరాటము.
        సంధి 4చే
        మండుట
        =
        మండ్రాటము." (వ్యాఖ్యానము)
        "విశే :- ప్రాయికంబుగా - అనుటచే, కోలాట అనియు నుండును." (విశేషాలు)
        -
        "టువర్ణకంబు పడ్వాదుల కగు నగుచో నవి యాద్యక్షర శేషంబులయి
        దీర్ఘంబు నొందు. 8" (పూర్తి సూత్రం)
        పడు... పాట, చెడు... చేటు, అడుచు... ఆటు, కఱచు... కాటు, పొడుచు...
        పోటు, వయిచు....వాటు, ఏయు.... ఏటు, ఓడు....ఓటు, వ్రేయు.... వ్రేటు. (వృత్తి, ఉదాహరణలు)
        =
        .
        "వ్యా. పడు
        పడు - మొదలగు ధాతువులకు 'టు' ప్రత్యయమగును. అగుచో వాని
        తొలి యక్షరము మిగిలి దీర్ఘమునందును. పడు+టు, అనుచో - డు వర్ణము లోపించి,
        శేషించిన మొదటి యక్షరమునకు దీర్ఘము : పా+టు = పాటు. తత్సమ 48చే - పాటు+వు.
        :
        పాటు. ఇట్లే చెడు+టు = చే+టు : చేటు. అడుచు+టు = ఆ+టు : ఆటు. మొ||" (వ్యాఖ్యానము)
        "పాఠపరిశీలన :- ప్రథమ ముద్రణమున, కఱుచు
        ప్రథమ ముద్రణమున, కఱుచు - అని కలదు. కఱచు అనియే
        57 చే
        -
        యుండవలెను." (పాఠపరిశీలన)
        "పవర్ణకంబు తిరియ్వాదుల కగు. 9" (పూర్తి సూత్రం)
        తిరియు....తిరిపము, కలియున కత్వంబునగు : కలపము, పొలయు....
        పొలపము, మురియు.... మురిపము : సొలయు....సొలపము,
        వలియు.... వలిపము." (వృత్తి, ఉదాహరణలు)
        "వ్యా. తిరియు - మొదలగు వానికి, ప వర్ణకమగును. తిరియు+ప - 2చే
        తిరి+ప. ఆచ్చిక 2చే - క్లీబస్మమగును గాన, తత్సమ 39చే - తిరిప+ము = తిరిపము,
        కలియు ధాతువునకు అత్వము సైతమగును. కలియు+ప. 2చే - కలి+ప
        “కలియునకత్వంబునగు" కల+ప. క్లీబస్మము గాన తత్సమ 39చే కలప +ము =
        పొలప+ము = పొలపము ఇత్యాది.
        కలపము. పొలయు+ప" 
        =
        308
        -(వ్యాఖ్యానము)
        
        యూజర్ ఇన్పుట్ గా ఇచ్చిన (''{query}'') అనే ప్రశ్నకు సంబంధించిన అన్ని సూత్రాలు ఒకదాని తరువాత ఒకటి ai summary లో ఉండాలి.
        
        ⚠️ అత్యంత ముఖ్యం: ప్రతిసారీ AI సొంతంగా లేఅవుట్ తయారు చేయకూడదు. కింది ఇచ్చిన ఖచ్చితమైన **స్టాండర్డ్ HTML టెంప్లేట్** రూపంలో మాత్రమే సమాధానాన్ని అవుట్‌పుట్ ఇవ్వాలి:

        <div style="background-color: #f8f9fa; border-left: 5px solid #0d6efd; padding: 15px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
            <h4 style="color: #0d6efd; font-weight: bold; margin-bottom: 10px;">📜 సూత్రము</h4>
            <p style="font-size: 25px; margin-bottom: 5px;"><strong>పూర్తి సూత్రం:</strong> [ఇక్కడ సూత్రం రాయండి]</p>
            <p style="font-size: 20px; color: #6c757d; margin-bottom: 0;"><strong>గ్రంథ సూచన:</strong> [గ్రంథం, పరిచ్ఛేదం, సూత్ర సంఖ్య]</p>
        </div>

        <div style="background-color: #fff3cd; border-left: 5px solid #ffc107; padding: 15px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
            <h4 style="color: #856404; font-weight: bold; margin-bottom: 5px;">💡 వృత్తి / వివరణ</h4>
            <p style="font-size: 20px; margin-bottom: 0;">[ఇక్కడ వృత్తి, వ్యాఖ్యాత వివరణ రాయండి]</p>
        </div>

        <div style="background-color: #d1e7dd; border-left: 5px solid #198754; padding: 15px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
            <h4 style="color: #0f5132; font-weight: bold; margin-bottom: 10px;">🌟 ఉదాహరణలు</h4>
            <div style="background: #ffffff; padding: 10px; border-radius: 4px; border: 1px solid #badbcc;">
                [ఇక్కడ ఉదాహరణలను రంగుల బాక్సులు లేదా బోల్డ్ అక్షరాలతో ఇవ్వండి]
            </div>
        </div>
        
        <div style="background-color: #d1e7dd; border-left: 5px solid #198754; padding: 15px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
            <h4 style="color: #0f5132; font-weight: bold; margin-bottom: 10px;">🌟 రూపసాధన</h4>
            <div style="background: #ffffff; padding: 10px; border-radius: 4px; border: 1px solid #badbcc;">
                [ఇక్కడ రూపసాధనను రంగుల బాక్సులు లేదా బోల్డ్ అక్షరాలతో ఇవ్వండి]
            </div>
        </div>

        <div style="background-color: #cfe2ff; border-left: 5px solid #0dcaf0; padding: 15px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
            <h4 style="color: #055160; font-weight: bold; margin-bottom: 10px;">🔍 విశేషాలు, పాఠపరిశీలన</h4>
            <p style="font-size: 20px; margin-bottom: 5px;"><strong>విశే :-</strong> [విశేషాంశాలు]</p>
            <p style="font-size: 20px; margin-bottom: 0;"><strong>పాఠపరిశీలన :-</strong> [పాఠపరిశీలన వివరాలు]</p>
        </div>
        
        - No preamble, no closing summary. Start directly with the HTML template structure given above.

        {combined_text}"""

        chat = client.chats.create(model="gemini-3.8-flash")
        response = chat.send_message(prompt)
        return response.text if response and response.text else "AI సేవలు అందుబాటులో లేవు."
    except Exception as e:
        print("Gemini AI Error:", str(e))
        return "సారాంశం రూపొందించడంలో సాంకేతిక లోపం ఏర్పడింది."

@app.get("/search-pdf-highlight")
def search_pdf_highlight(request: Request, query: str = Query(...), books: str = Query(None)):
    user = request.session.get('user')
    if not user: return {"error": "Unauthorized"}
    
    cleaned_query = query.strip()
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    selected_books = [b.strip() for b in books.split(",")] if books else []
    book_filter = ""
    book_params = []
    if selected_books:
        placeholders = ",".join(["%s"] * len(selected_books))
        book_filter = f" AND book_title IN ({placeholders})"
        book_params = selected_books

    matches = []
    stop_words = ["లో", "కి", "కు", "ను", "ని", "యొక్క", "అంటే", "ఏమిటి", "ఇవ్వు", "గురించి"]
    words = [w for w in cleaned_query.split() if len(w) > 1 and w not in stop_words]
    if not words: words = [cleaned_query]

    if words:
        conditions = " OR ".join(["extracted_text ILIKE %s OR book_title ILIKE %s" for _ in words])
        params = []
        for w in words:
            params.extend([f"%{w}%", f"%{w}%"])
            
        cursor.execute(f'''
            SELECT book_title, filename, page_number, extracted_text, word_boxes 
            FROM pdf_text_index 
            WHERE ({conditions}){book_filter}
            ORDER BY id DESC
        ''', tuple(params + book_params))
        matches = [dict(row) for row in cursor.fetchall()]
        
    if not matches:
        cursor.execute(f'''
            SELECT book_title, filename, page_number, extracted_text, word_boxes 
            FROM pdf_text_index 
            WHERE (extracted_text ILIKE %s OR book_title ILIKE %s){book_filter}
            ORDER BY id DESC
        ''', tuple([f'%{cleaned_query}%', f'%{cleaned_query}%'] + book_params))
        matches = [dict(row) for row in cursor.fetchall()]

    if not matches and not selected_books:
        cursor.execute('''
            SELECT book_title, filename, page_number, extracted_text, word_boxes 
            FROM pdf_text_index 
            WHERE book_title ILIKE %s OR book_title ILIKE %s
            ORDER BY id DESC
            LIMIT 50
        ''', ('%బాలవ్యాకరణ%', '%ప్రౌఢవ్యాకరణ%'))
        matches = [dict(row) for row in cursor.fetchall()]
        
    conn.close()
    
    results = []
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
        
        if not filename and m.get('book_title'):
            for f in os.listdir(DOWNLOAD_DIR):
                if m['book_title'][:10] in f and f.endswith('.pdf'):
                    filename = f
                    break

        # ఇమేజ్ జనరేషన్ కోడ్ పూర్తిగా తొలగించబడింది (Fast & Clean)
        results.append({
            "book_title": m['book_title'],
            "filename": filename or "",
            "page_number": m['page_number'],
            "snippet": f"...{snippet}...",
            "word_boxes": json.loads(m['word_boxes']) if m.get('word_boxes') else [],
            "query": matched_w,
            "page_image": ""  # ఖాళీగా పంపబడుతుంది
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