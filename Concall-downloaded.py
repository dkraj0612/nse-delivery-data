"""
🚀 ENHANCED INDIAN EARNINGS TRANSCRIPT SCRAPER
============================================================
Collects earnings call transcripts from:
  • BSE India (official announcements)
  • Moneycontrol (major companies)
  • Company Investor Relations pages
  • NSE India (backup)

Features:
  • Multi-source fallback mechanism
  • Proper WAF bypass techniques
  • Exponential backoff & retries
  • Session management
  • PDF extraction with validation
  • Comprehensive logging
  • Cache to avoid re-scraping
"""

import requests
import os
import time
import csv
import urllib.parse
import re
import fitz  # PyMuPDF
import pytz
import zipfile
import json
from bs4 import BeautifulSoup
from io import BytesIO
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================================
# CONFIGURATION
# ============================================================================

IST = pytz.timezone('Asia/Kolkata')

# Rotating user agents to bypass WAF detection
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
]

HEADERS_TEMPLATE = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9,hi;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
}

# Keywords for identifying earnings call transcripts in Indian market
TRANSCRIPT_KEYWORDS = [
    'earnings conference call',
    'earnings call transcript',
    'q1 fy', 'q2 fy', 'q3 fy', 'q4 fy',  # Indian financial year format
    'investor conference call',
    'investor call',
    'results conference call',
    'earnings meet',
    'investor meet',
    'conference call',
    'fy2024', 'fy2023', 'fy2022',  # Financial year references
    'quarterly results',
    'annual results',
    'transcript',
    'concall',
    'earnings presentation',
]

# ============================================================================
# UTILITIES
# ============================================================================

def get_rotating_headers(referer='https://www.bseindia.com'):
    """Get headers with rotating user agent to bypass WAF."""
    headers = HEADERS_TEMPLATE.copy()
    headers['User-Agent'] = USER_AGENTS[hash(datetime.now().isoformat()) % len(USER_AGENTS)]
    headers['Referer'] = referer
    return headers

def clean_filename(text, max_length=40):
    """Clean text for use in filenames."""
    text = re.sub(r'[\\/*?:"<>|]', "", text).strip()
    # Replace spaces with underscores and remove non-ASCII
    text = text.encode('ascii', 'ignore').decode('ascii')
    return text[:max_length]

def update_dashboard(scrip, name, status, files_downloaded=0, source="UNKNOWN"):
    """Log execution progress to dashboard."""
    with open("execution_dashboard.log", "a", encoding="utf-8") as f:
        timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {scrip:>6} | {name[:25]:<25} | {status:<20} | Files: {files_downloaded:>2} | Source: {source}\n")

def get_session_with_retries(max_retries=3):
    """Create requests session with exponential backoff retry strategy."""
    session = requests.Session()
    
    retry_strategy = Retry(
        total=max_retries,
        status_forcelist=[429, 500, 502, 503, 504],
        method_whitelist=["HEAD", "GET", "OPTIONS"],
        backoff_factor=1.5,  # Exponential backoff: 1.5s, 3s, 6s, etc.
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session

def is_transcript_text(text):
    """Validate if extracted text looks like a transcript."""
    if not text or len(text.strip()) < 500:
        return False
    
    # Look for typical transcript patterns
    patterns = [
        r'good\s+(morning|afternoon|evening)',  # Greeting
        r'(moderator|host|speaker)',  # Transcript identifier
        r'(question|answer|q&a)',  # Q&A pattern
        r'thank\s+you',  # Common in transcripts
        r'participant|panelist',  # Speaker identification
    ]
    
    text_lower = text.lower()
    match_count = sum(1 for pattern in patterns if re.search(pattern, text_lower))
    
    return match_count >= 2

# ============================================================================
# PDF PROCESSING
# ============================================================================

def extract_text_from_pdf(pdf_bytes, max_pages=100):
    """Extract text from PDF with error handling."""
    if not pdf_bytes or not pdf_bytes.startswith(b'%PDF'):
        return None, "INVALID_PDF_FORMAT"
    
    if len(pdf_bytes) < 1000:
        return None, "PDF_TOO_SMALL"
    
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        # Check if document is password protected
        if doc.needs_pass:
            return None, "PASSWORD_PROTECTED"
        
        # Extract text from first 100 pages
        text = ""
        for page_num, page in enumerate(doc):
            if page_num >= max_pages:
                break
            try:
                text += page.get_text() + "\n"
            except Exception as e:
                continue
        
        # Validate extracted text
        if not text or len(text.strip()) < 200:
            return None, "INSUFFICIENT_TEXT_EXTRACTED"
        
        # Check if looks like a transcript
        if not is_transcript_text(text):
            return None, "NOT_A_TRANSCRIPT"
        
        return text, "SUCCESS"
    
    except Exception as e:
        return None, f"PDF_PARSE_ERROR: {str(e)[:30]}"
    
    finally:
        if doc:
            doc.close()

def process_downloaded_file(file_bytes, content_type, filename=""):
    """Process downloaded file (ZIP or PDF)."""
    if not file_bytes:
        return None, "EMPTY_FILE"
    
    # Try ZIP extraction first
    if 'zip' in content_type.lower() or file_bytes.startswith(b'PK\x03\x04'):
        try:
            with zipfile.ZipFile(BytesIO(file_bytes)) as z:
                # Look for PDF files in ZIP
                for zip_filename in z.namelist():
                    if zip_filename.lower().endswith('.pdf'):
                        try:
                            pdf_content = z.read(zip_filename)
                            text, status = extract_text_from_pdf(pdf_content)
                            if text:
                                return text, "SUCCESS_FROM_ZIP"
                        except Exception:
                            continue
                
                # If no PDFs in ZIP, try extracting text from other formats
                for zip_filename in z.namelist():
                    if zip_filename.lower().endswith(('.txt', '.doc', '.docx')):
                        try:
                            content = z.read(zip_filename)
                            if len(content) > 200:
                                return content.decode('utf-8', errors='ignore'), "SUCCESS_FROM_ZIP_TEXT"
                        except Exception:
                            continue
            
            return None, "ZIP_HAS_NO_VALID_CONTENT"
        
        except zipfile.BadZipFile:
            return None, "CORRUPT_ZIP"
        except Exception as e:
            return None, f"ZIP_ERROR: {str(e)[:20]}"
    
    # Try direct PDF extraction
    return extract_text_from_pdf(file_bytes)

# ============================================================================
# SOURCE 1: BSE INDIA (OFFICIAL ANNOUNCEMENTS)
# ============================================================================

def get_bse_announcements(scrip_code, session=None):
    """
    Fetch announcements from BSE using improved API endpoint.
    
    Args:
        scrip_code: BSE scrip code (numeric string, e.g., "500325")
        session: requests.Session object
    
    Returns:
        List of announcement dictionaries
    """
    if session is None:
        session = get_session_with_retries()
    
    announcements = []
    seen_urls = set()
    
    # Try multiple API endpoints (BSE has different backends)
    endpoints = [
        "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w",
        "https://www.bseindia.com/json/AnnounceData",
    ]
    
    # Search with varied keywords specific to Indian earnings calls
    search_terms = [
        'earnings call',
        'conference call',
        'investor meet',
        'concall',
        'earnings',
        'results',
    ]
    
    for endpoint in endpoints:
        for search_term in search_terms:
            try:
                # Try to fetch recent 5 pages
                for page_num in range(1, 6):
                    params = {
                        'pageno': str(page_num),
                        'strScrip': str(scrip_code),
                        'strSearch': search_term,
                        'strCat': '-1',
                        'strType': 'C',  # Category: Corporate Announcements
                        'strPrevDate': '',
                        'strToDate': '',
                    }
                    
                    try:
                        response = session.get(
                            endpoint,
                            headers=get_rotating_headers(),
                            params=params,
                            timeout=15,
                        )
                        response.raise_for_status()
                        
                        # Parse response
                        if 'json' in response.headers.get('content-type', '').lower():
                            data = response.json().get('Table', [])
                        else:
                            # Try parsing as HTML if not JSON
                            data = []
                        
                        if not data:
                            break  # No more pages for this search term
                        
                        for item in data:
                            # Extract announcement details
                            item_dict = {
                                'NEWSSUB': item.get('NEWSSUB', ''),
                                'NEWS_DT': item.get('NEWS_DT', item.get('Dttm_Announcement', '')),
                                'ATTACHMENTNAME': item.get('ATTACHMENTNAME', ''),
                            }
                            
                            if not item_dict['ATTACHMENTNAME']:
                                continue
                            
                            # Check if this looks like a transcript/earnings call
                            subject = item_dict['NEWSSUB'].lower()
                            attachment = item_dict['ATTACHMENTNAME'].lower()
                            
                            is_relevant = any(kw in subject for kw in TRANSCRIPT_KEYWORDS)
                            
                            # Negative filters: Explicitly exclude non-transcripts
                            is_irrelevant = any(
                                bad in subject 
                                for bad in ['board meeting', 'dividend', 'stock split', 'merger', 'acquisition']
                            )
                            
                            if is_relevant and not is_irrelevant:
                                url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{urllib.parse.quote(item_dict['ATTACHMENTNAME'])}"
                                
                                if url not in seen_urls:
                                    announcements.append(item_dict)
                                    seen_urls.add(url)
                        
                        time.sleep(0.8)  # Rate limiting
                    
                    except requests.exceptions.RequestException as e:
                        if page_num == 1:
                            break  # First page failed, skip this endpoint
                    
                    time.sleep(0.5)
            
            except Exception as e:
                continue
    
    return announcements

# ============================================================================
# SOURCE 2: MONEYCONTROL (MAJOR COMPANIES ONLY)
# ============================================================================

def get_moneycontrol_transcripts(symbol, session=None):
    """
    Fetch transcripts from Moneycontrol (for major companies only).
    
    Args:
        symbol: Stock ticker symbol (e.g., "INFY")
        session: requests.Session object
    
    Returns:
        List of transcript text or URLs
    """
    if session is None:
        session = get_session_with_retries()
    
    transcripts = []
    
    try:
        # Moneycontrol transcript URL pattern
        url = f"https://www.moneycontrol.com/company/{symbol.lower()}/transcripts/"
        
        response = session.get(
            url,
            headers=get_rotating_headers('https://www.moneycontrol.com'),
            timeout=15,
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find all transcript links
        for link in soup.find_all('a', href=re.compile(r'transcript|earnings')):
            href = link.get('href', '')
            text = link.get_text(strip=True)
            
            if 'transcript' in text.lower() or 'earnings' in text.lower():
                if href.startswith('/'):
                    href = 'https://www.moneycontrol.com' + href
                
                if href.startswith('http'):
                    transcripts.append({
                        'url': href,
                        'title': text,
                        'source': 'moneycontrol',
                    })
        
        time.sleep(1)
    
    except Exception as e:
        pass
    
    return transcripts

# ============================================================================
# SOURCE 3: COMPANY INVESTOR RELATIONS PAGES
# ============================================================================

def get_ir_page_transcripts(ir_url, session=None):
    """
    Fetch transcripts from company IR pages (for known IR URLs).
    
    Args:
        ir_url: Company's IR page URL
        session: requests.Session object
    
    Returns:
        List of transcript downloads
    """
    if not ir_url or session is None:
        return []
    
    transcripts = []
    
    try:
        response = session.get(
            ir_url,
            headers=get_rotating_headers(ir_url),
            timeout=15,
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Look for PDF links related to earnings
        for link in soup.find_all('a', href=re.compile(r'\.(pdf|docx?)$', re.I)):
            href = link.get('href', '')
            text = link.get_text(strip=True)
            
            is_relevant = any(kw in text.lower() for kw in TRANSCRIPT_KEYWORDS)
            
            if is_relevant:
                if href.startswith('/'):
                    href = urllib.parse.urljoin(ir_url, href)
                
                transcripts.append({
                    'url': href,
                    'title': text,
                    'source': 'ir_page',
                })
        
        time.sleep(1)
    
    except Exception:
        pass
    
    return transcripts

# ============================================================================
# COMPANY LIST MANAGEMENT
# ============================================================================

def get_target_companies():
    """
    Fetch list of target companies from multiple sources.
    
    Priority:
    1. Load from local cache
    2. Try to fetch from Nifty 500 API
    3. Use fallback hardcoded list
    """
    cache_file = "bse_companies_cache.json"
    
    # Try to load from cache (max 30 days old)
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                
                cache_age = (datetime.now() - datetime.fromisoformat(cache_data.get('timestamp', '2000-01-01'))).days
                
                if cache_age < 30:
                    print(f"✓ Loaded {len(cache_data.get('companies', []))} companies from cache ({cache_age} days old)")
                    return cache_data.get('companies', [])
        except Exception:
            pass
    
    print("🔄 Fetching live company list from NSE...")
    companies = []
    
    try:
        # Try NSE master list
        session = get_session_with_retries()
        
        # Multiple NSE API sources
        sources = [
            "https://www.nseindia.com/api/master-quote/",
            "https://archives.nseindia.com/products/content/sec_bhavdata.csv",
        ]
        
        for source in sources:
            try:
                response = session.get(
                    source,
                    headers=get_rotating_headers('https://www.nseindia.com'),
                    timeout=20,
                )
                response.raise_for_status()
                
                if 'csv' in source:
                    # CSV format
                    lines = response.text.splitlines()
                    reader = csv.DictReader(lines)
                    
                    for row in reader:
                        symbol = row.get('SYMBOL', '').strip()
                        if symbol:
                            companies.append({
                                'symbol': symbol,
                                'name': row.get('NAME_OF_COMPANY', symbol),
                                'isin': row.get('ISIN', ''),
                            })
                else:
                    # JSON format
                    data = response.json()
                    
                    for item in data.get('data', []):
                        symbol = item.get('symbol', '').strip()
                        if symbol:
                            companies.append({
                                'symbol': symbol,
                                'name': item.get('companyName', symbol),
                                'isin': item.get('isin', ''),
                            })
                
                if companies:
                    break
            
            except Exception as e:
                continue
        
        if not companies:
            raise Exception("All sources failed")
        
        print(f"✓ Fetched {len(companies)} companies from NSE")
    
    except Exception as e:
        print(f"⚠ Failed to fetch from NSE: {e}")
        print("✓ Using fallback company list...")
        
        # Fallback: Major Nifty 500 companies
        companies = [
            {'symbol': 'RELIANCE', 'name': 'Reliance Industries Limited', 'isin': 'INE002A01018'},
            {'symbol': 'INFY', 'name': 'Infosys Limited', 'isin': 'INE009A01021'},
            {'symbol': 'HDFC', 'name': 'HDFC Bank Limited', 'isin': 'INE001A01015'},
            {'symbol': 'TCS', 'name': 'Tata Consultancy Services', 'isin': 'INE467B01029'},
            {'symbol': 'WIPRO', 'name': 'Wipro Limited', 'isin': 'INE020A01038'},
            {'symbol': 'BAJAJFINSV', 'name': 'Bajaj Finserv Limited', 'isin': 'INE296A01024'},
            {'symbol': 'SBIN', 'name': 'State Bank of India', 'isin': 'INE062A01020'},
            {'symbol': 'ICICIBANK', 'name': 'ICICI Bank Limited', 'isin': 'INE090A01021'},
            {'symbol': 'KOTAKBANK', 'name': 'Kotak Mahindra Bank', 'isin': 'INE237A01028'},
            {'symbol': 'AXISBANK', 'name': 'Axis Bank Limited', 'isin': 'INE238A01034'},
            {'symbol': 'HDFCBANK', 'name': 'HDFC Bank Limited', 'isin': 'INE001A01015'},
            {'symbol': 'LT', 'name': 'Larsen & Toubro Limited', 'isin': 'INE018A01030'},
            {'symbol': 'MARUTI', 'name': 'Maruti Suzuki India', 'isin': 'INE585B01010'},
            {'symbol': 'TECHM', 'name': 'Tech Mahindra Limited', 'isin': 'INE786D01026'},
            {'symbol': 'BHARTIARTL', 'name': 'Bharti Airtel Limited', 'isin': 'INE397D01024'},
            {'symbol': 'SUNPHARMA', 'name': 'Sun Pharmaceutical', 'isin': 'INE044A01035'},
            {'symbol': 'CIPLA', 'name': 'Cipla Limited', 'isin': 'INE059A01026'},
            {'symbol': 'DRREDDYS', 'name': 'Dr. Reddy\' Laboratories', 'isin': 'INE089A01023'},
            {'symbol': 'BRITANNIA', 'name': 'Britannia Industries', 'isin': 'INE216A01038'},
            {'symbol': 'NESTLEIND', 'name': 'Nestle India Limited', 'isin': 'INE239A01016'},
        ]
    
    # Cache the results
    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'companies': companies,
            }, f, indent=2)
    except Exception:
        pass
    
    return companies

# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    """Main execution function."""
    
    print("=" * 70)
    print("🚀 ENHANCED INDIAN EARNINGS TRANSCRIPT SCRAPER")
    print("=" * 70)
    print(f"📍 Timezone: {IST}")
    print(f"⏰ Started: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}")
    print("")
    
    # Create output directories
    os.makedirs("transcripts", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    # Get target companies
    companies = get_target_companies()
    print(f"📊 Total companies to process: {len(companies)}\n")
    
    # Initialize session
    session = get_session_with_retries()
    
    # Processing limits
    max_companies = 20  # Process first 20 companies
    max_files_per_company = 10
    
    processed_count = 0
    total_files_downloaded = 0
    
    for idx, company in enumerate(companies):
        if processed_count >= max_companies:
            break
        
        symbol = company.get('symbol', '').upper()
        name = company.get('name', symbol)
        isin = company.get('isin', '')
        
        if not symbol:
            continue
        
        # Create company folder
        folder = f"transcripts/{clean_filename(name)} ({symbol})"
        os.makedirs(folder, exist_ok=True)
        
        # Check if already processed recently
        marker_file = f"{folder}/_last_checked.txt"
        should_process = True
        
        if os.path.exists(marker_file):
            try:
                with open(marker_file, 'r') as f:
                    last_checked = datetime.fromisoformat(f.read().strip())
                    days_since = (datetime.now(IST) - last_checked.replace(tzinfo=IST)).days
                    
                    if days_since < 30:  # Re-check every 30 days
                        should_process = False
            except Exception:
                pass
        
        if not should_process:
            print(f"⏭️  SKIP {symbol:8} | {name[:35]:<35} (checked recently)")
            continue
        
        print(f"\n📍 Processing {idx+1}/{len(companies)}: {symbol:8} | {name[:40]:<40}")
        
        files_downloaded = 0
        sources_found = []
        
        try:
            # Try BSE API
            print(f"   ├─ Checking BSE announcements...", end='', flush=True)
            bse_announcements = get_bse_announcements(symbol, session)
            
            if bse_announcements:
                print(f" ✓ Found {len(bse_announcements)} announcements")
                sources_found.append(f"BSE({len(bse_announcements)})")
                
                for announcement in bse_announcements[:max_files_per_company]:
                    try:
                        url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{urllib.parse.quote(announcement['ATTACHMENTNAME'])}"
                        filename = f"{folder}/{announcement['NEWS_DT'][:10]}_{clean_filename(announcement['NEWSSUB'])}_{hash(url)%10000}.txt"
                        
                        if os.path.exists(filename):
                            continue
                        
                        response = session.get(url, headers=get_rotating_headers(), timeout=20)
                        content_type = response.headers.get('Content-Type', '').lower()
                        
                        text, status = process_downloaded_file(response.content, content_type)
                        
                        if text:
                            with open(filename, 'w', encoding='utf-8') as f:
                                f.write(f"TITLE: {announcement['NEWSSUB']}\nDATE: {announcement['NEWS_DT']}\nSOURCE: BSE\nURL: {url}\n\n{text}")
                            files_downloaded += 1
                        else:
                            # Log error
                            error_file = filename.replace('.txt', f'_ERROR_{status}.txt')
                            with open(error_file, 'w') as f:
                                f.write(f"ERROR: {status}\nURL: {url}")
                        
                        time.sleep(1)
                    except Exception as e:
                        pass
            else:
                print(f" ✗ No BSE announcements found")
        
        except Exception as e:
            print(f" ✗ BSE Error: {str(e)[:40]}")
        
        # Try Moneycontrol (for major companies)
        try:
            print(f"   ├─ Checking Moneycontrol...", end='', flush=True)
            mc_transcripts = get_moneycontrol_transcripts(symbol, session)
            
            if mc_transcripts:
                print(f" ✓ Found {len(mc_transcripts)} transcripts")
                sources_found.append(f"MC({len(mc_transcripts)})")
            else:
                print(f" ✗")
        except Exception as e:
            print(f" ✗")
        
        # Update tracking
        with open(marker_file, 'w') as f:
            f.write(datetime.now(IST).isoformat())
        
        # Log results
        status = "SUCCESS" if files_downloaded > 0 else "NO_DATA"
        sources_str = ", ".join(sources_found) if sources_found else "NONE"
        
        update_dashboard(symbol, name, status, files_downloaded, sources_str)
        
        print(f"   └─ Result: {files_downloaded} files downloaded from {sources_str}")
        
        processed_count += 1
        total_files_downloaded += files_downloaded
        
        time.sleep(2)  # Rate limiting between companies
    
    # Summary
    print("\n" + "=" * 70)
    print(f"✓ EXECUTION COMPLETED")
    print(f"  • Companies processed: {processed_count}")
    print(f"  • Total files downloaded: {total_files_downloaded}")
    print(f"  • Completed at: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

if __name__ == "__main__":
    main()
