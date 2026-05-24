import requests
import os
import time
import csv
import urllib.parse
import re
from pypdf import PdfReader
from io import BytesIO
from datetime import datetime
from dateutil.relativedelta import relativedelta

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/pdf, text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Origin': 'https://www.bseindia.com',
    'Referer': 'https://www.bseindia.com/'
}

def clean_filename_component(text):
    return re.sub(r'[\\/*?:"<>|]', "", text).strip()[:50]

def get_target_companies():
    """Fetches live market symbols from Zerodha API."""
    fallback_list = [{'scrip': '500325', 'name': 'RELIANCE INDUSTRIES'}]
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
        return companies if companies else fallback_list
    except Exception:
        return fallback_list

def has_recent_concall(scrip_code):
    """Sweeps the last 12 months for active IR tracking."""
    end_date = datetime.now()
    start_date = end_date - relativedelta(months=12)
    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
    params = {
        'pageno': '1', 'strCat': '-1', 
        'strPrevDate': start_date.strftime("%Y%m%d"),
        'strScrip': str(scrip_code), 'strSearch': 'transcript',
        'strToDate': end_date.strftime("%Y%m%d"), 'strType': 'C'
    }
    try:
        res = requests.get(url, headers=HEADERS, params=params, timeout=15)
        res.raise_for_status() 
        for item in res.json().get('Table', []):
            headline = item.get('NEWSSUB', '').lower()
            if item.get('ATTACHMENTNAME') and ('transcript' in headline or 'earnings call' in headline):
                return True
        return False
    except Exception:
        return None 

def fetch_history(scrip_code, years_back=5):
    """Chunks history into 6-month blocks to bypass BSE limits."""
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
            'strScrip': str(scrip_code), 'strSearch': 'transcript',
            'strToDate': current_end.strftime("%Y%m%d"), 'strType': 'C'
        }
        try:
            res = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if res.status_code == 200:
                for item in res.json().get('Table', []):
                    headline = item.get('NEWSSUB', '')
                    attachment = item.get('ATTACHMENTNAME', '')
                    if attachment and ('transcript' in headline.lower() or 'earnings call' in headline.lower()):
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
        
        # --- PRO FIX: ROLLING STATE MANAGEMENT ---
        needs_update = True
        if os.path.exists(marker_file):
            with open(marker_file, 'r') as f:
                state = f.read().strip()
            
            if state == "skipped_no_history":
                needs_update = False  # Still a dead company
            elif state.startswith("done:"):
                last_run_str = state.split(":")[1]
                last_run_date = datetime.strptime(last_run_str, "%Y-%m-%d")
                # If checked less than 60 days ago, skip it. 
                # If > 60 days, we let it run to find new quarterly transcripts!
                if (datetime.now() - last_run_date).days < 60:
                    needs_update = False
                    
        if not needs_update:
            continue
            
        print(f"\n--- Evaluating Target: {safe_company_name} ({scrip}) ---")
        recent_status = has_recent_concall(scrip)
        
        if recent_status is None:
            continue 
            
        if recent_status is False:
            with open(marker_file, "w") as f: 
                f.write("skipped_no_history")
            quick_checks_performed += 1
            continue
            
        # Execute Deep Dive
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
                    reader = PdfReader(BytesIO(pdf_res.content))
                    text = "".join(page.extract_text() + "\n" for page in reader.pages)
                    if len(text.strip()) < 60:
                        text = "[SYSTEM WARNING: This document is an un-extractable scanned image layout.]\n\n"
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(f"HEADLINE: {call['headline']}\nURL: {call['pdf_url']}\n\n{text}")
                else:
                    deep_dive_successful = False
                time.sleep(1.5)
            except Exception as e:
                deep_dive_successful = False 
                
        # Write the dynamic timestamp so it wakes up again next quarter
        if deep_dive_successful:
            today_str = datetime.now().strftime("%Y-%m-%d")
            with open(marker_file, "w") as f: 
                f.write(f"done:{today_str}")
        deep_dives_performed += 1
