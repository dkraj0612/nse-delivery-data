import os
import time
import logging
import re
import requests
from datetime import datetime, timedelta
from urllib.parse import urljoin
import pdfplumber
import json

# ================== CONFIG ==================
BASE_FOLDER = "transcripts"
BATCH_DAYS = 10  # Smaller batch = safer
DELAY = 2.0  # Slower = less chance of block
RESUME_FILE = os.path.join(BASE_FOLDER, "last_processed_date.txt")
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

# ===========================================

# Ensure output folder exists
os.makedirs(BASE_FOLDER, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_FOLDER, "run_log.txt"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)

logging.info("=" * 100)
logging.info("🔄 EARNINGS CALL TRANSCRIPT DOWNLOADER - LAST 5 YEARS")
logging.info("=" * 100)

# ============= CALCULATE CORRECT DATE RANGE =============
today = datetime.now()
five_years_ago = today - timedelta(days=365 * 5)
start_date_str = five_years_ago.strftime("%Y%m%d")
end_date_str = today.strftime("%Y%m%d")

logging.info(f"📅 Target Range: {start_date_str} to {end_date_str} (5 years)")

# Resume from checkpoint if available
if os.path.exists(RESUME_FILE):
    try:
        with open(RESUME_FILE, 'r') as f:
            content = f.read().strip()
            # Validate resume date format
            if content.isdigit() and len(content) == 8:
                resume_date = datetime.strptime(content, "%Y%m%d")
                if resume_date >= five_years_ago and resume_date <= today:
                    start_date_str = content
                    logging.info(f"✅ Resuming from: {start_date_str}")
                else:
                    logging.warning(f"⚠️ Resume date out of range, starting fresh")
                    os.remove(RESUME_FILE)
            else:
                logging.warning(f"⚠️ Invalid resume file format, starting fresh")
                os.remove(RESUME_FILE)
    except Exception as e:
        logging.error(f"❌ Resume file error: {e}, starting fresh")
        os.remove(RESUME_FILE)

logging.info(f"🚀 Starting from: {start_date_str}")

# ============= LOAD COMPANY MAPPINGS =============
company_map = {}
for folder in os.listdir(BASE_FOLDER):
    folder_path = os.path.join(BASE_FOLDER, folder)
    if os.path.isdir(folder_path) and folder != "__pycache__":
        # Try to extract company code from folder name like "Tata Consultancy Services (532540)"
        match = re.search(r'\((\d{5,6})\)', folder)
        if match:
            scrip_code = match.group(1)
            company_map[scrip_code] = folder_path
            logging.info(f"  ✓ Found company: {folder} (Code: {scrip_code})")

logging.info(f"📊 Loaded {len(company_map)} companies from folder structure")

if len(company_map) == 0:
    logging.error("❌ NO COMPANIES FOUND! Create folders like: transcripts/CompanyName_(SCRIPCODE)/")
    exit(1)

# ============= HTTP SETUP =============
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate"
}

session = requests.Session()
session.headers.update(headers)

# ============= HELPER FUNCTIONS =============

def is_transcript(ann):
    """Check if announcement is a transcript"""
    try:
        headline = str(ann.get('headline', '')).lower()
        subject = str(ann.get('subject', '')).lower()
        text = headline + " " + subject
        
        keywords = ["transcript", "earnings call", "concall", "conference call", 
                   "investor meet transcript", "q1", "q2", "q3", "q4", "fy"]
        
        return any(kw in text for kw in keywords)
    except:
        return False

def extract_text(pdf_bytes):
    """Extract text from PDF bytes with fallback"""
    text = ""
    try:
        with pdfplumber.open(pdf_bytes) as pdf:
            logging.debug(f"  PDF has {len(pdf.pages)} pages")
            for i, page in enumerate(pdf.pages):
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n\n"
                except Exception as e:
                    logging.debug(f"  ⚠️ Page {i+1} extraction failed: {e}")
                    continue
        
        if not text.strip():
            logging.warning("  ⚠️ PDF extraction returned empty text")
            text = "[PDF extraction returned no text]"
            
    except Exception as e:
        logging.error(f"  ❌ PDF extraction failed: {e}")
        text = f"[PDF extraction failed: {str(e)}]"
    
    return text

def download_pdf_with_retry(pdf_url, retries=MAX_RETRIES):
    """Download PDF with retry logic"""
    for attempt in range(retries):
        try:
            resp = session.get(pdf_url, timeout=45)
            if resp.status_code == 200:
                return resp.content
            elif resp.status_code == 404:
                logging.warning(f"  ⚠️ PDF not found (404): {pdf_url}")
                return None
            else:
                logging.warning(f"  ⚠️ HTTP {resp.status_code} (attempt {attempt+1}/{retries})")
        except requests.Timeout:
            logging.warning(f"  ⚠️ Timeout downloading PDF (attempt {attempt+1}/{retries})")
        except Exception as e:
            logging.warning(f"  ⚠️ Download error: {str(e)[:100]} (attempt {attempt+1}/{retries})")
        
        if attempt < retries - 1:
            time.sleep(RETRY_DELAY)
    
    return None

def save_file(ann):
    """Download and save transcript"""
    try:
        # Get company code
        scrip = str(ann.get('scrip_code', '') or ann.get('SC_CODE', '')).strip()
        
        if not scrip or scrip not in company_map:
            return False
        
        company_folder = company_map[scrip]
        cat_folder = os.path.join(company_folder, "Transcripts")
        os.makedirs(cat_folder, exist_ok=True)
        
        # Get metadata
        company_name = str(ann.get('company_name', 'Unknown')).strip()
        date_str = str(ann.get('dt', '')).strip()
        headline = str(ann.get('headline', '')).strip()
        
        # Get PDF URL - check multiple possible fields
        pdf_url = (ann.get('attachment') or 
                  ann.get('pdf_link') or 
                  ann.get('ATTACHMENT') or
                  ann.get('PDF_URL'))
        
        if not pdf_url:
            logging.debug(f"  ⚠️ No PDF URL found for {company_name} | {date_str}")
            return False
        
        pdf_url = str(pdf_url).strip()
        if not pdf_url.startswith('http'):
            # Try to build full URL if it's relative
            if pdf_url.startswith('/'):
                pdf_url = urljoin("https://www.bseindia.com", pdf_url)
            else:
                logging.debug(f"  ⚠️ Invalid PDF URL: {pdf_url}")
                return False
        
        # Download PDF
        logging.debug(f"  📥 Downloading: {pdf_url[:80]}...")
        pdf_bytes = download_pdf_with_retry(pdf_url)
        
        if not pdf_bytes:
            logging.warning(f"  ❌ Failed to download PDF: {company_name} | {date_str}")
            return False
        
        # Extract text
        text = extract_text(pdf_bytes)
        
        # Save to file
        clean_headline = re.sub(r'[^\w\s-]', '_', headline)[:80]
        clean_company = re.sub(r'[^\w\s-]', '_', company_name)[:40]
        filename = f"{date_str}_{clean_company}_{clean_headline}_transcript.txt"
        
        filepath = os.path.join(cat_folder, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(f"Company: {company_name}\n")
            f.write(f"Date: {date_str}\n")
            f.write(f"Headline: {headline}\n")
            f.write(f"Downloaded: {datetime.now().isoformat()}\n")
            f.write("=" * 80 + "\n\n")
            f.write(text)
        
        logging.info(f"  ✅ SAVED: {company_name} | {date_str} | {len(text)} chars")
        return True
        
    except Exception as e:
        logging.error(f"  ❌ Save error: {str(e)}")
        return False

def fetch_announcements_with_retry(start_str, end_str, retries=MAX_RETRIES):
    """Fetch announcements from API with retry logic"""
    for attempt in range(retries):
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
            
            logging.debug(f"  Fetching API... (attempt {attempt+1}/{retries})")
            resp = session.get(url, params=params, timeout=30)
            
            if resp.status_code == 200:
                data = resp.json()
                return data.get('Table', [])
            else:
                logging.warning(f"  ⚠️ API returned HTTP {resp.status_code} (attempt {attempt+1}/{retries})")
        
        except requests.Timeout:
            logging.warning(f"  ⚠️ API timeout (attempt {attempt+1}/{retries})")
        except json.JSONDecodeError:
            logging.warning(f"  ⚠️ Invalid JSON response (attempt {attempt+1}/{retries})")
        except Exception as e:
            logging.warning(f"  ⚠️ API error: {str(e)[:100]} (attempt {attempt+1}/{retries})")
        
        if attempt < retries - 1:
            time.sleep(RETRY_DELAY)
    
    return []

# ============= MAIN LOOP =============

current = datetime.strptime(start_date_str, "%Y%m%d")
end_dt = datetime.strptime(end_date_str, "%Y%m%d")
total_saved = 0
total_checked = 0

while current <= end_dt:
    batch_end = min(current + timedelta(days=BATCH_DAYS - 1), end_dt)
    start_str = current.strftime("%Y%m%d")
    end_str = batch_end.strftime("%Y%m%d")
    
    logging.info(f"\n{'='*100}")
    logging.info(f"📍 BATCH: {start_str} → {end_str}")
    logging.info('='*100)
    
    try:
        # Fetch with retry
        anns = fetch_announcements_with_retry(start_str, end_str)
        
        if not anns:
            logging.warning(f"  ⚠️ No announcements found in this batch")
        else:
            logging.info(f"  📄 API returned {len(anns)} announcements")
            
            batch_saved = 0
            for ann in anns:
                if is_transcript(ann):
                    total_checked += 1
                    if save_file(ann):
                        batch_saved += 1
                        total_saved += 1
                    time.sleep(DELAY)
            
            logging.info(f"  ✅ Batch complete: Saved {batch_saved} transcripts")
    
    except Exception as e:
        logging.error(f"  ❌ Batch error: {str(e)}")
        # Continue to next batch instead of breaking
    
    # Update resume file with current batch end date
    try:
        with open(RESUME_FILE, 'w') as f:
            f.write(batch_end.strftime("%Y%m%d"))
    except Exception as e:
        logging.error(f"  ❌ Could not update resume file: {e}")
    
    current = batch_end + timedelta(days=1)
    time.sleep(4)  # Extra delay between batches

# ============= SUMMARY =============
logging.info(f"\n{'='*100}")
logging.info(f"🎉 DOWNLOAD COMPLETE!")
logging.info(f"{'='*100}")
logging.info(f"✅ Total Transcripts Saved: {total_saved}")
logging.info(f"📊 Total Transcripts Checked: {total_checked}")
logging.info(f"📁 Saved in: {BASE_FOLDER}/")
logging.info(f"{'='*100}\n")
