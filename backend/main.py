import os
import re
import uuid
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# --- స్క్రాపర్ క్లాస్‌ల ఇంపోర్ట్స్ (Shodhganga తొలగించబడింది) ---
from backend.svk import SVKScraper
from backend.archive import InternetArchiveScraper
from backend.manasu import ManasuFoundationScraper
from backend.ttd import TTDScraper

app = FastAPI()

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
def read_root():
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
    query: str = Query(..., description="Search term"),
    use_svk: bool = Query(True, description="Fetch from SVK"),
    use_ia: bool = Query(True, description="Fetch from Internet Archive"),
    use_manasu: bool = Query(True, description="Fetch from Manasu Foundation"),
    use_ttd: bool = Query(True, description="Fetch from TTD Ebooks"),
    page: int = Query(1, description="Page number"),
    limit: int = Query(10, description="Items per page")
):
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
def download_book(book_page_url: str = Query(...), title: str = Query(...), source: str = Query("SVK")):
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