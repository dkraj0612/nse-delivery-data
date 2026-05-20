"""
dual_engine_backtest.py
=======================
1. Fetches Nifty 50 from Yahoo Finance for the 200-EMA Circuit Breaker.
2. Merges Bhav Copy with sector mapping (LEFT join for 2000+ stocks).
3. Allocates 50% capital to Top 3 Sectors (Engine A).
4. Allocates 50% capital to lone wolf breakouts (Engine B).
5. Uses Gemini AI to audit the final portfolio and generate an HTML dashboard.
"""
import os
import glob
import re
import pandas as pd
import numpy as np
import yfinance as yf
from google import genai

def fetch_nifty_regime():
    print("Fetching Nifty 50 index data for Circuit Breaker...")
    nifty = yf.download('^NSEI', period='5y', progress=False)
    nifty['NIFTY_EMA_200'] = nifty['Close'].ewm(span=200, adjust=False).mean()
    nifty['REGIME_GREEN'] = nifty['Close'] > nifty['NIFTY_EMA_200']
    
    nifty = nifty.reset_index()
    nifty['DATE'] = pd.to_datetime(nifty['Date']).dt.tz_localize(None)
    return nifty[['DATE', 'REGIME_GREEN']]

def load_data(folder_path, sector_map_path):
    print("Loading Bhav Copy and Sector Mapping...")
    sector_map = pd.read_csv(sector_map_path)[['SYMBOL', 'SECTOR']]
    
    all_files = glob.glob(os.path.join(folder_path, "**/*.csv"), recursive=True)
    if not all_files:
        raise ValueError("No CSV files found in the specified path.")
        
    df_list = []
    for file in all_files:
        try:
            df = pd.read_csv(file, usecols=['SYMBOL', 'DATE', 'CLOSE_PRICE', 'TURNOVER_LACS', 'DELIV_PER'])
            df.columns = df.columns.str.strip()
            df_list.append(df)
        except Exception:
            continue
            
    master_df = pd.concat(df_list, ignore_index=True)
    master_df['DATE'] = pd.to_datetime(master_df['DATE'], errors='coerce')
    
    for col in ['CLOSE_PRICE', 'TURNOVER_LACS', 'DELIV_PER']:
        master_df[col] = pd.to_numeric(master_df[col], errors='coerce')
        
    master_df['DELIV_PER'] = master_df['DELIV_PER'].fillna(0)
    master_df = master_df.dropna(subset=['DATE', 'CLOSE_PRICE'])
    
    # LEFT join ensures stocks outside the top 750 remain for Engine B
    master_df = pd.merge(master_df, sector_map, on='SYMBOL', how='left')
    return master_df.sort_values(by=['SYMBOL', 'DATE']).reset_index(drop=True)

def run_dual_engine_backtest(df, regime_df):
    print("Calculating Metrics & Simulating Dual-Engine Portfolio...")
    
    df['12M_RET'] = df.groupby('SYMBOL')['CLOSE_PRICE'].pct_change(252)
    df['6M_RET']  = df.groupby('SYMBOL')['CLOSE_PRICE'].pct_change(126)
    df['PRICE_MOMENTUM'] = (df['12M_RET'] + df['6M_RET']) / 2
    
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    df['DELIV_PER_20MA'] = df.groupby('SYMBOL')['DELIV_PER'].transform(lambda x: x.rolling(20).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    
    df['YEAR_MONTH'] = df['DATE'].dt.to_period('M')
    month_ends = df.groupby('YEAR_MONTH')['DATE'].max().reset_index()
    rebalance_df = df[df['DATE'].isin(month_ends['DATE'])].copy()
    
    rebalance_df['NEXT_MONTH_CLOSE'] = rebalance_df.groupby('SYMBOL')['CLOSE_PRICE'].shift(-1)
    rebalance_df['FORWARD_1M_RET'] = (rebalance_df['NEXT_MONTH_CLOSE'] / rebalance_df['CLOSE_PRICE']) - 1
    
    rebalance_df['ACCUMULATION_MULT'] = rebalance_df['DELIV_PER_20MA'] / 50.0
    rebalance_df['MASTER_SCORE'] = rebalance_df['PRICE_MOMENTUM'] * rebalance_df['ACCUMULATION_MULT']
    
    rebalance_df = pd.merge(rebalance_df, regime_df, on='DATE', how='left')
    rebalance_df['REGIME_GREEN'] = rebalance_df['REGIME_GREEN'].fillna(True)
    
    valid_pool = rebalance_df[
        (rebalance_df['CLOSE_PRICE'] >= 50.0) & 
        (rebalance_df['AVG_TURNOVER'] >= 500.0) & 
        (rebalance_df['DELIV_PER_20MA'] >= 35.0) & 
        (rebalance_df['CLOSE_PRICE'] >= (rebalance_df['52W_HIGH'] * 0.80)) & 
        (rebalance_df['MASTER_SCORE'].notna())
    ].copy()

    dates = sorted(valid_pool['DATE'].unique())
    monthly_records = []
    
    for current_date in dates:
        candidates = valid_pool[valid_pool['DATE'] == current_date].copy()
        regime_status = candidates['REGIME_GREEN'].iloc[0] if not candidates.empty else False
        
        if not regime_status:
            monthly_records.append({'DATE': current_date, 'NET_RETURN': 0.004, 'REGIME': 'BEAR (CASH)'})
            continue

        # ENGINE A: The Sector Wave (10 Stocks)
        sector_mom = candidates.groupby('SECTOR')['PRICE_MOMENTUM'].mean().reset_index()
        top_3_sectors = sector_mom.sort_values(by='PRICE_MOMENTUM', ascending=False).head(3)['SECTOR'].tolist()
        
        engine_a_candidates = candidates[candidates['SECTOR'].isin(top_3_sectors)]
        engine_a = engine_a_candidates.sort_values(by='MASTER_SCORE', ascending=False).head(10)
        
        # ENGINE B: The Lone Wolf (10 Stocks)
        engine_b_candidates = candidates[~candidates['SYMBOL'].isin(engine_a['SYMBOL'])]
        engine_b = engine_b_candidates.sort_values(by='MASTER_SCORE', ascending=False).head(10)
        
        final_portfolio = pd.concat([engine_a, engine_b])
        avg_raw_return = final_portfolio['FORWARD_1M_RET'].mean()
        net_monthly_return = avg_raw_return - 0.002
        
        monthly_records.append({'DATE': current_date, 'NET_RETURN': net_monthly_return, 'REGIME': 'BULL (EQUITY)'})

    perf_df = pd.DataFrame(monthly_records).dropna()
    if not perf_df.empty:
        perf_df['EQUITY_CURVE'] = (1 + perf_df['NET_RETURN']).cumprod()
        total_months = len(perf_df)
        cagr = ((perf_df['EQUITY_CURVE'].iloc[-1] ** (12 / total_months)) - 1) * 100
        perf_df['PEAK'] = perf_df['EQUITY_CURVE'].cummax()
        perf_df['DRAWDOWN'] = (perf_df['EQUITY_CURVE'] - perf_df['PEAK']) / perf_df['PEAK']
        max_dd = perf_df['DRAWDOWN'].min() * 100
        
        print("\n" + "="*50)
        print("🚀 DUAL-ENGINE BARBELL STRATEGY RESULTS")
        print("="*50)
        print(f"Months Tested       : {total_months}")
        print(f"Realized CAGR       : {cagr:.2f}%")
        print(f"Maximum Drawdown    : {max_dd:.2f}%")
        print("="*50)

def audit_portfolio_with_gemini(raw_df):
    latest_date = raw_df['DATE'].max()
    month_data = raw_df[raw_df['DATE'] == latest_date].copy()
    
    # Re-run selection for the final month
    sector_mom = month_data.groupby('SECTOR')['PRICE_MOMENTUM'].mean().reset_index()
    top_3_sectors = sector_mom.sort_values(by='PRICE_MOMENTUM', ascending=False).head(3)['SECTOR'].tolist()
    
    engine_a = month_data[month_data['SECTOR'].isin(top_3_sectors)].sort_values(by='MASTER_SCORE', ascending=False).head(10)
    engine_a['SOURCE_ENGINE'] = 'Engine A (Sector)'
    
    engine_b = month_data[~month_data['SYMBOL'].isin(engine_a['SYMBOL'])].sort_values(by='MASTER_SCORE', ascending=False).head(10)
    engine_b['SOURCE_ENGINE'] = 'Engine B (Lone Wolf)'
    
    final_live_portfolio = pd.concat([engine_a, engine_b])
    final_live_portfolio['SECTOR'] = final_live_portfolio['SECTOR'].fillna("UNKNOWN")
    
    print("\n" + "="*50)
    print(f"🤖 GEMINI AI RISK AUDIT & DASHBOARD GEN: {latest_date.strftime('%Y-%m-%d')}")
    print("="*50)
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("⚠️ GEMINI_API_KEY secret not found. Skipping AI audit.")
        return
        
    table_str = final_live_portfolio[['SYMBOL', 'SECTOR', 'SOURCE_ENGINE', 'CLOSE_PRICE', 'AVG_TURNOVER', 'DELIV_PER_20MA', 'MASTER_SCORE']].to_markdown(index=False)
    
    prompt = f"""
    You are the Chief Risk Officer and Lead UI Developer for a quant fund. 
    Our Dual-Engine algorithm selected these 20 stocks on {latest_date.strftime('%Y-%m-%d')}.
    
    Data:
    {table_str}
    
    Perform two tasks:
    
    PART 1: RISK AUDIT
    Provide a brief, ruthless safety audit. Identify operator traps (weird turnover/delivery spikes in UNKNOWN sectors), sector concentration risks, and explicitly list 1-3 stocks to manually reject. Use markdown.
    
    PART 2: HTML DASHBOARD
    Generate a complete, single-file HTML document (with embedded CSS) that creates a beautiful, dark-mode, mobile-responsive dashboard displaying these 20 stocks. 
    - Group them visually by Engine A vs Engine B.
    - Highlight the Ticker, Sector, Price, and Master Score.
    - You MUST wrap the HTML code inside a ```html codeblock.
    """
    
    client = genai.Client()
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        
        text_output = response.text
        
        # Extract HTML using regex
        html_match = re.search(r'
```html\n(.*?)\n```', text_output, re.DOTALL)
        if html_match:
            html_content = html_match.group(1)
            with open("portfolio_dashboard.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            print("✅ HTML Dashboard successfully generated and saved as 'portfolio_dashboard.html'")
            
            # Remove the raw HTML from the console output so it's clean to read
            text_output = re.sub(r'```html\n.*?\n
```', '[HTML Saved to File]', text_output, flags=re.DOTALL)
            
        print("\n" + text_output)
        
    except Exception as e:
        print(f"Gemini API Error: {e}")

if __name__ == "__main__":
    DATA_PATH = "./HistoricalBhavCopy/NSE"
    SECTOR_MAP = "./nifty500_sectors.csv" 
    
    try:
        nifty_regime = fetch_nifty_regime()
        raw_df = load_data(DATA_PATH, SECTOR_MAP)
        
        run_dual_engine_backtest(raw_df, nifty_regime)
        audit_portfolio_with_gemini(raw_df)
        
    except Exception as e:
        print(f"Execution failed: {e}")
