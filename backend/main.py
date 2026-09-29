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


# --- ARCHITECTURE: Scraper Classes ---

class BaseLibraryScraper:
    def search(self, query: str):
        raise NotImplementedError
    
    def download(self, book_url: str, title: str, filepath: str):
        raise NotImplementedError


class SVKScraper(BaseLibraryScraper):
    def __init__(self):
        self.base_domain = "https://sundarayya.org"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    def search(self, query: str):
        all_books = []
        page = 0
        while True:
            url = f"{self.base_domain}/books"
            params = {"field_book_title_in_english_value": query, "page": page}
            try:
                response = requests.get(url, params=params, headers=self.headers, timeout=15)
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

                    author_text = author_tag.get_text(strip=True) if author_tag else ""

                    if title_tag:
                        title_text = title_tag.get_text(strip=True)
                        detail_href = title_tag.get("href", "")
                        detail_url = f"{self.base_domain}{detail_href}" if detail_href.startswith("/") else detail_href
                        display_title = f"{title_text} - {author_text}" if author_text else title_text

                        cover_url = ""
                        if img_tag and img_tag.get("src"):
                            src = img_tag.get("src")
                            cover_url = f"{self.base_domain}{src}" if src.startswith("/") else src

                        book_item = {
                            "title": display_title,
                            "download_url": detail_url,
                            "cover_image": cover_url,
                            "source": "SVK"
                        }
                        if book_item not in all_books:
                            all_books.append(book_item)
                            page_books_added += 1

                next_page_btn = soup.select_one(".pager__item--next a")
                if not next_page_btn or page_books_added == 0:
                    break
                page += 1
            except Exception as e:
                print(f"SVK Error on page {page}: {e}")
                break
        return all_books

    def download(self, book_page_url: str, title: str, filepath: str):
        response = requests.get(book_page_url, headers=self.headers, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
        pdf_href = None
        
        download_button = soup.select_one("button[onclick*='location.href']")
        if download_button:
            match = re.search(r"location\.href=['\"]([^'\"]+)['\"]", download_button.get("onclick", ""))
            if match:
                pdf_href = match.group(1)

        if not pdf_href:
            fallback_pdf = soup.find("a", href=lambda h: h and h != "#" and ".pdf" in h.lower())
            if fallback_pdf:
                pdf_href = fallback_pdf.get("href")

        if not pdf_href or pdf_href == "#":
            raise Exception("Could not extract PDF link from SVK book page.")

        pdf_url = f"{self.base_domain}{pdf_href}" if pdf_href.startswith("/") else pdf_href
        pdf_response = requests.get(pdf_url, headers=self.headers, stream=True)
        if pdf_response.status_code != 200:
            raise Exception("Failed to download PDF stream from SVK.")

        with open(filepath, "wb") as f:
            for chunk in pdf_response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)


class InternetArchiveScraper(BaseLibraryScraper):
    def __init__(self):
        self.api_url = "https://archive.org/advancedsearch.php"

    def search(self, query: str):
        all_books = []
        params = {
            "q": f"title:({query}) OR description:({query}) AND mediatype:(texts)",
            "fl[]": "identifier,title,creator,publicdate",
            "rows": 20,
            "output": "json"
        }
        try:
            res = requests.get(self.api_url, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json()
                docs = data.get("response", {}).get("docs", [])
                for doc in docs:
                    identifier = doc.get("identifier")
                    title = doc.get("title", "Untitled")
                    creator = doc.get("creator", "Unknown Author")
                    if isinstance(creator, list):
                        creator = ", ".join(creator)

                    display_title = f"{title} - {creator}"
                    detail_url = f"https://archive.org/details/{identifier}"
                    cover_url = f"https://archive.org/services/img/{identifier}"

                    all_books.append({
                        "title": display_title,
                        "download_url": detail_url,
                        "cover_image": cover_url,
                        "source": "Internet Archive",
                        "identifier": identifier
                    })
        except Exception as e:
            print(f"Internet Archive Search Error: {e}")
        return all_books

    def download(self, book_url: str, title: str, filepath: str):
        identifier = book_url.rstrip("/").split("/")[-1]
        metadata_url = f"https://archive.org/metadata/{identifier}"
        res = requests.get(metadata_url, timeout=15)
        if res.status_code != 200:
            raise Exception("Failed to fetch Internet Archive metadata.")
            
        files = res.json().get("files", [])
        pdf_file = None
        for file in files:
            if file.get("format") == "Text PDF" or file.get("name", "").endswith(".pdf"):
                pdf_file = file.get("name")
                break
        
        if not pdf_file:
            for file in files:
                if file.get("name", "").lower().endswith(".pdf"):
                    pdf_file = file.get("name")
                    break

        if not pdf_file:
            raise Exception("No direct PDF file found in this Internet Archive item.")

        pdf_download_url = f"https://archive.org/download/{identifier}/{pdf_file}"
        pdf_response = requests.get(pdf_download_url, stream=True, timeout=30)
        if pdf_response.status_code != 200:
            raise Exception("Failed to download PDF from Internet Archive.")

        with open(filepath, "wb") as f:
            for chunk in pdf_response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)


class ManasuFoundationScraper(BaseLibraryScraper):
    def __init__(self):
        self.base_domain = "https://www.manasufoundation.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    def search(self, query: str):
        all_books = []
        url = f"{self.base_domain}/books/"
        params = {"wbg_title_s": query}
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=15)
            if response.status_code != 200:
                return all_books

            soup = BeautifulSoup(response.text, "html.parser")
            items = soup.select(".wgb-item-link, .wbg-main-wrapper a, div.wbg-item a")
            
            seen_urls = set()
            for item in items:
                href = item.get("href")
                if not href or "/books/" not in href or href == self.base_domain + "/books/":
                    continue
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                title_text = item.get_text(strip=True)
                img_tag = item.find("img")
                cover_url = ""
                if img_tag and img_tag.get("src"):
                    cover_url = img_tag.get("src")
                    if img_tag.get("alt"):
                        title_text = img_tag.get("alt")

                if not title_text:
                    title_text = "Manasu Foundation Book"

                all_books.append({
                    "title": title_text,
                    "download_url": href,
                    "cover_image": cover_url,
                    "source": "Manasu Foundation"
                })
        except Exception as e:
            print(f"Manasu Foundation Search Error: {e}")
        return all_books

    def download(self, book_page_url: str, title: str, filepath: str):
        response = requests.get(book_page_url, headers=self.headers, timeout=15)
        if response.status_code != 200:
            raise Exception("Failed to open Manasu Foundation book page.")

        soup = BeautifulSoup(response.text, "html.parser")
        download_a = soup.select_one("#post-5883 div.wbg-details-column.wbg-details-wrapper div div.wbg-details-summary span.wbg-single-button-container a")
        if not download_a:
            download_a = soup.select_one(".wbg-single-button-container a, .wbg-details-summary a[href*='drive.google.com'], .wbg-details-summary a")

        if not download_a or not download_a.get("href"):
            raise Exception("Could not find download link on Manasu Foundation book page.")

        target_url = download_a.get("href")
        if "drive.google.com" in target_url:
            match = re.search(r"/d/([a-zA-Z0-9_-]+)", target_url)
            if match:
                file_id = match.group(1)
                target_url = f"https://drive.google.com/uc?export=download&id={file_id}"

        file_response = requests.get(target_url, headers=self.headers, stream=True, timeout=30)
        if file_response.status_code != 200:
            raise Exception("Failed to download PDF stream from destination link.")

        with open(filepath, "wb") as f:
            for chunk in file_response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)


class TTDScraper(BaseLibraryScraper):
    def __init__(self):
        self.base_domain = "https://ebooks.tirumala.org"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,te;q=0.8"
        }

    def search(self, query: str):
        all_books = []
        url = f"{self.base_domain}/search"
        params = {"value": query, "key": "search"}
        
        try:
            # Increased search timeout to 60 seconds to match the slow server response rate
            response = requests.get(url, params=params, headers=self.headers, timeout=90)
            if response.status_code != 200:
                print(f"TTD Server responded with status code: {response.status_code}")
                return all_books

            soup = BeautifulSoup(response.text, "html.parser")
            cards = soup.select("a.btn, .book-item, .col-md-3, .product-layout, div.search-result")
            
            seen_urls = set()
            for card in cards:
                link_tag = card if card.name == "a" and "read?" in card.get("href", "") else card.select_one("a[href*='read?']")
                if not link_tag:
                    continue

                href = link_tag.get("href")
                if not href:
                    continue

                detail_url = f"{self.base_domain}/{href}" if not href.startswith("http") else href
                if detail_url in seen_urls:
                    continue
                seen_urls.add(detail_url)

                title_text = "TTD Publication"
                cover_url = ""
                
                img_tag = link_tag.find("img") or card.find("img")
                if img_tag:
                    if img_tag.get("alt"):
                        title_text = img_tag.get("alt").strip()
                    if img_tag.get("src"):
                        src = img_tag.get("src")
                        cover_url = f"{self.base_domain}/{src}" if not src.startswith("http") else src

                all_books.append({
                    "title": title_text,
                    "download_url": detail_url,
                    "cover_image": cover_url,
                    "source": "TTD Ebooks"
                })
        except Exception as e:
            print(f"TTD Ebooks Search Error: {e}")
        return all_books

    def download(self, book_page_url: str, title: str, filepath: str):
        match = re.search(r"id=(\d+)", book_page_url)
        if not match:
            raise Exception("Could not find publication ID for this TTD ebook.")
            
        book_id = match.group(1)
        pdf_url = f"{self.base_domain}/download.php?id={book_id}"

        session = requests.Session()
        session.headers.update(self.headers)
        
        try:
            # Pre-visit the reader page with an extended 60-second window
            session.get(book_page_url, timeout=60)
            
            # Request the stream with a 120-second (2-minute) timeout to handle slow server speeds
            download_headers = {"Referer": book_page_url}
            pdf_response = session.get(pdf_url, headers=download_headers, stream=True, timeout=120)
            
            if pdf_response.status_code != 200:
                alt_url = f"{self.base_domain}/uploads/pdf/{book_id}.pdf"
                pdf_response = session.get(alt_url, headers=download_headers, stream=True, timeout=120)

            if pdf_response.status_code != 200:
                raise Exception(f"TTD server response error (HTTP {pdf_response.status_code}).")

            # Write the file safely in chunks without rushing the slow connection
            with open(filepath, "wb") as f:
                for chunk in pdf_response.iter_content(chunk_size=16384):
                    if chunk:
                        f.write(chunk)
                        
        except requests.exceptions.Timeout:
            raise Exception("The TTD server is responding too slowly (timed out after 120 seconds). Please try again later.")
        except Exception as e:
            raise Exception(str(e))


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
    use_ttd: bool = Query(True, description="Fetch from TTD Ebooks")
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
        
    return {"results": results, "total": len(results)}


@app.get("/download-book")
def download_book(book_page_url: str = Query(...), title: str = Query(...), source: str = Query("SVK")):
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
    filename = f"{safe_title}.pdf"
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
        print(f"Download error: {e}")
        return {"status": "error", "message": str(e)}
    

@app.post("/reset-app")
def reset_application():
    try:
        # Clear out any downloaded temporary files in the local downloads directory if desired
        downloads_dir = "downloads"
        if os.path.exists(downloads_dir):
            for filename in os.listdir(downloads_dir):
                file_path = os.path.join(downloads_dir, filename)
                if os.path.isfile(file_path):
                    try:
                        os.unlink(file_path)
                    except Exception:
                        pass
        return {"status": "success", "message": "App reset successfully."}
    except Exception as e:
        return {"status": "error", "message": str(e)}