"""
VCP Scanner Configuration — All tunable parameters in one place.
Based on Mark Minervini's SEPA (Specific Entry Point Analysis) method.
"""

# ──────────────────────────────────────────────────────────────
# Universe
# ──────────────────────────────────────────────────────────────
UNIVERSE: str = "NIFTY200"  # NIFTY200, NIFTY500, or path to CSV with Symbol column
EXCHANGE_SUFFIX: str = ".NS"  # Yahoo Finance suffix for NSE

# ──────────────────────────────────────────────────────────────
# Data
# ──────────────────────────────────────────────────────────────
HISTORY_DAYS: int = 400  # bars to download (need ~252 trading days for 200 SMA + lookback)
CACHE_DIR: str = "cache"
CACHE_EXPIRY_HOURS: int = 4  # re-download price data after this many hours

# ──────────────────────────────────────────────────────────────
# Stage 2 Filter (Minervini's SEPA Criteria)
# ──────────────────────────────────────────────────────────────
# Price must be above these moving averages
SMA_50: int = 50
SMA_150: int = 150
SMA_200: int = 200

# SMA ordering: 50 > 150 > 200 (bullish alignment)
REQUIRE_SMA_ALIGNMENT: bool = True

# 200 SMA must be rising for at least this many days
SMA200_RISING_DAYS: int = 20

# Price must be within this % of 52-week high
MAX_PCT_FROM_52W_HIGH: float = 0.25  # 25%

# Price must be at least this % above 52-week low
MIN_PCT_ABOVE_52W_LOW: float = 0.30  # 30%

# Minimum average daily volume (shares)
MIN_AVG_VOLUME: int = 50_000

# Minimum price filter (avoid penny stocks)
MIN_PRICE: float = 50.0

# ──────────────────────────────────────────────────────────────
# VCP Detection
# ──────────────────────────────────────────────────────────────
# ZigZag swing detection — minimum % move to register a swing
ZIGZAG_PCT: float = 0.05  # 5%

# Lookback window for VCP pattern (trading days)
LOOKBACK_BARS: int = 120

# Contractions must shrink by at least this ratio (each T < previous T * ratio)
CONTRACTION_RATIO_MAX: float = 0.95  # each contraction ≤ 95% of previous

# Minimum number of contractions (T-counts)
MIN_CONTRACTIONS: int = 2
MAX_CONTRACTIONS: int = 6

# Maximum width of first contraction (% from swing high to swing low)
MAX_FIRST_CONTRACTION_PCT: float = 0.40  # 40%

# Minimum width of last contraction
MIN_LAST_CONTRACTION_PCT: float = 0.02  # 2%

# Volume should decline during pattern formation
REQUIRE_VOLUME_DECLINE: bool = False  # score it but don't hard-filter
VOLUME_DECLINE_THRESHOLD: float = 0.70  # avg volume in last 20 bars < 70% of avg in first 20

# ──────────────────────────────────────────────────────────────
# Pivot & Entry
# ──────────────────────────────────────────────────────────────
# Pivot = highest high within the last contraction
# Stock must be within this % of pivot to be actionable
MAX_PCT_FROM_PIVOT: float = 0.08  # 8%

# ──────────────────────────────────────────────────────────────
# Scoring Weights (total = 100)
# ──────────────────────────────────────────────────────────────
SCORE_WEIGHTS: dict = {
    "contraction_quality": 25,   # how cleanly contractions shrink
    "volume_dry_up": 20,         # volume decline quality
    "proximity_to_pivot": 20,    # how close price is to breakout
    "sma_alignment": 15,         # how well-ordered the SMAs are
    "price_vs_high": 10,         # proximity to 52-week high
    "num_contractions": 10,      # 3-4 is ideal per Minervini
}

# ──────────────────────────────────────────────────────────────
# Enrichment
# ──────────────────────────────────────────────────────────────
ENRICHMENT_WORKERS: int = 5  # ThreadPoolExecutor workers for yfinance calls
ENRICHMENT_CACHE_DIR: str = "cache/enrichment"

# Earnings proximity thresholds (days)
EARNINGS_EXCLUDE_DAYS: int = 7     # exclude entirely if earnings < 7 days
EARNINGS_WARNING_DAYS: int = 14    # flag warning if < 14 days
EARNINGS_SAFE_DAYS: int = 30       # flag safe if 30+ days

# Fundamental thresholds for flagging
MIN_ROE: float = 0.10  # 10%

# Sector performance comparison period (trading days)
SECTOR_COMPARISON_DAYS_1W: int = 5
SECTOR_COMPARISON_DAYS_1M: int = 22

# Sector headwind/tailwind threshold
SECTOR_HEADWIND_THRESHOLD: float = -0.03  # -3% vs Nifty

# Volume interest detection
VOLUME_INTEREST_MULTIPLIER: float = 1.5  # volume > 1.5x 50-day avg
VOLUME_INTEREST_LOOKBACK: int = 5        # check last 5 trading days

# RS Rating weight scheme
RS_WEIGHTS: dict = {
    "3m": 0.50,
    "6m": 0.30,
    "12m": 0.20,
}

# ──────────────────────────────────────────────────────────────
# Trade Plan
# ──────────────────────────────────────────────────────────────
PORTFOLIO_SIZE: float = 1_000_000  # ₹10 lakh default — change to your actual size
RISK_PER_TRADE_PCT: float = 0.01  # 1% of portfolio risked per trade
STOP_LOSS_PCT: float = 0.07       # 7% below entry

# Entry conditions
ENTRY_VOLUME_MULTIPLIER: float = 1.5   # breakout day volume must be > 1.5x 50-day avg
ENTRY_CLOSE_RANGE_PCT: float = 0.75    # close must be in upper 25% of day's range
ENTRY_MIN_RS: int = 70                 # RS Rating must be > 70
ENTRY_MIN_EARNINGS_DAYS: int = 14      # no entry if earnings < 14 days

# Exit conditions
EXIT_SMA_SHORT: int = 10           # sell if close < 10 SMA for 3 consecutive days
EXIT_SMA_SHORT_DAYS: int = 3       # number of days below 10 SMA to trigger
EXIT_SMA_MEDIUM: int = 50          # sell if close < 50 SMA
CLIMAX_GAIN_PERCENTILE: float = 0.95  # top 5% daily gain = climax warning
CLIMAX_VOLUME_MULTIPLIER: float = 2.0  # volume > 2x avg on a big gain day = climax

# ──────────────────────────────────────────────────────────────
# NSE Direct Data
# ──────────────────────────────────────────────────────────────
# Delivery volume thresholds
DELIVERY_HIGH_PCT: float = 60.0       # delivery % above this = real buying (not just trading)
DELIVERY_LOW_PCT: float = 30.0        # below this = speculative churn, red flag
DELIVERY_LOOKBACK_DAYS: int = 30      # days of historical delivery data to fetch

# Institutional holding thresholds
INSTITUTIONAL_MIN_PCT: float = 15.0   # minimum institutional holding % for confidence
INSTITUTIONAL_HIGH_PCT: float = 30.0  # above this = strong institutional backing

# Bulk/block deal flags
BULK_DEAL_FLAG: bool = True           # flag stocks with recent bulk/block deals

# ──────────────────────────────────────────────────────────────
# Telegram
# ──────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = ""  # set via env var TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID: str = ""    # set via env var TELEGRAM_CHAT_ID

# ──────────────────────────────────────────────────────────────
# Output
# ──────────────────────────────────────────────────────────────
OUTPUT_DIR: str = "output"
DASHBOARD_FILENAME: str = "dashboard.html"
JSON_FILENAME: str = "scan_results.json"
MAX_RESULTS: int = 30  # max setups to show in dashboard

# ──────────────────────────────────────────────────────────────
# Nifty Index Lists (Yahoo tickers)
# ──────────────────────────────────────────────────────────────
NIFTY_50_INDEX: str = "^NSEI"
