"""
🚀 FIXED INDIAN EARNINGS TRANSCRIPT SCRAPER v3
============================================================
Fixed Issues:
  • Company symbol → BSE scrip code mapping
  • Correct BSE API endpoint and parameters
  • Proper Moneycontrol URL patterns
  • Better error diagnosis
"""

import requests
import os
import time
import csv
import urllib.parse
import re
import fitz
import pytz
import zipfile
import json
from bs4 import BeautifulSoup
from io import BytesIO
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

IST = pytz.timezone('Asia/Kolkata')

# CRITICAL: BSE Scrip Code Mapping (Symbol → Numeric Code)
BSE_SCRIP_MAPPING = {
    'RELIANCE': '500325',
    'TCS': '532540',
    'INFY': '500209',
    'WIPRO': '500330',
    'HDFC': '500010',
    'HDFCBANK': '500180',
    'ICICIBANK': '532174',
    'KOTAKBANK': '500510',
    'AXISBANK': '532215',
    'SBIN': '500112',
    'BAJAJFINSV': '500034',
    'LT': '500510',
    'MARUTI': '532500',
    'TECHM': '532150',
    'BHARTIARTL': '532454',
    'SUNPHARMA': '500092',
    'CIPLA': '500087',
    'DRREDDYS': '500124',
    'LUPIN': '500257',
    'BRITANNIA': '531242',
    'NESTLEIND': '500150',
}

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
]

def get_rotating_headers(referer='https://www.bseindia.com'):
    """Get headers with rotating user agent."""
    headers = {
        'User-Agent': USER_AGENTS[hash(datetime.now().isoformat()) % len(USER_AGENTS)],
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': referer,
    }
    return headers

def clean_filename(text, max_length=40):
    """Clean text for filenames."""
    text = re.sub(r'[\\/*?:"<>|]', "", text).strip()
    text = text.encode('ascii', 'ignore').decode('ascii')
    return text[:max_length]

def update_dashboard(scrip, name, status, files_downloaded=0, source="UNKNOWN"):
    """Log results."""
    with open("execution_dashboard.log", "a", encoding="utf-8") as f:
        timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {scrip:>6} | {name[:25]:<25} | {status:<20} | Files: {files_downloaded:>2} | Source: {source}\n")

def get_session_with_retries(max_retries=3):
    """Create session with retry strategy."""
    session = requests.Session()
    retry_strategy = Retry(
        total=max_retries,
        status_forcelist=[429, 500, 502, 503, 504],
        method_whitelist=["HEAD", "GET", "OPTIONS"],
        backoff_factor=1.5,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

def extract_text_from_pdf(pdf_bytes, max_pages=100):
    """Extract text from PDF."""
    if not pdf_bytes or not pdf_bytes.startswith(b'%PDF'):
        return None, "INVALID_PDF_FORMAT"
    
    if len(pdf_bytes) < 1000:
        return None, "PDF_TOO_SMALL"
    
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.needs_pass:
            return None, "PASSWORD_PROTECTED"
        
        text = ""
        for page_num, page in enumerate(doc):
            if page_num >= max_pages:
                break
            try:
                text += page.get_text() + "\n"
            except:
                continue
        
        if not text or len(text.strip()) < 200:
            return None, "INSUFFICIENT_TEXT"
        
        # Check if transcript
        patterns = [r'good\s+(morning|afternoon)', r'(moderator|speaker)', r'(question|answer)']
        match_count = sum(1 for p in patterns if re.search(p, text.lower()))
        if match_count < 1 and 'earnings' not in text.lower():
            return None, "NOT_A_TRANSCRIPT"
        
        return text, "SUCCESS"
    except Exception as e:
        return None, f"PDF_PARSE_ERROR"
    finally:
        if doc:
            doc.close()

def process_downloaded_file(file_bytes, content_type):
    """Process ZIP or PDF."""
    if not file_bytes:
        return None, "EMPTY_FILE"
    
    if 'zip' in content_type.lower() or file_bytes.startswith(b'PK\x03\x04'):
        try:
            with zipfile.ZipFile(BytesIO(file_bytes)) as z:
                for zip_filename in z.namelist():
                    if zip_filename.lower().endswith('.pdf'):
                        try:
                            pdf_content = z.read(zip_filename)
                            text, status = extract_text_from_pdf(pdf_content)
                            if text:
                                return text, "SUCCESS_FROM_ZIP"
                        except:
                            continue
            return None, "ZIP_HAS_NO_PDF"
        except:
            return None, "CORRUPT_ZIP"
    
    return extract_text_from_pdf(file_bytes)

def get_bse_announcements(symbol, scrip_code, session=None):
    """
    Fetch announcements from BSE using CORRECT scrip code.
    KEY FIX: Use numeric scrip code, not symbol!
    """
    if session is None:
        session = get_session_with_retries()
    
    announcements = []
    seen_urls = set()
    
    # BSE API endpoint
    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
    
    # Keywords for earnings calls in Indian market
    keywords = [
        'earnings',
        'conference call',
        'results',
        'investor',
    ]
    
    for keyword in keywords:
        try:
            for page in range(1, 4):  # Check 3 pages
                params = {
                    'pageno': str(page),
                    'strScrip': str(scrip_code),  # MUST be numeric scrip code!
                    'strSearch': keyword,
                    'strCat': '-1',
                    'strType': 'C',
                    'strPrevDate': '',
                    'strToDate': '',
                }
                
                response = session.get(
                    url,
                    headers=get_rotating_headers(),
                    params=params,
                    timeout=15,
                )
                response.raise_for_status()
                
                data = response.json().get('Table', [])
                
                if not data:
                    break
                
                for item in data:
                    subject = item.get('NEWSSUB', '').lower()
                    attachment = item.get('ATTACHMENTNAME', '')
                    
                    if not attachment:
                        continue
                    
                    # Filter for earnings/results/conference
                    if any(kw in subject for kw in ['earnings', 'results', 'conference', 'investor', 'concall']):
                        url_full = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{urllib.parse.quote(attachment)}"
                        if url_full not in seen_urls:
                            announcements.append({
                                'NEWSSUB': item.get('NEWSSUB', ''),
                                'NEWS_DT': item.get('NEWS_DT', ''),
                                'ATTACHMENTNAME': attachment,
                                'url': url_full,
                            })
                            seen_urls.add(url_full)
                
                time.sleep(0.8)
        
        except Exception as e:
            print(f"     Error on keyword '{keyword}': {str(e)[:40]}")
            continue
    
    return announcements

def get_moneycontrol_transcript(symbol, session=None):
    """Fetch transcripts from Moneycontrol."""
    if session is None:
        session = get_session_with_retries()
    
    try:
        # Moneycontrol URL pattern - convert symbol to lowercase
        url = f"https://www.moneycontrol.com/news/business/{symbol.lower()}-earnings.html"
        
        response = session.get(
            url,
            headers=get_rotating_headers('https://www.moneycontrol.com'),
            timeout=15,
        )
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for transcript links
            for link in soup.find_all('a'):
                href = link.get('href', '')
                text = link.get_text(strip=True).lower()
                
                if 'transcript' in text or 'earnings' in text:
                    if href.startswith('/'):
                        href = 'https://www.moneycontrol.com' + href
                    return href
        
        time.sleep(1)
    except Exception as e:
        pass
    
    return None

def main():
    """Main execution."""
    print("=" * 70)
    print("🚀 ENHANCED BSE TRANSCRIPT SCRAPER v3")
    print("=" * 70)
    print(f"⏰ Started: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    os.makedirs("transcripts", exist_ok=True)
    
    # Companies to process with their BSE scrip codes
    companies = [
        {'symbol': 'RELIANCE', 'name': 'Reliance Industries', 'scrip': '500325'},
        {'symbol': 'TCS', 'name': 'Tata Consultancy Services', 'scrip': '532540'},
        {'symbol': 'INFY', 'name': 'Infosys', 'scrip': '500209'},
        {'symbol': 'WIPRO', 'name': 'Wipro', 'scrip': '500330'},
        {'symbol': 'HDFC', 'name': 'HDFC', 'scrip': '500010'},
        {'symbol': 'HDFCBANK', 'name': 'HDFC Bank', 'scrip': '500180'},
        {'symbol': 'ICICIBANK', 'name': 'ICICI Bank', 'scrip': '532174'},
        {'symbol': 'KOTAKBANK', 'name': 'Kotak Bank', 'scrip': '500510'},
        {'symbol': 'AXISBANK', 'name': 'Axis Bank', 'scrip': '532215'},
        {'symbol': 'SBIN', 'name': 'SBI', 'scrip': '500112'},
        {'symbol': 'BAJAJFINSV', 'name': 'Bajaj Finserv', 'scrip': '500034'},
        {'symbol': 'LT', 'name': 'L&T', 'scrip': '500510'},
        {'symbol': 'MARUTI', 'name': 'Maruti Suzuki', 'scrip': '532500'},
        {'symbol': 'TECHM', 'name': 'Tech Mahindra', 'scrip': '532150'},
        {'symbol': 'BHARTIARTL', 'name': 'Bharti Airtel', 'scrip': '532454'},
        {'symbol': 'SUNPHARMA', 'name': 'Sun Pharma', 'scrip': '500092'},
        {'symbol': 'CIPLA', 'name': 'Cipla', 'scrip': '500087'},
        {'symbol': 'DRREDDYS', 'name': "Dr. Reddy's", 'scrip': '500124'},
        {'symbol': 'BRITANNIA', 'name': 'Britannia', 'scrip': '531242'},
        {'symbol': 'NESTLEIND', 'name': 'Nestle India', 'scrip': '500150'},
    ]
    
    session = get_session_with_retries()
    
    processed_count = 0
    total_files = 0
    
    for idx, company in enumerate(companies, 1):
        symbol = company['symbol']
        name = company['name']
        scrip_code = company['scrip']
        
        folder = f"transcripts/{clean_filename(name)} ({symbol})"
        os.makedirs(folder, exist_ok=True)
        
        print(f"📍 Processing {idx}/{len(companies)}: {symbol:12} | {name[:35]:<35}")
        
        files_downloaded = 0
        sources_found = []
        
        try:
            # ✅ KEY FIX: Use SCRIP CODE, not symbol!
            print(f"   ├─ Checking BSE (Code: {scrip_code})...", end='', flush=True)
            announcements = get_bse_announcements(symbol, scrip_code, session)
            
            if announcements:
                print(f" ✓ Found {len(announcements)}")
                sources_found.append(f"BSE({len(announcements)})")
                
                for ann in announcements[:5]:
                    try:
                        response = session.get(ann['url'], headers=get_rotating_headers(), timeout=20)
                        content_type = response.headers.get('Content-Type', '').lower()
                        
                        text, status = process_downloaded_file(response.content, content_type)
                        
                        if text:
                            date = ann['NEWS_DT'][:10]
                            title = clean_filename(ann['NEWSSUB'][:30])
                            filename = f"{folder}/{date}_{title}_{hash(ann['url'])%10000}.txt"
                            
                            with open(filename, 'w', encoding='utf-8') as f:
                                f.write(f"TITLE: {ann['NEWSSUB']}\nDATE: {date}\nSOURCE: BSE\nURL: {ann['url']}\n\n{text}")
                            
                            files_downloaded += 1
                            print(f"      ✓ Downloaded: {title}")
                        
                        time.sleep(1)
                    except Exception as e:
                        print(f"      ✗ Failed: {str(e)[:30]}")
            else:
                print(f" ✗ No results")
        
        except Exception as e:
            print(f" ✗ Error: {str(e)[:40]}")
        
        # Try Moneycontrol
        try:
            print(f"   └─ Checking Moneycontrol...", end='', flush=True)
            mc_url = get_moneycontrol_transcript(symbol, session)
            if mc_url:
                print(f" ✓ Found")
                sources_found.append("MC")
            else:
                print(f" ✗")
        except:
            print(f" ✗")
        
        # Log
        status = "SUCCESS" if files_downloaded > 0 else "NO_DATA"
        source_str = ", ".join(sources_found) if sources_found else "NONE"
        update_dashboard(scrip_code, name, status, files_downloaded, source_str)
        
        print(f"      Result: {files_downloaded} files\n")
        
        processed_count += 1
        total_files += files_downloaded
        
        time.sleep(2)
    
    print("=" * 70)
    print(f"✓ COMPLETED")
    print(f"  • Processed: {processed_count} companies")
    print(f"  • Downloaded: {total_files} files")
    print("=" * 70)

if __name__ == "__main__":
    main()
