import os
import time
import logging
import requests
from datetime import datetime, timedelta
import pandas as pd
import pdfplumber
import re

# ================== CONFIG ==================
START_DATE = "20200101"      # From 2020 onwards
END_DATE = "20260524"
BASE_FOLDER = "transcripts"  # Your existing folder
BATCH_DAYS = 10
DELAY = 2.5
MAX_RETRIES = 3
# ===========================================

# Setup Logging
log_file = os.path.join(BASE_FOLDER, "run_log.txt")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)

logging.info("=== Script Started - Company Specific Mode ===")
logging.info(f"Processing companies from folder: {BASE_FOLDER}")

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bseindia.com/"
}

session = requests.Session()

def get_scrip_code_from_folder(folder_name):
    """Extract BSE code like 500002 from folder name"""
    match = re.search(r'\((\d{5,6})\)', folder_name)
    return match.group(1) if match else None

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
        text = "Could not extract text from PDF"
    return text

def process_ann(ann, category, company_folder):
    for attempt in range(MAX_RETRIES):
        try:
            pdf_url = ann.get('attachment') or ann.get('pdf_link') or ann.get('ATTACHMENT')
            if not pdf_url or not str(pdf_url).startswith('http'):
                return False

            company = str(ann.get('company_name', 'Unknown'))
            date_str = str(ann.get('dt', ''))
            headline = str(ann.get('headline', ''))

            logging.info(f"Downloading: {company} | {date_str}")

            resp = session.get(pdf_url, headers=headers, timeout=40)
            if resp.status_code != 200:
                time.sleep(2)
                continue

            text = extract_text(resp.content)

            cat_folder = os.path.join(company_folder, category)
            os.makedirs(cat_folder, exist_ok=True)

            clean_headline = "".join(c if c.isalnum() or c in " _-" else "_" for c in headline)[:100]
            filename = f"{company}_{date_str}_{category}_{clean_headline}.txt"
            path = os.path.join(cat_folder, filename)

            with open(path, "w", encoding="utf-8") as f:
                f.write(f"Company: {company}\nDate: {date_str}\nCategory: {category}\nHeadline: {headline}\n")
                f.write("="*80 + "\n\n")
                f.write(text)

            logging.info(f"✓ SAVED in {category}: {filename}")
            return True
        except Exception as e:
            logging.error(f"Attempt {attempt+1} failed: {str(e)}")
            time.sleep(3)
    return False

# ============== Get All Company Folders ==============
companies = [f for f in os.listdir(BASE_FOLDER) 
             if os.path.isdir(os.path.join(BASE_FOLDER, f)) and f != "__pycache__"]

logging.info(f"Found {len(companies)} company folders")

# ============== Main Processing ==============
total = 0
current = datetime.strptime(START_DATE, "%Y%m%d")
end_dt = datetime.strptime(END_DATE, "%Y%m%d")

while current <= end_dt:
    batch_end = min(current + timedelta(days=BATCH_DAYS - 1), end_dt)
    start_str = current.strftime("%Y%m%d")
    end_str = batch_end.strftime("%Y%m%d")

    logging.info(f"\n{'='*90}")
    logging.info(f"BATCH: {start_str} → {end_str}")

    for folder in companies:
        scrip_code = get_scrip_code_from_folder(folder)
        if not scrip_code:
            continue

        company_folder = os.path.join(BASE_FOLDER, folder)
        logging.info(f"Processing {folder} (Code: {scrip_code})")

        try:
            url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
            params = {
                "pageno": 1,
                "strCat": "-1",
                "strPrevDate": start_str,
                "strToDate": end_str,
                "strScrip": scrip_code,      # ← Key: Specific company
                "strSearch": "P"
            }

            resp = session.get(url, params=params, headers=headers, timeout=30)
            
            if resp.status_code == 200:
                data = resp.json()
                anns = data.get('Table', [])

                relevant = [dict(a, filtered_category=cat) for a in anns if (cat := is_relevant(a))]

                if relevant:
                    for ann in relevant:
                        process_ann(ann, ann['filtered_category'], company_folder)
                        time.sleep(DELAY)
                    total += len(relevant)
        except Exception as e:
            logging.error(f"Error processing {folder}: {str(e)}")

    current = batch_end + timedelta(days=1)
    time.sleep(4)

logging.info(f"\n🎉 FINISHED! Total announcements processed: {total}")
print("Script completed. Check run_log.txt")
