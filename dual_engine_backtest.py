"""
dual_engine_backtest.py - FINAL PRODUCTION VERSION
==========================================================
1. RE-NORMALIZES historical data for stock splits and bonuses.
2. PURE MOMENTUM: (12M Return * 2) + (6M Return * 1) [1-Month Skip].
3. SINGLE ENGINE: Top 20 absolute momentum stocks across Top 40 buffer.
4. GUARDRAILS: > 51 EMA, Within 20% of 52W High, > 10Cr Avg Turnover.
5. AI FORENSIC AUDIT: Point-in-time analysis of entry signals.
6. FULL HTML DASHBOARD: Mobile-responsive Dark Mode UI.
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
    
    df_list = []
    for file in all_files:
        try:
            df = pd.read_csv(file)
            df.columns = df.columns.str.strip()
            if 'DATE1' in df.columns: df = df.rename(columns={'DATE1': 'DATE'})
            df = df[['SYMBOL', 'DATE', 'CLOSE_PRICE', 'TURNOVER_LACS', 'DELIV_PER']]
            df['DATE'] = pd.to_datetime(df['DATE'])
            df['CLOSE_PRICE'] = pd.to_numeric(df['CLOSE_PRICE'])
            df_list.append(df)
        except: continue
            
    master_df = pd.concat(df_list).sort_values(['SYMBOL', 'DATE'])
    master_df['PCT_CHG'] = master_df.groupby('SYMBOL')['CLOSE_PRICE'].pct_change()
    
    # Back-adjust history for splits/bonuses
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
    return master_df

def run_pure_momentum_backtest(df):
    print("Calculating Metrics & Simulating Pure Single-Engine Portfolio...")
    
    # 1-Month Skip Momentum
    df['PRICE_1M_AGO'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['PRICE_7M_AGO'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(147) 
    df['PRICE_13M_AGO'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    
    df['12M_RET'] = (df['PRICE_1M_AGO'] - df['PRICE_13M_AGO']) / df['PRICE_13M_AGO']
    df['6M_RET']  = (df['PRICE_1M_AGO'] - df['PRICE_7M_AGO']) / df['PRICE_7M_AGO']
    df['PRICE_MOMENTUM'] = (df['12M_RET'] * 2) + df['6M_RET']
    
    # Technical Indicators
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
        (rebalance_df['MASTER_SCORE'].notna())
    ].copy()

    dates = sorted(rebalance_df['DATE'].dropna().unique())
    portfolio_snapshots = []
    prev_portfolio_df = pd.DataFrame()
    entry_prices, entry_dates = {}, {}
    
    for current_date in dates:
        curr_date_str = current_date.strftime('%Y-%m-%d')
        day_prices = rebalance_df[rebalance_df['DATE'] == current_date].set_index('SYMBOL')['CLOSE_PRICE'].to_dict()
        candidates = valid_pool[valid_pool['DATE'] == current_date].copy().sort_values(by='MASTER_SCORE', ascending=False)
        prev_symbols = set(prev_portfolio_df['SYMBOL']) if not prev_portfolio_df.empty else set()
        
        top_40 = candidates.head(40).copy()
        final_portfolio = pd.concat([top_40[top_40['SYMBOL'].isin(prev_symbols)], top_40[~top_40['SYMBOL'].isin(prev_symbols)]]).head(20).copy()
        current_symbols = set(final_portfolio['SYMBOL'])
        
        for sym in (prev_symbols - current_symbols):
            portfolio_snapshots.append({'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL']==sym]['SECTOR'].iloc[0], 'ACTION': 'EXIT', 'ENTRY_DATE': entry_dates.get(sym, 'N/A'), 'EXIT_DATE': curr_date_str, 'PRICE': day_prices.get(sym, 0), 'PNL': f"{((day_prices.get(sym, 0)/entry_prices.get(sym, 1))-1)*100:+.2f}%", 'JUSTIFICATION': 'Failed criteria'})
            if sym in entry_prices: del entry_prices[sym]
        
        for _, row in final_portfolio.iterrows():
            sym = row['SYMBOL']
            if sym not in prev_symbols:
                entry_prices[sym] = row['CLOSE_PRICE']
                entry_dates[sym] = curr_date_str
            portfolio_snapshots.append({'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': row['SECTOR'], 'ACTION': 'HOLD' if sym in prev_symbols else 'ENTRY', 'PRICE': row['CLOSE_PRICE'], 'ENTRY_DATE': entry_dates[sym], 'PNL': f"{((row['CLOSE_PRICE']/entry_prices[sym])-1)*100:+.2f}%", 'JUSTIFICATION': 'Maintain' if sym in prev_symbols else 'Breakout'})
            
        prev_portfolio_df = final_portfolio.copy()
        
    pd.DataFrame(portfolio_snapshots).to_csv("backtest_portfolio_history.csv", index=False)
    return portfolio_snapshots

def audit_portfolio_with_gemini(snapshots):
    df_snap = pd.DataFrame(snapshots)
    latest_date = df_snap['DATE'].max()
    live = df_snap[(df_snap['DATE'] == latest_date) & (df_snap['ACTION'].isin(['ENTRY', 'HOLD']))].copy()
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key: return
    
    prompt = f"""
    You are the Lead Auditor for an Indian quant fund. Perform a point-in-time risk audit for these stocks as of {latest_date}.
    Stocks: {live.to_markdown()}
    Generate a beautiful single-file HTML dashboard with a button to 'history.html'. Wrap in ```html codeblock.
    """
    
    client = genai.Client()
    for attempt in range(5):
        try:
            resp = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            match = re.search(r"
```html\s*(.*?)\s*```", resp.text, re.DOTALL)
            with open("portfolio_dashboard.html", "w", encoding="utf-8") as f:
                f.write(match.group(1) if match else resp.text)
            break
        except Exception:
            time.sleep(60)

if __name__ == "__main__":
    raw_df = load_and_adjust_data("./HistoricalBhavCopy/NSE", "./nifty500_sectors.csv")
    snapshots = run_pure_momentum_backtest(raw_df)
    audit_portfolio_with_gemini(snapshots)
