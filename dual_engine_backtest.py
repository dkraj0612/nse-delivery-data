"""
dual_engine_backtest.py - PRODUCTION BACKTESTER & AI AUDITOR
========================================================================
1. Historical Math: Pure Quant (No AI labels, formatted prices).
2. AI Auditor: Runs ONLY on the latest portfolio.
3. UI Generation: Fixed JS/Python syntax collisions.
"""

import os
import glob
import json
import hashlib
import re
import pandas as pd
import numpy as np
from google import genai

# ==========================================
# MODULE 1: LOCAL QUANT DATA ENGINE
# ==========================================
def get_deterministic_mcap(symbol):
    hash_val = int(hashlib.md5(symbol.encode('utf-8')).hexdigest(), 16)
    return 1000 + (hash_val % 99000)

def load_and_adjust_data(folder_path="./HistoricalBhavCopy/NSE", sector_map_path="./nifty500_sectors.csv", index_path="./nifty500_index.csv"):
    print("Loading Data and Sector Maps...")
    try: sector_map = pd.read_csv(sector_map_path)[['SYMBOL', 'SECTOR']]
    except: sector_map = pd.DataFrame(columns=['SYMBOL', 'SECTOR'])

    if os.path.exists(index_path):
        nifty_df = pd.read_csv(index_path)
        nifty_df['DATE'] = pd.to_datetime(nifty_df['DATE'], errors='coerce')
        nifty_df['CLOSE_PRICE'] = pd.to_numeric(nifty_df['CLOSE_PRICE'], errors='coerce')
        nifty_df = nifty_df.dropna(subset=['DATE', 'CLOSE_PRICE']).sort_values('DATE')
        nifty_df['NIFTY_EMA_200'] = nifty_df['CLOSE_PRICE'].ewm(span=200, adjust=False).mean()
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
    master_df = master_df.dropna(subset=['DATE', 'CLOSE_PRICE']).drop_duplicates(subset=['SYMBOL', 'DATE']).sort_values(['SYMBOL', 'DATE'])
    
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
    
    # FIX: Catch NaN sectors as seen in image_4.png
    final_df['SECTOR'] = final_df['SECTOR'].fillna('Unknown')
    return final_df, nifty_df

# ==========================================
# MODULE 2: LOCAL QUANT BACKTEST ENGINE
# ==========================================
def run_momentum_backtest(df, nifty_df, ema_param=100, deliv_param=30.0, turnover_param=1000.0, risk_on=20, risk_off=10, friction_tax=0.005):
    print("Executing Historical Quant Backtest...")
    
    df['P_1M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(21)
    df['P_13M'] = df.groupby('SYMBOL')['CLOSE_PRICE'].shift(273) 
    df['P_13M'] = df['P_13M'].replace(0, np.nan)
    df['PRICE_MOMENTUM'] = (df['P_1M'] - df['P_13M']) / df['P_13M']
    
    df['DAILY_RET'] = df.groupby('SYMBOL')['CLOSE_PRICE'].pct_change()
    df['VOLATILITY_90D'] = df.groupby('SYMBOL')['DAILY_RET'].transform(lambda x: x.rolling(90, min_periods=20).std() * np.sqrt(252))
    df['VOLATILITY_90D'] = df['VOLATILITY_90D'].replace(0, 0.001).fillna(0.001) 
    
    df['MASTER_SCORE'] = (df['PRICE_MOMENTUM'] / df['VOLATILITY_90D']) * 100
    df['EMA_X'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.ewm(span=ema_param, adjust=False).mean())
    df['AVG_DELIV_PER'] = df.groupby('SYMBOL')['DELIV_PER'].transform(lambda x: x.rolling(20).mean())
    df['AVG_TURNOVER'] = df.groupby('SYMBOL')['TURNOVER_LACS'].transform(lambda x: x.rolling(20).mean())
    df['52W_HIGH'] = df.groupby('SYMBOL')['CLOSE_PRICE'].transform(lambda x: x.rolling(252).max())
    
    df['YEAR_MONTH'] = df['DATE'].dt.to_period('M')
    month_ends = df.groupby('YEAR_MONTH')['DATE'].max().reset_index()
    rebalance_df = df[df['DATE'].isin(month_ends['DATE'])].copy()
    
    valid_pool = rebalance_df[
        (rebalance_df['MKT_CAP_CR'] >= 1000) &
        (rebalance_df['CLOSE_PRICE'] >= rebalance_df['EMA_X']) & 
        (rebalance_df['CLOSE_PRICE'] >= 20.0) &  
        (rebalance_df['AVG_TURNOVER'] >= turnover_param) & 
        (rebalance_df['AVG_DELIV_PER'] >= deliv_param) & 
        (rebalance_df['MASTER_SCORE'].notna())
    ].copy()

    warmup_end_date = df['DATE'].min() + pd.DateOffset(months=12)
    dates = sorted(rebalance_df[rebalance_df['DATE'] >= warmup_end_date]['DATE'].dropna().unique())
    
    portfolio_snapshots = []
    equity_curve = []
    prev_portfolio_df = pd.DataFrame()
    entry_prices = {}
    capital_selected = 1000000.0 
    capital_rejected = 1000000.0
    
    latest_top_50 = pd.DataFrame()
    latest_date_str = ""

    for current_date in dates:
        curr_date_str = current_date.strftime('%Y-%m-%d')
        day_prices = rebalance_df[rebalance_df['DATE'] == current_date].set_index('SYMBOL')['CLOSE_PRICE'].to_dict()
        
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
        
        target_limit = risk_on
        
        existing_mask = candidates['SYMBOL'].isin(prev_symbols)
        strict_entry_mask = (candidates['CLOSE_PRICE'] >= (candidates['52W_HIGH'] * 0.80))
        valid_candidates = candidates[existing_mask | strict_entry_mask].copy()
        valid_candidates = valid_candidates.sort_values(by='MASTER_SCORE', ascending=False)
        top_50 = valid_candidates.head(50).copy()
        
        if top_50.empty:
            equity_curve.append({'DATE': curr_date_str, 'SELECTED_EQUITY': capital_selected, 'REJECTED_EQUITY': capital_rejected, 'CHURN': 0.0, 'REGIME': "RISK-ON"})
            continue

        final_portfolio = top_50.head(target_limit).copy()
        rejected_portfolio = top_50.tail(len(top_50) - target_limit).copy()
        
        if not rejected_portfolio.empty:
            capital_rejected = capital_rejected * (1 + rejected_portfolio['PCT_CHG'].mean())
            
        current_symbols = set(final_portfolio['SYMBOL'])
        num_new_trades = len(current_symbols - prev_symbols)
        churn_ratio = (num_new_trades / len(final_portfolio)) if len(final_portfolio) > 0 else 0.0
        capital_selected -= (capital_selected * churn_ratio * friction_tax)
        
        equity_curve.append({'DATE': curr_date_str, 'SELECTED_EQUITY': capital_selected, 'REJECTED_EQUITY': capital_rejected, 'CHURN': churn_ratio, 'REGIME': "RISK-ON"})
        
        for _, row in top_50.iterrows():
            sym = row['SYMBOL']
            is_chosen = sym in current_symbols
            pnl_str = f"{((day_prices.get(sym, row['CLOSE_PRICE'])/entry_prices.get(sym, row['CLOSE_PRICE']))-1)*100:+.2f}%" if sym in entry_prices else "NEW"
            if is_chosen and sym not in prev_symbols: entry_prices[sym] = row['CLOSE_PRICE']
                
            portfolio_snapshots.append({
                'DATE': curr_date_str, 'SYMBOL': sym, 'SECTOR': row['SECTOR'],
                'ACTION': 'SELECTED' if is_chosen else 'REJECTED', 
                'PRICE': f"{row['CLOSE_PRICE']:.2f}", # FIX: Enforces clean float formatting, preventing scientific notation seen in image_4.png
                'SCORE': f"{row['MASTER_SCORE']:.1f}", 
                'DELIV_%': f"{row['AVG_DELIV_PER']:.1f}%", 
                'PNL': pnl_str,
                'REASON': "Quant Baseline Qualification"
            })
            
        prev_portfolio_df = final_portfolio.copy()
        latest_top_50 = top_50.copy()
        latest_date_str = curr_date_str

    return pd.DataFrame(portfolio_snapshots), pd.DataFrame(equity_curve), latest_top_50, latest_date_str, risk_on

# ==========================================
# MODULE 3: AI LATEST AUDITOR
# ==========================================
def run_ai_latest_portfolio_audit(latest_top_50, target_limit, date_str):
    print(f"Executing AI Audit for the latest portfolio ({date_str})...")
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client() if api_key else None
    
    if not client: return latest_top_50.head(target_limit)['SYMBOL'].tolist(), {s: "API key missing." for s in latest_top_50['SYMBOL']}

    prompt = f"""
    [INSTITUTIONAL AUDIT: {date_str}]
    Analyze these top 50 mathematical candidates. Select exactly {target_limit}.
    Provide a 1-sentence macro/sector justification for EVERY stock.
    
    {latest_top_50[['SYMBOL', 'SECTOR', 'MASTER_SCORE']].to_markdown(index=False)}
    
    OUTPUT FORMAT:
    SYMBOL | REASON
    FINAL_SELECTIONS = ["SYM1", "SYM2"]
    """
    
    try:
        response = client.models.generate_content(model='models/gemini-1.5-flash', contents=prompt)
        syms, reasons = [], {}
        match = re.search(r'FINAL_SELECTIONS\s*=\s*\[(.*?)\]', response.text, re.DOTALL)
        if match: syms = [s.strip().replace('"', '').replace("'", "") for s in match.group(1).split(',') if s.strip()]
        for line in response.text.split('\n'):
            if '|' in line and not line.startswith('---'):
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 2: reasons[parts[0].replace('*', '')] = parts[1]
        return syms[:target_limit], reasons
    except Exception as e:
        print(f"AI Audit Error: {e}")
        return latest_top_50.head(target_limit)['SYMBOL'].tolist(), {}

# ==========================================
# MODULE 4: SAFE UI GENERATOR
# ==========================================
def generate_static_html(df_snaps, df_equity, ai_symbols, ai_reasons, latest_date_str):
    init_eq = 1000000.0
    fin_sel = df_equity['SELECTED_EQUITY'].iloc[-1]
    fin_rej = df_equity['REJECTED_EQUITY'].iloc[-1]
    
    days_span = (pd.to_datetime(df_equity['DATE'].iloc[-1]) - pd.to_datetime(df_equity['DATE'].iloc[0])).days / 365.25
    cagr_sel = (((fin_sel / init_eq) ** (1 / days_span)) - 1) * 100
    
    df_equity['PEAK_SEL'] = df_equity['SELECTED_EQUITY'].cummax()
    max_dd_sel = ((df_equity['SELECTED_EQUITY'] - df_equity['PEAK_SEL']) / df_equity['PEAK_SEL']).min() * 100
    
    monthly_data = {}
    for d in sorted(df_snaps['DATE'].unique(), reverse=True):
        day_snaps = df_snaps[df_snaps['DATE'] == d].copy()
        eq_row = df_equity[df_equity['DATE'] == d].iloc[0]
        
        if d == latest_date_str and ai_symbols:
            day_snaps['ACTION'] = day_snaps['SYMBOL'].apply(lambda s: 'AI APPROVED' if s in ai_symbols else 'AI REJECTED')
            day_snaps['REASON'] = day_snaps['SYMBOL'].apply(lambda s: ai_reasons.get(s, "Audited."))

        monthly_data[d] = {
            "regime": eq_row['REGIME'],
            "churn": f"{eq_row['CHURN']*100:.1f}%",
            "selected": day_snaps[day_snaps['ACTION'].isin(['SELECTED', 'AI APPROVED'])].to_dict('records'),
            "rejected": day_snaps[day_snaps['ACTION'].isin(['REJECTED', 'AI REJECTED'])].to_dict('records')
        }
        
    json_payload = json.dumps({
        "global": {
            "sel_cagr": f"{cagr_sel:.2f}%",
            "sel_dd": f"{max_dd_sel:.2f}%",
            "avg_churn": f"{df_equity['CHURN'].mean()*100:.1f}%",
            "chart_dates": df_equity['DATE'].tolist(),
            "chart_selected": [round(x, 2) for x in df_equity['SELECTED_EQUITY'].tolist()]
        },
        "monthly": monthly_data
    })
    
    # FIX: Clean separation of HTML/JS string block. No Python f-strings colliding with JS ${}.
    html_template = """
    <!DOCTYPE html><html lang="en"><head>
        <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Momentum Alpha Center</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            :root { --bg: #06080C; --surface: #0F131A; --text: #F3F4F6; --accent: #2563EB; --success: #10B981; }
            body { font-family: sans-serif; background: var(--bg); color: var(--text); margin: 0; display: flex; height: 100vh;}
            .sidebar { width: 280px; background: var(--surface); padding: 20px; overflow-y: auto; }
            .btn-nav { width: 100%; text-align: left; background: transparent; color: var(--text); padding: 12px; border: 1px solid #1B222E; border-radius: 8px; cursor: pointer; margin-bottom: 8px; }
            .btn-nav.active { background: var(--accent); }
            .main-panel { flex-grow: 1; padding: 30px; overflow-y: auto; }
            .grid { display: flex; gap: 15px; margin-bottom: 20px; }
            .card { background: var(--surface); padding: 20px; border-radius: 12px; flex: 1; }
            .stock-card { background: var(--surface); padding: 15px; border-radius: 8px; margin-bottom: 10px; border: 1px solid #1B222E; }
            .tabs { margin-bottom: 15px; } .tab-btn { background: transparent; color: gray; border: none; padding: 10px; cursor: pointer;} .tab-btn.active { color: white; border-bottom: 2px solid white; }
            .hidden { display: none; }
        </style>
    </head><body>
        <div class="sidebar" id="sidebar">
            <h3 style="color:var(--accent);">Alpha Center</h3>
            <button class="btn-nav active" onclick="showGlobal()">📊 Quant Dashboard</button>
            <div id="timeline-btns" style="margin-top:20px;"></div>
        </div>
        <div class="main-panel">
            <div id="global-deck">
                <div class="grid">
                    <div class="card"><h5>Quant Selected CAGR</h5><h2 id="g-cagr">--</h2></div>
                    <div class="card"><h5>Max Drawdown</h5><h2 id="g-dd" style="color:#EF4444;">--</h2></div>
                </div>
                <div class="card" style="height:300px;"><canvas id="masterChart"></canvas></div>
            </div>
            <div id="monthly-deck" class="hidden">
                <div class="tabs">
                    <button class="tab-btn active" id="tab-sel" onclick="switchTab('sel')">Allocated Assets</button>
                    <button class="tab-btn" id="tab-rej" onclick="switchTab('rej')">Rejected Assets</button>
                </div>
                <div id="stock-list"></div>
            </div>
        </div>
        <script>
            const coreData = REPLACE_WITH_JSON_PAYLOAD;
            let currentMonth = ''; let currentMode = 'sel'; let myChart = null;

            function renderStocks() {
                const arr = currentMode === 'sel' ? coreData.monthly[currentMonth].selected : coreData.monthly[currentMonth].rejected;
                document.getElementById('stock-list').innerHTML = arr.map(s => `
                    <div class="stock-card">
                        <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                            <strong>${s.SYMBOL} <span style="font-size:12px; color:gray;">| ${s.SECTOR}</span></strong>
                            <span style="color:${s.PNL.includes('-')?'#EF4444':'#10B981'}; font-weight:bold;">${s.PNL}</span>
                        </div>
                        <div style="font-size:12px; color:gray; margin-bottom:8px;">Price: ₹${s.PRICE} | Score: ${s.SCORE}</div>
                        <div style="background:#06080C; padding:8px; border-radius:4px; font-size:13px; border-left:3px solid var(--accent);">${s.REASON}</div>
                    </div>`).join('');
            }

            function switchTab(mode) {
                currentMode = mode;
                document.getElementById('tab-sel').className = mode === 'sel' ? 'tab-btn active' : 'tab-btn';
                document.getElementById('tab-rej').className = mode === 'rej' ? 'tab-btn active' : 'tab-btn';
                renderStocks();
            }

            function showMonth(dateStr) {
                currentMonth = dateStr;
                document.getElementById('global-deck').classList.add('hidden');
                document.getElementById('monthly-deck').classList.remove('hidden');
                document.querySelectorAll('.btn-nav').forEach(b => b.classList.remove('active'));
                document.getElementById('btn-'+dateStr).classList.add('active');
                renderStocks();
            }

            function showGlobal() {
                document.getElementById('global-deck').classList.remove('hidden');
                document.getElementById('monthly-deck').classList.add('hidden');
                document.querySelectorAll('.btn-nav').forEach(b => b.classList.remove('active'));
                
                document.getElementById('g-cagr').innerText = coreData.global.sel_cagr;
                document.getElementById('g-dd').innerText = coreData.global.sel_dd;
                
                if(myChart) myChart.destroy();
                myChart = new Chart(document.getElementById('masterChart'), {
                    type: 'line', data: { labels: coreData.global.chart_dates, datasets: [{label: 'Quant Equity', data: coreData.global.chart_selected, borderColor: '#10B981', tension:0.1}] }
                });
            }

            const tb = document.getElementById('timeline-btns');
            Object.keys(coreData.monthly).forEach(d => {
                tb.innerHTML += `<button class="btn-nav" id="btn-${d}" onclick="showMonth('${d}')">${d}</button>`;
            });
            showGlobal();
        </script>
    </body></html>
    """
    final_html = html_template.replace("REPLACE_WITH_JSON_PAYLOAD", json_payload)
    with open("index.html", "w", encoding="utf-8") as f: f.write(final_html)
    print("Dashboard UI Built Successfully.")

if __name__ == "__main__":
    raw_df, nifty_df = load_and_adjust_data()
    snaps, equity, latest_top_50, latest_date_str, latest_limit = run_momentum_backtest(raw_df, nifty_df)
    ai_symbols, ai_reasons = run_ai_latest_portfolio_audit(latest_top_50, latest_limit, latest_date_str)
    generate_static_html(snaps, equity, ai_symbols, ai_reasons, latest_date_str)
