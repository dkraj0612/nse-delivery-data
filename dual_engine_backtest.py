"""
dual_engine_backtest.py - INSTITUTIONAL EDITION (RISK-ADJUSTED)
==========================================================
1. BACKTEST ENGINE: Risk-Adjusted Momentum (Volatility Penalty) + 0.5% Friction Tax.
2. AI AUDITOR: Audits latest month to save API limits.
3. GITHUB PUBLISHER: Bloomberg-style dashboard with Sharpe, Volatility, and Churn metrics.
"""
import os
import glob
import json
import time
import pandas as pd
import numpy as np
import concurrent.futures
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
# 2. RISK-ADJUSTED BACKTEST ENGINE 
# ==========================================
def run_pure_momentum_backtest(df):
    print("Running Risk-Adjusted Backtest Engine & Equity Simulator...")
    
    # Base Momentum
    df['P_1M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['P_7M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(147) 
    df['P_13M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    df['PRICE_MOMENTUM'] = (((df['P_1M'] - df['P_13M']) / df['P_13M']) * 2) + ((df['P_1M'] - df['P_7M']) / df['P_7M'])
    
    # IMPLEMENTATION 2: Volatility Penalty (Risk-Adjusted Momentum)
    df['DAILY_RET'] = df.groupby('SYMBOL')['CLOSE_PRICE'].pct_change()
    df['VOLATILITY_90D'] = df.groupby('SYMBOL')['DAILY_RET'].transform(lambda x: x.rolling(90, min_periods=20).std() * np.sqrt(252))
    df['VOLATILITY_90D'] = df['VOLATILITY_90D'].replace(0, 0.001).fillna(0.001) # Prevent division by zero
    
    df['EMA_100'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=100, adjust=False).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    df['AVG_DELIV_PER'] = df.groupby('SYMBOL')['DELIV_PER'].transform(lambda x: x.rolling(20).mean())
    
    df['YEAR_MONTH'] = df['DATE'].dt.to_period('M')
    month_ends = df.groupby('YEAR_MONTH')['DATE'].max().reset_index()
    rebalance_df = df[df['DATE'].isin(month_ends['DATE'])].copy()
    
    # THE NEW MASTER SCORE: Momentum divided by Volatility
    rebalance_df['MASTER_SCORE'] = (rebalance_df['PRICE_MOMENTUM'] / rebalance_df['VOLATILITY_90D']) * 100
    
    valid_pool = rebalance_df[
        (rebalance_df['CLOSE_PRICE'] >= rebalance_df['EMA_100']) & 
        (rebalance_df['CLOSE_PRICE'] >= (rebalance_df['52W_HIGH'] * 0.80)) & 
        (rebalance_df['AVG_TURNOVER'] >= 1000.0) & 
        (rebalance_df['AVG_DELIV_PER'] >= 30.0) & 
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
        
        # 1. CALCULATE EQUITY CURVE (Capital Appreciation)
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
            
        # 2. CONSTRUCT NEW PORTFOLIO
        candidates = valid_pool[valid_pool['DATE'] == current_date].copy()
        prev_symbols = set(prev_portfolio_df['SYMBOL']) if not prev_portfolio_df.empty else set()
        
        if candidates.empty:
            for sym in prev_symbols:
                portfolio_snapshots.append({
                    'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL']==sym]['SECTOR'].iloc[0],
                    'ACTION': 'EXIT', 'PRICE': day_prices.get(sym, 0), 'ENTRY_DATE': entry_dates.get(sym, 'N/A'),
                    'PNL': f"{((day_prices.get(sym, 0)/entry_prices.get(sym, 1))-1)*100:+.2f}%", 
                    'DELIV_%': f"{day_deliv.get(sym, 0):.1f}%" if pd.notna(day_deliv.get(sym, 0)) else "N/A",
                })
            entry_prices.clear(); entry_dates.clear()
            prev_portfolio_df = pd.DataFrame()
            equity_curve.append({'DATE': curr_date_str, 'EQUITY': capital, 'CHURN': 1.0})
            continue

        candidates = candidates.sort_values(by='MASTER_SCORE', ascending=False)
        top_40 = candidates.head(40).copy()
        final_portfolio = pd.concat([top_40[top_40['SYMBOL'].isin(prev_symbols)], top_40[~top_40['SYMBOL'].isin(prev_symbols)]]).head(20).copy()
        current_symbols = set(final_portfolio['SYMBOL'])
        
        # IMPLEMENTATION 3: The Friction Tax (0.5% on Churn)
        num_new_trades = len(current_symbols - prev_symbols)
        num_slots = len(final_portfolio)
        churn_ratio = 0.0
        
        if num_slots > 0:
            churn_ratio = num_new_trades / num_slots
            # Apply 0.5% tax ONLY to the capital being moved/swapped
            friction_penalty = capital * churn_ratio * 0.005 
            capital -= friction_penalty
            
        # Append equity curve AFTER paying the rebalance tax
        equity_curve.append({'DATE': curr_date_str, 'EQUITY': capital, 'CHURN': churn_ratio})
        
        for sym in (prev_symbols - current_symbols):
            portfolio_snapshots.append({
                'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL']==sym]['SECTOR'].iloc[0],
                'ACTION': 'EXIT', 'PRICE': day_prices.get(sym, 0), 'ENTRY_DATE': entry_dates.get(sym, 'N/A'),
                'PNL': f"{((day_prices.get(sym, 0)/entry_prices.get(sym, 1))-1)*100:+.2f}%", 
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
                'ACTION': 'HOLD' if sym in prev_symbols else 'ENTRY', 'PRICE': curr_price, 
                'ENTRY_DATE': entry_dates[sym], 'PNL': pnl_str, 
                'DELIV_%': f"{row['AVG_DELIV_PER']:.1f}%",
            })
            
        prev_portfolio_df = final_portfolio.copy()

    df_snaps = pd.DataFrame(portfolio_snapshots)
    df_equity = pd.DataFrame(equity_curve)
    
    df_equity['MOM_RET'] = df_equity['EQUITY'].pct_change() * 100
    df_equity['MOM_RET'] = df_equity['MOM_RET'].fillna(0.0)
    
    df_snaps.to_csv("backtest_portfolio_history.csv", index=False)
    return df_snaps, df_equity

# ==========================================
# 3. FAST AI AUDIT
# ==========================================
def run_single_latest_audit(portfolio_df):
    print("\nTriggering Single AI Audit for the Latest Portfolio...")
    progress_file = "audit_progress.json"
    if os.path.exists(progress_file):
        with open(progress_file, "r") as f:
            try:
                audit_progress = json.load(f)
            except json.JSONDecodeError:
                audit_progress = {"results": {}}
    else:
        audit_progress = {"results": {}}
        
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client() if api_key else None
    if not client: return audit_progress

    latest_date = portfolio_df['DATE'].max()
    latest_portfolio = portfolio_df[(portfolio_df['DATE'] == latest_date) & (portfolio_df['ACTION'].isin(['ENTRY', 'HOLD']))]
    
    if latest_portfolio.empty: return audit_progress
        
    audit_df = latest_portfolio[['SYMBOL', 'SECTOR', 'PRICE', 'DELIV_%', 'ENTRY_DATE']]
    prompt = f"""
    FORENSIC AUDIT DATE: {latest_date}
    You are an Equities Auditor. You are performing a STRICT point-in-time audit for {latest_date}.
    Analyze for severe historical governance red flags known ONLY up to {latest_date}. 
    Keep it brief. If clean, output: "✓ No major historical anomalies detected as of {latest_date}."
    \n{audit_df.to_markdown(index=False)}
    """
    
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(client.models.generate_content, model='gemini-2.5-flash', contents=prompt)
            resp = future.result(timeout=30) 
            
        audit_progress["results"][latest_date] = resp.text
        with open(progress_file, "w") as f:
            json.dump(audit_progress, f, indent=4)
            
        print(f"  -> [SUCCESS] Audit logged for {latest_date}.")
    except Exception as e:
        print(f"  -> [FATAL ERROR] API Failed: {e}")

    return audit_progress

# ==========================================
# 4. INSTITUTIONAL DASHBOARD PUBLISHER
# ==========================================
def generate_dashboards(audit_progress, df_snaps, df_equity):
    print("Generating Pro-Grade Dashboard with Global Risk Metrics...")
    
    initial_equity = 1000000.0
    final_equity = df_equity['EQUITY'].iloc[-1]
    total_return_pct = ((final_equity / initial_equity) - 1) * 100
    
    start_date = pd.to_datetime(df_equity['DATE'].iloc[0])
    end_date = pd.to_datetime(df_equity['DATE'].iloc[-1])
    years = (end_date - start_date).days / 365.25
    if years <= 0: years = 1 
    
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
    
    active_positions = df_snaps[df_snaps['ACTION'].isin(['ENTRY', 'HOLD'])].copy()
    dashboard_data = {}
    unique_dates = sorted(active_positions['DATE'].unique(), reverse=True)
    
    for date in unique_dates:
        day_df = active_positions[active_positions['DATE'] == date].copy()
        if day_df.empty: continue
        
        mom_ret_val = df_equity.loc[df_equity['DATE'] == date, 'MOM_RET'].values
        mom_val = mom_ret_val[0] if len(mom_ret_val) > 0 and pd.notna(mom_ret_val[0]) else 0.0
        
        churn_val_arr = df_equity.loc[df_equity['DATE'] == date, 'CHURN'].values
        churn_val = (churn_val_arr[0] * 100) if len(churn_val_arr) > 0 and pd.notna(churn_val_arr[0]) else 0.0
        
        stocks_list = day_df[['SYMBOL', 'SECTOR', 'ENTRY_DATE', 'PRICE', 'DELIV_%', 'PNL']].to_dict('records')
        ai_audit_text = audit_progress.get("results", {}).get(date, "Historical mathematical backtest completed. (AI Audit skipped to preserve rate limits).")
        
        dashboard_data[date] = {
            "portfolio_pnl": f"{mom_val:+.2f}%",
            "month_churn": f"{churn_val:.1f}%",
            "ai_audit": ai_audit_text,
            "stocks": stocks_list
        }
    
    page_data = {
        "global": {
            "cagr": f"{cagr_pct:.2f}%",
            "total_ret": f"{total_return_pct:.2f}%",
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
            :root {{
                --bg-primary: #0a0b10;
                --bg-secondary: #14151c;
                --accent: #3b82f6;
                --accent-glow: rgba(59, 130, 246, 0.3);
                --text-main: #f1f5f9;
                --text-muted: #94a3b8;
                --pos: #22c55e;
                --neg: #ef4444;
                --border: #1e293b;
            }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: var(--bg-primary); color: var(--text-main); margin: 0; display: flex; height: 100vh; overflow: hidden; }}
            
            .sidebar {{ width: 280px; background: var(--bg-secondary); height: 100vh; overflow-y: auto; padding: 25px 20px; box-sizing: border-box; border-right: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; }}
            .sidebar h3 {{ color: var(--text-muted); margin-top: 0; margin-bottom: 15px; text-transform: uppercase; font-size: 12px; letter-spacing: 1.5px; font-weight: 700; border-bottom: 1px solid var(--border); padding-bottom: 10px;}}
            
            .btn-overview {{ background: rgba(59, 130, 246, 0.1); color: var(--accent); border: 1px solid var(--accent); padding: 14px 16px; cursor: pointer; text-align: left; border-radius: 8px; font-size: 15px; font-weight: 700; transition: all 0.2s ease; margin-bottom: 20px; box-shadow: 0 4px 12px var(--accent-glow); }}
            .btn-overview:hover {{ background: var(--accent); color: #fff; }}

            .month-btn {{ display: block; width: 100%; background: transparent; color: var(--text-muted); border: 1px solid transparent; padding: 12px 16px; cursor: pointer; text-align: left; border-radius: 8px; font-size: 14px; font-weight: 500; transition: all 0.2s ease; }}
            .month-btn:hover {{ background: rgba(255,255,255,0.05); color: var(--text-main); }}
            .month-btn.active {{ background: #1e293b; color: #fff; border-left: 4px solid var(--accent); border-radius: 4px 8px 8px 4px; font-weight: 700; }}
            
            .main-content {{ flex-grow: 1; padding: 40px; overflow-y: auto; box-sizing: border-box; }}
            
            .header-row {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 20px; margin-bottom: 30px; }}
            h1 {{ margin: 0; font-size: 32px; font-weight: 800; letter-spacing: -0.5px; }}
            
            .nav-controls {{ display: flex; gap: 12px; }}
            .nav-btn {{ background: var(--bg-secondary); color: var(--text-main); border: 1px solid var(--border); padding: 10px 20px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 14px; transition: all 0.2s; display: none; }}
            .nav-btn.show {{ display: flex; align-items: center; justify-content: center; }}
            .nav-btn:hover:not(:disabled) {{ background: var(--border); }}
            .nav-btn:disabled {{ opacity: 0.3; cursor: not-allowed; }}

            .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-bottom: 40px; }}
            .metric-card {{ background: var(--bg-secondary); padding: 24px; border-radius: 12px; border: 1px solid var(--border); box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
            .metric-title {{ font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; font-weight: 600; }}
            .metric-value {{ font-size: 36px; font-weight: 800; letter-spacing: -1px; }}
            
            .chart-container {{ background: var(--bg-secondary); padding: 24px; border-radius: 12px; border: 1px solid var(--border); margin-bottom: 40px; height: 400px; }}
            
            .audit-box {{ background: rgba(59, 130, 246, 0.05); padding: 24px; border-radius: 12px; margin-bottom: 40px; border: 1px solid rgba(59, 130, 246, 0.2); }}
            pre {{ white-space: pre-wrap; font-family: 'Segoe UI', sans-serif; font-size: 15px; color: var(--text-main); margin: 0; line-height: 1.6; }}
            
            table {{ width: 100%; border-collapse: collapse; background: var(--bg-secondary); border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1); border: 1px solid var(--border); }}
            th, td {{ padding: 16px 20px; text-align: left; border-bottom: 1px solid var(--border); font-size: 14px; }}
            th {{ background: #181a25; color: var(--text-muted); font-weight: 600; text-transform: uppercase; font-size: 12px; letter-spacing: 1px; }}
            tr:hover {{ background: rgba(255,255,255,0.02); }}
            
            .pos {{ color: var(--pos); }}
            .neg {{ color: var(--neg); }}
            .hidden {{ display: none !important; }}
            .new-badge {{ background: rgba(59, 130, 246, 0.2); color: var(--accent); padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 700; }}
        </style>
    </head>
    <body>
        <div class="sidebar" id="sidebar">
            <button class="btn-overview" onclick="showOverview()">📊 Global Overview</button>
            <h3>Monthly Timeline</h3>
        </div>
        
        <div class="main-content">
            <div class="header-row">
                <h1 id="page-title">Portfolio Performance Overview</h1>
                <div class="nav-controls" id="nav-controls">
                    <button class="nav-btn" id="prev-btn" onclick="goPrev()">⬅ Older</button>
                    <button class="nav-btn" id="next-btn" onclick="goNext()">Newer ➡</button>
                </div>
            </div>
            
            <div id="overview-view">
                <div class="metrics-grid">
                    <div class="metric-card">
                        <div class="metric-title">CAGR</div>
                        <div class="metric-value pos" id="g-cagr">--</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Max Drawdown</div>
                        <div class="metric-value neg" id="g-dd">--</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Win Rate</div>
                        <div class="metric-value" id="g-win">--</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Ann. Volatility</div>
                        <div class="metric-value" id="g-vol" style="color: #fcd34d;">--</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Sharpe Ratio</div>
                        <div class="metric-value" id="g-sharpe" style="color: #c084fc;">--</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Avg Monthly Churn</div>
                        <div class="metric-value" id="g-churn" style="color: #cbd5e1;">--</div>
                    </div>
                </div>
                
                <div class="chart-container">
                    <canvas id="equityChart"></canvas>
                </div>
            </div>

            <div id="monthly-view" class="hidden">
                <div class="metrics-grid">
                    <div class="metric-card">
                        <div class="metric-title">Month-Over-Month Return</div>
                        <div class="metric-value" id="port-pnl">--</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-title">Portfolio Churn</div>
                        <div class="metric-value" id="port-churn" style="color: var(--text-muted);">--</div>
                    </div>
                </div>

                <div class="audit-box">
                    <div class="metric-title" style="color: var(--accent);">AI Forensic Governance Audit</div>
                    <pre id="ai-audit">Loading...</pre>
                </div>

                <div class="metric-title" style="margin-bottom: 16px;">Active Holdings for Month</div>
                <table>
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Sector</th>
                            <th>Entry Date</th>
                            <th>Price</th>
                            <th>Deliv %</th>
                            <th>Open PNL</th>
                        </tr>
                    </thead>
                    <tbody id="table-body"></tbody>
                </table>
            </div>
        </div>

        <script>
            const fullData = {json_payload};
            const global = fullData.global;
            const monthly = fullData.monthly;
            const dates = Object.keys(monthly); 
            let currentIndex = 0;
            let myChart = null;

            function showOverview() {{
                document.getElementById('overview-view').classList.remove('hidden');
                document.getElementById('monthly-view').classList.add('hidden');
                document.getElementById('page-title').innerText = "Performance Overview";
                
                document.getElementById('prev-btn').classList.remove('show');
                document.getElementById('next-btn').classList.remove('show');
                document.querySelectorAll('.month-btn').forEach(btn => btn.classList.remove('active'));

                document.getElementById('g-cagr').innerText = global.cagr;
                document.getElementById('g-dd').innerText = global.max_dd;
                document.getElementById('g-win').innerText = global.win_rate;
                document.getElementById('g-vol').innerText = global.volatility;
                document.getElementById('g-sharpe').innerText = global.sharpe;
                document.getElementById('g-churn').innerText = global.avg_churn;

                if(myChart) myChart.destroy();
                const ctx = document.getElementById('equityChart').getContext('2d');
                myChart = new Chart(ctx, {{
                    type: 'line',
                    data: {{
                        labels: global.chart_dates,
                        datasets: [{{
                            label: 'Equity Growth (₹)',
                            data: global.chart_equity,
                            borderColor: '#3b82f6',
                            backgroundColor: 'rgba(59, 130, 246, 0.1)',
                            borderWidth: 3,
                            fill: true,
                            tension: 0.1,
                            pointRadius: 0
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {{ legend: {{ display: false }} }},
                        scales: {{
                            y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
                            x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8' }} }}
                        }}
                    }}
                }});
            }}
            
            function showMonth(date) {{
                currentIndex = dates.indexOf(date);
                
                document.getElementById('overview-view').classList.add('hidden');
                document.getElementById('monthly-view').classList.remove('hidden');
                document.getElementById('page-title').innerText = "Snapshot: " + date;
                
                document.getElementById('prev-btn').classList.add('show');
                document.getElementById('next-btn').classList.add('show');
                
                document.getElementById('prev-btn').disabled = (currentIndex >= dates.length - 1);
                document.getElementById('next-btn').disabled = (currentIndex <= 0);

                document.querySelectorAll('.month-btn').forEach(btn => btn.classList.remove('active'));
                const activeBtn = document.getElementById('btn-' + date);
                if(activeBtn) activeBtn.classList.add('active');

                const data = monthly[date];
                
                const pnlElement = document.getElementById('port-pnl');
                pnlElement.innerText = data.portfolio_pnl;
                pnlElement.className = "metric-value " + (data.portfolio_pnl.includes('-') ? 'neg' : 'pos');
                
                document.getElementById('port-churn').innerText = data.month_churn;
                document.getElementById('ai-audit').innerText = data.ai_audit;

                let rowsHtml = '';
                data.stocks.forEach(stock => {{
                    let pnlDisplay = stock.PNL;
                    let pnlClass = '';
                    
                    if (stock.PNL === 'NEW') {{
                        pnlDisplay = '<span class="new-badge">NEW ENTRY</span>';
                    }} else if (stock.PNL.includes('-')) {{
                        pnlClass = 'neg';
                    }} else {{
                        pnlClass = 'pos';
                    }}

                    rowsHtml += `<tr>
                        <td style="font-weight: 700; color: #fff;">${{stock.SYMBOL}}</td>
                        <td style="color: var(--text-muted);">${{stock.SECTOR}}</td>
                        <td>${{stock.ENTRY_DATE}}</td>
                        <td>₹${{parseFloat(stock.PRICE).toFixed(2)}}</td>
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
                const btn = document.createElement('button');
                btn.className = 'month-btn';
                btn.id = 'btn-' + date;
                btn.innerText = date;
                btn.onclick = () => showMonth(date);
                sidebar.appendChild(btn);
            }});

            showOverview();
        </script>
    </body>
    </html>
    """
    
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print("Dashboard complete. Check index.html!")

if __name__ == "__main__":
    DATA_PATH = "./HistoricalBhavCopy/NSE"
    SECTOR_MAP = "./nifty500_sectors.csv" 
    
    try:
        raw_df = load_and_adjust_data(DATA_PATH, SECTOR_MAP)
        portfolio_history_df, equity_curve_df = run_pure_momentum_backtest(raw_df)
        audit_state = run_single_latest_audit(portfolio_history_df)
        generate_dashboards(audit_state, portfolio_history_df, equity_curve_df)
        print("\nProcess Complete.")
        
    except Exception as e:
        print(f"Execution failed: {e}")
