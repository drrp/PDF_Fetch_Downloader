import os
import re
import urllib.parse
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://sundarayya.org"
INDEX_URL = f"{BASE_URL}/telugu-uploaded-books"
DOWNLOAD_DIR = "sundarayya_pdf_collection"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
}

# Strict blacklist for any non-book system URLs
EXCLUDE_PATHS = {
    "/", "/about-us", "/policies", "/who-is-who", "/tribute-to-PS",
    "/library", "/books", "/english-books", "/urdu-books", "/study-circle-books",
    "/audiobooks", "/photos", "/videos", "/sundarayya-museum", "/donations",
    "/gachibowlifacilities", "/baghlingampallifacilities", "/sundarayya-works",
    "/events", "/news-and-reports", "/publication", "/timeline", "/contact-us",
    "/search_catalogue.php", "/sundarayya-centenary-celebrations-may-2013-1"
}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def sanitize_filename(name):
    """Removes invalid file path characters."""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def derive_pdf_url_from_img(img_src):
    """Reconstructs PDF URL from thumbnail screenshot URL."""
    full_img_url = urllib.parse.urljoin(BASE_URL, img_src)
    
    if "Screenshot_" in full_img_url and "pdf.png" in full_img_url:
        clean_url = re.sub(r'Screenshot_\d{4}-\d{2}-\d{2}%20', '', full_img_url)
        clean_url = re.sub(r'Screenshot_\d{4}-\d{2}-\d{2}\s+', '', clean_url)
        clean_url = clean_url.replace('%20pdf.png', '.pdf').replace(' pdf.png', '.pdf')
        return clean_url
    
    return None

def process_book_detail_page(detail_url, book_title):
    print(f"\nProcessing Book: {book_title}")
    print(f"Detail Page URL: {detail_url}")

    try:
        res = requests.get(detail_url, headers=HEADERS, timeout=12)
        if res.status_code != 200:
            print("  └─ Could not load detail page.")
            return

        soup = BeautifulSoup(res.text, "html.parser")
        target_pdf_url = None

        # Method 1: Check explicit download link ending in .pdf
        for a_tag in soup.find_all("a", href=True):
            href = a_tag['href']
            if href.endswith(".pdf") and "/sites/default/files/" in href:
                target_pdf_url = urllib.parse.urljoin(BASE_URL, href)
                break

        # Method 2: Reconstruct from screenshot thumbnail source
        if not target_pdf_url:
            for img in soup.find_all("img", src=True):
                derived = derive_pdf_url_from_img(img['src'])
                if derived:
                    target_pdf_url = derived
                    break

        if not target_pdf_url:
            print("  └─ Error: PDF URL could not be resolved.")
            return

        print(f"  └─ Found PDF Target: {target_pdf_url}")

        filename = f"{sanitize_filename(book_title)}.pdf"
        filepath = os.path.join(DOWNLOAD_DIR, filename)

        if os.path.exists(filepath):
            print("  └─ File already exists. Skipping.")
            return

        pdf_res = requests.get(target_pdf_url, headers=HEADERS, stream=True, timeout=30)
        if pdf_res.status_code == 200:
            with open(filepath, "wb") as f:
                for chunk in pdf_res.iter_content(chunk_size=16384):
                    f.write(chunk)
            print(f"  └─ Successfully saved: {filepath}")
        else:
            print(f"  └─ Download failed (HTTP status {pdf_res.status_code})")

    except Exception as err:
        print(f"  └─ Error: {err}")

def scrape_index_page(page_num):
    print(f"\n================ Fetching Index Page {page_num} ================")
    params = {"title": "", "page": page_num}
    
    response = requests.get(INDEX_URL, params=params, headers=HEADERS)
    if response.status_code != 200:
        print(f"Failed to load index page {page_num}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    
    # Target only elements inside the primary catalogue view container
    catalog_view = soup.find("div", class_=re.compile(r"view-display-id-page|view-content"))
    
    if not catalog_view:
        print("Could not find catalog view container on page.")
        return

    book_anchors = catalog_view.find_all("a", href=True)
    processed_urls = set()

    for link in book_anchors:
        href = link['href']
        title = link.get_text(strip=True)

        # Filter out system and navigation links
        if not href or href.startswith("http") or href.startswith("#") or "page=" in href:
            continue
        if href in EXCLUDE_PATHS or href.startswith("/themes/") or href.startswith("/sites/"):
            continue

        detail_url = urllib.parse.urljoin(BASE_URL, href)
        
        if detail_url not in processed_urls and title and len(title) > 1:
            processed_urls.add(detail_url)
            process_book_detail_page(detail_url, title)

if __name__ == "__main__":
    # Specify the page numbers to download (0 = Page 1, 139 = Page 140)
    START_PAGE = 0
    END_PAGE = 2

    for current_page in range(START_PAGE, END_PAGE + 1):
        scrape_index_page(current_page)