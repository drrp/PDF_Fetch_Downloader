import requests
import re
from bs4 import BeautifulSoup

# --- బేస్ క్లాస్‌ని ఇక్కడ చేర్చండి ---
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