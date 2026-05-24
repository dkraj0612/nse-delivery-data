import os
import time
import logging
import re
from datetime import datetime, timedelta
import pdfplumber
from bse import BSE

# ================== CONFIG ==================
BASE_FOLDER = "transcripts"
BATCH_DAYS = 20
DELAY = 2.0
RESUME_FILE = os.path.join(BASE_FOLDER, "last_processed_date.txt")
# ===========================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_FOLDER, "run_log.txt"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)

logging.info("=== Transcript Downloader - Ultra Stable Version ===")

# ================== Resume Logic (Very Defensive) ==================
start_date_str = "20200101"

if os.path.exists(RESUME_FILE):
    try:
        with open(RESUME_FILE, 'r') as f:
            content = f.read().strip()
            if content and len(content) == 8 and content.isdigit():
                start_date_str = content
                logging.info(f"✅ Resuming from: {start_date_str}")
            else:
                logging.info("Invalid resume date, starting from 2020")
    except Exception as e:
        logging.warning(f"Resume file error: {e}. Starting from beginning.")

logging.info(f"Starting Date: {start_date_str}")

# Company Mapping
company_map = {}
for folder in os.listdir(BASE_FOLDER):
    if os.path.isdir(os.path.join(BASE_FOLDER, folder)):
        match = re.search(r'\((\d{5,6})\)', folder)
        if match:
            scrip = match.group(1)
            company_map[scrip] = os.path.join(BASE_FOLDER, folder)

logging.info(f"Loaded {len(company_map)} companies")

b = BSE(download_folder=os.path.join(BASE_FOLDER, "temp_downloads"))

def is_transcript(ann):
    text = (str(ann.get('headline', '')) + " " + str(ann.get('subject', ''))).lower()
    keywords = ["transcript", "earnings call", "concall", "conference call", 
                "investor meet transcript", "con call", "earnings transcript"]
    return any(kw in text for kw in keywords)

def extract_text(pdf_bytes):
    text = ""
    try:
        with pdfplumber.open(pdf_bytes) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    text += f"--- Page {i+1} ---\n{page_text}\n\n"
    except:
        text = "PDF extraction failed"
    return text

def save_file(ann):
    try:
        scrip = str(ann.get('scrip_code') or ann.get('SC_CODE', ''))
        if scrip not in company_map:
            return False

        company_folder = company_map[scrip]
        cat_folder = os.path.join(company_folder, "Transcripts")
        os.makedirs(cat_folder, exist_ok=True)

        company_name = str(ann.get('company_name', 'Unknown'))
        date_str = str(ann.get('dt', ''))
        headline = str(ann.get('headline', ''))

        pdf_url = ann.get('attachment') or ann.get('pdf_link')
        if not pdf_url:
            return False

        resp = b.session.get(pdf_url, timeout=40)
        if resp.status_code != 200:
            return False

        text = extract_text(resp.content)

        clean_headline = re.sub(r'[^\w\s-]', '_', headline)[:100]
        filename = f"{company_name}_{date_str}_Transcript_{clean_headline}.txt"
        path = os.path.join(cat_folder, filename)

        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Company: {company_name}\nDate: {date_str}\nType: Transcript\nHeadline: {headline}\n\n")
            f.write("="*80 + "\n\n")
            f.write(text)

        logging.info(f"✓ SAVED: {company_name} | {date_str}")
        return True
    except Exception as e:
        logging.error(f"Save failed: {str(e)}")
        return False

# ============== Main Loop - Very Safe Date Handling ==============
current = datetime.strptime(start_date_str, "%Y%m%d")
end_dt = datetime.strptime("20260524", "%Y%m%d")
total = 0

while current <= end_dt:
    batch_end = min(current + timedelta(days=BATCH_DAYS - 1), end_dt)

    # Force string conversion safely
    start_str = current.strftime("%Y-%m-%d")
    end_str = batch_end.strftime("%Y-%m-%d")

    logging.info(f"\n{'='*90}")
    logging.info(f"BATCH: {start_str} → {end_str}")

    try:
        anns = b.announcements(from_date=start_str, to_date=end_str, category="-1")
        logging.info(f"Found {len(anns)} announcements")

        saved_count = 0
        for ann in anns:
            if is_transcript(ann):
                if save_file(ann):
                    saved_count += 1
                    total += 1
                time.sleep(DELAY)

        logging.info(f"Saved {saved_count} transcripts in this batch")

        # Update resume
        resume_date = batch_end.strftime("%Y%m%d")
        with open(RESUME_FILE, 'w') as f:
            f.write(resume_date)
        logging.info(f"Resume updated → {resume_date}")

    except Exception as e:
        logging.error(f"Batch error: {str(e)}")
        break

    current = batch_end + timedelta(days=1)
    time.sleep(3)

logging.info(f"\n🎉 FINISHED! Total Transcripts Saved: {total}")
