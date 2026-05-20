"""
dual_engine_backtest.py
=======================
1. Fetches Nifty 50 from Yahoo Finance (handles multi-index).
2. Cleans dirty NSE CSV headers and merges with sector map.
3. Corporate Action Filter: Detects and excludes stock splits.
4. Allocates 50% capital to Top Sectors, 50% to Lone Wolves.
5. Logs all trades to a CSV and a structured, searchable HTML Data Table.
6. Uses Gemini AI to audit the final portfolio and generate an HTML dashboard.
7. Includes Bulletproof Retry Loop for Gemini API 503 server errors.
"""
import os
import glob
import re
import time
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
    
    rebalance_df['ACCUMULATION_MULT'] = rebalance_df['DELIV_PER_20MA'] / 50.0
    rebalance_df['MASTER_SCORE'] = rebalance_df['PRICE_MOMENTUM'] * rebalance_df['ACCUMULATION_MULT']
    
    rebalance_df = pd.merge(rebalance_df, regime_df, on='DATE', how='left')
    rebalance_df['REGIME_GREEN'] = rebalance_df['REGIME_GREEN'].fillna(True)
    
    valid_pool = rebalance_df[
        (rebalance_df['CLOSE_PRICE'] >= 50.0) & 
        (rebalance_df['AVG_TURNOVER'] >= 2500.0) & 
        (rebalance_df['DELIV_PER_20MA'] >= 35.0) & 
        (rebalance_df['CLOSE_PRICE'] >= (rebalance_df['52W_HIGH'] * 0.80)) & 
        (rebalance_df['MASTER_SCORE'].notna()) &
        (rebalance_df['RECENT_SPLIT'] == 0) & 
        (rebalance_df['SECTOR'].notna()) 
    ].copy()

    dates = sorted(valid_pool['DATE'].unique())
    monthly_records = []
    
    history_records = []
    prev_portfolio_df = pd.DataFrame()
    
    for current_date in dates:
        candidates = valid_pool[valid_pool['DATE'] == current_date].copy()
        regime_status = candidates['REGIME_GREEN'].iloc[0] if not candidates.empty else False
        
        if not regime_status:
            if not prev_portfolio_df.empty:
                for _, row in prev_portfolio_df.iterrows():
                    history_records.append({
                        'DATE': current_date.strftime('%Y-%m-%d'),
                        'SYMBOL': row['SYMBOL'],
                        'ACTION': 'EXIT',
                        'ENGINE': row.get('SOURCE_ENGINE', 'Unknown'),
                        'PRICE': f"₹{row['CLOSE_PRICE']:.2f}",
                        'JUSTIFICATION': 'Systemic Crash: Nifty 50 dropped below 200-day EMA.'
                    })
            prev_portfolio_df = pd.DataFrame()
            monthly_records.append({'DATE': current_date, 'NET_RETURN': 0.004, 'REGIME': 'BEAR (CASH)'})
            continue

        sector_mom = candidates.groupby('SECTOR')['PRICE_MOMENTUM'].mean().reset_index()
        top_3_sectors = sector_mom.sort_values(by='PRICE_MOMENTUM', ascending=False).head(3)['SECTOR'].tolist()
        
        engine_a_candidates = candidates[candidates['SECTOR'].isin(top_3_sectors)]
        engine_a = engine_a_candidates.groupby('SECTOR').head(3).sort_values(by='MASTER_SCORE', ascending=False).head(10).copy()
        engine_a['SOURCE_ENGINE'] = 'Engine A (Sector)'
        
        engine_b_candidates = candidates[~candidates['SYMBOL'].isin(engine_a['SYMBOL'])]
        engine_b = engine_b_candidates.sort_values(by='MASTER_SCORE', ascending=False).head(10).copy()
        engine_b['SOURCE_ENGINE'] = 'Engine B (Lone Wolf)'
        
        final_portfolio = pd.concat([engine_a, engine_b])
        
        current_symbols = set(final_portfolio['SYMBOL'])
        prev_symbols = set(prev_portfolio_df['SYMBOL']) if not prev_portfolio_df.empty else set()
        
        exits = prev_symbols - current_symbols
        for sym in exits:
            justification = "Rank Decay: Momentum dropped below top 10." if sym in candidates['SYMBOL'].values else "Filter Failure: Lost momentum, liquidity, or split detected."
            history_records.append({
                'DATE': current_date.strftime('%Y-%m-%d'),
                'SYMBOL': sym,
                'ACTION': 'EXIT',
                'ENGINE': 'N/A',
                'PRICE': 'N/A',
                'JUSTIFICATION': justification
            })
            
        for _, row in final_portfolio.iterrows():
            sym = row['SYMBOL']
            action = 'HOLD' if sym in prev_symbols else 'ENTRY'
            justification = f"Top Breakout Picked by {row['SOURCE_ENGINE']}" if action == 'ENTRY' else "Maintained Top 10 Rank"
            
            history_records.append({
                'DATE': current_date.strftime('%Y-%m-%d'),
                'SYMBOL': sym,
                'ACTION': action,
                'ENGINE': row['SOURCE_ENGINE'],
                'PRICE': f"₹{row['CLOSE_PRICE']:.2f}",
                'JUSTIFICATION': justification
            })
            
        prev_portfolio_df = final_portfolio.copy()
        
        if not final_portfolio.empty:
            avg_raw_return = final_portfolio['FORWARD_1M_RET'].mean()
            net_monthly_return = avg_raw_return - 0.002
            monthly_records.append({'DATE': current_date, 'NET_RETURN': net_monthly_return, 'REGIME': 'BULL (EQUITY)'})

    # Save to CSV and generate STRUCTURED HTML Table
    if history_records:
        history_df = pd.DataFrame(history_records)
        history_df.to_csv("backtest_portfolio_history.csv", index=False)
        
        # We tell Pandas to assign an ID so Javascript can control it
        html_table = history_df.to_html(index=False, border=0, table_id="historyTable", classes="display")
        
        history_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Quant Fund - Backtest Ledger</title>
            <!-- DataTables CSS for structured UI -->
            <link rel="stylesheet" type="text/css" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css">
            <style>
                body {{ background-color: #121212; color: #e0e0e0; font-family: -apple-system, sans-serif; padding: 20px; }}
                h2 {{ color: #ffffff; margin-bottom: 20px; }}
                .btn {{ display: inline-block; padding: 10px 15px; background-color: #333; color: #fff; text-decoration: none; border-radius: 5px; margin-bottom: 20px; }}
                
                /* Dark Mode fixes for the table */
                .dataTables_wrapper {{ background: #1e1e1e; padding: 15px; border-radius: 8px; }}
                table.dataTable tbody tr {{ background-color: #1e1e1e; color: #e0e0e0; }}
                table.dataTable tbody tr:hover {{ background-color: #2a2a2a; }}
                table.dataTable thead th {{ border-bottom: 1px solid #444; color: #bb86fc; }}
                .dataTables_wrapper .dataTables_length, .dataTables_wrapper .dataTables_filter, .dataTables_wrapper .dataTables_info, .dataTables_wrapper .dataTables_paginate {{ color: #e0e0e0 !important; margin-bottom: 15px; }}
                .dataTables_wrapper input, .dataTables_wrapper select {{ background-color: #333; color: #fff; border: 1px solid #555; border-radius: 4px; padding: 4px; }}
                
                /* Color Badges for Actions */
                .badge-entry {{ background-color: #2e7d32; color: white; padding: 5px 10px; border-radius: 4px; font-weight: bold; font-size: 12px; }}
                .badge-exit {{ background-color: #c62828; color: white; padding: 5px 10px; border-radius: 4px; font-weight: bold; font-size: 12px; }}
                .badge-hold {{ background-color: #555555; color: white; padding: 5px 10px; border-radius: 4px; font-weight: bold; font-size: 12px; }}
            </style>
        </head>
        <body>
            <a href="index.html" class="btn">⬅ Back to Live Dashboard</a>
            <a href="backtest_portfolio_history.csv" class="btn" style="background-color: #1e88e5;">⬇️ Download Raw CSV</a>
            <h2>Master Backtest Ledger</h2>
            
            {html_table}

            <!-- Inject jQuery and DataTables JavaScript -->
            <script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
            <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
            <script>
                $(document).ready(function() {{
                    $('#historyTable').DataTable({{
                        "pageLength": 50,
                        "order": [[ 0, "desc" ]], // Auto-sort Date by Newest First
                        "createdRow": function(row, data, dataIndex) {{
                            // Apply Color Badges to the Action column (Index 2)
                            var action = data[2]; 
                            if (action === 'ENTRY') {{
                                $('td:eq(2)', row).html('<span class="badge-entry">ENTRY</span>');
                            }} else if (action === 'EXIT') {{
                                $('td:eq(2)', row).html('<span class="badge-exit">EXIT</span>');
                            }} else if (action === 'HOLD') {{
                                $('td:eq(2)', row).html('<span class="badge-hold">HOLD</span>');
                            }}
                        }}
                    }});
                }});
            </script>
        </body>
        </html>
        """
        with open("history.html", "w", encoding="utf-8") as f:
            f.write(history_html)

    perf_df = pd.DataFrame(monthly_records).dropna()
    if not perf_df.empty:
        perf_df['EQUITY_CURVE'] = (1 + perf_df['NET_RETURN']).cumprod()
        total_months = len(perf_df)
        cagr = ((perf_df['EQUITY_CURVE'].iloc[-1] ** (12 / total_months)) - 1) * 100
        perf_df['PEAK'] = perf_df['EQUITY_CURVE'].cummax()
        perf_df['DRAWDOWN'] = (perf_df['EQUITY_CURVE'] - perf_df['PEAK']) / perf_df['PEAK']
        max_dd = perf_df['DRAWDOWN'].min() * 100
        
        print("\n" + "="*50)
        print("🚀 ADJUSTED DUAL-ENGINE STRATEGY RESULTS")
        print("="*50)
        print(f"Months Tested       : {total_months}")
        print(f"Realized CAGR       : {cagr:.2f}%")
        print(f"Maximum Drawdown    : {max_dd:.2f}%")
        print("="*50)
        
    return valid_pool 

def audit_portfolio_with_gemini(valid_pool):
    if valid_pool.empty:
        print("No valid portfolio data generated to audit.")
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
    
    print("\n" + "="*50)
    print(f"🤖 GEMINI AI RISK AUDIT & DASHBOARD GEN: {latest_date.strftime('%Y-%m-%d')}")
    print("="*50)
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("⚠️ GEMINI_API_KEY secret not found in GitHub Actions. Skipping AI audit.")
        return
        
    table_str = final_live_portfolio[['SYMBOL', 'SECTOR', 'SOURCE_ENGINE', 'CLOSE_PRICE', 'AVG_TURNOVER', 'DELIV_PER_20MA', 'MASTER_SCORE']].to_markdown(index=False)
    
    prompt = f"""
    You are the Chief Risk Officer and Lead UI Developer for an Indian quant fund. 
    Our Dual-Engine algorithm selected these 20 stocks on {latest_date.strftime('%Y-%m-%d')}.
    
    Data:
    {table_str}
    
    Perform two tasks:
    
    PART 1: RISK AUDIT
    Provide a brief safety audit. Confirm that sector concentration is now managed and verify the overall liquidity profile of the portfolio. Explicitly list any remaining outlier stocks to manually reject. Use markdown.
    
    PART 2: HTML DASHBOARD
    Generate a complete, single-file HTML document (with embedded CSS) that creates a beautiful, dark-mode, mobile-responsive dashboard displaying these 20 stocks. 
    - Group them visually by Engine A vs Engine B.
    - Highlight the Ticker, Sector, Price, and Master Score.
    - Include a prominent button at the top that links to 'history.html' with the text 'View Full Backtest Ledger'.
    - You MUST wrap the HTML code inside a ```html codeblock.
    """
    
    client = genai.Client()
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )
            
            text_output = response.text
            
            html_match = re.search(r"""
```html\s*(.*?)\s*```""", text_output, re.DOTALL)
            if html_match:
                html_content = html_match.group(1)
                with open("portfolio_dashboard.html", "w", encoding="utf-8") as f:
                    f.write(html_content)
                print("✅ HTML Dashboard successfully generated and saved as 'portfolio_dashboard.html'")
                
                text_output = re.sub(r"""```html\s*(.*?)\s*
```""", '\n[HTML Saved to File]', text_output, flags=re.DOTALL)
                
            print("\n" + text_output)
            break # Success, exit the retry loop
            
        except Exception as e:
            if '503' in str(e) and attempt < max_retries - 1:
                wait_time = 10 * (attempt + 1)
                print(f"⚠️ Gemini servers busy (503). Retrying in {wait_time} seconds (Attempt {attempt + 2}/{max_retries})...")
                time.sleep(wait_time)
            else:
                print(f"❌ Gemini API Error: {e}")
                break

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
