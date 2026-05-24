import os
import time
import logging
import re
from datetime import datetime, timedelta
import pdfplumber
from bse import BSE

# ================== CONFIG ==================
START_DATE = "20200101"
END_DATE = "20260524"
BASE_FOLDER = "transcripts"
BATCH_DAYS = 20
DELAY = 2.0
# ===========================================

RESUME_FILE = os.path.join(BASE_FOLDER, "last_processed_date.txt")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_FOLDER, "run_log.txt"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)

logging.info("=== Transcript / Concall Downloader with Resume ===")

# ================== Resume Logic ==================
if os.path.exists(RESUME_FILE):
    with open(RESUME_FILE, 'r') as f:
        last_date = f.read().strip()
        if last_date:
            START_DATE = last_date
            logging.info(f"✅ Resuming from last processed date: {START_DATE}")
        else:
            logging.info("No previous resume date found. Starting from beginning.")
else:
    logging.info("No resume file found. Starting from 2020.")

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
    text = (str(ann.get('headline', '')) + " " + 
            str(ann.get('subject', ''))).lower()
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

# ============== Main Loop with Resume Support ==============
current = datetime.strptime(START_DATE, "%Y%m%d")
end_dt = datetime.strptime(END_DATE, "%Y%m%d")
total = 0

while current <= end_dt:
    batch_end = min(current + timedelta(days=BATCH_DAYS - 1), end_dt)
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

        # Update resume file after successful batch
        with open(RESUME_FILE, 'w') as f:
            f.write(batch_end.strftime("%Y%m%d"))
        logging.info(f"Resume point updated to: {batch_end.strftime('%Y-%m-%d')}")

    except Exception as e:
        logging.error(f"Batch error: {str(e)}")
        break  # Stop on major error so you can resume

    current = batch_end + timedelta(days=1)
    time.sleep(3)

logging.info(f"\n🎉 PROCESS COMPLETED! Total Transcripts Saved: {total}")
print("Script finished. You can re-run anytime to resume.")
