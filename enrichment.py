"""
Enrichment Layer — Adds fundamental data, earnings context, sector momentum,
and relative strength to VCP setups that already passed detection.

Only called on the ~5-15 stocks that make it through the funnel.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd
import yfinance as yf

import config
from nse_data import fetch_nse_data

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Cache helpers
# ──────────────────────────────────────────────────────────────

def _cache_path(symbol: str) -> str:
    os.makedirs(config.ENRICHMENT_CACHE_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(config.ENRICHMENT_CACHE_DIR, f"{symbol}_{today}.json")


def _read_cache(symbol: str) -> Optional[dict]:
    path = _cache_path(symbol)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _write_cache(symbol: str, data: dict) -> None:
    path = _cache_path(symbol)
    try:
        with open(path, "w") as f:
            json.dump(data, f, default=str)
    except Exception as e:
        log.debug(f"Cache write failed for {symbol}: {e}")


# ──────────────────────────────────────────────────────────────
# Fundamental + Earnings fetch
# ──────────────────────────────────────────────────────────────

def _safe_get(info: dict, key: str, default: Any = None) -> Any:
    """Safely get a value from yfinance info dict."""
    val = info.get(key, default)
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return val


def fetch_fundamentals(symbol: str) -> dict:
    """
    Fetch fundamental data + earnings calendar for a single symbol.
    Returns dict with all enrichment fields. Never raises.
    """
    cached = _read_cache(symbol)
    if cached:
        return cached

    ticker_str = f"{symbol}{config.EXCHANGE_SUFFIX}"
    result: dict = {
        "earnings_date": None,
        "days_to_earnings": None,
        "earnings_flag": "UNKNOWN",
        "revenue_growth": None,
        "earnings_growth": None,
        "roe": None,
        "profit_margins": None,
        "market_cap": None,
        "market_cap_category": "Unknown",
        "sector": None,
        "industry": None,
    }

    retries = 3
    for attempt in range(retries):
        try:
            ticker = yf.Ticker(ticker_str)
            info = ticker.info or {}

            # Fundamentals
            result["revenue_growth"] = _safe_get(info, "revenueGrowth")
            result["earnings_growth"] = _safe_get(info, "earningsGrowth")
            result["profit_margins"] = _safe_get(info, "profitMargins")

            # ROE: try info first, then compute from financials
            roe = _safe_get(info, "returnOnEquity")
            if roe is None:
                try:
                    bs = ticker.balance_sheet
                    inc = ticker.income_stmt
                    if bs is not None and not bs.empty and inc is not None and not inc.empty:
                        eq_keys = [k for k in bs.index if "Stockholders Equity" in str(k)]
                        ni_keys = [k for k in inc.index if k == "Net Income"]
                        if eq_keys and ni_keys:
                            eq = float(bs.loc[eq_keys[0]].iloc[0])
                            ni = float(inc.loc[ni_keys[0]].iloc[0])
                            if eq > 0:
                                roe = ni / eq
                except Exception:
                    pass
            result["roe"] = roe

            mc = _safe_get(info, "marketCap", 0)
            result["market_cap"] = mc
            if mc and mc > 0:
                if mc >= 50_000_000_000:  # 50K Cr+ (approx)
                    result["market_cap_category"] = "Large"
                elif mc >= 10_000_000_000:
                    result["market_cap_category"] = "Mid"
                else:
                    result["market_cap_category"] = "Small"

            result["sector"] = _safe_get(info, "sector")
            result["industry"] = _safe_get(info, "industry")

            # Earnings date
            try:
                cal = ticker.calendar
                if cal is not None and isinstance(cal, dict):
                    ed = cal.get("Earnings Date")
                    if ed:
                        from datetime import date
                        today = date.today()
                        dates = ed if isinstance(ed, list) else [ed]
                        # Filter to future dates
                        future = [d for d in dates if isinstance(d, date) and d >= today]
                        if future:
                            next_date = min(future)
                            result["earnings_date"] = next_date.strftime("%Y-%m-%d")
                            result["days_to_earnings"] = (next_date - today).days
            except Exception as e:
                log.debug(f"Earnings calendar parse failed for {symbol}: {e}")

            # Set earnings flag
            dte = result["days_to_earnings"]
            if dte is not None:
                if dte < config.EARNINGS_EXCLUDE_DAYS:
                    result["earnings_flag"] = "EXCLUDE"
                elif dte < config.EARNINGS_WARNING_DAYS:
                    result["earnings_flag"] = "WARNING"
                elif dte >= config.EARNINGS_SAFE_DAYS:
                    result["earnings_flag"] = "SAFE"
                else:
                    result["earnings_flag"] = "CLEAR"
            else:
                result["earnings_flag"] = "UNKNOWN"

            _write_cache(symbol, result)
            return result

        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                log.debug(f"Retry {attempt + 1} for {symbol} after {wait}s: {e}")
                time.sleep(wait)
            else:
                log.warning(f"Failed to fetch fundamentals for {symbol}: {e}")

    return result


# ──────────────────────────────────────────────────────────────
# Relative Strength
# ──────────────────────────────────────────────────────────────

def compute_relative_strength(setups: list[dict], prices: dict[str, pd.DataFrame]) -> list[dict]:
    """
    Compute RS rating for each setup:
    - Stock returns over 1m, 3m, 6m, 12m
    - Compare to Nifty 50
    - Percentile rank among all scanned stocks
    """
    # Fetch Nifty 50 data for comparison
    nifty_returns = _get_index_returns(config.NIFTY_50_INDEX)

    # Compute returns for all setups
    for setup in setups:
        sym = setup["symbol"]
        df = prices.get(sym)
        if df is None or len(df) < 252:
            setup["returns_1m"] = None
            setup["returns_3m"] = None
            setup["returns_6m"] = None
            setup["returns_12m"] = None
            setup["rs_rating"] = 0
            continue

        close = df["Close"].values.astype(float)
        setup["returns_1m"] = _pct_return(close, 22)
        setup["returns_3m"] = _pct_return(close, 63)
        setup["returns_6m"] = _pct_return(close, 126)
        setup["returns_12m"] = _pct_return(close, 252)

        # Relative vs Nifty
        setup["nifty_1m"] = nifty_returns.get("1m")
        setup["nifty_3m"] = nifty_returns.get("3m")
        setup["nifty_6m"] = nifty_returns.get("6m")
        setup["nifty_12m"] = nifty_returns.get("12m")

    # Compute weighted composite for ranking
    composites = []
    for setup in setups:
        r3 = setup.get("returns_3m")
        r6 = setup.get("returns_6m")
        r12 = setup.get("returns_12m")
        if r3 is not None and r6 is not None and r12 is not None:
            comp = (r3 * config.RS_WEIGHTS["3m"] +
                    r6 * config.RS_WEIGHTS["6m"] +
                    r12 * config.RS_WEIGHTS["12m"])
            composites.append(comp)
        else:
            composites.append(None)

    # Rank as percentile
    valid = [c for c in composites if c is not None]
    if valid:
        sorted_vals = sorted(valid)
        for i, setup in enumerate(setups):
            if composites[i] is not None:
                rank = sorted_vals.index(composites[i]) + 1
                setup["rs_rating"] = int(round(rank / len(sorted_vals) * 100))
            else:
                setup["rs_rating"] = 0

    return setups


def _pct_return(close: np.ndarray, periods: int) -> Optional[float]:
    if len(close) < periods + 1:
        return None
    old = close[-(periods + 1)]
    new = close[-1]
    if old > 0:
        return round((new - old) / old * 100, 1)
    return None


def _get_index_returns(ticker: str) -> dict:
    """Get Nifty 50 returns for comparison periods."""
    try:
        t = yf.Ticker(ticker)
        df = t.history(period="400d", timeout=15)
        if df.empty:
            return {}
        close = df["Close"].values.astype(float)
        return {
            "1m": _pct_return(close, 22),
            "3m": _pct_return(close, 63),
            "6m": _pct_return(close, 126),
            "12m": _pct_return(close, 252),
        }
    except Exception as e:
        log.warning(f"Failed to fetch index returns: {e}")
        return {}


# ──────────────────────────────────────────────────────────────
# Sector Momentum
# ──────────────────────────────────────────────────────────────

def compute_sector_momentum(setups: list[dict], prices: dict[str, pd.DataFrame]) -> list[dict]:
    """
    Compute sector relative performance vs Nifty 50.
    Uses average returns of stocks in the same sector from our scanned universe.
    """
    # Get Nifty returns for comparison
    nifty_returns = _get_index_returns(config.NIFTY_50_INDEX)
    nifty_1w = nifty_returns.get("1m", 0) or 0  # approximate 1w from 1m
    nifty_1m = nifty_returns.get("1m", 0) or 0

    # Fetch Nifty 1-week return separately
    try:
        t_n = yf.Ticker(config.NIFTY_50_INDEX)
        df_n = t_n.history(period="10d", timeout=10)
        if not df_n.empty:
            nc = df_n["Close"].values.astype(float)
            nifty_1w = _pct_return(nc, config.SECTOR_COMPARISON_DAYS_1W) or 0
    except Exception:
        pass

    # Group stocks by sector and compute average performance
    sector_stocks: dict[str, list[float]] = {}
    for sym, df in prices.items():
        if len(df) < 30:
            continue
        close = df["Close"].values.astype(float)
        ret_1m = _pct_return(close, config.SECTOR_COMPARISON_DAYS_1M)
        if ret_1m is None:
            continue

        # Find sector for this stock from setups
        sector = None
        for s in setups:
            if s["symbol"] == sym:
                sector = s.get("sector")
                break
        if sector:
            sector_stocks.setdefault(sector, []).append(ret_1m)

    # Compute sector averages
    sector_avg: dict[str, float] = {}
    for sector, returns in sector_stocks.items():
        sector_avg[sector] = round(np.mean(returns), 1)

    # Attach to setups
    for setup in setups:
        sector = setup.get("sector")
        if sector and sector in sector_avg:
            avg = sector_avg[sector]
            vs_nifty = round(avg - nifty_1m, 1)
            setup["sector_perf_1m"] = avg
            setup["sector_vs_nifty_1m"] = vs_nifty

            if vs_nifty > 0:
                setup["sector_momentum"] = "TAILWIND"
            elif vs_nifty < config.SECTOR_HEADWIND_THRESHOLD * 100:
                setup["sector_momentum"] = "HEADWIND"
            else:
                setup["sector_momentum"] = "NEUTRAL"
        else:
            setup["sector_perf_1m"] = None
            setup["sector_vs_nifty_1m"] = None
            setup["sector_momentum"] = "UNKNOWN"

    return setups


# ──────────────────────────────────────────────────────────────
# Volume Interest Detection
# ──────────────────────────────────────────────────────────────

def check_volume_interest(setups: list[dict], prices: dict[str, pd.DataFrame]) -> list[dict]:
    """Check if any recent day had volume > 1.5x the 50-day average."""
    for setup in setups:
        sym = setup["symbol"]
        df = prices.get(sym)
        if df is None or len(df) < 55:
            setup["volume_interest"] = False
            continue

        volume = df["Volume"].values.astype(float)
        avg_50 = np.mean(volume[-55:-5])  # 50-day avg excluding last 5
        recent = volume[-config.VOLUME_INTEREST_LOOKBACK:]
        threshold = avg_50 * config.VOLUME_INTEREST_MULTIPLIER

        setup["volume_interest"] = bool(np.any(recent > threshold))
        if setup["volume_interest"]:
            max_vol_day = int(np.argmax(recent))
            setup["volume_spike_ratio"] = round(float(recent[max_vol_day] / avg_50), 1)

    return setups


# ──────────────────────────────────────────────────────────────
# Validation & Flag Assignment
# ──────────────────────────────────────────────────────────────

def validate_and_flag(setups: list[dict]) -> list[dict]:
    """
    Run pre-validation checks and assign flags to each setup.
    Excludes stocks with earnings < 7 days.
    """
    valid_setups = []

    for setup in setups:
        flags: list[str] = []

        # Earnings proximity
        dte = setup.get("days_to_earnings")
        earnings_flag = setup.get("earnings_flag", "UNKNOWN")

        if earnings_flag == "EXCLUDE":
            log.info(f"Excluding {setup['symbol']}: earnings in {dte} days")
            continue  # skip entirely

        if earnings_flag == "WARNING":
            flags.append("EARNINGS_WARNING")
        elif earnings_flag == "SAFE":
            flags.append("EARNINGS_CLEAR")
        elif earnings_flag == "CLEAR":
            flags.append("EARNINGS_CLEAR")
        else:
            flags.append("EARNINGS_UNKNOWN")

        # Fundamental quality
        weak = False
        rg = setup.get("revenue_growth")
        eg = setup.get("earnings_growth")
        roe = setup.get("roe")
        if rg is not None and rg < 0:
            weak = True
        if eg is not None and eg < 0:
            weak = True
        if roe is not None and roe < config.MIN_ROE:
            weak = True
        if weak:
            flags.append("WEAK_FUNDAMENTALS")
        elif rg is not None and eg is not None and rg > 0 and eg > 0:
            flags.append("STRONG_FUNDAMENTALS")

        # Sector momentum
        sm = setup.get("sector_momentum", "UNKNOWN")
        if sm == "TAILWIND":
            flags.append("SECTOR_TAILWIND")
        elif sm == "HEADWIND":
            flags.append("SECTOR_HEADWIND")

        # Volume interest
        if setup.get("volume_interest"):
            flags.append("VOLUME_INTEREST")

        # NSE delivery volume signals
        del_pct = setup.get("delivery_pct_avg_20d")
        if del_pct is not None:
            if del_pct >= config.DELIVERY_HIGH_PCT:
                flags.append("HIGH_DELIVERY")
            elif del_pct < config.DELIVERY_LOW_PCT:
                flags.append("LOW_DELIVERY")

        # Delivery trend — rising delivery % means real accumulation
        if setup.get("delivery_pct_trend") == "RISING":
            flags.append("DELIVERY_RISING")

        # Institutional backing
        inst_pct = setup.get("institutional_pct")
        if inst_pct is not None:
            if inst_pct >= config.INSTITUTIONAL_HIGH_PCT:
                flags.append("INSTITUTIONAL_BACKED")
            elif inst_pct < config.INSTITUTIONAL_MIN_PCT:
                flags.append("LOW_INSTITUTIONAL")

        # Bulk/block deals — big money entering
        if setup.get("has_bulk_deal") or setup.get("has_block_deal"):
            flags.append("BULK_BLOCK_DEAL")

        setup["flags"] = flags
        valid_setups.append(setup)

    return valid_setups


# ──────────────────────────────────────────────────────────────
# Trade Plan
# ──────────────────────────────────────────────────────────────

def compute_trade_plans(setups: list[dict], prices: dict[str, pd.DataFrame]) -> list[dict]:
    """
    For each setup, compute a full trade plan:
    - Entry conditions (all must be true to act)
    - Stop loss price
    - Position size
    - Exit signals from recent price action
    - Overall readiness: GREEN / YELLOW / RED
    """
    for setup in setups:
        sym = setup["symbol"]
        df = prices.get(sym)
        pivot = setup["pivot"]
        current_price = setup["close"]

        # ── Entry conditions ──
        entry = {}

        # 1. Price within striking distance of pivot (below, not already past)
        pct_away = setup["pct_from_pivot"]
        entry["near_pivot"] = 0 < pct_away <= 5  # between 0-5% below pivot
        entry["at_pivot"] = -1 <= pct_away <= 1   # within 1% of pivot

        # 2. RS Rating check
        rs = setup.get("rs_rating", 0)
        entry["rs_pass"] = rs >= config.ENTRY_MIN_RS

        # 3. Earnings check
        dte = setup.get("days_to_earnings")
        entry["earnings_pass"] = dte is None or dte >= config.ENTRY_MIN_EARNINGS_DAYS

        # 4. Volume confirmation — use delivery % when available, fall back to raw volume
        del_pct_today = setup.get("delivery_pct_today")
        del_avg = setup.get("delivery_pct_avg_20d")
        if del_pct_today is not None and del_avg is not None:
            # Delivery-based: today's delivery % must be above average AND above threshold
            entry["volume_confirm"] = bool(
                del_pct_today >= del_avg and del_pct_today >= config.DELIVERY_HIGH_PCT * 0.8
            )
            entry["last_vol_ratio"] = round(del_pct_today / del_avg, 1) if del_avg > 0 else 0
            entry["volume_source"] = "delivery"
        elif df is not None and len(df) >= 50:
            vol = df["Volume"].values.astype(float)
            vol_50_avg = np.mean(vol[-50:])
            last_vol = vol[-1]
            entry["volume_confirm"] = bool(last_vol > vol_50_avg * config.ENTRY_VOLUME_MULTIPLIER)
            entry["last_vol_ratio"] = round(float(last_vol / vol_50_avg), 1) if vol_50_avg > 0 else 0
            entry["volume_source"] = "raw"
        else:
            entry["volume_confirm"] = False
            entry["last_vol_ratio"] = 0
            entry["volume_source"] = "none"

        # 5. Close in upper 25% of day's range
        if df is not None and len(df) > 0:
            last_high = float(df["High"].values[-1])
            last_low = float(df["Low"].values[-1])
            last_close = float(df["Close"].values[-1])
            day_range = last_high - last_low
            if day_range > 0:
                close_position = (last_close - last_low) / day_range
                entry["strong_close"] = close_position >= config.ENTRY_CLOSE_RANGE_PCT
                entry["close_position"] = round(close_position * 100)
            else:
                entry["strong_close"] = False
                entry["close_position"] = 50
        else:
            entry["strong_close"] = False
            entry["close_position"] = 50

        # 6. Institutional holding check
        inst = setup.get("institutional_pct")
        entry["institutional_pass"] = inst is not None and inst >= config.INSTITUTIONAL_MIN_PCT

        # Count how many entry conditions are met
        conditions = [entry["near_pivot"] or entry["at_pivot"],
                      entry["rs_pass"], entry["earnings_pass"],
                      entry["volume_confirm"], entry["strong_close"],
                      entry["institutional_pass"]]
        entry["conditions_met"] = sum(conditions)
        entry["conditions_total"] = len(conditions)

        setup["entry"] = entry

        # ── Stop loss & position sizing ──
        stop_price = round(pivot * (1 - config.STOP_LOSS_PCT), 2)
        risk_per_share = pivot - stop_price
        risk_amount = config.PORTFOLIO_SIZE * config.RISK_PER_TRADE_PCT

        if risk_per_share > 0:
            shares = int(risk_amount / risk_per_share)
            position_value = round(shares * pivot)
            position_pct = round(position_value / config.PORTFOLIO_SIZE * 100, 1)
        else:
            shares = 0
            position_value = 0
            position_pct = 0

        setup["trade_plan"] = {
            "entry_price": pivot,
            "stop_loss": stop_price,
            "stop_pct": config.STOP_LOSS_PCT * 100,
            "risk_per_share": round(risk_per_share, 2),
            "shares": shares,
            "position_value": position_value,
            "position_pct": position_pct,
            "risk_amount": round(risk_amount),
            "reward_1r": round(pivot + risk_per_share, 2),     # 1:1 R
            "reward_2r": round(pivot + 2 * risk_per_share, 2), # 2:1 R
            "reward_3r": round(pivot + 3 * risk_per_share, 2), # 3:1 R
        }

        # ── Exit signals (from current price action) ──
        exits = {}

        if df is not None and len(df) >= 50:
            close = df["Close"].values.astype(float)

            # 1. Close below 10 SMA streak
            sma10 = np.mean(close[-config.EXIT_SMA_SHORT:])
            below_10sma_days = 0
            for i in range(1, min(10, len(close))):
                sma = np.mean(close[-(config.EXIT_SMA_SHORT + i):-i]) if len(close) > config.EXIT_SMA_SHORT + i else sma10
                if close[-i] < sma:
                    below_10sma_days += 1
                else:
                    break
            exits["below_10sma_days"] = below_10sma_days
            exits["sell_10sma"] = below_10sma_days >= config.EXIT_SMA_SHORT_DAYS

            # 2. Close below 50 SMA
            sma50 = np.mean(close[-config.EXIT_SMA_MEDIUM:])
            exits["below_50sma"] = bool(close[-1] < sma50)

            # 3. Climax top detection
            # Check if the most recent up day was abnormally large + high volume
            recent_close = close[-61:]
            daily_returns = np.diff(recent_close) / recent_close[:-1] * 100
            vol_60 = df["Volume"].values.astype(float)[-60:]
            vol_avg = np.mean(vol_60)

            if len(daily_returns) > 0:
                threshold = np.percentile(daily_returns, config.CLIMAX_GAIN_PERCENTILE * 100)
                last_return = daily_returns[-1]
                last_vol = vol_60[-1]
                exits["climax_warning"] = bool(
                    last_return > threshold and
                    last_vol > vol_avg * config.CLIMAX_VOLUME_MULTIPLIER
                )
                exits["last_daily_return"] = round(float(last_return), 1)
            else:
                exits["climax_warning"] = False
                exits["last_daily_return"] = 0
        else:
            exits["below_10sma_days"] = 0
            exits["sell_10sma"] = False
            exits["below_50sma"] = False
            exits["climax_warning"] = False
            exits["last_daily_return"] = 0

        setup["exits"] = exits

        # ── Overall readiness ──
        # GREEN: 5-6 entry conditions met, no exit signals
        # YELLOW: 4 conditions met, or minor exit concern
        # RED: <4 conditions met, or active exit signal
        has_exit = exits.get("sell_10sma") or exits.get("below_50sma") or exits.get("climax_warning")
        met = entry["conditions_met"]

        if has_exit:
            setup["readiness"] = "RED"
        elif met >= 5:
            setup["readiness"] = "GREEN"
        elif met >= 4:
            setup["readiness"] = "YELLOW"
        else:
            setup["readiness"] = "RED"

    return setups


# ──────────────────────────────────────────────────────────────
# Main enrichment pipeline
# ──────────────────────────────────────────────────────────────

def enrich_setups(setups: list[dict], prices: dict[str, pd.DataFrame]) -> list[dict]:
    """
    Full enrichment pipeline:
    1. Fetch fundamentals + earnings in parallel
    2. Compute relative strength
    3. Compute sector momentum
    4. Check volume interest
    5. Validate and assign flags
    """
    if not setups:
        return setups

    log.info(f"Enriching {len(setups)} setups...")

    # 1. Parallel fundamental fetch
    symbols = [s["symbol"] for s in setups]
    fund_data: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=config.ENRICHMENT_WORKERS) as pool:
        futures = {pool.submit(fetch_fundamentals, sym): sym for sym in symbols}
        for future in as_completed(futures):
            sym = futures[future]
            try:
                fund_data[sym] = future.result()
            except Exception as e:
                log.warning(f"Enrichment failed for {sym}: {e}")
                fund_data[sym] = {}

    # Merge fundamental data into setups
    for setup in setups:
        sym = setup["symbol"]
        fd = fund_data.get(sym, {})
        for key, val in fd.items():
            setup[key] = val

    # 1b. Parallel NSE direct data fetch (delivery %, bulk/block deals, holdings)
    log.info("Fetching NSE direct data (delivery %, deals, holdings)...")
    nse_data: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        nse_futures = {pool.submit(fetch_nse_data, sym): sym for sym in symbols}
        for future in as_completed(nse_futures):
            sym = nse_futures[future]
            try:
                nse_data[sym] = future.result()
            except Exception as e:
                log.warning(f"NSE data failed for {sym}: {e}")
                nse_data[sym] = {}

    for setup in setups:
        sym = setup["symbol"]
        nd = nse_data.get(sym, {})
        for key, val in nd.items():
            setup[key] = val

    # 2. Relative strength
    setups = compute_relative_strength(setups, prices)

    # 3. Sector momentum
    setups = compute_sector_momentum(setups, prices)

    # 4. Volume interest
    setups = check_volume_interest(setups, prices)

    # 5. Validate and flag
    setups = validate_and_flag(setups)

    # 6. Trade plans
    setups = compute_trade_plans(setups, prices)

    log.info(f"Enrichment complete: {len(setups)} setups after validation")
    return setups
