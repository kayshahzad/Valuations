"""
aletheia/data/split_adjuster.py

Cumulative split adjustment for as-filed XBRL share counts and per-share
metrics.

XBRL filers report shares at the count effective at the period end —
pre-split values for fiscal years before a subsequent split. yfinance
returns split-adjusted prices (auto_adjust=True), so backtest-time
mismatch produces fake IV-vs-price gaps for any fiscal year that predates
a later split. The same problem corrupts per-share displays in Streamlit
(P/E, EPS) and in the screening tab.

We apply the cumulative split factor at the cleaning layer so every
downstream consumer sees consistent split-adjusted values. As-filed values
are preserved on the record under `_AsFiled` keys for audit purposes.

Formula:
    factor(period_end) = product of all split ratios with split_date > period_end
                         and split_date <= today

Affected fields:
    SharesDiluted, SharesBasic, SharesOutstanding   → multiply by factor
    DilutedEPS, BasicEPS                             → divide by factor

Total dollar amounts (Revenue, NetIncome, Cash, etc.) are NOT affected —
splits are a numéraire change on the share count, not on company economics.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, Optional

import pandas as pd
import yfinance as yf


# Cache: ticker -> Series of split ratios (date-indexed)
_SPLIT_HISTORY: Dict[str, pd.Series] = {}

# Fields to scale UP by the factor (share counts)
_SHARE_COUNT_FIELDS = (
    "SharesDiluted",
    "SharesBasic",
    "SharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
)

# Fields to scale DOWN by the factor (per-share metrics)
_PER_SHARE_FIELDS = (
    "DilutedEPS",
    "BasicEPS",
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
)


def get_split_history(ticker: str) -> pd.Series:
    """
    Return a date-indexed Series of split ratios for the ticker.
    Cached after first call. Empty Series if ticker has no split history.
    """
    if ticker in _SPLIT_HISTORY:
        return _SPLIT_HISTORY[ticker]
    try:
        splits = yf.Ticker(ticker).splits
        if splits is None:
            splits = pd.Series(dtype=float)
        # Strip timezone for clean date comparisons
        if not splits.empty and splits.index.tz is not None:
            splits.index = splits.index.tz_localize(None)
    except Exception:
        splits = pd.Series(dtype=float)
    _SPLIT_HISTORY[ticker] = splits
    return splits


def cumulative_split_factor(
    ticker: str,
    period_end: Optional[date],
    as_of: Optional[date] = None,
) -> float:
    """
    Cumulative product of split ratios that occurred AFTER period_end and
    on/before `as_of` (default: today). Returns 1.0 if no splits in window.

    A 7:1 split returns ratio 7.0; a 4:1 split returns 4.0. For AAPL with
    period_end=2009-09-26 and as_of=today, factor = 7 * 4 = 28.
    """
    if period_end is None:
        return 1.0
    if as_of is None:
        as_of = date.today()
    splits = get_split_history(ticker)
    if splits.empty:
        return 1.0
    period_ts = pd.Timestamp(period_end)
    as_of_ts = pd.Timestamp(as_of)
    relevant = splits[(splits.index > period_ts) & (splits.index <= as_of_ts)]
    if relevant.empty:
        return 1.0
    return float(relevant.prod())


def apply_split_adjustment(record) -> None:
    """
    Mutate a CleanedRecord in place: apply cumulative split factor to
    share counts and per-share metrics. Preserves as-filed values under
    `_AsFiled` keys for audit.

    No-op if the record has no period_end_date (factor cannot be computed)
    or if the cumulative factor is 1.0 (no splits occurred since this FY end).
    """
    period_end_str = getattr(record, "period_end_date", None)
    if not period_end_str:
        return
    try:
        period_end = date.fromisoformat(str(period_end_str)[:10])
    except (ValueError, TypeError):
        return

    factor = cumulative_split_factor(record.ticker, period_end)
    if factor == 1.0:
        return  # No splits since this FY — nothing to adjust

    # Adjust share-count fields (multiply by factor)
    for src in (record.raw, record.clean):
        for fld in _SHARE_COUNT_FIELDS:
            v = src.get(fld)
            if v is None or (isinstance(v, float) and v != v):  # NaN check
                continue
            src[f"{fld}_AsFiled"] = v
            src[fld] = v * factor

    # Adjust per-share fields (divide by factor)
    for src in (record.raw, record.clean):
        for fld in _PER_SHARE_FIELDS:
            v = src.get(fld)
            if v is None or (isinstance(v, float) and v != v):
                continue
            if v == 0:
                continue
            src[f"{fld}_AsFiled"] = v
            src[fld] = v / factor

    # Audit metadata
    record.derived["split_factor_applied"] = factor
    if hasattr(record, "warn"):
        record.warn(
            f"Split adjustment applied: factor={factor:g} "
            f"(period_end={period_end_str})"
        )
