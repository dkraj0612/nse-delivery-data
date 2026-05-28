import os
import json
import time
import random
import logging
import re
from datetime import datetime
from dateutil.relativedelta import relativedelta
from pathlib import Path
from typing import List, Optional, Dict, Any

# Updated imports for the new Google GenAI SDK
from google import genai
from google.genai import types

# ========================= CONFIGURATION =========================
client = genai.Client()
MODEL_ID = 'gemini-2.5-flash-lite' 

OUTPUT_DIR = Path("reports")
PROGRESS_FILE = Path("progress.json")
LOG_FILE = Path("backtest_run.log")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ========================= LOGGING SETUP =========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# ========================= CORE FUNCTIONS =========================
def get_all_dates(years_back: int = 5) -> List[str]:
    """Generates a list of the last calendar days for every month looking backward."""
    end_date = datetime.now()
    start_date = end_date - relativedelta(years=years_back)
    dates = []
    current = start_date
    while current <= end_date:
        last_day = (current + relativedelta(months=1, days=-1)).strftime("%d-%b-%Y")
        dates.append(last_day)
        current += relativedelta(months=1)
    return dates

def load_progress() -> Dict[str, list]:
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logging.error(f"⚠️ {PROGRESS_FILE} is corrupted. Starting fresh.")
    return {"completed": []}

def save_progress(completed_dates: List[str]) -> None:
    temp_file = PROGRESS_FILE.with_suffix('.tmp')
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump({"completed": completed_dates}, f, indent=2)
        os.replace(temp_file, PROGRESS_FILE) 
    except Exception as e:
        logging.error(f"Failed to save progress: {e}")

def get_previous_month_state(current_date_str: str, all_dates: List[str]) -> str:
    """Retrieves the model_portfolio from the previous month's JSON to maintain state."""
    try:
        current_idx = all_dates.index(current_date_str)
        if current_idx == 0:
            return "NONE (This is the inception month. Create a new portfolio based strictly on the current month's data.)"
        
        prev_date_str = all_dates[current_idx - 1]
        prev_file = OUTPUT_DIR / f"{prev_date_str.replace('-', '_')}.json"
        
        if prev_file.exists():
            with open(prev_file, 'r', encoding='utf-8') as f:
                prev_data = json.load(f)
                portfolio_state = prev_data.get("model_portfolio", {})
                return json.dumps(portfolio_state, indent=2)
        else:
            return "ERROR_MISSING_PREVIOUS"
    except Exception as e:
        logging.error(f"Error fetching previous state: {e}")
        return "ERROR_MISSING_PREVIOUS"

def generate_analysis(cutoff_date_str: str, prev_month_data: str) -> Optional[Dict[str, Any]]:
    """Calls Gemini API with exponential backoff and robust JSON parsing."""
    try:
        with open("prompt_template.txt", "r", encoding="utf-8") as f:
            prompt = f.read()
            prompt = prompt.replace("{CUTOFF_DATE}", cutoff_date_str)
            prompt = prompt.replace("{PREVIOUS_MONTH_DATA}", prev_month_data)
    except FileNotFoundError:
        logging.error("🛑 prompt_template.txt not found!")
        return None

    max_retries = 15 
    
    for attempt in range(1, max_retries + 1):
        try:
            logging.info(f"    Requesting Gemini API for {cutoff_date_str} (Attempt {attempt}/{max_retries})...")
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=15000,
                    response_mime_type="application/json"
                )
            )
            
            raw_text = response.text
            
            # FIXED: Dynamically generate markdown ticks to prevent UI parser breaks
            cb = '`' * 3 
            pattern = rf'{cb}(?:json)?\s*(.*?)\s*{cb}'
            json_match = re.search(pattern, raw_text, re.DOTALL)
            text_to_parse = json_match.group(1) if json_match else raw_text.strip()
            
            data = json.loads(text_to_parse)
            logging.info(f"    ✅ Success for {cutoff_date_str}")
            return data
            
        except json.JSONDecodeError as je:
            logging.error(f"    ❌ JSON Decode Error for {cutoff_date_str}: {je}")
            time.sleep(10)
            
        except Exception as e:
            error_str = str(e).lower()
            if any(k in error_str for k in ["rate limit", "429", "quota", "resource exhausted"]):
                wait = min(60 * (2 ** (attempt - 1)), 900) 
                logging.warning(f"    ⚠️ Rate limit hit. Waiting {wait} seconds before retrying...")
                time.sleep(wait)
            else:
                logging.error(f"    ❌ Network/API Error for {cutoff_date_str}: {e}")
                time.sleep(30)
    
    logging.error(f"    🚨 FAILED completely for {cutoff_date_str} after {max_retries} attempts.")
    return None

# ========================= DASHBOARD GENERATOR =========================
def generate_dashboard():
    """Generates the HTML Dashboard by injecting data into dashboard_template.html."""
    logging.info("\n📊 Compiling results and generating Institutional Dashboard...")
    
    compiled_data = {}
    if OUTPUT_DIR.exists():
        for filename in os.listdir(OUTPUT_DIR):
            if filename.endswith(".json"):
                date_key = filename.replace("_", "-").replace(".json", "")
                filepath = OUTPUT_DIR / filename
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        compiled_data[date_key] = json.load(f)
                except Exception as e:
                    logging.error(f"  ⚠️ Error loading {filename}: {e}")

    if not compiled_data:
        logging.warning("  ⚠️ No data found to generate dashboard.")
        return

    json_data_str = json.dumps(compiled_data)

    try:
        with open("dashboard_template.html", "r", encoding="utf-8") as f:
            html_template = f.read()
    except FileNotFoundError:
        logging.error("🛑 dashboard_template.html not found! Cannot build visual dashboard.")
        return

    final_html = html_template.replace("__PYTHON_INJECT_DATA_HERE__", json_data_str)

    with open("dashboard.html", "w", encoding="utf-8") as f:
        f.write(final_html)
        
    logging.info("✅ Successfully created 'dashboard.html'! Open this file in your browser to view.")

# ========================= MAIN SEQUENTIAL LOOP =========================
if __name__ == "__main__":
    logging.info("🚀 Starting CONTINUOUS STATEFUL Indian Market Backtest...")
    
    all_dates = get_all_dates(years_back=5)
    progress = load_progress()
    completed = progress.get("completed", [])
    
    # Process strictly in order to preserve the portfolio's historical chain.
    for date_str in all_dates:
        if date_str in completed:
            continue 
            
        logging.info(f"\n➡️ Processing {date_str}...")
        
        # 1. Get memory state
        prev_state = get_previous_month_state(date_str, all_dates)
        
        if prev_state == "ERROR_MISSING_PREVIOUS":
            logging.error(f"🛑 FATAL: Cannot process {date_str} because the previous month's JSON is missing.")
            logging.error("Data integrity broken. You must restart from the missing month.")
            break 
            
        # 2. Call API (will block and retry automatically if rate limited)
        analysis = generate_analysis(date_str, prev_state)
        
        # 3. Save and Commit
        if analysis:
            filename = OUTPUT_DIR / f"{date_str.replace('-', '_')}.json"
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(analysis, f, indent=2, ensure_ascii=False)
                
            completed.append(date_str)
            save_progress(completed)
            logging.info(f"    💾 Saved state for {date_str}. Integrity maintained.")
            
            # Standard delay to prevent hitting RPM limits immediately
            delay = 35 + random.uniform(5, 10)
            logging.info(f"    Sleeping for {delay:.1f}s...")
            time.sleep(delay)
        else:
            logging.error(f"🛑 API failed to return valid data for {date_str}. Halting backtest to preserve integrity.")
            break

    logging.info("🎉 BACKTEST RUN COMPLETE.")
    
    if len(completed) > 0:
        generate_dashboard()


