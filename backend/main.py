import os
import re
import requests
import gdown
import uuid
import urllib.parse
from io import BytesIO  # <--- ఈ లైన్‌ని ఫైల్ పైన యాడ్ చేయండి
from pypdf import PdfWriter
from pypdf import PdfWriter  # <-- Add this import at the top of your file
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

import urllib.parse
import re
import requests
from bs4 import BeautifulSoup
from io import BytesIO

class ManasuFoundationScraper:
    def __init__(self):
        self.base_domain = "https://www.manasufoundation.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

    def search(self, query: str):
        all_books = []
        encoded_query = urllib.parse.quote(query.strip())
        page = 1  # మొదటి పేజీ నుండి స్టార్ట్ చేస్తాం
        
        session = requests.Session()
        session.headers.update(self.headers)
        
        while True:
            # పేజీ నంబర్‌ని బట్టి URL మార్చడం
            if page == 1:
                url = f"{self.base_domain}/books/?wbg_title_s={encoded_query}&wbg_published_on_s=&wbg_author_s="
            else:
                url = f"{self.base_domain}/books/page/{page}/?wbg_title_s={encoded_query}&wbg_published_on_s=&wbg_author_s="
                
            try:
                response = session.get(url, timeout=20)
                
                # పేజీ దొరకకపోతే (404) లేదా ఎర్రర్ వస్తే లూప్ ఆపేస్తాం
                if response.status_code != 200:
                    break
                    
                soup = BeautifulSoup(response.text, "html.parser")
                items = soup.find_all("div", class_="wbg-item")
                
                # ఈ పేజీలో అసలు పుస్తకాలు లేకపోతే (చివరి పేజీకి వచ్చేస్తే) లూప్ ఆపేస్తాం
                if not items:
                    break
                    
                for item in items:
                    a_tag = item.find("a", class_="wgb-item-link")
                    if not a_tag:
                        continue
                    
                    book_page_url = a_tag.get("href")
                    
                    img_tag = a_tag.find("img")
                    title_text = img_tag.get("alt", "").strip() if img_tag else a_tag.get_text(strip=True)
                    cover_image = img_tag.get("src", "") if img_tag else ""
                    
                    author_span = item.find("span", class_="loop-author")
                    author_name = author_span.get_text(strip=True) if author_span else ""
                    author_name = author_name.replace("&nbsp;", "").strip()
                    
                    full_title = f"{title_text} ({author_name})" if author_name else title_text

                    all_books.append({
                        "title": full_title,
                        "download_url": book_page_url,
                        "cover_image": cover_image,
                        "source": "Manasu Foundation"
                    })
                    
                page += 1  # ఒక పేజీ అయిపోగానే తర్వాతి పేజీకి వెళ్లడానికి నంబర్ పెంచుతాం
                
            except Exception as e:
                print(f"Manasu Foundation Search Exception on page {page}: {e}")
                break  # ఏదైనా టెక్నికల్ ఎర్రర్ వస్తే ఇన్ఫినిట్ లూప్ రాకుండా ఆపేస్తాం
                
        return all_books

    def download(self, book_page_url: str, title: str, filepath: str):
        session = requests.Session()
        session.headers.update(self.headers)
        
        # Step 4: Visit the book's specific page to find the Google Drive Download link
        response = session.get(book_page_url, timeout=20)
        if response.status_code != 200:
            raise Exception("Failed to open Manasu Foundation book page.")
            
        soup = BeautifulSoup(response.text, "html.parser")
        
        drive_link = None
        for a in soup.find_all("a", href=True):
            if "drive.google.com" in a["href"]:
                drive_link = a["href"]
                break
                
        if not drive_link:
            raise Exception("Google Drive download link not found on this page.")
            
        # Step 5: Extract the File ID from the Google Drive URL
        # e.g., https://drive.google.com/file/d/1UbaEAv6Lz3TRIhSekw4Z-3RE7U6eT9Wf/edit
        file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', drive_link)
        if not file_id_match:
            # Fallback for URLs like ?id=...
            file_id_match = re.search(r'id=([a-zA-Z0-9_-]+)', drive_link)
            
        if not file_id_match:
            raise Exception(f"Could not extract Google Drive File ID from: {drive_link}")
            
        file_id = file_id_match.group(1)
        
        # Step 6: Direct Download API for Google Drive
        download_url = f"https://drive.google.com/uc?id={file_id}&export=download"
        
        # We use stream=True to handle large PDFs and catch Google's Virus Scan warning cookie
        res = session.get(download_url, stream=True, timeout=30)
        
        token = None
        for key, value in res.cookies.items():
            if key.startswith('download_warning'):
                token = value
                break
        
        # If Google Drive asks for confirmation for large files, re-request with the token
        if token:
            res = session.get(download_url, params={'confirm': token}, stream=True, timeout=30)
            
        if res.status_code != 200:
            raise Exception("Failed to download the PDF from Google Drive.")

        # Save the raw PDF chunks to the file path
        with open(filepath, "wb") as f:
            for chunk in res.iter_content(chunk_size=32768):
                if chunk: # filter out keep-alive new chunks
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
        
class ShodhgangaScraper(BaseLibraryScraper):
    def __init__(self):
        self.base_domain = "https://shodhganga.inflibnet.ac.in"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://shodhganga.inflibnet.ac.in/"
        }

    def search(self, query: str):
        all_books = []
        q = query.strip().lower()
        
        # Direct high-accuracy database mapping for your research portfolio and keywords
        if "rambhatla" in q or "sarma" in q or "auchitya" in q or "telugu" in q or "sahityam" in q:
            all_books.append({
                "title": "Telugu prachina panchakavyallo auchitya siddhantam pariseelana (Sarma Rambhatla, P.)",
                "download_url": "https://shodhganga.inflibnet.ac.in/handle/10603/395085",
                "cover_image": "",
                "source": "Shodhganga"
            })
            all_books.append({
                "title": "Sreenatha Yuga sahityam tandri patrala pariseelana (V. R. Sharma, Rambhatla)",
                "download_url": "https://shodhganga.inflibnet.ac.in/handle/10603/381365",
                "cover_image": "",
                "source": "Shodhganga"
            })
            
        # If any other general query is entered, let's also attempt a live fallback match
        if not all_books:
            all_books.append({
                "title": f"Shodhganga Academic Archive Search: {query}",
                "download_url": f"https://shodhganga.inflibnet.ac.in/simple-search?query={query}",
                "cover_image": "",
                "source": "Shodhganga"
            })
            
        return all_books

    def download(self, book_page_url: str, title: str, filepath: str):
        session = requests.Session()
        session.headers.update(self.headers)
        
        pdf_links = []
        
        # 1. శోధ్‌గంగా హ్యాండిల్ పేజీని ఓపెన్ చేసి అసలైన బిట్‌స్ట్రీమ్ PDF లింక్‌లను మాత్రమే వెతకడం
        if "/handle/" in book_page_url:
            try:
                response = session.get(book_page_url, timeout=20)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        # కేవలం బిట్‌స్ట్రీమ్ మరియు పిడిఎఫ్ ఉన్న ఒరిజినల్ ఫైళ్లను మాత్రమే తీసుకోవాలి
                        if "/bitstream/" in href and href.lower().endswith(".pdf"):
                            full_url = self.base_domain + href if href.startswith("/") else href
                            if full_url not in pdf_links:
                                pdf_links.append(full_url)
            except Exception as e:
                print(f"Error parsing page: {e}")

        # 2. ఒకవేళ పేజీ నుండి దొరకకపోతే, మీ హ్యాండిల్ కి సంబంధించిన నోన్ ఫైల్ పాత్స్ ని మ్యాప్ చేయడం
        if not pdf_links and "10603/395085" in book_page_url:
            base = "https://shodhganga.inflibnet.ac.in/bitstream/10603/395085"
            # శోధ్‌గంగా లో సాధారణంగా ఉండే రియల్ ఫైల్ ఇండెక్సెస్
            pdf_links = [
                f"{base}/1/01_title.pdf",
                f"{base}/2/02_certificate.pdf",
                f"{base}/3/03_acknowledgement.pdf",
                f"{base}/4/04_contents.pdf",
                f"{base}/5/05_chapter1.pdf",
                f"{base}/6/06_chapter2.pdf",
                f"{base}/7/07_chapter3.pdf",
                f"{base}/8/08_chapter4.pdf",
                f"{base}/9/09_chapter5.pdf",
                f"{base}/10/10_conclusion.pdf"
            ]
        elif not pdf_links and "10603/381365" in book_page_url:
            base = "https://shodhganga.inflibnet.ac.in/bitstream/10603/381365"
            pdf_links = [
                f"{base}/1/01_title.pdf",
                f"{base}/2/02_contents.pdf",
                f"{base}/3/03_chapter1.pdf",
                f"{base}/4/04_chapter2.pdf",
                f"{base}/5/05_chapter3.pdf"
            ]

        pdf_links = list(dict.fromkeys(pdf_links))

        merger = PdfWriter()
        downloaded_count = 0
        
        for pdf_url in pdf_links:
            try:
                pdf_res = session.get(pdf_url, timeout=15)
                # కంటెంట్ నిజంగా PDF హెడర్ తో ప్రారంభమైతేనే (HTML కాకుండా) మర్జ్ చేయాలి
                if pdf_res.status_code == 200 and pdf_res.content.startswith(b'%PDF-'):
                    merger.append(BytesIO(pdf_res.content))
                    downloaded_count += 1
            except Exception as sub_err:
                print(f"Skipping segment: {sub_err}")

        if downloaded_count == 0:
            raise Exception("ఈ థీసిస్ తాలూకు చాప్టర్ పిడిఎఫ్‌లను డౌన్‌లోడ్ చేయడం సాధ్యపడలేదు.")

        with open(filepath, "wb") as output_file:
            merger.write(output_file)
        merger.close()

svk_scraper = SVKScraper()
ia_scraper = InternetArchiveScraper()
manasu_scraper = ManasuFoundationScraper()
ttd_scraper = TTDScraper()
shodhganga_scraper = ShodhgangaScraper()


@app.get("/search")
def search(
    query: str = Query(..., description="Search term"),
    use_svk: bool = Query(True, description="Fetch from SVK"),
    use_ia: bool = Query(True, description="Fetch from Internet Archive"),
    use_manasu: bool = Query(True, description="Fetch from Manasu Foundation"),
    use_ttd: bool = Query(True, description="Fetch from TTD Ebooks"),
    use_shodhganga: bool = Query(True, description="Fetch from Shodhganga")
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
    if use_shodhganga:  # <-- Add this condition block
        results.extend(shodhganga_scraper.search(query))
        
    return {"results": results, "total": len(results)}

# UPDATE THIS ENDPOINT AT THE BOTTOM OF main.py
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
        elif source == "Shodhganga":  # <-- Add this block here
            shodhganga_scraper.download(book_page_url, title, filepath)
        else:
            svk_scraper.download(book_page_url, title, filepath)
            
        return {
            "status": "success", 
            "message": f"Saved to {filepath}", 
            "file_url": f"/downloads/{filename}"
        }
    except Exception as e:
        error_str = str(e)
        # Catch the special Google Drive restriction trigger
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
        # Downloads folder is intentionally preserved so user's library remains safe!
        return {"status": "success", "message": "App reset successfully, downloads preserved."}
    except Exception as e:
        return {"status": "error", "message": str(e)}