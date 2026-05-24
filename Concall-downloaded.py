import requests
import os
import time
import csv
import urllib.parse
import re
import fitz  # PyMuPDF
from bs4 import BeautifulSoup
from io import BytesIO
from datetime import datetime
from dateutil.relativedelta import relativedelta

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/pdf, text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Origin': 'https://www.bseindia.com',
    'Referer': 'https://www.bseindia.com/'
}

def clean_filename_component(text):
    """Removes illegal OS characters and truncates strings safely for paths."""
    clean = re.sub(r'[\\/*?:"<>|]', "", text).strip()
    return clean[:50]

def get_target_companies():
    """Fetches all active BSE equity codes and actual Company Names dynamically from Zerodha."""
    print("Fetching live market symbols from Zerodha API...")
    fallback_list = [
        {'scrip': '500034', 'name': 'BAJAJ FINANCE'},
        {'scrip': '500325', 'name': 'RELIANCE INDUSTRIES'},
        {'scrip': '532540', 'name': 'TATA CONSULTANCY SERVICES'}
    ]
    try:
        url = "https://api.kite.trade/instruments"
        res = requests.get(url, timeout=15)
        res.raise_for_status() 
        cr = csv.DictReader(res.content.decode('utf-8').splitlines())
        
        companies = []
        for row in cr:
            if row.get('exchange') == 'BSE' and row.get('instrument_type') == 'EQ':
                scrip_code = row.get('exchange_token')
                raw_name = row.get('name') or row.get('tradingsymbol') or "Unknown_Company"
                if scrip_code and scrip_code.isdigit():
                    companies.append({'scrip': scrip_code, 'name': raw_name.strip()})
        
        if companies:
            print(f"Success! Found {len(companies)} active BSE companies.")
            return companies
        return fallback_list
    except Exception as e:
        print(f"Failed to fetch live master list: {e}. Defaulting to safety fallback array.")
        return fallback_list

def is_valid_headline(headline):
    """Filters written records but actively discards audio feeds and slide desks."""
    h = headline.lower()
    if any(bad in h for bad in ['audio', 'video', 'mp3', 'recording', 'presentation', 'presentation slides']):
        return False
    return any(keyword in h for keyword in ['transcript', 'earnings call', 'analyst meet', 'investor call'])

def extract_urls(text):
    """Harvests raw web links hidden inside corporate confirmation sheets."""
    return re.findall(r'(https?://[^\s\"\'\>]+)', text)

def extract_text_from_pdf_bytes(pdf_bytes):
    """Safely reads streaming binary content and parses encrypted layouts via PyMuPDF."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.needs_pass:
            return "[SYSTEM WARNING: This PDF is password-protected and cannot be parsed.]\n"
        
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        return text if len(text.strip()) > 200 else None
    except Exception as e:
        print(f"   --> PDF Parsing engine exception error: {e}")
        return None

def try_resolve_external_link(url):
    """Traverses corporate landing directories to bypass external link cover letters."""
    url = url.rstrip('.,);:]')
    if any(x in url.lower() for x in ['bseindia.com', 'nseindia.com', '.mp3', '.mp4', 'youtube', 'zoom.us']):
        return None
        
    try:
        print(f"   --> Following cover-letter link: {url}")
        browser_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(url, headers=browser_headers, timeout=15)
        content_type = res.headers.get('Content-Type', '').lower()
        
        if res.status_code == 200 and 'pdf' in content_type:
            return extract_text_from_pdf_bytes(res.content)
            
        elif res.status_code == 200 and 'html' in content_type:
            print("   --> Landing page is HTML. Commencing soup traversal...")
            soup = BeautifulSoup(res.text, 'html.parser')
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                text = a_tag.get_text().lower()
                
                if href.lower().endswith('.pdf') or 'transcript' in text:
                    pdf_url = urllib.parse.urljoin(url, href)
                    print(f"   --> Found matching node link: {pdf_url}")
                    pdf_res = requests.get(pdf_url, headers=browser_headers, timeout=15)
                    if pdf_res.status_code == 200 and 'pdf' in pdf_res.headers.get('Content-Type', '').lower():
                        return extract_text_from_pdf_bytes(pdf_res.content)
    except Exception as e:
        print(f"   --> External link validation failed (Link Rot/Firewall): {e}")
    return None

def has_recent_concall(scrip_code):
    """Quick check logic sweeping the last 12 months for an active IR history."""
    end_date = datetime.now()
    start_date = end_date - relativedelta(months=12)
    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
    params = {
        'pageno': '1', 'strCat': '-1', 
        'strPrevDate': start_date.strftime("%Y%m%d"),
        'strScrip': str(scrip_code), 'strSearch': '',
        'strToDate': end_date.strftime("%Y%m%d"), 'strType': 'C'
    }
    try:
        res = requests.get(url, headers=HEADERS, params=params, timeout=15)
        res.raise_for_status() 
        for item in res.json().get('Table', []):
            if item.get('ATTACHMENTNAME') and is_valid_headline(item.get('NEWSSUB', '')):
                return True
        return False
    except Exception:
        return None 

def fetch_history(scrip_code, years_back=5):
    """Gathers historical records in rolling 6-month steps to accommodate BSE rules."""
    end_date = datetime.now()
    cutoff_date = end_date - relativedelta(years=years_back)
    all_transcripts = []
    current_end = end_date
    
    while current_end > cutoff_date:
        current_start = max(current_end - relativedelta(months=6), cutoff_date)
        url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
        params = {
            'pageno': '1', 'strCat': '-1', 
            'strPrevDate': current_start.strftime("%Y%m%d"),
            'strScrip': str(scrip_code), 'strSearch': '',
            'strToDate': current_end.strftime("%Y%m%d"), 'strType': 'C'
        }
        try:
            res = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if res.status_code == 200:
                for item in res.json().get('Table', []):
                    headline = item.get('NEWSSUB', '')
                    attachment = item.get('ATTACHMENTNAME', '')
                    if attachment and is_valid_headline(headline):
                        safe_attachment = urllib.parse.quote(attachment)
                        all_transcripts.append({
                            'date': item.get('NEWS_DT', 'Unknown_Date'), 
                            'headline': headline.strip(),
                            'pdf_url': f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{safe_attachment}"
                        })
        except Exception:
            pass 
        current_end = current_start
        time.sleep(0.5) 
    return all_transcripts

if __name__ == "__main__":
    os.makedirs("transcripts", exist_ok=True)
    companies = get_target_companies()
    
    deep_dives_performed = 0
    quick_checks_performed = 0
    MAX_DEEP_DIVES = 12
    MAX_QUICK_CHECKS = 250
    
    for comp in companies:
        if deep_dives_performed >= MAX_DEEP_DIVES or quick_checks_performed >= MAX_QUICK_CHECKS: 
            break
            
        scrip = comp['scrip']
        safe_company_name = clean_filename_component(comp['name'])
        company_folder = f"transcripts/{safe_company_name} ({scrip})"
        os.makedirs(company_folder, exist_ok=True)
            
        marker_file = f"{company_folder}/_checked.mar"
        needs_update = True
        
        if os.path.exists(marker_file):
            with open(marker_file, 'r') as f:
                state = f.read().strip()
            if state == "skipped_no_history":
                # Forces validation retry using our refined comprehensive headline matrix
                needs_update = True 
            elif state.startswith("done:"):
                last_run_str = state.split(":")[1]
                last_run_date = datetime.strptime(last_run_str, "%Y-%m-%d")
                if (datetime.now() - last_run_date).days < 60:
                    needs_update = False
                    
        if not needs_update:
            continue
            
        print(f"\n--- Evaluating Target: {safe_company_name} ({scrip}) ---")
        recent_status = has_recent_concall(scrip)
        
        if recent_status is None:
            continue 
            
        if recent_status is False:
            print("Zero active IR presence detected. Blacklisting company tracking.")
            with open(marker_file, "w") as f: 
                f.write("skipped_no_history")
            quick_checks_performed += 1
            continue
            
        print("Confirmed Concall History! Initializing 5-year data harvest...")
        calls = fetch_history(scrip, years_back=5)
        deep_dive_successful = True 
        
        for call in calls:
            raw_date = str(call['date'])
            safe_date = raw_date.replace(":", "-").replace(" ", "_").replace("/", "-")
            short_headline = clean_filename_component(call['headline'])
            filename = f"{company_folder}/{safe_date}_{short_headline}.txt"
            
            if os.path.exists(filename): 
                continue
                
            try:
                print(f"Downloading Document: {call['headline'][:50]}...")
                pdf_res = requests.get(call['pdf_url'], headers=HEADERS, timeout=20)
                content_type = pdf_res.headers.get('Content-Type', '').lower()
                
                if pdf_res.status_code == 200 and 'pdf' in content_type:
                    base_text = extract_text_from_pdf_bytes(pdf_res.content) or ""
                    final_text = base_text
                    
                    if len(base_text.strip()) < 3000:  
                        urls = extract_urls(base_text)
                        for url in urls:
                            external_transcript = try_resolve_external_link(url)
                            if external_transcript:
                                final_text += "\n\n=== RESOLVED EXTERNAL TRANSCRIPT DOCUMENT ===\n\n" + external_transcript
                                break 
                    
                    if len(final_text.strip()) < 60:
                        final_text = "[SYSTEM WARNING: This document is an un-extractable scanned image layout.]\n\n"
                        
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(f"HEADLINE: {call['headline']}\nURL: {call['pdf_url']}\n\n{final_text}")
                else:
                    deep_dive_successful = False
                time.sleep(1.5)
            except Exception as e:
                print(f"Extraction execution pipeline error: {e}")
                deep_dive_successful = False 
                
        if deep_dive_successful:
            today_str = datetime.now().strftime("%Y-%m-%d")
            with open(marker_file, "w") as f: 
                f.write(f"done:{today_str}")
        deep_dives_performed += 1
