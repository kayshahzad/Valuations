"""Dividend Policy — sustainability + growth + continuity.

Reads dividend payments from the per-record `clean_json` blob (key
`DividendsPaid`); the value is positive for a payment outflow per the
SEC tag's sign convention, sometimes negative depending on the filer.
We normalize to a positive payment magnitude.

Score combines three signals:

  1. **5y dividend CAGR** — growth above inflation is rewarded.
  2. **FCF coverage ratio** = median(dividends / FCF) over the window —
     >50% suggests strain; <40% suggests headroom.
  3. **Continuity** = consecutive years with non-zero dividends in the
     last 5. Builds the "no cuts" signal: companies that paid every
     year but didn't grow rank above those that paid sporadically.

Tickers that don't pay a dividend at all (sum of dividends == 0 across
the window) get score=None with `reason="no_dividend_program"`. The UI
should render this as a category-distinct empty state rather than as a
"low score" — non-payment is a strategic choice, not a failure.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from typing import List, Optional, Tuple

import pandas as pd

from . import ComputerResult


_FORMULA_VERSION = "dividend_policy_v1"
_LOOKBACK_YEARS = 5


def _extract_dividends_paid(clean_json_str: object) -> Optional[float]:
    """Pull `DividendsPaid` from the row's `clean_json` blob and
    normalize to a non-negative magnitude."""
    if not clean_json_str or not isinstance(clean_json_str, str):
        return None
    try:
        d = json.loads(clean_json_str)
    except json.JSONDecodeError:
        return None
    v = d.get("DividendsPaid")
    if v is None:
        return None
    try:
        return abs(float(v))
    except (TypeError, ValueError):
        return None


_MATERIALITY_THRESHOLD = 0.005   # 0.5% of revenue


def _bucket(cagr: float, coverage: float, continuity_years: int,
            total_years: int, materiality_yield: float) -> int:
    """Map (CAGR, coverage, continuity, materiality) to 1-7.

    Materiality threshold (NEW in v1): tickers paying a token dividend
    (< 0.5% of revenue) cap at 4 regardless of CAGR. Prevents NVDA-type
    artifacts where 25% CAGR on a $0.6B token program would otherwise
    score 7 against a $216B revenue base.

    Continuity floor (NEW in v1): full continuity (paid every year in
    the window) sets a minimum score of 5 — rewards "didn't cut" even
    when CAGR is slow. Prevents AAPL-type artifacts where a steady but
    low-growth dividend would score 4.

    Cutoffs:
      - Token dividend (yield < 0.5% revenue): cap at 4
      - CAGR > 10% with >70% coverage = strained growth (cap at 5)
      - CAGR 5-10% with <50% coverage = healthy growing dividend (6-7)
      - CAGR 0-5% with <60% coverage = stable dividend (4-5)
      - Negative CAGR or cuts in window = 1-3
    """
    full_continuity = (continuity_years == total_years)

    if not full_continuity and continuity_years < total_years - 1:
        # Multiple gap years suggests cuts or initiation/reinitiation
        if cagr < 0:                     return 1
        if cagr < 0.02:                  return 2
        return 3

    # Provisional score before materiality + continuity adjustments
    if cagr >= 0.10 and coverage > 0.70:  raw = 5    # strained growth
    elif cagr >= 0.10 and coverage <= 0.50: raw = 7
    elif cagr >= 0.10:                     raw = 6
    elif cagr >= 0.05 and coverage <= 0.50: raw = 6
    elif cagr >= 0.05:                     raw = 5
    elif cagr >= 0.02 and coverage <= 0.60: raw = 5
    elif cagr >= 0.02:                     raw = 4
    elif cagr >= 0.0:                      raw = 4
    else:                                  raw = 3

    # Materiality cap — token programs don't earn growing-dividend scores
    if materiality_yield < _MATERIALITY_THRESHOLD:
        raw = min(raw, 4)

    # Continuity floor — paid every year wins at least a 5
    if full_continuity and materiality_yield >= _MATERIALITY_THRESHOLD:
        raw = max(raw, 5)

    return raw


def _fingerprint(rows: List[Tuple[int, float, float]]) -> str:
    encoded = "|".join(f"{y}:{d:.2f}:{f:.2f}" for y, d, f in rows)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def compute_dividend_policy(df: pd.DataFrame) -> Optional[ComputerResult]:
    if df.empty:
        return None

    df = df.sort_values("fiscal_year").tail(_LOOKBACK_YEARS)
    rows: List[Tuple[int, float, float]] = []   # (fy, dividends, fcf)

    for _, row in df.iterrows():
        div = _extract_dividends_paid(row.get("clean_json"))
        if div is None:
            div = 0.0
        fcf = row.get("derived_FCF")
        if fcf is None or (isinstance(fcf, float) and fcf != fcf):
            fcf = row.get("clean_FCF")
        if fcf is None:
            continue
        try:
            fcf = float(fcf)
        except (TypeError, ValueError):
            continue
        rows.append((int(row["fiscal_year"]), div, fcf))

    if len(rows) < 3:
        return ComputerResult(
            score=None,
            source_payload={"formula": _FORMULA_VERSION,
                            "reason": "insufficient_history"},
            input_fingerprint=_fingerprint(rows),
            reason=f"Need at least 3 years; got {len(rows)}",
        )

    total_dividends = sum(d for _, d, _ in rows)
    if total_dividends <= 0:
        return ComputerResult(
            score=None,
            source_payload={"formula": _FORMULA_VERSION,
                            "reason": "no_dividend_program",
                            "years_used": [r[0] for r in rows]},
            input_fingerprint=_fingerprint(rows),
            reason="Company does not pay a dividend.",
        )

    # CAGR over the window — first vs last non-zero year
    paying_years = [(y, d) for y, d, _ in rows if d > 0]
    if len(paying_years) >= 2:
        first_y, first_d = paying_years[0]
        last_y,  last_d  = paying_years[-1]
        n = last_y - first_y
        cagr = (last_d / first_d) ** (1.0 / n) - 1.0 if n > 0 and first_d > 0 else 0.0
    else:
        cagr = 0.0

    # Coverage — median dividend / FCF over years where FCF > 0
    coverage_obs = [d / f for _, d, f in rows if f > 0 and d > 0]
    coverage = statistics.median(coverage_obs) if coverage_obs else 1.0

    continuity_years = len(paying_years)
    total_years = len(rows)

    # Materiality: median annual dividend / median annual revenue.
    # Adding revenue to the row would have required a 4-tuple but it's
    # cleaner to recompute here from the source df.
    revenues = []
    for _, row in df.iterrows():
        rev = row.get("clean_Revenue")
        if rev and not (isinstance(rev, float) and rev != rev):
            revenues.append(float(rev))
    median_revenue = sorted(revenues)[len(revenues) // 2] if revenues else 0.0
    median_dividend = sorted([d for _, d, _ in rows if d > 0])
    median_div = median_dividend[len(median_dividend) // 2] if median_dividend else 0.0
    materiality_yield = median_div / median_revenue if median_revenue > 0 else 0.0

    score = _bucket(cagr, coverage, continuity_years, total_years, materiality_yield)

    return ComputerResult(
        score=score,
        source_payload={
            "formula":               _FORMULA_VERSION,
            "lookback_years":        _LOOKBACK_YEARS,
            "dividend_cagr":         round(cagr, 4),
            "median_fcf_coverage":   round(coverage, 4),
            "materiality_yield":     round(materiality_yield, 4),
            "continuity_years":      continuity_years,
            "total_years":           total_years,
            "total_dividends_5y":    round(total_dividends, 2),
            "years_used":            [r[0] for r in rows],
        },
        input_fingerprint=_fingerprint(rows),
    )
