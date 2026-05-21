"""
dual_engine_backtest.py - REAL-TIME LOOP WITH STRICT NETWORK CUTOFF
==========================================================
Constructs the portfolio, triggers the AI audit, and cuts off
the API connection if it hangs for more than 30 seconds.
"""
import os
import glob
import json
import time
import pandas as pd
import numpy as np
import concurrent.futures
from google import genai

# ==========================================
# 1. DATA LOADING & ADJUSTMENT
# ==========================================
def load_and_adjust_data(folder_path, sector_map_path):
    print("Loading and Adjusting Bhav Copy for Corporate Actions...")
    sector_map = pd.read_csv(sector_map_path)[['SYMBOL', 'SECTOR']]
    
    all_files = glob.glob(os.path.join(folder_path, "**/*.csv"), recursive=True)
    df_list = []
    for file in all_files:
        try:
            df = pd.read_csv(file)
            df.columns = df.columns.str.strip()
            if 'DATE1' in df.columns: df = df.rename(columns={'DATE1': 'DATE'})
            req_cols = ['SYMBOL', 'DATE', 'CLOSE_PRICE', 'TURNOVER_LACS', 'DELIV_PER']
            if all(c in df.columns for c in req_cols):
                df_list.append(df[req_cols])
        except Exception:
            continue
            
    if not df_list:
        raise ValueError("No CSV files found. Check your folder path.")
        
    master_df = pd.concat(df_list, ignore_index=True)
    master_df['DATE'] = pd.to_datetime(master_df['DATE'], errors='coerce')
    master_df['CLOSE_PRICE'] = pd.to_numeric(master_df['CLOSE_PRICE'], errors='coerce')
    master_df['TURNOVER_LACS'] = pd.to_numeric(master_df['TURNOVER_LACS'], errors='coerce')
    
    master_df = master_df.dropna(subset=['DATE', 'CLOSE_PRICE'])
    master_df = master_df.drop_duplicates(subset=['SYMBOL', 'DATE']).sort_values(['SYMBOL', 'DATE'])
    
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
    return pd.merge(master_df, sector_map, on='SYMBOL', how='left').reset_index(drop=True)

# ==========================================
# 2. THE UNIFIED REAL-TIME LOOP (Math + AI)
# ==========================================
def run_realtime_construction_and_audit(df):
    print("Initializing Unified Backtest and Real-Time AI Agent...")
    
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client() if api_key else None
    
    progress_file = "audit_progress.json"
    if os.path.exists(progress_file):
        with open(progress_file, "r") as f:
            audit_progress = json.load(f)
    else:
        audit_progress = {"results": {}}

    df['P_1M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['P_7M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(147) 
    df['P_13M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    df['PRICE_MOMENTUM'] = (((df['P_1M'] - df['P_13M']) / df['P_13M']) * 2) + ((df['P_1M'] - df['P_7M']) / df['P_7M'])
    
    df['EMA_51'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=51, adjust=False).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    
    df['YEAR_MONTH'] = df['DATE'].dt.to_period('M')
    month_ends = df.groupby('YEAR_MONTH')['DATE'].max().reset_index()
    rebalance_df = df[df['DATE'].isin(month_ends['DATE'])].copy()
    rebalance_df['MASTER_SCORE'] = rebalance_df['PRICE_MOMENTUM'] * 100
    
    valid_pool = rebalance_df[
        (rebalance_df['CLOSE_PRICE'] >= rebalance_df['EMA_51']) & 
        (rebalance_df['CLOSE_PRICE'] >= (rebalance_df['52W_HIGH'] * 0.80)) & 
        (rebalance_df['AVG_TURNOVER'] >= 1000.0) & 
        (rebalance_df['MASTER_SCORE'].notna())
    ].copy()

    dates = sorted(rebalance_df['DATE'].dropna().unique())
    portfolio_snapshots = []
    prev_portfolio_df = pd.DataFrame()
    entry_prices = {}
    entry_dates = {}
    
    for current_date in dates:
        curr_date_str = current_date.strftime('%Y-%m-%d')
        day_data = rebalance_df[rebalance_df['DATE'] == current_date]
        if day_data.empty: continue
            
        day_prices = day_data.set_index('SYMBOL')['CLOSE_PRICE'].to_dict()
        candidates = valid_pool[valid_pool['DATE'] == current_date].copy()
        prev_symbols = set(prev_portfolio_df['SYMBOL']) if not prev_portfolio_df.empty else set()
        
        if candidates.empty:
            for sym in prev_symbols:
                portfolio_snapshots.append({
                    'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL']==sym]['SECTOR'].iloc[0],
                    'ACTION': 'EXIT', 'PRICE': day_prices.get(sym, 0), 'ENTRY_DATE': entry_dates.get(sym, 'N/A'),
                    'PNL': f"{((day_prices.get(sym, 0)/entry_prices.get(sym, 1))-1)*100:+.2f}%", 'JUSTIFICATION': "Failed Guardrails"
                })
            entry_prices.clear(); entry_dates.clear()
            prev_portfolio_df = pd.DataFrame() 
            continue

        candidates = candidates.sort_values(by='MASTER_SCORE', ascending=False)
        top_40 = candidates.head(40).copy()
        final_portfolio = pd.concat([top_40[top_40['SYMBOL'].isin(prev_symbols)], top_40[~top_40['SYMBOL'].isin(prev_symbols)]]).head(20).copy()
        current_symbols = set(final_portfolio['SYMBOL'])
        
        for sym in (prev_symbols - current_symbols):
            portfolio_snapshots.append({
                'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL']==sym]['SECTOR'].iloc[0],
                'ACTION': 'EXIT', 'PRICE': day_prices.get(sym, 0), 'ENTRY_DATE': entry_dates.get(sym, 'N/A'),
                'PNL': f"{((day_prices.get(sym, 0)/entry_prices.get(sym, 1))-1)*100:+.2f}%", 'JUSTIFICATION': "Fell below Buffer"
            })
            if sym in entry_prices: del entry_prices[sym]
            if sym in entry_dates: del entry_dates[sym]
            
        current_month_holdings = []
        for _, row in final_portfolio.iterrows():
            sym, curr_price = row['SYMBOL'], row['CLOSE_PRICE']
            if sym not in prev_symbols:
                entry_prices[sym], entry_dates[sym] = curr_price, curr_date_str
            pnl_str = f"{((curr_price/entry_prices[sym])-1)*100:+.2f}%" if entry_prices[sym] > 0 else "NEW"
            
            record = {
                'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': row['SECTOR'],
                'ACTION': 'HOLD' if sym in prev_symbols else 'ENTRY', 'PRICE': curr_price, 
                'ENTRY_DATE': entry_dates[sym], 'PNL': pnl_str, 'JUSTIFICATION': "Top 20 Momentum"
            }
            portfolio_snapshots.append(record)
            current_month_holdings.append(record)
            
        prev_portfolio_df = final_portfolio.copy()

        # ========================================================
        # REAL-TIME AI AUDIT WITH STRICT 30-SECOND CUTOFF
        # ========================================================
        if client and curr_date_str not in audit_progress["results"] and current_month_holdings:
            print(f"[{curr_date_str}] Portfolio Constructed. Triggering Point-in-Time AI Audit...")
            
            audit_df = pd.DataFrame(current_month_holdings)[['SYMBOL', 'SECTOR', 'PRICE', 'ENTRY_DATE']]
            
            prompt = f"""
            FORENSIC AUDIT DATE: {curr_date_str}
            
            You are an Equities Auditor. You are performing a STRICT point-in-time audit for {curr_date_str}.
            You are FORBIDDEN from using any data, news, price action, or SEBI filings that occurred AFTER {curr_date_str}.
            
            Top 20 Portfolio Constructed on this date:
            {audit_df.to_markdown(index=False)}
            
            Analyze for severe historical governance red flags known ONLY up to {curr_date_str}. 
            Keep it brief. If clean, output: "✓ No major historical anomalies detected as of {curr_date_str}."
            """
            
            success = False
            attempts = 0
            while not success and attempts < 5:
                attempts += 1
                try:
                    # Enforce a strict 30-second timeout to prevent silent network hangs
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(client.models.generate_content, model='gemini-2.5-flash', contents=prompt)
                        resp = future.result(timeout=30) 
                        
                    audit_progress["results"][curr_date_str] = resp.text
                    
                    with open(progress_file, "w") as f:
                        json.dump(audit_progress, f, indent=4)
                    
                    print(f"  -> [SUCCESS] Audit logged for {curr_date_str}.")
                    pd.DataFrame(portfolio_snapshots).to_csv("backtest_portfolio_history.csv", index=False)
                    time.sleep(5) 
                    success = True
                    
                except concurrent.futures.TimeoutError:
                    print(f"  -> [NETWORK TIMEOUT] Google API silently hung. Attempt {attempts}/5. Retrying...")
                    time.sleep(10)
                except Exception as e:
                    error_str = str(e)
                    if any(err in error_str for err in ["429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500", "502"]):
                        print(f"  -> [API BUSY] Attempt {attempts}/5. Waiting 60 seconds...")
                        time.sleep(60) 
                    else:
                        print(f"  -> [FATAL ERROR] {e}")
                        break 
                        
            if not success:
                print(f"  -> [SKIPPED] Moving to next month due to persistent API limits.")

    print("\nBacktest & Audit Sequence Complete.")
    return audit_progress

# ==========================================
# 3. GITHUB PAGES HTML PUBLISHER
# ==========================================
def generate_dashboards(audit_progress):
    print("Generating HTML Dashboards for GitHub Pages...")
    
    html_timeline = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>AI Forensic Timeline</title>
        <style>
            body { font-family: -apple-system, sans-serif; background: #121212; color: #e0e0e0; padding: 20px; }
            .header { text-align: center; margin-bottom: 30px; }
            .btn { background: #bb86fc; color: #121212; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;}
            .card { background: #1e1e1e; border-left: 4px solid #bb86fc; padding: 15px; margin-bottom: 20px; border-radius: 5px; }
            .date { font-size: 18px; font-weight: bold; color: #fff; margin-bottom: 10px; }
            pre { white-space: pre-wrap; font-family: inherit; font-size: 14px; color: #ccc; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Momentum AI Audit Timeline</h1>
            <a href="history.html" class="btn">View Detailed Trade Ledger</a>
        </div>
        <div style="max-width: 800px; margin: auto;">
    """
    
    for date in sorted(audit_progress.get("results", {}).keys(), reverse=True):
        report = audit_progress["results"][date]
        b_color = "#ff5252" if "warning" in report.lower() or "anomaly" in report.lower() else "#4caf50"
        html_timeline += f"<div class='card' style='border-left-color: {b_color};'><div class='date'>{date}</div><pre>{report}</pre></div>"
        
    html_timeline += "</div></body></html>"
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_timeline)

    if os.path.exists("backtest_portfolio_history.csv"):
        df = pd.read_csv("backtest_portfolio_history.csv")
        table_html = df.to_html(index=False, classes='trade-table')
        
        html_history = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Trade Ledger</title>
            <style>
                body {{ background: #121212; color: #fff; font-family: sans-serif; padding: 20px; }}
                a {{ color: #bb86fc; text-decoration: none; font-size: 18px; margin-bottom: 20px; display: inline-block; }}
                .trade-table {{ width: 100%; border-collapse: collapse; font-size: 12px; text-align: left; }}
                th, td {{ padding: 8px; border-bottom: 1px solid #333; }}
                th {{ background: #1e1e1e; color: #bb86fc; }}
                tr:hover {{ background: #1a1a1a; }}
            </style>
        </head>
        <body>
            <a href="index.html">⬅ Back to AI Audit</a>
            <h2>Complete Historical Trade Ledger</h2>
            {table_html}
        </body>
        </html>
        """
        with open("history.html", "w", encoding="utf-8") as f:
            f.write(html_history)

if __name__ == "__main__":
    DATA_PATH = "./HistoricalBhavCopy/NSE"
    SECTOR_MAP = "./nifty500_sectors.csv" 
    
    try:
        raw_df = load_and_adjust_data(DATA_PATH, SECTOR_MAP)
        audit_state = run_realtime_construction_and_audit(raw_df)
        generate_dashboards(audit_state)
        
    except Exception as e:
        print(f"Execution failed: {e}")
