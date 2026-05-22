"""
dual_engine_backtest.py - SINGLE AUDIT WITH DELIVERY PERCENTAGE & 100 EMA
==========================================================
1. BACKTEST ENGINE: Adjusts splits, calculates momentum, applies 100 EMA, 52W High, Turnover, and Delivery % guardrails.
2. AI AUDITOR: Makes ONE single API call for the latest portfolio to guarantee speed.
3. GITHUB PUBLISHER: Generates 'index.html' and 'history.html'.
"""
import os
import glob
import pandas as pd
import numpy as np
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
    
    # Clean delivery percentage (handle any weird string formats like '45.5%')
    master_df['DELIV_PER'] = master_df['DELIV_PER'].astype(str).str.replace('%', '', regex=False)
    master_df['DELIV_PER'] = pd.to_numeric(master_df['DELIV_PER'], errors='coerce')
    
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
# 2. PURE MOMENTUM BACKTEST ENGINE
# ==========================================
def run_pure_momentum_backtest(df):
    print("Running Mathematical Backtest Engine...")
    df['P_1M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['P_7M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(147) 
    df['P_13M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    df['PRICE_MOMENTUM'] = (((df['P_1M'] - df['P_13M']) / df['P_13M']) * 2) + ((df['P_1M'] - df['P_7M']) / df['P_7M'])
    
    # CHANGED TO 100 EMA
    df['EMA_100'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=100, adjust=False).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    
    # 20-Day Rolling Averages for Volume/Conviction Guardrails
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    df['AVG_DELIV_PER'] = df.groupby('SYMBOL')['DELIV_PER'].transform(lambda x: x.rolling(20).mean())
    
    df['YEAR_MONTH'] = df['DATE'].dt.to_period('M')
    month_ends = df.groupby('YEAR_MONTH')['DATE'].max().reset_index()
    rebalance_df = df[df['DATE'].isin(month_ends['DATE'])].copy()
    rebalance_df['MASTER_SCORE'] = rebalance_df['PRICE_MOMENTUM'] * 100
    
    # Guardrails: 100 EMA, 80% of 52W High, 1000L Turnover, 30% Delivery
    valid_pool = rebalance_df[
        (rebalance_df['CLOSE_PRICE'] >= rebalance_df['EMA_100']) & 
        (rebalance_df['CLOSE_PRICE'] >= (rebalance_df['52W_HIGH'] * 0.80)) & 
        (rebalance_df['AVG_TURNOVER'] >= 1000.0) & 
        (rebalance_df['AVG_DELIV_PER'] >= 30.0) & 
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
        day_deliv = day_data.set_index('SYMBOL')['AVG_DELIV_PER'].to_dict()
        
        candidates = valid_pool[valid_pool['DATE'] == current_date].copy()
        prev_symbols = set(prev_portfolio_df['SYMBOL']) if not prev_portfolio_df.empty else set()
        
        if candidates.empty:
            for sym in prev_symbols:
                portfolio_snapshots.append({
                    'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL']==sym]['SECTOR'].iloc[0],
                    'ACTION': 'EXIT', 'PRICE': day_prices.get(sym, 0), 'ENTRY_DATE': entry_dates.get(sym, 'N/A'),
                    'PNL': f"{((day_prices.get(sym, 0)/entry_prices.get(sym, 1))-1)*100:+.2f}%", 
                    'DELIV_%': f"{day_deliv.get(sym, 0):.1f}%" if pd.notna(day_deliv.get(sym, 0)) else "N/A",
                    'JUSTIFICATION': "Failed Guardrails"
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
                'PNL': f"{((day_prices.get(sym, 0)/entry_prices.get(sym, 1))-1)*100:+.2f}%", 
                'DELIV_%': f"{day_deliv.get(sym, 0):.1f}%" if pd.notna(day_deliv.get(sym, 0)) else "N/A",
                'JUSTIFICATION': "Fell below Buffer"
            })
            if sym in entry_prices: del entry_prices[sym]
            if sym in entry_dates: del entry_dates[sym]
            
        for _, row in final_portfolio.iterrows():
            sym, curr_price = row['SYMBOL'], row['CLOSE_PRICE']
            if sym not in prev_symbols:
                entry_prices[sym], entry_dates[sym] = curr_price, curr_date_str
            pnl_str = f"{((curr_price/entry_prices[sym])-1)*100:+.2f}%" if entry_prices[sym] > 0 else "NEW"
            
            portfolio_snapshots.append({
                'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': row['SECTOR'],
                'ACTION': 'HOLD' if sym in prev_symbols else 'ENTRY', 'PRICE': curr_price, 
                'ENTRY_DATE': entry_dates[sym], 'PNL': pnl_str, 
                'DELIV_%': f"{row['AVG_DELIV_PER']:.1f}%",
                'JUSTIFICATION': "Top 20 Momentum"
            })
            
        prev_portfolio_df = final_portfolio.copy()

    df_snaps = pd.DataFrame(portfolio_snapshots)
    df_snaps.to_csv("backtest_portfolio_history.csv", index=False)
    return df_snaps

# ==========================================
# 3. SINGLE AI AUDIT (LATEST PORTFOLIO ONLY)
# ==========================================
def run_single_latest_audit(portfolio_df):
    print("\nTriggering Single AI Audit for the Latest Portfolio...")
    audit_progress = {"results": {}}
    
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client() if api_key else None
    
    if not client:
        print("No GEMINI_API_KEY found. Skipping AI Audit.")
        return audit_progress

    # Get the single most recent date in the backtest
    latest_date = portfolio_df['DATE'].max()
    latest_portfolio = portfolio_df[(portfolio_df['DATE'] == latest_date) & (portfolio_df['ACTION'].isin(['ENTRY', 'HOLD']))]
    
    if latest_portfolio.empty:
        print("No active positions on the latest date.")
        return audit_progress
        
    audit_df = latest_portfolio[['SYMBOL', 'SECTOR', 'PRICE', 'DELIV_%', 'ENTRY_DATE']]
    
    prompt = f"""
    FORENSIC AUDIT DATE: {latest_date}
    
    You are an Equities Auditor. You are performing a STRICT point-in-time audit for {latest_date}.
    You are FORBIDDEN from using any data, news, price action, or SEBI filings that occurred AFTER {latest_date}.
    
    Top 20 Portfolio Constructed on this date:
    {audit_df.to_markdown(index=False)}
    
    Analyze for severe historical governance red flags known ONLY up to {latest_date}. 
    Keep it brief. If clean, output: "✓ No major historical anomalies detected as of {latest_date}."
    """
    
    try:
        resp = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        audit_progress["results"][latest_date] = resp.text
        print(f"  -> [SUCCESS] Audit logged for {latest_date}.")
    except Exception as e:
        print(f"  -> [FATAL ERROR] API Failed: {e}")
        audit_progress["results"][latest_date] = f"Audit failed: {e}"

    return audit_progress

# ==========================================
# 4. GITHUB PAGES HTML PUBLISHER
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
        portfolio_history_df = run_pure_momentum_backtest(raw_df)
        
        audit_state = run_single_latest_audit(portfolio_history_df)
        
        generate_dashboards(audit_state)
        print("\nProcess Complete.")
        
    except Exception as e:
        print(f"Execution failed: {e}")
