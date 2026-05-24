"""
🚀 EARNINGS TRANSCRIPT SCRAPER - GITHUB ACTIONS COMPATIBLE
============================================================
Uses Playwright (lightweight, works in GitHub Actions)
Auto-downloads browsers, handles JavaScript rendering
"""

import asyncio
import os
import time
import re
import fitz
import pytz
import zipfile
from datetime import datetime
from io import BytesIO

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Installing Playwright...")
    os.system("pip install playwright")
    os.system("playwright install")
    from playwright.async_api import async_playwright

IST = pytz.timezone('Asia/Kolkata')

COMPANIES = [
    ('RELIANCE', 'Reliance Industries'),
    ('TCS', 'Tata Consultancy Services'),
    ('INFY', 'Infosys'),
    ('WIPRO', 'Wipro'),
    ('HDFC', 'HDFC'),
    ('HDFCBANK', 'HDFC Bank'),
    ('ICICIBANK', 'ICICI Bank'),
    ('KOTAKBANK', 'Kotak Bank'),
    ('AXISBANK', 'Axis Bank'),
    ('SBIN', 'SBI'),
    ('BAJAJFINSV', 'Bajaj Finserv'),
    ('LT', 'L&T'),
    ('MARUTI', 'Maruti Suzuki'),
    ('TECHM', 'Tech Mahindra'),
    ('BHARTIARTL', 'Bharti Airtel'),
    ('SUNPHARMA', 'Sun Pharma'),
    ('CIPLA', 'Cipla'),
    ('DRREDDYS', "Dr. Reddy's"),
    ('BRITANNIA', 'Britannia'),
    ('NESTLEIND', 'Nestle India'),
]

MONEYCONTROL_URLS = {
    'RELIANCE': 'https://www.moneycontrol.com/company/reliance-industries/',
    'TCS': 'https://www.moneycontrol.com/company/tcs/',
    'INFY': 'https://www.moneycontrol.com/company/infosys/',
    'WIPRO': 'https://www.moneycontrol.com/company/wipro/',
    'HDFC': 'https://www.moneycontrol.com/company/hdfc/',
    'HDFCBANK': 'https://www.moneycontrol.com/company/hdfc-bank/',
    'ICICIBANK': 'https://www.moneycontrol.com/company/icici-bank/',
    'KOTAKBANK': 'https://www.moneycontrol.com/company/kotak-bank/',
    'AXISBANK': 'https://www.moneycontrol.com/company/axis-bank/',
    'SBIN': 'https://www.moneycontrol.com/company/sbi/',
    'BAJAJFINSV': 'https://www.moneycontrol.com/company/bajaj-finserv/',
    'LT': 'https://www.moneycontrol.com/company/larsen-toubro/',
    'MARUTI': 'https://www.moneycontrol.com/company/maruti-suzuki/',
    'TECHM': 'https://www.moneycontrol.com/company/tech-mahindra/',
    'BHARTIARTL': 'https://www.moneycontrol.com/company/bharti-airtel/',
    'SUNPHARMA': 'https://www.moneycontrol.com/company/sun-pharma/',
    'CIPLA': 'https://www.moneycontrol.com/company/cipla/',
    'DRREDDYS': 'https://www.moneycontrol.com/company/dr-reddys/',
    'BRITANNIA': 'https://www.moneycontrol.com/company/britannia/',
    'NESTLEIND': 'https://www.moneycontrol.com/company/nestle-india/',
}

def extract_pdf_text(pdf_bytes):
    """Extract text from PDF."""
    try:
        if not pdf_bytes.startswith(b'%PDF'):
            return None
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "".join(page.get_text() for page in doc)
        doc.close()
        return text if len(text.strip()) > 300 else None
    except:
        return None

def process_file(content):
    """Process ZIP or PDF."""
    if not content:
        return None
    
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
    
    return extract_pdf_text(content)

def log_result(symbol, name, status, files):
    """Log results."""
    with open("execution_dashboard.log", "a", encoding="utf-8") as f:
        ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{ts}] {symbol:12} | {name[:30]:<30} | {status:<15} | Files: {files}\n")

async def scrape_company(playwright, symbol, name, url, idx, total):
    """Scrape one company using Playwright."""
    
    files_downloaded = 0
    folder = f"transcripts/{name} ({symbol})"
    os.makedirs(folder, exist_ok=True)
    
    browser = None
    
    try:
        print(f"📍 {idx:2d}/{total} | {symbol:12} | {name:35} | ", end="", flush=True)
        
        # Launch browser (headless for GitHub Actions)
        browser = await playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context()
        page = await context.new_page()
        
        # Goto page and wait for content
        await page.goto(url, wait_until='networkidle', timeout=30000)
        
        # Wait for PDF/transcript links to load
        await page.wait_for_selector('a[href*=".pdf"], a[href*="transcript"]', timeout=10000)
        
        # Extract all links that look like transcripts
        links = await page.eval_on_selector_all(
            'a[href*=".pdf"], a[href*="transcript"], a[href*="earnings"], a[href*="results"]',
            'elements => elements.map(el => ({title: el.innerText, url: el.href}))'
        )
        
        if links:
            print(f"Found {len(links):2d} | ", end="", flush=True)
            
            # Download up to 3 transcripts
            for link in links[:3]:
                try:
                    title = link.get('title', 'transcript')
                    pdf_url = link.get('url', '')
                    
                    if not pdf_url:
                        continue
                    
                    # Download the PDF
                    response = await page.goto(pdf_url, wait_until='networkidle')
                    
                    if response and response.ok:
                        content = await response.body()
                        text = process_file(content)
                        
                        if text:
                            filename = f"{folder}/{int(time.time())}.txt"
                            with open(filename, 'w', encoding='utf-8') as f:
                                f.write(f"TITLE: {title}\n")
                                f.write(f"DATE: {datetime.now(IST).strftime('%Y-%m-%d')}\n")
                                f.write(f"SOURCE: Moneycontrol\n")
                                f.write(f"URL: {pdf_url}\n\n")
                                f.write(text)
                            files_downloaded += 1
                    
                    await asyncio.sleep(1)
                
                except Exception as e:
                    pass
        else:
            print(f"✗ No links found | ", end="", flush=True)
        
        await context.close()
        await browser.close()
        
        print(f"Downloaded {files_downloaded}")
        
        status = "SUCCESS" if files_downloaded > 0 else "NO_DATA"
        log_result(symbol, name, status, files_downloaded)
        
        return files_downloaded
    
    except Exception as e:
        print(f"✗ Error: {str(e)[:30]}")
        log_result(symbol, name, "ERROR", 0)
        return 0
    
    finally:
        if browser:
            await browser.close()

async def main():
    """Main execution."""
    print("=" * 90)
    print("🚀 PLAYWRIGHT-BASED EARNINGS TRANSCRIPT SCRAPER")
    print("=" * 90)
    print(f"Started: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    os.makedirs("transcripts", exist_ok=True)
    
    total_files = 0
    
    async with async_playwright() as playwright:
        for idx, (symbol, name) in enumerate(COMPANIES, 1):
            url = MONEYCONTROL_URLS.get(symbol)
            
            if url:
                files = await scrape_company(playwright, symbol, name, url, idx, len(COMPANIES))
                total_files += files
            
            await asyncio.sleep(2)
    
    print("\n" + "=" * 90)
    print(f"✓ COMPLETED")
    print(f"  • Total files downloaded: {total_files}")
    print(f"  • Completed: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 90)

if __name__ == "__main__":
    asyncio.run(main())
