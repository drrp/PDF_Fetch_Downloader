import os
import re
import urllib.parse
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://sundarayya.org"
SEARCH_ENDPOINT = f"{BASE_URL}/books"
DOWNLOAD_DIR = "sundarayya_search_results"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def sanitize_filename(name):
    """Clean title to create a valid file path."""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def extract_pdf_from_detail_page(detail_url):
    """
    Parses a detail page completely to find direct anchor links,
    embedded objects, iframes, or scripts pointing to PDF files.
    """
    try:
        # Use short connection and read timeouts
        res = requests.get(detail_url, headers=HEADERS, timeout=(5, 12))
        if res.status_code != 200:
            return None

        soup = BeautifulSoup(res.text, "html.parser")

        # 1. Look for explicit <a> href ending in .pdf
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if ".pdf" in href.lower():
                return urllib.parse.urljoin(BASE_URL, href)

        # 2. Look inside <iframe src="...">, <embed src="...">, or <object data="...">
        for tag in soup.find_all(["iframe", "embed", "object"]):
            src = tag.get("src") or tag.get("data")
            if src and ".pdf" in src.lower():
                return urllib.parse.urljoin(BASE_URL, src)

        # 3. Reconstruct from cover image URL if screenshot naming is used
        for img in soup.find_all("img", src=True):
            src = img["src"]
            if "Screenshot_" in src and "pdf.png" in src:
                clean = re.sub(r'Screenshot_\d{4}-\d{2}-\d{2}%20', '', src)
                clean = re.sub(r'Screenshot_\d{4}-\d{2}-\d{2}\s+', '', clean)
                clean = clean.replace('%20pdf.png', '.pdf').replace(' pdf.png', '.pdf')
                return urllib.parse.urljoin(BASE_URL, clean)

        # 4. Search raw JS or inline text for embedded file paths
        matches = re.findall(r'/sites/default/files/[^\s\'"<>]+?\.pdf', res.text, re.IGNORECASE)
        if matches:
            return urllib.parse.urljoin(BASE_URL, matches[0])

    except requests.exceptions.RequestException as e:
        print(f"  └─ Connection error: {e}")
    
    return None

def fetch_and_download_pdf(detail_url, book_title):
    print(f"\n[Book Detail] {book_title}")
    print(f" -> URL: {detail_url}")

    filename = f"{sanitize_filename(book_title)}.pdf"
    filepath = os.path.join(DOWNLOAD_DIR, filename)

    if os.path.exists(filepath):
        print("  └─ File already exists. Skipping.")
        return

    # Extract target PDF URL
    target_pdf_url = extract_pdf_from_detail_page(detail_url)

    if not target_pdf_url:
        print("  └─ Could not locate PDF URL on detail page.")
        return

    print(f"  └─ Target PDF: {target_pdf_url}")

    try:
        pdf_res = requests.get(target_pdf_url, headers=HEADERS, stream=True, timeout=(5, 30))
        if pdf_res.status_code == 200:
            with open(filepath, "wb") as f:
                for chunk in pdf_res.iter_content(chunk_size=16384):
                    f.write(chunk)
            print(f"  └─ Success! Saved to -> {filepath}")
        else:
            print(f"  └─ Download failed (HTTP {pdf_res.status_code})")
    except requests.exceptions.RequestException as err:
        print(f"  └─ Network error during download: {err}")

def search_and_download(keyword, max_pages=1):
    print(f"Searching SVK Books for query: '{keyword}'...")

    for page_num in range(max_pages):
        params = {
            "field_book_title_in_english_value": keyword,
            "page": page_num
        }

        print(f"\n================ Fetching Search Page {page_num + 1} ================")
        try:
            response = requests.get(SEARCH_ENDPOINT, params=params, headers=HEADERS, timeout=(5, 15))
            if response.status_code != 200:
                print(f"Search failed (HTTP {response.status_code})")
                break
        except requests.exceptions.RequestException as err:
            print(f"Search request timeout: {err}")
            break

        soup = BeautifulSoup(response.text, "html.parser")

        books_container = soup.find("div", class_="books-wrap")
        if not books_container:
            print("No results container found.")
            break

        book_cards = books_container.find_all("div", class_=re.compile(r"book-card-margin|col-md-4"))
        if not book_cards:
            print("No result cards found for this page.")
            break

        print(f"Found {len(book_cards)} book entry/entries on page {page_num + 1}.")

        for card in book_cards:
            # Target inner links, ignoring href="#"
            title_links = [
                a for a in card.find_all("a", href=True) 
                if a["href"] != "#" and not a["href"].startswith("javascript")
            ]

            if title_links:
                target_link = title_links[0]
                href = target_link["href"]
                title = target_link.get_text(strip=True)

                if not title and card.find("div", class_="book-title"):
                    title = card.find("div", class_="book-title").get_text(strip=True)

                detail_url = urllib.parse.urljoin(BASE_URL, href)
                fetch_and_download_pdf(detail_url, title)

if __name__ == "__main__":
    SEARCH_KEYWORD = "sahitya"  # Query term
    PAGES_TO_FETCH = 2          # Number of result pages to process

    search_and_download(SEARCH_KEYWORD, max_pages=PAGES_TO_FETCH)