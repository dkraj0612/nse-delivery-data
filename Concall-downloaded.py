import os
import time
import logging
from datetime import datetime, timedelta
import pandas as pd
import pdfplumber
from bse import BSE

# ================== CONFIG ==================
START_DATE = "20250501"      # ← Start with recent month for testing!
END_DATE = "20260524"
BASE_FOLDER = "bse_results_transcripts_text"
BATCH_DAYS = 15              # Smaller = safer & faster feedback
DELAY = 2.0
# ===========================================

# Setup Logging
log_file = os.path.join(BASE_FOLDER, "run_log.txt")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()   # Also print to console
    ]
)

metadata_folder = os.path.join(BASE_FOLDER, "metadata")
companies_folder = os.path.join(BASE_FOLDER, "Companies")
os.makedirs(metadata_folder, exist_ok=True)
os.makedirs(companies_folder, exist_ok=True)

logging.info("=== Script Started ===")
logging.info(f"Date Range: {START_DATE} to {END_DATE}")
logging.info(f"Base Folder: {BASE_FOLDER}")

b = BSE()

def is_relevant(ann):
    text = (str(ann.get('headline', '')) + " " + 
            str(ann.get('subject', ''))).lower()
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
            logging.warning(f"No valid PDF URL for {ann.get('company_name')}")
            return False

        company = str(ann.get('company_name', 'Unknown'))
        date_str = str(ann.get('dt', ''))
        headline = str(ann.get('headline', ''))

        logging.info(f"Downloading PDF for {company} | {date_str}")

        resp = b.session.get(pdf_url, timeout=30)
        if resp.status_code != 200:
            logging.error(f"Download failed {resp.status_code} for {company}")
            return False

        logging.info(f"Extracting text from PDF ({len(resp.content)/1024:.1f} KB)")
        text = extract_text(resp.content)

        # Save file
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

        logging.info(f"✓ SUCCESS: {clean_company} | {category} | {date_str}")
        return True
    except Exception as e:
        logging.error(f"✗ Failed processing {company}: {str(e)}")
        return False

# ============== Main Processing Loop ==============
current = datetime.strptime(START_DATE, "%Y%m%d")
end_dt = datetime.strptime(END_DATE, "%Y%m%d")
total_processed = 0

while current <= end_dt:
    batch_end = min(current + timedelta(days=BATCH_DAYS), end_dt)
    start_str = current.strftime("%Y-%m-%d")
    end_str = batch_end.strftime("%Y-%m-%d")

    logging.info(f"\n{'='*60}")
    logging.info(f"STARTING BATCH: {start_str} to {end_str}")

    try:
        logging.info("Calling BSE API for announcements...")
        anns = b.announcements(from_date=start_str, to_date=end_str, category="-1")
        
        logging.info(f"API returned {len(anns)} total announcements")

        relevant = []
        for a in anns:
            cat = is_relevant(a)
            if cat:
                a['filtered_category'] = cat
                relevant.append(a)

        logging.info(f"Filtered to {len(relevant)} relevant (Results/Transcript)")

        if relevant:
            df = pd.DataFrame(relevant)
            csv_path = os.path.join(metadata_folder, f"metadata_{start_str}_{end_str}.csv")
            df.to_csv(csv_path, index=False)
            logging.info(f"Saved metadata CSV: {len(relevant)} rows")

            for i, ann in enumerate(relevant, 1):
                logging.info(f"Processing {i}/{len(relevant)}: {ann.get('company_name')}")
                process_ann(ann, ann['filtered_category'])
                time.sleep(DELAY)
            
            total_processed += len(relevant)
        else:
            logging.info("No relevant announcements in this batch.")

    except Exception as e:
        logging.error(f"Batch failed with error: {str(e)}")

    current = batch_end + timedelta(days=1)
    time.sleep(3)

logging.info(f"\n🎉 SCRIPT FINISHED!")
logging.info(f"Total relevant announcements processed: {total_processed}")
logging.info(f"Log file: {log_file}")
logging.info(f"Check folder: {BASE_FOLDER}")
