import requests
import re
from bs4 import BeautifulSoup
import urllib.parse
import gdown
from io import BytesIO
from pypdf import PdfWriter

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