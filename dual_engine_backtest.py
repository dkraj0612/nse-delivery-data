"""
live_momentum_engine.py - AI DUAL-PORTFOLIO DASHBOARD (LATEST ONLY)
===================================================================
Features: 100-Point Scoring, 70/30 Momentum, Live Portfolio Generation,
Interactive SPA HTML Dashboard, Bulletproof AI Text Parsing.
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
    """Bulletproof parser to handle weird AI string formatting."""
    symbols = []
    reasoning_map = {}
    
    # 1. Extract the array of final selections
    array_match = re.search(r'FINAL_SELECTIONS\s*=\s*\[(.*?)\]', text, re.DOTALL)
    if array_match:
        raw_syms = array_match.group(1).split(',')
        symbols = [re.sub(r'[^A-Z0-9-]', '', s.strip().upper()) for s in raw_syms if s.strip()]
            
    # 2. Extract the reasoning block by splitting on pipes
    for line in text.split('\n'):
        if '|' in line:
            parts = line.split('|', 1) # Split only on the first pipe
            sym_raw = parts[0].strip()
            reason = parts[1].strip()
            
            # Clean the symbol side to strictly A-Z, 0-9, and hyphens (removes markdown bolding, numbers)
            clean_sym = re.sub(r'[^A-Z0-9-]', '', sym_raw.upper())
            
            # Only save it if it looks like an actual ticker and not a header row
            if len(clean_sym) >= 2 and clean_sym != 'SYMBOL':
                reasoning_map[clean_sym] = reason
                
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
    
    # Calculate Sub-Scores (Saved to DataFrame for UI export)
    df['MOMENTUM_PTS'] = df.groupby('DATE')['PRICE_MOMENTUM'].rank(pct=True) * 50.0
    df['DELIV_PTS'] = df.groupby('DATE')['AVG_DELIV_PER'].rank(pct=True) * 15.0
    df['TURN_PTS'] = df.groupby('DATE')['AVG_TURNOVER'].rank(pct=True) * 15.0
    
    df['EMA_PTS'] = np.where(df['CLOSE_PRICE'] > df['EMA_51'], 7, 0) + \
                    np.where(df['CLOSE_PRICE'] > df['EMA_100'], 6, 0) + \
                    np.where(df['CLOSE_PRICE'] > df['EMA_200'], 7, 0)
                      
    df['MASTER_SCORE'] = df['MOMENTUM_PTS'] + df['EMA_PTS'] + df['DELIV_PTS'] + df['TURN_PTS']
    
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
        regime = "CRITICAL (Cash Only)"
        target_limit = 0
    elif current_breadth < 50.0 or current_vol > 18.0:
        regime = "DEFENSIVE (Reduced Allocation)"
        target_limit = risk_off
    else:
        regime = "RISK-ON (Full Allocation)"
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
                'SCORE': row['MASTER_SCORE'], 
                'MOM_PTS': row['MOMENTUM_PTS'], 'EMA_PTS': row['EMA_PTS'], 
                'DEL_PTS': row['DELIV_PTS'], 'TURN_PTS': row['TURN_PTS'],
                'ENTRY_DATE': curr_date_str, # Explicitly adding Entry Date
                'REASON': ai_reasons.get(sym, "Waiting for AI reasoning or skipped by PM layer.")
            })

    final_df = pd.DataFrame(portfolio_snapshots)
    if not final_df.empty:
        final_df['REGIME'] = regime
    return final_df

# ==========================================
# MODULE 4: INTERACTIVE UI DASHBOARD COMPILER
# ==========================================
def generate_live_html(df_snaps):
    print("Compiling Modern Interactive UI...")
    
    if df_snaps.empty:
        print("No eligible stocks found for current market conditions.")
        return
        
    latest_date = df_snaps['DATE'].iloc[0]
    regime = df_snaps['REGIME'].iloc[0]
    
    # Calculate Dashboard Metrics
    selected_df = df_snaps[df_snaps['ACTION'] == 'SELECTED']
    avg_score = selected_df['SCORE'].mean() if not selected_df.empty else 0
    sector_counts = selected_df['SECTOR'].value_counts().to_dict()
    
    # Format data for JS injection
    fields = ['SYMBOL', 'SECTOR', 'PRICE', 'SCORE', 'MOM_PTS', 'EMA_PTS', 'DEL_PTS', 'TURN_PTS', 'REASON', 'ENTRY_DATE']
    selected_data = selected_df[fields].to_dict('records')
    rejected_data = df_snaps[df_snaps['ACTION'] == 'REJECTED'][fields].to_dict('records')
    
    payload = {
        "date": latest_date,
        "regime": regime,
        "avg_score": round(avg_score, 1),
        "sector_labels": list(sector_counts.keys()),
        "sector_data": list(sector_counts.values()),
        "selected": selected_data,
        "rejected": rejected_data
    }
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Momentum Alpha Live</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            :root {{ --bg-base: #0B0F19; --bg-surface: #151A27; --bg-hover: #1E2538; --border: #2A344A; --accent: #3B82F6; --success: #10B981; --warning: #F59E0B; --danger: #EF4444; --text: #F8FAFC; --text-muted: #94A3B8; }}
            body {{ font-family: 'Inter', sans-serif; background: var(--bg-base); color: var(--text); margin: 0; display: flex; height: 100vh; overflow: hidden; }}
            
            /* Sidebar Layout */
            .sidebar {{ width: 280px; background: var(--bg-surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; }}
            .brand {{ padding: 24px; font-size: 20px; font-weight: 800; border-bottom: 1px solid var(--border); letter-spacing: -0.5px; }}
            .brand i {{ color: var(--accent); margin-right: 8px; }}
            .nav-menu {{ padding: 16px; display: flex; flex-direction: column; gap: 8px; flex-grow: 1; }}
            .nav-btn {{ background: transparent; color: var(--text-muted); border: none; padding: 14px 16px; border-radius: 8px; cursor: pointer; text-align: left; font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 12px; transition: all 0.2s; }}
            .nav-btn:hover {{ background: var(--bg-hover); color: var(--text); }}
            .nav-btn.active {{ background: rgba(59, 130, 246, 0.1); color: var(--accent); border: 1px solid rgba(59, 130, 246, 0.2); }}
            
            /* Main Content Area */
            .main-content {{ flex-grow: 1; overflow-y: auto; padding: 32px; scroll-behavior: smooth; }}
            .view-section {{ display: none; animation: fadeIn 0.3s ease; }}
            .view-section.active {{ display: block; }}
            @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
            
            .page-title {{ font-size: 28px; font-weight: 800; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: center; }}
            .timestamp-badge {{ background: var(--bg-hover); padding: 6px 12px; border-radius: 6px; font-size: 13px; font-weight: 600; color: var(--text-muted); border: 1px solid var(--border); }}
            
            /* Dashboard Grid */
            .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 20px; margin-bottom: 32px; }}
            .card {{ background: var(--bg-surface); padding: 24px; border-radius: 16px; border: 1px solid var(--border); box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
            .card-icon {{ width: 40px; height: 40px; border-radius: 10px; background: rgba(59, 130, 246, 0.1); color: var(--accent); display: flex; align-items: center; justify-content: center; font-size: 18px; margin-bottom: 16px; }}
            .card-title {{ font-size: 13px; text-transform: uppercase; color: var(--text-muted); font-weight: 700; margin-bottom: 8px; letter-spacing: 0.5px; }}
            .card-value {{ font-size: 28px; font-weight: 800; }}
            
            .dashboard-layout {{ display: grid; grid-template-columns: 1fr 350px; gap: 24px; }}
            .chart-card {{ background: var(--bg-surface); padding: 24px; border-radius: 16px; border: 1px solid var(--border); }}
            .methodology-card {{ background: var(--bg-surface); padding: 24px; border-radius: 16px; border: 1px solid var(--border); }}
            .method-row {{ display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid var(--border); }}
            .method-row:last-child {{ border-bottom: none; }}
            
            /* Portfolio Grid View */
            .controls-bar {{ display: flex; justify-content: space-between; margin-bottom: 20px; }}
            .search-box {{ background: var(--bg-surface); border: 1px solid var(--border); color: var(--text); padding: 12px 16px; border-radius: 8px; width: 300px; font-family: 'Inter'; outline: none; }}
            .search-box:focus {{ border-color: var(--accent); }}
            
            .portfolio-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 24px; }}
            .stock-card {{ background: var(--bg-surface); border-radius: 16px; border: 1px solid var(--border); overflow: hidden; transition: transform 0.2s, border-color 0.2s; }}
            .stock-card:hover {{ transform: translateY(-3px); border-color: var(--accent); }}
            .sc-header {{ padding: 20px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: flex-start; background: rgba(0,0,0,0.1); }}
            .sc-symbol {{ font-size: 22px; font-weight: 800; margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }}
            .sc-sector {{ font-size: 13px; color: var(--text-muted); font-weight: 500; display: block; }}
            .sc-score-badge {{ background: rgba(16, 185, 129, 0.1); color: var(--success); padding: 8px 14px; border-radius: 12px; font-weight: 800; font-size: 18px; border: 1px solid rgba(16, 185, 129, 0.2); }}
            
            .sc-body {{ padding: 20px; }}
            .score-breakdown {{ margin-bottom: 20px; }}
            .sb-row {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; font-size: 12px; font-weight: 600; color: #E2E8F0; }}
            .sb-bar-bg {{ height: 6px; background: var(--bg-hover); border-radius: 3px; overflow: hidden; margin-bottom: 12px; }}
            .sb-bar-fill {{ height: 100%; border-radius: 3px; }}
            
            .sc-reason {{ background: rgba(59, 130, 246, 0.05); padding: 16px; border-radius: 8px; border-left: 4px solid var(--accent); font-size: 14px; line-height: 1.6; color: var(--text); border: 1px solid rgba(59, 130, 246, 0.1); }}
            .sc-reason-title {{ font-size: 12px; text-transform: uppercase; font-weight: 800; color: var(--accent); margin-bottom: 8px; letter-spacing: 0.5px; display: flex; align-items: center; gap: 6px; }}
        </style>
    </head>
    <body>
        
        <div class="sidebar">
            <div class="brand"><i class="fas fa-bolt"></i> Momentum Alpha</div>
            <div class="nav-menu">
                <button class="nav-btn active" onclick="switchView('dashboard')" id="btn-dashboard"><i class="fas fa-chart-pie"></i> Metrics Dashboard</button>
                <button class="nav-btn" onclick="switchView('selected')" id="btn-selected"><i class="fas fa-check-circle" style="color: var(--success)"></i> Selected Portfolio <span style="background:var(--bg-hover); padding:2px 8px; border-radius:10px; font-size:12px; margin-left:auto;">{len(selected_data)}</span></button>
                <button class="nav-btn" onclick="switchView('rejected')" id="btn-rejected"><i class="fas fa-times-circle" style="color: var(--danger)"></i> Rejected Candidates</button>
            </div>
            <div style="padding: 24px; font-size: 12px; color: var(--text-muted); text-align: center; border-top: 1px solid var(--border);">
                Algorithmic Scoring Engine v2.0
            </div>
        </div>
        
        <div class="main-content">
            
            <!-- DASHBOARD VIEW -->
            <div id="view-dashboard" class="view-section active">
                <div class="page-title">
                    Live System Dashboard 
                    <span class="timestamp-badge"><i class="far fa-clock"></i> As of {latest_date}</span>
                </div>
                
                <div class="metrics-grid">
                    <div class="card">
                        <div class="card-icon"><i class="fas fa-chess-board"></i></div>
                        <div class="card-title">Market Regime Framework</div>
                        <div class="card-value" style="color: var(--accent); font-size: 22px;">{regime}</div>
                    </div>
                    <div class="card">
                        <div class="card-icon"><i class="fas fa-layer-group"></i></div>
                        <div class="card-title">Final AI Allocations</div>
                        <div class="card-value">{len(selected_data)} <span style="font-size:14px; color:var(--text-muted); font-weight:500;">/ 50 Screened</span></div>
                    </div>
                    <div class="card">
                        <div class="card-icon"><i class="fas fa-star"></i></div>
                        <div class="card-title">Average Portfolio Score</div>
                        <div class="card-value">{round(avg_score, 1)} <span style="font-size:14px; color:var(--text-muted); font-weight:500;">/ 100</span></div>
                    </div>
                </div>
                
                <div class="dashboard-layout">
                    <div class="chart-card">
                        <h3 style="margin-top:0; color:var(--text-muted); font-size:14px; text-transform:uppercase;">Sector Allocation</h3>
                        <div style="height: 300px; display:flex; justify-content:center;">
                            <canvas id="sectorChart"></canvas>
                        </div>
                    </div>
                    <div class="methodology-card">
                        <h3 style="margin-top:0; margin-bottom:20px; color:var(--text-muted); font-size:14px; text-transform:uppercase;"><i class="fas fa-cogs"></i> Scoring Engine (100 Pts)</h3>
                        <div class="method-row"><div>Pure Momentum</div><div style="font-weight:700; color:#3B82F6;">Max 50 pts</div></div>
                        <div class="method-row"><div>Trend Alignment (EMAs)</div><div style="font-weight:700; color:#10B981;">Max 20 pts</div></div>
                        <div class="method-row"><div>Strong Hands (Delivery %)</div><div style="font-weight:700; color:#F59E0B;">Max 15 pts</div></div>
                        <div class="method-row"><div>Liquidity (Turnover)</div><div style="font-weight:700; color:#8B5CF6;">Max 15 pts</div></div>
                        <div style="margin-top:20px; padding:12px; background:rgba(239, 68, 68, 0.1); border-left:3px solid var(--danger); border-radius:6px; font-size:13px;">
                            <strong>Hard Filter:</strong> Must score ≥70 and price within 20% of 52-week high.
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- SELECTED PORTFOLIO VIEW -->
            <div id="view-selected" class="view-section">
                <div class="page-title">AI Selected Portfolio</div>
                <div class="controls-bar">
                    <input type="text" class="search-box" id="search-sel" placeholder="Search symbol or sector..." onkeyup="filterCards('search-sel', 'grid-sel')">
                </div>
                <div class="portfolio-grid" id="grid-sel"></div>
            </div>
            
            <!-- REJECTED VIEW -->
            <div id="view-rejected" class="view-section">
                <div class="page-title">Rejected Candidates</div>
                <div class="controls-bar">
                    <input type="text" class="search-box" id="search-rej" placeholder="Search symbol or sector..." onkeyup="filterCards('search-rej', 'grid-rej')">
                </div>
                <div class="portfolio-grid" id="grid-rej"></div>
            </div>
            
        </div>

        <script>
            const coreData = {payload};
            
            // View Switching Logic
            function switchView(viewName) {{
                document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));
                
                document.getElementById('view-' + viewName).classList.add('active');
                document.getElementById('btn-' + viewName).classList.add('active');
            }}
            
            // Search Filter Logic
            function filterCards(inputId, gridId) {{
                let input = document.getElementById(inputId).value.toUpperCase();
                let grid = document.getElementById(gridId);
                let cards = grid.getElementsByClassName('stock-card');
                
                for (let i = 0; i < cards.length; i++) {{
                    let text = cards[i].innerText.toUpperCase();
                    cards[i].style.display = text.indexOf(input) > -1 ? "" : "none";
                }}
            }}
            
            // Render Cards Function
            function renderCards(dataList, containerId, mode) {{
                let html = '';
                dataList.forEach(s => {{
                    let total = parseFloat(s.SCORE).toFixed(1);
                    let mPct = (s.MOM_PTS / 50) * 100;
                    let ePct = (s.EMA_PTS / 20) * 100;
                    let dPct = (s.DEL_PTS / 15) * 100;
                    let tPct = (s.TURN_PTS / 15) * 100;
                    
                    let badgeColor = total >= 85 ? 'var(--success)' : (total >= 75 ? 'var(--accent)' : 'var(--warning)');
                    let badgeBg = total >= 85 ? 'rgba(16,185,129,0.1)' : (total >= 75 ? 'rgba(59,130,246,0.1)' : 'rgba(245,158,11,0.1)');
                    
                    let statusText = mode === 'SELECTED' ? `<span style="color:var(--success);">Active Hold</span>` : `<span style="color:var(--danger);">Rejected</span>`;
                    let entryText = mode === 'SELECTED' ? `Entry: ${{s.ENTRY_DATE}}` : `Evaluated: ${{s.ENTRY_DATE}}`;

                    html += `
                    <div class="stock-card">
                        <div class="sc-header">
                            <div>
                                <div class="sc-symbol">${{s.SYMBOL}}</div>
                                <div class="sc-sector">${{s.SECTOR}} &nbsp;&bull;&nbsp; ${{entryText}} &nbsp;&bull;&nbsp; ${{statusText}}</div>
                            </div>
                            <div class="sc-score-badge" style="color: ${{badgeColor}}; background: ${{badgeBg}}; border-color: ${{badgeColor}}40">
                                ${{total}}
                            </div>
                        </div>
                        <div class="sc-body">
                            <div class="score-breakdown">
                                <div class="sb-row"><span>Momentum</span><span>${{s.MOM_PTS.toFixed(1)}} / 50</span></div>
                                <div class="sb-bar-bg"><div class="sb-bar-fill" style="width:${{mPct}}%; background:#3B82F6;"></div></div>
                                
                                <div class="sb-row"><span>Trend (EMA)</span><span>${{s.EMA_PTS.toFixed(1)}} / 20</span></div>
                                <div class="sb-bar-bg"><div class="sb-bar-fill" style="width:${{ePct}}%; background:#10B981;"></div></div>
                                
                                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:16px;">
                                    <div>
                                        <div class="sb-row"><span>Delivery</span><span>${{s.DEL_PTS.toFixed(1)}}</span></div>
                                        <div class="sb-bar-bg"><div class="sb-bar-fill" style="width:${{dPct}}%; background:#F59E0B;"></div></div>
                                    </div>
                                    <div>
                                        <div class="sb-row"><span>Volume</span><span>${{s.TURN_PTS.toFixed(1)}}</span></div>
                                        <div class="sb-bar-bg"><div class="sb-bar-fill" style="width:${{tPct}}%; background:#8B5CF6;"></div></div>
                                    </div>
                                </div>
                            </div>
                            <div class="sc-reason">
                                <div class="sc-reason-title"><i class="fas fa-brain"></i> AI Portfolio Manager Analysis</div>
                                ${{s.REASON}}
                            </div>
                        </div>
                    </div>`;
                }});
                document.getElementById(containerId).innerHTML = html;
            }}
            
            // Initialization
            renderCards(coreData.selected, 'grid-sel', 'SELECTED');
            renderCards(coreData.rejected, 'grid-rej', 'REJECTED');
            
            // Render Donut Chart
            if(coreData.sector_labels.length > 0) {{
                const ctx = document.getElementById('sectorChart').getContext('2d');
                new Chart(ctx, {{
                    type: 'doughnut',
                    data: {{
                        labels: coreData.sector_labels,
                        datasets: [{{
                            data: coreData.sector_data,
                            backgroundColor: ['#3B82F6', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899', '#06B6D4', '#64748B'],
                            borderWidth: 0,
                            hoverOffset: 4
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {{
                            legend: {{ position: 'right', labels: {{ color: '#94A3B8', font: {{ family: 'Inter', size: 12 }} }} }}
                        }},
                        cutout: '70%'
                    }}
                }});
            }}
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
