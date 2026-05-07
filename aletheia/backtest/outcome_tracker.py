"""
aletheia/backtest/outcome_tracker.py

Historical price retrieval. Uses yfinance with auto_adjust=True so prices
are split- AND dividend-adjusted (total return). Without adjustment SMCI's
10:1 split (Oct 2024) would produce a phantom 90% drawdown in the backtest;
without dividend adjustment, AAPL/MSFT/COST returns are understated by
~0.5-2% per year.

Single-ticker history is downloaded once and cached in-process to avoid
repeated network calls for backtests that touch the same ticker on multiple
dates.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Optional

import pandas as pd
import yfinance as yf

# Process-local cache: ticker -> DataFrame of historical adjusted close
_PRICE_CACHE: Dict[str, pd.DataFrame] = {}


def _fetch_history(ticker: str, force: bool = False) -> Optional[pd.DataFrame]:
    """
    Download max-available history for a ticker, split- and dividend-adjusted.
    Cached after first call.
    """
    if not force and ticker in _PRICE_CACHE:
        return _PRICE_CACHE[ticker]
    try:
        # auto_adjust=True is the modern yfinance default and gives fully
        # adjusted Close (splits + dividends folded into the price series)
        hist = yf.Ticker(ticker).history(period="max", auto_adjust=True)
    except Exception:
        return None
    if hist is None or hist.empty:
        return None
    # Strip tz so date comparisons are clean
    hist.index = hist.index.tz_localize(None) if hist.index.tz is not None else hist.index
    hist = hist[["Close"]].dropna()
    _PRICE_CACHE[ticker] = hist
    return hist


def get_price_at(ticker: str, target_date: date) -> Optional[float]:
    """
    Return the adjusted close at target_date, or the most recent prior
    trading day. Returns None if no price is available within 10 calendar
    days before target_date.
    """
    hist = _fetch_history(ticker)
    if hist is None or hist.empty:
        return None
    target_ts = pd.Timestamp(target_date)
    # asof returns the last value at or before target_ts
    try:
        idx_at_or_before = hist.index.asof(target_ts)
    except Exception:
        return None
    if pd.isna(idx_at_or_before):
        return None
    # Sanity: don't accept stale data older than 10 days
    if (target_ts - idx_at_or_before).days > 10:
        return None
    return float(hist.loc[idx_at_or_before, "Close"])


def compute_forward_return(
    ticker: str,
    entry_date: date,
    horizon_months: int,
) -> Optional[float]:
    """
    (price_at_horizon - entry_price) / entry_price.

    Returns None if either price is missing OR if the horizon date is in the
    future (return cannot be observed yet).
    """
    today = date.today()
    horizon_date = entry_date + timedelta(days=horizon_months * 30)
    if horizon_date > today:
        return None
    entry = get_price_at(ticker, entry_date)
    exit = get_price_at(ticker, horizon_date)
    if entry is None or exit is None or entry <= 0:
        return None
    return (exit - entry) / entry


def compute_momentum(
    ticker: str,
    as_of_date: date,
    lookback_days: int = 180,
) -> Optional[float]:
    """
    Trailing-window return: (price_now / price_n_days_ago) - 1.
    Used as the dumb-momentum control variable for the backtest.
    """
    now = get_price_at(ticker, as_of_date)
    past = get_price_at(ticker, as_of_date - timedelta(days=lookback_days))
    if now is None or past is None or past <= 0:
        return None
    return (now / past) - 1.0


def warm_cache(tickers: list) -> None:
    """Pre-fetch history for a list of tickers (parallel download via yfinance)."""
    for t in tickers:
        _fetch_history(t)
