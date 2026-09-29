import os
import time
from PIL import Image
import img2pdf
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

BOOK_ID = "26380"
VIEWER_URL = f"https://sathyakam.com/pdfImageBook.php?bId={BOOK_ID}"
OUTPUT_PDF = f"book_{BOOK_ID}.pdf"
TEMP_DIR = "downloaded_pages"

os.makedirs(TEMP_DIR, exist_ok=True)

options = webdriver.ChromeOptions()
# Comment out headless mode if you need to debug visual loading in real-time
# options.add_argument("--headless")
options.add_argument("--window-size=1600,1200")
options.add_argument("--disable-gpu")

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

try:
    print(f"Loading web page: {VIEWER_URL}")
    driver.get(VIEWER_URL)

    # 1. Wait up to 20 seconds for the viewer iframe or book container to appear
    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    
    # Give full JS scripts time to complete initialization
    time.sleep(8)

    # Switch to inner viewer iframe if one exists
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    if iframes:
        print(f"Found {len(iframes)} iframe(s), switching context...")
        driver.switch_to.frame(iframes[0])
        time.sleep(3)

    image_files = []
    page_num = 1

    # Loop through book pages
    while True:
        image_path = os.path.join(TEMP_DIR, f"page_{page_num:04d}.png")
        
        # Locate main reader container element
        reader_elements = driver.find_elements(By.CSS_SELECTOR, "canvas, #magazine, .page, #viewer, img")
        
        if reader_elements:
            # Capture screenshot of the specific reader element rather than the whole browser
            target_el = reader_elements[0]
            target_el.screenshot(image_path)
        else:
            driver.save_screenshot(image_path)

        image_files.append(image_path)
        print(f"Captured page {page_num}")

        # Attempt to click Next button controls
        next_btn = driver.find_elements(By.XPATH, "//a[contains(@class, 'next') or contains(@id, 'next') or contains(@title, 'Next')]")
        
        if next_btn and next_btn[0].is_displayed():
            next_btn[0].click()
            time.sleep(3)  # Wait for page turn transition
            page_num += 1
        else:
            # Try right arrow key navigation
            from selenium.webdriver.common.keys import Keys
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.RIGHT)
            time.sleep(3)
            
            # Stop if no new pages are rendering
            break

finally:
    driver.quit()

# Assemble into final PDF
if image_files:
    print("\nMerging captured pages into a single PDF...")
    with open(OUTPUT_PDF, "wb") as f:
        f.write(img2pdf.convert(image_files))
    print(f"Success! Saved to {os.path.abspath(OUTPUT_PDF)}")