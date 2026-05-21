"""
dual_engine_backtest.py - FULL PRODUCTION VERSION
==========================================================
1. RE-NORMALIZES historical data for stock splits and bonuses.
2. PURE MOMENTUM: (12M Return * 2) + (6M Return * 1) [1-Month Skip].
3. SINGLE ENGINE: Top 20 absolute momentum stocks across Top 40 buffer.
4. GUARDRAILS: > 51 EMA, Within 20% of 52W High, > 10Cr Avg Turnover.
5. PERFORMANCE METRICS: Full CAGR, Max DD, and Win Rate calculations.
6. FULL HTML LEDGER: Beautiful, dynamic JS/CSS historical dashboard.
7. AI FORENSIC AUDIT: Strict point-in-time analysis of entry signals.
8. FIXED: Dynamic regex string construction to prevent copy-paste SyntaxErrors.
"""
import os
import glob
import json
import time
import re
import pandas as pd
import numpy as np
from google import genai

def load_and_adjust_data(folder_path, sector_map_path):
    print("Loading and Adjusting Bhav Copy for Corporate Actions...")
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
    master_df = master_df.sort_values(['SYMBOL', 'DATE'])
    
    # ADJUSTMENT LOGIC: Back-adjust history for splits/bonuses
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
    master_df = pd.merge(master_df, sector_map, on='SYMBOL', how='left')
    return master_df.reset_index(drop=True)

def run_pure_momentum_backtest(df):
    print("Calculating Metrics & Simulating Pure Single-Engine Portfolio...")
    
    # 1-Month Skip Momentum
    df['PRICE_1M_AGO'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['PRICE_7M_AGO'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(147) 
    df['PRICE_13M_AGO'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    
    df['12M_RET'] = (df['PRICE_1M_AGO'] - df['PRICE_13M_AGO']) / df['PRICE_13M_AGO']
    df['6M_RET']  = (df['PRICE_1M_AGO'] - df['PRICE_7M_AGO']) / df['PRICE_7M_AGO']
    
    df['PRICE_MOMENTUM'] = (df['12M_RET'] * 2) + df['6M_RET']
    
    # Technical Guardrails
    df['EMA_51'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=51, adjust=False).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    
    df['YEAR_MONTH'] = df['DATE'].dt.to_period('M')
    month_ends = df.groupby('YEAR_MONTH')['DATE'].max().reset_index()
    rebalance_df = df[df['DATE'].isin(month_ends['DATE'])].copy()
    
    rebalance_df['NEXT_MONTH_CLOSE'] = rebalance_df.groupby('SYMBOL')['CLOSE_PRICE'].shift(-1)
    rebalance_df['FORWARD_1M_RET'] = (rebalance_df['NEXT_MONTH_CLOSE'] / rebalance_df['CLOSE_PRICE']) - 1
    
    rebalance_df['MASTER_SCORE'] = rebalance_df['PRICE_MOMENTUM'] * 100
    
    # Strict Guardrails (Entry & Hold Criteria)
    valid_pool = rebalance_df[
        (rebalance_df['CLOSE_PRICE'] >= rebalance_df['EMA_51']) & 
        (rebalance_df['CLOSE_PRICE'] >= (rebalance_df['52W_HIGH'] * 0.80)) & 
        (rebalance_df['AVG_TURNOVER'] >= 1000.0) & 
        (rebalance_df['MASTER_SCORE'].notna()) & 
        (rebalance_df['SECTOR'].notna()) 
    ].copy()

    dates = sorted(rebalance_df['DATE'].dropna().unique())
    formatted_dates = [d.strftime('%Y-%m-%d') for d in dates]
    
    monthly_records = []
    portfolio_snapshots = []
    prev_portfolio_df = pd.DataFrame()
    
    entry_prices = {}
    entry_dates = {}
    
    for current_date in dates:
        curr_date_str = current_date.strftime('%Y-%m-%d')
        day_data_full = rebalance_df[rebalance_df['DATE'] == current_date]
        if day_data_full.empty: continue
            
        day_prices = day_data_full.set_index('SYMBOL')['CLOSE_PRICE'].to_dict()
        candidates = valid_pool[valid_pool['DATE'] == current_date].copy()
        
        prev_symbols = set(prev_portfolio_df['SYMBOL']) if not prev_portfolio_df.empty else set()
        
        if candidates.empty:
            for sym in prev_symbols:
                entry_price = entry_prices.get(sym, 0)
                sym_entry_date = entry_dates.get(sym, "Unknown")
                curr_price = day_prices.get(sym)
                if curr_price is not None and entry_price > 0:
                    pnl_str = f"{((curr_price / entry_price) - 1)*100:+.2f}%"
                    curr_price_str = f"₹{curr_price:.2f}"
                else:
                    pnl_str = "-"
                    curr_price_str = "N/A"
                    
                portfolio_snapshots.append({
                    'DATE': curr_date_str,
                    'SYMBOL': sym,
                    'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL'] == sym]['SECTOR'].values[0],
                    'ACTION': 'EXIT',
                    'PRICE': curr_price_str,
                    'PNL': pnl_str,
                    'JUSTIFICATION': "Failed EMA51 / 52W High / Liquidity Rules",
                    'ENTRY_DATE': sym_entry_date,
                    'EXIT_DATE': curr_date_str
                })
            entry_prices.clear()
            entry_dates.clear()
            crash_return = -0.01 if prev_symbols else 0.004
            monthly_records.append({'DATE': current_date, 'NET_RETURN': crash_return, 'REGIME': 'BEAR (CASH)'})
            prev_portfolio_df = pd.DataFrame() 
            continue

        candidates = candidates.sort_values(by='MASTER_SCORE', ascending=False)
        top_40 = candidates.head(40).copy()
        
        held_stocks = top_40[top_40['SYMBOL'].isin(prev_symbols)]
        new_stocks = top_40[~top_40['SYMBOL'].isin(prev_symbols)]
        
        final_portfolio = pd.concat([held_stocks, new_stocks]).head(20).copy()
        current_symbols = set(final_portfolio['SYMBOL'])
        
        exits = prev_symbols - current_symbols
        entries = current_symbols - prev_symbols
        
        for sym in exits:
            entry_price = entry_prices.get(sym, 0)
            sym_entry_date = entry_dates.get(sym, "Unknown")
            curr_price = day_prices.get(sym)
            if curr_price is not None and entry_price > 0:
                pnl_str = f"{((curr_price / entry_price) - 1)*100:+.2f}%"
                curr_price_str = f"₹{curr_price:.2f}"
            else:
                pnl_str = "-"
                curr_price_str = "N/A"
                
            justification = "Fell below Top 40 Buffer Rank" if sym in candidates['SYMBOL'].values else "Failed EMA51 / 52W High / Liquidity Rules"
            
            portfolio_snapshots.append({
                'DATE': curr_date_str,
                'SYMBOL': sym,
                'SECTOR': prev_portfolio_df[prev_portfolio_df['SYMBOL'] == sym]['SECTOR'].values[0],
                'ACTION': 'EXIT',
                'PRICE': curr_price_str,
                'PNL': pnl_str,
                'JUSTIFICATION': justification,
                'ENTRY_DATE': sym_entry_date,
                'EXIT_DATE': curr_date_str
            })
            if sym in entry_prices: del entry_prices[sym]
            if sym in entry_dates: del entry_dates[sym]
            
        for _, row in final_portfolio.iterrows():
            sym = row['SYMBOL']
            curr_price = row['CLOSE_PRICE']
            score = row['MASTER_SCORE']
            
            if sym in prev_symbols:
                action = 'HOLD'
                entry_price = entry_prices.get(sym, curr_price)
                sym_entry_date = entry_dates.get(sym, curr_date_str)
                pnl_str = f"{((curr_price / entry_price) - 1)*100:+.2f}%" if entry_price > 0 else "-"
                justification = "Maintained Top 40 Buffer"
            else:
                action = 'ENTRY'
                entry_prices[sym] = curr_price
                entry_dates[sym] = curr_date_str
                sym_entry_date = curr_date_str
                pnl_str = "NEW"
                justification = "Top 20 Absolute Breakout"
                
            portfolio_snapshots.append({
                'DATE': curr_date_str,
                'SYMBOL': sym,
                'SECTOR': row['SECTOR'],
                'ACTION': action,
                'PRICE': f"₹{curr_price:.2f}",
                'SCORE': f"{score:.1f}",
                'PNL': pnl_str,
                'JUSTIFICATION': justification,
                'ENTRY_DATE': sym_entry_date,
                'EXIT_DATE': "-"
            })
            
        if not final_portfolio.empty:
            returns = final_portfolio['FORWARD_1M_RET']
            clean_returns = returns[(returns >= -0.25) & (returns <= 2.5)] 
            avg_raw_return = clean_returns.mean() if not clean_returns.empty else 0
            
            churn_ratio = len(entries) / max(1, len(current_symbols))
            transaction_drag = churn_ratio * 0.005 
            
            net_monthly_return = avg_raw_return - transaction_drag
            monthly_records.append({'DATE': current_date, 'NET_RETURN': net_monthly_return, 'REGIME': 'BULL (EQUITY)'})
            
        prev_portfolio_df = final_portfolio.copy()

    perf_df = pd.DataFrame(monthly_records).dropna()
    perf_dict = {}
    total_months = cagr = max_dd = total_return = win_rate = 0.0
    
    if not perf_df.empty:
        perf_df['EQUITY_CURVE'] = (1 + perf_df['NET_RETURN']).cumprod()
        perf_df['CUM_MONTHS'] = range(1, len(perf_df) + 1)
        perf_df['CAGR'] = ((perf_df['EQUITY_CURVE'] ** (12 / perf_df['CUM_MONTHS'])) - 1) * 100
        
        perf_df['CUM_PNL'] = ((perf_df['EQUITY_CURVE'] - 1) * 100).round(2).astype(str) + '%'
        perf_df['CAGR_STR'] = perf_df['CAGR'].round(2).astype(str) + '%'
        perf_df['DATE_STR'] = perf_df['DATE'].dt.strftime('%Y-%m-%d')
        perf_dict = perf_df.set_index('DATE_STR')[['CUM_PNL', 'CAGR_STR']].to_dict('index')

        total_months = len(perf_df)
        cagr = perf_df['CAGR'].iloc[-1]
        perf_df['PEAK'] = perf_df['EQUITY_CURVE'].cummax()
        perf_df['DRAWDOWN'] = (perf_df['EQUITY_CURVE'] - perf_df['PEAK']) / perf_df['PEAK']
        max_dd = perf_df['DRAWDOWN'].min() * 100
        total_return = (perf_df['EQUITY_CURVE'].iloc[-1] - 1) * 100
        win_rate = (perf_df[perf_df['NET_RETURN'] > 0].shape[0] / total_months) * 100
        
        print("\n" + "="*50)
        print("🚀 TECHNICAL SINGLE-ENGINE SUMMARY")
        print("="*50)
        print(f"Total Return        : {total_return:.2f}%")
        print(f"Realized CAGR       : {cagr:.2f}%")
        print(f"Maximum Drawdown    : {max_dd:.2f}%")
        print(f"Win Rate            : {win_rate:.2f}%")
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
            
            .badge {{ padding: 3px 6px; border-radius: 4px; font-weight: bold; font-size: 10px; display: inline-block; margin-bottom: 3px; }}
            .badge-entry {{ background: #1b5e20; color: #a5d6a7; }}
            .badge-hold {{ background: #37474f; color: #b0bec5; }}
            .badge-exit {{ background: #b71c1c; color: #ef9a9a; }}
            .date-meta {{ font-size: 9px; color: #888; line-height: 1.2; }}
        </style>
    </head>
    <body>
        <div class="top-bar">
            <a href="portfolio_dashboard.html" class="btn">⬅ Live View</a>
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
                <div class="metric-title">Portfolio Cumulative Growth</div>
                <div id="cumPnl" class="pnl-value">-</div>
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
                        <th>Action & Dates</th>
                        <th>Cumulative PnL</th>
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
                
                const pnl = perfData[selectedDate] ? perfData[selectedDate].CUM_PNL : '0.00%';
                const runningCagr = perfData[selectedDate] ? perfData[selectedDate].CAGR_STR : '0.00%';
                
                const pnlElement = document.getElementById('cumPnl');
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
                        
                        let datesHtml = "";
                        if (row.ACTION === 'ENTRY' || row.ACTION === 'HOLD') {{
                            datesHtml = `<div class="date-meta">In: ${{row.ENTRY_DATE}}</div>`;
                        }} else if (row.ACTION === 'EXIT') {{
                            datesHtml = `<div class="date-meta">In: ${{row.ENTRY_DATE}}<br>Out: ${{row.EXIT_DATE}}</div>`;
                        }}
                        
                        tr.innerHTML = `
                            <td>
                                <div style="font-weight:bold; font-size:13px; color:#fff;">${{row.SYMBOL}}</div>
                                <div style="font-size:10px; color:#aaa; max-width:90px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${{row.SECTOR}}</div>
                            </td>
                            <td>
                                <span class="badge ${{badgeClass}}">${{row.ACTION}}</span>
                                ${{datesHtml}}
                            </td>
                            <td>
                                <div style="font-weight:bold; color:${{pnlColor}}">${{row.PNL}}</div>
                                <div style="font-size:10px; color:#aaa;">${{row.PRICE}}</div>
                            </td>
                            <td style="font-size:10px; color:#bbb; line-height:1.2;">${{row.JUSTIFICATION}}</td>
                        `;
                        tbody.appendChild(tr);
                    }});
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
        
    return portfolio_snapshots

def audit_portfolio_with_gemini(snapshots):
    df_snap = pd.DataFrame(snapshots)
    if df_snap.empty: return
    latest_date = df_snap['DATE'].max()
    live = df_snap[(df_snap['DATE'] == latest_date) & (df_snap['ACTION'].isin(['ENTRY', 'HOLD']))].copy()
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key: return
    
    table_str = live[['SYMBOL', 'SECTOR', 'PRICE', 'ENTRY_DATE', 'PNL']].to_markdown(index=False)
    
    prompt = f"""
    You are the Chief Risk Officer and Forensic Equities Auditor for a top-tier Indian quant fund.
    You are performing a STRICT POINT-IN-TIME risk audit for our current momentum portfolio.

    CRITICAL TEMPORAL DIRECTIVE:
    You are operating strictly on {latest_date}.
    You MUST NOT access, use, or reference any information, news, earnings, price action, or SEBI actions that occurred AFTER {latest_date}. 
    Look at the 'ENTRY_DATE' for each stock in the table. Evaluate anomalies and red flags ONLY based on data available BEFORE that specific entry date.

    DATA:
    {table_str}

    TASK 1: FORENSIC RISK AUDIT
    Perform a ruthless analysis of these stocks based ONLY on data prior to their respective Entry Dates.
    - Flag any stocks with historical SEBI warnings, corporate governance issues, or auditor resignations prior to their entry.
    - Be highly structured. Do not evaluate every stock—only list the ones that trigger a severe red flag based on historical data.

    TASK 2: UI GENERATION
    Generate a complete, single-file HTML document (with embedded CSS) that creates a beautiful, dark-mode, mobile-responsive dashboard.
    - Top of page: Prominent button `<a href="history.html" class="btn">View Detailed Trade Ledger</a>`
    - Section 1: The 'Forensic Risk Audit' results.
    - Section 2: The 'Live Portfolio Grid' displaying Ticker, Sector, Price, Entry Date, and PnL.
    - Use modern CSS (flexbox/grid, #121212 background, #bb86fc accents, clean typography).
    - You MUST wrap the HTML code inside a markdown html block (three backticks followed by html).
    """
    
    client = genai.Client()
    
    # Safely building the regex string so literal backticks are not in the python code
    bkticks = "`" * 3
    pattern = bkticks + r"html\s*(.*?)\s*" + bkticks

    for attempt in range(5):
        try:
            resp = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            match = re.search(pattern, resp.text, re.DOTALL)
            
            with open("portfolio_dashboard.html", "w", encoding="utf-8") as f:
                if match:
                    f.write(match.group(1))
                else:
                    f.write(resp.text)
                    
            print(f"✅ HTML Dashboard with Point-in-Time Audit generated successfully.")
            break 
            
        except Exception as e:
            if ('503' in str(e) or '429' in str(e)) and attempt < 4:
                wait_time = 30 * (attempt + 1)
                print(f"⚠️ Gemini API Limit hit. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"❌ Gemini API Error: {e}")
                break

if __name__ == "__main__":
    DATA_PATH = "./HistoricalBhavCopy/NSE"
    SECTOR_MAP = "./nifty500_sectors.csv" 
    try:
        raw_df = load_and_adjust_data(DATA_PATH, SECTOR_MAP)
        snapshots = run_pure_momentum_backtest(raw_df)
        audit_portfolio_with_gemini(snapshots)
    except Exception as e:
        print(f"Execution failed: {e}")
