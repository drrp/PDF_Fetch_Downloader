import requests
import re
from bs4 import BeautifulSoup
import urllib.parse
import gdown
from io import BytesIO
from pypdf import PdfWriter
import concurrent.futures

class ManasuFoundationScraper:
    def __init__(self):
        self.base_domain = "https://www.manasufoundation.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://www.manasufoundation.com/books/",
            "X-Requested-With": "XMLHttpRequest"
        }

    def _fetch_pages(self, base_url: str, params: dict, session: requests.Session):
        books = []
        page = 1
        
        while True:
            # WP Grid Builder పేజినేషన్ ఫార్మాట్
            if page == 1:
                url = base_url
            else:
                if "?" in base_url:
                    parts = base_url.split("?")
                    url = f"{parts[0]}page/{page}/?{parts[1]}"
                else:
                    url = f"{base_url}page/{page}/"
            
            try:
                res = session.get(url, params=params, timeout=15)
                if res.status_code != 200: 
                    break
                    
                soup = BeautifulSoup(res.text, "html.parser")
                page_books = []
                
                # WP Grid Builder ఐటమ్స్
                for item in soup.find_all("div", class_="wbg-item"):
                    a_tag = item.find("a", class_="wgb-item-link")
                    if not a_tag: continue
                    book_url = a_tag.get("href", "")
                    img_tag = a_tag.find("img")
                    title = img_tag.get("alt", "").strip() if img_tag else a_tag.get_text(strip=True)
                    cover = img_tag.get("src", "") if img_tag else ""
                    author_span = item.find("span", class_="loop-author")
                    author = author_span.get_text(strip=True) if author_span else ""
                    author = author.replace("&nbsp;", "").strip()
                    
                    full_title = f"{title} ({author})" if author else title
                    page_books.append({"title": full_title, "download_url": book_url, "cover_image": cover, "source": "Manasu Foundation"})
                
                if not page_books: 
                    break
                    
                books.extend(page_books)
                page += 1
            except Exception as e:
                break
                
        return books

    def search(self, query: str):
        all_books = []
        cleaned_query = query.strip()
        seen_urls = set()
        
        session = requests.Session()
        session.headers.update(self.headers)
        
        def add_books(new_books):
            for b in new_books:
                if b["download_url"] not in seen_urls:
                    seen_urls.add(b["download_url"])
                    all_books.append(b)

        # 1. టైటిల్ ద్వారా సెర్చ్
        add_books(self._fetch_pages(
            f"{self.base_domain}/books/", 
            {"wbg_title_s": cleaned_query, "wbg_published_on_s": "", "wbg_author_s": ""}, 
            session
        ))

        # 2. ఆథర్ పేరు ద్వారా నేరుగా సెర్చ్ (WP Grid Builder కి తగినట్లు పారామీటర్స్ పంపడం)
        add_books(self._fetch_pages(
            f"{self.base_domain}/books/", 
            {"wbg_title_s": "", "wbg_published_on_s": "", "wbg_author_s": cleaned_query}, 
            session
        ))

        # 3. స్మార్ట్ ఆథర్ ఫైండర్ (డ్రాప్‌డౌన్ లేదా హోమ్‌పేజీ సోర్స్ నుండి పాక్షిక/పూర్తి పేర్లను ಮ్యాప్ చేయడం)
        try:
            res_home = session.get(f"{self.base_domain}/books/", timeout=15)
            if res_home.status_code == 200:
                decoded_html = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), res_home.text)
                
                matched_authors = set()
                # అట్రిబ్యూట్స్ మరియు ఆప్షన్స్ లో మన క్వెరీ ఉన్న పేర్ల కోసం వెతకడం
                patterns = [
                    r'value=["\']([^"\']*?' + re.escape(cleaned_query) + r'[^"\']*?)["\']',
                    r'data-value=["\']([^"\']*?' + re.escape(cleaned_query) + r'[^"\']*?)["\']',
                    r'>(?:[^<]*?)(?:' + re.escape(cleaned_query) + r')(?:[^<]*?)<'
                ]
                
                for pat in patterns:
                    found = re.findall(pat, decoded_html, re.IGNORECASE)
                    for f in found:
                        f_clean = f.strip()
                        if f_clean and len(f_clean) < 50 and not any(c in f_clean for c in ['<', '>', '{', '}', '[', ']', ';', '/*']):
                            matched_authors.add(f_clean)
                
                # దొరికిన ప్రతి ఆథర్ పేరుతో సెర్చ్ చేయడం
                for author in matched_authors:
                    add_books(self._fetch_pages(
                        f"{self.base_domain}/books/", 
                        {"wbg_title_s": "", "wbg_published_on_s": "", "wbg_author_s": author}, 
                        session
                    ))
        except Exception as e:
            print("Smart Author Matcher Error:", e)

        # 4. చివరిగా గ్లోబల్ వర్డ్‌ప్రెస్ సెర్చ్ ఫాల్‌బ్యాక్
        if not all_books:
            add_books(self._fetch_pages(
                self.base_domain, 
                {"s": cleaned_query}, 
                session
            ))

        return all_books

    def download(self, book_page_url: str, title: str, filepath: str):
        session = requests.Session()
        session.headers.update(self.headers)
        
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
            
        file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', drive_link)
        if not file_id_match:
            file_id_match = re.search(r'id=([a-zA-Z0-9_-]+)', drive_link)
            
        if not file_id_match:
            raise Exception(f"Could not extract Google Drive File ID from: {drive_link}")
            
        file_id = file_id_match.group(1)
        download_url = f"https://drive.google.com/uc?id={file_id}&export=download"
        
        res = session.get(download_url, stream=True, timeout=30)
        
        token = None
        for key, value in res.cookies.items():
            if key.startswith('download_warning'):
                token = value
                break
        
        if token:
            res = session.get(download_url, params={'confirm': token}, stream=True, timeout=30)
            
        if res.status_code != 200:
            raise Exception("Failed to download the PDF from Google Drive.")

        with open(filepath, "wb") as f:
            for chunk in res.iter_content(chunk_size=32768):
                if chunk: 
                    f.write(chunk)