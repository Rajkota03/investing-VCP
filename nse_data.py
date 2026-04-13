"""
NSE Direct Data Module — Fetches delivery volume %, bulk/block deals,
and institutional holding data directly from NSE for higher accuracy signals.

Uses jugaad-data for NSE bhavcopy (delivery %) and live trade info.
Uses yfinance for institutional holding percentages.
"""

import logging
import time
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

import config

log = logging.getLogger(__name__)

# Lazy-load jugaad_data to handle import errors gracefully
_nse_live = None
_jugaad_available = None


def _get_nse_live():
    global _nse_live, _jugaad_available
    if _jugaad_available is None:
        try:
            from jugaad_data.nse import NSELive
            _nse_live = NSELive()
            _jugaad_available = True
        except ImportError:
            log.warning("jugaad-data not installed — NSE direct data disabled")
            _jugaad_available = False
    return _nse_live


def _stock_df_safe(symbol: str, from_date: date, to_date: date) -> Optional[pd.DataFrame]:
    """Fetch historical NSE data with delivery columns. Returns None on failure."""
    try:
        from jugaad_data.nse import stock_df
        df = stock_df(symbol, from_date=from_date, to_date=to_date, series="EQ")
        if df is not None and not df.empty:
            return df
    except Exception as e:
        log.debug(f"stock_df failed for {symbol}: {e}")
    return None


# ──────────────────────────────────────────────────────────────
# Delivery Volume %
# ──────────────────────────────────────────────────────────────

def fetch_delivery_data(symbol: str, lookback_days: int = 30) -> dict:
    """
    Fetch delivery volume % from NSE bhavcopy.

    Returns:
        {
            "delivery_pct_today": float or None,
            "delivery_pct_avg_20d": float or None,
            "delivery_pct_trend": "RISING" | "FALLING" | "FLAT" | None,
            "high_delivery_days": int  (days in last 20 with delivery > 60%)
        }
    """
    result = {
        "delivery_pct_today": None,
        "delivery_pct_avg_20d": None,
        "delivery_pct_trend": None,
        "high_delivery_days": 0,
    }

    # Try live data first for today's delivery %
    nse = _get_nse_live()
    if nse:
        try:
            ti = nse.trade_info(symbol)
            dp = ti.get("securityWiseDP", {})
            if dp:
                result["delivery_pct_today"] = dp.get("deliveryToTradedQuantity")
        except Exception as e:
            log.debug(f"Live delivery fetch failed for {symbol}: {e}")

    # Historical delivery data for trend analysis
    to_date = date.today()
    from_date = to_date - timedelta(days=lookback_days + 15)  # buffer for weekends/holidays

    df = _stock_df_safe(symbol, from_date, to_date)
    if df is None or df.empty:
        return result

    # Sort by date ascending
    df = df.sort_values("DATE").reset_index(drop=True)
    delivery_pct = df["DELIVERY %"].values.astype(float)

    if len(delivery_pct) < 5:
        return result

    # Last 20 trading days
    recent = delivery_pct[-20:] if len(delivery_pct) >= 20 else delivery_pct

    result["delivery_pct_avg_20d"] = round(float(np.mean(recent)), 1)
    result["high_delivery_days"] = int(np.sum(recent >= config.DELIVERY_HIGH_PCT))

    # If we didn't get today from live, use last historical
    if result["delivery_pct_today"] is None:
        result["delivery_pct_today"] = round(float(delivery_pct[-1]), 1)

    # Trend: compare last 5 days avg vs previous 10 days avg
    if len(recent) >= 15:
        last_5_avg = np.mean(recent[-5:])
        prev_10_avg = np.mean(recent[-15:-5])
        diff = last_5_avg - prev_10_avg
        if diff > 5:
            result["delivery_pct_trend"] = "RISING"
        elif diff < -5:
            result["delivery_pct_trend"] = "FALLING"
        else:
            result["delivery_pct_trend"] = "FLAT"

    return result


# ──────────────────────────────────────────────────────────────
# Bulk & Block Deals
# ──────────────────────────────────────────────────────────────

def fetch_bulk_block_deals(symbol: str) -> dict:
    """
    Check for recent bulk/block deals from NSE.

    Returns:
        {
            "has_bulk_deal": bool,
            "has_block_deal": bool,
            "bulk_block_details": list[str]  (human-readable summaries)
        }
    """
    result = {
        "has_bulk_deal": False,
        "has_block_deal": False,
        "bulk_block_details": [],
    }

    nse = _get_nse_live()
    if not nse:
        return result

    try:
        ti = nse.trade_info(symbol)
        deals = ti.get("bulkBlockDeals", [])

        for session in deals:
            session_data = session.get("data", [])
            if not session_data:
                continue

            for deal in session_data:
                deal_type = deal.get("dealType", "").upper()
                client = deal.get("clientName", "Unknown")
                qty = deal.get("quantity", 0)
                price = deal.get("avgPrice", 0)
                buy_sell = deal.get("buySell", "")

                if "BULK" in deal_type:
                    result["has_bulk_deal"] = True
                elif "BLOCK" in deal_type:
                    result["has_block_deal"] = True
                else:
                    result["has_bulk_deal"] = True  # default to bulk

                summary = f"{buy_sell} {client}: {qty:,} @ ₹{price:,.0f}"
                result["bulk_block_details"].append(summary)

    except Exception as e:
        log.debug(f"Bulk/block deal fetch failed for {symbol}: {e}")

    return result


# ──────────────────────────────────────────────────────────────
# Institutional Holdings (from yfinance)
# ──────────────────────────────────────────────────────────────

def fetch_institutional_holdings(symbol: str) -> dict:
    """
    Fetch institutional and insider holding percentages.

    Returns:
        {
            "institutional_pct": float or None,  (0-100)
            "insider_pct": float or None,         (0-100, promoter holding proxy)
            "institutional_count": int or None,
        }
    """
    result = {
        "institutional_pct": None,
        "insider_pct": None,
        "institutional_count": None,
    }

    ticker_str = f"{symbol}{config.EXCHANGE_SUFFIX}"
    try:
        t = yf.Ticker(ticker_str)
        info = t.info or {}

        inst = info.get("heldPercentInstitutions")
        if inst is not None and not np.isnan(inst):
            result["institutional_pct"] = round(inst * 100, 1)

        insider = info.get("heldPercentInsiders")
        if insider is not None and not np.isnan(insider):
            result["insider_pct"] = round(insider * 100, 1)

        inst_count = info.get("institutionsCount")
        if inst_count is not None:
            result["institutional_count"] = int(inst_count)

    except Exception as e:
        log.debug(f"Institutional holdings fetch failed for {symbol}: {e}")

    return result


# ──────────────────────────────────────────────────────────────
# Combined NSE Data Fetch
# ──────────────────────────────────────────────────────────────

def fetch_nse_data(symbol: str) -> dict:
    """
    Fetch all NSE direct data for a single symbol.
    Combines delivery %, bulk/block deals, and institutional holdings.
    Designed to be called in a ThreadPoolExecutor.
    """
    data = {}

    # Delivery volume data
    delivery = fetch_delivery_data(symbol)
    data.update(delivery)

    # Small delay to avoid NSE rate limiting
    time.sleep(0.3)

    # Bulk/block deals
    deals = fetch_bulk_block_deals(symbol)
    data.update(deals)

    # Institutional holdings (from yfinance, already fetched in fundamentals
    # but we grab it here if not available)
    holdings = fetch_institutional_holdings(symbol)
    data.update(holdings)

    return data
