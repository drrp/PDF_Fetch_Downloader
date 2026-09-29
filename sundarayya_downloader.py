import os
import re
import urllib.parse
import requests
from bs4 import BeautifulSoup

# Configuration
BASE_URL = "https://sundarayya.org"
INDEX_URL = "https://sundarayya.org/telugu-uploaded-books"
DOWNLOAD_DIR = "sundarayya_books"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def sanitize_filename(filename):
    """Clean string to create a valid file name."""
    return re.sub(r'[\\/*?:"<>|]', "", filename).strip()

def download_pdf(pdf_url, custom_name=""):
    """Downloads a PDF file from a direct link."""
    parsed_url = urllib.parse.unquote(pdf_url)
    default_name = parsed_url.split('/')[-1]
    
    if custom_name:
        filename = f"{sanitize_filename(custom_name)}.pdf"
    else:
        filename = sanitize_filename(default_name)

    filepath = os.path.join(DOWNLOAD_DIR, filename)

    if os.path.exists(filepath):
        print(f"Skipping (Already exists): {filename}")
        return

    print(f"Downloading: {filename} ...")
    try:
        response = requests.get(pdf_url, headers=HEADERS, stream=True, timeout=20)
        if response.status_code == 200:
            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"Saved -> {filepath}")
        else:
            print(f"Failed (HTTP {response.status_code}): {pdf_url}")
    except Exception as e:
        print(f"Error downloading {pdf_url}: {e}")

def process_page(page_number, search_keyword=""):
    """Fetches a specific index page and retrieves PDF book links."""
    params = {"title": search_keyword, "page": page_number}
    print(f"\n--- Checking Index Page {page_number} ---")
    
    try:
        res = requests.get(INDEX_URL, params=params, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"Could not load page {page_number}")
            return False

        soup = BeautifulSoup(res.text, "html.parser")
        
        # Look for direct PDF links or node detail links
        links = soup.find_all("a", href=True)
        pdf_found = 0

        for link in links:
            href = link["href"]
            title = link.get_text(strip=True)

            # Option A: Direct PDF links found on page
            if ".pdf" in href.lower():
                full_pdf_url = urllib.parse.urljoin(BASE_URL, href)
                download_pdf(full_pdf_url, custom_name=title)
                pdf_found += 1
            
            # Option B: Detail page links containing the actual PDF download link
            elif "/content/" in href or "/node/" in href:
                detail_url = urllib.parse.urljoin(BASE_URL, href)
                try:
                    detail_res = requests.get(detail_url, headers=HEADERS, timeout=10)
                    detail_soup = BeautifulSoup(detail_res.text, "html.parser")
                    for pdf_link in detail_soup.find_all("a", href=True):
                        if ".pdf" in pdf_link["href"].lower():
                            target_pdf = urllib.parse.urljoin(BASE_URL, pdf_link["href"])
                            download_pdf(target_pdf, custom_name=title)
                            pdf_found += 1
                            break
                except Exception:
                    continue

        if pdf_found == 0:
            print(f"No PDF files found on index page {page_number}.")
        return True

    except Exception as e:
        print(f"Error reading page {page_number}: {e}")
        return False

# --- Main Execution Loop ---
if __name__ == "__main__":
    # Settings:
    # Set START_PAGE and END_PAGE to crawl specific page ranges (0 to 140)
    START_PAGE = 135
    END_PAGE = 140
    SEARCH_QUERY = ""  # Leave empty for all books, or filter by keyword e.g. "చరిత్ర"

    for p in range(START_PAGE, END_PAGE + 1):
        process_page(p, search_keyword=SEARCH_QUERY)