import os
import time
import logging
import re
import requests
from datetime import datetime, timedelta
import pdfplumber

# ================== CONFIG ==================
BASE_FOLDER = "transcripts"
BATCH_DAYS = 10          # Smaller batch = safer
DELAY = 3.0              # Slower = less chance of block
RESUME_FILE = os.path.join(BASE_FOLDER, "last_processed_date.txt")
# ===========================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.FileHandler(os.path.join(BASE_FOLDER, "run_log.txt"), encoding='utf-8', mode='a'),
              logging.StreamHandler()]
)

logging.info("=== Safe & Simple Transcript Downloader ===")

# Resume
start_date_str = "20200101"
if os.path.exists(RESUME_FILE):
    try:
        with open(RESUME_FILE, 'r') as f:
            content = f.read().strip()
            if content.isdigit() and len(content) == 8:
                start_date_str = content
                logging.info(f"Resuming from {start_date_str}")
    except:
        pass

logging.info(f"Starting from {start_date_str}")

# Company Map
company_map = {}
for folder in os.listdir(BASE_FOLDER):
    if os.path.isdir(os.path.join(BASE_FOLDER, folder)):
        match = re.search(r'\((\d{5,6})\)', folder)
        if match:
            company_map[match.group(1)] = os.path.join(BASE_FOLDER, folder)

logging.info(f"Loaded {len(company_map)} companies")

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bseindia.com/"
}
session = requests.Session()

def is_transcript(ann):
    text = (str(ann.get('headline', '')) + " " + str(ann.get('subject', ''))).lower()
    keywords = ["transcript", "earnings call", "concall", "conference call", "investor meet transcript"]
    return any(kw in text for kw in keywords)

def extract_text(pdf_bytes):
    text = ""
    try:
        with pdfplumber.open(pdf_bytes) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"
    except:
        text = "Extraction failed"
    return text

def save_file(ann):
    try:
        scrip = str(ann.get('scrip_code', '') or ann.get('SC_CODE', ''))
        if scrip not in company_map:
            return False

        company_folder = company_map[scrip]
        cat_folder = os.path.join(company_folder, "Transcripts")
        os.makedirs(cat_folder, exist_ok=True)

        company_name = str(ann.get('company_name', 'Unknown'))
        date_str = str(ann.get('dt', ''))
        headline = str(ann.get('headline', ''))

        pdf_url = ann.get('attachment') or ann.get('pdf_link') or ann.get('ATTACHMENT')
        if not pdf_url or not str(pdf_url).startswith('http'):
            return False

        resp = session.get(pdf_url, headers=headers, timeout=45)
        if resp.status_code != 200:
            return False

        text = extract_text(resp.content)

        clean_headline = re.sub(r'[^\w\s-]', '_', headline)[:80]
        filename = f"{company_name}_{date_str}_Transcript_{clean_headline}.txt"
        path = os.path.join(cat_folder, filename)

        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Company: {company_name}\nDate: {date_str}\nHeadline: {headline}\n\n")
            f.write(text)

        logging.info(f"✓ SAVED: {company_name} | {date_str}")
        return True
    except Exception as e:
        logging.error(f"Save error: {str(e)}")
        return False

# Main Loop
current = datetime.strptime(start_date_str, "%Y%m%d")
end_dt = datetime.strptime("20260524", "%Y%m%d")
total = 0

while current <= end_dt:
    batch_end = min(current + timedelta(days=BATCH_DAYS - 1), end_dt)
    start_str = current.strftime("%Y%m%d")
    end_str = batch_end.strftime("%Y%m%d")

    logging.info(f"\n{'='*85}")
    logging.info(f"BATCH: {start_str} → {end_str}")

    try:
        url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
        params = {
            "pageno": 1,
            "strCat": "-1",
            "strPrevDate": start_str,
            "strToDate": end_str,
            "strScrip": "",
            "strSearch": "P"
        }

        resp = session.get(url, params=params, headers=headers, timeout=30)
        
        if resp.status_code == 200:
            anns = resp.json().get('Table', [])
            logging.info(f"API returned {len(anns)} announcements")

            count = 0
            for ann in anns:
                if is_transcript(ann):
                    if save_file(ann):
                        count += 1
                        total += 1
                    time.sleep(DELAY)
            logging.info(f"Saved {count} transcripts")
        else:
            logging.warning(f"API status: {resp.status_code}")
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        break

    # Update resume
    with open(RESUME_FILE, 'w') as f:
        f.write(batch_end.strftime("%Y%m%d"))

    current = batch_end + timedelta(days=1)
    time.sleep(4)

logging.info(f"\n🎉 FINISHED! Total saved: {total}")
