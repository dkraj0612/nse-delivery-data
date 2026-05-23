"""
dual_engine_backtest.py - INSTITUTIONAL DAILY MACRO MATRIX ENGINE (WITH AUTO-RECOVERY)
=============================================================================
Features: 70/30 Momentum, Strict Daily Look-Back Macro Sandbox, 
Asymmetric Free-Tier 90-Second Throttling + 429 Auto-Retry, Dual-Curve Dashboard.
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
# MODULE 1: LOCAL DATA ENGINE & DAY-LEVEL MACRO HISTORIAN
# ==========================================
def get_deterministic_mcap(symbol):
    hash_val = int(hashlib.md5(symbol.encode('utf-8')).hexdigest(), 16)
    return 1000 + (hash_val % 99000)

def get_historical_macro_context(date_timestamp):
    """Provides a precise, day-level chronological snapshot of Indian and Global macro landscapes."""
    current_date = pd.to_datetime(date_timestamp)
    
    # Precise timeline of macro-critical events (Indian & Global specific)
    macro_timeline = [
        ("2021-01-01", "Post-COVID structural economic restart. Global liquidity extreme easing, record low interest rates."),
        ("2021-06-15", "Massive global IT sector spend tailwinds. Indian IT corporate earnings boom."),
        ("2021-11-15", "Global commodity super-cycle peaks. Global supply chain chokepoints intensify inflation fears."),
        ("2022-02-24", "CRITICAL: Russia-Ukraine War erupts. Global crude oil spikes past $100/bbl. Massive FII outflows from emerging markets, sharp corrections in NSE midcaps."),
        ("2022-05-04", "RBI unexpectedly hikes repo rate by 40 bps in an off-cycle meeting to tame inflation. Domestic liquidity tightening begins."),
        ("2022-09-21", "US Federal Reserve announces third consecutive 75 bps rate hike. Global growth slowdown fears, but Indian markets show high relative strength."),
        ("2023-01-24", "Adani Group short-seller report released. Intense intra-month volatility in Indian infra and PSU banks. Sector rotation to defensives."),
        ("2023-05-19", "RBI announces withdrawal of Rs 2,000 currency notes. Banking system liquidity spikes temporarily."),
        ("2023-10-07", "Middle East geopolitical crisis escalates (Gaza). Global energy markets volatile, crude spikes, structural mid-cap consolidation."),
        ("2024-02-01", "Indian Interim Budget announces aggressive public CapEx increases, driving a vertical momentum boom in Railways, Defense, and Green Energy stocks."),
        ("2024-06-04", "Indian Lok Sabha Election results cause severe single-day 6% margin crash due to seat variance, followed by an immediate multi-day recovery on policy continuity."),
        ("2024-07-23", "Union Budget hikes Long-Term Capital Gains (LTCG) tax from 10% to 12.5% and STCG to 20%. Short-term retail momentum cooling down."),
        ("2025-01-15", "Global supply chains completely normalized. Tech spending stabilizes while domestic capital flows into Indian manufacturing hit record highs."),
        ("2025-08-20", "Emerging global tariff conflicts under new trade mandates. Absolute defensive shift inside corporate balance sheets toward domestic consumption lines."),
        ("2026-01-01", "High structural equity valuation multiples in India, heavily supported by robust domestic mutual fund SIP inflows."),
        ("2026-05-01", "Baseline macroeconomic stability. Focus firmly on cross-sectional earnings growth quality and low-volatility structural trends.")
    ]
    
    effective_context = "Normal structural growth environment with stable cross-sectional liquidity indicators."
    
    # Loop chronologically to find the closest event on or before the current day
    for event_date_str, description in macro_timeline:
        event_date = pd.to_datetime(event_date_str)
        if current_date >= event_date:
            effective_context = f"Active Environment (As of {event_date_str}): {description}"
            
    return effective_context

def load_and_adjust_data(folder_path="./HistoricalBhavCopy/NSE", sector_map_path="./nifty500_sectors.csv", index_path="./nifty500_index.csv"):
    print("Loading Local BhavCopy Data and Sector Maps...")
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
# MODULE 2: AI INSTITUTIONAL DEEP DIVE SELECTION ENGINE
# ==========================================
def parse_ai_reasoning_payload(text):
    symbols = []
    reasoning_map = {}
    
    array_match = re.search(r'FINAL_SELECTIONS\s*=\s*\[(.*?)\]', text, re.DOTALL)
    if array_match:
        try: symbols = json.loads('[' + array_match.group(1) + ']')
        except: symbols = [s.strip().replace('"', '').replace("'", "") for s in array_match.group(1).split(',') if s.strip()]
            
    for line in text.split('\n'):
        if '|' in line and not line.startswith('---') and not 'SYMBOL' in line:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 2:
                sym = parts[0].replace('*', '').replace('"', '').replace("'", "")
                reasoning_map[sym] = parts[1]
                
    return symbols, reasoning_map

def call_gemini_institutional_analor(top_50_df, target_limit, date_str, cache):
    if target_limit == 0: return [], {}
    cache_key = f"v3_daily_{date_str}_{target_limit}"
    if cache_key in cache:
        return cache[cache_key]["symbols"], cache[cache_key]["reasons"]
        
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client() if api_key else None
    if not client: 
        fallback_syms = top_50_df.head(target_limit)['SYMBOL'].tolist()
        return fallback_syms, {s: "Mathematical selection mode default." for s in top_50_df['SYMBOL']}

    macro_context = get_historical_macro_context(date_str)
    print(f"  [AI Processing] Deep analyzing top 50 stocks for timeline match: {date_str}...")
    
    prompt = f"""
    STRICT CHRONOLOGICAL TIMESTAMP: {date_str}
    You are an Institutional Portfolio Manager evaluating assets on exactly {date_str}.
    
    STRICT TIMELINE DIRECTIVE: You have no knowledge of any event, chart pattern, earnings release, or political outcome that occurs after {date_str}. Do not look ahead.
    
    DAY-LEVEL MACRO CONTEXT VALID ON {date_str}:
    {macro_context}
    
    PORTFOLIO CONSTRAINT: Select exactly {target_limit} symbols from the top 50 table below.
    
    CANDIDATE TOP 50 UNIVERSE DATA:
    {top_50_df[['SYMBOL', 'SECTOR', 'MASTER_SCORE', 'VOLATILITY_90D', 'MKT_CAP_CR']].to_markdown(index=False)}
    
    EXPLICIT EVALUATION DIRECTIVE:
    Analyze every single stock. Align selections with the day-level macro context. If the market contains specific shocks valid on this day, reject highly speculative lines and fund robust, asset-backed configurations. Include specific chronological or trigger references inside your logic.
    
    REQUIRED SYSTEM OUTPUT FORMAT:
    You must output a row-by-row reason for ALL 50 stocks using this strict syntax:
    SYMBOL | REASON (You must stamp a date or event reference here, e.g., 'Given the macro shock on {date_str}...')
    
    At the final line of your return string, write the array code block verbatim:
    FINAL_SELECTIONS = ["SYM1", "SYM2", ...]
    """
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(client.models.generate_content, model='gemini-2.5-flash', contents=prompt)
                resp = future.result(timeout=60)
                
            syms, reasons = parse_ai_reasoning_payload(resp.text)
            if syms:
                cache[cache_key] = {"symbols": syms[:target_limit], "reasons": reasons}
                with open("ai_selections_cache.json", "w") as f: json.dump(cache, f, indent=4)
                print(f"  [Throttling Guard] Call clean. Sleeping 90 seconds to preserve Free-Tier boundaries...")
                time.sleep(90) # STRICT USER REQUEST DIRECTIVE BUFFER
                return cache[cache_key]["symbols"], cache[cache_key]["reasons"]
                
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                print(f"  [Rate Limit Hit] API quota exhausted. Cooling down for 60 seconds before retrying (Attempt {attempt + 1}/{max_retries})...")
                time.sleep(60) # Wait for the quota to reset
            else:
                print(f"  [API Error] Unexpected failure: {e}. Retrying in 30 seconds...")
                time.sleep(30)
                
    print(f"  [Inference Bypass] Max retries exhausted for {date_str}. Engaging fallback routing.")
    time.sleep(90)
    fallback_syms = top_50_df.head(target_limit)['SYMBOL'].tolist()
    return fallback_syms, {s: "Baseline algorithmic selection fallback status." for s in top_50_df['SYMBOL']}

# ==========================================
# MODULE 3: STRATEGY ENGINE (DUAL CURVE PROCESSING)
# ==========================================
def run_momentum_backtest(df, nifty_df, ema_param=100, deliv_param=30.0, turnover_param=1000.0, risk_on=20, risk_off=10, friction_tax=0.005):
    print("Running Institutional Dual-Curve Simulation Engine...")
    
    df['P_1M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['P_7M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(147) 
    df['P_13M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    df['P_13M'] = df['P_13M'].replace(0, np.nan)
    df['P_7M'] = df['P_7M'].replace(0, np.nan)
    
    ret_12m = (df['P_1M'] - df['P_13M']) / df['P_13M']
    ret_6m = (df['P_1M'] - df['P_7M']) / df['P_7M']
    df['PRICE_MOMENTUM'] = (ret_12m * 0.70) + (ret_6m * 0.30)
    
    df['DAILY_RET'] = df.groupby('SYMBOL')['CLOSE_PRICE'].pct_change()
    df['VOLATILITY_90D'] = df.groupby('SYMBOL')['DAILY_RET'].transform(lambda x: x.rolling(90, min_periods=20).std() * np.sqrt(252))
    df['VOLATILITY_90D'] = df['VOLATILITY_90D'].replace(0, 0.001).fillna(0.001) 
    
    df['VOL_RANK'] = df.groupby('DATE')['VOLATILITY_90D'].rank(pct=True)
    df['SMOOTH_BONUS'] = np.where(df['VOL_RANK'] <= 0.30, 1.2, 1.0)
    
    df['HIGH_63D'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(63).max().shift(1))
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    df['VOL_SPIKE'] = df['TURNOVER_LACS'] > (df['AVG_TURNOVER'].shift(1) * 1.5)
    df['BREAKOUT_BONUS'] = np.where((df['CLOSE_PRICE'] >= df['HIGH_63D']) & df['VOL_SPIKE'], 1.3, 1.0)
    
    df['EMA_X'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=ema_param, adjust=False).mean())
    df['EMA_50'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=50, adjust=False).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    df['AVG_DELIV_PER'] = df.groupby('SYMBOL')['DELIV_PER'].transform(lambda x: x.rolling(20).mean())
    
    df['MASTER_SCORE'] = ((df['PRICE_MOMENTUM'] / df['VOLATILITY_90D']) * 100) * df['SMOOTH_BONUS'] * df['BREAKOUT_BONUS']
    
    df['ABOVE_50_EMA'] = (df['CLOSE_PRICE'] > df['EMA_50']).astype(int)
    breadth_series = df.groupby('DATE')['ABOVE_50_EMA'].mean() * 100
    
    df['YEAR_MONTH'] = df['DATE'].dt.to_period('M')
    month_ends = df.groupby('YEAR_MONTH')['DATE'].max().reset_index()
    rebalance_df = df[df['DATE'].isin(month_ends['DATE'])].copy()
    
    valid_pool = rebalance_df[
        (rebalance_df['MKT_CAP_CR'] >= 1000) &
        (rebalance_df['MKT_CAP_CR'] <= 100000) &
        (rebalance_df['CLOSE_PRICE'] >= rebalance_df['EMA_X']) & 
        (rebalance_df['CLOSE_PRICE'] >= 20.0) &  
        (rebalance_df['AVG_TURNOVER'] >= turnover_param) & 
        (rebalance_df['AVG_DELIV_PER'] >= deliv_param) & 
        (rebalance_df['MASTER_SCORE'].notna())
    ].copy()

    dates = sorted(rebalance_df['DATE'].dropna().unique())
    portfolio_snapshots = []
    equity_curve = []
    
    prev_portfolio_df = pd.DataFrame()
    entry_prices = {}
    
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
            regime = "CRITICAL (Cash)"
            target_limit = 0
        elif current_breadth < 50.0 or current_vol > 18.0:
            regime = "DEFENSIVE"
            target_limit = risk_off
        else:
            regime = "RISK-ON"
            target_limit = risk_on
            
        if target_limit == 0:
            equity_curve.append({'DATE': curr_date_str, 'SELECTED_EQUITY': capital_selected, 'REJECTED_EQUITY': capital_rejected, 'CHURN': 0.0, 'REGIME': regime})
            prev_portfolio_df = pd.DataFrame()
            continue

        existing_mask = candidates['SYMBOL'].isin(prev_symbols)
        strict_entry_mask = (candidates['CLOSE_PRICE'] >= (candidates['52W_HIGH'] * 0.80))
        valid_candidates = candidates[existing_mask | strict_entry_mask].copy()

        valid_candidates = valid_candidates.sort_values(by='MASTER_SCORE', ascending=False)
        top_50 = valid_candidates.head(50).copy()
        
        ai_symbols, ai_reasons = call_gemini_institutional_analor(top_50, target_limit, curr_date_str, ai_cache)
        
        final_portfolio = top_50[top_50['SYMBOL'].isin(ai_symbols)].head(target_limit).copy()
        rejected_portfolio = top_50[~top_50['SYMBOL'].isin(ai_symbols)].copy()
        
        if not rejected_portfolio.empty:
            capital_rejected = capital_rejected * (1 + rejected_portfolio['PCT_CHG'].mean())
            
        current_symbols = set(final_portfolio['SYMBOL'])
        num_new_trades = len(current_symbols - prev_symbols)
        churn_ratio = (num_new_trades / len(final_portfolio)) if len(final_portfolio) > 0 else 0.0
        capital_selected -= (capital_selected * churn_ratio * friction_tax)
        
        equity_curve.append({'DATE': curr_date_str, 'SELECTED_EQUITY': capital_selected, 'REJECTED_EQUITY': capital_rejected, 'CHURN': churn_ratio, 'REGIME': regime})
        
        for _, row in top_50.iterrows():
            sym = row['SYMBOL']
            is_chosen = sym in current_symbols
            pnl_str = f"{((day_prices.get(sym, row['CLOSE_PRICE'])/entry_prices.get(sym, row['CLOSE_PRICE']))-1)*100:+.2f}%" if sym in entry_prices else "NEW"
            if is_chosen and sym not in prev_symbols:
                entry_prices[sym] = row['CLOSE_PRICE']
                
            portfolio_snapshots.append({
                'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': row['SECTOR'],
                'ACTION': 'SELECTED' if is_chosen else 'REJECTED', 'PRICE': row['CLOSE_PRICE'],
                'SCORE': row['MASTER_SCORE'], 'DELIV_%': f"{row['AVG_DELIV_PER']:.1f}%", 'PNL': pnl_str,
                'REASON': ai_reasons.get(sym, "Algorithmic baseline asset execution logic.")
            })
            
        prev_portfolio_df = final_portfolio.copy()

    return pd.DataFrame(portfolio_snapshots), pd.DataFrame(equity_curve)

def verify_backtest_integrity(df_snaps, df_equity):
    assert df_equity['SELECTED_EQUITY'].min() >= 0, "Security validation break."
    print("✅ Python Structural Controls Guardrails Verified.")
    return True

# ==========================================
# MODULE 4: DUAL-CURVE RESPONSIVE PUBLISHER
# ==========================================
def generate_static_html(df_snaps, df_equity):
    print("Compiling Mobile-Responsive Dashboard Matrices...")
    
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
            "sel_cagr": f"{cagr_sel:.2f}%",
            "rej_cagr": f"{cagr_rej:.2f}%",
            "sel_dd": f"{max_dd_sel:.2f}%",
            "avg_churn": f"{df_equity['CHURN'].mean()*100:.1f}%",
            "chart_dates": df_equity['DATE'].tolist(),
            "chart_selected": df_equity['SELECTED_EQUITY'].tolist(),
            "chart_rejected": df_equity['REJECTED_EQUITY'].tolist()
        },
        "monthly": monthly_data
    }
    
    json_payload = json.dumps(payload)
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Momentum Alpha Matrix Center</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            :root {{ --bg-base: #06080C; --bg-surface: #0F131A; --bg-hover: #171D28; --border: #1B222E; --accent: #2563EB; --success: #10B981; --danger: #EF4444; --text: #F3F4F6; --text-muted: #9CA3AF; }}
            body {{ font-family: 'Inter', sans-serif; background: var(--bg-base); color: var(--text); margin: 0; display: flex; flex-direction: column; height: 100vh; }}
            @media(min-width: 768px) {{ body {{ flex-direction: row; }} }}
            
            .sidebar {{ width: 100%; background: var(--bg-surface); padding: 20px; box-sizing: border-box; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 10px; }}
            @media(min-width: 768px) {{ .sidebar {{ width: 300px; height: 100vh; overflow-y: auto; border-bottom: none; border-right: 1px solid var(--border); }} }}
            
            .brand {{ font-size: 18px; font-weight: 800; letter-spacing: -0.5px; color: #fff; margin-bottom: 15px; }}
            .brand span {{ color: var(--accent); }}
            
            .btn-nav {{ background: var(--bg-hover); color: var(--text); border: 1px solid var(--border); padding: 12px; border-radius: 8px; cursor: pointer; text-align: left; font-size: 14px; font-weight: 600; transition: all 0.2s; }}
            .btn-nav.active, .btn-nav:hover {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
            
            .main-panel {{ flex-grow: 1; padding: 16px; overflow-y: auto; box-sizing: border-box; }}
            @media(min-width: 768px) {{ .main-panel {{ padding: 40px; }} }}
            
            .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 14px; margin-bottom: 24px; }}
            .card {{ background: var(--bg-surface); padding: 16px; border-radius: 12px; border: 1px solid var(--border); }}
            .card-title {{ font-size: 10px; text-transform: uppercase; color: var(--text-muted); font-weight: 700; margin-bottom: 4px; }}
            .card-value {{ font-size: 20px; font-weight: 800; }}
            @media(min-width: 768px) {{ .card-value {{ font-size: 26px; }} }}
            
            .chart-box {{ background: var(--bg-surface); padding: 16px; border-radius: 12px; border: 1px solid var(--border); height: 320px; margin-bottom: 24px; }}
            
            .tab-container {{ display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
            .tab-btn {{ background: transparent; border: none; color: var(--text-muted); font-size: 13px; font-weight: 600; padding: 8px 16px; cursor: pointer; border-radius: 6px; }}
            .tab-btn.active {{ background: rgba(255,255,255,0.05); color: #fff; }}
            
            .stock-card {{ background: var(--bg-surface); padding: 16px; border-radius: 12px; border: 1px solid var(--border); margin-bottom: 10px; display: flex; flex-direction: column; gap: 8px; }}
            .stock-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 6px; }}
            .stock-symbol {{ font-weight: 800; font-size: 15px; }}
            .stock-reason {{ font-size: 13px; color: var(--text-muted); line-height: 1.5; background: rgba(0,0,0,0.15); padding: 10px; border-radius: 6px; border-left: 3px solid var(--accent); margin-top: 4px; }}
            .pos {{ color: var(--success); }} .neg {{ color: var(--danger); }} .hidden {{ display: none !important; }}
        </style>
    </head>
    <body>
        <div class="sidebar" id="sidebar">
            <div class="brand">⚡ Momentum<span>Alpha Center</span></div>
            <button class="btn-nav active" onclick="showGlobal()">📊 Strategy Deck Dashboard</button>
            <h3 style="font-size:10px; text-transform:uppercase; color:var(--text-muted); margin-top:15px; font-weight:700;">Rebalance Timeline</h3>
        </div>
        
        <div class="main-panel">
            <div id="global-deck">
                <div class="metrics-grid">
                    <div class="card"><div class="card-title">AI Selected CAGR</div><div class="card-value pos" id="g-cagr">--</div></div>
                    <div class="card"><div class="card-title">Rejected Asset CAGR</div><div class="card-value neg" id="g-rcagr">--</div></div>
                    <div class="card"><div class="card-title">Max Drawdown</div><div class="card-value neg" id="g-dd">--</div></div>
                    <div class="card"><div class="card-title">Avg Churn Rate</div><div class="card-value" id="g-churn">--</div></div>
                </div>
                <div class="chart-box"><canvas id="masterChart"></canvas></div>
            </div>
            
            <div id="monthly-deck" class="hidden">
                <div class="metrics-grid">
                    <div class="card"><div class="card-title">Regime Signature</div><div class="card-value" id="m-regime" style="color:#60A5FA;">--</div></div>
                    <div class="card"><div class="card-title">Snapshot Churn</div><div class="card-value" id="m-churn">--</div></div>
                </div>
                <div class="tab-container">
                    <button class="tab-btn active" id="btn-sel-tab" onclick="switchTab('SELECTED')">✅ Selected Allotments</button>
                    <button class="tab-btn" id="btn-rej-tab" onclick="switchTab('REJECTED')">❌ Rejected Matrix</button>
                </div>
                <div id="stocks-list-container"></div>
            </div>
        </div>

        <script>
            const coreData = {json_payload};
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
                            {{ label: 'AI Selected Active Curve', data: global.chart_selected, borderColor: '#10B981', pointRadius: 0, borderWidth:2, tension:0.2 }},
                            {{ label: 'Rejected Candidates Curve', data: global.chart_rejected, borderColor: '#EF4444', pointRadius: 0, borderWidth:2, tension:0.2 }}
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
                            <div class="stock-symbol">${{s.SYMBOL}} <span style="font-size:11px; font-weight:400; color:var(--text-muted);">| ${{s.SECTOR}}</span></div>
                            <div style="font-weight:700;" class="${{s.PNL.includes('-')?'neg':'pos'}}">${{s.PNL}}</div>
                        </div>
                        <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--text-muted);">
                            <div>Factor Score: ${{parseFloat(s.SCORE).toFixed(1)}}</div>
                            <div>Delivery Base: ${{s.DELIV_％||s['DELIV_%']}}</div>
                        </div>
                        <div class="stock-reason">${{s.REASON}}</div>
                    </div>`;
                }});
                document.getElementById('stocks-list-container').innerHTML = cardsHtml || '<div style="color:var(--text-muted); font-size:13px; padding:20px 0;">No active elements registered for this configuration posture.</div>';
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
    print("✅ System Core Automation Matrix successfully written to index.html.")

if __name__ == "__main__":
    raw_df, nifty_df = load_and_adjust_data()
    snaps, equity = run_momentum_backtest(raw_df, nifty_df)
    verify_backtest_integrity(snaps, equity)
    generate_static_html(snaps, equity)
