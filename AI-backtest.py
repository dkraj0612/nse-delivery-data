import os
import json
import time
import random
from datetime import datetime
from dateutil.relativedelta import relativedelta

# Updated imports for the new Google GenAI SDK
from google import genai
from google.genai import types

# ========================= CONFIGURATION =========================
# Automatically uses the GEMINI_API_KEY environment variable
client = genai.Client()
MODEL_ID = 'gemini-2.5-pro'

OUTPUT_DIR = "reports"
PROGRESS_FILE = "progress.json"
BATCH_SIZE = 8                    # Number of months per batch
SLEEP_BETWEEN_BATCHES_MINUTES = 5 # 5 minutes as requested

os.makedirs(OUTPUT_DIR, exist_ok=True)

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed": [], "last_run": None}

def save_progress(completed_dates):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "completed": completed_dates,
            "last_run": datetime.now().isoformat()
        }, f, indent=2)

def generate_analysis(cutoff_date_str: str, max_retries=5):
    with open("prompt_template.txt", "r", encoding="utf-8") as f:
        prompt = f.read().replace("{CUTOFF_DATE}", cutoff_date_str)

    for attempt in range(max_retries):
        try:
            # Updated to use the new client.models.generate_content syntax
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=15000,
                    response_mime_type="application/json"
                )
            )
            text = response.text.strip()
            if text.startswith("
http://googleusercontent.com/immersive_entry_chip/0
http://googleusercontent.com/immersive_entry_chip/1
