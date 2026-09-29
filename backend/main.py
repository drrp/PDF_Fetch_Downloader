import os
import re
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Path configuration
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
FRONTEND_INDEX = os.path.join(FRONTEND_DIR, "index.html")
ROOT_INDEX = os.path.join(BASE_DIR, "index.html")

# Create a dedicated downloads folder in your project directory
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Mount frontend and downloads directories
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


def fetch_all_pages(query: str):
    all_books = []
    base_domain = "https://sundarayya.org"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    # Dynamically loop through all pages until no further results exist
    page = 0
    while True:
        url = f"{base_domain}/books"
        params = {
            "field_book_title_in_english_value": query,
            "page": page,
        }

        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            if response.status_code != 200:
                break

            soup = BeautifulSoup(response.text, "html.parser")
            cards = soup.select(".book-card-margin")

            if not cards:
                break

            page_books_added = 0
            for card in cards:
                title_tag = None
                for a_tag in card.select(".book-title a"):
                    href_val = a_tag.get("href", "")
                    if href_val and href_val != "#":
                        title_tag = a_tag
                        break
                
                author_tag = card.select_one(".book-author")
                img_tag = card.select_one(".book-image img") 

                author_text = ""
                if author_tag:
                    author_text = author_tag.get_text(strip=True)

                if title_tag:
                    title_text = title_tag.get_text(strip=True)
                    detail_href = title_tag.get("href", "")

                    detail_url = f"{base_domain}{detail_href}" if detail_href.startswith("/") else detail_href
                    display_title = f"{title_text} - {author_text}" if author_text else title_text

                    cover_url = ""
                    if img_tag and img_tag.get("src"):
                        src = img_tag.get("src")
                        cover_url = f"{base_domain}{src}" if src.startswith("/") else src

                    book_item = {
                        "title": display_title,
                        "download_url": detail_url,
                        "cover_image": cover_url
                    }

                    if book_item not in all_books:
                        all_books.append(book_item)
                        page_books_added += 1

            next_page_btn = soup.select_one(".pager__item--next a")
            if not next_page_btn or page_books_added == 0:
                break

            page += 1

        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            break

    return all_books


@app.get("/search")
def search(query: str = Query(..., description="Search term")):
    results = fetch_all_pages(query)
    return {"results": results, "total": len(results)}


@app.get("/download-book")
def download_book(book_page_url: str = Query(...), title: str = Query(...)):
    headers = {"User-Agent": "Mozilla/5.0"}
    base_domain = "https://sundarayya.org"
    
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
    filename = f"{safe_title}.pdf"
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    
    try:
        response = requests.get(book_page_url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
        
        pdf_href = None
        
        # 1. Extract from the button's onclick attribute
        download_button = soup.select_one("button[onclick*='location.href']")
        if download_button:
            onclick_text = download_button.get("onclick")
            match = re.search(r"location\.href=['\"]([^'\"]+)['\"]", onclick_text)
            if match:
                pdf_href = match.group(1)

        # 2. Fallback to any <a> tag containing '.pdf'
        if not pdf_href:
            fallback_pdf = soup.find("a", href=lambda href: href and href != "#" and ".pdf" in href.lower())
            if fallback_pdf:
                pdf_href = fallback_pdf.get("href")
                
        # 3. Fallback to typical Drupal file paths
        if not pdf_href:
            for a_tag in soup.select("#block-sundarayya-content a"):
                href = a_tag.get("href")
                if href and href != "#" and ("sites/default/files" in href or "download" in href):
                    pdf_href = href
                    break
        
        if not pdf_href or pdf_href == "#":
            return {"status": "error", "message": "Could not extract the PDF link from the page."}
            
        pdf_url = f"{base_domain}{pdf_href}" if pdf_href.startswith("/") else pdf_href
        
        pdf_response = requests.get(pdf_url, headers=headers, stream=True)
        if pdf_response.status_code != 200:
            return {"status": "error", "message": "Failed to download PDF file."}
            
        with open(filepath, "wb") as f:
            for chunk in pdf_response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
                    
        return {
            "status": "success", 
            "message": f"Saved to {filepath}", 
            "file_url": f"/downloads/{filename}"
        }
        
    except Exception as e:
        print(f"Download error: {e}")
        return {"status": "error", "message": str(e)}