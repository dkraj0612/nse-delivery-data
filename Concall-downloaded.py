import requests
import os
import time
from datetime import datetime, timedelta
import pandas as pd
import pdfplumber

# ================== CONFIG ==================
START_DATE = "20200101"
END_DATE = "20260524"
BASE_FOLDER = "bse_results_transcripts_text"
BATCH_MONTHS = 1
DELAY = 1.5
# ===========================================

# Create folder structure
metadata_folder = os.path.join(BASE_FOLDER, "metadata")
companies_folder = os.path.join(BASE_FOLDER, "Companies")
os.makedirs(metadata_folder, exist_ok=True)
os.makedirs(companies_folder, exist_ok=True)

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bseindia.com/"
}

session = requests.Session()

def is_relevant_announcement(ann):
    headline = str(ann.get('headline', '')).lower()
    subject = str(ann.get('subject', '')).lower()
    text = headline + " " + subject
    
    if any(kw in text for kw in ["result", "financial result", "quarterly", "annual result", "audited"]):
        return "Results"
    if any(kw in text for kw in ["transcript", "earnings call", "concall", "conference call", "investor meet transcript"]):
        return "Transcript"
    return None

def extract_text_from_pdf(pdf_content):
    text = ""
    try:
        with pdfplumber.open(pdf_content) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"
                tables = page.extract_tables()
                if tables:
                    text += "\n--- TABLE ---\n"
                    for table in tables:
                        text += str(table) + "\n\n"
    except Exception as e:
        text = f"Extraction Error: {str(e)}"
    return text

def process_pdf(pdf_url, company_name, category, date_str, headline):
    try:
        response = session.get(pdf_url, headers=headers, timeout=30)
        if response.status_code != 200:
            return False

        text = extract_text_from_pdf(response.content)

        # Clean company name for folder
        clean_company = "".join(c if c.isalnum() or c in " _-" else "_" for c in company_name)[:80]
        company_folder = os.path.join(companies_folder, clean_company)
        cat_folder = os.path.join(company_folder, category)
        os.makedirs(cat_folder, exist_ok=True)

        # Filename starts with Company Name
        clean_headline = "".join(c if c.isalnum() or c in " _-" else "_" for c in headline)[:100]
        filename = f"{clean_company}_{date_str}_{category}_{clean_headline}.txt"
        txt_path = os.path.join(cat_folder, filename)

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"Company: {company_name}\n")
            f.write(f"Date: {date_str}\n")
            f.write(f"Category: {category}\n")
            f.write(f"Headline: {headline}\n")
            f.write("="*80 + "\n\n")
            f.write(text)

        print(f"✓ Saved: {clean_company} / {category} / {filename}")
        return True
    except Exception as e:
        print(f"✗ Failed {company_name}: {str(e)}")
        return False

def fetch_announcements(start_str, end_str, page=1):
    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
    params = {
        "pageno": page, "strCat": "-1", "strPrevDate": start_str,
        "strToDate": end_str, "strScrip": "", "strSearch": "P"
    }
    try:
        resp = session.get(url, params=params, headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.json().get('Table', [])
    except:
        pass
    return []

def process_batch(start_date, end_date):
    print(f"\n=== Processing {start_date} to {end_date} ===")
    all_data = []
    page = 1

    while True:
        announcements = fetch_announcements(start_date, end_date, page)
        if not announcements:
            break

        relevant = [ann for ann in announcements if (cat := is_relevant_announcement(ann))]
        for ann in relevant:
            ann['filtered_category'] = cat
