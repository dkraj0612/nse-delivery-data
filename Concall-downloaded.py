import os
import time
import logging
from datetime import datetime, timedelta
import pandas as pd
import pdfplumber
from bse import BSE

# ================== CONFIG ==================
START_DATE = "20250501"      # Start small for testing
END_DATE = "20260524"
BASE_FOLDER = "bse_results_transcripts_text"
BATCH_DAYS = 10              # Reduced for safety
DELAY = 2.0
# ===========================================

# Create folders
os.makedirs(BASE_FOLDER, exist_ok=True)
metadata_folder = os.path.join(BASE_FOLDER, "metadata")
companies_folder = os.path.join(BASE_FOLDER, "Companies")
os.makedirs(metadata_folder, exist_ok=True)
os.makedirs(companies_folder, exist_ok=True)

# Logging
log_file = os.path.join(BASE_FOLDER, "run_log.txt")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)

logging.info("=== Script Started ===")
logging.info(f"Date Range: {START_DATE} to {END_DATE}")
logging.info(f"Batch size: {BATCH_DAYS} days")

# Initialize BSE
b = BSE(download_folder=os.path.join(BASE_FOLDER, "temp_downloads"))
logging.info("BSE client initialized")

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
    except Exception as e:
        text = f"PDF extraction failed: {str(e)}"
    return text

def process_ann(ann, category):
    try:
        pdf_url = ann.get('attachment') or ann.get('pdf_link')
        if not pdf_url or not str(pdf_url).startswith('http'):
            logging.warning(f"No PDF URL for {ann.get('company_name')}")
            return False

        company = str(ann.get('company_name', 'Unknown'))
        date_str = str(ann.get('dt', ''))
        headline = str(ann.get('headline', ''))

        logging.info(f"Downloading PDF → {company} | {date_str}")

        resp = b.session.get(pdf_url, timeout=30)
        if resp.status_code != 200:
            logging.error(f"Download failed ({resp.status_code})")
            return False

        logging.info("Extracting text from PDF...")
        text = extract_text(resp.content)

        clean_company = "".join(c if c.isalnum() or c in " _-" else "_" for c in company)[:80]
        clean_headline = "".join(c if c.isalnum() or c in " _-" else "_" for c in headline)[:100]

        company_dir = os.path.join(companies_folder, clean_company)
        cat_dir = os.path.join(company_dir, category)
        os.makedirs(cat_dir, exist_ok=True)

        filename = f"{clean_company}_{date_str}_{category}_{clean_headline}.txt"
        path = os.path.join(cat_dir, filename)

        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Company: {company}\nDate: {date_str}\nCategory: {category}\nHeadline: {headline}\n")
            f.write("="*80 + "\n\n")
            f.write(text)

        logging.info(f"✓ SAVED: {clean_company} | {category}")
        return True
    except Exception as e:
        logging.error(f"Failed processing announcement: {str(e)}")
        return False

# ============== Main Loop ==============
current = datetime.strptime(START_DATE, "%Y%m%d")
end_dt = datetime.strptime(END_DATE, "%Y%m%d")
total = 0

while current <= end_dt:
    batch_end = min(current + timedelta(days=BATCH_DAYS - 1), end_dt)   # Fixed: -1 to avoid overlap

    start_str = current.strftime("%Y-%m-%d")
    end_str = batch_end.strftime("%Y-%m-%d")

    logging.info(f"\n{'='*80}")
    logging.info(f"BATCH: {start_str} → {end_str}")

    try:
        logging.info("Fetching announcements from BSE...")
        anns = b.announcements(from_date=start_str, to_date=end_str, category="-1")
        
        logging.info(f"API returned {len(anns)} announcements")

        relevant = []
        for a in anns:
            cat = is_relevant(a)
            if cat:
                a_copy = dict(a)                    # Avoid modifying original
                a_copy['filtered_category'] = cat
                relevant.append(a_copy)

        logging.info(f"Relevant Results/Transcripts: {len(relevant)}")

        if relevant:
            df = pd.DataFrame(relevant)
            csv_path = os.path.join(metadata_folder, f"metadata_{start_str}_{end_str}.csv")
            df.to_csv(csv_path, index=False)
            logging.info("Metadata CSV saved")

            for i, ann in enumerate(relevant, 1):
                logging.info(f"[{i}/{len(relevant)}] Processing: {ann.get('company_name')}")
                process_ann(ann, ann['filtered_category'])
                time.sleep(DELAY)

            total += len(relevant)
    except Exception as e:
        logging.error(f"Batch error: {str(e)}")

    # Move to next batch
    current = batch_end + timedelta(days=1)
    time.sleep(3)

logging.info(f"\n🎉 SCRIPT COMPLETED! Total announcements processed: {total}")
print("Done! Check the folder and run_log.txt")
