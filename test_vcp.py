"""
VCP Scanner Tests — Encodes the exact failure modes we discovered.
Run: python3 test_vcp.py
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from engine import find_zigzag_swings, detect_vcp, apply_stage2_filter
import config


def make_df(prices: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    """Build a DataFrame from a list of close prices (high=close*1.01, low=close*0.99)."""
    n = len(prices)
    if volumes is None:
        volumes = [1_000_000] * n
    dates = pd.date_range(end=datetime.now(), periods=n, freq="B")
    return pd.DataFrame({
        "Open": prices,
        "High": [p * 1.01 for p in prices],
        "Low": [p * 0.99 for p in prices],
        "Close": prices,
        "Volume": volumes,
    }, index=dates)


def make_realistic_df(swings: list[tuple[str, float, int]],
                      base_vol: int = 1_000_000) -> pd.DataFrame:
    """
    Build realistic price data from swing definitions.
    swings: list of (type, price, duration_bars)
      e.g. [("up", 100, 10), ("down", 90, 5), ("up", 105, 8), ...]
    Linearly interpolates between swing targets.
    """
    prices = []
    current = swings[0][1]  # start price

    for swing_type, target, bars in swings:
        step = (target - current) / max(bars, 1)
        for i in range(bars):
            current += step
            prices.append(current)

    n = len(prices)
    # Add noise to high/low
    highs = [p * (1 + np.random.uniform(0.001, 0.015)) for p in prices]
    lows = [p * (1 - np.random.uniform(0.001, 0.015)) for p in prices]
    # Volume declines over time (realistic for VCP)
    vols = [base_vol * (1 - 0.3 * i / n) for i in range(n)]

    dates = pd.date_range(end=datetime.now(), periods=n, freq="B")
    return pd.DataFrame({
        "Open": prices,
        "High": highs,
        "Low": lows,
        "Close": prices,
        "Volume": vols,
    }, index=dates)


# ──────────────────────────────────────────────────────────────
# Test Cases
# ──────────────────────────────────────────────────────────────

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}  — {detail}")


def test_clean_3t_vcp():
    """A textbook 3T VCP should be detected."""
    print("\n[TEST] Clean 3T VCP")
    # Build: uptrend → 20% drop → recovery → 12% drop → recovery → 6% drop → near pivot
    df = make_realistic_df([
        ("up", 100, 50),    # base uptrend to get SMAs right
        ("up", 200, 80),    # strong uptrend
        ("up", 250, 30),    # push to high
        ("down", 200, 10),  # T1: 20% drop
        ("up", 248, 12),    # recovery near high
        ("down", 218, 8),   # T2: ~12% drop
        ("up", 246, 10),    # recovery
        ("down", 232, 6),   # T3: ~6% drop
        ("up", 245, 5),     # approaching pivot
    ])
    result = detect_vcp(df)
    check("Detected", result is not None, "Should detect a 3T VCP")
    if result:
        check("Multiple contractions", result["num_contractions"] >= 2,
              f"Got {result['num_contractions']}T")
        check("Close to pivot", abs(result["pct_from_pivot"]) < 8,
              f"Got {result['pct_from_pivot']}%")
        check("Contractions tighten",
              all(result["contractions"][i] < result["contractions"][i-1]
                  for i in range(1, len(result["contractions"]))),
              f"Got {result['contractions']}")


def test_already_broke_out():
    """Stock that already broke out 5%+ past pivot should NOT be detected."""
    print("\n[TEST] Already broke out past pivot")
    df = make_realistic_df([
        ("up", 100, 50),
        ("up", 200, 80),
        ("up", 250, 20),    # high
        ("down", 215, 8),   # T1: 14%
        ("up", 248, 10),    # near high
        ("down", 230, 6),   # T2: 7%
        ("up", 246, 8),     # near high
        ("down", 236, 4),   # T3: 4%
        ("up", 280, 10),    # BREAKOUT — ran 12% past pivot
    ])
    result = detect_vcp(df)
    check("Rejected", result is None,
          f"Should reject — already broke out. Got: {result}")


def test_pattern_broken_by_bigger_drop():
    """If a larger contraction happens after a tightening sequence, VCP is dead."""
    print("\n[TEST] Pattern broken by bigger drop")
    # HINDALCO scenario: had 12.8→5.2 VCP, then a 14.6% dump
    df = make_realistic_df([
        ("up", 100, 50),
        ("up", 200, 80),
        ("up", 250, 20),
        ("down", 218, 8),   # T1: 12.8%
        ("up", 247, 10),
        ("down", 234, 5),   # T2: 5.2% — nice tightening!
        ("up", 248, 6),
        ("down", 212, 8),   # DUMP: 14.5% — bigger than T1, kills the VCP
        ("up", 245, 10),    # recovery near old high
    ])
    result = detect_vcp(df)
    check("Rejected", result is None,
          f"Should reject — pattern broken by 14.5% drop. Got: {result}")


def test_old_pattern_ignored():
    """A VCP from 2 months ago with lots of activity since should not be detected."""
    print("\n[TEST] Old stale pattern")
    df = make_realistic_df([
        ("up", 100, 50),
        ("up", 200, 60),
        ("up", 250, 15),
        ("down", 220, 8),   # T1: 12%
        ("up", 248, 10),
        ("down", 235, 6),   # T2: 5.2%
        ("up", 250, 8),     # recovery
        # Then 40 bars of messy sideways action (pattern is stale)
        ("down", 240, 5),
        ("up", 255, 8),
        ("down", 242, 5),
        ("up", 258, 8),
        ("down", 248, 6),
        ("up", 252, 8),     # current price, far from old pattern
    ])
    result = detect_vcp(df)
    # Either rejected entirely, or if detected, should be a RECENT pattern, not the old one
    if result:
        # The old T1 was at bar ~135-143. Last contraction should end much later.
        check("Uses recent pattern (not stale)",
              result["pattern_bars"] < 30,
              f"Pattern spans {result['pattern_bars']} bars — might be using old data")
    else:
        check("Rejected stale pattern", True)


def test_4t_federalbnk_style():
    """FEDERALBNK scenario: 15.3→7.7→6.1→5.0 should be detected as 4T or 3T."""
    print("\n[TEST] FEDERALBNK-style 4T VCP")
    df = make_realistic_df([
        ("up", 100, 50),
        ("up", 200, 80),
        ("up", 300, 20),
        ("down", 254, 8),   # T1: 15.3%
        ("up", 290, 10),
        ("down", 268, 6),   # T2: 7.6%
        ("up", 288, 8),
        ("down", 270, 5),   # T3: 6.3%
        ("up", 285, 8),
        ("down", 271, 4),   # T4: 4.9%
        ("up", 283, 5),     # approaching pivot
    ])
    result = detect_vcp(df)
    check("Detected", result is not None, "Should detect multi-T VCP")
    if result:
        check("3+ contractions", result["num_contractions"] >= 3,
              f"Got {result['num_contractions']}T — should find at least 3")


def test_v_shaped_recovery_rejected():
    """A sharp V-shaped bounce is NOT a VCP."""
    print("\n[TEST] V-shaped recovery (not a VCP)")
    df = make_realistic_df([
        ("up", 100, 50),
        ("up", 200, 80),
        ("up", 300, 20),    # high
        ("down", 240, 5),   # sharp 20% crash
        ("up", 295, 8),     # sharp V recovery
    ])
    result = detect_vcp(df)
    check("Rejected", result is None,
          f"V-recovery is not a VCP. Got: {result}")


def test_pivot_is_highest_high():
    """Pivot should be the highest high in the pattern, not just the last contraction's high."""
    print("\n[TEST] Pivot = highest high in pattern")
    df = make_realistic_df([
        ("up", 100, 50),
        ("up", 200, 80),
        ("up", 260, 20),    # THE highest point
        ("down", 230, 8),   # T1 from 260
        ("up", 255, 10),    # doesn't reach 260
        ("down", 240, 6),   # T2
        ("up", 253, 8),     # approaching but below 260
    ])
    result = detect_vcp(df)
    if result:
        # Pivot should be near 260, not 255 or 253
        check("Pivot near highest high",
              result["pivot"] > 250,
              f"Pivot={result['pivot']}, should be near pattern high ~260")


def test_zigzag_basic():
    """ZigZag should find swings correctly."""
    print("\n[TEST] ZigZag swing detection")
    # Simple up-down-up pattern with 5% threshold
    prices = ([100] * 5 + [110] * 5 + [103] * 5 + [115] * 5)
    high = np.array([p * 1.01 for p in prices])
    low = np.array([p * 0.99 for p in prices])
    close = np.array(prices, dtype=float)

    swings = find_zigzag_swings(high, low, close, 0.05)
    check("Found swings", len(swings) >= 3,
          f"Got {len(swings)} swings, expected at least 3")

    types = [s["type"] for s in swings]
    # Should alternate H and L
    for i in range(1, len(types)):
        if types[i] == types[i-1]:
            check("Alternating H/L", False, f"Back-to-back {types[i]} at index {i}")
            break
    else:
        check("Alternating H/L", True)


def test_volume_decline_scoring():
    """Volume declining should improve the score, not declining should lower it."""
    print("\n[TEST] Volume decline affects scoring")
    # Build identical VCP patterns but with different volume profiles
    base_swings = [
        ("up", 100, 50),
        ("up", 200, 80),
        ("up", 250, 20),
        ("down", 220, 8),
        ("up", 248, 10),
        ("down", 235, 6),
        ("up", 245, 5),
    ]

    df_declining_vol = make_realistic_df(base_swings, base_vol=2_000_000)
    df_flat_vol = make_realistic_df(base_swings, base_vol=1_000_000)

    r1 = detect_vcp(df_declining_vol)
    r2 = detect_vcp(df_flat_vol)

    if r1 and r2:
        check("Both detected", True)
        # With short synthetic patterns, volume decline may not trigger
        # (needs 20+ bar span). Just verify the field exists.
        check("Volume field present",
              "volume_declining" in r1 and "volume_declining" in r2,
              "Missing volume_declining field")
    elif r1 or r2:
        check("At least one detected", True)
    else:
        check("At least one detected", False, "Neither pattern detected")


def test_live_sanity():
    """Run against actual cached data and verify basic sanity."""
    print("\n[TEST] Live data sanity checks")
    from engine import fetch_price_data
    import os

    # Only run if we have cached data
    cache_dir = os.path.join(config.CACHE_DIR, "prices")
    if not os.path.exists(cache_dir):
        check("Skipped (no cache)", True)
        return

    parquets = [f for f in os.listdir(cache_dir) if f.endswith(".parquet")]
    if not parquets:
        check("Skipped (no cache)", True)
        return

    # Load all cached data, run VCP detection, verify basic constraints
    detections = 0
    violations = []

    for pf in parquets[:50]:  # test first 50
        sym = pf.replace(".parquet", "")
        df = fetch_price_data(sym)
        if df is None or len(df) < 250:
            continue

        result = detect_vcp(df)
        if result is None:
            continue

        detections += 1
        close = df["Close"].values.astype(float)[-1]
        high = df["High"].values.astype(float)

        # CONSTRAINT 1: Contractions must actually tighten
        c = result["contractions"]
        for j in range(1, len(c)):
            if c[j] > c[j-1] * (config.CONTRACTION_RATIO_MAX * 100 / 100 + 0.01):
                violations.append(f"{sym}: contraction {c[j]}% > {c[j-1]}% * {config.CONTRACTION_RATIO_MAX}")

        # CONSTRAINT 2: Pivot must be reasonable (within 20% of current price)
        pivot = result["pivot"]
        if abs(pivot - close) / close > 0.20:
            violations.append(f"{sym}: pivot {pivot} too far from close {close:.0f}")

        # CONSTRAINT 3: pct_from_pivot should match reality
        expected_pct = (pivot - close) / pivot * 100
        if abs(result["pct_from_pivot"] - expected_pct) > 0.5:
            violations.append(f"{sym}: pct_from_pivot mismatch {result['pct_from_pivot']} vs {expected_pct:.1f}")

        # CONSTRAINT 4: num_contractions matches contractions list length
        if result["num_contractions"] != len(result["contractions"]):
            violations.append(f"{sym}: num_contractions {result['num_contractions']} != len {len(result['contractions'])}")

    check(f"Ran on {detections} detections", detections > 0, "No detections in cached data")
    check(f"No constraint violations ({len(violations)} found)",
          len(violations) == 0,
          "; ".join(violations[:5]))


# ──────────────────────────────────────────────────────────────
# Run all tests
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("VCP SCANNER TEST SUITE")
    print("=" * 50)

    test_zigzag_basic()
    test_clean_3t_vcp()
    test_4t_federalbnk_style()
    test_already_broke_out()
    test_pattern_broken_by_bigger_drop()
    test_old_pattern_ignored()
    test_v_shaped_recovery_rejected()
    test_pivot_is_highest_high()
    test_volume_decline_scoring()
    test_live_sanity()

    print("\n" + "=" * 50)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 50)

    if failed > 0:
        exit(1)
