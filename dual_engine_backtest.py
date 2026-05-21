"""
dual_engine_backtest.py
=======================
1. Fetches Nifty 50 from Yahoo Finance.
2. Cleans dirty NSE CSV headers and merges with sector map.
3. Corporate Action Filter: Detects and excludes stock splits.
4. Allocates 50% capital to Top Sectors, 50% to Lone Wolves.
5. Scores based PURELY on Price Momentum.
6. Institutional Buffer Rank Rebalancing.
7. Logs detailed Trade Ledger (Entry/Hold/Exit, PnL, Justification) to HTML.
8. BULLETPROOF Dashboard Generator (prevents cp: cannot stat errors).
"""
import os
import glob
import re
import time
import json
import pandas as pd
import numpy as np
import yfinance as yf
from google import genai

def fetch_nifty_regime():
    print("Fetching Nifty 50 index data for Circuit Breaker...")
    nifty = yf.download('^NSEI', period='5y', progress=False)
    
    if isinstance(nifty.columns, pd.MultiIndex):
        nifty.columns = nifty.columns.get_level_values(0)
        
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
            df = pd.read_csv(file)
            df.columns = df.columns.str.strip()
            
            if 'DATE1' in df.columns:
                df = df.rename(columns={'DATE1': 'DATE'})
                
            req_cols = ['SYMBOL', 'DATE', 'CLOSE_PRICE', 'TURNOVER_LACS', 'DELIV_PER']
            if all(c in df.columns for c in req_cols):
                df_list.append(df[req_cols])
        except Exception:
            continue
            
    if not df_list:
        raise ValueError("Failed to parse CSVs.")
        
    master_df = pd.concat(df_list, ignore_index=True)
    master_df['DATE'] = pd.to_datetime(master_df['DATE'], errors='coerce')
    
    for col in ['CLOSE_PRICE', 'TURNOVER_LACS', 'DELIV_PER']:
        master_df[col] = pd.to_numeric(master_df[col], errors='coerce')
        
    master_df['DELIV_PER'] = master_df['DELIV_PER'].fillna(0)
    master_df = master_df.dropna(subset=['DATE', 'CLOSE_PRICE'])
    
    master_df = master_df.drop_duplicates(subset=['SYMBOL', 'DATE'], keep='first')
    
    master_df = pd.merge(master_df, sector_map, on='SYMBOL', how='left')
    return master_df.sort_values(by=['SYMBOL', 'DATE']).reset_index(drop=True)

def run_dual_engine_backtest(df, regime_df):
    print("Calculating Metrics & Simulating Dual-Engine Portfolio...")
    
    df['DAILY_RET'] = df.groupby('SYMBOL')['CLOSE_PRICE'].pct_change()
    df['IS_CORPORATE_ACTION'] = (df['DAILY_RET'] < -0.25) | (df['DAILY_RET'] > 0.50)
    df['RECENT_SPLIT'] = df.groupby('SYMBOL')['IS_CORPORATE_ACTION'].transform(lambda x: x.rolling(252).max())
    
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
    
    rebalance_df['MASTER_SCORE'] = rebalance_df['PRICE_MOMENTUM'] * 100
    
    rebalance_df = pd.merge(rebalance_df, regime_df, on='DATE', how='left')
    rebalance_df['REGIME_GREEN'] = rebalance_df['REGIME_GREEN'].fillna(True)
    
    valid_pool = rebalance_df[
        (rebalance_df['CLOSE_PRICE'] >= 50.0) & 
        (rebalance_df['AVG_TURNOVER'] >= 1000.0) & 
        (rebalance_df['DELIV_PER_20MA'] >= 30.0) & 
        (rebalance_df['CLOSE_PRICE'] >= (rebalance_df['52W_HIGH'] * 0.70)) & 
        (rebalance_df['MASTER_SCORE'].notna()) &
        (rebalance_df['RECENT_SPLIT'] == 0) & 
        (rebalance_df['SECTOR'].notna()) 
    ].copy()

    dates = sorted(rebalance_df['DATE'].dropna().unique())
    formatted_dates = [d.strftime('%Y-%m-%d') for d in dates]
    
    monthly_records = []
    portfolio_snapshots = []
    prev_portfolio_df = pd.DataFrame()
    
    for current_date in dates:
        day_data_full = rebalance_df[rebalance_df['DATE'] == current_date]
        if day_data_full.empty: continue
            
        day_prices = day_data_full.set_index('SYMBOL')['CLOSE_PRICE'].to_dict()
        regime_status = day_data_full['REGIME_GREEN'].iloc[0]
        candidates = valid_pool[valid_pool['DATE'] == current_date].copy()
        
        prev_symbols = set(prev_portfolio_df['SYMBOL']) if not prev_portfolio_df.empty else set()
        prev_prices = prev_portfolio_df.set_index('SYMBOL')['CLOSE_PRICE'].to_dict() if not prev_portfolio_df.empty else {}
        
        if not regime_status or candidates.empty:
            justification = "Systematic Market Crash" if not regime_status else "No Stocks Passed Filter"
            for sym in prev_symbols:
                prev_price = prev_prices.get(sym, 0)
                curr_price = day_prices.get(sym)
                if curr_price is not None and prev_price > 0:
                    pnl_str = f"{((curr_price / prev_price) - 1)*100:+.2f}%"
                    curr_price_str = f"₹{curr_price:.2f}"
                else:
                    pnl_str = "-"
                    curr_price_str = "N/A"
                    
                portfolio_snapshots.append({
                    'DATE': current_date.strftime('%Y-%m-%d'),
                    'SYMBOL': sym,
                    'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL'] == sym]['SECTOR'].values[0],
                    'ACTION': 'EXIT',
                    'PRICE': curr_price_str,
                    'PNL': pnl_str,
                    'JUSTIFICATION': justification
                })
                
            monthly_records.append({'DATE': current_date, 'NET_RETURN': 0.004, 'REGIME': 'BEAR (CASH)'})
            prev_portfolio_df = pd.DataFrame() 
            continue

        sector_mom = candidates.groupby('SECTOR')['PRICE_MOMENTUM'].mean().reset_index()
        top_3_sectors = sector_mom.sort_values(by='PRICE_MOMENTUM', ascending=False).head(3)['SECTOR'].tolist()
        
        engine_a_candidates = candidates[candidates['SECTOR'].isin(top_3_sectors)].sort_values(by='MASTER_SCORE', ascending=False)
        engine_a_candidates = engine_a_candidates.groupby('SECTOR').head(3).reset_index(drop=True)
        top_15_a = engine_a_candidates.head(15).copy()
        engine_a = pd.concat([top_15_a[top_15_a['SYMBOL'].isin(prev_symbols)], top_15_a[~top_15_a['SYMBOL'].isin(prev_symbols)]]).head(10).copy()
        engine_a['SOURCE_ENGINE'] = 'Engine A'
        
        engine_b_candidates = candidates[~candidates['SYMBOL'].isin(engine_a['SYMBOL'])].sort_values(by='MASTER_SCORE', ascending=False)
        top_20_b = engine_b_candidates.head(20).copy()
        engine_b = pd.concat([top_20_b[top_20_b['SYMBOL'].isin(prev_symbols)], top_20_b[~top_20_b['SYMBOL'].isin(prev_symbols)]]).head(10).copy()
        engine_b['SOURCE_ENGINE'] = 'Engine B'
        
        final_portfolio = pd.concat([engine_a, engine_b])
        current_symbols = set(final_portfolio['SYMBOL'])
        
        exits = prev_symbols - current_symbols
        for sym in exits:
            prev_price = prev_prices.get(sym, 0)
            curr_price = day_prices.get(sym)
            if curr_price is not None and prev_price > 0:
                pnl_str = f"{((curr_price / prev_price) - 1)*100:+.2f}%"
                curr_price_str = f"₹{curr_price:.2f}"
            else:
                pnl_str = "-"
                curr_price_str = "N/A"
                
            justification = "Fell below Buffer Rank" if sym in candidates['SYMBOL'].values else "Failed Liquidity/Delivery/Price Filter"
            portfolio_snapshots.append({
                'DATE': current_date.strftime('%Y-%m-%d'),
                'SYMBOL': sym,
                'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL'] == sym]['SECTOR'].values[0],
                'ACTION': 'EXIT',
                'PRICE': curr_price_str,
                'PNL': pnl_str,
                'JUSTIFICATION': justification
            })
            
        for _, row in final_portfolio.iterrows():
            sym = row['SYMBOL']
            curr_price = row['CLOSE_PRICE']
            if sym in prev_symbols:
                action = 'HOLD'
                prev_price = prev_prices.get(sym, curr_price)
                pnl_str = f"{((curr_price / prev_price) - 1)*100:+.2f}%" if prev_price > 0 else "-"
                justification = "Maintained Buffer Rank"
            else:
                action = 'ENTRY'
                pnl_str = "NEW"
                justification = f"Top Breakout ({row['SOURCE_ENGINE']})"
                
            portfolio_snapshots.append({
                'DATE': current_date.strftime('%Y-%m-%d'),
                'SYMBOL': sym,
                'SECTOR': row['SECTOR'],
                'ACTION': action,
                'PRICE': f"₹{curr_price:.2f}",
                'PNL': pnl_str,
                'JUSTIFICATION': justification
            })
            
        if not final_portfolio.empty:
            returns = final_portfolio['FORWARD_1M_RET']
            clean_returns = returns[(returns >= -0.35) & (returns <= 1.0)] 
            avg_raw_return = clean_returns.mean() if not clean_returns.empty else 0
            net_monthly_return = avg_raw_return - 0.003
            monthly_records.append({'DATE': current_date, 'NET_RETURN': net_monthly_return, 'REGIME': 'BULL (EQUITY)'})
            
        prev_portfolio_df = final_portfolio.copy()

    perf_df = pd.DataFrame(monthly_records).dropna()
    perf_dict = {}
    total_months = cagr = max_dd = total_return = win_rate = 0.0
    
    if not perf_df.empty:
        perf_df['EQUITY_CURVE'] = (1 + perf_df['NET_RETURN']).cumprod()
        perf_df['CUM_MONTHS'] = range(1, len(perf_df) + 1)
        perf_df['CAGR'] = ((perf_df['EQUITY_CURVE'] ** (12 / perf_df['CUM_MONTHS'])) - 1) * 100
        perf_df['MONTHLY_PNL'] = (perf_df['NET_RETURN'] * 100).round(2).astype(str) + '%'
        perf_df['CAGR_STR'] = perf_df['CAGR'].round(2).astype(str) + '%'
        perf_df['DATE_STR'] = perf_df['DATE'].dt.strftime('%Y-%m-%d')
        perf_dict = perf_df.set_index('DATE_STR')[['MONTHLY_PNL', 'CAGR_STR']].to_dict('index')

        total_months = len(perf_df)
        cagr = perf_df['CAGR'].iloc[-1]
        perf_df['PEAK'] = perf_df['EQUITY_CURVE'].cummax()
        perf_df['DRAWDOWN'] = (perf_df['EQUITY_CURVE'] - perf_df['PEAK']) / perf_df['PEAK']
        max_dd = perf_df['DRAWDOWN'].min() * 100
        total_return = (perf_df['EQUITY_CURVE'].iloc[-1] - 1) * 100
        win_rate = (perf_df[perf_df['NET_RETURN'] > 0].shape[0] / total_months) * 100
        
        print("\n" + "="*50)
        print("🚀 FINAL STRATEGY SUMMARY (LEDGER INTEGRATED)")
        print("="*50)
        print(f"Realized CAGR       : {cagr:.2f}%")
        print(f"Maximum Drawdown    : {max_dd:.2f}%")
        print("="*50)

    pd.DataFrame(portfolio_snapshots).to_csv("backtest_portfolio_history.csv", index=False)
    snapshots_json = json.dumps(portfolio_snapshots)
    perf_json = json.dumps(perf_dict)
    dates_json = json.dumps(formatted_dates[::-1])
    
    history_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Quant Fund - Detailed Ledger</title>
        <style>
            body {{ background-color: #121212; color: #e0e0e0; font-family: -apple-system, sans-serif; padding: 10px; margin: 0; }}
            h2 {{ color: #ffffff; font-size: 18px; text-align: center; margin-bottom: 15px; }}
            .top-bar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }}
            .btn {{ padding: 8px 12px; background-color: #333; color: #fff; text-decoration: none; border-radius: 5px; font-size: 13px; border: none; cursor: pointer; }}
            
            .summary-dashboard {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 20px; background: #1a1a1a; padding: 12px; border-radius: 8px; border: 1px solid #333; }}
            .summary-dashboard .full-width {{ grid-column: span 2; background: #222; border: 1px solid #bb86fc; }}
            .metric-box {{ background: #252525; padding: 10px; border-radius: 6px; text-align: center; }}
            .metric-box h4 {{ margin: 0 0 4px 0; font-size: 10px; color: #aaa; text-transform: uppercase; }}
            .metric-box p {{ margin: 0; font-size: 16px; font-weight: bold; color: #fff; }}
            .metric-box .highlight {{ color: #bb86fc; font-size: 20px; }}
            
            .nav-controls {{ display: flex; justify-content: space-between; align-items: center; background: #1e1e1e; padding: 10px; border-radius: 8px; margin-bottom: 15px; border: 1px solid #333; }}
            select {{ flex-grow: 1; margin: 0 10px; padding: 8px; background: #2a2a2a; color: white; border: 1px solid #444; border-radius: 6px; font-size: 14px; font-weight: bold; text-align: center; appearance: none; }}
            
            .metrics-grid {{ display: flex; justify-content: space-between; gap: 8px; margin-bottom: 15px; }}
            .metric-card {{ background: #1e1e1e; padding: 10px; border-radius: 8px; width: 48%; text-align: center; border: 1px solid #333; }}
            .metric-title {{ font-size: 10px; color: #888; text-transform: uppercase; margin-bottom: 4px; }}
            .pnl-value {{ font-size: 18px; font-weight: bold; }}
            
            .table-container {{ background: #1e1e1e; border-radius: 8px; overflow-x: auto; border: 1px solid #333; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
            th, td {{ padding: 10px 8px; text-align: left; border-bottom: 1px solid #2a2a2a; }}
            th {{ background-color: #2a2a2a; color: #bb86fc; font-weight: 600; text-transform: uppercase; font-size: 10px; }}
            tr:hover {{ background-color: #252525; }}
            
            .badge {{ padding: 3px 6px; border-radius: 4px; font-weight: bold; font-size: 10px; }}
            .badge-entry {{ background: #1b5e20; color: #a5d6a7; }}
            .badge-hold {{ background: #37474f; color: #b0bec5; }}
            .badge-exit {{ background: #b71c1c; color: #ef9a9a; }}
        </style>
    </head>
    <body>
        <div class="top-bar">
            <a href="index.html" class="btn">⬅ Live View</a>
            <a href="backtest_portfolio_history.csv" class="btn" style="background-color: #1e88e5;">⬇️ CSV</a>
        </div>
        
        <h2>Detailed Trade Ledger</h2>
        
        <div class="summary-dashboard">
            <div class="metric-box full-width">
                <h4>Total Strategy Return</h4>
                <p class="highlight">{total_return:.2f}%</p>
            </div>
            <div class="metric-box">
                <h4>CAGR</h4>
                <p style="color: #4caf50;">{cagr:.2f}%</p>
            </div>
            <div class="metric-box">
                <h4>Max Drawdown</h4>
                <p style="color: #ff5252;">{max_dd:.2f}%</p>
            </div>
            <div class="metric-box">
                <h4>Win Rate</h4>
                <p>{win_rate:.1f}%</p>
            </div>
            <div class="metric-box">
                <h4>Months Active</h4>
                <p>{total_months}</p>
            </div>
        </div>

        <div class="nav-controls">
            <button id="prevBtn" class="btn">Older ⬅</button>
            <select id="dateSelect"></select>
            <button id="nextBtn" class="btn">➡ Newer</button>
        </div>
        
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-title">Portfolio Month PnL</div>
                <div id="monthPnl" class="pnl-value">-</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Running CAGR</div>
                <div id="cagr-running" class="pnl-value" style="color: #bb86fc;">-</div>
            </div>
        </div>
        
        <div class="table-container">
            <table id="portfolioTable">
                <thead>
                    <tr>
                        <th>Asset</th>
                        <th>Action</th>
                        <th>Stock PnL</th>
                        <th>Reasoning</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>

        <script>
            const snapshots = {snapshots_json};
            const perfData = {perf_json};
            const dates = {dates_json}; 
            
            const select = document.getElementById('dateSelect');
            const prevBtn = document.getElementById('prevBtn');
            const nextBtn = document.getElementById('nextBtn');
            
            dates.forEach(date => {{
                let opt = document.createElement('option');
                opt.value = date;
                opt.innerHTML = date;
                select.appendChild(opt);
            }});
            
            function updateView() {{
                const selectedDate = select.value;
                
                const actionOrder = {{ 'ENTRY': 1, 'HOLD': 2, 'EXIT': 3 }};
                const data = snapshots.filter(item => item.DATE === selectedDate).sort((a, b) => actionOrder[a.ACTION] - actionOrder[b.ACTION]);
                
                const currentIndex = select.selectedIndex;
                prevBtn.disabled = (currentIndex === select.options.length - 1);
                nextBtn.disabled = (currentIndex === 0);
                
                const pnl = perfData[selectedDate] ? perfData[selectedDate].MONTHLY_PNL : '0.00%';
                const runningCagr = perfData[selectedDate] ? perfData[selectedDate].CAGR_STR : '0.00%';
                
                const pnlElement = document.getElementById('monthPnl');
                pnlElement.innerHTML = pnl;
                pnlElement.style.color = pnl.includes('-') ? '#ff5252' : '#4caf50';
                document.getElementById('cagr-running').innerHTML = runningCagr;
                
                const tbody = document.querySelector('#portfolioTable tbody');
                tbody.innerHTML = '';
                
                if (data.length > 0) {{
                    data.forEach(row => {{
                        let tr = document.createElement('tr');
                        
                        let badgeClass = row.ACTION === 'ENTRY' ? 'badge-entry' : (row.ACTION === 'EXIT' ? 'badge-exit' : 'badge-hold');
                        let pnlColor = row.PNL.includes('+') ? '#4caf50' : (row.PNL.includes('-') ? '#ff5252' : '#aaa');
                        
                        tr.innerHTML = `
                            <td>
                                <div style="font-weight:bold; font-size:13px; color:#fff;">${{row.SYMBOL}}</div>
                                <div style="font-size:10px; color:#aaa; max-width:90px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${{row.SECTOR}}</div>
                            </td>
                            <td><span class="badge ${{badgeClass}}">${{row.ACTION}}</span></td>
                            <td>
                                <div style="font-weight:bold; color:${{pnlColor}}">${{row.PNL}}</div>
                                <div style="font-size:10px; color:#aaa;">${{row.PRICE}}</div>
                            </td>
                            <td style="font-size:10px; color:#bbb; line-height:1.2;">${{row.JUSTIFICATION}}</td>
                        `;
                        tbody.appendChild(tr);
                    }});
                }} else {{
                    let tr = document.createElement('tr');
                    tr.innerHTML = `<td colspan="4" style="text-align:center; color:#ff5252; padding: 20px; font-weight:bold;">100% CASH REGIME</td>`;
                    tbody.appendChild(tr);
                }}
            }}
            
            select.addEventListener('change', updateView);
            prevBtn.addEventListener('click', () => {{
                if (select.selectedIndex < select.options.length - 1) {{ select.selectedIndex++; updateView(); }}
            }});
            nextBtn.addEventListener('click', () => {{
                if (select.selectedIndex > 0) {{ select.selectedIndex--; updateView(); }}
            }});
            
            if(dates.length > 0) updateView();
        </script>
    </body>
    </html>
    """
    with open("history.html", "w", encoding="utf-8") as f:
        f.write(history_html)
        
    return valid_pool 

def audit_portfolio_with_gemini(valid_pool):
    # Absolute fallback HTML generation to prevent cp error in GitHub Actions
    fallback_html = """
    <!DOCTYPE html><html><head><title>Dashboard</title>
    <style>body{background:#121212; color:#fff; font-family:sans-serif; text-align:center; padding:50px;} 
    a{color:#bb86fc; text-decoration:none; font-size:20px; border:1px solid #bb86fc; padding:10px 20px; border-radius:5px;}</style>
    </head><body>
    <h2>Live Portfolio Snapshot Unavailable</h2>
    <p style="color:#aaa; margin-bottom:30px;">The AI audit failed or the API key is missing. Historical data is still intact.</p>
    <a href="history.html">View Detailed Trade Ledger ➔</a>
    </body></html>
    """
    
    if valid_pool.empty:
        print("No valid portfolio data generated to audit.")
        with open("portfolio_dashboard.html", "w", encoding="utf-8") as f:
            f.write(fallback_html)
        return

    latest_date = valid_pool['DATE'].max()
    month_data = valid_pool[valid_pool['DATE'] == latest_date].copy()
    
    sector_mom = month_data.groupby('SECTOR')['PRICE_MOMENTUM'].mean().reset_index()
    top_3_sectors = sector_mom.sort_values(by='PRICE_MOMENTUM', ascending=False).head(3)['SECTOR'].tolist()
    
    engine_a = month_data[month_data['SECTOR'].isin(top_3_sectors)].groupby('SECTOR').head(3).sort_values(by='MASTER_SCORE', ascending=False).head(10)
    engine_a['SOURCE_ENGINE'] = 'Engine A (Sector)'
    
    engine_b = month_data[~month_data['SYMBOL'].isin(engine_a['SYMBOL'])].sort_values(by='MASTER_SCORE', ascending=False).head(10)
    engine_b['SOURCE_ENGINE'] = 'Engine B (Lone Wolf)'
    
    final_live_portfolio = pd.concat([engine_a, engine_b])
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key: 
        print("⚠️ GEMINI_API_KEY secret not found. Writing fallback HTML.")
        with open("portfolio_dashboard.html", "w", encoding="utf-8") as f:
            f.write(fallback_html)
        return
        
    table_str = final_live_portfolio[['SYMBOL', 'SECTOR', 'SOURCE_ENGINE', 'CLOSE_PRICE', 'MASTER_SCORE']].to_markdown(index=False)
    
    prompt = f"""
    You are the Chief Risk Officer for an Indian quant fund. 
    Our algorithm selected these 20 stocks on {latest_date.strftime('%Y-%m-%d')}.
    Data:
    {table_str}
    
    Generate a complete HTML dashboard wrapping the 20 stocks. Ensure it contains a prominent button linking to 'history.html' with the text 'View Detailed Trade Ledger'. Wrap output in ```html codeblock.
    """
    
    client = genai.Client()
    success = False
    
    for attempt in range(3):
        try:
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            
            # Extract HTML
            html_match = re.search(r"""```html\s*(.*?)\s*
```""", response.text, re.DOTALL)
            html_content = html_match.group(1) if html_match else response.text
            
            # Ensure it is at least basic HTML if regex fails
            if "<html" not in html_content.lower():
                html_content = f"<html><body><pre>{response.text}</pre><br><a href='history.html'>View History</a></body></html>"
                
            with open("portfolio_dashboard.html", "w", encoding="utf-8") as f:
                f.write(html_content)
                
            print("✅ HTML Dashboard generated successfully.")
            success = True
            break 
            
        except Exception as e:
            if '503' in str(e) and attempt < 2:
                print(f"⚠️ Gemini servers busy (503). Retrying in {10 * (attempt + 1)}s...")
                time.sleep(10 * (attempt + 1))
            else:
                print(f"❌ Gemini API Error: {e}")
                break
                
    if not success:
        print("⚠️ Gemini failed after all retries. Writing fallback HTML.")
        with open("portfolio_dashboard.html", "w", encoding="utf-8") as f:
            f.write(fallback_html)

if __name__ == "__main__":
    DATA_PATH = "./HistoricalBhavCopy/NSE"
    SECTOR_MAP = "./nifty500_sectors.csv" 
    try:
        nifty_regime = fetch_nifty_regime()
        raw_df = load_data(DATA_PATH, SECTOR_MAP)
        valid_pool = run_dual_engine_backtest(raw_df, nifty_regime)
        audit_portfolio_with_gemini(valid_pool)
    except Exception as e:
        print(f"Execution failed: {e}")
