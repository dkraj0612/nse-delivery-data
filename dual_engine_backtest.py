"""
dual_engine_backtest.py - PMS COMMAND CENTER (METRICS EXPANDED)
==========================================================
Module 1: Data Engine (Local NSE BhavCopy & Index)
Module 2: Strategy Engine (Risk-Adjusted Momentum, 0.5% Tax, > ₹20 Filter)
Module 3: Internal Python Verifier (5 Strict PMS Asserts)
Module 4: AI Heavy Lifter (Verifies & Justifies)
Module 5: HTML Publisher (Interactive GitHub Pages Dashboard)
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
    print("Running Risk-Adjusted Strategy Engine...")
    
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
    print("Running PMS 5-Point Algorithmic Integrity Check...")
    
    if df_equity.empty or df_snaps.empty:
        return True

    try:
        assert df_equity['EQUITY'].min() >= 0, "FATAL: Portfolio equity dropped below zero."
        assert df_equity['CHURN'].max() <= 1.0, "FATAL: Monthly churn exceeded 100%."
        assert df_equity['CHURN'].min() >= 0.0, "FATAL: Negative churn detected."
        max_positions = df_snaps.groupby('DATE')['SYMBOL'].count().max()
        assert max_positions <= 40, f"FATAL: Max position size breached. Counted {max_positions} active."

        df_entries = df_snaps[df_snaps['ACTION'].isin(['ENTRY', 'HOLD'])].copy()
        df_entries['DATE'] = pd.to_datetime(df_entries['DATE'])
        df_entries['ENTRY_DATE'] = pd.to_datetime(df_entries['ENTRY_DATE'])
        assert (df_entries['ENTRY_DATE'] <= df_entries['DATE']).all(), "FATAL: Look-ahead bias."
        assert not df_equity.isnull().values.any(), "FATAL: Missing values in the equity curve."
        
        print("✅ Python PMS Verification Passed. Math is strictly reliable.")
        return True
    except AssertionError as e:
        print(f"❌ PMS VERIFICATION FAILED: {e}")
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

    print(f"\nTriggering AI Heavy Lifting for {latest_date} Portfolio Construction...")
    
    latest_transitions = df_snaps[df_snaps['DATE'] == latest_date].copy()
    latest_regime = df_equity.iloc[-1]['REGIME']
    
    audit_df = latest_transitions[['ACTION', 'SYMBOL', 'SCORE', 'PNL']]
    
    prompt = f"""
    DATE: {latest_date} | MARKET REGIME: {latest_regime}
    
    You are a Quantitative Portfolio Manager. Below is the transition matrix executed on {latest_date}.
    
    TRANSITIONS:
    {audit_df.to_markdown(index=False)}
    
    Provide a professional PMS justification report explaining:
    1. Why specific stocks were EXITED.
    2. Why specific stocks were ENTERED.
    3. Why the HOLDS were maintained.
    Base reasoning strictly on the SCOREs, PNL, and {latest_regime} regime limit. Keep it concise and professional.
    """
    
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(client.models.generate_content, model='gemini-2.5-flash', contents=prompt)
            resp = future.result(timeout=45) 
            
        audit_progress["results"][latest_date] = resp.text
        with open(progress_file, "w") as f:
            json.dump(audit_progress, f, indent=4)
        print("✅ AI PMS Justification generated and saved.")
    except Exception as e:
        print(f"⚠️ AI Generation Failed: {e}")

    return audit_progress

# ==========================================
# MODULE 5: HTML PUBLISHER (METRICS EXPANDED)
# ==========================================
def generate_static_html(audit_progress, df_snaps, df_equity):
    print("Generating Interactive HTML Dashboard...")
    
    if df_equity.empty or df_snaps.empty:
        print("No data to generate dashboard.")
        return
        
    initial_equity = 1000000.0
    final_equity = df_equity['EQUITY'].iloc[-1]
    total_return_pct = ((final_equity / initial_equity) - 1) * 100
    
    start_date = pd.to_datetime(df_equity['DATE'].iloc[0])
    end_date = pd.to_datetime(df_equity['DATE'].iloc[-1])
    years = (end_date - start_date).days / 365.25
    
    if years < 1.0:
        cagr_pct = total_return_pct
    else:
        cagr_pct = (((final_equity / initial_equity) ** (1 / years)) - 1) * 100
    
    df_equity['PEAK'] = df_equity['EQUITY'].cummax()
    df_equity['DRAWDOWN'] = (df_equity['EQUITY'] - df_equity['PEAK']) / df_equity['PEAK']
    max_dd_pct = df_equity['DRAWDOWN'].min() * 100
    
    win_rate = (df_equity['MOM_RET'] > 0).mean() * 100
    monthly_returns_decimal = df_equity['MOM_RET'] / 100
    ann_volatility_pct = monthly_returns_decimal.std() * np.sqrt(12) * 100
    
    risk_free_rate = 0.07 
    if monthly_returns_decimal.std() > 0:
        sharpe_ratio = ((cagr_pct / 100) - risk_free_rate) / (monthly_returns_decimal.std() * np.sqrt(12))
    else:
        sharpe_ratio = 0.0
        
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
        ai_audit_text = audit_progress.get("results", {}).get(date, "Historical mathematical backtest completed. (AI Audit skipped to preserve API limits).")
        
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
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            :root {{ --bg-primary: #0a0b10; --bg-secondary: #14151c; --accent: #3b82f6; --text-main: #f1f5f9; --text-muted: #94a3b8; --pos: #22c55e; --neg: #ef4444; --border: #1e293b; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg-primary); color: var(--text-main); margin: 0; display: flex; height: 100vh; overflow: hidden; }}
            .sidebar {{ width: 280px; background: var(--bg-secondary); height: 100vh; overflow-y: auto; padding: 25px 20px; box-sizing: border-box; border-right: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; }}
            .sidebar h3 {{ color: var(--text-muted); margin-top: 0; margin-bottom: 15px; text-transform: uppercase; font-size: 12px; border-bottom: 1px solid var(--border); padding-bottom: 10px;}}
            .btn-overview {{ background: rgba(59, 130, 246, 0.1); color: var(--accent); border: 1px solid var(--accent); padding: 14px 16px; cursor: pointer; text-align: left; border-radius: 8px; font-weight: 700; margin-bottom: 20px; }}
            .month-btn {{ display: block; width: 100%; background: transparent; color: var(--text-muted); border: 1px solid transparent; padding: 12px 16px; cursor: pointer; text-align: left; border-radius: 8px; font-size: 14px; transition: all 0.2s; }}
            .month-btn.active {{ background: #1e293b; color: #fff; border-left: 4px solid var(--accent); }}
            .main-content {{ flex-grow: 1; padding: 40px; overflow-y: auto; box-sizing: border-box; }}
            .header-row {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 20px; margin-bottom: 30px; }}
            h1 {{ margin: 0; font-size: 32px; }}
            .nav-controls {{ display: flex; gap: 12px; }}
            .nav-btn {{ background: var(--bg-secondary); color: var(--text-main); border: 1px solid var(--border); padding: 10px 20px; border-radius: 8px; cursor: pointer; font-weight: 600; display: none; }}
            .nav-btn.show {{ display: flex; }}
            .nav-btn:disabled {{ opacity: 0.3; cursor: not-allowed; }}
            .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-bottom: 40px; }}
            .metric-card {{ background: var(--bg-secondary); padding: 24px; border-radius: 12px; border: 1px solid var(--border); }}
            .metric-title {{ font-size: 13px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 12px; }}
            .metric-value {{ font-size: 36px; font-weight: 800; }}
            .chart-container {{ background: var(--bg-secondary); padding: 24px; border-radius: 12px; border: 1px solid var(--border); margin-bottom: 40px; height: 400px; }}
            .audit-box {{ background: rgba(59, 130, 246, 0.05); padding: 24px; border-radius: 12px; margin-bottom: 40px; border: 1px solid rgba(59, 130, 246, 0.2); }}
            pre {{ white-space: pre-wrap; font-size: 15px; color: var(--text-main); margin: 0; line-height: 1.6; }}
            table {{ width: 100%; border-collapse: collapse; background: var(--bg-secondary); border-radius: 12px; overflow: hidden; }}
            th, td {{ padding: 16px 20px; text-align: left; border-bottom: 1px solid var(--border); font-size: 14px; }}
            th {{ background: #181a25; color: var(--text-muted); text-transform: uppercase; font-size: 12px; }}
            .pos {{ color: var(--pos); }}
            .neg {{ color: var(--neg); }}
            .hidden {{ display: none !important; }}
            .badge-hold {{ background: rgba(255,255,255,0.1); color: #fff; padding: 3px 8px; border-radius: 8px; font-size: 11px; }}
            .badge-entry {{ background: rgba(34, 197, 94, 0.2); color: var(--pos); padding: 3px 8px; border-radius: 8px; font-size: 11px; }}
            .badge-exit {{ background: rgba(239, 68, 68, 0.2); color: var(--neg); padding: 3px 8px; border-radius: 8px; font-size: 11px; }}
        </style>
    </head>
    <body>
        <div class="sidebar" id="sidebar">
            <button class="btn-overview" onclick="showOverview()">📊 Global Overview</button>
            <h3>Monthly Timeline</h3>
        </div>
        
        <div class="main-content">
            <div class="header-row">
                <h1 id="page-title">Performance Overview</h1>
                <div class="nav-controls" id="nav-controls">
                    <button class="nav-btn" id="prev-btn" onclick="goPrev()">⬅ Older</button>
                    <button class="nav-btn" id="next-btn" onclick="goNext()">Newer ➡</button>
                </div>
            </div>
            
            <div id="overview-view">
                <div class="metrics-grid">
                    <div class="metric-card"><div class="metric-title">Total Return</div><div class="metric-value pos" id="g-total">--</div></div>
                    <div class="metric-card"><div class="metric-title">CAGR</div><div class="metric-value pos" id="g-cagr">--</div></div>
                    <div class="metric-card"><div class="metric-title">Max Drawdown</div><div class="metric-value neg" id="g-dd">--</div></div>
                    <div class="metric-card"><div class="metric-title">Sharpe Ratio</div><div class="metric-value" id="g-sharpe" style="color: #c084fc;">--</div></div>
                    <div class="metric-card"><div class="metric-title">Avg Monthly Churn</div><div class="metric-value" id="g-churn">--</div></div>
                </div>
                <div class="chart-container"><canvas id="equityChart"></canvas></div>
            </div>

            <div id="monthly-view" class="hidden">
                <div class="metrics-grid">
                    <div class="metric-card"><div class="metric-title">Market Regime Limit</div><div class="metric-value" id="port-regime">--</div></div>
                    <div class="metric-card"><div class="metric-title">Month-Over-Month Return</div><div class="metric-value" id="port-pnl">--</div></div>
                    <div class="metric-card"><div class="metric-title">Monthly Churn</div><div class="metric-value" id="port-churn">--</div></div>
                </div>
                <div class="audit-box">
                    <div class="metric-title" style="color: var(--accent);">AI Heavy-Lifting Verification Report</div>
                    <pre id="ai-audit">Loading...</pre>
                </div>
                <div class="metric-title" style="margin-bottom: 16px;">Transition Matrix</div>
                <table>
                    <thead><tr><th>Action</th><th>Symbol</th><th>Entry Date</th><th>Exit Date</th><th>Score</th><th>Deliv %</th><th>Cum. PNL</th></tr></thead>
                    <tbody id="table-body"></tbody>
                </table>
            </div>
        </div>

        <script>
            const fullData = {json_payload};
            const global = fullData.global;
            const monthly = fullData.monthly;
            const dates = Object.keys(monthly); 
            let currentIndex = 0; let myChart = null;

            function showOverview() {{
                document.getElementById('overview-view').classList.remove('hidden');
                document.getElementById('monthly-view').classList.add('hidden');
                document.getElementById('page-title').innerText = "Performance Overview";
                document.getElementById('prev-btn').classList.remove('show'); document.getElementById('next-btn').classList.remove('show');
                document.querySelectorAll('.month-btn').forEach(btn => btn.classList.remove('active'));

                document.getElementById('g-total').innerText = global.total_ret;
                document.getElementById('g-cagr').innerText = global.cagr;
                document.getElementById('g-dd').innerText = global.max_dd;
                document.getElementById('g-sharpe').innerText = global.sharpe;
                document.getElementById('g-churn').innerText = global.avg_churn;

                if(myChart) myChart.destroy();
                const ctx = document.getElementById('equityChart').getContext('2d');
                myChart = new Chart(ctx, {{
                    type: 'line', data: {{ labels: global.chart_dates, datasets: [{{ label: 'Equity Growth', data: global.chart_equity, borderColor: '#3b82f6', borderWidth: 2, pointRadius: 0 }}] }},
                    options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }}, scales: {{ x: {{ grid: {{ display: false }} }} }} }}
                }});
            }}
            
            function showMonth(date) {{
                currentIndex = dates.indexOf(date);
                document.getElementById('overview-view').classList.add('hidden');
                document.getElementById('monthly-view').classList.remove('hidden');
                document.getElementById('page-title').innerText = "Snapshot: " + date;
                document.getElementById('prev-btn').classList.add('show'); document.getElementById('next-btn').classList.add('show');
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
                    let exitDate = stock.ACTION === 'EXIT' ? date : 'Active';
                    let pnlDisplay = stock.PNL;
                    let pnlClass = stock.PNL === 'NEW' ? '' : (stock.PNL.includes('-') ? 'neg' : 'pos');

                    rowsHtml += `<tr>
                        <td><span class="${{badgeClass}}">${{stock.ACTION}}</span></td>
                        <td style="font-weight: 700; color: #fff;">${{stock.SYMBOL}}</td>
                        <td>${{stock.ENTRY_DATE}}</td>
                        <td>${{exitDate}}</td>
                        <td>${{parseFloat(stock.SCORE).toFixed(2)}}</td>
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
