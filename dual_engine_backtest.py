"""
dual_engine_backtest.py - PMS COMMAND CENTER (ULTRA UI EDITION)
==========================================================
Module 1: Data Engine (Local NSE BhavCopy & Index)
Module 2: Strategy Engine (RSI > 50, Relative Alpha, 0.5% Tax)
Module 3: Internal Python Verifier
Module 4: AI Heavy Lifter (Verifies & Justifies)
Module 5: HTML Publisher (Modern Fintech UI/UX Dashboard)
"""

import os
import glob
import json
import pandas as pd
import numpy as np
import concurrent.futures
from google import genai

# ==========================================
# MODULE 1: LOCAL DATA ENGINE
# ==========================================
def load_and_adjust_data(folder_path="./HistoricalBhavCopy/NSE", sector_map_path="./nifty500_sectors.csv", index_path="./nifty500_index.csv"):
    print("Loading Local BhavCopy & Applying Corporate Action Adjustments...")
    
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
        raise ValueError("No CSV files found in HistoricalBhavCopy/NSE. Check folder path.")
        
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
    final_df = pd.merge(master_df, sector_map, on='SYMBOL', how='left').reset_index(drop=True)
    final_df['SECTOR'] = final_df['SECTOR'].fillna('Unknown')
    
    return final_df, nifty_df[['DATE', 'CLOSE_PRICE', 'NIFTY_EMA_200']]

# ==========================================
# MODULE 2: STRATEGY ENGINE
# ==========================================
def run_momentum_backtest(df, nifty_df, ema_param=100, deliv_param=30.0, turnover_param=1000.0, risk_on=20, risk_off=10, friction_tax=0.005):
    print("Running Risk-Adjusted Strategy Engine with RSI & Alpha Filters...")
    
    df['P_1M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['P_7M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(147) 
    df['P_13M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    df['P_13M'] = df['P_13M'].replace(0, np.nan)
    df['P_7M'] = df['P_7M'].replace(0, np.nan)
    df['PRICE_MOMENTUM'] = (((df['P_1M'] - df['P_13M']) / df['P_13M']) * 2) + ((df['P_1M'] - df['P_7M']) / df['P_7M'])
    
    df['DAILY_RET'] = df.groupby('SYMBOL')['CLOSE_PRICE'].pct_change()
    df['VOLATILITY_90D'] = df.groupby('SYMBOL')['DAILY_RET'].transform(lambda x: x.rolling(90, min_periods=20).std() * np.sqrt(252))
    df['VOLATILITY_90D'] = df['VOLATILITY_90D'].replace(0, 0.001).fillna(0.001) 
    
    df['EMA_X'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=ema_param, adjust=False).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    df['AVG_DELIV_PER'] = df.groupby('SYMBOL')['DELIV_PER'].transform(lambda x: x.rolling(20).mean())
    
    # RSI
    delta = df.groupby('SYMBOL')['CLOSE_PRICE'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.groupby(df['SYMBOL']).transform(lambda x: x.ewm(alpha=1/14, adjust=False).mean())
    avg_loss = loss.groupby(df['SYMBOL']).transform(lambda x: x.ewm(alpha=1/14, adjust=False).mean())
    rs = avg_gain / avg_loss
    df['RSI_14'] = 100 - (100 / (1 + rs))

    # Nifty Alpha
    if not nifty_df.empty:
        nifty_ret_map = nifty_df.set_index('DATE')['CLOSE_PRICE'].pct_change(21)
        df['NIFTY_1M_RET'] = df['DATE'].map(nifty_ret_map).fillna(0)
    else:
        df['NIFTY_1M_RET'] = 0.0
        
    df['STOCK_1M_RET'] = df.groupby('SYMBOL')['CLOSE_PRICE'].pct_change(21).fillna(0)
    df['MASTER_SCORE'] = (df['PRICE_MOMENTUM'] / df['VOLATILITY_90D']) * 100
    
    df['YEAR_MONTH'] = df['DATE'].dt.to_period('M')
    month_ends = df.groupby('YEAR_MONTH')['DATE'].max().reset_index()
    rebalance_df = df[df['DATE'].isin(month_ends['DATE'])].copy()
    
    valid_pool = rebalance_df[
        (rebalance_df['CLOSE_PRICE'] >= rebalance_df['EMA_X']) & 
        (rebalance_df['CLOSE_PRICE'] >= (rebalance_df['52W_HIGH'] * 0.80)) & 
        (rebalance_df['CLOSE_PRICE'] >= 20.0) &  
        (rebalance_df['AVG_TURNOVER'] >= turnover_param) & 
        (rebalance_df['AVG_DELIV_PER'] >= deliv_param) & 
        (rebalance_df['RSI_14'] > 50.0) & 
        (rebalance_df['STOCK_1M_RET'] > rebalance_df['NIFTY_1M_RET']) & 
        (rebalance_df['MASTER_SCORE'].notna())
    ].copy()

    dates = sorted(rebalance_df['DATE'].dropna().unique())
    portfolio_snapshots = []
    equity_curve = []
    prev_portfolio_df = pd.DataFrame()
    entry_prices = {}
    entry_dates = {}
    capital = 1000000.0 
    
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
        
        if not nifty_df.empty:
            bench_past = nifty_df[nifty_df['DATE'] <= current_date]
            is_risk_on = bool(bench_past.iloc[-1]['CLOSE_PRICE'] > bench_past.iloc[-1]['NIFTY_EMA_200']) if not bench_past.empty else True
        else:
            is_risk_on = True

        target_limit = risk_on if is_risk_on else risk_off
        
        if candidates.empty:
            for sym in prev_symbols:
                portfolio_snapshots.append({
                    'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL']==sym]['SECTOR'].iloc[0],
                    'ACTION': 'EXIT', 'PRICE': day_prices.get(sym, 0), 'SCORE': day_scores.get(sym, 0),
                    'ENTRY_DATE': entry_dates.get(sym, 'N/A'), 'PNL': f"{((day_prices.get(sym, 0)/entry_prices.get(sym, 1))-1)*100:+.2f}%", 
                    'DELIV_%': f"{day_deliv.get(sym, 0):.1f}%" if pd.notna(day_deliv.get(sym, 0)) else "N/A",
                })
            entry_prices.clear(); entry_dates.clear()
            prev_portfolio_df = pd.DataFrame()
            equity_curve.append({'DATE': curr_date_str, 'EQUITY': capital, 'CHURN': 1.0, 'REGIME': 'Risk-OFF' if not is_risk_on else 'Risk-ON'})
            continue

        candidates = candidates.sort_values(by='MASTER_SCORE', ascending=False)
        top_extended = candidates.head(40).copy()
        
        final_portfolio = pd.concat([
            top_extended[top_extended['SYMBOL'].isin(prev_symbols)], 
            top_extended[~top_extended['SYMBOL'].isin(prev_symbols)]
        ]).head(target_limit).copy()
        
        current_symbols = set(final_portfolio['SYMBOL'])
        
        num_new_trades = len(current_symbols - prev_symbols)
        num_slots = len(final_portfolio)
        churn_ratio = (num_new_trades / num_slots) if num_slots > 0 else 0.0
        
        capital -= (capital * churn_ratio * friction_tax)
        equity_curve.append({'DATE': curr_date_str, 'EQUITY': capital, 'CHURN': churn_ratio, 'REGIME': 'Risk-ON' if is_risk_on else 'Risk-OFF'})
        
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
# MODULE 3: INTERNAL PYTHON VERIFIER
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
# MODULE 4: AI HEAVY LIFTER
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
    except Exception as e: pass

    return audit_progress

# ==========================================
# MODULE 5: HTML PUBLISHER (ULTRA UI/UX)
# ==========================================
def generate_static_html(audit_progress, df_snaps, df_equity):
    print("Generating Next-Gen Interactive HTML Dashboard...")
    
    if df_equity.empty or df_snaps.empty:
        return
        
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
            
            /* Sidebar */
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
            
            /* Main Content & Animations */
            .main-content {{ flex-grow: 1; padding: 40px; overflow-y: auto; box-sizing: border-box; position: relative; }}
            .fade-in {{ animation: fadeIn 0.4s cubic-bezier(0.16, 1, 0.3, 1); }}
            @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
            
            /* Header & Navigation */
            .header-row {{ display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 1px solid var(--border-color); padding-bottom: 20px; margin-bottom: 32px; }}
            h1 {{ margin: 0; font-size: 32px; font-weight: 800; letter-spacing: -1px; }}
            .nav-controls {{ display: flex; gap: 8px; }}
            .nav-btn {{ background: var(--bg-surface); color: var(--text-primary); border: 1px solid var(--border-color); padding: 10px 18px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 13px; transition: all 0.2s; display: none; align-items: center; gap: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .nav-btn.show {{ display: flex; }}
            .nav-btn:hover:not(:disabled) {{ background: var(--bg-surface-hover); border-color: #374151; }}
            .nav-btn:disabled {{ opacity: 0.4; cursor: not-allowed; box-shadow: none; }}
            
            /* Metrics Grid */
            .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-bottom: 32px; }}
            .metric-card {{ background: var(--bg-surface); padding: 24px; border-radius: 16px; border: 1px solid var(--border-color); position: relative; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.2); transition: transform 0.2s; }}
            .metric-card:hover {{ transform: translateY(-2px); }}
            .metric-title {{ font-size: 12px; color: var(--text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.5px; margin-bottom: 8px; }}
            .metric-value {{ font-size: 32px; font-weight: 800; letter-spacing: -0.5px; }}
            
            /* Chart */
            .chart-container {{ background: var(--bg-surface); padding: 24px; border-radius: 16px; border: 1px solid var(--border-color); margin-bottom: 40px; height: 420px; box-shadow: 0 4px 6px rgba(0,0,0,0.2); }}
            
            /* AI Box */
            .audit-box {{ background: linear-gradient(145deg, rgba(139, 92, 246, 0.1) 0%, rgba(139, 92, 246, 0.02) 100%); padding: 24px; border-radius: 16px; margin-bottom: 32px; border: 1px solid rgba(139, 92, 246, 0.2); position: relative; }}
            .audit-box::before {{ content: '🤖'; position: absolute; top: 24px; right: 24px; font-size: 24px; opacity: 0.5; }}
            .audit-box .metric-title {{ color: #A78BFA; }}
            pre {{ white-space: pre-wrap; font-size: 14px; color: var(--text-primary); margin: 0; line-height: 1.7; font-family: 'Inter', sans-serif; }}
            
            /* Table */
            .table-container {{ background: var(--bg-surface); border-radius: 16px; border: 1px solid var(--border-color); overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.2); }}
            table {{ width: 100%; border-collapse: collapse; text-align: left; }}
            th, td {{ padding: 16px 24px; border-bottom: 1px solid var(--border-color); font-size: 14px; }}
            th {{ background: rgba(0,0,0,0.2); color: var(--text-secondary); text-transform: uppercase; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; white-space: nowrap; }}
            tr:hover td {{ background: rgba(255,255,255,0.02); }}
            tr:last-child td {{ border-bottom: none; }}
            
            /* Utilities */
            .pos {{ color: var(--accent-success); }}
            .neg {{ color: var(--accent-danger); }}
            .hidden {{ display: none !important; }}
            
            /* Badges */
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
                void container.offsetWidth; // trigger reflow
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
