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
# 2. INITIALIZE CLIENT & STABLE MODEL CASCADE
# ==============================================================================
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# The 5-Tier Stable Cascade (Including Flash-Lite)
GEMINI_MODEL_CASCADE = [
    'gemini-2.5-pro',            # Tier 1: Heavyweight (Deepest analysis, strict rate limits)
    'gemini-2.5-flash',          # Tier 2: Speedster (Generous free quota, highly capable)
    'gemini-2.5-flash-lite',     # Tier 3: Ultra-Lightweight (Massive rate limits, basic synthesis)
    'gemini-1.5-pro',            # Tier 4: Legacy Heavyweight (Alternate quota bucket)
    'gemini-1.5-flash'           # Tier 5: Legacy Speedster (Ultimate backup)
]

# ==============================================================================
# 3. DIRECTORY & PROMPT LOADER (Absolute Paths)
# ==============================================================================
script_dir = os.path.dirname(os.path.abspath(__file__))
output_dir = os.path.join(script_dir, "forensic_reports")
prompts_dir = os.path.join(script_dir, "Prompts")
status_file = os.path.join(output_dir, "pipeline_status.json")

os.makedirs(output_dir, exist_ok=True)

def load_prompt(filename):
    path = os.path.join(prompts_dir, filename)
    if not os.path.exists(path):
        logger.error(f"CRITICAL: Missing prompt file at {path}. Please create it.")
        exit(1)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def load_stock_queue(filename="target_stocks.txt"):
    path = os.path.join(prompts_dir, filename)
    if not os.path.exists(path):
        logger.error(f"CRITICAL ERROR: {path} not found. Please create it.")
        exit(1)
        
    stocks = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            clean_line = line.strip()
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
    # FIX: Safety check for empty/blocked responses
    if not raw_text:
        raise ValueError("API returned an empty response. (Likely a safety filter block).")
        
    raw_text = raw_text.strip()
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]
    elif raw_text.startswith("```"):
        raw_text = raw_text[3:]
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]
    return raw_text.strip()

# ==============================================================================
# 5. EXECUTION NODE
# ==============================================================================
def generate_institutional_report(stock_name):
    logger.info(f"STARTING: Initiating structured JSON pipeline for: {stock_name}")
    status_tracker["stocks"][stock_name] = "Processing..."
    save_status()
    
    total_models = len(GEMINI_MODEL_CASCADE)
    
    for attempt, current_model in enumerate(GEMINI_MODEL_CASCADE, 1):
        try:
            # ---------------------------------------------------------
            # PASS 1: CORE DATA GENERATION
            # ---------------------------------------------------------
            logger.info(f"[{stock_name}] Stage 1: Web Scrape & Synthesis using [{current_model}] (Tier {attempt}/{total_models})")
            
            master_prompt = system_master_prompt_template.replace("{stock_name}", stock_name)
            
            response = client.models.generate_content(
                model=current_model,
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
            logger.info(f"[{stock_name}] Stage 2: Independent Verification Audit using [{current_model}]")
            
            memory_metadata = json_payload.get("metadata", {})
            memory_kpis = json_payload.get("kpis", {})
            
            val_prompt = validation_prompt_template.format(
                stock_name=stock_name,
                metadata=json.dumps(memory_metadata, indent=2),
                kpis=json.dumps(memory_kpis, indent=2)
            )
            
            val_response = client.models.generate_content(
                model=current_model, 
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
                logger.warning(f"[{stock_name}] Audit failed to parse correctly. Defaulting to FAIL.")
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
            status_tracker["stocks"][stock_name] = f"Completed via {current_model} (Audit: {val_payload.get('status', 'N/A')})"
            status_tracker["completed"] += 1
            save_status()
            
            return # EXIT THE RETRY LOOP UPON SUCCESS
            
        except json.JSONDecodeError as je:
            logger.warning(f"Tier {attempt}/{total_models} FAILED (JSON Error) using [{current_model}] for {stock_name}. Error: {je}")
            if attempt < total_models:
                logger.info(f"Parsing failure. Escaping to next model layer in 15 seconds...")
                time.sleep(15)
            else:
                status_tracker["stocks"][stock_name] = f"Failed (JSON Parse Error on all models)"
                status_tracker["failed"] += 1
                save_status()
                
        except Exception as e:
            logger.warning(f"Tier {attempt}/{total_models} FAILED using [{current_model}] for {stock_name}. Error: {e}")
            if attempt < total_models:
                logger.info(f"API/Network/Quota failure. Escaping to next model layer in 15 seconds...")
                time.sleep(15)
            else:
                status_tracker["stocks"][stock_name] = f"Failed (All Models Exhausted): {str(e)}"
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

    logger.info(f"PIPELINE SUMMARY COMPLETE: {status_tracker['completed']} clean, {status_tracker['failed']} breaks."

