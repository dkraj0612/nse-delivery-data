"""
🚀 BSE TRANSCRIPT SCRAPER - FINAL WORKING VERSION
============================================================
Uses web scraping (HTML parsing) instead of unreliable JSON API
Works by directly accessing BSE website announcements page
"""

import requests
import os
import time
import re
import fitz
import pytz
import zipfile
from bs4 import BeautifulSoup
from io import BytesIO
from datetime import datetime
from urllib.parse import urljoin

IST = pytz.timezone('Asia/Kolkata')

# Company details
COMPANIES = [
    ('RELIANCE', 'Reliance Industries', '500325'),
    ('TCS', 'Tata Consultancy Services', '532540'),
    ('INFY', 'Infosys', '500209'),
    ('WIPRO', 'Wipro', '500330'),
    ('HDFC', 'HDFC', '500010'),
    ('HDFCBANK', 'HDFC Bank', '500180'),
    ('ICICIBANK', 'ICICI Bank', '532174'),
    ('KOTAKBANK', 'Kotak Bank', '500510'),
    ('AXISBANK', 'Axis Bank', '532215'),
    ('SBIN', 'SBI', '500112'),
    ('BAJAJFINSV', 'Bajaj Finserv', '500034'),
    ('LT', 'L&T', '500510'),
    ('MARUTI', 'Maruti Suzuki', '532500'),
    ('TECHM', 'Tech Mahindra', '532150'),
    ('BHARTIARTL', 'Bharti Airtel', '532454'),
    ('SUNPHARMA', 'Sun Pharma', '500092'),
    ('CIPLA', 'Cipla', '500087'),
    ('DRREDDYS', "Dr. Reddy's", '500124'),
    ('BRITANNIA', 'Britannia', '531242'),
    ('NESTLEIND', 'Nestle India', '500150'),
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

def log_result(scrip, name, status, files):
    """Log to dashboard."""
    with open("execution_dashboard.log", "a", encoding="utf-8") as f:
        ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{ts}] {scrip:>6} | {name[:25]:<25} | {status:<15} | Files: {files}\n")

def extract_text_from_pdf(pdf_bytes):
    """Extract text from PDF file."""
    try:
        if not pdf_bytes.startswith(b'%PDF'):
            return None
        
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        
        for page in doc:
            text += page.get_text() + "\n"
        
        doc.close()
        
        if len(text.strip()) > 300:
            return text
    except:
        pass
    
    return None

def process_downloaded_file(content, content_type):
    """Process downloaded file (PDF or ZIP)."""
    if not content:
        return None
    
    # Check if ZIP
    if content.startswith(b'PK\x03\x04'):
        try:
            with zipfile.ZipFile(BytesIO(content)) as z:
                for fname in z.namelist():
                    if fname.lower().endswith('.pdf'):
                        pdf_data = z.read(fname)
                        text = extract_text_from_pdf(pdf_data)
                        if text:
                            return text
        except:
            pass
    
    # Try as PDF
    return extract_text_from_pdf(content)

def fetch_bse_announcements(symbol, scrip_code):
    """
    Fetch announcements from BSE website.
    Uses direct HTML scraping instead of API.
    """
    announcements = []
    
    try:
        # URL: BSE corporate filing search
        url = f"https://www.bseindia.com/corporatesearch/strCorporateAction.aspx"
        
        params = {
            'txtSearch': scrip_code,
        }
        
        response = requests.get(url, headers=HEADERS, params=params, timeout=15)
        
        if response.status_code != 200:
            return announcements
        
        # Parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find all document links
        links_found = 0
        
        for link in soup.find_all('a', href=True):
            href = link.get('href', '')
            link_text = link.get_text(strip=True).lower()
            
            # Filter for earnings/results/conference calls
            if any(kw in link_text for kw in ['earnings', 'results', 'conference', 'call', 'investor meet', 'concall']):
                
                # Build full URL
                if href.startswith('http'):
                    full_url = href
                elif href.startswith('/'):
                    full_url = 'https://www.bseindia.com' + href
                else:
                    full_url = urljoin('https://www.bseindia.com/', href)
                
                # Check if it's a PDF or document link
                if href.endswith('.pdf') or 'pdf' in href.lower() or 'attachment' in href.lower():
                    announcements.append({
                        'title': link_text,
                        'url': full_url,
                        'text': link.get_text(strip=True),
                    })
                    links_found += 1
        
        time.sleep(1)
    
    except Exception as e:
        pass
    
    return announcements

def download_and_save(url, folder, title):
    """Download file from URL and extract text."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=20, verify=False)
        
        if response.status_code != 200:
            return False
        
        content_type = response.headers.get('Content-Type', 'application/octet-stream').lower()
        
        # Process the file
        text = process_downloaded_file(response.content, content_type)
        
        if text:
            # Save to file
            clean_title = re.sub(r'[\\/*?:"<>|]', '', title)[:30]
            filename = f"{folder}/{datetime.now().strftime('%Y%m%d')}_{hash(url)%10000}.txt"
            
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"TITLE: {title}\n")
                f.write(f"DATE: {datetime.now(IST).strftime('%Y-%m-%d')}\n")
                f.write(f"SOURCE: BSE\n")
                f.write(f"URL: {url}\n\n")
                f.write(text)
            
            return True
    
    except Exception as e:
        pass
    
    return False

def main():
    """Main execution."""
    print("=" * 80)
    print("🚀 BSE TRANSCRIPT SCRAPER - FINAL PRODUCTION VERSION")
    print("=" * 80)
    print(f"⏰ Started: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    os.makedirs("transcripts", exist_ok=True)
    
    total_files = 0
    
    for idx, (symbol, name, scrip) in enumerate(COMPANIES, 1):
        
        # Create company folder
        folder = f"transcripts/{name} ({symbol})"
        os.makedirs(folder, exist_ok=True)
        
        print(f"📍 {idx:2d}/20 | {symbol:12} | {name:30}", end="")
        
        files_downloaded = 0
        
        # Fetch announcements
        announcements = fetch_bse_announcements(symbol, scrip)
        
        if not announcements:
            print(f" | ✗ No announcements")
            log_result(scrip, name, "NO_DATA", 0)
            time.sleep(1)
            continue
        
        print(f" | Found {len(announcements):2d} | ", end="")
        
        # Download each announcement
        for ann in announcements[:5]:  # Max 5 per company
            if download_and_save(ann['url'], folder, ann['title']):
                files_downloaded += 1
        
        print(f"Downloaded {files_downloaded:2d}")
        
        log_result(scrip, name, "SUCCESS" if files_downloaded > 0 else "PARTIAL", files_downloaded)
        
        total_files += files_downloaded
        
        time.sleep(2)
    
    print("\n" + "=" * 80)
    print(f"✓ COMPLETED")
    print(f"  • Companies: 20")
    print(f"  • Total files downloaded: {total_files}")
    print(f"  • Completed at: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

if __name__ == "__main__":
    main()
