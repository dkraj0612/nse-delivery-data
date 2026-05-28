import os
import json
import time
import random
from datetime import datetime
from dateutil.relativedelta import relativedelta

# Updated imports for the new Google GenAI SDK
from google import genai
from google.genai import types

# ========================= CONFIGURATION =========================
# Automatically uses the GEMINI_API_KEY environment variable
client = genai.Client()
MODEL_ID = 'gemini-2.5-pro'

OUTPUT_DIR = "reports"
PROGRESS_FILE = "progress.json"
BATCH_SIZE = 8                    # Number of months per batch
SLEEP_BETWEEN_BATCHES_MINUTES = 5 # 5 minutes as requested

os.makedirs(OUTPUT_DIR, exist_ok=True)

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed": [], "last_run": None}

def save_progress(completed_dates):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "completed": completed_dates,
            "last_run": datetime.now().isoformat()
        }, f, indent=2)

def generate_analysis(cutoff_date_str: str, max_retries=5):
    with open("prompt_template.txt", "r", encoding="utf-8") as f:
        prompt = f.read().replace("{CUTOFF_DATE}", cutoff_date_str)

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=15000,
                    response_mime_type="application/json"
                )
            )
            text = response.text.strip()
            
            # Safely clean markdown code blocks without breaking syntax parsers
            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
                
            if text.endswith("```"):
                text = text[:-3]
                
            text = text.strip()
            return json.loads(text)
            
        except Exception as e:
            error_str = str(e).lower()
            if any(k in error_str for k in ["rate limit", "429", "quota", "resource exhausted"]):
                wait = 45 * (attempt + 1)
                print(f"â ï¸ Rate limit â Waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"â ï¸ Unexpected API Error: {e}")
                break
    return {"cutoff_date": cutoff_date_str, "error": "Failed"}

def get_all_dates(years_back=5):
    end_date = datetime.now()
    start_date = end_date - relativedelta(years=years_back)
    dates = []
    current = start_date
    while current <= end_date:
        last_day = (current + relativedelta(months=1, days=-1)).strftime("%d-%b-%Y")
        dates.append(last_day)
        current += relativedelta(months=1)
    return dates

# ========================= DASHBOARD GENERATOR =========================
def generate_dashboard():
    print("\nð Compiling results and generating Institutional Dashboard...")
    
    compiled_data = {}
    if os.path.exists(OUTPUT_DIR):
        for filename in os.listdir(OUTPUT_DIR):
            if filename.endswith(".json") and filename != PROGRESS_FILE:
                date_key = filename.replace("_", "-").replace(".json", "")
                filepath = os.path.join(OUTPUT_DIR, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        compiled_data[date_key] = json.load(f)
                except Exception as e:
                    print(f"  â ï¸ Error loading {filename}: {e}")

    if not compiled_data:
        print("  â ï¸ No data found to generate dashboard.")
        return

    json_data_str = json.dumps(compiled_data)

    html_template = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Institutional Quant Dashboard</title>
    
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    fontFamily: { sans: ['Inter', 'sans-serif'], mono: ['JetBrains Mono', 'monospace'] },
                    colors: { dark: { bg: '#0B0F19', card: '#111827', border: '#1F2937', hover: '#1E293B' }, pos: '#10B981', neg: '#EF4444' }
                }
            }
        }
    </script>
    
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    
    <style>
        body { background-color: #0B0F19; color: #E5E7EB; }
        .glass-card { background: rgba(17, 24, 39, 0.7); backdrop-filter: blur(12px); border: 1px solid #1F2937; border-radius: 12px; }
        .num-font { font-family: 'JetBrains Mono', monospace; }
        .text-pos { color: #10B981; } .text-neg { color: #EF4444; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: #0B0F19; }
        ::-webkit-scrollbar-thumb { background: #374151; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #4B5563; }
        .nav-item { transition: all 0.2s ease; border-left: 3px solid transparent; }
        .nav-active { background: linear-gradient(90deg, rgba(30,58,138,0.5) 0%, rgba(17,24,39,0) 100%); border-left: 3px solid #3b82f6; color: #fff; }
        .nav-item:hover:not(.nav-active) { background-color: #1F2937; }
        button:disabled { opacity: 0.3; cursor: not-allowed; }
    </style>
</head>
<body class="flex h-screen overflow-hidden antialiased">

    <!-- Sidebar Navigation -->
    <aside class="w-64 glass-card border-r border-y-0 border-l-0 flex flex-col z-20 rounded-none">
        <div class="p-6 border-b border-dark-border flex items-center space-x-3">
            <div class="w-10 h-10 rounded-lg bg-gradient-to-br from-blue-600 to-purple-600 flex items-center justify-center shadow-lg shadow-blue-500/20">
                <i class="fa-solid fa-layer-group text-white text-lg"></i>
            </div>
            <div>
                <h1 class="text-white text-lg font-bold tracking-tight">Alpha<span class="text-blue-500">Terminal</span></h1>
                <p class="text-xs text-gray-400 font-mono">v3.0 / PM-DASH</p>
            </div>
        </div>
        
        <div class="px-6 py-4">
            <p class="text-[10px] font-bold text-gray-500 uppercase tracking-widest mb-3">Backtest Periods</p>
            <div class="space-y-1 overflow-y-auto max-h-[calc(100vh-200px)] pr-2" id="nav-months"></div>
        </div>
    </aside>

    <!-- Main Content Workarea -->
    <main class="flex-1 flex flex-col h-screen overflow-hidden bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-gray-900 via-[#0B0F19] to-[#0B0F19]">
        
        <!-- Top Toolbar with Navigation -->
        <header class="px-8 py-4 border-b border-dark-border flex justify-between items-center glass-card border-x-0 border-t-0 rounded-none z-10 sticky top-0">
            <div>
                <h2 class="text-2xl font-semibold text-white tracking-tight flex items-center">
                    Period: <span id="header-period" class="ml-2 text-blue-400 num-font">--</span>
                </h2>
            </div>
            
            <div class="flex items-center space-x-6">
                <div class="flex items-center bg-gray-800 rounded-md border border-gray-700 p-1 shadow-inner">
                    <button id="btn-prev" class="px-3 py-1.5 text-gray-400 hover:text-white transition-colors flex items-center text-xs font-bold" title="Older Period">
                        <i class="fa-solid fa-chevron-left mr-1"></i> Prev
                    </button>
                    <div class="w-px h-4 bg-gray-600 mx-1"></div>
                    <button id="btn-next" class="px-3 py-1.5 text-gray-400 hover:text-white transition-colors flex items-center text-xs font-bold" title="Newer Period">
                        Next <i class="fa-solid fa-chevron-right ml-1"></i>
                    </button>
                </div>
                <button class="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-md text-xs font-medium transition duration-200 shadow-lg shadow-blue-500/20 flex items-center">
                    <i class="fa-solid fa-download mr-2"></i> Export Data
                </button>
            </div>
        </header>

        <!-- Scrollable Dashboard -->
        <div class="flex-1 overflow-y-auto p-8 custom-scrollbar relative">
            
            <!-- PERSISTENT OVERALL DASHBOARD (Inception to Cutoff) -->
            <div class="glass-card p-5 mb-6 border-l-4 border-l-blue-500 bg-gradient-to-r from-blue-900/20 to-transparent flex flex-col md:flex-row justify-between items-center gap-6 sticky top-0 z-10 backdrop-blur-xl">
                <div class="flex-1">
                    <h3 class="text-xs font-bold text-blue-400 uppercase tracking-widest mb-3 flex items-center">
                        <i class="fa-solid fa-timeline mr-2"></i> Inception to Cutoff Date (<span id="itd-cutoff-date" class="num-font text-white mx-1">--</span>)
                    </h3>
                    <div class="flex space-x-12">
                        <div>
                            <p class="text-[10px] text-gray-400 uppercase font-semibold">Cumulative Strategy</p>
                            <p class="text-2xl font-bold num-font text-white tracking-tight" id="itd-strat">--</p>
                        </div>
                        <div>
                            <p class="text-[10px] text-gray-400 uppercase font-semibold">Cumulative Benchmark</p>
                            <p class="text-2xl font-bold num-font text-white tracking-tight" id="itd-bmk">--</p>
                        </div>
                        <div>
                            <p class="text-[10px] text-gray-400 uppercase font-semibold">Active ITD Return</p>
                            <p class="text-2xl font-bold num-font tracking-tight" id="itd-active">--</p>
                        </div>
                    </div>
                </div>
                
                <div class="w-full md:w-1/3 bg-gray-900/50 p-4 rounded-lg border border-gray-800">
                    <div class="flex justify-between items-end mb-2">
                        <p class="text-[10px] text-gray-400 uppercase font-semibold">Backtest Timeline</p>
                        <p class="text-xs num-font text-blue-400 font-bold" id="timeline-pct">--</p>
                    </div>
                    <div class="w-full bg-gray-800 rounded-full h-1.5 mb-1 overflow-hidden">
                        <div id="timeline-bar" class="bg-blue-500 h-1.5 rounded-full transition-all duration-700 ease-out relative" style="width: 0%">
                            <div class="absolute right-0 top-0 bottom-0 w-4 bg-white/30 blur-[2px]"></div>
                        </div>
                    </div>
                    <div class="flex justify-between text-[9px] num-font text-gray-500">
                        <span id="timeline-start">--</span>
                        <span>Present</span>
                    </div>
                </div>
            </div>

            <div class="max-w-[1600px] mx-auto space-y-6">
                <!-- Monthly View Title -->
                <h3 class="text-sm font-semibold text-gray-300 uppercase tracking-widest border-b border-gray-800 pb-2">Single Period Analysis: <span id="local-period-title" class="text-white num-font"></span></h3>

                <!-- Institutional KPIs -->
                <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                    <div class="glass-card p-4 relative overflow-hidden group">
                        <div class="absolute top-0 right-0 w-16 h-16 bg-blue-500/5 rounded-full -mr-8 -mt-8 transition-transform group-hover:scale-150"></div>
                        <p class="text-xs text-gray-400 font-medium mb-1">Active Return (1M)</p>
                        <h3 class="text-2xl font-bold num-font tracking-tight" id="kpi-active">--</h3>
                        <p class="text-[10px] mt-2 flex justify-between text-gray-500">
                            <span>Port: <span id="kpi-port" class="text-white num-font">--</span></span>
                            <span>Bmk: <span id="kpi-bmk" class="text-white num-font">--</span></span>
                        </p>
                    </div>
                    <div class="glass-card p-4 relative overflow-hidden">
                        <p class="text-xs text-gray-400 font-medium mb-1">Alpha (Ann.)</p>
                        <h3 class="text-2xl font-bold num-font tracking-tight text-white" id="kpi-alpha">--</h3>
                        <p class="text-[10px] mt-2 text-gray-500">Jensen's Measure</p>
                    </div>
                    <div class="glass-card p-4 relative overflow-hidden">
                        <p class="text-xs text-gray-400 font-medium mb-1">Information Ratio</p>
                        <h3 class="text-2xl font-bold num-font tracking-tight text-white" id="kpi-ir">--</h3>
                        <p class="text-[10px] mt-2 text-gray-500">Active Risk Adj.</p>
                    </div>
                    <div class="glass-card p-4 relative overflow-hidden">
                        <p class="text-xs text-gray-400 font-medium mb-1">Sharpe Ratio</p>
                        <h3 class="text-2xl font-bold num-font tracking-tight text-white" id="kpi-sharpe">--</h3>
                        <p class="text-[10px] mt-2 text-gray-500">Risk Free: 4.2%</p>
                    </div>
                    <div class="glass-card p-4 relative overflow-hidden">
                        <p class="text-xs text-gray-400 font-medium mb-1">Max Drawdown</p>
                        <h3 class="text-2xl font-bold num-font tracking-tight text-neg" id="kpi-dd">--</h3>
                        <p class="text-[10px] mt-2 text-gray-500">Intra-month</p>
                    </div>
                    <div class="glass-card p-4 relative overflow-hidden">
                        <p class="text-xs text-gray-400 font-medium mb-1">Portfolio Beta</p>
                        <h3 class="text-2xl font-bold num-font tracking-tight text-white" id="kpi-beta">--</h3>
                        <p class="text-[10px] mt-2 text-gray-500">vs. Benchmark</p>
                    </div>
                </div>

                <!-- Charts Section -->
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    <div class="lg:col-span-2 glass-card p-5">
                        <div class="flex justify-between items-center mb-4">
                            <h3 class="text-sm font-semibold text-gray-200">Intra-Period Equity Curve</h3>
                            <div class="flex space-x-2 text-[10px] num-font">
                                <span class="px-2 py-1 bg-blue-500/20 text-blue-400 rounded">Strategy</span>
                                <span class="px-2 py-1 bg-gray-700/50 text-gray-300 rounded">Benchmark</span>
                            </div>
                        </div>
                        <div class="relative h-72 w-full"><canvas id="equityCurveChart"></canvas></div>
                    </div>
                    <div class="glass-card p-5">
                        <h3 class="text-sm font-semibold text-gray-200 mb-4">Brinson Sector Attribution</h3>
                        <div class="relative h-72 w-full"><canvas id="attributionChart"></canvas></div>
                    </div>
                </div>

                <!-- Position Analytics Table -->
                <div class="glass-card p-0 overflow-hidden flex flex-col">
                    <div class="p-5 border-b border-dark-border flex justify-between items-center bg-gray-900/50">
                        <h3 class="text-sm font-semibold text-gray-200">Position Level Analytics</h3>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left border-collapse">
                            <thead>
                                <tr class="bg-[#0f1524] text-[10px] font-bold text-gray-400 uppercase tracking-wider border-b border-dark-border">
                                    <th class="px-5 py-3">Ticker</th>
                                    <th class="px-5 py-3">Sector</th>
                                    <th class="px-5 py-3 text-right">Weight</th>
                                    <th class="px-5 py-3 text-right">Port. Return</th>
                                    <th class="px-5 py-3 text-right">Contribution</th>
                                    <th class="px-5 py-3 text-right">Vol (30D)</th>
                                </tr>
                            </thead>
                            <tbody id="holdings-table" class="text-xs divide-y divide-dark-border"></tbody>
                        </table>
                    </div>
                </div>

            </div>
        </div>
    </main>

    <script>
        Chart.defaults.color = '#9CA3AF';
        Chart.defaults.font.family = "'Inter', sans-serif";

        const advancedData = __PYTHON_INJECT_DATA_HERE__;

        let displayMonths = [];
        let sortedChronological = [];
        let currentIndex = 0;
        let eqChart = null;
        let attrChart = null;

        const fmtPct = (v) => (v > 0 ? '+' : '') + (v || 0).toFixed(2) + '%';
        const numColor = (v) => v > 0 ? 'text-pos' : (v < 0 ? 'text-neg' : 'text-gray-400');

        function init() {
            displayMonths = Object.keys(advancedData).sort((a,b) => new Date(b) - new Date(a));
            if(displayMonths.length === 0) return;
            
            sortedChronological = [...displayMonths].reverse();
            
            const nav = document.getElementById('nav-months');
            
            displayMonths.forEach((m, i) => {
                const btn = document.createElement('button');
                btn.id = `nav-btn-${i}`;
                btn.className = `w-full text-left px-4 py-2.5 rounded text-sm mb-1 nav-item flex justify-between items-center ${i===0 ? 'nav-active' : 'text-gray-400'}`;
                
                const ret = advancedData[m]?.metrics?.return || 0;
                
                btn.innerHTML = `<span class="font-medium"><i class="fa-regular fa-calendar-days text-[11px] mr-2 opacity-70"></i> ${m}</span><span class="text-[10px] num-font opacity-60">${fmtPct(ret)}</span>`;
                btn.onclick = () => selectMonth(i);
                nav.appendChild(btn);
            });

            document.getElementById('timeline-start').innerText = sortedChronological[0];

            document.getElementById('btn-prev').onclick = () => { if(currentIndex < displayMonths.length - 1) selectMonth(currentIndex + 1); };
            document.getElementById('btn-next').onclick = () => { if(currentIndex > 0) selectMonth(currentIndex - 1); };

            selectMonth(0);
        }

        function selectMonth(index) {
            currentIndex = index;
            const month = displayMonths[index];
            
            document.querySelectorAll('.nav-item').forEach(el => { el.classList.remove('nav-active'); el.classList.add('text-gray-400'); });
            const activeBtn = document.getElementById(`nav-btn-${index}`);
            if(activeBtn) { activeBtn.classList.add('nav-active'); activeBtn.classList.remove('text-gray-400'); }
            
            document.getElementById('header-period').innerText = month;
            document.getElementById('local-period-title').innerText = month;
            
            document.getElementById('btn-prev').disabled = (index === displayMonths.length - 1);
            document.getElementById('btn-next').disabled = (index === 0);
            
            calculateITD(month);
            loadLocalData(month);
        }

        function calculateITD(targetMonth) {
            let stratCum = 1;
            let bmkCum = 1;
            let monthsPassed = 0;
            
            for(let m of sortedChronological) {
                stratCum *= (1 + (advancedData[m]?.metrics?.return || 0) / 100);
                bmkCum *= (1 + (advancedData[m]?.metrics?.benchmark || 0) / 100);
                monthsPassed++;
                if (m === targetMonth) break;
            }
            
            const stratPct = (stratCum - 1) * 100;
            const bmkPct = (bmkCum - 1) * 100;
            const activePct = stratPct - bmkPct;
            
            document.getElementById('itd-cutoff-date').innerText = targetMonth;
            document.getElementById('itd-strat').innerText = fmtPct(stratPct);
            document.getElementById('itd-strat').className = `text-2xl font-bold num-font tracking-tight ${numColor(stratPct)}`;
            
            document.getElementById('itd-bmk').innerText = fmtPct(bmkPct);
            
            document.getElementById('itd-active').innerText = fmtPct(activePct);
            document.getElementById('itd-active').className = `text-2xl font-bold num-font tracking-tight ${numColor(activePct)}`;

            const totalMonths = sortedChronological.length;
            const pctComplete = (monthsPassed / totalMonths) * 100;
            document.getElementById('timeline-bar').style.width = `${pctComplete}%`;
            document.getElementById('timeline-pct').innerText = `${Math.round(pctComplete)}%`;
        }

        function loadLocalData(month) {
            const d = advancedData[month] || {};
            const m = d.metrics || {};
            
            const ret = m.return || 0;
            const bmk = m.benchmark || 0;
            const active = ret - bmk;
            
            document.getElementById('kpi-active').innerText = fmtPct(active);
            document.getElementById('kpi-active').className = `text-2xl font-bold num-font tracking-tight ${numColor(active)}`;
            document.getElementById('kpi-port').innerText = fmtPct(ret);
            document.getElementById('kpi-port').className = `num-font ${numColor(ret)}`;
            document.getElementById('kpi-bmk').innerText = fmtPct(bmk);
            
            document.getElementById('kpi-alpha').innerText = (m.alpha || 0).toFixed(2) + '%';
            document.getElementById('kpi-ir').innerText = (m.ir || 0).toFixed(2);
            document.getElementById('kpi-sharpe').innerText = (m.sharpe || 0).toFixed(2);
            document.getElementById('kpi-dd').innerText = (m.dd || 0).toFixed(1) + '%';
            document.getElementById('kpi-beta').innerText = (m.beta || 0).toFixed(2);

            const tbody = document.getElementById('holdings-table');
            tbody.innerHTML = '';
            if(d.positions && Array.isArray(d.positions)) {
                d.positions.sort((a,b) => (b.c || 0) - (a.c || 0)).forEach(p => {
                    tbody.innerHTML += `
                        <tr class="hover:bg-[#161f33] transition-colors">
                            <td class="px-5 py-3 font-bold text-gray-200 num-font">${p.t || '--'}</td>
                            <td class="px-5 py-3"><span class="bg-gray-800 text-gray-400 text-[10px] px-2 py-1 rounded border border-gray-700 uppercase tracking-wider">${p.s || '--'}</span></td>
                            <td class="px-5 py-3 text-right num-font text-gray-300">${(p.w || 0).toFixed(1)}%</td>
                            <td class="px-5 py-3 text-right num-font font-medium ${numColor(p.r)}">${fmtPct(p.r)}</td>
                            <td class="px-5 py-3 text-right num-font font-bold ${numColor(p.c)}">${fmtPct(p.c)}</td>
                            <td class="px-5 py-3 text-right num-font text-gray-500">${(p.v || 0).toFixed(1)}%</td>
                        </tr>
                    `;
                });
            }
            renderCharts(d);
        }

        function renderCharts(d) {
            if (eqChart) eqChart.destroy();
            const ctx1 = document.getElementById('equityCurveChart').getContext('2d');
            const grad = ctx1.createLinearGradient(0, 0, 0, 300);
            grad.addColorStop(0, 'rgba(59, 130, 246, 0.2)');
            grad.addColorStop(1, 'rgba(59, 130, 246, 0)');

            const eq = d.equityCurve || { labels: [], strategy: [], benchmark: [] };
            
            eqChart = new Chart(ctx1, {
                type: 'line',
                data: { labels: eq.labels, datasets: [
                    { label: 'Strategy', data: eq.strategy, borderColor: '#3b82f6', backgroundColor: grad, borderWidth: 2, fill: true, tension: 0.4, pointRadius: 0, pointHoverRadius: 4 },
                    { label: 'Benchmark', data: eq.benchmark, borderColor: '#4B5563', borderWidth: 2, borderDash: [5, 5], tension: 0.4, pointRadius: 0 }
                ]},
                options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false }, plugins: { legend: { display: false }, tooltip: { backgroundColor: 'rgba(17,24,39,0.9)', titleFont: {family: 'Inter'}, bodyFont: {family: 'JetBrains Mono'} } }, scales: { y: { grid: { color: '#1F2937', drawBorder: false }, ticks: { font: {family: 'JetBrains Mono'} } }, x: { grid: { display: false }, ticks: { font: {family: 'JetBrains Mono'} } } }
            });

            if (attrChart) attrChart.destroy();
            const ctx2 = document.getElementById('attributionChart').getContext('2d');
            const sectors = d.sectors || [];
            const sortedSectors = [...sectors].sort((a,b) => (b.contrib || 0) - (a.contrib || 0));
            
            attrChart = new Chart(ctx2, {
                type: 'bar',
                data: { labels: sortedSectors.map(s => s.name), datasets: [{ data: sortedSectors.map(s => s.contrib), backgroundColor: sortedSectors.map(v => (v.contrib || 0) >= 0 ? 'rgba(16, 185, 129, 0.8)' : 'rgba(239, 68, 68, 0.8)'), borderRadius: 2, barPercentage: 0.6 }] },
                options: { responsive: true, maintainAspectRatio: false, indexAxis: 'y', plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => fmtPct(c.raw) }, backgroundColor: 'rgba(17,24,39,0.9)' } }, scales: { x: { grid: { color: '#1F2937', drawBorder: false }, ticks: { font: {family: 'JetBrains Mono'}, callback: (v) => v+'%' } }, y: { grid: { display: false }, ticks: { font: {family: 'Inter', size: 11} } } }
            });
        }

        window.onload = init;
    </script>
</body>
</html>"""

    final_html = html_template.replace("__PYTHON_INJECT_DATA_HERE__", json_data_str)

    with open("dashboard.html", "w", encoding="utf-8") as f:
        f.write(final_html)
        
    print("â Successfully created 'dashboard.html'! Open this file in your browser to view.")

# ========================= MAIN =========================
if __name__ == "__main__":
    print("ð Starting FULLY AUTOMATIC SELF-RESUMING Indian Market Backtest...\n")
    
    all_dates = get_all_dates(years_back=5)
    progress = load_progress()
    completed = progress.get("completed", [])
    
    remaining = [d for d in all_dates if d not in completed]
    
    if not remaining:
        print("â All dates already processed!")
    else:
        print(f"Total remaining months: {len(remaining)}\n")
        
        while remaining:
            current_batch = remaining[:BATCH_SIZE]
            print(f"\nð Processing new batch of {len(current_batch)} months...")

            results = {}
            newly_completed = []

            for i, date_str in enumerate(current_batch):
                print(f"  [{i+1:2d}/{len(current_batch)}] â {date_str}")
                
                analysis = generate_analysis(date_str, max_retries=5)
                results[date_str] = analysis
                
                filename = f"{OUTPUT_DIR}/{date_str.replace('-', '_')}.json"
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(analysis, f, indent=2, ensure_ascii=False)
                
                newly_completed.append(date_str)
                
                time.sleep(35 + random.uniform(8, 15))   # Safe delay between individual calls

            # Update progress
            completed.extend(newly_completed)
            save_progress(completed)
            
            # Save batch result
            with open(f"backtest_batch_{datetime.now().strftime('%Y%m%d_%H%M')}.json", "w") as f:
                json.dump(results, f, indent=2)

            remaining = [d for d in all_dates if d not in completed]
            
            if remaining:
                print(f"\nâ³ Batch completed. Sleeping for {SLEEP_BETWEEN_BATCHES_MINUTES} minutes before next batch...\n")
                time.sleep(SLEEP_BETWEEN_BATCHES_MINUTES * 60)

    print(f"\nð FULL BACKTEST COMPLETED!")
    print(f"Total Months Processed: {len(completed)}")

    # Automatically generate the dashboard if files were successfully processed
    if len(completed) > 0:
        generate_dashboard()
