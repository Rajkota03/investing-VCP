"""
VCP Scanner Engine — Stage 2 filtering + Volatility Contraction Pattern detection.

Flow:
1. Fetch universe of NSE symbols
2. Download price history via yfinance
3. Apply Stage 2 (SEPA) filters
4. Detect VCP patterns using ZigZag swing analysis
5. Score and rank setups
"""

import logging
import os
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional
from urllib.request import urlopen

import numpy as np
import pandas as pd
import yfinance as yf

import config

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Universe Loading
# ──────────────────────────────────────────────────────────────

def get_nifty_symbols() -> list[str]:
    """Fetch Nifty 500/200 constituent symbols from NSE."""
    cache_path = os.path.join(config.CACHE_DIR, f"{config.UNIVERSE}_symbols.csv")
    os.makedirs(config.CACHE_DIR, exist_ok=True)

    # Use cache if fresh
    if os.path.exists(cache_path):
        age = time.time() - os.path.getmtime(cache_path)
        if age < 86400:  # 24h
            df = pd.read_csv(cache_path)
            return df["Symbol"].tolist()

    # Fetch from NSE website via pandas
    urls = {
        "NIFTY500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
        "NIFTY200": "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
        "NIFTY50": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
    }

    url = urls.get(config.UNIVERSE, urls["NIFTY500"])
    try:
        # Handle macOS SSL cert issues
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        import io
        response = urlopen(url, context=ctx)
        df = pd.read_csv(io.BytesIO(response.read()))
        df.to_csv(cache_path, index=False)
        log.info(f"Fetched {len(df)} symbols for {config.UNIVERSE}")
        return df["Symbol"].tolist()
    except Exception as e:
        log.error(f"Failed to fetch symbol list: {e}")
        # Fallback: try cache even if stale
        if os.path.exists(cache_path):
            df = pd.read_csv(cache_path)
            return df["Symbol"].tolist()
        raise


def fetch_price_data(symbol: str) -> Optional[pd.DataFrame]:
    """Download price history for a single symbol. Returns None on failure."""
    ticker_str = f"{symbol}{config.EXCHANGE_SUFFIX}"
    cache_path = os.path.join(config.CACHE_DIR, "prices", f"{symbol}.parquet")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    # Check cache
    if os.path.exists(cache_path):
        age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_hours < config.CACHE_EXPIRY_HOURS:
            try:
                return pd.read_parquet(cache_path)
            except Exception:
                pass

    try:
        # Use Ticker.history() instead of yf.download() — thread-safe
        ticker = yf.Ticker(ticker_str)
        df = ticker.history(period=f"{config.HISTORY_DAYS}d", timeout=15)

        if df.empty or len(df) < config.SMA_200 + 30:
            return None

        # history() returns clean columns: Open, High, Low, Close, Volume, etc.
        # Drop extra columns if present
        keep_cols = ["Open", "High", "Low", "Close", "Volume"]
        df = df[[c for c in keep_cols if c in df.columns]]

        df.to_parquet(cache_path)
        return df
    except Exception as e:
        log.debug(f"Failed to fetch {symbol}: {e}")
        return None


def fetch_all_prices(symbols: list[str], workers: int = 10) -> dict[str, pd.DataFrame]:
    """Fetch price data for all symbols in parallel."""
    results: dict[str, pd.DataFrame] = {}
    total = len(symbols)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_price_data, s): s for s in symbols}
        done = 0
        for future in as_completed(futures):
            done += 1
            sym = futures[future]
            if done % 50 == 0:
                log.info(f"Price fetch progress: {done}/{total}")
            try:
                df = future.result()
                if df is not None:
                    results[sym] = df
            except Exception as e:
                log.debug(f"Error fetching {sym}: {e}")

    log.info(f"Fetched price data for {len(results)}/{total} symbols")
    return results


# ──────────────────────────────────────────────────────────────
# Stage 2 Filter (Minervini SEPA)
# ──────────────────────────────────────────────────────────────

def apply_stage2_filter(symbol: str, df: pd.DataFrame) -> Optional[dict]:
    """
    Apply Minervini's Stage 2 criteria. Returns a dict with computed metrics
    if the stock passes, None otherwise.
    """
    if len(df) < config.SMA_200 + 30:
        return None

    close = df["Close"].values.astype(float)
    volume = df["Volume"].values.astype(float)
    high = df["High"].values.astype(float)
    low = df["Low"].values.astype(float)

    current_price = close[-1]
    current_volume_avg = np.mean(volume[-50:]) if len(volume) >= 50 else np.mean(volume)

    # Price and volume minimums
    if current_price < config.MIN_PRICE:
        return None
    if current_volume_avg < config.MIN_AVG_VOLUME:
        return None

    # Moving averages
    sma50 = np.mean(close[-config.SMA_50:])
    sma150 = np.mean(close[-config.SMA_150:])
    sma200 = np.mean(close[-config.SMA_200:])

    # Price above all key SMAs
    if current_price < sma50 or current_price < sma150 or current_price < sma200:
        return None

    # SMA alignment: 50 > 150 > 200
    if config.REQUIRE_SMA_ALIGNMENT:
        if not (sma50 > sma150 > sma200):
            return None

    # 200 SMA must be rising
    if config.SMA200_RISING_DAYS > 0:
        sma200_prev = np.mean(close[-(config.SMA_200 + config.SMA200_RISING_DAYS):-config.SMA200_RISING_DAYS])
        if sma200 <= sma200_prev:
            return None

    # 52-week high/low check
    high_252 = np.max(high[-252:]) if len(high) >= 252 else np.max(high)
    low_252 = np.min(low[-252:]) if len(low) >= 252 else np.min(low)

    pct_from_high = (high_252 - current_price) / high_252
    pct_above_low = (current_price - low_252) / low_252 if low_252 > 0 else 0

    if pct_from_high > config.MAX_PCT_FROM_52W_HIGH:
        return None
    if pct_above_low < config.MIN_PCT_ABOVE_52W_LOW:
        return None

    return {
        "symbol": symbol,
        "close": round(current_price, 2),
        "sma50": round(sma50, 2),
        "sma150": round(sma150, 2),
        "sma200": round(sma200, 2),
        "pct_from_high": round(pct_from_high * 100, 1),
        "pct_above_low": round(pct_above_low * 100, 1),
        "avg_volume": int(current_volume_avg),
        "high_52w": round(high_252, 2),
        "low_52w": round(low_252, 2),
    }


# ──────────────────────────────────────────────────────────────
# ZigZag Swing Finder
# ──────────────────────────────────────────────────────────────

def find_zigzag_swings(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                       pct: float) -> list[dict]:
    """
    Identify significant swing highs and lows using a percentage-based ZigZag.
    Returns list of dicts: {"idx": int, "price": float, "type": "H" or "L"}
    """
    if len(close) < 5:
        return []

    swings: list[dict] = []
    last_type = None
    last_price = close[0]
    last_idx = 0

    # Initialize with first bar
    if close[1] > close[0]:
        last_type = "L"
        last_price = low[0]
    else:
        last_type = "H"
        last_price = high[0]

    swings.append({"idx": 0, "price": last_price, "type": last_type})

    for i in range(1, len(close)):
        if last_type == "L":
            # Looking for swing high
            if high[i] >= last_price * (1 + pct):
                swings.append({"idx": i, "price": high[i], "type": "H"})
                last_type = "H"
                last_price = high[i]
                last_idx = i
            elif low[i] < last_price:
                # Lower low — update the last swing low
                swings[-1] = {"idx": i, "price": low[i], "type": "L"}
                last_price = low[i]
                last_idx = i
        else:
            # Looking for swing low
            if low[i] <= last_price * (1 - pct):
                swings.append({"idx": i, "price": low[i], "type": "L"})
                last_type = "L"
                last_price = low[i]
                last_idx = i
            elif high[i] > last_price:
                # Higher high — update the last swing high
                swings[-1] = {"idx": i, "price": high[i], "type": "H"}
                last_price = high[i]
                last_idx = i

    return swings


# ──────────────────────────────────────────────────────────────
# VCP Detection
# ──────────────────────────────────────────────────────────────

def detect_vcp(df: pd.DataFrame) -> Optional[dict]:
    """
    Detect Volatility Contraction Pattern in the lookback window.

    Rules (Minervini-faithful):
    1. Pattern must be CURRENT — the last contraction must end near the latest bar
    2. Each contraction is smaller than the previous (tightening)
    3. No larger contraction can appear AFTER a tightening sequence (that breaks it)
    4. Pivot = highest high across the entire pattern
    5. Current price must be near the pivot (within striking distance)
    6. Volume should decline during the pattern

    Returns dict with VCP metrics or None.
    """
    lookback = min(config.LOOKBACK_BARS, len(df))
    data = df.iloc[-lookback:]

    high = data["High"].values.astype(float)
    low = data["Low"].values.astype(float)
    close = data["Close"].values.astype(float)
    volume = data["Volume"].values.astype(float)
    n_bars = len(close)

    # Find swings
    swings = find_zigzag_swings(high, low, close, config.ZIGZAG_PCT)

    if len(swings) < 4:
        return None

    # Extract all contractions: swing high → following swing low
    all_contractions: list[dict] = []
    for i in range(len(swings) - 1):
        if swings[i]["type"] == "H" and swings[i + 1]["type"] == "L":
            swing_h = swings[i]["price"]
            swing_l = swings[i + 1]["price"]
            if swing_h > 0:
                depth_pct = (swing_h - swing_l) / swing_h
                all_contractions.append({
                    "high": swing_h,
                    "low": swing_l,
                    "depth_pct": depth_pct,
                    "high_idx": swings[i]["idx"],
                    "low_idx": swings[i + 1]["idx"],
                })

    if len(all_contractions) < config.MIN_CONTRACTIONS:
        return None

    current_price = close[-1]

    # RULE 1: The pattern must be CURRENT.
    # The last contraction in any valid VCP must end within the last 15 bars.
    # This prevents detecting old patterns the stock has moved past.
    max_recency_gap = 15

    # Build candidates: find tightening subsequences that END with a recent contraction.
    best: Optional[dict] = None
    best_score = -1

    for end in range(len(all_contractions) - 1, -1, -1):
        # RULE 1 check: last contraction must be recent
        if all_contractions[end]["low_idx"] < n_bars - max_recency_gap:
            continue

        for start in range(max(0, end - config.MAX_CONTRACTIONS + 1), end - config.MIN_CONTRACTIONS + 2):
            sub = all_contractions[start:end + 1]
            depths = [c["depth_pct"] for c in sub]

            # First contraction can't be too wide
            if depths[0] > config.MAX_FIRST_CONTRACTION_PCT:
                continue

            # Last contraction can't be too small (noise)
            if depths[-1] < config.MIN_LAST_CONTRACTION_PCT:
                continue

            # Check tightening: each contraction ≤ ratio * previous
            tightening = True
            for j in range(1, len(depths)):
                if depths[j] > depths[j - 1] * config.CONTRACTION_RATIO_MAX:
                    tightening = False
                    break
            if not tightening:
                continue

            # RULE 2: No LARGER contraction can exist AFTER this sequence
            # within the lookback. If there is one, the VCP was broken.
            pattern_broken = False
            last_depth = depths[-1]
            for later in all_contractions[end + 1:]:
                if later["depth_pct"] > depths[0] * 1.1:
                    # A contraction bigger than the first T happened after — pattern is dead
                    pattern_broken = True
                    break
            if pattern_broken:
                continue

            # RULE 3: Pivot = highest high across ALL contractions in the pattern
            pivot_price = max(c["high"] for c in sub)

            # Also check: the highest high in the data AFTER the pattern shouldn't
            # be much higher than pivot (that means a breakout already happened)
            pattern_end_idx = sub[-1]["low_idx"]
            if pattern_end_idx < n_bars - 1:
                post_pattern_high = np.max(high[pattern_end_idx:])
                if post_pattern_high > pivot_price * 1.03:
                    # Stock already broke out 3%+ past pivot — too late
                    continue

            pct_from_pivot = (pivot_price - current_price) / pivot_price if pivot_price > 0 else 1.0

            # Must be within striking distance of pivot (below or slightly above)
            if pct_from_pivot > config.MAX_PCT_FROM_PIVOT or pct_from_pivot < -0.03:
                continue

            # Score: prefer more contractions, better tightening, closer to pivot
            n_c = len(sub)
            contraction_quality = 1 - np.mean([depths[j] / depths[j - 1]
                                               for j in range(1, len(depths))])
            proximity = 1 - abs(pct_from_pivot) / config.MAX_PCT_FROM_PIVOT
            recency = sub[-1]["low_idx"] / n_bars

            candidate_score = (
                n_c * 15 +                   # more T's = better
                contraction_quality * 30 +    # cleaner tightening = better
                proximity * 25 +              # closer to pivot = better
                recency * 10                  # more recent = better
            )

            if candidate_score > best_score:
                best_score = candidate_score
                best = {
                    "sub": sub,
                    "pivot": pivot_price,
                    "pct_from_pivot": pct_from_pivot,
                }

    if best is None:
        return None

    sub = best["sub"]
    pivot_price = best["pivot"]
    pct_from_pivot = best["pct_from_pivot"]

    # Volume decline check across the pattern span
    pattern_start = sub[0]["high_idx"]
    pattern_end = sub[-1]["low_idx"]
    volume_declining = True
    vol_dry_up_ratio = 1.0

    if (pattern_end - pattern_start) >= 20:
        mid = (pattern_start + pattern_end) // 2
        first_half_vol = np.mean(volume[pattern_start:mid]) if mid > pattern_start else 1
        last_half_vol = np.mean(volume[mid:pattern_end]) if pattern_end > mid else 1
        if first_half_vol > 0:
            vol_dry_up_ratio = last_half_vol / first_half_vol
            volume_declining = vol_dry_up_ratio < config.VOLUME_DECLINE_THRESHOLD

    if config.REQUIRE_VOLUME_DECLINE and not volume_declining:
        return None

    return {
        "contractions": [round(c["depth_pct"] * 100, 1) for c in sub],
        "num_contractions": len(sub),
        "pivot": round(pivot_price, 2),
        "pct_from_pivot": round(pct_from_pivot * 100, 1),
        "volume_declining": volume_declining,
        "vol_dry_up_ratio": round(vol_dry_up_ratio, 2),
        "pattern_bars": sub[-1]["low_idx"] - sub[0]["high_idx"],
    }


# ──────────────────────────────────────────────────────────────
# Scoring
# ──────────────────────────────────────────────────────────────

def score_setup(stage2: dict, vcp: dict) -> int:
    """Score a VCP setup from 0-100 based on quality metrics."""
    score = 0.0
    w = config.SCORE_WEIGHTS

    # Contraction quality: how cleanly each T shrinks
    contraction_vals = vcp["contractions"]
    if len(contraction_vals) >= 2:
        ratios = [contraction_vals[i] / contraction_vals[i - 1]
                  for i in range(1, len(contraction_vals))
                  if contraction_vals[i - 1] > 0]
        if ratios:
            avg_ratio = np.mean(ratios)
            # Perfect ratio is ~0.5 (each T is half the previous), good is < 0.7
            quality = max(0, 1 - avg_ratio) * 2  # scale 0-1
            score += min(quality, 1.0) * w["contraction_quality"]

    # Volume dry up
    vol_ratio = vcp["vol_dry_up_ratio"]
    vol_score = max(0, 1 - vol_ratio)  # lower ratio = better dry up
    score += min(vol_score * 1.5, 1.0) * w["volume_dry_up"]

    # Proximity to pivot (closer = better, 0% = at pivot)
    pct_away = abs(vcp["pct_from_pivot"])
    prox_score = max(0, 1 - pct_away / 5)  # 0% away = 1.0, 5% away = 0.0
    score += prox_score * w["proximity_to_pivot"]

    # SMA alignment bonus
    if stage2["sma50"] > stage2["sma150"] > stage2["sma200"]:
        # How spread are they? Wider spread = stronger trend
        spread = (stage2["sma50"] - stage2["sma200"]) / stage2["sma200"] * 100
        alignment_score = min(spread / 15, 1.0)  # 15% spread = perfect
        score += alignment_score * w["sma_alignment"]

    # Price vs 52-week high
    high_score = max(0, 1 - stage2["pct_from_high"] / 25)
    score += high_score * w["price_vs_high"]

    # Number of contractions: 3-4 is ideal
    n = vcp["num_contractions"]
    if n == 3:
        score += w["num_contractions"]
    elif n == 4:
        score += w["num_contractions"] * 0.9
    elif n == 2:
        score += w["num_contractions"] * 0.7
    else:
        score += w["num_contractions"] * 0.5

    return min(int(round(score)), 100)


# ──────────────────────────────────────────────────────────────
# Main Scan Pipeline
# ──────────────────────────────────────────────────────────────

def run_scan() -> list[dict]:
    """
    Execute the full scan pipeline:
    1. Get universe → 2. Fetch prices → 3. Stage 2 filter → 4. VCP detect → 5. Score

    Returns sorted list of setup dicts (highest score first).
    """
    log.info("Starting VCP scan...")

    # 1. Get symbols
    symbols = get_nifty_symbols()
    log.info(f"Universe: {len(symbols)} symbols")

    # 2. Fetch prices
    prices = fetch_all_prices(symbols)

    # 3. Stage 2 filter
    stage2_passed: list[tuple[str, dict, pd.DataFrame]] = []
    for sym, df in prices.items():
        result = apply_stage2_filter(sym, df)
        if result:
            stage2_passed.append((sym, result, df))

    log.info(f"Stage 2 filter: {len(stage2_passed)}/{len(prices)} passed")

    # 4. VCP detection
    setups: list[dict] = []
    for sym, stage2, df in stage2_passed:
        vcp = detect_vcp(df)
        if vcp:
            s = score_setup(stage2, vcp)
            setup = {
                **stage2,
                **vcp,
                "score": s,
                "flags": [],
            }
            setups.append(setup)

    log.info(f"VCP detection: {len(setups)} setups found")

    # 5. Sort by score
    setups.sort(key=lambda x: x["score"], reverse=True)

    return setups[:config.MAX_RESULTS]
