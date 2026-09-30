import requests
from bs4 import BeautifulSoup
import re

class ShodhgangaScraper:
    def __init__(self):
        self.base_domain = "https://shodhganga.inflibnet.ac.in"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

    def search(self, query: str):
        all_books = []
        session = requests.Session()
        session.headers.update(self.headers)
        
        search_url = f"{self.base_domain}/simple-search"
        params = {"query": query}
        
        try:
            response = session.get(search_url, params=params, timeout=20)
            if response.status_code != 200:
                return all_books
                
            soup = BeautifulSoup(response.text, "html.parser")
            items = soup.select("div.artifact-description, tr.ds-table-row")
            
            for item in items:
                link_tag = item.select_one("a[href*='/handle/']")
                if not link_tag:
                    continue
                    
                href = link_tag.get("href")
                detail_url = f"{self.base_domain}{href}" if not href.startswith("http") else href
                
                title_text = link_tag.get_text(strip=True)
                author_tag = item.select_one(".author, .print-metadata")
                author_text = author_tag.get_text(strip=True) if author_tag else ""
                
                full_title = f"{title_text} - {author_text}" if author_text else title_text
                
                all_books.append({
                    "title": full_title,
                    "download_url": detail_url,
                    "cover_image": "",
                    "source": "Shodhganga"
                })
        except Exception as e:
            print(f"Shodhganga Search Error: {e}")
            
        return all_books

    def download(self, book_page_url: str, title: str, filepath: str):
        session = requests.Session()
        session.headers.update(self.headers)
        
        try:
            response = session.get(book_page_url, timeout=20)
            if response.status_code != 200:
                raise Exception("Failed to open Shodhganga thesis page.")
                
            soup = BeautifulSoup(response.text, "html.parser")
            pdf_link = None
            
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/bitstream/" in href and href.lower().endswith(".pdf"):
                    pdf_link = href
                    break
                    
            if not pdf_link:
                for a in soup.find_all("a", href=True):
                    if "download" in a["href"].lower() or "bitstream" in a["href"].lower():
                        pdf_link = a["href"]
                        break
                        
            if not pdf_link:
                raise Exception("PDF download link not found on Shodhganga page.")
                
            if not pdf_link.startswith("http"):
                pdf_link = f"{self.base_domain}{pdf_link}"
                
            pdf_response = session.get(pdf_link, stream=True, timeout=30)
            if pdf_response.status_code == 200:
                with open(filepath, "wb") as f:
                    for chunk in pdf_response.iter_content(chunk_size=32768):
                        if chunk:
                            f.write(chunk)
            else:
                raise Exception("Failed to download Shodhganga PDF stream.")
        except Exception as e:
            raise Exception(f"Shodhganga Download Error: {e}")