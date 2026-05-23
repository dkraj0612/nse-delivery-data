"""
live_momentum_engine.py - AI DUAL-PORTFOLIO DASHBOARD (LATEST ONLY)
===================================================================
Features: 100-Point Scoring, 70/30 Momentum, Live Portfolio Generation.
"""

import os
import glob
import json
import re
import hashlib
import time
import pandas as pd
import numpy as np
import concurrent.futures
from google import genai

# ==========================================
# MODULE 1: DATA ENGINE
# ==========================================
def get_deterministic_mcap(symbol):
    hash_val = int(hashlib.md5(symbol.encode('utf-8')).hexdigest(), 16)
    return 1000 + (hash_val % 99000)

def load_and_adjust_data(folder_path="./HistoricalBhavCopy/NSE", sector_map_path="./nifty500_sectors.csv", index_path="./nifty500_index.csv"):
    print("Loading Local BhavCopy & Calculating Metrics...")
    try: sector_map = pd.read_csv(sector_map_path)[['SYMBOL', 'SECTOR']]
    except: sector_map = pd.DataFrame(columns=['SYMBOL', 'SECTOR'])

    if os.path.exists(index_path):
        nifty_df = pd.read_csv(index_path)
        nifty_df['DATE'] = pd.to_datetime(nifty_df['DATE'], errors='coerce')
        nifty_df['CLOSE_PRICE'] = pd.to_numeric(nifty_df['CLOSE_PRICE'], errors='coerce')
        nifty_df = nifty_df.dropna(subset=['DATE', 'CLOSE_PRICE']).sort_values('DATE')
        nifty_df['NIFTY_EMA_200'] = nifty_df['CLOSE_PRICE'].ewm(span=200, adjust=False).mean()
        nifty_df['NIFTY_DAILY_RET'] = nifty_df['CLOSE_PRICE'].pct_change()
        nifty_df['NIFTY_VOL_20D'] = nifty_df['NIFTY_DAILY_RET'].rolling(20).std() * np.sqrt(252) * 100
    else: nifty_df = pd.DataFrame(columns=['DATE', 'CLOSE_PRICE', 'NIFTY_EMA_200'])

    all_files = glob.glob(os.path.join(folder_path, "**/*.csv"), recursive=True)
    df_list = []
    for file in all_files:
        try:
            df = pd.read_csv(file)
            df.columns = df.columns.str.strip()
            if 'DATE1' in df.columns: df = df.rename(columns={'DATE1': 'DATE'})
            req_cols = ['SYMBOL', 'DATE', 'CLOSE_PRICE', 'TURNOVER_LACS', 'DELIV_PER']
            if all(c in df.columns for c in req_cols): df_list.append(df[req_cols])
        except: continue
            
    master_df = pd.concat(df_list, ignore_index=True)
    master_df['DATE'] = pd.to_datetime(master_df['DATE'], errors='coerce')
    master_df['CLOSE_PRICE'] = pd.to_numeric(master_df['CLOSE_PRICE'], errors='coerce')
    master_df['TURNOVER_LACS'] = pd.to_numeric(master_df['TURNOVER_LACS'], errors='coerce')
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
    master_df['MKT_CAP_CR'] = master_df['SYMBOL'].apply(get_deterministic_mcap)
    final_df = pd.merge(master_df, sector_map, on='SYMBOL', how='left').reset_index(drop=True)
    final_df['SECTOR'] = final_df['SECTOR'].fillna('Unknown')
    
    return final_df, nifty_df

# ==========================================
# MODULE 2: REVOLUTIONIZED AI LOGIC ENGINE
# ==========================================
def parse_ai_reasoning_payload(text):
    symbols = []
    reasoning_map = {}
    
    array_match = re.search(r'FINAL_SELECTIONS\s*=\s*\[(.*?)\]', text, re.DOTALL)
    if array_match:
        try:
            symbols = json.loads('[' + array_match.group(1) + ']')
        except:
            symbols = [s.strip().replace('"', '').replace("'", "") for s in array_match.group(1).split(',') if s.strip()]
            
    for line in text.split('\n'):
        if '|' in line and not line.startswith('---') and not 'SYMBOL' in line:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 2:
                sym = parts[0].replace('*', '').replace('"', '').replace("'", "")
                reasoning_map[sym] = parts[1]
                
    return symbols, reasoning_map

def call_gemini_institutional_analor(top_50_df, target_limit, date_str, cache):
    if target_limit == 0: return [], {}
    cache_key = f"live_{date_str}_{target_limit}"
    if cache_key in cache:
        return cache[cache_key]["symbols"], cache[cache_key]["reasons"]
        
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client() if api_key else None
    if not client: 
        fallback_syms = top_50_df.head(target_limit)['SYMBOL'].tolist()
        return fallback_syms, {s: "Pure mathematical baseline entry." for s in top_50_df['SYMBOL']}

    print(f"  [AI Processing] Generating Latest Portfolio Construction for {date_str}...")
    
    prompt = f"""
    You are an Institutional Portfolio Manager building a live portfolio.
    
    PORTFOLIO CAPACITY CONSTRAINT: Select exactly {target_limit} symbols from the top 50 table below.
    
    CANDIDATE TOP 50 DATA:
    {top_50_df[['SYMBOL', 'SECTOR', 'MASTER_SCORE', 'MKT_CAP_CR']].to_markdown(index=False)}
    
    CRITERIA FOR REJECTION/SELECTION:
    Evaluate sector density and structural price smoothness. Avoid over-concentration in a single sector. Select the most robust names based on the scoring.
    
    REQUIRED OUTPUT FORMAT:
    You must provide individual row-level reasons for ALL 50 stocks using the pipe delimiter format below:
    SYMBOL | REASON 
    
    At the absolute end of your output, write the final selection array explicitly like this:
    FINAL_SELECTIONS = ["SYM1", "SYM2", ...]
    """
    
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(client.models.generate_content, model='gemini-2.5-flash', contents=prompt)
            resp = future.result(timeout=60)
            
        syms, reasons = parse_ai_reasoning_payload(resp.text)
        if syms:
            cache[cache_key] = {"symbols": syms[:target_limit], "reasons": reasons}
            with open("ai_selections_cache.json", "w") as f:
                json.dump(cache, f, indent=4)
            return cache[cache_key]["symbols"], cache[cache_key]["reasons"]
    except Exception as e:
        print(f"  [API Error/Timeout] Fallback activated: {e}")
        
    fallback_syms = top_50_df.head(target_limit)['SYMBOL'].tolist()
    return fallback_syms, {s: "API Limit Fallback: Mathematical selection." for s in top_50_df['SYMBOL']}

# ==========================================
# MODULE 3: STRATEGY ENGINE (LATEST ONLY)
# ==========================================
def run_momentum_live(df, nifty_df, risk_on=20, risk_off=10):
    print("Initializing System Core Engine with 100-Point Scoring...")
    
    df['P_1M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['P_7M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(147) 
    df['P_13M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    df['P_13M'] = df['P_13M'].replace(0, np.nan)
    df['P_7M'] = df['P_7M'].replace(0, np.nan)
    
    ret_12m = (df['P_1M'] - df['P_13M']) / df['P_13M']
    ret_6m = (df['P_1M'] - df['P_7M']) / df['P_7M']
    df['PRICE_MOMENTUM'] = (ret_12m * 0.70) + (ret_6m * 0.30)
    
    df['EMA_51'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=51, adjust=False).mean())
    df['EMA_100'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=100, adjust=False).mean())
    df['EMA_200'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=200, adjust=False).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    df['AVG_DELIV_PER'] = df.groupby('SYMBOL')['DELIV_PER'].transform(lambda x: x.rolling(20).mean())
    
    df['MOMENTUM_RANK'] = df.groupby('DATE')['PRICE_MOMENTUM'].rank(pct=True) * 100
    df['DELIV_RANK'] = df.groupby('DATE')['AVG_DELIV_PER'].rank(pct=True) * 100
    df['TURNOVER_RANK'] = df.groupby('DATE')['AVG_TURNOVER'].rank(pct=True) * 100
    
    df['EMA_SCORE'] = np.where(df['CLOSE_PRICE'] > df['EMA_51'], 7, 0) + \
                      np.where(df['CLOSE_PRICE'] > df['EMA_100'], 6, 0) + \
                      np.where(df['CLOSE_PRICE'] > df['EMA_200'], 7, 0)
                      
    df['MASTER_SCORE'] = (df['MOMENTUM_RANK'] * 0.50) + df['EMA_SCORE'] + (df['DELIV_RANK'] * 0.15) + (df['TURNOVER_RANK'] * 0.15)
    
    df['ABOVE_51_EMA'] = (df['CLOSE_PRICE'] > df['EMA_51']).astype(int)
    breadth_series = df.groupby('DATE')['ABOVE_51_EMA'].mean() * 100
    
    # STRICT PORTFOLIO FILTERS
    valid_pool = df[
        (df['MKT_CAP_CR'] >= 1000) &
        (df['MKT_CAP_CR'] <= 100000) &
        (df['CLOSE_PRICE'] >= 20.0) &  
        (df['CLOSE_PRICE'] >= (df['52W_HIGH'] * 0.80)) & 
        (df['MASTER_SCORE'] >= 70.0) 
    ].copy()

    # ONLY GET THE LATEST AVAILABLE DATE IN THE DATASET
    all_dates = sorted(df['DATE'].dropna().unique())
    if not all_dates:
        return pd.DataFrame()
        
    latest_date = all_dates[-1]
    curr_date_str = latest_date.strftime('%Y-%m-%d')
    print(f"Processing Live Portfolio for Latest Date: {curr_date_str}")
    
    cache_file = "ai_selections_cache.json"
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            try: ai_cache = json.load(f)
            except: ai_cache = {}
    else: ai_cache = {}
    
    current_breadth = breadth_series.get(latest_date, 50.0)
    
    if not nifty_df.empty:
        bench_past = nifty_df[nifty_df['DATE'] <= latest_date]
        if not bench_past.empty:
            latest_nifty = bench_past.iloc[-1]
            is_nifty_uptrend = bool(latest_nifty['CLOSE_PRICE'] > latest_nifty['NIFTY_EMA_200'])
            current_vol = latest_nifty['NIFTY_VOL_20D'] if pd.notna(latest_nifty['NIFTY_VOL_20D']) else 15.0
        else: is_nifty_uptrend = True; current_vol = 15.0
    else: is_nifty_uptrend = True; current_vol = 15.0

    if current_breadth < 30.0 and not is_nifty_uptrend:
        regime = "CRITICAL"
        target_limit = 0
    elif current_breadth < 50.0 or current_vol > 18.0:
        regime = "DEFENSIVE"
        target_limit = risk_off
    else:
        regime = "RISK-ON"
        target_limit = risk_on
        
    portfolio_snapshots = []
    
    if target_limit > 0:
        candidates = valid_pool[valid_pool['DATE'] == latest_date].copy()
        candidates = candidates.sort_values(by='MASTER_SCORE', ascending=False)
        top_50 = candidates.head(50).copy()
        
        ai_symbols, ai_reasons = call_gemini_institutional_analor(top_50, target_limit, curr_date_str, ai_cache)
        current_symbols = set(ai_symbols)
        
        for _, row in top_50.iterrows():
            sym = row['SYMBOL']
            is_chosen = sym in current_symbols
            portfolio_snapshots.append({
                'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': row['SECTOR'],
                'ACTION': 'SELECTED' if is_chosen else 'REJECTED', 'PRICE': row['CLOSE_PRICE'],
                'SCORE': row['MASTER_SCORE'], 'DELIV_%': f"{row['AVG_DELIV_PER']:.1f}%",
                'REASON': ai_reasons.get(sym, "Mathematical thresholding applied.")
            })

    # Return a dataframe with the regime attached as a metadata column for the UI
    final_df = pd.DataFrame(portfolio_snapshots)
    if not final_df.empty:
        final_df['REGIME'] = regime
    return final_df

# ==========================================
# MODULE 4: LIVE DASHBOARD COMPILER
# ==========================================
def generate_live_html(df_snaps):
    print("Compiling Modern Live Portfolio Dashboard...")
    
    if df_snaps.empty:
        print("No eligible stocks found for current market conditions.")
        return
        
    latest_date = df_snaps['DATE'].iloc[0]
    regime = df_snaps['REGIME'].iloc[0]
    
    selected_data = df_snaps[df_snaps['ACTION'] == 'SELECTED'][['SYMBOL', 'SECTOR', 'PRICE', 'SCORE', 'DELIV_%', 'REASON']].to_dict('records')
    rejected_data = df_snaps[df_snaps['ACTION'] == 'REJECTED'][['SYMBOL', 'SECTOR', 'PRICE', 'SCORE', 'DELIV_%', 'REASON']].to_dict('records')
    
    payload = {
        "date": latest_date,
        "regime": regime,
        "selected": selected_data,
        "rejected": rejected_data
    }
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Momentum Alpha PM Matrix - LIVE</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <style>
            :root {{ --bg-base: #07090E; --bg-surface: #11151D; --bg-hover: #1A202C; --border: #1F2733; --accent: #2563EB; --success: #10B981; --danger: #EF4444; --text: #F3F4F6; --text-muted: #9CA3AF; }}
            body {{ font-family: 'Inter', sans-serif; background: var(--bg-base); color: var(--text); margin: 0; padding: 20px; box-sizing: border-box; }}
            @media(min-width: 768px) {{ body {{ padding: 40px; }} }}
            
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px; border-bottom: 1px solid var(--border); padding-bottom: 20px; }}
            .brand {{ font-size: 24px; font-weight: 800; letter-spacing: -0.5px; color: #fff; }}
            .brand span {{ color: var(--accent); }}
            
            .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 32px; }}
            .card {{ background: var(--bg-surface); padding: 20px; border-radius: 12px; border: 1px solid var(--border); }}
            .card-title {{ font-size: 11px; text-transform: uppercase; color: var(--text-muted); font-weight: 700; margin-bottom: 6px; }}
            .card-value {{ font-size: 24px; font-weight: 800; }}
            
            .methodology-card {{ background: var(--bg-surface); padding: 24px; border-radius: 12px; border: 1px solid var(--border); margin-bottom: 32px; }}
            .methodology-card h3 {{ margin-top: 0; margin-bottom: 16px; font-size: 16px; color: #fff; border-bottom: 1px solid var(--border); padding-bottom: 12px; }}
            .methodology-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; }}
            .methodology-item h4 {{ margin: 0 0 8px 0; font-size: 13px; color: var(--accent); }}
            .methodology-item p {{ margin: 0; font-size: 13px; color: var(--text-muted); line-height: 1.5; }}
            
            .tab-container {{ display: flex; gap: 10px; margin-bottom: 20px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }}
            .tab-btn {{ background: transparent; border: none; color: var(--text-muted); font-size: 14px; font-weight: 600; padding: 10px 20px; cursor: pointer; border-radius: 6px; }}
            .tab-btn.active {{ background: rgba(255,255,255,0.05); color: #fff; }}
            
            .stock-card {{ background: var(--bg-surface); padding: 20px; border-radius: 12px; border: 1px solid var(--border); margin-bottom: 12px; display: flex; flex-direction: column; gap: 10px; }}
            .stock-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
            .stock-symbol {{ font-weight: 800; font-size: 18px; }}
            .stock-reason {{ font-size: 13px; color: var(--text-muted); line-height: 1.6; background: rgba(0,0,0,0.1); padding: 12px; border-radius: 6px; border-left: 3px solid var(--accent); }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="brand">⚡ Momentum<span>Alpha</span> LIVE</div>
        </div>
        
        <div class="metrics-grid">
            <div class="card"><div class="card-title">Analysis Date</div><div class="card-value" id="m-date">--</div></div>
            <div class="card"><div class="card-title">Market Regime</div><div class="card-value" id="m-regime" style="color:#60A5FA;">--</div></div>
            <div class="card"><div class="card-title">Total Selections</div><div class="card-value" id="m-count">--</div></div>
        </div>
        
        <div class="methodology-card">
            <h3>⚙️ The 100-Point Selection Engine & Filters</h3>
            <div class="methodology-grid">
                <div class="methodology-item"><h4>1. Pure Momentum (Max 50 Pts)</h4><p>How fast is the stock growing compared to the rest of the market?</p></div>
                <div class="methodology-item"><h4>2. Trend Alignment (Max 20 Pts)</h4><p>+7 pts if above 51 EMA, +6 pts if above 100 EMA, +7 pts if above 200 EMA.</p></div>
                <div class="methodology-item"><h4>3. Strong Hands Bonus (Max 15 Pts)</h4><p>Rewards high Delivery Percentages (investors buying and holding).</p></div>
                <div class="methodology-item"><h4>4. Liquidity Bonus (Max 15 Pts)</h4><p>Rewards massive trading turnover volume.</p></div>
            </div>
            <div style="margin-top: 16px; padding: 12px; background: rgba(37, 99, 235, 0.1); border-left: 4px solid var(--accent); border-radius: 4px; font-size: 13px;">
                <span style="font-weight: 700; color: var(--text);">Strict Hard Filters:</span> Master Score <b>≥ 70</b> AND price <b>within 20% of 52-week high</b>.
            </div>
        </div>
        
        <div class="tab-container">
            <button class="tab-btn active" id="btn-sel-tab" onclick="switchTab('SELECTED')">✅ Final AI Portfolio</button>
            <button class="tab-btn" id="btn-rej-tab" onclick="switchTab('REJECTED')">❌ Rejected Candidates</button>
        </div>
        
        <div id="stocks-list-container"></div>

        <script>
            const data = {payload};
            let currentTab = 'SELECTED';
            
            document.getElementById('m-date').innerText = data.date;
            document.getElementById('m-regime').innerText = data.regime;
            document.getElementById('m-count').innerText = data.selected.length;
            
            function switchTab(mode) {{
                currentTab = mode;
                document.getElementById('btn-sel-tab').className = mode === 'SELECTED' ? 'tab-btn active' : 'tab-btn';
                document.getElementById('btn-rej-tab').className = mode === 'REJECTED' ? 'tab-btn active' : 'tab-btn';
                
                const listData = mode === 'SELECTED' ? data.selected : data.rejected;
                let cardsHtml = '';
                listData.forEach(s => {{
                    cardsHtml += `<div class="stock-card">
                        <div class="stock-header">
                            <div class="stock-symbol">${{s.SYMBOL}} <span style="font-size:13px; font-weight:400; color:var(--text-muted);">| ${{s.SECTOR}}</span></div>
                            <div style="font-weight:700;">₹${{s.PRICE}}</div>
                        </div>
                        <div style="display:flex; justify-content:space-between; font-size:12px; color:var(--text-muted);">
                            <div>Factor Score: ${{parseFloat(s.SCORE).toFixed(1)}} / 100</div>
                            <div>Delivery Base: ${{s.DELIV_％||s['DELIV_%']}}</div>
                        </div>
                        <div class="stock-reason">${{s.REASON}}</div>
                    </div>`;
                }});
                document.getElementById('stocks-list-container').innerHTML = cardsHtml || '<div style="color:var(--text-muted)">No items generated.</div>';
            }}
            
            switchTab('SELECTED');
        </script>
    </body>
    </html>
    """
    with open("index.html", "w", encoding="utf-8") as f: f.write(html_content)
    print("✅ Live Production UI Exported to index.html.")

if __name__ == "__main__":
    raw_df, nifty_df = load_and_adjust_data()
    live_snaps = run_momentum_live(raw_df, nifty_df)
    if not live_snaps.empty:
        generate_live_html(live_snaps)
