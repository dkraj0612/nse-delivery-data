import os
import time
import json
import logging
from datetime import datetime, timezone
from google import genai
from google.genai import types

# ==============================================================================
# 1. SETUP STRUCTURED LOGGING
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ==============================================================================
# 2. INITIALIZE CLIENT
# ==============================================================================
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# ==============================================================================
# 3. DIRECTORY & PROMPT LOADER (Absolute Paths based on GitHub Structure)
# ==============================================================================
# Get the absolute path of the directory containing this script
script_dir = os.path.dirname(os.path.abspath(__file__))

# Define absolute paths based on the script's location
output_dir = os.path.join(script_dir, "forensic_reports")
prompts_dir = os.path.join(script_dir, "Prompts") # Capital 'P' matching your GitHub
status_file = os.path.join(output_dir, "pipeline_status.json")

# Ensure the output directory exists
os.makedirs(output_dir, exist_ok=True)

def load_prompt(filename):
    path = os.path.join(prompts_dir, filename)
    if not os.path.exists(path):
        logger.error(f"CRITICAL: Missing prompt file at {path}. Please create it.")
        exit(1)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def load_stock_queue(filename="target_stocks.txt"):
    """Reads the stock list from the Prompts folder."""
    path = os.path.join(prompts_dir, filename)
    if not os.path.exists(path):
        logger.error(f"CRITICAL ERROR: {path} not found. Please create it.")
        exit(1)
        
    stocks = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            clean_line = line.strip()
            # Ignore blank lines and comments starting with '#'
            if clean_line and not clean_line.startswith('#'):
                stocks.append(clean_line)
    return stocks

system_master_prompt_template = load_prompt("master_prompt.txt")
validation_prompt_template = load_prompt("validation_prompt.txt")
stock_list = load_stock_queue("target_stocks.txt")

# ==============================================================================
# 4. STATE TRACKER
# ==============================================================================
status_tracker = {
    "last_updated": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
    "total_stocks": len(stock_list),
    "completed": 0,
    "failed": 0,
    "stocks": {stock: "Pending" for stock in stock_list}
}

def save_status():
    status_tracker["last_updated"] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    with open(status_file, 'w', encoding='utf-8') as f:
        json.dump(status_tracker, f, indent=4)

def extract_json_from_text(raw_text):
    """Strips markdown code blocks to safely parse JSON"""
    raw_text = raw_text.strip()
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]
    elif raw_text.startswith("```"):
        raw_text = raw_text[3:]
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]
    return raw_text.strip()

# ==============================================================================
# 5. EXECUTION NODE (With Dual-Scraping Verification)
# ==============================================================================
def generate_institutional_report(stock_name, max_retries=3):
    logger.info(f"STARTING: Initiating structured JSON pipeline for: {stock_name}")
    status_tracker["stocks"][stock_name] = "Processing..."
    save_status()
    
    for attempt in range(1, max_retries + 1):
        try:
            # ---------------------------------------------------------
            # PASS 1: CORE DATA GENERATION
            # ---------------------------------------------------------
            logger.info(f"[{stock_name}] Stage 1: Primary Web Scrape & Synthesis")
            
            # Format the master prompt dynamically
            master_prompt = system_master_prompt_template.replace("{stock_name}", stock_name)
            
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"Execute the 8-module JSON master analysis strictly for: {stock_name}",
                config=types.GenerateContentConfig(
                    system_instruction=master_prompt,
                    temperature=0.1, 
                    tools=[{"google_search": {}}]
                )
            )
            
            clean_text = extract_json_from_text(response.text)
            json_payload = json.loads(clean_text)
            
            # ---------------------------------------------------------
            # PASS 2: VERIFICATION GATE (DUAL SCRAPING)
            # ---------------------------------------------------------
            logger.info(f"[{stock_name}] Stage 2: Independent Verification Audit")
            
            memory_metadata = json_payload.get("metadata", {})
            memory_kpis = json_payload.get("kpis", {})
            
            val_prompt = validation_prompt_template.format(
                stock_name=stock_name,
                metadata=json.dumps(memory_metadata, indent=2),
                kpis=json.dumps(memory_kpis, indent=2)
            )
            
            val_response = client.models.generate_content(
                model='gemini-2.5-pro', 
                contents=val_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0, 
                    tools=[{"google_search": {}}]
                )
            )
            
            clean_val_text = extract_json_from_text(val_response.text)
            try:
                val_payload = json.loads(clean_val_text)
                logger.info(f"[{stock_name}] Audit Result: {val_payload.get('status', 'UNKNOWN')}")
            except Exception as ve:
                logger.warning(f"[{stock_name}] Audit failed to parse correctly. Defaulting to FAIL. {ve}")
                val_payload = {"status": "FAIL", "discrepancies": "Audit JSON parsing failed."}
            
            # INJECT VERIFICATION STATUS
            json_payload["verification"] = val_payload
            
            # ---------------------------------------------------------
            # SAVE FINAL FILE
            # ---------------------------------------------------------
            filename = f"{output_dir}/{stock_name.replace(' ', '_')}_Forensic_Report.json"
            with open(filename, 'w', encoding='utf-8') as file:
                json.dump(json_payload, file, indent=4)
                
            logger.info(f"SUCCESS: JSON data committed cleanly to {filename}")
            status_tracker["stocks"][stock_name] = f"Completed (Audit: {val_payload.get('status', 'N/A')})"
            status_tracker["completed"] += 1
            save_status()
            return 
            
        except json.JSONDecodeError as je:
            logger.warning(f"Attempt {attempt}/{max_retries} FAILED (JSON Parse Error) for {stock_name}. Error: {je}")
            if attempt < max_retries:
                time.sleep(30 * attempt)
            else:
                status_tracker["stocks"][stock_name] = f"Failed (JSON Parse Error)"
                status_tracker["failed"] += 1
                save_status()
                
        except Exception as e:
            logger.warning(f"Attempt {attempt}/{max_retries} FAILED for {stock_name}. Error: {e}")
            if attempt < max_retries:
                time.sleep(30 * attempt)
            else:
                status_tracker["stocks"][stock_name] = f"Failed: {str(e)}"
                status_tracker["failed"] += 1
                save_status()

# ==============================================================================
# 6. WORKFLOW MAIN LOOP
# ==============================================================================
if __name__ == "__main__":
    logger.info(f"PIPELINE INITIATED: Loaded {len(stock_list)} nodes into JSON queue.")
    save_status()

    for idx, stock in enumerate(stock_list, 1):
        logger.info(f"--- Processing {idx}/{len(stock_list)} ---")
        generate_institutional_report(stock)
        
        if idx < len(stock_list):
            logger.info("Enforcing 30-second rate-limit cooling index...")
            time.sleep(30)

    logger.info(f"PIPELINE SUMMARY COMPLETE: {status_tracker['completed']} clean, {status_tracker['failed']} breaks.")



