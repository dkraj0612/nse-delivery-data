"""
dual_engine_backtest.py - INTERACTIVE DASHBOARD EDITION
==========================================================
1. BACKTEST ENGINE: Fast math for 100 EMA, 30% Delivery.
2. AI AUDITOR: Audits ONLY the latest month, saving to JSON history.
3. GITHUB PUBLISHER: Generates an interactive, clickable HTML dashboard.
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
# 2. PURE MOMENTUM BACKTEST ENGINE
# ==========================================
def run_pure_momentum_backtest(df):
    print("Running Mathematical Backtest Engine...")
    df['P_1M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['P_7M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(147) 
    df['P_13M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    df['PRICE_MOMENTUM'] = (((df['P_1M'] - df['P_13M']) / df['P_13M']) * 2) + ((df['P_1M'] - df['P_7M']) / df['P_7M'])
    
    df['EMA_100'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=100, adjust=False).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    df['AVG_DELIV_PER'] = df.groupby('SYMBOL')['DELIV_PER'].transform(lambda x: x.rolling(20).mean())
    
    df['YEAR_MONTH'] = df['DATE'].dt.to_period('M')
    month_ends = df.groupby('YEAR_MONTH')['DATE'].max().reset_index()
    rebalance_df = df[df['DATE'].isin(month_ends['DATE'])].copy()
    rebalance_df['MASTER_SCORE'] = rebalance_df['PRICE_MOMENTUM'] * 100
    
    valid_pool = rebalance_df[
        (rebalance_df['CLOSE_PRICE'] >= rebalance_df['EMA_100']) & 
        (rebalance_df['CLOSE_PRICE'] >= (rebalance_df['52W_HIGH'] * 0.80)) & 
        (rebalance_df['AVG_TURNOVER'] >= 1000.0) & 
        (rebalance_df['AVG_DELIV_PER'] >= 30.0) & 
        (rebalance_df['MASTER_SCORE'].notna())
    ].copy()

    dates = sorted(rebalance_df['DATE'].dropna().unique())
    portfolio_snapshots = []
    prev_portfolio_df = pd.DataFrame()
    entry_prices = {}
    entry_dates = {}
    
    for current_date in dates:
        curr_date_str = current_date.strftime('%Y-%m-%d')
        day_data = rebalance_df[rebalance_df['DATE'] == current_date]
        if day_data.empty: continue
            
        day_prices = day_data.set_index('SYMBOL')['CLOSE_PRICE'].to_dict()
        day_deliv = day_data.set_index('SYMBOL')['AVG_DELIV_PER'].to_dict()
        
        candidates = valid_pool[valid_pool['DATE'] == current_date].copy()
        prev_symbols = set(prev_portfolio_df['SYMBOL']) if not prev_portfolio_df.empty else set()
        
        if candidates.empty:
            for sym in prev_symbols:
                portfolio_snapshots.append({
                    'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL']==sym]['SECTOR'].iloc[0],
                    'ACTION': 'EXIT', 'PRICE': day_prices.get(sym, 0), 'ENTRY_DATE': entry_dates.get(sym, 'N/A'),
                    'PNL': f"{((day_prices.get(sym, 0)/entry_prices.get(sym, 1))-1)*100:+.2f}%", 
                    'DELIV_%': f"{day_deliv.get(sym, 0):.1f}%" if pd.notna(day_deliv.get(sym, 0)) else "N/A",
                    'JUSTIFICATION': "Failed Guardrails"
                })
            entry_prices.clear(); entry_dates.clear()
            prev_portfolio_df = pd.DataFrame() 
            continue

        candidates = candidates.sort_values(by='MASTER_SCORE', ascending=False)
        top_40 = candidates.head(40).copy()
        final_portfolio = pd.concat([top_40[top_40['SYMBOL'].isin(prev_symbols)], top_40[~top_40['SYMBOL'].isin(prev_symbols)]]).head(20).copy()
        current_symbols = set(final_portfolio['SYMBOL'])
        
        for sym in (prev_symbols - current_symbols):
            portfolio_snapshots.append({
                'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL']==sym]['SECTOR'].iloc[0],
                'ACTION': 'EXIT', 'PRICE': day_prices.get(sym, 0), 'ENTRY_DATE': entry_dates.get(sym, 'N/A'),
                'PNL': f"{((day_prices.get(sym, 0)/entry_prices.get(sym, 1))-1)*100:+.2f}%", 
                'DELIV_%': f"{day_deliv.get(sym, 0):.1f}%" if pd.notna(day_deliv.get(sym, 0)) else "N/A",
                'JUSTIFICATION': "Fell below Buffer"
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
                'JUSTIFICATION': "Top 20 Momentum"
            })
            
        prev_portfolio_df = final_portfolio.copy()

    df_snaps = pd.DataFrame(portfolio_snapshots)
    df_snaps.to_csv("backtest_portfolio_history.csv", index=False)
    return df_snaps

# ==========================================
# 3. FAST AI AUDIT (LATEST DATA ONLY, SAVES HISTORY)
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
    
    if not client:
        print("No GEMINI_API_KEY found. Skipping AI Audit.")
        return audit_progress

    latest_date = portfolio_df['DATE'].max()
    latest_portfolio = portfolio_df[(portfolio_df['DATE'] == latest_date) & (portfolio_df['ACTION'].isin(['ENTRY', 'HOLD']))]
    
    if latest_portfolio.empty:
        print("No active positions on the latest date.")
        return audit_progress
        
    audit_df = latest_portfolio[['SYMBOL', 'SECTOR', 'PRICE', 'DELIV_%', 'ENTRY_DATE']]
    
    prompt = f"""
    FORENSIC AUDIT DATE: {latest_date}
    
    You are an Equities Auditor. You are performing a STRICT point-in-time audit for {latest_date}.
    You are FORBIDDEN from using any data, news, price action, or SEBI filings that occurred AFTER {latest_date}.
    
    Top 20 Portfolio Constructed on this date:
    {audit_df.to_markdown(index=False)}
    
    Analyze for severe historical governance red flags known ONLY up to {latest_date}. 
    Keep it brief. If clean, output: "✓ No major historical anomalies detected as of {latest_date}."
    """
    
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(client.models.generate_content, model='gemini-2.5-flash', contents=prompt)
            resp = future.result(timeout=30) 
            
        audit_progress["results"][latest_date] = resp.text
        
        with open(progress_file, "w") as f:
            json.dump(audit_progress, f, indent=4)
            
        print(f"  -> [SUCCESS] Audit logged for {latest_date}. Dashboard preserved.")
    except Exception as e:
        print(f"  -> [FATAL ERROR] API Failed: {e}")

    return audit_progress

# ==========================================
# 4. GITHUB PAGES INTERACTIVE DASHBOARD PUBLISHER
# ==========================================
def generate_dashboards(audit_progress, df_snaps):
    print("Generating Interactive HTML Dashboard...")
    
    # 1. Prepare Data for the Dashboard
    active_positions = df_snaps[df_snaps['ACTION'].isin(['ENTRY', 'HOLD'])].copy()
    
    def pnl_to_float(pnl_str):
        if str(pnl_str) == "NEW" or pd.isna(pnl_str): return 0.0
        try:
            return float(str(pnl_str).replace('%', '').replace('+', ''))
        except:
            return 0.0

    active_positions['PNL_FLOAT'] = active_positions['PNL'].apply(pnl_to_float)
    
    dashboard_data = {}
    unique_dates = sorted(active_positions['DATE'].unique(), reverse=True)
    
    for date in unique_dates:
        day_df = active_positions[active_positions['DATE'] == date].copy()
        if day_df.empty: continue
        
        # Calculate Equal-Weight Cumulative Portfolio Open PNL
        avg_pnl = day_df['PNL_FLOAT'].mean()
        port_pnl_str = f"{avg_pnl:+.2f}%"
        
        stocks_list = day_df[['SYMBOL', 'SECTOR', 'ENTRY_DATE', 'PRICE', 'DELIV_%', 'PNL']].to_dict('records')
        ai_audit_text = audit_progress.get("results", {}).get(date, "Historical mathematical backtest completed. (AI Audit skipped to preserve rate limits).")
        
        dashboard_data[date] = {
            "portfolio_pnl": port_pnl_str,
            "ai_audit": ai_audit_text,
            "stocks": stocks_list
        }
    
    # 2. Inject Data into HTML Template
    json_payload = json.dumps(dashboard_data)
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Momentum AI Portfolio Dashboard</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #121212; color: #e0e0e0; margin: 0; display: flex; height: 100vh; overflow: hidden; }}
            .sidebar {{ width: 250px; background: #1e1e1e; height: 100vh; overflow-y: auto; padding: 20px; box-sizing: border-box; border-right: 1px solid #333; }}
            .sidebar h3 {{ color: #bb86fc; margin-top: 0; text-transform: uppercase; font-size: 14px; letter-spacing: 1px;}}
            .month-btn {{ display: block; width: 100%; background: transparent; color: #aaa; border: 1px solid #333; padding: 12px; margin-bottom: 8px; cursor: pointer; text-align: left; border-radius: 6px; font-size: 14px; transition: all 0.2s; }}
            .month-btn:hover {{ background: #2a2a2a; color: #fff; }}
            .month-btn.active {{ background: #bb86fc; color: #121212; font-weight: bold; border-color: #bb86fc; }}
            .main-content {{ flex-grow: 1; padding: 40px; overflow-y: auto; box-sizing: border-box; background: #121212; }}
            h1 {{ margin-top: 0; color: #fff; font-size: 28px; border-bottom: 2px solid #333; padding-bottom: 15px; margin-bottom: 30px; }}
            .cards-container {{ display: flex; gap: 20px; margin-bottom: 30px; flex-wrap: wrap; }}
            .metric-card {{ background: #1e1e1e; padding: 20px; border-radius: 8px; border-left: 4px solid #bb86fc; flex: 1; min-width: 250px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
            .metric-title {{ font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }}
            .metric-value {{ font-size: 36px; font-weight: bold; }}
            .pos-pnl {{ color: #4caf50; }}
            .neg-pnl {{ color: #ff5252; }}
            .audit-box {{ background: #1e1e1e; padding: 20px; border-radius: 8px; margin-bottom: 30px; border: 1px solid #333; }}
            pre {{ white-space: pre-wrap; font-family: inherit; font-size: 15px; color: #ccc; margin: 0; line-height: 1.5; }}
            table {{ width: 100%; border-collapse: collapse; background: #1e1e1e; border-radius: 8px; overflow: hidden; }}
            th, td {{ padding: 15px; text-align: left; border-bottom: 1px solid #2a2a2a; font-size: 14px; }}
            th {{ background: #2a2a2a; color: #bb86fc; font-weight: 600; text-transform: uppercase; font-size: 12px; letter-spacing: 1px; }}
            tr:last-child td {{ border-bottom: none; }}
            tr:hover {{ background: #252525; }}
            .new-badge {{ background: #bb86fc; color: #121212; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="sidebar" id="sidebar">
            <h3>Timeline</h3>
            </div>
        
        <div class="main-content">
            <h1 id="month-title">Loading...</h1>
            
            <div class="cards-container">
                <div class="metric-card">
                    <div class="metric-title">Cumulative Open Portfolio Return</div>
                    <div class="metric-value" id="port-pnl">--</div>
                </div>
            </div>

            <div class="audit-box">
                <div class="metric-title">AI Forensic Governance Audit</div>
                <pre id="ai-audit">Loading...</pre>
            </div>

            <div class="metric-title" style="margin-bottom: 15px;">Active Holdings for Month</div>
            <table>
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Sector</th>
                        <th>Entry Date</th>
                        <th>Current Price</th>
                        <th>Deliv %</th>
                        <th>Cumulative PNL</th>
                    </tr>
                </thead>
                <tbody id="table-body">
                    </tbody>
            </table>
        </div>

        <script>
            const data = {json_payload};
            const dates = Object.keys(data);
            
            function renderMonth(date) {{
                document.querySelectorAll('.month-btn').forEach(btn => btn.classList.remove('active'));
                document.getElementById('btn-' + date).classList.add('active');

                const monthData = data[date];
                document.getElementById('month-title').innerText = "Portfolio Snapshot: " + date;
                
                const pnlElement = document.getElementById('port-pnl');
                pnlElement.innerText = monthData.portfolio_pnl;
                pnlElement.className = "metric-value " + (monthData.portfolio_pnl.includes('-') ? 'neg-pnl' : 'pos-pnl');
                
                document.getElementById('ai-audit').innerText = monthData.ai_audit;

                let rowsHtml = '';
                monthData.stocks.forEach(stock => {{
                    let pnlDisplay = stock.PNL;
                    let pnlClass = '';
                    
                    if (stock.PNL === 'NEW') {{
                        pnlDisplay = '<span class="new-badge">NEW</span>';
                    }} else if (stock.PNL.includes('-')) {{
                        pnlClass = 'neg-pnl';
                    }} else {{
                        pnlClass = 'pos-pnl';
                    }}

                    rowsHtml += `<tr>
                        <td style="font-weight: bold; color: #fff;">${{stock.SYMBOL}}</td>
                        <td style="color: #aaa;">${{stock.SECTOR}}</td>
                        <td>${{stock.ENTRY_DATE}}</td>
                        <td>₹${{parseFloat(stock.PRICE).toFixed(2)}}</td>
                        <td>${{stock['DELIV_%']}}</td>
                        <td class="${{pnlClass}}" style="font-weight: bold;">${{pnlDisplay}}</td>
                    </tr>`;
                }});
                document.getElementById('table-body').innerHTML = rowsHtml;
            }}

            const sidebar = document.getElementById('sidebar');
            dates.forEach(date => {{
                const btn = document.createElement('button');
                btn.className = 'month-btn';
                btn.id = 'btn-' + date;
                btn.innerText = date;
                btn.onclick = () => renderMonth(date);
                sidebar.appendChild(btn);
            }});

            if (dates.length > 0) {{
                renderMonth(dates[0]);
            }}
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
        portfolio_history_df = run_pure_momentum_backtest(raw_df)
        
        audit_state = run_single_latest_audit(portfolio_history_df)
        
        # WE NOW PASS BOTH THE DATA AND THE AI AUDIT TO THE GENERATOR
        generate_dashboards(audit_state, portfolio_history_df)
        print("\nProcess Complete.")
        
    except Exception as e:
        print(f"Execution failed: {e}")
