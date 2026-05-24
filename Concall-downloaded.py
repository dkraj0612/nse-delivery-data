import os
import time
import logging
import requests
from datetime import datetime, timedelta
import pandas as pd
import pdfplumber

# ================== CONFIG ==================
START_DATE = "20250501"      # Keep small for testing
END_DATE = "20260524"
BASE_FOLDER = "bse_results_transcripts_text"
BATCH_DAYS = 5               # Very small to reduce risk
DELAY = 2.5
MAX_RETRIES = 3
# ===========================================

os.makedirs(BASE_FOLDER, exist_ok=True)
metadata_folder = os.path.join(BASE_FOLDER, "metadata")
companies_folder = os.path.join(BASE_FOLDER, "Companies")
os.makedirs(metadata_folder, exist_ok=True)
os.makedirs(companies_folder, exist_ok=True)

log_file = os.path.join(BASE_FOLDER, "run_log.txt")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.FileHandler(log_file, encoding='utf-8', mode='a'),
              logging.StreamHandler()]
)

logging.info("=== Script Started - Direct API Version ===")
logging.info(f"Range: {START_DATE} to {END_DATE}")

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bseindia.com/"
}

session = requests.Session()

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

def process_ann(ann, category):
    for attempt in range(MAX_RETRIES):
        try:
            pdf_url = ann.get('attachment') or ann.get('pdf_link') or ann.get('ATTACHMENT')
            if not pdf_url or not str(pdf_url).startswith('http'):
                return False

            company = str(ann.get('company_name', 'Unknown'))
            date_str = str(ann.get('dt', ''))
            headline = str(ann.get('headline', ''))

            logging.info(f"Downloading {company} ({date_str})")

            resp = session.get(pdf_url, headers=headers, timeout=40)
            if resp.status_code != 200:
                time.sleep(2)
                continue

            text = extract_text(resp.content)

            clean_company = "".join(c if c.isalnum() or c in " _-" else "_" for c in company)[:70]
            clean_headline = "".join(c if c.isalnum() or c in " _-" else "_" for c in headline)[:90]

            company_dir = os.path.join(companies_folder, clean_company)
            cat_dir = os.path.join(company_dir, category)
            os.makedirs(cat_dir, exist_ok=True)

            filename = f"{clean_company}_{date_str}_{category}_{clean_headline}.txt"
            path = os.path.join(cat_dir, filename)

            with open(path, "w", encoding="utf-8") as f:
                f.write(f"Company: {company}\nDate: {date_str}\nCategory: {category}\n")
                f.write(f"Headline: {headline}\n")
                f.write("="*80 + "\n\n")
                f.write(text)

            logging.info(f"✓ SAVED: {clean_company} | {category}")
            return True
        except Exception as e:
            logging.error(f"Attempt {attempt+1} failed: {str(e)}")
            time.sleep(3)
    return False

# Main Loop
current = datetime.strptime(START_DATE, "%Y%m%d")
end_dt = datetime.strptime(END_DATE, "%Y%m%d")
total = 0

while current <= end_dt:
    batch_end = min(current + timedelta(days=BATCH_DAYS - 1), end_dt)
    start_str = current.strftime("%Y%m%d")
    end_str = batch_end.strftime("%Y%m%d")

    logging.info(f"\n{'='*90}")
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
            data = resp.json()
            anns = data.get('Table', [])
            logging.info(f"Received {len(anns)} announcements")

            relevant = [dict(a, filtered_category=cat) for a in anns if (cat := is_relevant(a))]

            if relevant:
                pd.DataFrame(relevant).to_csv(
                    os.path.join(metadata_folder, f"metadata_{start_str}_{end_str}.csv"), 
                    index=False
                )

                for i, ann in enumerate(relevant, 1):
                    logging.info(f"[{i}/{len(relevant)}] Processing {ann.get('company_name')}")
                    process_ann(ann, ann['filtered_category'])
                    time.sleep(DELAY)

                total += len(relevant)
    except Exception as e:
        logging.error(f"Batch error: {str(e)}")

    current = batch_end + timedelta(days=1)
    time.sleep(4)

logging.info(f"\n🎉 FINISHED! Total: {total}")
