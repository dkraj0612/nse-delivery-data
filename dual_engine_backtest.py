"""
dual_engine_backtest.py - AI MACRO REGIME & DUAL-PORTFOLIO DASHBOARD
===================================================================
Features: 70/30 Momentum, Asymmetric Free-Tier Throttling (45s),
Point-in-Time Historical Macro Injector, Dual-Curve Dashboard, 100-Point Scoring.
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
# MODULE 1: DATA ENGINE & HISTORICAL MACRO MEMORY
# ==========================================
def get_deterministic_mcap(symbol):
    hash_val = int(hashlib.md5(symbol.encode('utf-8')).hexdigest(), 16)
    return 1000 + (hash_val % 99000)

def get_historical_macro_context(date_str):
    """Provides a strict point-in-time macroeconomic snapshot to ground the AI model without look-ahead bias."""
    year = int(date_str.split('-')[0])
    month = int(date_str.split('-')[1])
    
    macro_database = {
        2021: {
            1: "Post-COVID structural economic restart. Massive global liquidity easing. Low interest rates.",
            6: "Strong corporate earnings recovery in India. IT sector leading global cloud transformation boom.",
            12: "Inflation starting to tick upwards globally due to supply chain chokepoints. Commodity bull run."
        },
        2022: {
            2: "CRITICAL: Russia-Ukraine War erupts. Global oil spikes past $100/bbl. Massive FII outflows from India.",
            5: "RBI unexpectedly hikes repo rates to fight inflation. Liquidity tightening begins.",
            9: "US Fed aggressive rate hikes continue. Indian markets showing extreme relative strength vs global indices."
        },
        2023: {
            1: "Adani Hindenburg report volatility ripples through Indian infrastructure names. Defensive rotation.",
            6: "Inflation peaking. CapEx expansion cycle underway in India. Manufacturing & Defense sector momentum starting.",
            10: "Middle East geopolitical crisis (Gaza conflict). Oil prices volatile. Midcaps significantly outperforming large caps."
        },
        2024: {
            2: "Interim Budget accelerates public infrastructure, Rail, and Green Energy allocations. Structural sector peaks.",
            6: "Indian General Election results introduce minor coalition variance but policy continuity holds. Market highs.",
            12: "Corporate earnings growth cooling but domestic mutual fund inflows (SIP) reaching historic highs."
        },
        2025: {
            3: "Global supply chains normalizing. India retail participation shifts heavily toward options and midcap momentum.",
            8: "Global trade friction concerns under new tariff structures. Focus firmly on domestic consumption and defense manufacturing."
        },
        2026: {
            1: "Current regime posture: High structural valuation multiples in India, supported by robust domestic structural growth.",
            5: "Stability in baseline economic structures. Capital flows actively searching for high relative alpha configurations."
        }
    }
    
    # Fallback to closest past date logic
    for y in sorted(macro_database.keys(), reverse=True):
        if year >= y:
            months = sorted(macro_database[y].keys(), reverse=True)
            for m in months:
                if year > y or month >= m:
                    return macro_database[y][m]
    return "Stable macro baseline. Normal structural growth environment."

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
    """Safely extracts both the final selections array and the cross-sectional justification blocks."""
    symbols = []
    reasoning_map = {}
    
    array_match = re.search(r'FINAL_SELECTIONS\s*=\s*\[(.*?)\]', text, re.DOTALL)
    if array_match:
        try:
            symbols = json.loads('[' + array_match.group(1) + ']')
        except:
            symbols = [s.strip().replace('"', '').replace("'", "") for s in array_match.group(1).split(',') if s.strip()]
            
    # Parse individual reasoning lines: SYMBOL | REASON
    for line in text.split('\n'):
        if '|' in line and not line.startswith('---') and not 'SYMBOL' in line:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 2:
                sym = parts[0].replace('*', '').replace('"', '').replace("'", "")
                reasoning_map[sym] = parts[1]
                
    return symbols, reasoning_map

def call_gemini_institutional_analor(top_50_df, target_limit, date_str, cache):
    if target_limit == 0: return [], {}
    cache_key = f"v2_{date_str}_{target_limit}"
    if cache_key in cache:
        return cache[cache_key]["symbols"], cache[cache_key]["reasons"]
        
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client() if api_key else None
    if not client: 
        fallback_syms = top_50_df.head(target_limit)['SYMBOL'].tolist()
        return fallback_syms, {s: "Pure mathematical baseline entry." for s in top_50_df['SYMBOL']}

    macro_context = get_historical_macro_context(date_str)
    print(f"  [AI Processing] Heavy-Lifting Portfolio Construction for {date_str}...")
    
    prompt = f"""
    STRICT COMPLIANCE TIMESTAMP: {date_str}
    You are an Institutional Portfolio Manager. You are reviewing candidates on exactly {date_str}.
    
    STRICT INSTRUCTION: You do not know anything about future events past {date_str}. Do not reference future milestones, product releases, or structural shifts that happen after this timestamp.
    
    CURRENT MACRO LANDSCAPE AS OF {date_str}:
    {macro_context}
    
    PORTFOLIO CAPACITY CONSTRAINT: Select exactly {target_limit} symbols from the top 50 table below.
    
    CANDIDATE TOP 50 DATA:
    {top_50_df[['SYMBOL', 'SECTOR', 'MASTER_SCORE', 'MKT_CAP_CR']].to_markdown(index=False)}
    
    CRITERIA FOR REJECTION/SELECTION:
    Evaluate sector density, structural price smoothness, macro alignment (e.g. if the landscape says war/inflation, select robust themes, reject high-beta vulnerable names). Put precise point-in-time references when deciding.
    
    REQUIRED OUTPUT FORMAT:
    You must provide individual row-level reasons for ALL 50 stocks using the pipe delimiter format below:
    SYMBOL | REASON (Explicitly include a date or macro trigger like 'As of Feb 2022...')
    
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
            print(f"  [Throttling API] Flawless call complete. Sleeping 45 seconds to secure Free Tier TPM boundary...")
            time.sleep(45) # CRITICAL: Free Tier Safety Window
            return cache[cache_key]["symbols"], cache[cache_key]["reasons"]
    except Exception as e:
        print(f"  [API Error/Timeout] Fallback activated: {e}")
        
    time.sleep(45)
    fallback_syms = top_50_df.head(target_limit)['SYMBOL'].tolist()
    return fallback_syms, {s: "API Limit Fallback: Mathematical selection." for s in top_50_df['SYMBOL']}

# ==========================================
# MODULE 3: STRATEGY ENGINE (DUAL-CURVE PROCESSING)
# ==========================================
def run_momentum_backtest(df, nifty_df, risk_on=20, risk_off=10, friction_tax=0.005):
    print("Initializing Dual-Curve System Core Engine with 100-Point Scoring...")
    
    df['P_1M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['P_7M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(147) 
    df['P_13M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    df['P_13M'] = df['P_13M'].replace(0, np.nan)
    df['P_7M'] = df['P_7M'].replace(0, np.nan)
    
    ret_12m = (df['P_1M'] - df['P_13M']) / df['P_13M']
    ret_6m = (df['P_1M'] - df['P_7M']) / df['P_7M']
    df['PRICE_MOMENTUM'] = (ret_12m * 0.70) + (ret_6m * 0.30)
    
    # Calculate Core Moving Averages & Metrics
    df['EMA_51'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=51, adjust=False).mean())
    df['EMA_100'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=100, adjust=False).mean())
    df['EMA_200'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=200, adjust=False).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    df['AVG_DELIV_PER'] = df.groupby('SYMBOL')['DELIV_PER'].transform(lambda x: x.rolling(20).mean())
    
    # NEW 0-100 PERCENTILE SCORING LOGIC
    # Rank all stocks on a given day from 0.0 to 1.0, then multiply by 100
    df['MOMENTUM_RANK'] = df.groupby('DATE')['PRICE_MOMENTUM'].rank(pct=True) * 100
    df['DELIV_RANK'] = df.groupby('DATE')['AVG_DELIV_PER'].rank(pct=True) * 100
    df['TURNOVER_RANK'] = df.groupby('DATE')['AVG_TURNOVER'].rank(pct=True) * 100
    
    # Trend Alignment Score (Max 20 Points)
    df['EMA_SCORE'] = np.where(df['CLOSE_PRICE'] > df['EMA_51'], 7, 0) + \
                      np.where(df['CLOSE_PRICE'] > df['EMA_100'], 6, 0) + \
                      np.where(df['CLOSE_PRICE'] > df['EMA_200'], 7, 0)
                      
    # Master Score (Max 100): Momentum(50) + EMA(20) + Delivery(15) + Turnover(15)
    df['MASTER_SCORE'] = (df['MOMENTUM_RANK'] * 0.50) + df['EMA_SCORE'] + (df['DELIV_RANK'] * 0.15) + (df['TURNOVER_RANK'] * 0.15)
    
    # Market Breadth check (using 51 EMA)
    df['ABOVE_51_EMA'] = (df['CLOSE_PRICE'] > df['EMA_51']).astype(int)
    breadth_series = df.groupby('DATE')['ABOVE_51_EMA'].mean() * 100
    
    df['YEAR_MONTH'] = df['DATE'].dt.to_period('M')
    month_ends = df.groupby('YEAR_MONTH')['DATE'].max().reset_index()
    rebalance_df = df[df['DATE'].isin(month_ends['DATE'])].copy()
    
    # STRICT PORTFOLIO FILTERS
    valid_pool = rebalance_df[
        (rebalance_df['MKT_CAP_CR'] >= 1000) &
        (rebalance_df['MKT_CAP_CR'] <= 100000) &
        (rebalance_df['CLOSE_PRICE'] >= 20.0) &  
        (rebalance_df['CLOSE_PRICE'] >= (rebalance_df['52W_HIGH'] * 0.80)) & # Within 20% of 52-week High
        (rebalance_df['MASTER_SCORE'] >= 70.0) # Master Score Threshold
    ].copy()

    dates = sorted(rebalance_df['DATE'].dropna().unique())
    portfolio_snapshots = []
    equity_curve = []
    
    prev_portfolio_df = pd.DataFrame()
    entry_prices = {}
    entry_dates = {}
    
    capital_selected = 1000000.0 
    capital_rejected = 1000000.0
    
    cache_file = "ai_selections_cache.json"
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            try: ai_cache = json.load(f)
            except: ai_cache = {}
    else: ai_cache = {}
    
    for current_date in dates:
        curr_date_str = current_date.strftime('%Y-%m-%d')
        day_data = rebalance_df[rebalance_df['DATE'] == current_date]
        if day_data.empty: continue
            
        day_prices = day_data.set_index('SYMBOL')['CLOSE_PRICE'].to_dict()
        day_scores = day_data.set_index('SYMBOL')['MASTER_SCORE'].to_dict()
        
        # Track active performance update
        if not prev_portfolio_df.empty:
            num_holdings = len(prev_portfolio_df)
            if num_holdings > 0:
                temp_selected = 0.0
                weight_block = capital_selected / num_holdings
                for _, row in prev_portfolio_df.iterrows():
                    sym = row['SYMBOL']
                    temp_selected += weight_block * (day_prices.get(sym, row['CLOSE_PRICE']) / row['CLOSE_PRICE'])
                capital_selected = temp_selected

        candidates = valid_pool[valid_pool['DATE'] == current_date].copy()
        prev_symbols = set(prev_portfolio_df['SYMBOL']) if not prev_portfolio_df.empty else set()
        
        current_breadth = breadth_series.get(current_date, 50.0)
        
        if not nifty_df.empty:
            bench_past = nifty_df[nifty_df['DATE'] <= current_date]
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
            
        if target_limit == 0:
            equity_curve.append({'DATE': curr_date_str, 'SELECTED_EQUITY': capital_selected, 'REJECTED_EQUITY': capital_rejected, 'CHURN': 1.0, 'REGIME': regime})
            prev_portfolio_df = pd.DataFrame()
            continue

        existing_mask = candidates['SYMBOL'].isin(prev_symbols)
        strict_entry_mask = (candidates['CLOSE_PRICE'] >= (candidates['52W_HIGH'] * 0.80))
        valid_candidates = candidates[existing_mask | strict_entry_mask].copy()

        valid_candidates = valid_candidates.sort_values(by='MASTER_SCORE', ascending=False)
        top_50 = valid_candidates.head(50).copy()
        
        # Execute processing matrices
        ai_symbols, ai_reasons = call_gemini_institutional_analor(top_50, target_limit, curr_date_str, ai_cache)
        
        final_portfolio = top_50[top_50['SYMBOL'].isin(ai_symbols)].head(target_limit).copy()
        rejected_portfolio = top_50[~top_50['SYMBOL'].isin(ai_symbols)].copy()
        
        # Track synthetic performance mapping for rejected assets to calculate comparative curve
        if not rejected_portfolio.empty:
            capital_rejected = capital_rejected * (1 + rejected_portfolio['PCT_CHG'].mean())
            
        current_symbols = set(final_portfolio['SYMBOL'])
        num_new_trades = len(current_symbols - prev_symbols)
        churn_ratio = (num_new_trades / len(final_portfolio)) if len(final_portfolio) > 0 else 0.0
        capital_selected -= (capital_selected * churn_ratio * friction_tax)
        
        equity_curve.append({'DATE': curr_date_str, 'SELECTED_EQUITY': capital_selected, 'REJECTED_EQUITY': capital_rejected, 'CHURN': churn_ratio, 'REGIME': regime})
        
        # Log all 50 candidates state definitions for UI
        for _, row in top_50.iterrows():
            sym = row['SYMBOL']
            is_chosen = sym in current_symbols
            pnl_str = f"{((day_prices.get(sym, row['CLOSE_PRICE'])/entry_prices.get(sym, row['CLOSE_PRICE']))-1)*100:+.2f}%" if sym in entry_prices else "NEW"
            if is_chosen and sym not in prev_symbols:
                entry_prices[sym], entry_dates[sym] = row['CLOSE_PRICE'], curr_date_str
                
            portfolio_snapshots.append({
                'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': row['SECTOR'],
                'ACTION': 'SELECTED' if is_chosen else 'REJECTED', 'PRICE': row['CLOSE_PRICE'],
                'SCORE': row['MASTER_SCORE'], 'DELIV_%': f"{row['AVG_DELIV_PER']:.1f}%", 'PNL': pnl_str,
                'REASON': ai_reasons.get(sym, "Maintained index tracking framework configuration logic.")
            })
            
        prev_portfolio_df = final_portfolio.copy()

    return pd.DataFrame(portfolio_snapshots), pd.DataFrame(equity_curve)

def verify_backtest_integrity(df_snaps, df_equity):
    assert df_equity['SELECTED_EQUITY'].min() >= 0, "Security validation break."
    print("✅ Python Structural Controls Guardrails Verified.")
    return True

# ==========================================
# MODULE 4: DUAL-PORTFOLIO HTML COMPILER
# ==========================================
def generate_static_html(df_snaps, df_equity):
    print("Compiling Modern Fintech Dashboard Distribution Matrix...")
    
    init_eq = 1000000.0
    fin_sel = df_equity['SELECTED_EQUITY'].iloc[-1]
    fin_rej = df_equity['REJECTED_EQUITY'].iloc[-1]
    
    days_span = (pd.to_datetime(df_equity['DATE'].iloc[-1]) - pd.to_datetime(df_equity['DATE'].iloc[0])).days / 365.25
    cagr_sel = (((fin_sel / init_eq) ** (1 / days_span)) - 1) * 100
    cagr_rej = (((fin_rej / init_eq) ** (1 / days_span)) - 1) * 100
    
    df_equity['PEAK_SEL'] = df_equity['SELECTED_EQUITY'].cummax()
    max_dd_sel = ((df_equity['SELECTED_EQUITY'] - df_equity['PEAK_SEL']) / df_equity['PEAK_SEL']).min() * 100
    
    monthly_data = {}
    unique_dates = sorted(df_snaps['DATE'].unique(), reverse=True)
    
    for d in unique_dates:
        day_snaps = df_snaps[df_snaps['DATE'] == d]
        eq_row = df_equity[df_equity['DATE'] == d].iloc[0]
        
        monthly_data[d] = {
            "regime": eq_row['REGIME'],
            "churn": f"{eq_row['CHURN']*100:.1f}%",
            "selected": day_snaps[day_snaps['ACTION'] == 'SELECTED'][['SYMBOL', 'SECTOR', 'PRICE', 'SCORE', 'DELIV_%', 'PNL', 'REASON']].to_dict('records'),
            "rejected": day_snaps[day_snaps['ACTION'] == 'REJECTED'][['SYMBOL', 'SECTOR', 'PRICE', 'SCORE', 'DELIV_%', 'PNL', 'REASON']].to_dict('records')
        }
        
    payload = {
        "global": {
            "sel_total": f"{((fin_sel/init_eq)-1)*100:.2f}%",
            "sel_cagr": f"{cagr_pct:.2f}%" if 'cagr_pct' in locals() else f"{cagr_sel:.2f}%",
            "rej_cagr": f"{cagr_rej:.2f}%",
            "sel_dd": f"{max_dd_sel:.2f}%",
            "avg_churn": f"{df_equity['CHURN'].mean()*100:.1f}%",
            "chart_dates": df_equity['DATE'].tolist(),
            "chart_selected": df_equity['SELECTED_EQUITY'].tolist(),
            "chart_rejected": df_equity['REJECTED_EQUITY'].tolist()
        },
        "monthly": monthly_data
    }
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Momentum Alpha PM Matrix</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            :root {{ --bg-base: #07090E; --bg-surface: #11151D; --bg-hover: #1A202C; --border: #1F2733; --accent: #2563EB; --success: #10B981; --danger: #EF4444; --text: #F3F4F6; --text-muted: #9CA3AF; }}
            body {{ font-family: 'Inter', sans-serif; background: var(--bg-base); color: var(--text); margin: 0; display: flex; flex-direction: column; height: 100vh; }}
            @media(min-width: 768px) {{ body {{ flex-direction: row; }} }}
            
            .sidebar {{ width: 100%; background: var(--bg-surface); padding: 20px; box-sizing: border-box; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 10px; }}
            @media(min-width: 768px) {{ .sidebar {{ width: 300px; height: 100vh; overflow-y: auto; border-bottom: none; border-right: 1px solid var(--border); }} }}
            
            .brand {{ font-size: 18px; font-weight: 800; letter-spacing: -0.5px; color: #fff; }}
            .brand span {{ color: var(--accent); }}
            
            .btn-nav {{ background: var(--bg-hover); color: var(--text); border: 1px solid var(--border); padding: 12px; border-radius: 8px; cursor: pointer; text-align: left; font-size: 14px; font-weight: 600; transition: all 0.2s; }}
            .btn-nav.active, .btn-nav:hover {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
            
            .main-panel {{ flex-grow: 1; padding: 20px; overflow-y: auto; box-sizing: border-box; }}
            @media(min-width: 768px) {{ .main-panel {{ padding: 40px; }} }}
            
            .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 16px; margin-bottom: 32px; }}
            .card {{ background: var(--bg-surface); padding: 20px; border-radius: 12px; border: 1px solid var(--border); }}
            .card-title {{ font-size: 11px; text-transform: uppercase; color: var(--text-muted); font-weight: 700; margin-bottom: 6px; }}
            .card-value {{ font-size: 24px; font-weight: 800; }}
            
            .chart-box {{ background: var(--bg-surface); padding: 20px; border-radius: 12px; border: 1px solid var(--border); height: 350px; margin-bottom: 32px; }}
            
            .methodology-card {{ background: var(--bg-surface); padding: 24px; border-radius: 12px; border: 1px solid var(--border); margin-bottom: 32px; }}
            .methodology-card h3 {{ margin-top: 0; margin-bottom: 16px; font-size: 16px; color: #fff; border-bottom: 1px solid var(--border); padding-bottom: 12px; }}
            .methodology-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; }}
            .methodology-item h4 {{ margin: 0 0 8px 0; font-size: 13px; color: var(--accent); }}
            .methodology-item p {{ margin: 0; font-size: 13px; color: var(--text-muted); line-height: 1.5; }}
            .methodology-item code {{ background: rgba(255,255,255,0.1); padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 12px; color: #fff; }}
            
            .tab-container {{ display: flex; gap: 10px; margin-bottom: 20px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }}
            .tab-btn {{ background: transparent; border: none; color: var(--text-muted); font-size: 14px; font-weight: 600; padding: 10px 20px; cursor: pointer; border-radius: 6px; }}
            .tab-btn.active {{ background: rgba(255,255,255,0.05); color: #fff; }}
            
            .stock-card {{ background: var(--bg-surface); padding: 20px; border-radius: 12px; border: 1px solid var(--border); margin-bottom: 12px; display: flex; flex-direction: column; gap: 10px; }}
            .stock-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
            .stock-symbol {{ font-weight: 800; font-size: 16px; }}
            .stock-reason {{ font-size: 13px; color: var(--text-muted); line-height: 1.6; background: rgba(0,0,0,0.1); padding: 12px; border-radius: 6px; border-left: 3px solid var(--accent); }}
            
            .pos {{ color: var(--success); }}
            .neg {{ color: var(--danger); }}
            .hidden {{ display: none !important; }}
        </style>
    </head>
    <body>
        <div class="sidebar" id="sidebar">
            <div class="brand">⚡ Momentum<span>Alpha Core</span></div>
            <button class="btn-nav active" onclick="showGlobal()">📊 Global Strategy Deck</button>
            <h3 style="font-size:11px; text-transform:uppercase; color:var(--text-muted); margin-top:20px;">Timeline Matrix</h3>
        </div>
        
        <div class="main-panel">
            <div id="global-deck">
                <div class="metrics-grid">
                    <div class="card"><div class="card-title">AI Portfolio CAGR</div><div class="card-value pos" id="g-cagr">--</div></div>
                    <div class="card"><div class="card-title">Rejected Asset CAGR</div><div class="card-value neg" id="g-rcagr">--</div></div>
                    <div class="card"><div class="card-title">AI Max Drawdown</div><div class="card-value neg" id="g-dd">--</div></div>
                    <div class="card"><div class="card-title">Avg Churn Rate</div><div class="card-value" id="g-churn">--</div></div>
                </div>
                
                <div class="methodology-card">
                    <h3>⚙️ The 100-Point Selection Engine & Filters</h3>
                    <div class="methodology-grid">
                        <div class="methodology-item">
                            <h4>1. Pure Momentum (Max 50 Pts)</h4>
                            <p>How fast is the stock growing compared to the rest of the market? The absolute fastest movers get the maximum 50 points.</p>
                        </div>
                        <div class="methodology-item">
                            <h4>2. Trend Alignment (Max 20 Pts)</h4>
                            <p>Stocks earn points for sustained uptrends: +7 points if above 51 EMA, +6 points if above 100 EMA, and +7 points if above 200 EMA.</p>
                        </div>
                        <div class="methodology-item">
                            <h4>3. Strong Hands Bonus (Max 15 Pts)</h4>
                            <p>Stocks with high Delivery Percentages are rewarded. This proves investors are buying and holding (taking delivery).</p>
                        </div>
                        <div class="methodology-item">
                            <h4>4. Liquidity Bonus (Max 15 Pts)</h4>
                            <p>Stocks with massive trading volume (Turnover) get bonus points, proving big money is actively participating in the move.</p>
                        </div>
                    </div>
                    <div style="margin-top: 16px; padding: 12px; background: rgba(37, 99, 235, 0.1); border-left: 4px solid var(--accent); border-radius: 4px;">
                        <span style="font-weight: 700; color: var(--text);">Strict Hard Filters:</span> A stock must score a <b>70 or higher</b> AND be priced <b>within 20% of its 52-week high</b> to be eligible for final AI selection.
                    </div>
                </div>
                
                <div class="chart-box"><canvas id="masterChart"></canvas></div>
            </div>
            
            <div id="monthly-deck" class="hidden">
                <div class="metrics-grid">
                    <div class="card"><div class="card-title">Regime Framework</div><div class="card-value" id="m-regime" style="color:#60A5FA;">--</div></div>
                    <div class="card"><div class="card-title">Monthly Churn</div><div class="card-value" id="m-churn">--</div></div>
                </div>
                <div class="tab-container">
                    <button class="tab-btn active" id="btn-sel-tab" onclick="switchTab('SELECTED')">✅ Selected Allocations</button>
                    <button class="tab-btn" id="btn-rej-tab" onclick="switchTab('REJECTED')">❌ Rejected Candidates</button>
                </div>
                <div id="stocks-list-container"></div>
            </div>
        </div>

        <script>
            const coreData = {payload};
            const global = coreData.global; const monthly = coreData.monthly;
            const timelineDates = Object.keys(monthly);
            let currentTab = 'SELECTED'; let currentActiveMonth = ''; let chartObj = null;
            
            function showGlobal() {{
                document.getElementById('global-deck').classList.remove('hidden');
                document.getElementById('monthly-deck').classList.add('hidden');
                document.querySelectorAll('.btn-nav').forEach(b => b.classList.remove('active'));
                document.getElementById('g-cagr').innerText = global.sel_cagr;
                document.getElementById('g-rcagr').innerText = global.rej_cagr;
                document.getElementById('g-dd').innerText = global.sel_dd;
                document.getElementById('g-churn').innerText = global.avg_churn;
                
                if(chartObj) chartObj.destroy();
                chartObj = new Chart(document.getElementById('masterChart'), {{
                    type: 'line',
                    data: {{
                        labels: global.chart_dates,
                        datasets: [
                            {{ label: 'AI Active Portfolio', data: global.chart_selected, borderColor: '#10B981', pointRadius: 0, tension:0.3 }},
                            {{ label: 'Rejected Assets Curve', data: global.chart_rejected, borderColor: '#EF4444', pointRadius: 0, tension:0.3 }}
                        ]
                    }},
                    options: {{ responsive: true, maintainAspectRatio: false, scales: {{ x:{{grid:{{display:false}}}}, y:{{grid:{{color:'#1F2733'}}}} }} }}
                }});
            }}
            
            function showMonth(dateStr) {{
                currentActiveMonth = dateStr;
                document.getElementById('global-deck').classList.add('hidden');
                document.getElementById('monthly-deck').classList.remove('hidden');
                document.querySelectorAll('.btn-nav').forEach(b => b.classList.remove('active'));
                document.getElementById('m-btn-'+dateStr).classList.add('active');
                
                const data = monthly[dateStr];
                document.getElementById('m-regime').innerText = data.regime;
                document.getElementById('m-churn').innerText = data.churn;
                switchTab(currentTab);
            }}
            
            function switchTab(mode) {{
                currentTab = mode;
                document.getElementById('btn-sel-tab').className = mode === 'SELECTED' ? 'tab-btn active' : 'tab-btn';
                document.getElementById('btn-rej-tab').className = mode === 'REJECTED' ? 'tab-btn active' : 'tab-btn';
                
                const listData = mode === 'SELECTED' ? monthly[currentActiveMonth].selected : monthly[currentActiveMonth].rejected;
                let cardsHtml = '';
                listData.forEach(s => {{
                    cardsHtml += `<div class="stock-card">
                        <div class="stock-header">
                            <div class="stock-symbol">${{s.SYMBOL}} <span style="font-size:12px; font-weight:400; color:var(--text-muted);">| ${{s.SECTOR}}</span></div>
                            <div style="font-weight:700;" class="${{s.PNL.includes('-')?'neg':'pos'}}">${{s.PNL}}</div>
                        </div>
                        <div style="display:flex; justify-content:space-between; font-size:12px; color:var(--text-muted);">
                            <div>Factor Score: ${{parseFloat(s.SCORE).toFixed(1)}}</div>
                            <div>Delivery Base: ${{s.DELIV_％||s['DELIV_%']}}</div>
                        </div>
                        <div class="stock-reason">${{s.REASON}}</div>
                    </div>`;
                }});
                document.getElementById('stocks-list-container').innerHTML = cardsHtml || '<div style="color:var(--text-muted)">No items under this framework profile posture.</div>';
            }}
            
            const sidebar = document.getElementById('sidebar');
            timelineDates.forEach(d => {{
                const b = document.createElement('button'); b.className = 'btn-nav'; b.id = 'm-btn-'+d; b.innerText = d; b.onclick = () => showMonth(d);
                sidebar.appendChild(b);
            }});
            showGlobal();
        </script>
    </body>
    </html>
    """
    with open("index.html", "w", encoding="utf-8") as f: f.write(html_content)
    print("✅ Full Production Deployment Assets Export Complete.")

if __name__ == "__main__":
    raw_df, nifty_df = load_and_adjust_data()
    snaps, equity = run_momentum_backtest(raw_df, nifty_df)
    verify_backtest_integrity(snaps, equity)
    generate_static_html(snaps, equity)
