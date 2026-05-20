"""Cyclicality — volatility of revenue around its trend.

Measures *structural* revenue volatility, not raw revenue dispersion.
Computing CV on raw revenues conflates cyclicality with growth — a
hyper-grower like NVDA shows a high raw CV simply because its trend
slope is steep, even when growth is monotonically smooth (the
NVIDIA-FY26 spot-check exposed this; raw CV scored NVDA as 1, deep-
cyclical, when the actual issue is rapid trend growth, not cycle).

v1 fix: compute volatility of *log year-over-year growth rates* — i.e.
how variable is the growth rate, not how dispersed are the levels. A
stable grower has tightly-clustered log returns; a true cyclical (CAT,
TXN, MU) has widely-dispersed log returns even when long-term trend is
healthy.

Formula:
    log_returns = [log(R_t / R_{t-1}) for t in last 7 years]
    score derived from stdev(log_returns) — interpreted as
    "annualized log-return volatility"

Score is inverted: low log-return volatility → high score (predictable
revenue). The lifecycle-classification flag `is_cyclical_peak` is a
*current-state* flag and is exposed in the source payload but is NOT
used in the score itself.

Locked at code_version=1.
"""

from __future__ import annotations

import hashlib
import math
import statistics
from typing import List, Optional, Tuple

import pandas as pd

from . import ComputerResult


_FORMULA_VERSION = "cyclicality_v1"
_LOOKBACK_YEARS = 7   # one full cycle for most non-tech businesses
_MIN_OBSERVATIONS = 4


def _bucket(log_return_volatility: float) -> int:
    """Map log-return stdev to 1-7. Higher score = lower volatility =
    more predictable revenue.

    Cutoffs anchored against universe expectations:
      - Consumer staples (KO, PG): log-return stdev < 0.05 → 7
      - Stable compounders (MSFT, V): 0.05-0.10 → 6
      - Hyper-growth at consistent pace (NVDA, AAPL): 0.10-0.20 → 5
      - Mid-cyclicals (financials, healthcare): 0.20-0.30 → 4
      - Industrial cyclicals (CAT, TXN): 0.30-0.45 → 3
      - Deep cyclicals (semis, energy): >0.45 → 1-2"""
    v = log_return_volatility
    if v < 0.05: return 7
    if v < 0.08: return 6
    if v < 0.13: return 5
    if v < 0.22: return 4
    if v < 0.32: return 3
    if v < 0.45: return 2
    return 1


def _fingerprint(rows: List[Tuple[int, float]]) -> str:
    encoded = "|".join(f"{y}:{r:.2f}" for y, r in rows)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def compute_cyclicality(df: pd.DataFrame) -> Optional[ComputerResult]:
    if df.empty:
        return None

    df = df.sort_values("fiscal_year").tail(_LOOKBACK_YEARS)
    rows: List[Tuple[int, float]] = []
    for _, row in df.iterrows():
        rev = row.get("clean_Revenue")
        if rev is None or (isinstance(rev, float) and rev != rev) or rev <= 0:
            continue
        rows.append((int(row["fiscal_year"]), float(rev)))

    if len(rows) < _MIN_OBSERVATIONS:
        return ComputerResult(
            score=None,
            source_payload={"formula": _FORMULA_VERSION,
                            "reason": "insufficient_history"},
            input_fingerprint=_fingerprint(rows),
            reason=f"Need at least {_MIN_OBSERVATIONS} years of revenue; got {len(rows)}",
        )

    revenues = [r[1] for r in rows]

    # Detrend via log year-over-year returns. Stable growers cluster
    # tightly; cyclicals show wide dispersion regardless of trend slope.
    log_returns: List[float] = []
    for i in range(1, len(revenues)):
        prev, cur = revenues[i - 1], revenues[i]
        if prev > 0 and cur > 0:
            log_returns.append(math.log(cur / prev))

    if len(log_returns) < 3:
        return ComputerResult(
            score=None,
            source_payload={"formula": _FORMULA_VERSION,
                            "reason": "insufficient_log_returns"},
            input_fingerprint=_fingerprint(rows),
            reason=f"Need at least 3 valid log-returns; got {len(log_returns)}",
        )

    log_return_volatility = statistics.stdev(log_returns)
    mean_log_return = statistics.mean(log_returns)

    score = _bucket(log_return_volatility)

    return ComputerResult(
        score=score,
        source_payload={
            "formula":                _FORMULA_VERSION,
            "lookback_years":         _LOOKBACK_YEARS,
            "log_return_volatility":  round(log_return_volatility, 4),
            "mean_log_return":        round(mean_log_return, 4),  # ~ avg growth rate
            "log_returns":            [round(x, 4) for x in log_returns],
            "years_used":             [r[0] for r in rows],
            "note": (
                "Score is based on volatility of log year-over-year revenue "
                "growth — captures cyclicality independent of trend slope. "
                "`mean_log_return` approximates the average annual growth "
                "rate over the window. Use `is_cyclical_peak` from the "
                "lifecycle classification (separate signal) for "
                "current-cycle position."
            ),
        },
        input_fingerprint=_fingerprint(rows),
    )
