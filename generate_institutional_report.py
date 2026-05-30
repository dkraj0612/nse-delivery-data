import os
import time
from google import genai
from google.genai import types

# 1. Initialize Client via GitHub Secrets Environment Variable
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# 2. Setup Output Directory
output_dir = "forensic_reports"
os.makedirs(output_dir, exist_ok=True)

# 3. Define the Target Pipeline
stock_list = [
    "Lumax Auto", 
    "Acutaas Chemicals", 
    "Bliss GVS Pharma", 
    "Maithan Alloys"
    # Insert remaining 36 stocks here
] 

# 4. Define the AI Master Prompt
system_master_prompt = """
[PASTE YOUR ENTIRE 8-MODULE MASTER PROMPT HERE]
Do not ask for permission to proceed. Generate the complete 8-module report for the provided stock.
"""

def generate_institutional_report(stock_name):
    print(f"Executing forensic web-scraping and analysis for: {stock_name}...")
    
    try:
        response = client.models.generate_content(
            model='gemini-3.1-pro', # Leveraging the extended context model
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
            
        print(f"SUCCESS: Report saved to {filename}\n")
        
    except Exception as e:
        print(f"FAILED: Could not generate report for {stock_name}. Error: {e}\n")

# 5. Execute the Gated Loop
for stock in stock_list:
    generate_institutional_report(stock)
    time.sleep(30) # 30-second delay prevents API rate-limiting during massive web loads

print("Pipeline execution complete. All reports generated.")
