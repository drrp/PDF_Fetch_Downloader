import requests
from bs4 import BeautifulSoup
import concurrent.futures
import re

# --- బేస్ క్లాస్‌ని ఇక్కడ డిఫైన్ చేయండి ---
class BaseLibraryScraper:
    def search(self, query: str):
        raise NotImplementedError
    
    def download(self, book_url: str, title: str, filepath: str):
        raise NotImplementedError

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
        seen_urls = set()
        
        # టైటిల్ మరియు ఆథర్ సెర్చ్ కీలు
        search_keys = ["search", "author"]
        
        # నెట్‌వర్క్ రిక్వెస్ట్ చేసే చిన్న ఫంక్షన్
        def fetch_data(key):
            try:
                params = {"value": query, "key": key}
                # ఇక్కడ ప్రత్యేకంగా session కాకుండా డైరెక్ట్ requests.get వాడటం త్రెడింగ్‌లో సురక్షితం
                response = requests.get(url, params=params, headers=self.headers, timeout=60)
                if response.status_code == 200:
                    return response.text
            except Exception as e:
                print(f"TTD Ebooks Search Error for key '{key}': {e}")
            return None

        # --- Multi-threading లాజిక్ ఇక్కడే మొదలవుతుంది ---
        # రెండు రిక్వెస్ట్‌లను ఒకేసారి పంపి డేటాను లాగుతుంది!
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            html_responses = list(executor.map(fetch_data, search_keys))
            
        # వచ్చిన రిజల్ట్స్ ని పార్స్ చేయడం (పాత లాజిక్)
        for html in html_responses:
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            results_list = soup.find("div", class_="results-list")
            if not results_list:
                continue

            cards = results_list.find_all("div", class_="stripc")
            
            for card in cards:
                link_tag = card.select_one(".stripc-part1 a")
                if not link_tag:
                    continue

                href = link_tag.get("href")
                if not href:
                    continue

                # డూప్లికేట్స్ తీసెయ్యడం
                detail_url = f"{self.base_domain}/{href}" if not href.startswith("http") else href
                if detail_url in seen_urls:
                    continue
                seen_urls.add(detail_url)

                title_tag = card.select_one(".stripc-part2 h6")
                title_text = title_tag.get_text(strip=True) if title_tag else "Unknown Title"
                
                author_tag = card.select_one(".stripc-part2 p")
                if author_tag:
                    author_text = author_tag.get_text(strip=True).replace("By :", "").strip()
                    if author_text:
                        title_text = f"{title_text} ({author_text})"

                cover_url = ""
                img_tag = link_tag.find("img")
                if img_tag and img_tag.get("src"):
                    src = img_tag.get("src")
                    cover_url = f"{self.base_domain}/{src}" if not src.startswith("http") else src

                all_books.append({
                    "title": title_text,
                    "download_url": detail_url,
                    "cover_image": cover_url,
                    "source": "TTD Ebooks"
                })
                
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