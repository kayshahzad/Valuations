"""
aletheia/data/historical_macro.py

Historical macro lookups for backtest. The production code path uses
today's risk-free rate, beta, and ERP. The backtest needs the values that
were observable at each as_of_date — without these, IVs computed for 2020
signals use 2025 macro conditions, which systematically distorts the
WACC and inflates the apparent IV gap.

Three functions:
- get_risk_free_rate(as_of_date): yfinance ^IRX history, cached
- get_equity_risk_premium(as_of_date): Damodaran annual implied ERP, interpolated
- compute_historical_beta(ticker, as_of_date): 5y weekly returns vs ^GSPC,
  filtered to dates <= as_of_date

All three cache aggressively. yfinance is hit at most once per ticker per
process (^IRX once; ^GSPC once; each backtest ticker once).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Process-local caches
_RF_HISTORY: Optional[pd.Series] = None
_ERP_HISTORY: Optional[pd.DataFrame] = None
_RETURNS_CACHE: Dict[str, pd.Series] = {}  # ticker -> weekly returns series
_MARKET_RETURNS: Optional[pd.Series] = None  # ^GSPC weekly returns

_MACRO_DIR = Path("valuation_data/macro")
_RF_CACHE_PATH = _MACRO_DIR / "risk_free_rate_history.parquet"
_ERP_CSV_PATH = _MACRO_DIR / "damodaran_implied_erp.csv"

_RF_FLOOR = 0.001  # COVID-era yields hit ~0; floor at 10bp to avoid div-by-zero
_ERP_FALLBACK = 0.0475


# ─────────────────────────────────────────────────────────────────────────
# Risk-free rate
# ─────────────────────────────────────────────────────────────────────────

def _load_rf_history() -> pd.Series:
    """Load ^IRX history. Reads cached parquet if present; else fetches once."""
    global _RF_HISTORY
    if _RF_HISTORY is not None:
        return _RF_HISTORY
    if _RF_CACHE_PATH.exists():
        _RF_HISTORY = pd.read_parquet(_RF_CACHE_PATH)["Close"]
        return _RF_HISTORY
    # First call — fetch and cache
    _MACRO_DIR.mkdir(parents=True, exist_ok=True)
    hist = yf.Ticker("^IRX").history(period="max", auto_adjust=False)
    if hist is None or hist.empty:
        _RF_HISTORY = pd.Series(dtype=float)
        return _RF_HISTORY
    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)
    series = hist["Close"].dropna() / 100.0  # ^IRX is in percent points
    series.to_frame().to_parquet(_RF_CACHE_PATH)
    _RF_HISTORY = series
    return _RF_HISTORY


def get_risk_free_rate(as_of_date: date) -> float:
    """
    13-week T-bill yield (^IRX) as of `as_of_date` (or nearest prior trading
    day). Returns rate as decimal (0.036 = 3.6%). Floored at 0.001.
    """
    series = _load_rf_history()
    if series.empty:
        return _RF_FLOOR
    target = pd.Timestamp(as_of_date)
    try:
        idx = series.index.asof(target)
    except Exception:
        return _RF_FLOOR
    if pd.isna(idx):
        return _RF_FLOOR
    val = float(series.loc[idx])
    return max(val, _RF_FLOOR)


def get_current_risk_free_rate() -> float:
    """Production-side current Rf. Same as as-of today."""
    return get_risk_free_rate(date.today())


def get_risk_free_rate_normalized(
    as_of_date: date,
    window_years: int = 5,
) -> float:
    """
    Smoothed historical Rf: rolling-mean of ^IRX over `window_years` ending
    at as_of_date. Dampens regime extremes (COVID 0.1%, 2023 5.2%) so the
    DCF terminal-value math doesn't whip around from the discount-rate side.

    This is a **diagnostic variant** for backtest sensitivity testing — it's
    not the production path. Tests the hypothesis that DCF edge collapse is
    driven by macro volatility rather than methodology limits.
    """
    series = _load_rf_history()
    if series.empty:
        return _RF_FLOOR
    end = pd.Timestamp(as_of_date)
    start = end - pd.Timedelta(days=window_years * 365 + 10)
    window = series[(series.index >= start) & (series.index <= end)]
    if window.empty:
        return _RF_FLOOR
    val = float(window.mean())
    return max(val, _RF_FLOOR)


# ─────────────────────────────────────────────────────────────────────────
# Equity risk premium (Damodaran annual)
# ─────────────────────────────────────────────────────────────────────────

def _load_erp_history() -> pd.DataFrame:
    """Load Damodaran ERP CSV. Returns df with year_end_date, erp_decimal."""
    global _ERP_HISTORY
    if _ERP_HISTORY is not None:
        return _ERP_HISTORY
    if not _ERP_CSV_PATH.exists():
        logger.warning("Damodaran ERP CSV not found at %s", _ERP_CSV_PATH)
        _ERP_HISTORY = pd.DataFrame(columns=["year_end_date", "erp_decimal"])
        return _ERP_HISTORY
    df = pd.read_csv(_ERP_CSV_PATH)
    df["year_end_date"] = pd.to_datetime(df["year_end_date"])
    df = df.sort_values("year_end_date").reset_index(drop=True)
    _ERP_HISTORY = df
    return _ERP_HISTORY


def get_equity_risk_premium(as_of_date: date) -> float:
    """
    Implied equity risk premium as of `as_of_date`, interpolated linearly
    between Damodaran annual year-end observations.

    Damodaran's "Year=2020" row is the ERP estimated as of 2020-12-31 looking
    forward. So for as_of_date=2020-04-01 we interpolate between Year=2019
    (5.20%) and Year=2020 (4.72%) — gives ~5.08% for early-2020.

    Falls back to 4.75% (current academic consensus) on lookup failure.
    """
    df = _load_erp_history()
    if df.empty:
        return _ERP_FALLBACK
    target = pd.Timestamp(as_of_date)
    # Both bounds before earliest -> earliest value
    if target <= df["year_end_date"].iloc[0]:
        return float(df["erp_decimal"].iloc[0])
    # Past latest -> latest value
    if target >= df["year_end_date"].iloc[-1]:
        return float(df["erp_decimal"].iloc[-1])
    # Linear interpolation between adjacent year-ends
    after = df[df["year_end_date"] >= target].iloc[0]
    before = df[df["year_end_date"] < target].iloc[-1]
    span = (after["year_end_date"] - before["year_end_date"]).days
    if span == 0:
        return float(after["erp_decimal"])
    weight = (target - before["year_end_date"]).days / span
    return float(before["erp_decimal"] + weight * (after["erp_decimal"] - before["erp_decimal"]))


# ─────────────────────────────────────────────────────────────────────────
# Historical beta
# ─────────────────────────────────────────────────────────────────────────

def _load_weekly_returns(ticker: str) -> pd.Series:
    """Load full-history weekly returns for a ticker. Process-cached."""
    if ticker in _RETURNS_CACHE:
        return _RETURNS_CACHE[ticker]
    try:
        # auto_adjust=True so split/dividend-adjusted; we want total return
        hist = yf.Ticker(ticker).history(period="max", interval="1wk", auto_adjust=True)
    except Exception:
        _RETURNS_CACHE[ticker] = pd.Series(dtype=float)
        return _RETURNS_CACHE[ticker]
    if hist is None or hist.empty:
        _RETURNS_CACHE[ticker] = pd.Series(dtype=float)
        return _RETURNS_CACHE[ticker]
    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)
    returns = hist["Close"].pct_change().dropna()
    _RETURNS_CACHE[ticker] = returns
    return returns


def _load_market_returns() -> pd.Series:
    """^GSPC weekly returns; cached."""
    global _MARKET_RETURNS
    if _MARKET_RETURNS is not None:
        return _MARKET_RETURNS
    _MARKET_RETURNS = _load_weekly_returns("^GSPC")
    return _MARKET_RETURNS


def compute_historical_beta(
    ticker: str,
    as_of_date: date,
    window_years: int = 5,
) -> Optional[float]:
    """
    Beta computed from weekly returns over the `window_years` years ending
    at `as_of_date`. Filters strictly to dates <= as_of_date so no future
    information leaks into the regression.

    Returns None if insufficient data (< 50 weekly observations in window).
    """
    ticker_returns = _load_weekly_returns(ticker)
    market_returns = _load_market_returns()
    if ticker_returns.empty or market_returns.empty:
        return None

    end = pd.Timestamp(as_of_date)
    start = end - pd.Timedelta(days=window_years * 365 + 10)
    t_window = ticker_returns[(ticker_returns.index >= start) & (ticker_returns.index <= end)]
    m_window = market_returns[(market_returns.index >= start) & (market_returns.index <= end)]

    # Align on common dates
    aligned = pd.concat([t_window, m_window], axis=1, join="inner").dropna()
    aligned.columns = ["t", "m"]
    if len(aligned) < 50:
        return None

    cov = aligned["t"].cov(aligned["m"])
    var = aligned["m"].var()
    if var <= 0:
        return None
    return float(cov / var)


# ────────────────────────────────────────────────────────────────────────────
# GDP projections (Reality Checks — Feature 1)
# ────────────────────────────────────────────────────────────────────────────

def get_gdp_projection(year: int) -> Optional[float]:
    """
    Return projected US nominal GDP for `year` in USD billions, linearly
    interpolated between the curated CBO projection points in
    `config/gdp_projections.py`. Returns None if `year` is outside the
    curated range (i.e., requires extrapolation).

    Used by the Reality Checks GDP comparison: terminal-year projected
    revenue ÷ projected GDP at that horizon.
    """
    try:
        from config.gdp_projections import GDP_PROJECTIONS_USD_BILLIONS
    except Exception:
        return None

    if not GDP_PROJECTIONS_USD_BILLIONS:
        return None

    pts = sorted(GDP_PROJECTIONS_USD_BILLIONS.keys())
    if year < pts[0] or year > pts[-1]:
        return None
    if year in GDP_PROJECTIONS_USD_BILLIONS:
        return float(GDP_PROJECTIONS_USD_BILLIONS[year])

    # Linear interpolation between the two surrounding curated points.
    lo = max(p for p in pts if p < year)
    hi = min(p for p in pts if p > year)
    lo_v = GDP_PROJECTIONS_USD_BILLIONS[lo]
    hi_v = GDP_PROJECTIONS_USD_BILLIONS[hi]
    frac = (year - lo) / (hi - lo)
    return float(lo_v + frac * (hi_v - lo_v))
