"""
VCP Scanner Runner — Orchestrates scan, enrichment, dashboard generation, and alerts.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def ist_now_str() -> str:
    """Return current IST time formatted for display."""
    return datetime.now(IST).strftime("%d %b %Y · %I:%M %p IST")

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

    # Sanity check: NSE geo-blocks non-Indian IPs. If we got almost nothing
    # back, the data source is broken — fail loudly so the workflow goes red
    # and the user gets a GitHub email, instead of silently publishing a
    # "0 setups" page that looks identical to a legitimate empty day.
    min_expected = max(50, int(len(symbols) * 0.5))
    if len(prices) < min_expected:
        log.error(
            f"DATA FETCH FAILED: got {len(prices)} price series for {len(symbols)} "
            f"symbols (need >= {min_expected}). Likely NSE geo-block from CI runner. "
            f"Run the scanner from an Indian IP or via a Mumbai-region VM."
        )
        sys.exit(2)

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

    # Metadata file (last-updated timestamp for index.html to read)
    meta = {
        "last_updated_iso": datetime.now(IST).isoformat(),
        "last_updated_display": ist_now_str(),
        "last_updated_epoch": int(datetime.now(IST).timestamp()),
        "setups_total": len(setups),
        "setups_green": sum(1 for s in setups if s.get("readiness") == "GREEN"),
        "setups_yellow": sum(1 for s in setups if s.get("readiness") == "YELLOW"),
        "scan_seconds": round(elapsed),
    }
    meta_path = os.path.join(config.OUTPUT_DIR, "meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    log.info(f"Meta saved: {meta_path}")

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


# ──────────────────────────────────────────────────────────────
# Shared CSS theme
# ──────────────────────────────────────────────────────────────

_THEME_CSS = """
:root {
    --bg: #111318;
    --surface: #1a1d24;
    --surface2: #22262f;
    --border: #2e333d;
    --text: #e8e4dc;
    --text-dim: #8a8780;
    --text-muted: #5c5a56;
    --accent: #c9a84c;
    --accent-dim: #c9a84c18;
    --positive: #4caf7c;
    --positive-dim: #4caf7c18;
    --negative: #d4644a;
    --negative-dim: #d4644a18;
    --info: #5b8fd9;
    --info-dim: #5b8fd918;
    --warn: #d4a04a;
    --warn-dim: #d4a04a18;
    --radius: 10px;
    --radius-sm: 6px;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}

/* Typography */
.t-display { font-weight: 700; font-size: 28px; letter-spacing: -0.5px; color: var(--text); }
.t-heading { font-weight: 600; font-size: 13px; letter-spacing: 0.5px; text-transform: uppercase; color: var(--text-dim); }
.t-mono { font-family: 'JetBrains Mono', 'SF Mono', monospace; }
.t-dim { color: var(--text-dim); }
.t-muted { color: var(--text-muted); }

/* Utility */
.flex { display: flex; }
.flex-col { flex-direction: column; }
.flex-wrap { flex-wrap: wrap; }
.items-center { align-items: center; }
.justify-between { justify-content: space-between; }
.gap-8 { gap: 8px; }
.gap-12 { gap: 12px; }
.gap-16 { gap: 16px; }
.text-center { text-align: center; }
.text-right { text-align: right; }

/* Components */
.card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
}

.pill {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.3px;
}
.pill-positive { background: var(--positive-dim); color: var(--positive); }
.pill-negative { background: var(--negative-dim); color: var(--negative); }
.pill-warn { background: var(--warn-dim); color: var(--warn); }
.pill-info { background: var(--info-dim); color: var(--info); }
.pill-accent { background: var(--accent-dim); color: var(--accent); }

.kv { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.kv:last-child { border-bottom: none; }
.kv .k { color: var(--text-dim); }
.kv .v { font-family: 'JetBrains Mono', monospace; font-weight: 500; }

/* Nav */
.nav {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 24px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    position: sticky;
    top: 0;
    z-index: 100;
    gap: 12px;
}
.nav-left { display: flex; align-items: center; gap: 16px; }
.nav-logo { font-weight: 700; font-size: 15px; color: var(--text); text-decoration: none; }
.nav-logo span { color: var(--accent); }
.nav-links { display: flex; gap: 4px; }
.nav-links a {
    padding: 6px 14px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 500;
    color: var(--text-dim);
    text-decoration: none;
    transition: all 0.15s;
}
.nav-links a:hover { color: var(--text); background: var(--surface2); }
.nav-links a.active { color: var(--accent); background: var(--accent-dim); }

.updated {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 20px;
    font-size: 11px;
    color: var(--text-dim);
    font-family: 'JetBrains Mono', monospace;
    white-space: nowrap;
}
.updated .pulse {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--positive);
    box-shadow: 0 0 0 0 var(--positive);
    animation: pulse 2s infinite;
}
.updated .label { color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; font-size: 10px; }
.updated .time { color: var(--text); font-weight: 500; }
.updated.stale .pulse { background: var(--warn); animation: none; }
.updated.stale .time { color: var(--warn); }

@keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(76, 175, 124, 0.5); }
    70% { box-shadow: 0 0 0 8px rgba(76, 175, 124, 0); }
    100% { box-shadow: 0 0 0 0 rgba(76, 175, 124, 0); }
}

@media (max-width: 640px) {
    .nav { padding: 10px 14px; gap: 8px; flex-wrap: wrap; }
    .nav-left { gap: 10px; }
    .updated { padding: 5px 10px; font-size: 10px; }
    .updated .label { display: none; }
}
"""


# ──────────────────────────────────────────────────────────────
# Dashboard
# ──────────────────────────────────────────────────────────────

def generate_dashboard(setups: list[dict], elapsed: float) -> str:
    now = ist_now_str()
    n = len(setups)
    earnings_clear = sum(1 for s in setups if "EARNINGS_CLEAR" in s.get("flags", []))
    tailwind = sum(1 for s in setups if "SECTOR_TAILWIND" in s.get("flags", []))
    vol_interest = sum(1 for s in setups if "VOLUME_INTEREST" in s.get("flags", []))
    high_delivery = sum(1 for s in setups if "HIGH_DELIVERY" in s.get("flags", []))
    inst_backed = sum(1 for s in setups if "INSTITUTIONAL_BACKED" in s.get("flags", []))
    green_count = sum(1 for s in setups if s.get("readiness") == "GREEN")
    avg_score = int(sum(s["score"] for s in setups) / n) if n else 0

    data_json = json.dumps(setups, default=str)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VCP Scanner</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
{_THEME_CSS}

.header {{
    padding: 20px 24px;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
}}
.header .logo {{
    font-weight: 700;
    font-size: 20px;
    letter-spacing: -0.3px;
}}
.header .logo span {{ color: var(--accent); }}
.header .meta {{
    font-size: 12px;
    color: var(--text-dim);
    font-family: 'JetBrains Mono', monospace;
}}

.stats {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
    gap: 1px;
    background: var(--border);
    border-bottom: 1px solid var(--border);
}}
.stat {{
    background: var(--bg);
    padding: 16px 20px;
}}
.stat .label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); font-weight: 600; }}
.stat .num {{ font-family: 'JetBrains Mono', monospace; font-size: 28px; font-weight: 700; margin-top: 4px; }}
.stat .num.gold {{ color: var(--accent); }}
.stat .num.green {{ color: var(--positive); }}
.stat .num.blue {{ color: var(--info); }}

.filters {{
    display: flex;
    gap: 6px;
    padding: 16px 24px;
    overflow-x: auto;
    border-bottom: 1px solid var(--border);
    -webkit-overflow-scrolling: touch;
}}
.fbtn {{
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 6px 14px;
    color: var(--text-dim);
    font-size: 12px;
    font-family: 'Inter', sans-serif;
    font-weight: 500;
    cursor: pointer;
    white-space: nowrap;
    transition: all 0.15s;
}}
.fbtn:hover {{ border-color: var(--accent); color: var(--text); }}
.fbtn.on {{ background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }}

.content {{
    display: flex;
    height: calc(100vh - 240px);
    min-height: 400px;
}}

.table-wrap {{
    flex: 1;
    overflow: auto;
    border-right: 1px solid var(--border);
}}

table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
thead {{ position: sticky; top: 0; z-index: 10; }}
th {{
    background: var(--surface);
    padding: 10px 14px;
    text-align: left;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--text-muted);
    font-weight: 600;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    cursor: pointer;
    user-select: none;
}}
th:hover {{ color: var(--text-dim); }}
td {{
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    white-space: nowrap;
}}
tr {{ cursor: pointer; transition: background 0.12s; }}
tr:hover {{ background: var(--surface); }}
tr.sel {{ background: var(--accent-dim); }}

.badge {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 38px; height: 26px;
    border-radius: var(--radius-sm);
    font-weight: 600;
    font-size: 13px;
}}
.badge-high {{ background: var(--positive-dim); color: var(--positive); }}
.badge-mid {{ background: var(--warn-dim); color: var(--warn); }}
.badge-low {{ background: var(--negative-dim); color: var(--negative); }}

.dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }}
.dot-green {{ background: var(--positive); }}
.dot-yellow {{ background: var(--warn); }}
.dot-red {{ background: var(--negative); }}

.detail {{
    width: 400px;
    min-width: 400px;
    overflow-y: auto;
    padding: 24px;
    display: none;
}}
.detail.show {{ display: block; }}
.detail h2 {{ font-weight: 700; font-size: 24px; letter-spacing: -0.3px; margin-bottom: 2px; }}
.detail .sub {{ font-size: 12px; color: var(--text-dim); margin-bottom: 16px; }}
.detail-sec {{ margin-bottom: 20px; }}
.detail-sec h3 {{ font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); margin-bottom: 8px; font-weight: 600; }}

.cbar-wrap {{ display: flex; align-items: end; gap: 6px; height: 50px; margin: 8px 0 4px; }}
.cbar {{
    flex: 1;
    background: var(--accent);
    border-radius: 3px 3px 0 0;
    opacity: 0.6;
    position: relative;
    min-width: 20px;
}}
.cbar span {{
    position: absolute;
    top: -16px;
    left: 50%;
    transform: translateX(-50%);
    font-size: 9px;
    font-family: 'JetBrains Mono', monospace;
    color: var(--text-dim);
}}

.badges-wrap {{ display: flex; flex-wrap: wrap; gap: 4px; margin: 8px 0; }}

.trade-box {{
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px;
    margin-top: 8px;
}}

.chart-link {{
    display: inline-block;
    margin-top: 20px;
    padding: 10px 24px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    color: var(--text);
    text-decoration: none;
    font-size: 13px;
    font-weight: 500;
    transition: border-color 0.15s;
}}
.chart-link:hover {{ border-color: var(--accent); }}

@media (max-width: 1100px) {{
    .detail {{ width: 340px; min-width: 340px; }}
}}
@media (max-width: 860px) {{
    .content {{ flex-direction: column; height: auto; }}
    .detail {{ width: 100%; min-width: 100%; border-right: none; border-top: 1px solid var(--border); }}
    .table-wrap {{ border-right: none; max-height: 50vh; }}
    .header {{ padding: 16px; }}
    .filters {{ padding: 12px 16px; }}
    td, th {{ padding: 8px 10px; }}
}}
@media (max-width: 600px) {{
    .stat .num {{ font-size: 22px; }}
    .stat {{ padding: 12px 14px; }}
    .stats {{ grid-template-columns: repeat(auto-fit, minmax(90px, 1fr)); }}
}}
</style>
</head>
<body>

<nav class="nav">
    <div class="nav-left">
        <a href="index.html" class="nav-logo">VCP <span>Scanner</span></a>
        <div class="nav-links">
            <a href="action.html">Action Board</a>
            <a href="dashboard.html" class="active">Dashboard</a>
        </div>
    </div>
    <div class="updated" id="updatedBadge" data-updated="{now}">
        <span class="pulse"></span>
        <span class="label">Updated</span>
        <span class="time">{now}</span>
    </div>
</nav>

<div class="header">
    <div class="logo">VCP <span>Scanner</span></div>
    <div class="meta">{n} setups &middot; scan took {elapsed:.0f}s</div>
</div>

<div class="stats">
    <div class="stat"><div class="label">Setups</div><div class="num gold">{n}</div></div>
    <div class="stat"><div class="label">Avg Score</div><div class="num">{avg_score}</div></div>
    <div class="stat"><div class="label">Ready</div><div class="num green">{green_count}</div></div>
    <div class="stat"><div class="label">Earnings OK</div><div class="num blue">{earnings_clear}</div></div>
    <div class="stat"><div class="label">Tailwind</div><div class="num green">{tailwind}</div></div>
    <div class="stat"><div class="label">Hi Delivery</div><div class="num green">{high_delivery}</div></div>
    <div class="stat"><div class="label">Inst Backed</div><div class="num blue">{inst_backed}</div></div>
</div>

<div class="filters">
    <button class="fbtn on" data-f="all">All</button>
    <button class="fbtn" data-f="ready_green">GREEN</button>
    <button class="fbtn" data-f="earnings_clear">Earnings Clear</button>
    <button class="fbtn" data-f="strong_fundamentals">Strong Fundamentals</button>
    <button class="fbtn" data-f="sector_tailwind">Tailwind</button>
    <button class="fbtn" data-f="volume_interest">Volume Interest</button>
    <button class="fbtn" data-f="high_delivery">Hi Delivery</button>
    <button class="fbtn" data-f="inst_backed">Inst Backed</button>
    <button class="fbtn" data-f="score_70">Score 70+</button>
</div>

<div class="content">
    <div class="table-wrap">
        <table>
            <thead><tr>
                <th data-s="rank">#</th>
                <th data-s="readiness">Status</th>
                <th data-s="score">Score</th>
                <th data-s="symbol">Ticker</th>
                <th data-s="close">Price</th>
                <th data-s="pivot">Pivot</th>
                <th data-s="pct_from_pivot">Away</th>
                <th data-s="num_contractions">VCP</th>
                <th data-s="days_to_earnings">Earn</th>
                <th data-s="rs_rating">RS</th>
                <th data-s="delivery_pct_today">Del%</th>
                <th data-s="sector">Sector</th>
                <th>Flags</th>
            </tr></thead>
            <tbody id="tb"></tbody>
        </table>
    </div>
    <div class="detail" id="dp"></div>
</div>

<script>
const D = {data_json};
let filt = "all", sk = "score", sa = false, si = -1;

function pc(f) {{
    if (/CLEAR|STRONG|TAILWIND|INTEREST|HIGH_DELIVERY|INSTITUTIONAL_BACKED|DELIVERY_RISING|BULK_BLOCK/.test(f)) return "pill-positive";
    if (/WARNING|HEADWIND|UNKNOWN|FLAT/.test(f)) return "pill-warn";
    if (/WEAK|LOW|EXCLUDE/.test(f)) return "pill-negative";
    return "pill-info";
}}
function bc(s) {{ return s >= 70 ? "badge-high" : s >= 50 ? "badge-mid" : "badge-low"; }}
function fp(v) {{ if (v==null) return "N/A"; let p=v*100; return (p>0?"+":"")+p.toFixed(0)+"%"; }}
function dc(r) {{ return r==="GREEN"?"dot-green":r==="YELLOW"?"dot-yellow":"dot-red"; }}
function es(d) {{ let e=d.days_to_earnings; if (e==null) return '<span class="t-muted">—</span>'; let c=e>=30?"var(--positive)":e>=14?"var(--text)":"var(--warn)"; return `<span style="color:${{c}}">${{e}}d</span>`; }}
function ss(s) {{ if (!s) return "—"; return s.replace("Consumer ","").replace("Information ","").replace(" & ","/").substring(0,14); }}

function af(data) {{
    if (filt==="all") return data;
    return data.filter(d => {{
        let f=d.flags||[];
        if (filt==="ready_green") return d.readiness==="GREEN";
        if (filt==="earnings_clear") return f.includes("EARNINGS_CLEAR");
        if (filt==="strong_fundamentals") return f.includes("STRONG_FUNDAMENTALS");
        if (filt==="sector_tailwind") return f.includes("SECTOR_TAILWIND");
        if (filt==="volume_interest") return f.includes("VOLUME_INTEREST");
        if (filt==="high_delivery") return f.includes("HIGH_DELIVERY");
        if (filt==="inst_backed") return f.includes("INSTITUTIONAL_BACKED");
        if (filt==="score_70") return d.score>=70;
        return true;
    }});
}}

function render() {{
    let data = af([...D]);
    data.sort((a,b) => {{
        let va=a[sk],vb=b[sk];
        if (va==null) va=sa?Infinity:-Infinity;
        if (vb==null) vb=sa?Infinity:-Infinity;
        if (typeof va==="string") return sa?va.localeCompare(vb):vb.localeCompare(va);
        return sa?va-vb:vb-va;
    }});

    let h="";
    data.forEach((d,i) => {{
        let flags=(d.flags||[]).map(f=>`<span class="pill ${{pc(f)}}">${{f.replace(/_/g," ")}}</span>`).join(" ");
        let idx=D.indexOf(d);
        let del=d.delivery_pct_today!=null?d.delivery_pct_today+"%":"—";
        let delColor=(d.delivery_pct_today||0)>=60?"var(--positive)":(d.delivery_pct_today||0)<30?"var(--negative)":"var(--text)";
        h+=`<tr class="${{idx===si?'sel':''}}" onclick="sel(${{idx}})">
            <td style="color:var(--text-muted)">${{i+1}}</td>
            <td><span class="dot ${{dc(d.readiness)}}"></span>${{d.readiness}}</td>
            <td><span class="badge ${{bc(d.score)}}">${{d.score}}</span></td>
            <td style="font-weight:600;color:var(--text)">${{d.symbol}}</td>
            <td>₹${{d.close.toLocaleString()}}</td>
            <td>₹${{d.pivot.toLocaleString()}}</td>
            <td style="color:${{d.pct_from_pivot<=2?'var(--positive)':'var(--text)'}}">${{d.pct_from_pivot}}%</td>
            <td>${{d.contractions.length}}T</td>
            <td>${{es(d)}}</td>
            <td style="color:${{d.rs_rating>=80?'var(--positive)':d.rs_rating>=50?'var(--warn)':'var(--text-muted)'}}">${{d.rs_rating||"—"}}</td>
            <td style="color:${{delColor}}">${{del}}</td>
            <td style="font-size:11px;font-family:'Inter',sans-serif">${{ss(d.sector)}}</td>
            <td>${{flags}}</td>
        </tr>`;
    }});
    document.getElementById("tb").innerHTML=h;
}}

function sel(idx) {{ si=idx; render(); detail(D[idx]); }}

function detail(d) {{
    let p=document.getElementById("dp");
    p.classList.add("show");

    let cb=d.contractions.map((c,i)=>{{
        let h=Math.max(c/d.contractions[0]*100,15);
        return `<div class="cbar" style="height:${{h}}%"><span>${{c}}%</span></div>`;
    }}).join("");

    let fb=(d.flags||[]).map(f=>`<span class="pill ${{pc(f)}}">${{f.replace(/_/g," ")}}</span>`).join(" ");

    let tp=d.trade_plan||{{}}, en=d.entry||{{}}, ex=d.exits||{{}};
    let rc=d.readiness==="GREEN"?"var(--positive)":d.readiness==="YELLOW"?"var(--warn)":"var(--negative)";

    let checks=[
        ["Near Pivot",en.near_pivot||en.at_pivot],
        ["RS > 70",en.rs_pass],
        ["Earnings Clear",en.earnings_pass],
        ["Volume "+(en.last_vol_ratio||0)+"x",en.volume_confirm],
        ["Close "+(en.close_position||0)+"%",en.strong_close],
        ["Inst Backed",en.institutional_pass],
    ];
    let checkH=checks.map(([n,ok])=>`<div style="color:${{ok?'var(--positive)':'var(--negative)'}};font-size:12px;padding:3px 0">${{ok?'✓':'✗'}} ${{n}}</div>`).join("");

    let exitW=[];
    if (ex.sell_10sma) exitW.push("Below 10 SMA "+ex.below_10sma_days+"d");
    if (ex.below_50sma) exitW.push("Below 50 SMA");
    if (ex.climax_warning) exitW.push("CLIMAX TOP");
    let exitH=exitW.length>0?exitW.map(w=>`<div style="color:var(--negative);font-size:12px">⚠ ${{w}}</div>`).join(""):`<div style="color:var(--positive);font-size:12px">No exit signals</div>`;

    p.innerHTML=`
        <h2>${{d.symbol}}</h2>
        <div class="sub">${{d.sector||"—"}} · ${{d.industry||"—"}} · ${{d.market_cap_category||"—"}} Cap</div>
        <div class="badges-wrap">${{fb}}</div>

        <div class="detail-sec">
            <h3>VCP Pattern</h3>
            <div class="cbar-wrap">${{cb}}</div>
            <div class="kv"><span class="k">Contractions</span><span class="v">${{d.contractions.map(c=>c+"%").join(" → ")}} (${{d.num_contractions}}T)</span></div>
            <div class="kv"><span class="k">Volume Declining</span><span class="v" style="color:${{d.volume_declining?'var(--positive)':'var(--negative)'}}">${{d.volume_declining?"Yes":"No"}}</span></div>
            <div class="kv"><span class="k">Pattern Bars</span><span class="v">${{d.pattern_bars}}</span></div>
        </div>

        <div class="detail-sec">
            <h3>Price</h3>
            <div class="kv"><span class="k">Close</span><span class="v">₹${{d.close.toLocaleString()}}</span></div>
            <div class="kv"><span class="k">Pivot</span><span class="v">₹${{d.pivot.toLocaleString()}}</span></div>
            <div class="kv"><span class="k">Distance</span><span class="v" style="color:var(--positive)">${{d.pct_from_pivot}}%</span></div>
            <div class="kv"><span class="k">52W High</span><span class="v">₹${{d.high_52w.toLocaleString()}} (${{d.pct_from_high}}%)</span></div>
        </div>

        <div class="detail-sec">
            <h3>Fundamentals</h3>
            <div class="kv"><span class="k">Revenue Growth</span><span class="v">${{fp(d.revenue_growth)}}</span></div>
            <div class="kv"><span class="k">Earnings Growth</span><span class="v">${{fp(d.earnings_growth)}}</span></div>
            <div class="kv"><span class="k">ROE</span><span class="v">${{fp(d.roe)}}</span></div>
            <div class="kv"><span class="k">Market Cap</span><span class="v">${{d.market_cap?"₹"+(d.market_cap/10000000).toFixed(0)+" Cr":"N/A"}}</span></div>
        </div>

        <div class="detail-sec">
            <h3>Strength</h3>
            <div class="kv"><span class="k">RS Rating</span><span class="v" style="color:${{d.rs_rating>=80?'var(--positive)':'var(--warn)'}}">${{d.rs_rating}}th</span></div>
            <div class="kv"><span class="k">3M Return</span><span class="v">${{d.returns_3m!=null?d.returns_3m+"%":"N/A"}} <span class="t-muted" style="font-size:10px">vs ${{d.nifty_3m||"—"}}%</span></span></div>
            <div class="kv"><span class="k">6M Return</span><span class="v">${{d.returns_6m!=null?d.returns_6m+"%":"N/A"}} <span class="t-muted" style="font-size:10px">vs ${{d.nifty_6m||"—"}}%</span></span></div>
            <div class="kv"><span class="k">Sector vs Nifty</span><span class="v" style="color:${{(d.sector_vs_nifty_1m||0)>0?'var(--positive)':'var(--negative)'}}">${{d.sector_vs_nifty_1m!=null?(d.sector_vs_nifty_1m>0?"+":"")+d.sector_vs_nifty_1m+"%":"N/A"}}</span></div>
        </div>

        <div class="detail-sec">
            <h3>NSE Data</h3>
            <div class="kv"><span class="k">Delivery %</span><span class="v" style="color:${{(d.delivery_pct_today||0)>=60?'var(--positive)':(d.delivery_pct_today||0)<30?'var(--negative)':'var(--text)'}}">${{d.delivery_pct_today!=null?d.delivery_pct_today+"%":"N/A"}}</span></div>
            <div class="kv"><span class="k">Del 20d Avg</span><span class="v">${{d.delivery_pct_avg_20d!=null?d.delivery_pct_avg_20d+"%":"N/A"}}</span></div>
            <div class="kv"><span class="k">Del Trend</span><span class="v" style="color:${{d.delivery_pct_trend==='RISING'?'var(--positive)':d.delivery_pct_trend==='FALLING'?'var(--negative)':'var(--text)'}}">${{d.delivery_pct_trend||"N/A"}}</span></div>
            <div class="kv"><span class="k">Institutional</span><span class="v" style="color:${{(d.institutional_pct||0)>=30?'var(--positive)':(d.institutional_pct||0)<15?'var(--negative)':'var(--text)'}}">${{d.institutional_pct!=null?d.institutional_pct+"%":"N/A"}}</span></div>
            <div class="kv"><span class="k">Promoter</span><span class="v">${{d.insider_pct!=null?d.insider_pct+"%":"N/A"}}</span></div>
            ${{d.has_bulk_deal||d.has_block_deal?'<div class="kv"><span class="k">Deals</span><span class="v" style="color:var(--positive)">BULK/BLOCK DETECTED</span></div>':''}}
        </div>

        <div class="trade-box" style="border-color:${{rc}}">
            <h3 style="color:${{rc}};margin-bottom:12px">Trade Plan — ${{d.readiness}} (${{en.conditions_met||0}}/${{en.conditions_total||6}})</h3>
            <div class="kv"><span class="k">Entry</span><span class="v" style="color:var(--positive)">₹${{(tp.entry_price||0).toLocaleString()}}</span></div>
            <div class="kv"><span class="k">Stop</span><span class="v" style="color:var(--negative)">₹${{(tp.stop_loss||0).toLocaleString()}} (-${{tp.stop_pct||7}}%)</span></div>
            <div class="kv"><span class="k">Position</span><span class="v">${{tp.shares||0}} sh · ₹${{(tp.position_value||0).toLocaleString()}}</span></div>
            <div class="kv"><span class="k">Risk</span><span class="v">₹${{(tp.risk_amount||0).toLocaleString()}}</span></div>
            <div class="kv"><span class="k">1R / 2R / 3R</span><span class="v">₹${{(tp.reward_1r||0).toLocaleString()}} / ₹${{(tp.reward_2r||0).toLocaleString()}} / ₹${{(tp.reward_3r||0).toLocaleString()}}</span></div>
            <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border)">
                <div style="font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted);margin-bottom:6px;font-weight:600">Entry Checklist</div>
                ${{checkH}}
            </div>
            <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
                <div style="font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted);margin-bottom:6px;font-weight:600">Exit Signals</div>
                ${{exitH}}
            </div>
        </div>

        <div class="text-center">
            <a href="https://www.tradingview.com/chart/?symbol=NSE%3A${{d.symbol}}" target="_blank" class="chart-link">Open on TradingView →</a>
        </div>
    `;
}}

// Check staleness of data
fetch('meta.json?t='+Date.now()).then(r=>r.json()).then(m=>{{
    const ageH = (Date.now()/1000 - m.last_updated_epoch)/3600;
    const badge = document.getElementById('updatedBadge');
    if (!badge) return;
    const timeEl = badge.querySelector('.time');
    if (ageH < 1) timeEl.textContent = m.last_updated_display + ' · just now';
    else if (ageH < 24) timeEl.textContent = m.last_updated_display + ' · ' + Math.floor(ageH) + 'h ago';
    else timeEl.textContent = m.last_updated_display + ' · ' + Math.floor(ageH/24) + 'd ago';
    if (ageH > 26) badge.classList.add('stale');
}}).catch(()=>{{}});

document.querySelectorAll(".fbtn").forEach(b=>{{
    b.addEventListener("click",()=>{{
        document.querySelectorAll(".fbtn").forEach(x=>x.classList.remove("on"));
        b.classList.add("on");
        filt=b.dataset.f;
        render();
    }});
}});

document.querySelectorAll("th[data-s]").forEach(th=>{{
    th.addEventListener("click",()=>{{
        let k=th.dataset.s;
        if (sk===k) sa=!sa; else {{ sk=k; sa=false; }}
        render();
    }});
}});

render();
if (D.length>0) sel(0);
</script>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────
# Action Board
# ──────────────────────────────────────────────────────────────

def generate_action_board(setups: list[dict], elapsed: float) -> str:
    now = ist_now_str()

    green = [s for s in setups if s.get("readiness") == "GREEN"]
    yellow = [s for s in setups if s.get("readiness") == "YELLOW"]
    red = [s for s in setups if s.get("readiness") == "RED"]

    def _card(s: dict) -> str:
        tp = s.get("trade_plan", {})
        entry = s.get("entry", {})
        exits = s.get("exits", {})
        r = s.get("readiness", "RED")

        rc = "var(--positive)" if r == "GREEN" else "var(--warn)" if r == "YELLOW" else "var(--negative)"
        c_str = " → ".join(f'{c}%' for c in s["contractions"])

        vol_label = f"Del {s.get('delivery_pct_today', 0)}%" if entry.get("volume_source") == "delivery" else f"Vol {entry.get('last_vol_ratio', 0)}x"
        checks = [
            ("Near Pivot", entry.get("near_pivot") or entry.get("at_pivot")),
            (f"RS {s.get('rs_rating', 0)}", entry.get("rs_pass")),
            ("Earnings", entry.get("earnings_pass")),
            (vol_label, entry.get("volume_confirm")),
            (f"Close {entry.get('close_position', 0)}%", entry.get("strong_close")),
            (f"Inst {s.get('institutional_pct', 0) or 0:.0f}%", entry.get("institutional_pass")),
        ]
        check_html = "".join(
            f'<span class="check {"ok" if ok else "no"}">{name}</span>'
            for name, ok in checks
        )

        exit_html = ""
        exit_items = []
        if exits.get("sell_10sma"):
            exit_items.append(f"Below 10 SMA {exits['below_10sma_days']}d")
        if exits.get("below_50sma"):
            exit_items.append("Below 50 SMA")
        if exits.get("climax_warning"):
            exit_items.append("CLIMAX TOP")
        if exit_items:
            for e in exit_items:
                exit_html += f'<div style="color:var(--negative);font-size:12px;padding:2px 0">⚠ {e}</div>'
        else:
            exit_html = '<div style="color:var(--positive);font-size:12px">No exit signals</div>'

        rg = f"{s.get('revenue_growth', 0) * 100:.0f}%" if s.get('revenue_growth') is not None else "—"
        eg = f"{s.get('earnings_growth', 0) * 100:.0f}%" if s.get('earnings_growth') is not None else "—"
        roe_val = f"{s.get('roe', 0) * 100:.0f}%" if s.get('roe') is not None else "—"
        dte = s.get("days_to_earnings")
        earn_str = f"{dte}d" if dte is not None else "?"

        del_color = "var(--positive)" if (s.get("delivery_pct_today") or 0) >= 60 else "var(--warn)" if (s.get("delivery_pct_today") or 0) >= 40 else "var(--negative)"
        inst_color = "var(--positive)" if (s.get("institutional_pct") or 0) >= 30 else "var(--warn)" if (s.get("institutional_pct") or 0) >= 15 else "var(--negative)"
        deal_tag = ' <span class="pill pill-info">DEAL</span>' if s.get("has_bulk_deal") or s.get("has_block_deal") else ""

        return f"""
        <div class="tc" style="border-color:{rc}">
            <div class="tc-head">
                <div>
                    <div class="tc-sym" style="color:{rc}">{s['symbol']}</div>
                    <div class="tc-meta">{s.get('sector', '')} · {s.get('market_cap_category', '')} Cap</div>
                </div>
                <div class="text-right">
                    <div class="tc-status" style="color:{rc}">{r}</div>
                    <div class="tc-meta">Score {s['score']} · RS {s.get('rs_rating', 0)}</div>
                </div>
            </div>

            <div class="tc-grid3">
                <div class="tc-box">
                    <div class="tc-label">ENTRY</div>
                    <div class="tc-val" style="color:var(--positive)">₹{tp.get('entry_price', 0):,.0f}</div>
                    <div class="tc-sub">{s['pct_from_pivot']}% away</div>
                </div>
                <div class="tc-box">
                    <div class="tc-label">STOP</div>
                    <div class="tc-val" style="color:var(--negative)">₹{tp.get('stop_loss', 0):,.0f}</div>
                    <div class="tc-sub">-{tp.get('stop_pct', 7):.0f}%</div>
                </div>
                <div class="tc-box">
                    <div class="tc-label">POSITION</div>
                    <div class="tc-val">{tp.get('shares', 0)} sh</div>
                    <div class="tc-sub">₹{tp.get('position_value', 0):,.0f}</div>
                </div>
            </div>

            <div class="tc-grid3 targets">
                <div class="tc-box sm"><div class="tc-label">1R</div><div class="tc-val sm" style="color:var(--info)">₹{tp.get('reward_1r', 0):,.0f}</div></div>
                <div class="tc-box sm"><div class="tc-label">2R</div><div class="tc-val sm" style="color:var(--info)">₹{tp.get('reward_2r', 0):,.0f}</div></div>
                <div class="tc-box sm"><div class="tc-label">3R</div><div class="tc-val sm" style="color:var(--positive)">₹{tp.get('reward_3r', 0):,.0f}</div></div>
            </div>

            <div class="tc-info">
                <div>VCP: <span style="color:var(--text)">{c_str} ({s['num_contractions']}T)</span> · Earn: <span style="color:var(--text)">{earn_str}</span> · Rev: <span style="color:var(--text)">{rg}</span> · EPS: <span style="color:var(--text)">{eg}</span> · ROE: <span style="color:var(--text)">{roe_val}</span></div>
                <div>Del: <span style="color:{del_color}">{s.get("delivery_pct_today", "—")}%</span> (avg {s.get("delivery_pct_avg_20d", "—")}%) · Inst: <span style="color:{inst_color}">{s.get("institutional_pct", "—")}%</span>{deal_tag}</div>
            </div>

            <div class="tc-checks">{check_html}</div>
            <div class="tc-exits">{exit_html}</div>

            <div class="text-right" style="margin-top:10px">
                <a href="https://www.tradingview.com/chart/?symbol=NSE%3A{s['symbol']}" target="_blank" class="tv-link">TradingView →</a>
            </div>
        </div>"""

    green_cards = "".join(_card(s) for s in green)
    yellow_cards = "".join(_card(s) for s in yellow)
    # Top 5 reds as a fallback so the page is never empty
    red_top = sorted(red, key=lambda x: x.get("score", 0), reverse=True)[:5]
    red_cards = "".join(_card(s) for s in red_top)

    green_section = ""
    if green:
        green_section = f"""
        <div class="section">
            <h2 class="section-title" style="color:var(--positive)">BUY — {len(green)} ready</h2>
            <p class="section-sub">All 6 entry conditions met. Set alerts at pivot, buy on volume confirmation.</p>
            {green_cards}
        </div>"""
    elif yellow:
        green_section = """
        <div class="empty-state" style="padding:20px">
            <div class="empty-title">No GREEN setups today</div>
            <p class="empty-sub">No stocks pass all 6 entry conditions yet — check WATCH list below.</p>
        </div>"""

    yellow_section = ""
    if yellow:
        yellow_section = f"""
        <div class="section">
            <h2 class="section-title" style="color:var(--warn)">WATCH — {len(yellow)} forming</h2>
            <p class="section-sub">4-5 conditions met. Set price alerts — these could turn GREEN in 1-3 days.</p>
            {yellow_cards}
        </div>"""

    # Fallback: when both GREEN and YELLOW are empty, show top REDs so the page
    # is never blank. RED = setup detected but multiple entry conditions failing,
    # so framed as "monitoring" not "buy".
    red_section = ""
    if not green and not yellow:
        if red_top:
            red_section = f"""
            <div class="empty-state" style="padding:24px;margin-bottom:20px">
                <div class="empty-title">Nothing actionable today</div>
                <p class="empty-sub">Zero GREEN, zero YELLOW. Showing the {len(red_top)} closest setups so you can monitor them — none are ready to act on.</p>
            </div>
            <div class="section">
                <h2 class="section-title" style="color:var(--text-dim)">MONITORING — top {len(red_top)} of {len(red)} red</h2>
                <p class="section-sub">These have a VCP pattern but multiple entry conditions are still failing. They are NOT buy candidates — just watch what's closest to ripening.</p>
                {red_cards}
            </div>"""
        else:
            red_section = """
            <div class="empty-state" style="padding:40px">
                <div class="empty-title">Quiet day — no setups detected</div>
                <p class="empty-sub">The scan ran successfully but no stocks formed a valid VCP pattern today. This happens — markets aren't always set up. Check back tomorrow.</p>
            </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VCP Action Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
{_THEME_CSS}

body {{ padding: 0; }}
.wrap {{ max-width: 720px; margin: 0 auto; padding: 24px 20px; }}

.ab-header {{
    margin-bottom: 28px;
}}
.ab-title {{
    font-weight: 700;
    font-size: 24px;
    letter-spacing: -0.3px;
}}
.ab-title span {{ color: var(--accent); }}
.ab-meta {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 6px;
}}

.ab-stats {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin-bottom: 28px;
}}
.ab-stat {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 14px 16px;
    text-align: center;
}}
.ab-stat .label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); font-weight: 600; }}
.ab-stat .num {{ font-family: 'JetBrains Mono', monospace; font-size: 20px; font-weight: 700; margin-top: 2px; }}

.section {{ margin-bottom: 32px; }}
.section-title {{ font-weight: 700; font-size: 20px; letter-spacing: -0.3px; margin-bottom: 6px; }}
.section-sub {{ font-size: 13px; color: var(--text-dim); margin-bottom: 16px; }}

.empty-state {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 40px;
    text-align: center;
    margin-bottom: 28px;
}}
.empty-title {{ font-weight: 700; font-size: 18px; color: var(--text-dim); }}
.empty-sub {{ color: var(--text-muted); font-size: 13px; margin-top: 6px; }}

.tc {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 3px solid;
    border-radius: var(--radius);
    padding: 20px;
    margin-bottom: 14px;
}}

.tc-head {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 16px;
}}
.tc-sym {{ font-weight: 700; font-size: 22px; letter-spacing: -0.3px; }}
.tc-status {{ font-weight: 700; font-size: 16px; }}
.tc-meta {{ font-size: 12px; color: var(--text-dim); margin-top: 2px; }}

.tc-grid3 {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin-bottom: 12px;
}}
.tc-box {{
    background: var(--surface2);
    border-radius: var(--radius-sm);
    padding: 12px;
    text-align: center;
}}
.tc-box.sm {{ padding: 8px; }}
.tc-label {{
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-muted);
    font-weight: 600;
}}
.tc-val {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 18px;
    font-weight: 600;
    margin-top: 2px;
    color: var(--text);
}}
.tc-val.sm {{ font-size: 14px; }}
.tc-sub {{ font-size: 11px; color: var(--text-dim); margin-top: 1px; }}

.tc-info {{
    background: var(--surface2);
    border-radius: var(--radius-sm);
    padding: 12px;
    margin-bottom: 12px;
    font-size: 12px;
    color: var(--text-dim);
    line-height: 1.6;
}}

.tc-checks {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 8px;
}}
.check {{
    font-size: 11px;
    font-weight: 500;
    padding: 3px 8px;
    border-radius: 4px;
}}
.check.ok {{ background: var(--positive-dim); color: var(--positive); }}
.check.ok::before {{ content: "✓ "; }}
.check.no {{ background: var(--negative-dim); color: var(--negative); }}
.check.no::before {{ content: "✗ "; }}

.tc-exits {{ margin-top: 4px; }}

.tv-link {{
    color: var(--info);
    font-size: 12px;
    text-decoration: none;
    font-weight: 500;
}}
.tv-link:hover {{ text-decoration: underline; }}

.footer-note {{
    text-align: center;
    padding: 32px 0;
    font-size: 12px;
    color: var(--text-muted);
    font-family: 'JetBrains Mono', monospace;
}}
.footer-note a {{ color: var(--info); text-decoration: none; }}

@media (max-width: 500px) {{
    .wrap {{ padding: 16px 14px; }}
    .tc {{ padding: 16px; }}
    .tc-sym {{ font-size: 18px; }}
    .tc-val {{ font-size: 15px; }}
    .tc-val.sm {{ font-size: 12px; }}
    .tc-grid3 {{ gap: 6px; }}
    .tc-box {{ padding: 10px 6px; }}
    .ab-stats {{ grid-template-columns: repeat(3, 1fr); gap: 6px; }}
    .ab-stat {{ padding: 10px 8px; }}
    .ab-stat .num {{ font-size: 16px; }}
    .check {{ font-size: 10px; padding: 2px 6px; }}
}}
</style>
</head>
<body>

<nav class="nav">
    <div class="nav-left">
        <a href="index.html" class="nav-logo">VCP <span>Scanner</span></a>
        <div class="nav-links">
            <a href="action.html" class="active">Action Board</a>
            <a href="dashboard.html">Dashboard</a>
        </div>
    </div>
    <div class="updated" id="updatedBadge" data-updated="{now}">
        <span class="pulse"></span>
        <span class="label">Updated</span>
        <span class="time">{now}</span>
    </div>
</nav>

<div class="wrap">

<div class="ab-header">
    <div class="ab-title">VCP <span>Action Board</span></div>
    <div class="ab-meta">{len(setups)} scanned &middot; scan took {elapsed:.0f}s</div>
</div>

<div class="ab-stats">
    <div class="ab-stat"><div class="label">Portfolio</div><div class="num">₹{config.PORTFOLIO_SIZE / 100000:.0f}L</div></div>
    <div class="ab-stat"><div class="label">Risk/Trade</div><div class="num">₹{config.PORTFOLIO_SIZE * config.RISK_PER_TRADE_PCT:,.0f}</div></div>
    <div class="ab-stat"><div class="label">Stop Loss</div><div class="num" style="color:var(--negative)">{config.STOP_LOSS_PCT * 100:.0f}%</div></div>
</div>

{green_section}
{yellow_section}
{red_section}

<div class="footer-note">
    {len(red)} red setups hidden · <a href="dashboard.html">Full Dashboard →</a>
</div>

</div>

<script>
fetch('meta.json?t='+Date.now()).then(r=>r.json()).then(m=>{{
    const ageH = (Date.now()/1000 - m.last_updated_epoch)/3600;
    const badge = document.getElementById('updatedBadge');
    if (!badge) return;
    const timeEl = badge.querySelector('.time');
    if (ageH < 1) timeEl.textContent = m.last_updated_display + ' · just now';
    else if (ageH < 24) timeEl.textContent = m.last_updated_display + ' · ' + Math.floor(ageH) + 'h ago';
    else timeEl.textContent = m.last_updated_display + ' · ' + Math.floor(ageH/24) + 'd ago';
    if (ageH > 26) badge.classList.add('stale');
}}).catch(()=>{{}});
</script>

</body>
</html>"""


if __name__ == "__main__":
    main()
