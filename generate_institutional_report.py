import os
import time
import json
import logging
from datetime import datetime, timezone
from google import genai
from google.genai import types

# --- 1. SETUP STRUCTURED LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- 2. INITIALIZE CLIENT & DIRECTORIES ---
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
output_dir = "forensic_reports"
os.makedirs(output_dir, exist_ok=True)

# Define the status tracking file path
status_file = os.path.join(output_dir, "pipeline_status.json")

# --- 3. DEFINE THE TARGET PIPELINE ---
stock_list = [
    "Lumax Auto", 
    "Acutaas Chemicals", 
    "Bliss GVS Pharma", 
    "Maithan Alloys"
    # Insert remaining stocks here
]

# --- 4. INITIALIZE JSON STATE TRACKER ---
status_tracker = {
    "last_updated": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
    "total_stocks": len(stock_list),
    "completed": 0,
    "failed": 0,
    "stocks": {stock: "Pending" for stock in stock_list}
}

def save_status():
    """Writes the current pipeline state to a JSON file for monitoring."""
    status_tracker["last_updated"] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    with open(status_file, 'w', encoding='utf-8') as f:
        json.dump(status_tracker, f, indent=4)

# --- 5. THE AI MASTER PROMPT ---
system_master_prompt = """
[PASTE YOUR ENTIRE 8-MODULE MASTER PROMPT HERE]
Do not ask for permission to proceed. Generate the complete 8-module report for the provided stock.
"""

def generate_institutional_report(stock_name):
    logger.info(f"STARTING: Initiating forensic web-scraping and analysis for: {stock_name}")
    
    # Update JSON state to processing
    status_tracker["stocks"][stock_name] = "Processing..."
    save_status()
    
    try:
        response = client.models.generate_content(
            model='gemini-3.1-pro', 
            contents=f"Execute the master analysis strictly for this stock: {stock_name}",
            config=types.GenerateContentConfig(
                system_instruction=system_master_prompt,
                temperature=0.2, 
                tools=[{"google_search": {}}] 
            )
        )
        
        filename = f"{output_dir}/{stock_name.replace(' ', '_')}_Forensic_Report.md"
        with open(filename, 'w', encoding='utf-8') as file:
            file.write(response.text)
            
        logger.info(f"SUCCESS: Report saved to {filename}")
        
        # Update JSON state to completed
        status_tracker["stocks"][stock_name] = "Completed"
        status_tracker["completed"] += 1
        save_status()
        
    except Exception as e:
        logger.error(f"FAILED: Could not generate report for {stock_name}. Error: {e}")
        
        # Update JSON state to failed with error string
        status_tracker["stocks"][stock_name] = f"Failed: {str(e)}"
        status_tracker["failed"] += 1
        save_status()

# --- 6. EXECUTE THE PIPELINE LOOP ---
logger.info(f"PIPELINE INITIATED: Loaded {len(stock_list)} stocks into the queue.")
save_status() # Create the initial tracking file

for idx, stock in enumerate(stock_list, 1):
    logger.info(f"--- Processing {idx}/{len(stock_list)} ---")
    generate_institutional_report(stock)
    
    if idx < len(stock_list):
        logger.info("Applying 30-second rate-limit cooldown...")
        time.sleep(30)

logger.info(f"PIPELINE COMPLETE: {status_tracker['completed']} successful, {status_tracker['failed']} failed.")
