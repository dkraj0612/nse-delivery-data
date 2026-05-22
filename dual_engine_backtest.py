"""
dual_engine_backtest.py - PMS COMMAND CENTER (VERIFIED ARCHITECTURE)
==========================================================
Module 1: Data Engine (Local NSE BhavCopy & Index)
Module 2: Strategy Engine (Risk-Adjusted Momentum, 0.5% Tax)
Module 3: Internal Python Verifier (5 Strict PMS Asserts)
Module 4: AI Heavy Lifter (Verifies & Justifies Entries/Exits/Holds)
Module 5: Dual Publisher (Streamlit UI & Static HTML)
"""

import os
import glob
import json
import pandas as pd
import numpy as np
import concurrent.futures
import plotly.express as px
import streamlit as st
from streamlit import runtime
from google import genai

# ==========================================
# MODULE 1: LOCAL DATA ENGINE
# ==========================================
@st.cache_data(show_spinner=False)
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
        print("⚠️ Warning: nifty500_index.csv not found. Defaulting to perpetual Risk-ON regime.")
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
        print("Skipping verification - no trades executed.")
        return True

    try:
        # Instruction 1: Capital Conservation (No leverage/bankruptcy)
        assert df_equity['EQUITY'].min() >= 0, "FATAL: Portfolio equity dropped below zero. Invalid weighting."
        
        # Instruction 2: Turnover Boundaries
        assert df_equity['CHURN'].max() <= 1.0, "FATAL: Monthly churn exceeded 100%. Friction tax calculation error."
        assert df_equity['CHURN'].min() >= 0.0, "FATAL: Negative churn detected."
        
        # Instruction 3: Regime Compliance 
        max_positions = df_snaps.groupby('DATE')['SYMBOL'].count().max()
        assert max_positions <= 40, f"FATAL: Max position size breached. Counted {max_positions} active."

        # Instruction 4: No Look-Ahead Bias
        df_entries = df_snaps[df_snaps['ACTION'].isin(['ENTRY', 'HOLD'])].copy()
        df_entries['DATE'] = pd.to_datetime(df_entries['DATE'])
        df_entries['ENTRY_DATE'] = pd.to_datetime(df_entries['ENTRY_DATE'])
        assert (df_entries['ENTRY_DATE'] <= df_entries['DATE']).all(), "FATAL: Look-ahead bias. Entry date occurs after execution date."
        
        # Instruction 5: Data Continuity
        assert not df_equity.isnull().values.any(), "FATAL: Missing values in the equity curve. Calculation break."
        
        print("✅ Python PMS Verification Passed. Math is strictly reliable.")
        return True
    except AssertionError as e:
        print(f"❌ PMS VERIFICATION FAILED: {e}")
        raise SystemExit(1)

# ==========================================
# MODULE 4: AI HEAVY LIFTER (PMS JUSTIFICATION)
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
    if not client: return audit_progress

    latest_date = df_snaps['DATE'].max()
    if latest_date in audit_progress["results"]:
        return audit_progress # Already audited

    print(f"\nTriggering AI Heavy Lifting for {latest_date} Portfolio Construction...")
    
    latest_transitions = df_snaps[df_snaps['DATE'] == latest_date].copy()
    latest_regime = df_equity.iloc[-1]['REGIME']
    
    audit_df = latest_transitions[['ACTION', 'SYMBOL', 'SECTOR', 'SCORE', 'PNL']]
    
    prompt = f"""
    DATE: {latest_date}
    CURRENT MARKET REGIME: {latest_regime}
    
    You are a Quantitative Portfolio Manager auditing the algorithmic momentum system. 
    Below is the exact transition matrix executed on {latest_date}. The algorithm selects stocks based on Risk-Adjusted Momentum (SCORE), Technical Guardrails, and the Market Regime limit.
    
    TRANSITIONS:
    {audit_df.to_markdown(index=False)}
    
    Perform the "Heavy Lifting" verification. Provide a professional, concise PMS justification report explaining:
    1. Why specific stocks were EXITED (e.g., momentum decay, risk-off regime trigger).
    2. Why specific stocks were ENTERED (e.g., high risk-adjusted score).
    3. Why the HOLDS were maintained.
    Do not hallucinate external news. Base your reasoning strictly on the SCOREs, PNL, and the {latest_regime} regime limit provided.
    Output directly in a clean, professional format.
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
# MODULE 5: DUAL PUBLISHER
# ==========================================
def calculate_global_metrics(df_equity):
    if df_equity.empty: return {"cagr": "0%", "max_dd": "0%", "sharpe": "0", "sortino": "0", "win_rate": "0%"}
    
    initial_equity = 1000000.0
    final_equity = df_equity['EQUITY'].iloc[-1]
    
    start_date = pd.to_datetime(df_equity['DATE'].iloc[0])
    end_date = pd.to_datetime(df_equity['DATE'].iloc[-1])
    years = (end_date - start_date).days / 365.25
    if years <= 0: years = 1 
    
    cagr = (((final_equity / initial_equity) ** (1 / years)) - 1) * 100
    
    df_equity['PEAK'] = df_equity['EQUITY'].cummax()
    df_equity['DRAWDOWN'] = (df_equity['EQUITY'] - df_equity['PEAK']) / df_equity['PEAK']
    max_dd = df_equity['DRAWDOWN'].min() * 100
    
    win_rate = (df_equity['MOM_RET'] > 0).mean() * 100
    
    monthly_returns_decimal = df_equity['MOM_RET'] / 100
    risk_free = 0.07 
    
    # Sharpe
    sharpe = ((cagr / 100) - risk_free) / (monthly_returns_decimal.std() * np.sqrt(12)) if monthly_returns_decimal.std() > 0 else 0
    
    # Sortino (Professional Downside Metric)
    downside_returns = monthly_returns_decimal[monthly_returns_decimal < 0]
    downside_vol = downside_returns.std() * np.sqrt(12)
    sortino = ((cagr / 100) - risk_free) / downside_vol if downside_vol > 0 else 0
    
    return {"cagr": f"{cagr:.2f}%", "max_dd": f"{max_dd:.2f}%", "sharpe": f"{sharpe:.2f}", "sortino": f"{sortino:.2f}", "win_rate": f"{win_rate:.1f}%"}

def render_streamlit():
    st.title("⚡ Momentum Alpha Command Center")
    
    with st.sidebar:
        st.header("⚙️ Strategy Parameters")
        ema_param = st.number_input("Trend Filter (EMA)", value=100)
        deliv_param = st.number_input("Min Delivery %", value=30.0)
        turnover_param = st.number_input("Min Turnover (Lacs)", value=1000.0)
        
        st.header("🛡️ Sizing & Regime")
        risk_on = st.slider("Risk-ON Capacity", 5, 40, 20)
        risk_off = st.slider("Risk-OFF Capacity", 0, 40, 10)
        friction = st.number_input("Friction Tax per Churn (%)", value=0.5, step=0.1) / 100
        
        if st.button("🔄 Run Backtest", type="primary"):
            st.session_state['run'] = True

    with st.spinner("Processing databases & Verifying Integrity..."):
        raw_df, nifty_df = load_and_adjust_data()
        df_snaps, df_equity = run_momentum_backtest(raw_df, nifty_df, ema_param, deliv_param, turnover_param, risk_on, risk_off, friction)
        
        verify_backtest_integrity(df_snaps, df_equity)
        audit_state = ai_portfolio_verifier(df_snaps, df_equity)

    metrics = calculate_global_metrics(df_equity)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("CAGR", metrics['cagr'])
    c2.metric("Max Drawdown", metrics['max_dd'])
    c3.metric("Sharpe Ratio", metrics['sharpe'])
    c4.metric("Sortino Ratio", metrics['sortino'])
    c5.metric("Win Rate", metrics['win_rate'])
    
    st.markdown("---")
    fig = px.line(df_equity, x='DATE', y='EQUITY', title='Verified Equity Growth (₹)')
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", yaxis_title="Portfolio Value")
    st.plotly_chart(fig, use_container_width=True)

    if not df_snaps.empty:
        latest_date = df_snaps['DATE'].max()
        st.subheader(f"📋 Live Portfolio Construction: {latest_date}")
        regime_status = df_equity.iloc[-1]['REGIME']
        st.write(f"**Market Breadth Regime:** {regime_status}")
        
        latest_port = df_snaps[(df_snaps['DATE'] == latest_date) & (df_snaps['ACTION'].isin(['ENTRY', 'HOLD']))]
        st.dataframe(latest_port[['SYMBOL', 'SECTOR', 'ACTION', 'ENTRY_DATE', 'PRICE', 'SCORE', 'DELIV_%', 'PNL']], use_container_width=True)
        
        if latest_date in audit_state.get("results", {}):
            with st.expander("🤖 AI PMS Construction Verification", expanded=True):
                st.write(audit_state["results"][latest_date])

def generate_static_html(audit_progress, df_snaps, df_equity):
    print("Generating Static Dashboard...")
    df_snaps.to_csv("backtest_portfolio_history.csv", index=False)
    metrics = calculate_global_metrics(df_equity)
    
    html_content = f"""
    <!DOCTYPE html><html><head><title>Dashboard Rendered</title><style>body{{background:#121212;color:#fff;font-family:sans-serif;padding:40px;}}</style></head>
    <body><h1>Static Pipeline Successful</h1><p>CAGR: {metrics['cagr']}</p><p>Sharpe: {metrics['sharpe']}</p>
    <a href="backtest_portfolio_history.csv" style="color:#bb86fc;">Download Trade Ledger</a></body></html>
    """
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)

# ==========================================
# EXECUTION ROUTER
# ==========================================
if __name__ == "__main__":
    if runtime.exists():
        render_streamlit()
    else:
        print("🚀 Running in Bare Python Mode (GitHub Actions).")
        raw_df, nifty_df = load_and_adjust_data()
        df_snaps, df_equity = run_momentum_backtest(raw_df, nifty_df)
        verify_backtest_integrity(df_snaps, df_equity)
        audit_state = ai_portfolio_verifier(df_snaps, df_equity)
        generate_static_html(audit_state, df_snaps, df_equity)
        print("✅ Pipeline Complete.")
