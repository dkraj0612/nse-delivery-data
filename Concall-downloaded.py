import os
import time
import logging
import requests
from datetime import datetime, timedelta
import pandas as pd
import pdfplumber
import re

# ================== CONFIG ==================
START_DATE = "20200101"
END_DATE = "20260524"
BASE_FOLDER = "transcripts"
BATCH_DAYS = 15              # Larger batch = fewer API calls
DELAY = 2.0
# ===========================================

os.makedirs(BASE_FOLDER, exist_ok=True)

log_file = os.path.join(BASE_FOLDER, "run_log.txt")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.FileHandler(log_file, encoding='utf-8', mode='a'),
              logging.StreamHandler()]
)

logging.info("=== Optimized Company-Specific Script Started ===")

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bseindia.com/"
}
session = requests.Session()

# Build company mapping: scrip_code → folder_path
company_map = {}
for folder in os.listdir(BASE_FOLDER):
    if os.path.isdir(os.path.join(BASE_FOLDER, folder)):
        match = re.search(r'\((\d{5,6})\)', folder)
        if match:
            scrip = match.group(1)
            company_map[scrip] = os.path.join(BASE_FOLDER, folder)

logging.info(f"Loaded {len(company_map)} companies")

def is_relevant(ann):
    text = (str(ann.get('headline', '')) + " " + str(ann.get('subject', ''))).lower()
    if any(k in text for k in ["result", "financial", "quarterly", "annual", "audited"]):
        return "Results"
    if any(k in text for k in ["transcript", "earnings call", "concall", "conference call"]):
        return "Transcript"
    return None

def extract_text(pdf_bytes):
    text = ""
    try:
        with pdfplumber.open(pdf_bytes) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    text += f"--- Page {i+1} ---\n{page_text}\n\n"
    except:
        text = "Extraction failed"
    return text

def save_announcement(ann, category):
    scrip = str(ann.get('scrip_code', ''))
    if not scrip or scrip not in company_map:
        return False

    company_folder = company_map[scrip]
    cat_folder = os.path.join(company_folder, category)
    os.makedirs(cat_folder, exist_ok=True)

    company_name = str(ann.get('company_name', 'Unknown'))
    date_str = str(ann.get('dt', ''))
    headline = str(ann.get('headline', ''))

    try:
        pdf_url = ann.get('attachment') or ann.get('pdf_link') or ann.get('ATTACHMENT')
        resp = session.get(pdf_url, headers=headers, timeout=40)
        if resp.status_code != 200:
            return False

        text = extract_text(resp.content)

        clean_headline = "".join(c if c.isalnum() or c in " _-" else "_" for c in headline)[:100]
        filename = f"{company_name}_{date_str}_{category}_{clean_headline}.txt"
        path = os.path.join(cat_folder, filename)

        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Company: {company_name}\nDate: {date_str}\nCategory: {category}\n")
            f.write(f"Headline: {headline}\n")
            f.write("="*80 + "\n\n")
            f.write(text)

        logging.info(f"✓ SAVED: {company_name} | {category} | {date_str}")
        return True
    except Exception as e:
        logging.error(f"Save failed for {company_name}: {str(e)}")
        return False

# ============== Main Loop ==============
current = datetime.strptime(START_DATE, "%Y%m%d")
end_dt = datetime.strptime(END_DATE, "%Y%m%d")
total_saved = 0

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
            "strScrip": "",           # Empty = all companies
            "strSearch": "P"
        }

        resp = session.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code == 200:
            anns = resp.json().get('Table', [])
            logging.info(f"API returned {len(anns)} announcements")

            for ann in anns:
                cat = is_relevant(ann)
                if cat:
                    if save_announcement(ann, cat):
                        total_saved += 1
                    time.sleep(DELAY)
    except Exception as e:
        logging.error(f"Batch error: {str(e)}")

    current = batch_end + timedelta(days=1)
    time.sleep(4)

logging.info(f"\n🎉 FINISHED! Total files saved: {total_saved}")
print("Completed. Check run_log.txt")
