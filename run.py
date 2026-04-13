"""
VCP Scanner Runner — Orchestrates scan, enrichment, dashboard generation, and alerts.
"""

import json
import logging
import os
import sys
from datetime import datetime

import config
from engine import run_scan, fetch_all_prices, get_nifty_symbols
from enrichment import enrich_setups
from alerts import send_alerts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main() -> None:
    start = datetime.now()
    log.info("=" * 50)
    log.info("VCP Scanner — Stage 2 + Volatility Contraction Patterns")
    log.info("=" * 50)

    # Run the scan engine
    from engine import fetch_all_prices, get_nifty_symbols, apply_stage2_filter, detect_vcp, score_setup

    symbols = get_nifty_symbols()
    log.info(f"Universe: {len(symbols)} symbols")

    prices = fetch_all_prices(symbols)

    # Stage 2 filter
    stage2_passed = []
    for sym, df in prices.items():
        result = apply_stage2_filter(sym, df)
        if result:
            stage2_passed.append((sym, result, df))
    log.info(f"Stage 2: {len(stage2_passed)}/{len(prices)} passed")

    # VCP detection
    setups = []
    for sym, stage2, df in stage2_passed:
        vcp = detect_vcp(df)
        if vcp:
            s = score_setup(stage2, vcp)
            setup = {**stage2, **vcp, "score": s, "flags": []}
            setups.append(setup)
    log.info(f"VCP: {len(setups)} setups found")

    setups.sort(key=lambda x: x["score"], reverse=True)
    setups = setups[:config.MAX_RESULTS]

    # Enrich only the setups that passed (not the entire universe)
    setups = enrich_setups(setups, prices)

    # Re-sort after enrichment (scores unchanged but order may shift due to exclusions)
    setups.sort(key=lambda x: x["score"], reverse=True)

    elapsed = (datetime.now() - start).total_seconds()
    log.info(f"Scan complete in {elapsed:.0f}s — {len(setups)} final setups")

    # Output
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    # JSON output
    json_path = os.path.join(config.OUTPUT_DIR, config.JSON_FILENAME)
    with open(json_path, "w") as f:
        json.dump(setups, f, indent=2, default=str)
    log.info(f"JSON saved: {json_path}")

    # Full dashboard (detailed)
    html_path = os.path.join(config.OUTPUT_DIR, config.DASHBOARD_FILENAME)
    html = generate_dashboard(setups, elapsed)
    with open(html_path, "w") as f:
        f.write(html)
    log.info(f"Dashboard saved: {html_path}")

    # Action board (clean, ready-to-act only)
    action_path = os.path.join(config.OUTPUT_DIR, "action.html")
    action_html = generate_action_board(setups, elapsed)
    with open(action_path, "w") as f:
        f.write(action_html)
    log.info(f"Action board saved: {action_path}")

    # Telegram alerts
    send_alerts(setups)

    log.info("Done!")


def generate_dashboard(setups: list[dict], elapsed: float) -> str:
    """Generate a self-contained HTML dashboard with embedded data."""
    now = datetime.now().strftime("%d %b %Y, %I:%M %p")
    n = len(setups)
    earnings_clear = sum(1 for s in setups if "EARNINGS_CLEAR" in s.get("flags", []))
    tailwind = sum(1 for s in setups if "SECTOR_TAILWIND" in s.get("flags", []))
    vol_interest = sum(1 for s in setups if "VOLUME_INTEREST" in s.get("flags", []))
    high_delivery = sum(1 for s in setups if "HIGH_DELIVERY" in s.get("flags", []))
    inst_backed = sum(1 for s in setups if "INSTITUTIONAL_BACKED" in s.get("flags", []))
    green_count = sum(1 for s in setups if s.get("readiness") == "GREEN")
    avg_score = int(sum(s["score"] for s in setups) / n) if n else 0
    avg_rs = int(sum(s.get("rs_rating", 0) for s in setups) / n) if n else 0

    # Serialize setups for JS
    data_json = json.dumps(setups, default=str)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VCP Scanner Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}

:root {{
    --bg: #0a0a0f;
    --surface: #12121a;
    --surface2: #1a1a26;
    --border: #2a2a3a;
    --text: #e8e8f0;
    --text-dim: #8888a0;
    --green: #00e676;
    --green-dim: #00c85320;
    --amber: #ffab00;
    --amber-dim: #ffab0020;
    --red: #ff5252;
    --red-dim: #ff525220;
    --blue: #448aff;
    --blue-dim: #448aff20;
    --purple: #b388ff;
}}

body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'DM Sans', -apple-system, sans-serif;
    min-height: 100vh;
    overflow-x: hidden;
}}

.header {{
    padding: 32px 40px 24px;
    border-bottom: 1px solid var(--border);
}}
.header h1 {{
    font-family: 'Bebas Neue', sans-serif;
    font-size: 36px;
    letter-spacing: 2px;
    color: var(--green);
}}
.header .meta {{
    font-size: 13px;
    color: var(--text-dim);
    margin-top: 4px;
    font-family: 'JetBrains Mono', monospace;
}}

.stats {{
    display: flex;
    gap: 16px;
    padding: 24px 40px;
    overflow-x: auto;
}}
.stat-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px 24px;
    min-width: 160px;
    flex-shrink: 0;
}}
.stat-card .label {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--text-dim);
    font-weight: 600;
}}
.stat-card .value {{
    font-family: 'Bebas Neue', sans-serif;
    font-size: 42px;
    line-height: 1;
    margin-top: 8px;
}}
.stat-card .value.green {{ color: var(--green); }}
.stat-card .value.amber {{ color: var(--amber); }}
.stat-card .value.blue {{ color: var(--blue); }}

.filters {{
    display: flex;
    gap: 10px;
    padding: 0 40px 20px;
    flex-wrap: wrap;
}}
.filter-btn {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 16px;
    color: var(--text-dim);
    font-size: 13px;
    font-family: 'DM Sans', sans-serif;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
}}
.filter-btn:hover {{ border-color: var(--green); color: var(--text); }}
.filter-btn.active {{ background: var(--green-dim); border-color: var(--green); color: var(--green); }}

.content {{
    display: flex;
    gap: 0;
    height: calc(100vh - 280px);
    min-height: 500px;
}}

.table-wrap {{
    flex: 1;
    overflow: auto;
    border-right: 1px solid var(--border);
}}

table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}}
thead {{
    position: sticky;
    top: 0;
    z-index: 10;
}}
th {{
    background: var(--surface);
    padding: 12px 16px;
    text-align: left;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-dim);
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    cursor: pointer;
    user-select: none;
}}
th:hover {{ color: var(--text); }}

td {{
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    white-space: nowrap;
}}
tr {{
    cursor: pointer;
    transition: background 0.15s;
}}
tr:hover {{ background: var(--surface2); }}
tr.selected {{ background: var(--green-dim); }}

.score {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 28px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 14px;
}}
.score.high {{ background: var(--green-dim); color: var(--green); }}
.score.mid {{ background: var(--amber-dim); color: var(--amber); }}
.score.low {{ background: var(--red-dim); color: var(--red); }}

.pill {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.5px;
    margin: 1px 2px;
}}
.pill.green {{ background: var(--green-dim); color: var(--green); }}
.pill.amber {{ background: var(--amber-dim); color: var(--amber); }}
.pill.red {{ background: var(--red-dim); color: var(--red); }}
.pill.blue {{ background: var(--blue-dim); color: var(--blue); }}

.detail-panel {{
    width: 420px;
    min-width: 420px;
    overflow-y: auto;
    padding: 24px;
    display: none;
}}
.detail-panel.show {{ display: block; }}

.detail-panel h2 {{
    font-family: 'Bebas Neue', sans-serif;
    font-size: 28px;
    letter-spacing: 1px;
    color: var(--green);
    margin-bottom: 4px;
}}
.detail-panel .sub {{
    font-size: 12px;
    color: var(--text-dim);
    margin-bottom: 20px;
}}

.detail-section {{
    margin-bottom: 20px;
}}
.detail-section h3 {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--text-dim);
    margin-bottom: 10px;
    font-weight: 600;
}}
.detail-row {{
    display: flex;
    justify-content: space-between;
    padding: 6px 0;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
}}
.detail-row .label {{ color: var(--text-dim); }}
.detail-row .val {{ font-family: 'JetBrains Mono', monospace; font-weight: 500; }}

.contraction-viz {{
    display: flex;
    align-items: end;
    gap: 8px;
    height: 60px;
    margin: 12px 0;
}}
.contraction-bar {{
    flex: 1;
    background: var(--green);
    border-radius: 4px 4px 0 0;
    opacity: 0.7;
    position: relative;
    min-width: 24px;
}}
.contraction-bar span {{
    position: absolute;
    top: -18px;
    left: 50%;
    transform: translateX(-50%);
    font-size: 10px;
    font-family: 'JetBrains Mono', monospace;
    color: var(--text-dim);
}}

.badges {{
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 8px;
}}

@media (max-width: 1200px) {{
    .detail-panel {{ width: 360px; min-width: 360px; }}
}}
@media (max-width: 900px) {{
    .content {{ flex-direction: column; height: auto; }}
    .detail-panel {{ width: 100%; min-width: 100%; border-right: none; border-top: 1px solid var(--border); }}
    .table-wrap {{ border-right: none; }}
}}
</style>
</head>
<body>

<div class="header">
    <h1>VCP SCANNER</h1>
    <div class="meta">{now} &nbsp;|&nbsp; {n} setups &nbsp;|&nbsp; {elapsed:.0f}s scan time</div>
</div>

<div class="stats">
    <div class="stat-card">
        <div class="label">Setups Found</div>
        <div class="value green">{n}</div>
    </div>
    <div class="stat-card">
        <div class="label">Avg Score</div>
        <div class="value green">{avg_score}</div>
    </div>
    <div class="stat-card">
        <div class="label">Earnings Clear</div>
        <div class="value blue">{earnings_clear}</div>
    </div>
    <div class="stat-card">
        <div class="label">Sector Tailwind</div>
        <div class="value amber">{tailwind}</div>
    </div>
    <div class="stat-card">
        <div class="label">Volume Interest</div>
        <div class="value amber">{vol_interest}</div>
    </div>
    <div class="stat-card">
        <div class="label">High Delivery</div>
        <div class="value green">{high_delivery}</div>
    </div>
    <div class="stat-card">
        <div class="label">Inst Backed</div>
        <div class="value blue">{inst_backed}</div>
    </div>
    <div class="stat-card">
        <div class="label">Ready (GREEN)</div>
        <div class="value green">{green_count}</div>
    </div>
</div>

<div class="filters">
    <button class="filter-btn active" data-filter="all">All</button>
    <button class="filter-btn" data-filter="ready_green">GREEN Only</button>
    <button class="filter-btn" data-filter="earnings_clear">Earnings Clear</button>
    <button class="filter-btn" data-filter="strong_fundamentals">Strong Fundamentals</button>
    <button class="filter-btn" data-filter="sector_tailwind">Sector Tailwind</button>
    <button class="filter-btn" data-filter="volume_interest">Volume Interest</button>
    <button class="filter-btn" data-filter="high_delivery">High Delivery</button>
    <button class="filter-btn" data-filter="inst_backed">Inst Backed</button>
    <button class="filter-btn" data-filter="score_70">Score 70+</button>
</div>

<div class="content">
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th data-sort="rank">#</th>
                    <th data-sort="readiness">Ready</th>
                    <th data-sort="score">Score</th>
                    <th data-sort="symbol">Symbol</th>
                    <th data-sort="close">Price</th>
                    <th data-sort="pivot">Pivot</th>
                    <th data-sort="pct_from_pivot">Away</th>
                    <th data-sort="num_contractions">VCP</th>
                    <th data-sort="days_to_earnings">Earnings</th>
                    <th data-sort="rs_rating">RS</th>
                    <th data-sort="sector">Sector</th>
                    <th>Flags</th>
                </tr>
            </thead>
            <tbody id="tableBody"></tbody>
        </table>
    </div>
    <div class="detail-panel" id="detailPanel"></div>
</div>

<script>
const DATA = {data_json};

let activeFilter = "all";
let sortKey = "score";
let sortAsc = false;
let selectedIdx = -1;

function pillClass(flag) {{
    if (flag.includes("CLEAR") || flag.includes("STRONG") || flag.includes("TAILWIND") || flag.includes("INTEREST")) return "green";
    if (flag.includes("WARNING") || flag.includes("HEADWIND") || flag.includes("UNKNOWN")) return "amber";
    if (flag.includes("WEAK") || flag.includes("EXCLUDE")) return "red";
    return "blue";
}}

function scoreClass(s) {{
    if (s >= 70) return "high";
    if (s >= 50) return "mid";
    return "low";
}}

function fmt(v, suffix) {{
    if (v === null || v === undefined) return "N/A";
    return (v > 0 ? "+" : "") + (typeof v === "number" ? (Math.abs(v) < 1 ? (v * 100).toFixed(0) : v.toFixed ? v.toFixed(1) : v) : v) + (suffix || "");
}}

function fmtPctRaw(v) {{
    if (v === null || v === undefined) return "N/A";
    let p = v * 100;
    return (p > 0 ? "+" : "") + p.toFixed(0) + "%";
}}

function earningsStr(d) {{
    let dte = d.days_to_earnings;
    if (dte === null || dte === undefined) return '<span style="color:var(--text-dim)">—</span>';
    let flag = dte >= 14 ? "✓" : "⚠️";
    let color = dte >= 30 ? "var(--green)" : dte >= 14 ? "var(--text)" : "var(--amber)";
    return `<span style="color:${{color}}">${{dte}}d ${{flag}}</span>`;
}}

function sectorShort(s) {{
    if (!s) return "—";
    return s.replace("Consumer ", "").replace("Information ", "").replace(" & ", "/").substring(0, 16);
}}

function readinessIcon(r) {{
    if (r === "GREEN") return '<span style="color:var(--green)">● GREEN</span>';
    if (r === "YELLOW") return '<span style="color:var(--amber)">● YELLOW</span>';
    return '<span style="color:var(--red)">● RED</span>';
}}

function applyFilter(data) {{
    if (activeFilter === "all") return data;
    return data.filter(d => {{
        let flags = d.flags || [];
        if (activeFilter === "ready_green") return d.readiness === "GREEN";
        if (activeFilter === "earnings_clear") return flags.includes("EARNINGS_CLEAR");
        if (activeFilter === "strong_fundamentals") return flags.includes("STRONG_FUNDAMENTALS");
        if (activeFilter === "sector_tailwind") return flags.includes("SECTOR_TAILWIND");
        if (activeFilter === "volume_interest") return flags.includes("VOLUME_INTEREST");
        if (activeFilter === "high_delivery") return flags.includes("HIGH_DELIVERY");
        if (activeFilter === "inst_backed") return flags.includes("INSTITUTIONAL_BACKED");
        if (activeFilter === "score_70") return d.score >= 70;
        return true;
    }});
}}

function renderTable() {{
    let data = applyFilter([...DATA]);
    data.sort((a, b) => {{
        let va = a[sortKey], vb = b[sortKey];
        if (va === null || va === undefined) va = sortAsc ? Infinity : -Infinity;
        if (vb === null || vb === undefined) vb = sortAsc ? Infinity : -Infinity;
        if (typeof va === "string") return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        return sortAsc ? va - vb : vb - va;
    }});

    let html = "";
    data.forEach((d, i) => {{
        let flags = (d.flags || []).map(f => `<span class="pill ${{pillClass(f)}}">${{f.replace("_", " ")}}</span>`).join("");
        html += `<tr data-idx="${{DATA.indexOf(d)}}" class="${{DATA.indexOf(d) === selectedIdx ? 'selected' : ''}}" onclick="selectRow(${{DATA.indexOf(d)}})">
            <td>${{i + 1}}</td>
            <td>${{readinessIcon(d.readiness)}}</td>
            <td><span class="score ${{scoreClass(d.score)}}">${{d.score}}</span></td>
            <td style="font-weight:600;color:var(--text)">${{d.symbol}}</td>
            <td>₹${{d.close.toLocaleString()}}</td>
            <td>₹${{d.pivot.toLocaleString()}}</td>
            <td style="color:${{d.pct_from_pivot <= 2 ? 'var(--green)' : 'var(--text)'}}">${{d.pct_from_pivot}}%</td>
            <td>${{d.contractions.length}}T</td>
            <td>${{earningsStr(d)}}</td>
            <td style="color:${{d.rs_rating >= 80 ? 'var(--green)' : d.rs_rating >= 50 ? 'var(--amber)' : 'var(--text-dim)'}}">${{d.rs_rating || "—"}}</td>
            <td style="font-size:11px;font-family:'DM Sans'">${{sectorShort(d.sector)}}</td>
            <td>${{flags}}</td>
        </tr>`;
    }});
    document.getElementById("tableBody").innerHTML = html;
}}

function selectRow(idx) {{
    selectedIdx = idx;
    renderTable();
    showDetail(DATA[idx]);
}}

function showDetail(d) {{
    let panel = document.getElementById("detailPanel");
    panel.classList.add("show");

    let contrBars = d.contractions.map((c, i) => {{
        let h = Math.max(c / d.contractions[0] * 100, 15);
        return `<div class="contraction-bar" style="height:${{h}}%"><span>${{c}}%</span></div>`;
    }}).join("");

    let flagBadges = (d.flags || []).map(f => `<span class="pill ${{pillClass(f)}}">${{f.replace("_", " ")}}</span>`).join("");

    panel.innerHTML = `
        <h2>${{d.symbol}}</h2>
        <div class="sub">${{d.sector || "—"}} · ${{d.industry || "—"}} · ${{d.market_cap_category || "—"}} Cap</div>

        <div class="badges">${{flagBadges}}</div>

        <div class="detail-section" style="margin-top:20px">
            <h3>VCP Pattern</h3>
            <div class="contraction-viz">${{contrBars}}</div>
            <div class="detail-row"><span class="label">Contractions</span><span class="val">${{d.contractions.map(c => c + "%").join(" → ")}} (${{d.num_contractions}}T)</span></div>
            <div class="detail-row"><span class="label">Volume Declining</span><span class="val" style="color:${{d.volume_declining ? 'var(--green)' : 'var(--red)'}}">${{d.volume_declining ? "Yes ✓" : "No ✗"}}</span></div>
            <div class="detail-row"><span class="label">Vol Dry-up Ratio</span><span class="val">${{d.vol_dry_up_ratio}}</span></div>
            <div class="detail-row"><span class="label">Pattern Bars</span><span class="val">${{d.pattern_bars}}</span></div>
        </div>

        <div class="detail-section">
            <h3>Price & Pivot</h3>
            <div class="detail-row"><span class="label">Close</span><span class="val">₹${{d.close.toLocaleString()}}</span></div>
            <div class="detail-row"><span class="label">Pivot</span><span class="val">₹${{d.pivot.toLocaleString()}}</span></div>
            <div class="detail-row"><span class="label">Distance to Pivot</span><span class="val" style="color:var(--green)">${{d.pct_from_pivot}}%</span></div>
            <div class="detail-row"><span class="label">52W High</span><span class="val">₹${{d.high_52w.toLocaleString()}} (${{d.pct_from_high}}% away)</span></div>
            <div class="detail-row"><span class="label">SMA 50 / 150 / 200</span><span class="val">${{d.sma50}} / ${{d.sma150}} / ${{d.sma200}}</span></div>
        </div>

        <div class="detail-section">
            <h3>Earnings</h3>
            <div class="detail-row"><span class="label">Next Earnings</span><span class="val">${{d.earnings_date || "Unknown"}}</span></div>
            <div class="detail-row"><span class="label">Days Until</span><span class="val">${{d.days_to_earnings !== null ? d.days_to_earnings + " days" : "N/A"}}</span></div>
        </div>

        <div class="detail-section">
            <h3>Fundamentals</h3>
            <div class="detail-row"><span class="label">Revenue Growth (YoY)</span><span class="val">${{fmtPctRaw(d.revenue_growth)}}</span></div>
            <div class="detail-row"><span class="label">Earnings Growth (YoY)</span><span class="val">${{fmtPctRaw(d.earnings_growth)}}</span></div>
            <div class="detail-row"><span class="label">ROE</span><span class="val">${{fmtPctRaw(d.roe)}}</span></div>
            <div class="detail-row"><span class="label">Profit Margins</span><span class="val">${{fmtPctRaw(d.profit_margins)}}</span></div>
            <div class="detail-row"><span class="label">Market Cap</span><span class="val">${{d.market_cap ? "₹" + (d.market_cap / 10000000).toFixed(0) + " Cr" : "N/A"}}</span></div>
        </div>

        <div class="detail-section">
            <h3>Relative Strength</h3>
            <div class="detail-row"><span class="label">RS Rating</span><span class="val" style="color:${{d.rs_rating >= 80 ? 'var(--green)' : 'var(--amber)'}}">${{d.rs_rating}}th percentile</span></div>
            <div class="detail-row"><span class="label">1M Return</span><span class="val">${{d.returns_1m !== null ? d.returns_1m + "%" : "N/A"}} <span style="color:var(--text-dim);font-size:11px">vs ${{d.nifty_1m || "—"}}% Nifty</span></span></div>
            <div class="detail-row"><span class="label">3M Return</span><span class="val">${{d.returns_3m !== null ? d.returns_3m + "%" : "N/A"}} <span style="color:var(--text-dim);font-size:11px">vs ${{d.nifty_3m || "—"}}% Nifty</span></span></div>
            <div class="detail-row"><span class="label">6M Return</span><span class="val">${{d.returns_6m !== null ? d.returns_6m + "%" : "N/A"}} <span style="color:var(--text-dim);font-size:11px">vs ${{d.nifty_6m || "—"}}% Nifty</span></span></div>
            <div class="detail-row"><span class="label">12M Return</span><span class="val">${{d.returns_12m !== null ? d.returns_12m + "%" : "N/A"}} <span style="color:var(--text-dim);font-size:11px">vs ${{d.nifty_12m || "—"}}% Nifty</span></span></div>
        </div>

        <div class="detail-section">
            <h3>Sector</h3>
            <div class="detail-row"><span class="label">Sector</span><span class="val">${{d.sector || "N/A"}}</span></div>
            <div class="detail-row"><span class="label">Industry</span><span class="val">${{d.industry || "N/A"}}</span></div>
            <div class="detail-row"><span class="label">Sector 1M vs Nifty</span><span class="val" style="color:${{(d.sector_vs_nifty_1m || 0) > 0 ? 'var(--green)' : 'var(--red)'}}">${{d.sector_vs_nifty_1m !== null ? (d.sector_vs_nifty_1m > 0 ? "+" : "") + d.sector_vs_nifty_1m + "%" : "N/A"}}</span></div>
        </div>

        <div class="detail-section">
            <h3>NSE Data</h3>
            <div class="detail-row"><span class="label">Delivery % (Today)</span><span class="val" style="color:${{(d.delivery_pct_today || 0) >= 60 ? 'var(--green)' : (d.delivery_pct_today || 0) < 30 ? 'var(--red)' : 'var(--amber)'}}">${{d.delivery_pct_today != null ? d.delivery_pct_today + "%" : "N/A"}}</span></div>
            <div class="detail-row"><span class="label">Delivery % (20d Avg)</span><span class="val">${{d.delivery_pct_avg_20d != null ? d.delivery_pct_avg_20d + "%" : "N/A"}}</span></div>
            <div class="detail-row"><span class="label">Delivery Trend</span><span class="val" style="color:${{d.delivery_pct_trend === 'RISING' ? 'var(--green)' : d.delivery_pct_trend === 'FALLING' ? 'var(--red)' : 'var(--amber)'}}">${{d.delivery_pct_trend || "N/A"}}</span></div>
            <div class="detail-row"><span class="label">High Delivery Days</span><span class="val">${{d.high_delivery_days || 0}} / 20</span></div>
            <div class="detail-row"><span class="label">Institutional %</span><span class="val" style="color:${{(d.institutional_pct || 0) >= 30 ? 'var(--green)' : (d.institutional_pct || 0) < 15 ? 'var(--red)' : 'var(--amber)'}}">${{d.institutional_pct != null ? d.institutional_pct + "%" : "N/A"}}</span></div>
            <div class="detail-row"><span class="label">Promoter %</span><span class="val">${{d.insider_pct != null ? d.insider_pct + "%" : "N/A"}}</span></div>
            ${{d.has_bulk_deal || d.has_block_deal ? '<div class="detail-row"><span class="label">Deals</span><span class="val" style="color:var(--green)">BULK/BLOCK DEAL DETECTED</span></div>' : ''}}
        </div>

        ${{(() => {{
            let tp = d.trade_plan || {{}};
            let entry = d.entry || {{}};
            let exits = d.exits || {{}};
            let rColor = d.readiness === "GREEN" ? "var(--green)" : d.readiness === "YELLOW" ? "var(--amber)" : "var(--red)";

            let checks = [
                ["Near Pivot", entry.near_pivot || entry.at_pivot],
                ["RS > 70", entry.rs_pass],
                ["Earnings Clear", entry.earnings_pass],
                ["Volume " + (entry.last_vol_ratio || 0) + "x", entry.volume_confirm],
                ["Close " + (entry.close_position || 0) + "%", entry.strong_close],
                ["Inst Backed", entry.institutional_pass],
            ];
            let checkHtml = checks.map(([name, ok]) =>
                '<span style="color:' + (ok ? 'var(--green)' : 'var(--red)') + '">' + (ok ? '✓' : '✗') + ' ' + name + '</span>'
            ).join('<br>');

            let exitWarnings = [];
            if (exits.sell_10sma) exitWarnings.push("Below 10 SMA for " + exits.below_10sma_days + " days");
            if (exits.below_50sma) exitWarnings.push("Below 50 SMA");
            if (exits.climax_warning) exitWarnings.push("CLIMAX TOP WARNING");
            let exitHtml = exitWarnings.length > 0
                ? exitWarnings.map(w => '<div style="color:var(--red);font-size:12px">⚠ ' + w + '</div>').join('')
                : '<div style="color:var(--green);font-size:12px">No exit signals</div>';

            return `
        <div class="detail-section" style="border:1px solid ${{rColor}};border-radius:10px;padding:16px;margin-top:4px">
            <h3 style="color:${{rColor}}">Trade Plan — ${{d.readiness}} (${{entry.conditions_met || 0}}/${{entry.conditions_total || 5}})</h3>
            <div class="detail-row"><span class="label">Entry (Pivot)</span><span class="val" style="color:var(--green)">₹${{(tp.entry_price || 0).toLocaleString()}}</span></div>
            <div class="detail-row"><span class="label">Stop Loss</span><span class="val" style="color:var(--red)">₹${{(tp.stop_loss || 0).toLocaleString()}} (-${{tp.stop_pct || 7}}%)</span></div>
            <div class="detail-row"><span class="label">Position</span><span class="val">${{tp.shares || 0}} shares / ₹${{(tp.position_value || 0).toLocaleString()}} (${{tp.position_pct || 0}}%)</span></div>
            <div class="detail-row"><span class="label">Risk</span><span class="val">₹${{(tp.risk_amount || 0).toLocaleString()}} (₹${{tp.risk_per_share || 0}}/share)</span></div>
            <div class="detail-row"><span class="label">Target 1R</span><span class="val">₹${{(tp.reward_1r || 0).toLocaleString()}}</span></div>
            <div class="detail-row"><span class="label">Target 2R</span><span class="val">₹${{(tp.reward_2r || 0).toLocaleString()}}</span></div>
            <div class="detail-row"><span class="label">Target 3R</span><span class="val" style="color:var(--green)">₹${{(tp.reward_3r || 0).toLocaleString()}}</span></div>
            <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border)">
                <div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--text-dim);margin-bottom:8px;font-weight:600">Entry Checklist</div>
                ${{checkHtml}}
            </div>
            <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border)">
                <div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--text-dim);margin-bottom:8px;font-weight:600">Exit Signals</div>
                ${{exitHtml}}
            </div>
        </div>`;
        }})()}}

        <div style="margin-top:24px;text-align:center">
            <a href="https://www.tradingview.com/chart/?symbol=NSE%3A${{d.symbol}}" target="_blank"
               style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:10px 24px;color:var(--text);text-decoration:none;font-size:13px;font-weight:600;display:inline-block">
                Open on TradingView →
            </a>
        </div>
    `;
}}

// Filter buttons
document.querySelectorAll(".filter-btn").forEach(btn => {{
    btn.addEventListener("click", () => {{
        document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        activeFilter = btn.dataset.filter;
        renderTable();
    }});
}});

// Sort headers
document.querySelectorAll("th[data-sort]").forEach(th => {{
    th.addEventListener("click", () => {{
        let key = th.dataset.sort;
        if (sortKey === key) sortAsc = !sortAsc;
        else {{ sortKey = key; sortAsc = false; }}
        renderTable();
    }});
}});

// Initial render
renderTable();
if (DATA.length > 0) selectRow(0);
</script>
</body>
</html>"""


def generate_action_board(setups: list[dict], elapsed: float) -> str:
    """
    Clean action board — only shows stocks ready to trade.
    No filters, no clicking. Just trade cards.
    """
    now = datetime.now().strftime("%d %b %Y, %I:%M %p")

    # Separate into tiers
    green = [s for s in setups if s.get("readiness") == "GREEN"]
    yellow = [s for s in setups if s.get("readiness") == "YELLOW"]
    red = [s for s in setups if s.get("readiness") == "RED"]

    def trade_card(s: dict, rank: int) -> str:
        tp = s.get("trade_plan", {})
        entry = s.get("entry", {})
        exits = s.get("exits", {})
        r = s.get("readiness", "RED")

        r_color = "#00e676" if r == "GREEN" else "#ffab00" if r == "YELLOW" else "#ff5252"
        border = r_color

        # Contractions
        c_str = " → ".join(f'{c}%' for c in s["contractions"])

        # Entry checklist
        vol_label = f"Del {s.get('delivery_pct_today', 0)}%" if entry.get("volume_source") == "delivery" else f"Vol {entry.get('last_vol_ratio', 0)}x"
        checks = [
            ("Near Pivot", entry.get("near_pivot") or entry.get("at_pivot")),
            (f"RS {s.get('rs_rating', 0)}", entry.get("rs_pass")),
            ("Earnings Clear", entry.get("earnings_pass")),
            (vol_label, entry.get("volume_confirm")),
            (f"Close {entry.get('close_position', 0)}%", entry.get("strong_close")),
            (f"Inst {s.get('institutional_pct', 0) or 0:.0f}%", entry.get("institutional_pass")),
        ]
        check_html = "".join(
            f'<span style="color:{"#00e676" if ok else "#ff5252"};margin-right:12px">{"✓" if ok else "✗"} {name}</span>'
            for name, ok in checks
        )

        # Exit warnings
        exit_items = []
        if exits.get("sell_10sma"):
            exit_items.append(f"Below 10 SMA for {exits['below_10sma_days']}d")
        if exits.get("below_50sma"):
            exit_items.append("Below 50 SMA")
        if exits.get("climax_warning"):
            exit_items.append("CLIMAX TOP")
        exit_html = (
            "".join(f'<div style="color:#ff5252;font-size:13px">⚠ {{e}}</div>' for e in exit_items)
            if exit_items
            else '<div style="color:#00e676;font-size:13px">No exit signals</div>'
        )
        # Fix the f-string for exit items
        exit_html = ""
        if exit_items:
            for e in exit_items:
                exit_html += f'<div style="color:#ff5252;font-size:13px">⚠ {e}</div>'
        else:
            exit_html = '<div style="color:#00e676;font-size:13px">No exit signals</div>'

        # Fundamentals line
        rg = f"{s.get('revenue_growth', 0) * 100:.0f}%" if s.get('revenue_growth') is not None else "N/A"
        eg = f"{s.get('earnings_growth', 0) * 100:.0f}%" if s.get('earnings_growth') is not None else "N/A"
        roe_val = f"{s.get('roe', 0) * 100:.0f}%" if s.get('roe') is not None else "N/A"

        dte = s.get("days_to_earnings")
        earn_str = f"{dte}d" if dte is not None else "?"

        sector_vs = s.get("sector_vs_nifty_1m")
        sector_str = s.get("sector", "?")
        if sector_vs is not None:
            arrow = "↑" if sector_vs > 0 else "↓"
            sector_str += f" ({arrow}{abs(sector_vs):.1f}% vs Nifty)"

        return f"""
        <div style="background:#12121a;border:2px solid {border};border-radius:14px;padding:24px;margin-bottom:16px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
                <div>
                    <span style="font-family:'Bebas Neue';font-size:32px;color:{r_color};letter-spacing:1px">{s['symbol']}</span>
                    <span style="font-size:13px;color:#8888a0;margin-left:12px">{s.get('sector', '')} · {s.get('market_cap_category', '')} Cap</span>
                </div>
                <div style="text-align:right">
                    <div style="font-family:'Bebas Neue';font-size:24px;color:{r_color}">{r}</div>
                    <div style="font-size:12px;color:#8888a0">Score {s['score']} · RS {s.get('rs_rating', 0)}</div>
                </div>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:16px">
                <div style="background:#1a1a26;border-radius:8px;padding:12px;text-align:center">
                    <div style="font-size:11px;color:#8888a0;text-transform:uppercase;letter-spacing:1px">Entry</div>
                    <div style="font-family:'JetBrains Mono';font-size:20px;color:#00e676;margin-top:4px">₹{tp.get('entry_price', 0):,.0f}</div>
                    <div style="font-size:11px;color:#8888a0">{s['pct_from_pivot']}% away</div>
                </div>
                <div style="background:#1a1a26;border-radius:8px;padding:12px;text-align:center">
                    <div style="font-size:11px;color:#8888a0;text-transform:uppercase;letter-spacing:1px">Stop Loss</div>
                    <div style="font-family:'JetBrains Mono';font-size:20px;color:#ff5252;margin-top:4px">₹{tp.get('stop_loss', 0):,.0f}</div>
                    <div style="font-size:11px;color:#8888a0">-{tp.get('stop_pct', 7):.0f}%</div>
                </div>
                <div style="background:#1a1a26;border-radius:8px;padding:12px;text-align:center">
                    <div style="font-size:11px;color:#8888a0;text-transform:uppercase;letter-spacing:1px">Position</div>
                    <div style="font-family:'JetBrains Mono';font-size:20px;color:#e8e8f0;margin-top:4px">{tp.get('shares', 0)} sh</div>
                    <div style="font-size:11px;color:#8888a0">₹{tp.get('position_value', 0):,.0f} ({tp.get('position_pct', 0)}%)</div>
                </div>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:16px">
                <div style="background:#1a1a26;border-radius:8px;padding:10px;text-align:center">
                    <div style="font-size:10px;color:#8888a0">TARGET 1R</div>
                    <div style="font-family:'JetBrains Mono';font-size:16px;color:#448aff">₹{tp.get('reward_1r', 0):,.0f}</div>
                </div>
                <div style="background:#1a1a26;border-radius:8px;padding:10px;text-align:center">
                    <div style="font-size:10px;color:#8888a0">TARGET 2R</div>
                    <div style="font-family:'JetBrains Mono';font-size:16px;color:#448aff">₹{tp.get('reward_2r', 0):,.0f}</div>
                </div>
                <div style="background:#1a1a26;border-radius:8px;padding:10px;text-align:center">
                    <div style="font-size:10px;color:#8888a0">TARGET 3R</div>
                    <div style="font-family:'JetBrains Mono';font-size:16px;color:#00e676">₹{tp.get('reward_3r', 0):,.0f}</div>
                </div>
            </div>

            <div style="background:#1a1a26;border-radius:8px;padding:14px;margin-bottom:12px">
                <div style="font-size:12px;color:#8888a0;margin-bottom:8px">VCP: <span style="color:#e8e8f0;font-family:'JetBrains Mono'">{c_str} ({s['num_contractions']}T)</span> · Earnings: <span style="color:#e8e8f0">{earn_str}</span> · Rev: <span style="color:#e8e8f0">{rg}</span> · EPS: <span style="color:#e8e8f0">{eg}</span> · ROE: <span style="color:#e8e8f0">{roe_val}</span></div>
                <div style="font-size:12px;color:#8888a0;margin-bottom:4px">{sector_str}</div>
                <div style="font-size:12px;color:#8888a0">Delivery: <span style="color:{"#00e676" if (s.get("delivery_pct_today") or 0) >= 60 else "#ffab00" if (s.get("delivery_pct_today") or 0) >= 40 else "#ff5252"}">{s.get("delivery_pct_today", "N/A")}%</span> (avg {s.get("delivery_pct_avg_20d", "N/A")}%) · Inst: <span style="color:{"#00e676" if (s.get("institutional_pct") or 0) >= 30 else "#ffab00" if (s.get("institutional_pct") or 0) >= 15 else "#ff5252"}">{s.get("institutional_pct", "N/A")}%</span>{" · <span style='color:#448aff'>BULK/BLOCK DEAL</span>" if s.get("has_bulk_deal") or s.get("has_block_deal") else ""}</div>
            </div>

            <div style="margin-bottom:8px;font-size:13px">{check_html}</div>

            <div>{exit_html}</div>

            <div style="margin-top:12px;text-align:right">
                <a href="https://www.tradingview.com/chart/?symbol=NSE%3A{s['symbol']}" target="_blank"
                   style="color:#448aff;font-size:12px;text-decoration:none">Open Chart →</a>
            </div>
        </div>"""

    # Build sections
    green_cards = "".join(trade_card(s, i + 1) for i, s in enumerate(green))
    yellow_cards = "".join(trade_card(s, i + 1) for i, s in enumerate(yellow))

    green_section = ""
    if green:
        green_section = f"""
        <div style="margin-bottom:32px">
            <h2 style="font-family:'Bebas Neue';font-size:28px;color:#00e676;letter-spacing:2px;margin-bottom:16px">
                BUY — {len(green)} setups ready
            </h2>
            <p style="font-size:13px;color:#8888a0;margin-bottom:20px">
                All 6 entry conditions met. Set alerts at pivot, buy on volume confirmation.
            </p>
            {green_cards}
        </div>"""
    else:
        green_section = """
        <div style="background:#12121a;border:1px solid #2a2a3a;border-radius:14px;padding:40px;text-align:center;margin-bottom:32px">
            <div style="font-family:'Bebas Neue';font-size:28px;color:#8888a0">NO GREEN SETUPS TODAY</div>
            <p style="color:#8888a0;font-size:13px;margin-top:8px">No stocks pass all 6 entry conditions right now. Check YELLOW setups below — they may ripen in 1-3 days.</p>
        </div>"""

    yellow_section = ""
    if yellow:
        yellow_section = f"""
        <div style="margin-bottom:32px">
            <h2 style="font-family:'Bebas Neue';font-size:28px;color:#ffab00;letter-spacing:2px;margin-bottom:16px">
                WATCH — {len(yellow)} setups forming
            </h2>
            <p style="font-size:13px;color:#8888a0;margin-bottom:20px">
                4-5 conditions met. Set price alerts — these could turn GREEN in 1-3 days.
            </p>
            {yellow_cards}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VCP Action Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    background: #0a0a0f;
    color: #e8e8f0;
    font-family: 'DM Sans', -apple-system, sans-serif;
    min-height: 100vh;
    padding: 32px;
    max-width: 800px;
    margin: 0 auto;
}}
</style>
</head>
<body>

<div style="margin-bottom:32px">
    <h1 style="font-family:'Bebas Neue';font-size:42px;color:#00e676;letter-spacing:3px">VCP ACTION BOARD</h1>
    <div style="font-family:'JetBrains Mono';font-size:12px;color:#8888a0;margin-top:4px">
        {now} · {len(setups)} scanned · {len(green)} green · {len(yellow)} yellow · {len(red)} red · {elapsed:.0f}s
    </div>
</div>

<div style="display:flex;gap:12px;margin-bottom:32px;flex-wrap:wrap">
    <div style="background:#12121a;border:1px solid #2a2a3a;border-radius:10px;padding:16px 20px;flex:1;min-width:120px">
        <div style="font-size:10px;color:#8888a0;text-transform:uppercase;letter-spacing:1.5px">Portfolio</div>
        <div style="font-family:'Bebas Neue';font-size:28px;color:#e8e8f0">₹{config.PORTFOLIO_SIZE:,.0f}</div>
    </div>
    <div style="background:#12121a;border:1px solid #2a2a3a;border-radius:10px;padding:16px 20px;flex:1;min-width:120px">
        <div style="font-size:10px;color:#8888a0;text-transform:uppercase;letter-spacing:1.5px">Risk/Trade</div>
        <div style="font-family:'Bebas Neue';font-size:28px;color:#e8e8f0">{config.RISK_PER_TRADE_PCT * 100:.0f}% (₹{config.PORTFOLIO_SIZE * config.RISK_PER_TRADE_PCT:,.0f})</div>
    </div>
    <div style="background:#12121a;border:1px solid #2a2a3a;border-radius:10px;padding:16px 20px;flex:1;min-width:120px">
        <div style="font-size:10px;color:#8888a0;text-transform:uppercase;letter-spacing:1.5px">Stop Loss</div>
        <div style="font-family:'Bebas Neue';font-size:28px;color:#ff5252">{config.STOP_LOSS_PCT * 100:.0f}%</div>
    </div>
</div>

{green_section}
{yellow_section}

<div style="text-align:center;padding:40px 0;color:#8888a0;font-size:12px;font-family:'JetBrains Mono'">
    {len(red)} RED setups hidden (conditions not met) · <a href="dashboard.html" style="color:#448aff;text-decoration:none">Full Dashboard →</a>
</div>

</body>
</html>"""


if __name__ == "__main__":
    main()
