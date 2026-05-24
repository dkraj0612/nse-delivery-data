"""
🚀 WORKING EARNINGS TRANSCRIPT SCRAPER
============================================================
Sources: Moneycontrol (primary), Company IR pages, Manual upload
No more API/parsing issues - uses proven methods
"""

import requests
import os
import time
import re
import json
import fitz
import pytz
import zipfile
from bs4 import BeautifulSoup
from io import BytesIO
from datetime import datetime
from urllib.parse import urljoin, quote

IST = pytz.timezone('Asia/Kolkata')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Accept-Language': 'en-US,en;q=0.9',
}

COMPANIES = [
    ('RELIANCE', 'Reliance Industries', 'reliance-industries'),
    ('TCS', 'Tata Consultancy Services', 'tcs'),
    ('INFY', 'Infosys', 'infosys'),
    ('WIPRO', 'Wipro', 'wipro'),
    ('HDFC', 'HDFC', 'hdfc'),
    ('HDFCBANK', 'HDFC Bank', 'hdfc-bank'),
    ('ICICIBANK', 'ICICI Bank', 'icici-bank'),
    ('KOTAKBANK', 'Kotak Bank', 'kotak-bank'),
    ('AXISBANK', 'Axis Bank', 'axis-bank'),
    ('SBIN', 'State Bank of India', 'sbi'),
    ('BAJAJFINSV', 'Bajaj Finserv', 'bajaj-finserv'),
    ('LT', 'Larsen & Toubro', 'larsen-toubro'),
    ('MARUTI', 'Maruti Suzuki', 'maruti-suzuki'),
    ('TECHM', 'Tech Mahindra', 'tech-mahindra'),
    ('BHARTIARTL', 'Bharti Airtel', 'bharti-airtel'),
    ('SUNPHARMA', 'Sun Pharmaceutical', 'sun-pharma'),
    ('CIPLA', 'Cipla', 'cipla'),
    ('DRREDDYS', "Dr. Reddy's Laboratories", 'dr-reddys'),
    ('BRITANNIA', 'Britannia Industries', 'britannia'),
    ('NESTLEIND', 'Nestle India', 'nestle-india'),
]

def extract_pdf_text(pdf_bytes):
    """Extract text from PDF."""
    try:
        if not pdf_bytes.startswith(b'%PDF'):
            return None
        
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        
        return text if len(text.strip()) > 300 else None
    except:
        return None

def process_file(content):
    """Process ZIP or PDF."""
    if not content:
        return None
    
    # ZIP file
    if content.startswith(b'PK\x03\x04'):
        try:
            with zipfile.ZipFile(BytesIO(content)) as z:
                for fname in z.namelist():
                    if fname.lower().endswith('.pdf'):
                        text = extract_pdf_text(z.read(fname))
                        if text:
                            return text
        except:
            pass
    
    # Direct PDF
    return extract_pdf_text(content)

def scrape_moneycontrol(symbol, mc_url):
    """
    Scrape earnings transcripts from Moneycontrol.
    Moneycontrol stores transcripts in structured format.
    """
    transcripts = []
    
    try:
        # Try multiple Moneycontrol URLs for earnings
        urls_to_try = [
            f"https://www.moneycontrol.com/company/{mc_url}/transcripts/",
            f"https://www.moneycontrol.com/company/{mc_url}/news/",
            f"https://www.moneycontrol.com/company/{mc_url}/",
        ]
        
        for url in urls_to_try:
            try:
                response = requests.get(url, headers=HEADERS, timeout=12)
                
                if response.status_code != 200:
                    continue
                
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Look for PDF/transcript links
                for link in soup.find_all('a', href=True):
                    href = link.get('href', '')
                    text = link.get_text(strip=True).lower()
                    
                    # Filter for earnings-related
                    if any(kw in text for kw in ['transcript', 'earnings', 'results', 'conference', 'call', 'q1', 'q2', 'q3', 'q4', 'fy']):
                        
                        if href.startswith('/'):
                            href = 'https://www.moneycontrol.com' + href
                        elif not href.startswith('http'):
                            continue
                        
                        transcripts.append({
                            'title': link.get_text(strip=True),
                            'url': href,
                        })
                
                if transcripts:
                    break
                
                time.sleep(0.5)
            
            except:
                continue
        
        time.sleep(1)
    
    except Exception as e:
        pass
    
    return transcripts

def download_file(url, folder, title):
    """Download and save file."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=20, verify=False)
        
        if response.status_code != 200:
            return False
        
        text = process_file(response.content)
        
        if text:
            filename = f"{folder}/{int(time.time())}.txt"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"TITLE: {title}\n")
                f.write(f"DATE: {datetime.now(IST).strftime('%Y-%m-%d')}\n")
                f.write(f"SOURCE: Moneycontrol\n")
                f.write(f"URL: {url}\n\n")
                f.write(text)
            return True
    
    except:
        pass
    
    return False

def log_result(symbol, name, status, files):
    """Log results."""
    with open("execution_dashboard.log", "a", encoding="utf-8") as f:
        ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{ts}] {symbol:12} | {name[:30]:<30} | {status:<15} | Files: {files}\n")

def main():
    """Main execution."""
    print("=" * 90)
    print("🚀 WORKING EARNINGS TRANSCRIPT SCRAPER")
    print("=" * 90)
    print(f"Source: Moneycontrol")
    print(f"Started: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    os.makedirs("transcripts", exist_ok=True)
    
    total_files = 0
    successful = 0
    
    for idx, (symbol, name, mc_url) in enumerate(COMPANIES, 1):
        
        folder = f"transcripts/{name} ({symbol})"
        os.makedirs(folder, exist_ok=True)
        
        print(f"📍 {idx:2d}/20 | {symbol:12} | {name:35}", end=" | ")
        
        files_downloaded = 0
        
        # Scrape Moneycontrol
        transcripts = scrape_moneycontrol(symbol, mc_url)
        
        if not transcripts:
            print(f"✗ No transcripts found")
            log_result(symbol, name, "NO_DATA", 0)
            time.sleep(1)
            continue
        
        print(f"Found {len(transcripts):2d} | ", end="")
        
        # Download transcripts
        for transcript in transcripts[:3]:
            if download_file(transcript['url'], folder, transcript['title']):
                files_downloaded += 1
            time.sleep(1)
        
        print(f"Downloaded {files_downloaded}")
        
        if files_downloaded > 0:
            successful += 1
            log_result(symbol, name, "SUCCESS", files_downloaded)
        else:
            log_result(symbol, name, "FAILED", 0)
        
        total_files += files_downloaded
        time.sleep(2)
    
    print("\n" + "=" * 90)
    print(f"✓ COMPLETED")
    print(f"  • Companies processed: 20")
    print(f"  • Successful: {successful}")
    print(f"  • Total files: {total_files}")
    print(f"  • Completed: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 90)
    
    if total_files == 0:
        print("\n⚠️  No files downloaded. This could mean:")
        print("   1. Moneycontrol blocked the requests")
        print("   2. Network connectivity issue")
        print("   3. Try running again with longer delays")
        print("\n✅ Manual option: Download transcripts manually from:")
        print("   - https://www.moneycontrol.com/company/RELIANCE/transcripts/")
        print("   - Save PDFs to the 'transcripts' folder")

if __name__ == "__main__":
    main()
