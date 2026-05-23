"""
dual_engine_backtest.py - AI DEEP DIVE COMMAND CENTER (FREE TIER OPTIMIZED)
==========================================================
Module 1: Data Engine (BhavCopy + Deterministic Market Cap)
Module 2: Strategy Engine (70/30 Weight, Breakout Bonus, Smoothness Bonus)
Module 3: AI Selection Engine (API Throttled for Free Tier)
Module 4: Internal Python Verifier
Module 5: AI Post-Mortem Justifier
Module 6: HTML Publisher (Modern Fintech UI)
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
# MODULE 1: LOCAL DATA ENGINE
# ==========================================
def get_deterministic_mcap(symbol):
    """Generates a fixed pseudo-Market Cap (in Cr) between 1,000 and 100,000 based on the ticker name."""
    hash_val = int(hashlib.md5(symbol.encode('utf-8')).hexdigest(), 16)
    return 1000 + (hash_val % 99000)

def load_and_adjust_data(folder_path="./HistoricalBhavCopy/NSE", sector_map_path="./nifty500_sectors.csv", index_path="./nifty500_index.csv"):
    print("Loading Local BhavCopy & Calculating Deterministic Market Caps...")
    
    try:
        sector_map = pd.read_csv(sector_map_path)[['SYMBOL', 'SECTOR']]
    except Exception:
        sector_map = pd.DataFrame(columns=['SYMBOL', 'SECTOR'])

    if os.path.exists(index_path):
        nifty_df = pd.read_csv(index_path)
        nifty_df['DATE'] = pd.to_datetime(nifty_df['DATE'], errors='coerce')
        nifty_df['CLOSE_PRICE'] = pd.to_numeric(nifty_df['CLOSE_PRICE'], errors='coerce')
        nifty_df = nifty_df.dropna(subset=['DATE', 'CLOSE_PRICE']).sort_values('DATE')
        nifty_df['NIFTY_EMA_200'] = nifty_df['CLOSE_PRICE'].ewm(span=200, adjust=False).mean()
        nifty_df['NIFTY_DAILY_RET'] = nifty_df['CLOSE_PRICE'].pct_change()
        nifty_df['NIFTY_VOL_20D'] = nifty_df['NIFTY_DAILY_RET'].rolling(20).std() * np.sqrt(252) * 100
    else:
        nifty_df = pd.DataFrame(columns=['DATE', 'CLOSE_PRICE', 'NIFTY_EMA_200'])

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
        except Exception: continue
            
    if not df_list:
        raise ValueError("No CSV files found in HistoricalBhavCopy/NSE. Check case sensitivity.")
        
    master_df = pd.concat(df_list, ignore_index=True)
    master_df['DATE'] = pd.to_datetime(master_df['DATE'], errors='coerce')
    master_df['CLOSE_PRICE'] = pd.to_numeric(master_df['CLOSE_PRICE'], errors='coerce')
    master_df['TURNOVER_LACS'] = pd.to_numeric(master_df['TURNOVER_LACS'], errors='coerce')
    master_df['DELIV_PER'] = master_df['DELIV_PER'].astype(str).str.replace('%', '', regex=False)
    master_df['DELIV_PER'] = pd.to_numeric(master_df['DELIV_PER'], errors='coerce')
    master_df = master_df.dropna(subset=['DATE', 'CLOSE_PRICE'])
    master_df = master_df.drop_duplicates(subset=['SYMBOL', 'DATE']).sort_values(['SYMBOL', 'DATE'])
    
    # Corp Action Adjustments
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
    
    # Apply Deterministic Market Cap
    master_df['MKT_CAP_CR'] = master_df['SYMBOL'].apply(get_deterministic_mcap)
    
    final_df = pd.merge(master_df, sector_map, on='SYMBOL', how='left').reset_index(drop=True)
    final_df['SECTOR'] = final_df['SECTOR'].fillna('Unknown')
    
    return final_df, nifty_df

# ==========================================
# MODULE 2: AI SELECTION ENGINE (FREE TIER)
# ==========================================
def extract_json_array(text):
    try:
        match = re.search(r'\[(.*?)\]', text, re.DOTALL)
        if match:
            return json.loads('[' + match.group(1) + ']')
    except: pass
    return []

def call_gemini_selector(top_50_df, target_limit, date_str, cache):
    if target_limit == 0: return []
    if date_str in cache: 
        return cache[date_str] 
        
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client() if api_key else None
    if not client: return top_50_df.head(target_limit)['SYMBOL'].tolist()

    print(f"  [AI Trigger] Deep analyzing Top 50 stocks for {date_str}...")
    
    prompt = f"""
    DATE: {date_str}
    You are an elite Quantitative Portfolio Manager. I have pre-filtered the top 50 Indian stocks based on mathematical momentum, volatility, and volume breakouts.
    
    Your task: Select exactly {target_limit} symbols from this list to form the optimal portfolio. 
    Optimize for sector diversification, structural smoothness, and avoiding highly correlated crashes.
    
    CANDIDATE POOL:
    {top_50_df[['SYMBOL', 'SECTOR', 'MASTER_SCORE', 'VOLATILITY_90D', 'MKT_CAP_CR']].to_markdown(index=False)}
    
    Output ONLY a raw JSON array of the {target_limit} selected symbols. Do not use markdown blocks.
    Example: ["TCS", "INFY", "RELIANCE"]
    """
    
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(client.models.generate_content, model='gemini-2.5-flash', contents=prompt)
            resp = future.result(timeout=30) 
            
        selected = extract_json_array(resp.text)
        if len(selected) > 0:
            cache[date_str] = selected[:target_limit]
            with open("ai_selections_cache.json", "w") as f:
                json.dump(cache, f, indent=4)
            time.sleep(4) # THROTTLE: Protects against Free Tier 15 RPM ban
            return cache[date_str]
    except Exception as e:
        print(f"  [AI Timeout/Error] Defaulting to pure math for {date_str}: {e}")
        
    time.sleep(4) # THROTTLE: Protects against Free Tier 15 RPM ban even on fail
    return top_50_df.head(target_limit)['SYMBOL'].tolist()

# ==========================================
# MODULE 3: STRATEGY ENGINE (BONUS LOGIC)
# ==========================================
def run_momentum_backtest(df, nifty_df, ema_param=100, deliv_param=30.0, turnover_param=1000.0, risk_on=20, risk_off=10, friction_tax=0.005):
    print("Running Mathematical Engine (70/30 Weights, Volatility Bonus, Breakout Multipliers)...")
    
    # 1. New 70/30 Momentum Weighting
    df['P_1M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['P_7M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(147) 
    df['P_13M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    df['P_13M'] = df['P_13M'].replace(0, np.nan)
    df['P_7M'] = df['P_7M'].replace(0, np.nan)
    
    ret_12m = (df['P_1M'] - df['P_13M']) / df['P_13M']
    ret_6m = (df['P_1M'] - df['P_7M']) / df['P_7M']
    df['PRICE_MOMENTUM'] = (ret_12m * 0.70) + (ret_6m * 0.30)
    
    # 2. Volatility Base
    df['DAILY_RET'] = df.groupby('SYMBOL')['CLOSE_PRICE'].pct_change()
    df['VOLATILITY_90D'] = df.groupby('SYMBOL')['DAILY_RET'].transform(lambda x: x.rolling(90, min_periods=20).std() * np.sqrt(252))
    df['VOLATILITY_90D'] = df['VOLATILITY_90D'].replace(0, 0.001).fillna(0.001) 
    
    # 3. New Bonus: Smoothness (Cross-Sectional Top 30%)
    df['VOL_RANK'] = df.groupby('DATE')['VOLATILITY_90D'].rank(pct=True)
    df['SMOOTH_BONUS'] = np.where(df['VOL_RANK'] <= 0.30, 1.2, 1.0)
    
    # 4. New Bonus: 3-Month High Breakout with Volume
    df['HIGH_63D'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(63).max().shift(1))
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    df['VOL_SPIKE'] = df['TURNOVER_LACS'] > (df['AVG_TURNOVER'].shift(1) * 1.5)
    df['BREAKOUT_BONUS'] = np.where((df['CLOSE_PRICE'] >= df['HIGH_63D']) & df['VOL_SPIKE'], 1.3, 1.0)
    
    # Tech Guardrails
    df['EMA_X'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=ema_param, adjust=False).mean())
    df['EMA_50'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=50, adjust=False).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    df['AVG_DELIV_PER'] = df.groupby('SYMBOL')['DELIV_PER'].transform(lambda x: x.rolling(20).mean())
    
    # 5. Advanced Master Score
    df['MASTER_SCORE'] = ((df['PRICE_MOMENTUM'] / df['VOLATILITY_90D']) * 100) * df['SMOOTH_BONUS'] * df['BREAKOUT_BONUS']
    
    # Macro Breadth
    df['ABOVE_50_EMA'] = (df['CLOSE_PRICE'] > df['EMA_50']).astype(int)
    breadth_series = df.groupby('DATE')['ABOVE_50_EMA'].mean() * 100
    
    df['YEAR_MONTH'] = df['DATE'].dt.to_period('M')
    month_ends = df.groupby('YEAR_MONTH')['DATE'].max().reset_index()
    rebalance_df = df[df['DATE'].isin(month_ends['DATE'])].copy()
    
    # 6. Apply Market Cap Filter (1000 Cr to 100000 Cr)
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
    entry_dates = {}
    capital = 1000000.0 
    
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
        day_deliv = day_data.set_index('SYMBOL')['AVG_DELIV_PER'].to_dict()
        day_scores = day_data.set_index('SYMBOL')['MASTER_SCORE'].to_dict()
        
        if not prev_portfolio_df.empty:
            num_holdings = len(prev_portfolio_df)
            if num_holdings > 0:
                temp_capital = 0.0
                weight_per_stock = capital / num_holdings
                for _, row in prev_portfolio_df.iterrows():
                    sym = row['SYMBOL']
                    old_price = row['CLOSE_PRICE']
                    curr_price = day_prices.get(sym, old_price) 
                    temp_capital += weight_per_stock * (curr_price / old_price)
                capital = temp_capital
            
        candidates = valid_pool[valid_pool['DATE'] == current_date].copy()
        prev_symbols = set(prev_portfolio_df['SYMBOL']) if not prev_portfolio_df.empty else set()
        
        current_breadth = breadth_series.get(current_date, 50.0)
        
        if not nifty_df.empty:
            bench_past = nifty_df[nifty_df['DATE'] <= current_date]
            if not bench_past.empty:
                latest_nifty = bench_past.iloc[-1]
                is_nifty_uptrend = bool(latest_nifty['CLOSE_PRICE'] > latest_nifty['NIFTY_EMA_200'])
                current_vol = latest_nifty['NIFTY_VOL_20D'] if pd.notna(latest_nifty['NIFTY_VOL_20D']) else 15.0
            else:
                is_nifty_uptrend = True
                current_vol = 15.0
        else:
            is_nifty_uptrend = True
            current_vol = 15.0

        if current_breadth < 30.0 and not is_nifty_uptrend:
            regime = "CRITICAL (100% Cash)"
            target_limit = 0
        elif current_breadth < 50.0 or current_vol > 18.0:
            regime = "DEFENSIVE (Half Size)"
            target_limit = risk_off
        else:
            regime = "RISK-ON"
            target_limit = risk_on
            
        if target_limit == 0:
            for sym in prev_symbols:
                portfolio_snapshots.append({
                    'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL']==sym]['SECTOR'].iloc[0],
                    'ACTION': 'EXIT', 'PRICE': day_prices.get(sym, 0), 'SCORE': day_scores.get(sym, 0),
                    'ENTRY_DATE': entry_dates.get(sym, 'N/A'), 'PNL': f"{((day_prices.get(sym, 0)/entry_prices.get(sym, 1))-1)*100:+.2f}%", 
                    'DELIV_%': f"{day_deliv.get(sym, 0):.1f}%" if pd.notna(day_deliv.get(sym, 0)) else "N/A",
                })
            entry_prices.clear(); entry_dates.clear()
            prev_portfolio_df = pd.DataFrame()
            equity_curve.append({'DATE': curr_date_str, 'EQUITY': capital, 'CHURN': 1.0, 'REGIME': regime})
            continue

        existing_mask = candidates['SYMBOL'].isin(prev_symbols)
        strict_entry_mask = (candidates['CLOSE_PRICE'] >= (candidates['52W_HIGH'] * 0.80))
        valid_candidates = candidates[existing_mask | strict_entry_mask].copy()

        # RANK & AI DEEP DIVE LOGIC
        valid_candidates = valid_candidates.sort_values(by='MASTER_SCORE', ascending=False)
        top_50 = valid_candidates.head(50).copy()
        
        # 7. AI SELECTS TOP 20 FROM TOP 50
        ai_symbols = call_gemini_selector(top_50, target_limit, curr_date_str, ai_cache)
        
        # Filter final portfolio based on AI choice (or fallback math)
        if ai_symbols:
            final_portfolio = top_50[top_50['SYMBOL'].isin(ai_symbols)].head(target_limit).copy()
        else:
            final_portfolio = pd.concat([
                top_50[top_50['SYMBOL'].isin(prev_symbols)], 
                top_50[~top_50['SYMBOL'].isin(prev_symbols)]
            ]).head(target_limit).copy()
        
        current_symbols = set(final_portfolio['SYMBOL'])
        
        num_new_trades = len(current_symbols - prev_symbols)
        num_slots = len(final_portfolio)
        churn_ratio = (num_new_trades / num_slots) if num_slots > 0 else 0.0
        
        capital -= (capital * churn_ratio * friction_tax)
        equity_curve.append({'DATE': curr_date_str, 'EQUITY': capital, 'CHURN': churn_ratio, 'REGIME': regime})
        
        for sym in (prev_symbols - current_symbols):
            portfolio_snapshots.append({
                'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL']==sym]['SECTOR'].iloc[0],
                'ACTION': 'EXIT', 'PRICE': day_prices.get(sym, 0), 'SCORE': day_scores.get(sym, 0),
                'ENTRY_DATE': entry_dates.get(sym, 'N/A'), 'PNL': f"{((day_prices.get(sym, 0)/entry_prices.get(sym, 1))-1)*100:+.2f}%", 
                'DELIV_%': f"{day_deliv.get(sym, 0):.1f}%" if pd.notna(day_deliv.get(sym, 0)) else "N/A",
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
                'ACTION': 'HOLD' if sym in prev_symbols else 'ENTRY', 'PRICE': curr_price, 'SCORE': row['MASTER_SCORE'],
                'ENTRY_DATE': entry_dates[sym], 'PNL': pnl_str, 'DELIV_%': f"{row['AVG_DELIV_PER']:.1f}%",
            })
            
        prev_portfolio_df = final_portfolio.copy()

    df_snaps = pd.DataFrame(portfolio_snapshots)
    df_equity = pd.DataFrame(equity_curve)
    
    if not df_equity.empty:
        df_equity['MOM_RET'] = df_equity['EQUITY'].pct_change() * 100
        df_equity['MOM_RET'] = df_equity['MOM_RET'].fillna(0.0)
    
    return df_snaps, df_equity

# ==========================================
# MODULE 4: INTERNAL PYTHON VERIFIER
# ==========================================
def verify_backtest_integrity(df_snaps, df_equity):
    if df_equity.empty or df_snaps.empty: return True
    try:
        assert df_equity['EQUITY'].min() >= 0, "FATAL: Portfolio equity dropped below zero."
        assert df_equity['CHURN'].max() <= 1.0, "FATAL: Monthly churn exceeded 100%."
        assert df_equity['CHURN'].min() >= 0.0, "FATAL: Negative churn detected."
        max_positions = df_snaps.groupby('DATE')['SYMBOL'].count().max()
        assert max_positions <= 40, f"FATAL: Max position size breached."
        df_entries = df_snaps[df_snaps['ACTION'].isin(['ENTRY', 'HOLD'])].copy()
        df_entries['DATE'] = pd.to_datetime(df_entries['DATE'])
        df_entries['ENTRY_DATE'] = pd.to_datetime(df_entries['ENTRY_DATE'])
        assert (df_entries['ENTRY_DATE'] <= df_entries['DATE']).all(), "FATAL: Look-ahead bias."
        return True
    except AssertionError as e:
        raise SystemExit(1)

# ==========================================
# MODULE 5: AI POST-MORTEM JUSTIFIER
# ==========================================
def ai_portfolio_verifier(df_snaps, df_equity):
    progress_file = "audit_progress.json"
    if os.path.exists(progress_file):
        with open(progress_file, "r") as f:
            try: audit_progress = json.load(f)
            except: audit_progress = {"results": {}}
    else: audit_progress = {"results": {}}
        
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client() if api_key else None
    if not client or df_snaps.empty: return audit_progress

    latest_date = df_snaps['DATE'].max()
    if latest_date in audit_progress["results"]:
        return audit_progress 

    latest_transitions = df_snaps[df_snaps['DATE'] == latest_date].copy()
    latest_regime = df_equity.iloc[-1]['REGIME']
    audit_df = latest_transitions[['ACTION', 'SYMBOL', 'SCORE', 'PNL']]
    
    prompt = f"DATE: {latest_date} | MARKET REGIME: {latest_regime}\nQuant PM verification. Transitions:\n{audit_df.to_markdown(index=False)}\nProvide a concise PMS justification report explaining EXITS, ENTRIES, and HOLDS based on SCORE, PNL, and Regime."
    
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(client.models.generate_content, model='gemini-2.5-flash', contents=prompt)
            resp = future.result(timeout=45) 
        audit_progress["results"][latest_date] = resp.text
        with open(progress_file, "w") as f:
            json.dump(audit_progress, f, indent=4)
        time.sleep(4) # THROTTLE: Protects against Free Tier 15 RPM ban
    except Exception as e: 
        time.sleep(4)

    return audit_progress

# ==========================================
# MODULE 6: HTML PUBLISHER (ULTRA UI/UX)
# ==========================================
def generate_static_html(audit_progress, df_snaps, df_equity):
    print("Generating Next-Gen Interactive HTML Dashboard...")
    
    if df_equity.empty or df_snaps.empty: return
        
    initial_equity = 1000000.0
    final_equity = df_equity['EQUITY'].iloc[-1]
    total_return_pct = ((final_equity / initial_equity) - 1) * 100
    
    start_date = pd.to_datetime(df_equity['DATE'].iloc[0])
    end_date = pd.to_datetime(df_equity['DATE'].iloc[-1])
    years = (end_date - start_date).days / 365.25
    cagr_pct = total_return_pct if years < 1.0 else (((final_equity / initial_equity) ** (1 / years)) - 1) * 100
    
    df_equity['PEAK'] = df_equity['EQUITY'].cummax()
    df_equity['DRAWDOWN'] = (df_equity['EQUITY'] - df_equity['PEAK']) / df_equity['PEAK']
    max_dd_pct = df_equity['DRAWDOWN'].min() * 100
    
    win_rate = (df_equity['MOM_RET'] > 0).mean() * 100
    monthly_returns_decimal = df_equity['MOM_RET'] / 100
    ann_volatility_pct = monthly_returns_decimal.std() * np.sqrt(12) * 100
    
    risk_free_rate = 0.07 
    sharpe_ratio = ((cagr_pct / 100) - risk_free_rate) / (monthly_returns_decimal.std() * np.sqrt(12)) if monthly_returns_decimal.std() > 0 else 0.0
    avg_churn_pct = df_equity['CHURN'].mean() * 100
    
    chart_dates = df_equity['DATE'].tolist()
    chart_equity = df_equity['EQUITY'].tolist()
    
    active_positions = df_snaps.copy()
    dashboard_data = {}
    unique_dates = sorted(active_positions['DATE'].unique(), reverse=True)
    
    for date in unique_dates:
        day_df = active_positions[active_positions['DATE'] == date].copy()
        if day_df.empty: continue
        
        mom_ret_val = df_equity.loc[df_equity['DATE'] == date, 'MOM_RET'].values
        mom_val = mom_ret_val[0] if len(mom_ret_val) > 0 and pd.notna(mom_ret_val[0]) else 0.0
        
        churn_val = df_equity.loc[df_equity['DATE'] == date, 'CHURN'].values
        month_churn_val = (churn_val[0] * 100) if len(churn_val) > 0 and pd.notna(churn_val[0]) else 0.0
        
        regime_val = df_equity.loc[df_equity['DATE'] == date, 'REGIME'].values
        regime_str = regime_val[0] if len(regime_val) > 0 else "Risk-ON"
        
        stocks_list = day_df[['SYMBOL', 'SECTOR', 'ACTION', 'ENTRY_DATE', 'PRICE', 'SCORE', 'DELIV_%', 'PNL']].to_dict('records')
        ai_audit_text = audit_progress.get("results", {}).get(date, "Backtest math completed. (AI Audit skipped to preserve API limits).")
        
        dashboard_data[date] = {
            "portfolio_pnl": f"{mom_val:+.2f}%",
            "month_churn": f"{month_churn_val:.1f}%",
            "regime": regime_str,
            "ai_audit": ai_audit_text,
            "stocks": stocks_list
        }
    
    page_data = {
        "global": {
            "total_ret": f"{total_return_pct:.2f}%",
            "cagr": f"{cagr_pct:.2f}%",
            "max_dd": f"{max_dd_pct:.2f}%",
            "win_rate": f"{win_rate:.1f}%",
            "volatility": f"{ann_volatility_pct:.2f}%",
            "sharpe": f"{sharpe_ratio:.2f}",
            "avg_churn": f"{avg_churn_pct:.1f}%",
            "chart_dates": chart_dates,
            "chart_equity": chart_equity
        },
        "monthly": dashboard_data
    }
    
    json_payload = json.dumps(page_data)
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Momentum Alpha Command Center</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            :root {{ 
                --bg-base: #0B0E14; 
                --bg-surface: #151A22; 
                --bg-surface-hover: #1E2532;
                --border-color: #222A35;
                --accent-primary: #3B82F6; 
                --accent-primary-glow: rgba(59, 130, 246, 0.2);
                --accent-success: #10B981;
                --accent-danger: #EF4444;
                --text-primary: #F3F4F6; 
                --text-secondary: #9CA3AF; 
            }}
            body {{ font-family: 'Inter', sans-serif; background: var(--bg-base); color: var(--text-primary); margin: 0; display: flex; height: 100vh; overflow: hidden; }}
            
            .sidebar {{ width: 280px; background: var(--bg-surface); height: 100vh; overflow-y: auto; padding: 24px 20px; box-sizing: border-box; border-right: 1px solid var(--border-color); display: flex; flex-direction: column; gap: 8px; }}
            .sidebar::-webkit-scrollbar {{ width: 6px; }}
            .sidebar::-webkit-scrollbar-thumb {{ background: var(--border-color); border-radius: 4px; }}
            
            .brand {{ font-size: 16px; font-weight: 800; color: #fff; margin-bottom: 30px; display: flex; align-items: center; gap: 10px; letter-spacing: -0.5px; }}
            .brand span {{ color: var(--accent-primary); }}
            
            .sidebar h3 {{ color: var(--text-secondary); margin-top: 10px; margin-bottom: 15px; text-transform: uppercase; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
            
            .btn-overview {{ background: var(--accent-primary-glow); color: var(--accent-primary); border: 1px solid rgba(59, 130, 246, 0.4); padding: 14px 16px; cursor: pointer; text-align: left; border-radius: 10px; font-weight: 600; font-size: 14px; transition: all 0.2s; margin-bottom: 10px; }}
            .btn-overview:hover {{ background: var(--accent-primary); color: #fff; transform: translateY(-1px); box-shadow: 0 4px 12px var(--accent-primary-glow); }}
            
            .month-btn {{ display: block; width: 100%; background: transparent; color: var(--text-secondary); border: none; padding: 12px 16px; cursor: pointer; text-align: left; border-radius: 8px; font-size: 14px; font-weight: 500; transition: all 0.2s; border-left: 3px solid transparent; }}
            .month-btn:hover {{ background: var(--bg-surface-hover); color: var(--text-primary); }}
            .month-btn.active {{ background: rgba(255,255,255,0.05); color: #fff; border-left: 3px solid var(--accent-primary); font-weight: 600; }}
            
            .main-content {{ flex-grow: 1; padding: 40px; overflow-y: auto; box-sizing: border-box; position: relative; }}
            .fade-in {{ animation: fadeIn 0.4s cubic-bezier(0.16, 1, 0.3, 1); }}
            @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
            
            .header-row {{ display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 1px solid var(--border-color); padding-bottom: 20px; margin-bottom: 32px; }}
            h1 {{ margin: 0; font-size: 32px; font-weight: 800; letter-spacing: -1px; }}
            .nav-controls {{ display: flex; gap: 8px; }}
            .nav-btn {{ background: var(--bg-surface); color: var(--text-primary); border: 1px solid var(--border-color); padding: 10px 18px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 13px; transition: all 0.2s; display: none; align-items: center; gap: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .nav-btn.show {{ display: flex; }}
            .nav-btn:hover:not(:disabled) {{ background: var(--bg-surface-hover); border-color: #374151; }}
            .nav-btn:disabled {{ opacity: 0.4; cursor: not-allowed; box-shadow: none; }}
            
            .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-bottom: 32px; }}
            .metric-card {{ background: var(--bg-surface); padding: 24px; border-radius: 16px; border: 1px solid var(--border-color); position: relative; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.2); transition: transform 0.2s; }}
            .metric-card:hover {{ transform: translateY(-2px); }}
            .metric-title {{ font-size: 12px; color: var(--text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.5px; margin-bottom: 8px; }}
            .metric-value {{ font-size: 32px; font-weight: 800; letter-spacing: -0.5px; }}
            
            .chart-container {{ background: var(--bg-surface); padding: 24px; border-radius: 16px; border: 1px solid var(--border-color); margin-bottom: 40px; height: 420px; box-shadow: 0 4px 6px rgba(0,0,0,0.2); }}
            
            .audit-box {{ background: linear-gradient(145deg, rgba(139, 92, 246, 0.1) 0%, rgba(139, 92, 246, 0.02) 100%); padding: 24px; border-radius: 16px; margin-bottom: 32px; border: 1px solid rgba(139, 92, 246, 0.2); position: relative; }}
            .audit-box::before {{ content: '🤖'; position: absolute; top: 24px; right: 24px; font-size: 24px; opacity: 0.5; }}
            .audit-box .metric-title {{ color: #A78BFA; }}
            pre {{ white-space: pre-wrap; font-size: 14px; color: var(--text-primary); margin: 0; line-height: 1.7; font-family: 'Inter', sans-serif; }}
            
            .table-container {{ background: var(--bg-surface); border-radius: 16px; border: 1px solid var(--border-color); overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.2); }}
            table {{ width: 100%; border-collapse: collapse; text-align: left; }}
            th, td {{ padding: 16px 24px; border-bottom: 1px solid var(--border-color); font-size: 14px; }}
            th {{ background: rgba(0,0,0,0.2); color: var(--text-secondary); text-transform: uppercase; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; white-space: nowrap; }}
            tr:hover td {{ background: rgba(255,255,255,0.02); }}
            tr:last-child td {{ border-bottom: none; }}
            
            .pos {{ color: var(--accent-success); }}
            .neg {{ color: var(--accent-danger); }}
            .hidden {{ display: none !important; }}
            
            .badge {{ display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }}
            .badge-hold {{ background: rgba(255,255,255,0.05); color: var(--text-secondary); border: 1px solid rgba(255,255,255,0.1); }}
            .badge-entry {{ background: rgba(16, 185, 129, 0.15); color: var(--accent-success); border: 1px solid rgba(16, 185, 129, 0.3); }}
            .badge-exit {{ background: rgba(239, 68, 68, 0.15); color: var(--accent-danger); border: 1px solid rgba(239, 68, 68, 0.3); }}
        </style>
    </head>
    <body>
        <div class="sidebar" id="sidebar">
            <div class="brand">⚡ Momentum<span>Alpha</span></div>
            <button class="btn-overview" onclick="showOverview()">📊 Global Overview</button>
            <h3>Monthly Timeline</h3>
        </div>
        
        <div class="main-content" id="view-container">
            <div class="header-row">
                <h1 id="page-title">Performance Overview</h1>
                <div class="nav-controls" id="nav-controls">
                    <button class="nav-btn" id="prev-btn" onclick="goPrev()">← Prev</button>
                    <button class="nav-btn" id="next-btn" onclick="goNext()">Next →</button>
                </div>
            </div>
            
            <div id="overview-view">
                <div class="metrics-grid">
                    <div class="metric-card"><div class="metric-title">Total Return</div><div class="metric-value pos" id="g-total">--</div></div>
                    <div class="metric-card"><div class="metric-title">CAGR</div><div class="metric-value pos" id="g-cagr">--</div></div>
                    <div class="metric-card"><div class="metric-title">Max Drawdown</div><div class="metric-value neg" id="g-dd">--</div></div>
                    <div class="metric-card"><div class="metric-title">Sharpe Ratio</div><div class="metric-value" id="g-sharpe" style="color: #A78BFA;">--</div></div>
                    <div class="metric-card"><div class="metric-title">Avg Monthly Churn</div><div class="metric-value" id="g-churn">--</div></div>
                </div>
                <div class="chart-container"><canvas id="equityChart"></canvas></div>
            </div>

            <div id="monthly-view" class="hidden">
                <div class="metrics-grid">
                    <div class="metric-card"><div class="metric-title">Regime Posture</div><div class="metric-value" id="port-regime" style="color: #60A5FA;">--</div></div>
                    <div class="metric-card"><div class="metric-title">MoM Return</div><div class="metric-value" id="port-pnl">--</div></div>
                    <div class="metric-card"><div class="metric-title">Monthly Churn</div><div class="metric-value" id="port-churn">--</div></div>
                </div>
                
                <div class="audit-box">
                    <div class="metric-title">AI Portfolio Justification</div>
                    <pre id="ai-audit">Loading...</pre>
                </div>
                
                <h3 style="margin-bottom: 16px; font-size: 16px; font-weight: 600;">Transition Matrix</h3>
                <div class="table-container">
                    <table>
                        <thead><tr><th>Action</th><th>Symbol</th><th>Entry Date</th><th>Exit Date</th><th>Score</th><th>Deliv %</th><th>Cum. PNL</th></tr></thead>
                        <tbody id="table-body"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <script>
            const fullData = {json_payload};
            const global = fullData.global;
            const monthly = fullData.monthly;
            const dates = Object.keys(monthly); 
            let currentIndex = 0; let myChart = null;

            function triggerAnimation() {{
                const container = document.getElementById('view-container');
                container.classList.remove('fade-in');
                void container.offsetWidth; 
                container.classList.add('fade-in');
            }}

            function showOverview() {{
                triggerAnimation();
                document.getElementById('overview-view').classList.remove('hidden');
                document.getElementById('monthly-view').classList.add('hidden');
                document.getElementById('page-title').innerText = "Performance Overview";
                document.getElementById('prev-btn').classList.remove('show'); 
                document.getElementById('next-btn').classList.remove('show');
                document.querySelectorAll('.month-btn').forEach(btn => btn.classList.remove('active'));

                document.getElementById('g-total').innerText = global.total_ret;
                document.getElementById('g-cagr').innerText = global.cagr;
                document.getElementById('g-dd').innerText = global.max_dd;
                document.getElementById('g-sharpe').innerText = global.sharpe;
                document.getElementById('g-churn').innerText = global.avg_churn;

                if(myChart) myChart.destroy();
                const ctx = document.getElementById('equityChart').getContext('2d');
                
                let gradient = ctx.createLinearGradient(0, 0, 0, 400);
                gradient.addColorStop(0, 'rgba(59, 130, 246, 0.4)');
                gradient.addColorStop(1, 'rgba(59, 130, 246, 0.0)');

                myChart = new Chart(ctx, {{
                    type: 'line', 
                    data: {{ 
                        labels: global.chart_dates, 
                        datasets: [{{ 
                            label: 'Equity Growth', 
                            data: global.chart_equity, 
                            borderColor: '#3B82F6', 
                            backgroundColor: gradient,
                            borderWidth: 2, 
                            pointRadius: 0,
                            fill: true,
                            tension: 0.4
                        }}] 
                    }},
                    options: {{ 
                        responsive: true, 
                        maintainAspectRatio: false, 
                        plugins: {{ 
                            legend: {{ display: false }},
                            tooltip: {{
                                backgroundColor: 'rgba(15, 23, 42, 0.9)',
                                titleFont: {{ size: 13, family: 'Inter' }},
                                bodyFont: {{ size: 14, family: 'Inter', weight: 'bold' }},
                                padding: 12,
                                displayColors: false,
                                callbacks: {{
                                    label: function(context) {{
                                        return '₹ ' + context.parsed.y.toLocaleString('en-IN', {{maximumFractionDigits: 0}});
                                    }}
                                }}
                            }}
                        }}, 
                        interaction: {{ intersect: false, mode: 'index' }},
                        scales: {{ 
                            x: {{ grid: {{ display: false }}, ticks: {{ color: '#6B7280', font: {{family: 'Inter'}} }} }},
                            y: {{ grid: {{ color: '#1E293B', borderDash: [4, 4] }}, ticks: {{ color: '#6B7280', font: {{family: 'Inter'}} }} }}
                        }} 
                    }}
                }});
            }}
            
            function showMonth(date) {{
                triggerAnimation();
                currentIndex = dates.indexOf(date);
                document.getElementById('overview-view').classList.add('hidden');
                document.getElementById('monthly-view').classList.remove('hidden');
                document.getElementById('page-title').innerText = "Snapshot: " + date;
                document.getElementById('prev-btn').classList.add('show'); 
                document.getElementById('next-btn').classList.add('show');
                document.getElementById('prev-btn').disabled = (currentIndex >= dates.length - 1);
                document.getElementById('next-btn').disabled = (currentIndex <= 0);
                document.querySelectorAll('.month-btn').forEach(btn => btn.classList.remove('active'));
                const activeBtn = document.getElementById('btn-' + date); if(activeBtn) activeBtn.classList.add('active');

                const data = monthly[date];
                document.getElementById('port-pnl').innerText = data.portfolio_pnl;
                document.getElementById('port-pnl').className = "metric-value " + (data.portfolio_pnl.includes('-') ? 'neg' : 'pos');
                document.getElementById('port-regime').innerText = data.regime;
                document.getElementById('port-churn').innerText = data.month_churn;
                document.getElementById('ai-audit').innerText = data.ai_audit;

                let rowsHtml = '';
                data.stocks.forEach(stock => {{
                    let badgeClass = stock.ACTION === 'ENTRY' ? 'badge-entry' : (stock.ACTION === 'EXIT' ? 'badge-exit' : 'badge-hold');
                    let exitDate = stock.ACTION === 'EXIT' ? date : '-';
                    let pnlDisplay = stock.PNL;
                    let pnlClass = stock.PNL === 'NEW' ? '' : (stock.PNL.includes('-') ? 'neg' : 'pos');

                    rowsHtml += `<tr>
                        <td><span class="badge ${{badgeClass}}">${{stock.ACTION}}</span></td>
                        <td style="font-weight: 700; color: #fff;">${{stock.SYMBOL}}</td>
                        <td style="color: var(--text-secondary);">${{stock.ENTRY_DATE}}</td>
                        <td style="color: var(--text-secondary);">${{exitDate}}</td>
                        <td style="font-family: monospace; font-size: 13px;">${{parseFloat(stock.SCORE).toFixed(2)}}</td>
                        <td>${{stock['DELIV_%']}}</td>
                        <td class="${{pnlClass}}" style="font-weight: 700;">${{pnlDisplay}}</td>
                    </tr>`;
                }});
                document.getElementById('table-body').innerHTML = rowsHtml;
            }}

            function goPrev() {{ if (currentIndex < dates.length - 1) showMonth(dates[currentIndex + 1]); }}
            function goNext() {{ if (currentIndex > 0) showMonth(dates[currentIndex - 1]); }}

            const sidebar = document.getElementById('sidebar');
            dates.forEach(date => {{
                const btn = document.createElement('button'); btn.className = 'month-btn'; btn.id = 'btn-' + date; btn.innerText = date; btn.onclick = () => showMonth(date);
                sidebar.appendChild(btn);
            }});

            showOverview();
        </script>
    </body>
    </html>
    """
    df_snaps.to_csv("backtest_portfolio_history.csv", index=False)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    print("✅ Dashboard complete. Check index.html!")

# ==========================================
# EXECUTION ROUTER
# ==========================================
if __name__ == "__main__":
    print("🚀 Running PMS Command Center Pipeline.")
    raw_df, nifty_df = load_and_adjust_data()
    df_snaps, df_equity = run_momentum_backtest(raw_df, nifty_df)
    verify_backtest_integrity(df_snaps, df_equity)
    audit_state = ai_portfolio_verifier(df_snaps, df_equity)
    generate_static_html(audit_state, df_snaps, df_equity)
    print("🏁 Pipeline Successfully Terminated.")
