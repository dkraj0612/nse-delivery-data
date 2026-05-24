import requests
import os
import time
import csv
from pypdf import PdfReader
from io import BytesIO
from datetime import datetime
from dateutil.relativedelta import relativedelta

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Origin': 'https://www.bseindia.com',
    'Referer': 'https://www.bseindia.com/'
}

def get_target_companies():
    """Fetches a list of active Indian scrip codes dynamically with a reliable fallback."""
    fallback_list = ['500325', '532540', '532500', '500180', '500696', '532174', '532215']
    try:
        url = "https://raw.githubusercontent.com/sonyl/indian-stock-bse-nse-code/master/bse_nse.csv"
        res = requests.get(url, timeout=10)
        res.raise_for_status() 
        
        decoded = res.content.decode('utf-8')
        cr = csv.reader(decoded.splitlines(), delimiter=',')
        
        scrips = []
        for row in cr:
            if row and row[0].strip().isdigit(): 
                scrips.append(row[0].strip())
                
        if not scrips:
            return fallback_list
            
        return scrips[:1000] 
        
    except Exception as e:
        print(f"Failed to fetch master list: {e}. Using fallback.")
        return fallback_list

def has_recent_concall(scrip_code):
    """
    Sweeps the last 12 months. 
    Returns: True (Found), False (Not Found), None (Network Error - Do Not Blacklist)
    """
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
        res.raise_for_status() # Catch 404s/500s explicitly
        data = res.json()
        
        for item in data.get('Table', []):
            headline = item.get('NEWSSUB', '').lower()
            attachment = item.get('ATTACHMENTNAME', '')
            if attachment and ('transcript' in headline or 'earnings call' in headline):
                return True
        return False
        
    except Exception as e:
        print(f"Network error during Quick Check for {scrip_code}: {e}")
        return None # Return None so we retry this company on the next run!

def fetch_5_years(scrip_code):
    """Chunks 5 years of history into 6-month blocks to bypass BSE limits."""
    end_date = datetime.now()
    cutoff_date = end_date - relativedelta(years=5)
    all_transcripts = []
    
    current_end = end_date
    while current_end > cutoff_date:
        current_start = current_end - relativedelta(months=6)
        if current_start < cutoff_date: 
            current_start = cutoff_date
            
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
                data = res.json()
                for item in data.get('Table', []):
                    headline = item.get('NEWSSUB', '')
                    attachment = item.get('ATTACHMENTNAME', '')
                    if attachment and ('transcript' in headline.lower() or 'earnings call' in headline.lower()):
                        all_transcripts.append({
                            'date': item.get('NEWS_DT', 'Unknown_Date'), # Fallback if date missing
                            'headline': headline,
                            'pdf_url': f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attachment}"
                        })
        except Exception:
            pass # Keep moving if a single 6-month chunk fails
            
        current_end = current_start
        time.sleep(1) 
    return all_transcripts

if __name__ == "__main__":
    os.makedirs("transcripts", exist_ok=True)
    
    scrips = get_target_companies()
    print(f"Loaded {len(scrips)} companies to process.")
    
    processed_this_run = 0
    
    for scrip in scrips:
        if processed_this_run >= 30: 
            print("Reached batch limit for this run. Waking up in 2 hours to continue.")
            break
            
        marker_file = f"transcripts/{scrip}_checked.mar"
        if os.path.exists(marker_file):
            continue
            
        print(f"\n--- Processing Scrip: {scrip} ---")
        
        # STEP 1: The Quick Check
        recent_status = has_recent_concall(scrip)
        
        if recent_status is None:
            print(f"API failed for {scrip}. Skipping for now to avoid false blacklisting.")
            continue # Do not increment processed_this_run, do not write marker
            
        if recent_status is False:
            print(f"No concalls in the last 12 months for {scrip}. Skipping 5-year search.")
            with open(marker_file, "w") as f: 
                f.write("skipped_no_history")
            processed_this_run += 1
            time.sleep(1)
            continue
            
        # STEP 2: The Deep Dive
        print(f"Active IR found. Scanning 5-year history...")
        calls = fetch_5_years(scrip)
        
        for call in calls:
            # Safe date replacing to prevent crashes
            raw_date = str(call['date'])
            safe_date = raw_date.replace(":", "-").replace(" ", "_").replace("/", "-")
            filename = f"transcripts/{scrip}_{safe_date}.txt"
            
            if os.path.exists(filename): 
                continue
                
            try:
                print(f"Downloading: {call['headline']}")
                pdf_res = requests.get(call['pdf_url'], headers=HEADERS, timeout=20)
                
                if pdf_res.status_code == 200:
                    reader = PdfReader(BytesIO(pdf_res.content))
                    text = "".join(page.extract_text() + "\n" for page in reader.pages)
                    
                    # PRO FIX: Detect Scanned/Empty PDFs
                    if len(text.strip()) < 50:
                        text = "[WARNING: This PDF appears to be a scanned image. PyPDF could not extract text. Manual review or OCR required.]\n\n"
                    
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(f"HEADLINE: {call['headline']}\nURL: {call['pdf_url']}\n\n{text}")
                    time.sleep(2)
            except Exception as e:
                print(f"Error parsing PDF for {scrip}: {e}")
                
        # Drop a marker file so we know this company's 5-year history is complete
        with open(marker_file, "w") as f: 
            f.write("download_complete")
            
        processed_this_run += 1
