import requests
import os
import time
import csv
import urllib.parse
import re
import fitz  # PyMuPDF
import pytz
import zipfile
from bs4 import BeautifulSoup
from io import BytesIO
from datetime import datetime

# --- SETUP & RESILIENCE ---
IST = pytz.timezone('Asia/Kolkata')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Origin': 'https://www.bseindia.com',
    'Referer': 'https://www.bseindia.com/'
}

# --- UTILITIES ---
def clean_filename(text, max_length=30):
    return re.sub(r'[\\/*?:"<>|]', "", text).strip()[:max_length]

def update_dashboard(scrip, name, status, files_downloaded=0):
    with open("execution_dashboard.log", "a", encoding="utf-8") as f:
        timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {scrip} - {name[:20]:<20} | Status: {status:<15} | Files: {files_downloaded}\n")

def get_target_companies():
    print("Fetching live market symbols...")
    cache_file = "bse_master_cache.csv"
    fallback_list = [{'scrip': '500325', 'name': 'RELIANCE INDUSTRIES'}, {'scrip': '500696', 'name': 'HINDUSTAN UNILEVER'}]
    
    try:
        res = requests.get("https://api.kite.trade/instruments", timeout=15)
        res.raise_for_status() 
        lines = res.content.decode('utf-8').splitlines()
        cr = csv.DictReader(lines)
        
        companies = []
        for row in cr:
            if row.get('exchange') == 'BSE' and row.get('instrument_type') == 'EQ':
                scrip_code = row.get('exchange_token')
                raw_name = row.get('name') or row.get('tradingsymbol') or "Unknown_Company"
                if scrip_code and scrip_code.isdigit():
                    companies.append({'scrip': scrip_code, 'name': raw_name.strip()})
        
        if companies:
            with open(cache_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=['scrip', 'name'])
                writer.writeheader()
                writer.writerows(companies)
            print(f"Success! Found {len(companies)} companies.")
            return companies
    except Exception as e:
        print(f"API fetch failed: {e}. Attempting to load from local cache...")
        
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return list(csv.DictReader(f))
        except Exception:
            pass
    return fallback_list

# --- CORE DATA ENGINE (RESTORED TO ORIGINAL LOGIC) ---
def get_bse_announcements(scrip):
    """Uses BSE's internal search engine directly, bypassing volume crashes."""
    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
    valid_announcements = []
    seen_attachments = set()
    
    # We search the 3 most common terms individually. BSE handles the history automatically.
    keywords = ['transcript', 'call', 'meet']
    
    for kw in keywords:
        try:
            for pageno in range(1, 4): # 3 pages per keyword is plenty for historical data
                params = {
                    'pageno': str(pageno), 'strCat': '-1', 
                    'strPrevDate': '', 'strScrip': str(scrip), 
                    'strSearch': kw, 'strToDate': '', 'strType': 'C'
                }
                res = requests.get(url, headers=HEADERS, params=params, timeout=15)
                data = res.json().get('Table', [])
                
                if not data: 
                    break
                
                for item in data:
                    h = item.get('NEWSSUB', '').lower()
                    attachment = item.get('ATTACHMENTNAME', '')
                    
                    if not attachment or attachment in seen_attachments:
                        continue
                        
                    # Negative filter: Reject audio files unless it explicitly says 'transcript'
                    if any(bad in h for bad in ['audio', 'video', 'mp3', 'presentation', 'slides']):
                        if 'transcript' not in h:
                            continue
                            
                    valid_announcements.append(item)
                    seen_attachments.add(attachment)
                    
                time.sleep(0.5)
        except Exception as e:
            print(f"BSE API Error for {scrip} on keyword '{kw}': {e}")
            
    return valid_announcements

# --- PDF PROCESSING ---
def extract_text_from_pdf(pdf_bytes):
    if not pdf_bytes.startswith(b'%PDF'):
        return None, "INVALID_PDF_FORMAT_OR_WAF_BLOCK"
        
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.needs_pass:
            return None, "PASSWORD_LOCKED"
            
        text = "".join(page.get_text() + "\n" for page in doc)
        
        if len(text.strip()) < 50:
            for page in doc:
                for link in page.get_links():
                    if 'uri' in link:
                        text += f"\n[EMBEDDED LINK FOUND: {link['uri']}]\n"
                        
        return text if len(text.strip()) > 50 else None, "SUCCESS"
    except Exception:
        return None, "PARSE_ERROR"
    finally:
        if doc: doc.close()

def process_downloaded_payload(file_bytes, content_type):
    if 'zip' in content_type or file_bytes.startswith(b'PK\x03\x04'):
        try:
            with zipfile.ZipFile(BytesIO(file_bytes)) as z:
                for filename in z.namelist():
                    if filename.lower().endswith('.pdf'):
                        with z.open(filename) as pdf_file:
                            return extract_text_from_pdf(pdf_file.read())
            return None, "NO_PDF_IN_ZIP"
        except zipfile.BadZipFile:
            return None, "CORRUPT_ZIP"
            
    return extract_text_from_pdf(file_bytes)

def resolve_external_link(url):
    url = url.rstrip('.,);:]')
    if any(x in url.lower() for x in ['.mp3', '.mp4', 'youtube', 'zoom.us', 'bseindia.com']): 
        return None
        
    stealth_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
        
    try:
        res = requests.get(url, headers=stealth_headers, timeout=15)
        if 'pdf' in res.headers.get('Content-Type', '').lower() or res.content.startswith(b'%PDF'):
            text, _ = extract_text_from_pdf(res.content)
            return text
        elif 'html' in res.headers.get('Content-Type', '').lower() or b'<html' in res.content[:500].lower():
            soup = BeautifulSoup(res.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                if a['href'].lower().endswith('.pdf') or 'transcript' in a.get_text().lower():
                    pdf_url = urllib.parse.urljoin(url, a['href'])
                    pdf_res = requests.get(pdf_url, headers=stealth_headers, timeout=15)
                    text, _ = extract_text_from_pdf(pdf_res.content)
                    return text
    except Exception:
        pass
    return None

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    print("==========================================")
    print("🚀 PIPELINE WAKING UP - ENVIRONMENT LOADED")
    print("==========================================")
    
    os.makedirs("transcripts", exist_ok=True)
    companies = get_target_companies()
    
    deep_dives_performed = 0
    quick_checks_performed = 0
    MAX_DEEP_DIVES = 12
    MAX_QUICK_CHECKS = 250
    
    for comp in companies:
        if deep_dives_performed >= MAX_DEEP_DIVES or quick_checks_performed >= MAX_QUICK_CHECKS: 
            break
            
        scrip, name = comp['scrip'], clean_filename(comp['name'], 40)
        folder = f"transcripts/{name} ({scrip})"
        os.makedirs(folder, exist_ok=True)
            
        marker = f"{folder}/_checked.mar"
        needs_update = True
        
        if os.path.exists(marker):
            state = open(marker, 'r').read().strip()
            if ":" in state:
                status, date_str = state.split(":")
                days_passed = (datetime.now(IST) - datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=IST)).days
                if status == "skipped_no_history" and days_passed < 90: 
                    needs_update = False
                elif status == "done" and days_passed < 60: 
                    needs_update = False

        if not needs_update: 
            continue
            
        print(f"\nEvaluating: {name} ({scrip})")
        
        # We no longer need separate Quick Check / Deep Dive phases.
        # The Targeted Multi-Search fetches everything instantly.
        all_calls = get_bse_announcements(scrip)
        
        if not all_calls:
            print("No IR history. 90-day cooldown.")
            open(marker, "w").write(f"skipped_no_history:{datetime.now(IST).strftime('%Y-%m-%d')}")
            update_dashboard(scrip, name, "NO_HISTORY")
            quick_checks_performed += 1
            continue
            
        print(f"IR History found. Harvesting {len(all_calls)} targeted documents...")
        success = True
        files_saved = 0
        
        for item in all_calls:
            date = item['NEWS_DT'][:10]
            headline = clean_filename(item['NEWSSUB'], 30)
            pdf_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{urllib.parse.quote(item['ATTACHMENTNAME'])}"
            unique_id = pdf_url.split('/')[-1][:8] 
            
            filename = f"{folder}/{date}_{headline}_{unique_id}.txt"
            if os.path.exists(filename) or os.path.exists(f"{folder}/ERROR_{date}_{headline}_{unique_id}.txt"): 
                continue
                
            try:
                res = requests.get(pdf_url, headers=HEADERS, timeout=20)
                content_type = res.headers.get('Content-Type', '').lower()
                
                text, err_status = process_downloaded_payload(res.content, content_type)
                
                if text and len(text) < 3000: 
                    urls = re.findall(r'(https?://[^\s\"\'\>]+)', text)
                    for link in urls[:3]:
                        ext_text = resolve_external_link(link)
                        if ext_text: 
                            text += "\n\n=== EXTERNAL TRANSCRIPT ===\n\n" + ext_text
                            break
                
                if not text:
                    filename = f"{folder}/ERROR_{date}_{headline}_{unique_id}.txt"
                    text = f"[SYSTEM WARNING: Extraction failed. Reason: {err_status}]"
                    
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(f"HEADLINE: {item['NEWSSUB']}\nURL: {pdf_url}\n\n{text}")
                files_saved += 1
                time.sleep(2) 
            except Exception as e:
                print(f"File extraction failure: {e}")
                success = False
                
        if success:
            open(marker, "w").write(f"done:{datetime.now(IST).strftime('%Y-%m-%d')}")
            update_dashboard(scrip, name, "SUCCESS", files_saved)
        else:
            update_dashboard(scrip, name, "PARTIAL_FAIL", files_saved)
            
        deep_dives_performed += 1
