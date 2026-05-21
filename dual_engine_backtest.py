"""
dual_engine_backtest.py - HISTORICAL AI AUDITOR VERSION
==========================================================
1. Python handles the mathematical Split/Bonus adjustments.
2. PURE MOMENTUM: (12M Return * 2) + (6M Return * 1).
3. SINGLE ENGINE: Top 20 absolute momentum stocks.
4. HISTORICAL AI AUDIT: Gemini is called AT EVERY REBALANCE DATE to check for anomalies.
5. Generates a master HTML timeline of AI audits.
"""
import os
import glob
import json
import time
import re
import pandas as pd
import numpy as np
from google import genai

def load_and_adjust_data(folder_path, sector_map_path):
    print("Loading and Adjusting Bhav Copy for Corporate Actions...")
    sector_map = pd.read_csv(sector_map_path)[['SYMBOL', 'SECTOR']]
    
    all_files = glob.glob(os.path.join(folder_path, "**/*.csv"), recursive=True)
    if not all_files:
        raise ValueError("No CSV files found in the specified path.")
        
    df_list = []
    for file in all_files:
        try:
            df = pd.read_csv(file)
            df.columns = df.columns.str.strip()
            if 'DATE1' in df.columns: df = df.rename(columns={'DATE1': 'DATE'})
            df = df[['SYMBOL', 'DATE', 'CLOSE_PRICE', 'TURNOVER_LACS', 'DELIV_PER']]
            df['DATE'] = pd.to_datetime(df['DATE'])
            df['CLOSE_PRICE'] = pd.to_numeric(df['CLOSE_PRICE'], errors='coerce')
            df_list.append(df)
        except Exception:
            continue
            
    master_df = pd.concat(df_list, ignore_index=True)
    master_df = master_df.dropna(subset=['DATE', 'CLOSE_PRICE'])
    master_df = master_df.drop_duplicates(subset=['SYMBOL', 'DATE'])
    master_df = master_df.sort_values(['SYMBOL', 'DATE'])
    
    # Python MUST do mathematical back-adjustment for accurate momentum scoring
    master_df['PCT_CHG'] = master_df.groupby('SYMBOL')['CLOSE_PRICE'].pct_change()
    adjusted_dfs = []
    for sym, group in master_df.groupby('SYMBOL'):
        g = group.copy()
        split_mask = g['PCT_CHG'] < -0.25
        if split_mask.any():
            for i in reversed(range(len(g))):
                if split_mask.iloc[i]:
                    factor = 1 + g.iloc[i]['PCT_CHG']
                    g.iloc[:i, g.columns.get_loc('CLOSE_PRICE')] *= factor
        adjusted_dfs.append(g)
        
    master_df = pd.concat(adjusted_dfs)
    master_df = pd.merge(master_df, sector_map, on='SYMBOL', how='left')
    return master_df.reset_index(drop=True)

def analyze_with_gemini(client, current_date_str, portfolio_df):
    """Calls Gemini for a specific historical date to find anomalies."""
    if portfolio_df.empty:
        return "No stocks in portfolio for this period."

    table_str = portfolio_df[['SYMBOL', 'SECTOR', 'CLOSE_PRICE']].to_markdown(index=False)
    
    prompt = f"""
    You are a Forensic Equities Auditor for a quant fund.
    You are performing a STRICT POINT-IN-TIME risk audit for {current_date_str}.
    
    CRITICAL TEMPORAL DIRECTIVE:
    You are operating strictly on {current_date_str}. 
    You MUST NOT access, use, or reference any information, news, earnings, or price action that occurred AFTER {current_date_str}.
    
    Look at these 20 stocks we are holding on {current_date_str}:
    {table_str}
    
    Based ONLY on data available BEFORE {current_date_str}, do any of these stocks have severe anomalies? 
    Look for:
    1. Historical SEBI warnings or bans prior to {current_date_str}.
    2. Severe corporate governance issues or auditor resignations prior to {current_date_str}.
    3. Announced (but not yet executed) massive equity dilution.
    
    Be extremely brief. If a stock is clean, DO NOT mention it. Only list the stocks with severe anomalies in bullet points.
    If all stocks are clean up to this date, simply output "✓ No major historical anomalies detected prior to {current_date_str}."
    """
    
    for attempt in range(5):
        try:
            resp = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            return resp.text.strip()
        except Exception as e:
            if '429' in str(e) or '503' in str(e):
                wait_time = 30 * (attempt + 1)
                print(f"    [Gemini API Rate Limit] Pausing for {wait_time}s to avoid quota exhaustion...")
                time.sleep(wait_time)
            else:
                return f"Error analyzing data: {e}"
    return "Failed to analyze due to API limits."

def run_pure_momentum_backtest(df):
    print("Calculating Metrics & Simulating Pure Single-Engine Portfolio...")
    
    df['PRICE_1M_AGO'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['PRICE_7M_AGO'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(147) 
    df['PRICE_13M_AGO'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    
    df['12M_RET'] = (df['PRICE_1M_AGO'] - df['PRICE_13M_AGO']) / df['PRICE_13M_AGO']
    df['6M_RET']  = (df['PRICE_1M_AGO'] - df['PRICE_7M_AGO']) / df['PRICE_7M_AGO']
    df['PRICE_MOMENTUM'] = (df['12M_RET'] * 2) + df['6M_RET']
    
    df['EMA_51'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=51, adjust=False).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    
    df['YEAR_MONTH'] = df['DATE'].dt.to_period('M')
    month_ends = df.groupby('YEAR_MONTH')['DATE'].max().reset_index()
    rebalance_df = df[df['DATE'].isin(month_ends['DATE'])].copy()
    
    rebalance_df['NEXT_MONTH_CLOSE'] = rebalance_df.groupby('SYMBOL')['CLOSE_PRICE'].shift(-1)
    rebalance_df['FORWARD_1M_RET'] = (rebalance_df['NEXT_MONTH_CLOSE'] / rebalance_df['CLOSE_PRICE']) - 1
    rebalance_df['MASTER_SCORE'] = rebalance_df['PRICE_MOMENTUM'] * 100
    
    valid_pool = rebalance_df[
        (rebalance_df['CLOSE_PRICE'] >= rebalance_df['EMA_51']) & 
        (rebalance_df['CLOSE_PRICE'] >= (rebalance_df['52W_HIGH'] * 0.80)) & 
        (rebalance_df['AVG_TURNOVER'] >= 1000.0) & 
        (rebalance_df['MASTER_SCORE'].notna()) & 
        (rebalance_df['SECTOR'].notna()) 
    ].copy()

    dates = sorted(rebalance_df['DATE'].dropna().unique())
    prev_portfolio_df = pd.DataFrame()
    
    ai_audit_log = []
    
    # Initialize Gemini Client for the loop
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client() if api_key else None
    
    print(f"Starting Month-by-Month Rebalance & AI Audit across {len(dates)} periods...")
    
    for current_date in dates:
        curr_date_str = current_date.strftime('%Y-%m-%d')
        candidates = valid_pool[valid_pool['DATE'] == current_date].copy()
        prev_symbols = set(prev_portfolio_df['SYMBOL']) if not prev_portfolio_df.empty else set()
        
        if candidates.empty:
            prev_portfolio_df = pd.DataFrame() 
            continue

        candidates = candidates.sort_values(by='MASTER_SCORE', ascending=False)
        top_40 = candidates.head(40).copy()
        
        held_stocks = top_40[top_40['SYMBOL'].isin(prev_symbols)]
        new_stocks = top_40[~top_40['SYMBOL'].isin(prev_symbols)]
        final_portfolio = pd.concat([held_stocks, new_stocks]).head(20).copy()
        
        # --- HISTORICAL AI AUDIT TRIGGER ---
        if client and not final_portfolio.empty:
            print(f"  -> {curr_date_str}: Calling Gemini to audit {len(final_portfolio)} stocks...")
            audit_result = analyze_with_gemini(client, curr_date_str, final_portfolio)
            ai_audit_log.append({
                'DATE': curr_date_str,
                'AUDIT_TEXT': audit_result
            })
            
        prev_portfolio_df = final_portfolio.copy()

    # Generate HTML Audit Report
    generate_audit_html(ai_audit_log)
    print("\n✅ Backtest & AI Historical Audit Complete.")

def generate_audit_html(audit_log):
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Historical AI Risk Audit</title>
        <style>
            body { background-color: #121212; color: #e0e0e0; font-family: sans-serif; padding: 20px; }
            h1 { color: #bb86fc; text-align: center; }
            .card { background-color: #1e1e1e; border-left: 4px solid #bb86fc; padding: 15px; margin-bottom: 20px; border-radius: 6px; }
            .date { font-weight: bold; font-size: 1.2em; color: #fff; margin-bottom: 10px; }
            .text { font-size: 14px; line-height: 1.5; color: #ccc; white-space: pre-wrap; }
        </style>
    </head>
    <body>
        <h1>Month-by-Month AI Forensic Audit Log</h1>
        <p style="text-align:center; color:#888;">Gemini evaluated the Top 20 stocks every single month using strict Point-in-Time data.</p>
        <div style="max-width: 800px; margin: auto;">
    """
    
    for log in reversed(audit_log):
        # Color coding: If it found anomalies, make the border red
        border_color = "#ff5252" if "anomaly" in log['AUDIT_TEXT'].lower() or "warning" in log['AUDIT_TEXT'].lower() else "#4caf50"
        
        html_content += f"""
        <div class="card" style="border-left-color: {border_color};">
            <div class="date">{log['DATE']}</div>
            <div class="text">{log['AUDIT_TEXT']}</div>
        </div>
        """
        
    html_content += """
        </div>
    </body>
    </html>
    """
    
    with open("historical_ai_audit.html", "w", encoding="utf-8") as f:
        f.write(html_content)

if __name__ == "__main__":
    DATA_PATH = "./HistoricalBhavCopy/NSE"
    SECTOR_MAP = "./nifty500_sectors.csv" 
    try:
        raw_df = load_and_adjust_data(DATA_PATH, SECTOR_MAP)
        run_pure_momentum_backtest(raw_df)
    except Exception as e:
        print(f"Execution failed: {e}")
