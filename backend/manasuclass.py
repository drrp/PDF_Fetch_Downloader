class ManasuFoundationScraper(BaseLibraryScraper):
    def __init__(self):
        self.base_domain = "https://www.manasufoundation.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
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

    # UPDATE THIS METHOD INSIDE ManasuFoundationScraper class
    def download(self, book_page_url: str, title: str, filepath: str):
        import time
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
        file_id = None
        
        if "drive.google.com" in target_url:
            match = re.search(r"/d/([a-zA-Z0-9_-]+)", target_url)
            if not match:
                match = re.search(r"id=([a-zA-Z0-9_-]+)", target_url)
            
            if match:
                file_id = match.group(1)
                
        if file_id:
            try:
                # 1. Download via gdown
                gdown.download(url=target_url, output=filepath, quiet=False, fuzzy=True)

                time.sleep(0.5)

                if not os.path.exists(filepath):
                    raise Exception("File not created.")

                # 2. Verify PDF magic number securely
                is_pdf = False
                for _ in range(3):
                    try:
                        with open(filepath, 'rb') as f:
                            header = f.read(5)
                            if header == b'%PDF-':
                                is_pdf = True
                        break
                    except PermissionError:
                        time.sleep(0.3)

                if not is_pdf:
                    raise Exception("Downloaded file is not a valid PDF.")

                return  # Success
            except Exception as e:
                # Clean up fake file safely
                if os.path.exists(filepath):
                    for _ in range(3):
                        try:
                            os.remove(filepath)
                            break
                        except PermissionError:
                            time.sleep(0.3)
                
                # Instantly catch the Google login/restriction wall and provide direct Drive link
                view_url = f"https://drive.google.com/file/d/{file_id}/view"
                raise Exception(f"GDRIVE_RESTRICTED|{view_url}")
                
        else:
            # Fallback for standard links
            file_response = requests.get(target_url, stream=True, timeout=30)
            if file_response.status_code == 200:
                with open(filepath, "wb") as f:
                    for chunk in file_response.iter_content(chunk_size=16384):
                        if chunk: f.write(chunk)
                with open(filepath, 'rb') as f:
                    if f.read(5) != b'%PDF-':
                        try:
                            os.remove(filepath)
                        except OSError:
                            pass
                        raise Exception("Downloaded file is not a valid PDF.")
            else:
                raise Exception("Failed to download file from standard link.")