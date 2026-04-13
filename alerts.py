"""
Telegram Alert Module — Sends VCP setup alerts with full enrichment context.
"""

import logging
import os
from datetime import datetime
from typing import Optional

import requests

import config

log = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", config.TELEGRAM_BOT_TOKEN)
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", config.TELEGRAM_CHAT_ID)


def _fmt_pct(val: Optional[float], suffix: str = "%") -> str:
    if val is None:
        return "N/A"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.0%}{suffix}" if isinstance(val, float) and abs(val) < 10 else f"{sign}{val}{suffix}"


def _fmt_pct_raw(val: Optional[float]) -> str:
    """Format a raw decimal like 0.18 as +18%."""
    if val is None:
        return "N/A"
    pct = val * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.0f}%"


def _earnings_str(setup: dict) -> str:
    dte = setup.get("days_to_earnings")
    if dte is None:
        return "📅 Earnings: Unknown"
    flag = "✓" if dte >= 14 else "⚠️"
    return f"📅 Earnings: {dte} days away {flag}"


def _sector_str(setup: dict) -> str:
    sector = setup.get("sector", "Unknown")
    vs_nifty = setup.get("sector_vs_nifty_1m")
    if vs_nifty is not None:
        arrow = "↑" if vs_nifty > 0 else "↓"
        return f"🏭 Sector: {sector} ({arrow}{abs(vs_nifty):.1f}% vs Nifty)"
    return f"🏭 Sector: {sector}"


def _fundamentals_str(setup: dict) -> str:
    rg = _fmt_pct_raw(setup.get("revenue_growth"))
    eg = _fmt_pct_raw(setup.get("earnings_growth"))
    roe = _fmt_pct_raw(setup.get("roe"))
    return f"💹 Revenue {rg} | Earnings {eg} | ROE {roe}"


def _delivery_str(setup: dict) -> str:
    today = setup.get("delivery_pct_today")
    avg = setup.get("delivery_pct_avg_20d")
    trend = setup.get("delivery_pct_trend", "")
    if today is None:
        return "📦 Delivery: N/A"
    trend_icon = {"RISING": "↑", "FALLING": "↓", "FLAT": "→"}.get(trend, "")
    avg_str = f" (avg {avg}%)" if avg else ""
    return f"📦 Delivery: {today}%{avg_str} {trend_icon}"


def _institutional_str(setup: dict) -> str:
    inst = setup.get("institutional_pct")
    insider = setup.get("insider_pct")
    if inst is None:
        return "🏦 Institutions: N/A"
    insider_str = f" | Promoter {insider}%" if insider else ""
    return f"🏦 Institutions: {inst}%{insider_str}"


def _flags_str(setup: dict) -> str:
    flags = setup.get("flags", [])
    if not flags:
        return ""
    return f"🏷️ {' | '.join(flags)}"


def format_setup_message(setup: dict, rank: int) -> str:
    """Format a single setup as a Telegram message."""
    sym = setup["symbol"]
    score = setup["score"]
    close = setup["close"]
    pivot = setup["pivot"]
    pct_away = setup["pct_from_pivot"]
    contractions = setup["contractions"]
    num_t = setup["num_contractions"]
    vol_dec = "Yes ✓" if setup["volume_declining"] else "No ✗"
    rs = setup.get("rs_rating", 0)

    # Format contractions like "18.2% → 11.4% → 5.8%"
    c_str = " → ".join(f"{c}%" for c in contractions)

    # Readiness indicator
    readiness = setup.get("readiness", "RED")
    r_icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(readiness, "⚪")

    # Trade plan
    tp = setup.get("trade_plan", {})
    entry_conds = setup.get("entry", {})
    conds_met = entry_conds.get("conditions_met", 0)
    conds_total = entry_conds.get("conditions_total", 5)

    lines = [
        f"{r_icon} #{rank} {sym}  —  Score: {score}  [{readiness}]",
        f"   💰 Close: ₹{close:,.0f}  →  Pivot: ₹{pivot:,.0f}",
        f"   🔥 {pct_away}% away",
        f"   📐 VCP: {c_str}  ({num_t}T)",
        f"   📉 Volume declining: {vol_dec}",
        f"   {_earnings_str(setup)}",
        f"   📊 RS Rating: {rs}th percentile",
        f"   {_sector_str(setup)}",
        f"   {_fundamentals_str(setup)}",
        f"   {_delivery_str(setup)}",
        f"   {_institutional_str(setup)}",
        f"",
        f"   📋 TRADE PLAN ({conds_met}/{conds_total} conditions met)",
        f"   🎯 Entry: ₹{tp.get('entry_price', 0):,.0f}",
        f"   🛑 Stop: ₹{tp.get('stop_loss', 0):,.0f} (-{tp.get('stop_pct', 7):.0f}%)",
        f"   📦 Size: {tp.get('shares', 0)} shares (₹{tp.get('position_value', 0):,.0f} / {tp.get('position_pct', 0)}%)",
        f"   💵 Risk: ₹{tp.get('risk_amount', 0):,.0f}",
        f"   🎯 Targets: 1R=₹{tp.get('reward_1r', 0):,.0f} | 2R=₹{tp.get('reward_2r', 0):,.0f} | 3R=₹{tp.get('reward_3r', 0):,.0f}",
    ]

    # Entry condition checklist
    checks = [
        ("Near Pivot", entry_conds.get("near_pivot") or entry_conds.get("at_pivot")),
        ("RS > 70", entry_conds.get("rs_pass")),
        ("Earnings Clear", entry_conds.get("earnings_pass")),
        (f"Vol {entry_conds.get('last_vol_ratio', 0)}x", entry_conds.get("volume_confirm")),
        (f"Close {entry_conds.get('close_position', 0)}%", entry_conds.get("strong_close")),
        ("Inst Backed", entry_conds.get("institutional_pass")),
    ]
    check_str = "  ".join(f"{'✓' if ok else '✗'}{name}" for name, ok in checks)
    lines.append(f"   {check_str}")

    # Exit warnings
    exits = setup.get("exits", {})
    exit_warnings = []
    if exits.get("sell_10sma"):
        exit_warnings.append(f"Below 10SMA {exits['below_10sma_days']}d")
    if exits.get("below_50sma"):
        exit_warnings.append("Below 50SMA")
    if exits.get("climax_warning"):
        exit_warnings.append("CLIMAX TOP")
    if exit_warnings:
        lines.append(f"   ⚠️ EXIT: {' | '.join(exit_warnings)}")

    flags_line = _flags_str(setup)
    if flags_line:
        lines.append(f"   {flags_line}")

    return "\n".join(lines)


def format_summary_message(setups: list[dict]) -> str:
    """Format the scan summary header."""
    now = datetime.now().strftime("%d %b %Y, %I:%M %p")
    n = len(setups)
    earnings_clear = sum(1 for s in setups if "EARNINGS_CLEAR" in s.get("flags", []))
    tailwind = sum(1 for s in setups if "SECTOR_TAILWIND" in s.get("flags", []))
    avg_score = int(sum(s["score"] for s in setups) / n) if n else 0

    return (
        f"📡 VCP Scanner Report — {now}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔎 Setups found: {n}\n"
        f"✅ Earnings Clear: {earnings_clear}\n"
        f"🌊 Sector Tailwind: {tailwind}\n"
        f"⭐ Avg Score: {avg_score}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )


def send_telegram(text: str) -> bool:
    """Send a message via Telegram bot. Returns True on success."""
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("Telegram credentials not set — skipping alert")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        if resp.status_code != 200:
            log.error(f"Telegram API error: {resp.status_code} {resp.text}")
            return False
        return True
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def send_alerts(setups: list[dict]) -> None:
    """Send all setup alerts to Telegram."""
    if not setups:
        log.info("No setups to alert")
        return

    if not BOT_TOKEN or not CHAT_ID:
        log.info("Telegram credentials not set — skipping alerts")
        return

    # Send summary
    summary = format_summary_message(setups)
    send_telegram(summary)

    # Send individual setups
    for i, setup in enumerate(setups, 1):
        msg = format_setup_message(setup, rank=i)
        send_telegram(msg)

    log.info(f"Sent {len(setups) + 1} Telegram messages")
