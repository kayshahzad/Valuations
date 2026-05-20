"""Buyback Discipline — yield + consistency proxy.

Code-version-1 limitation: the project does not yet ingest historical
per-year share prices, so we cannot weight buybacks by the EV/EBITDA at
which they were executed (the strictly correct multiple-aware measure).
Instead, v1 uses two proxies:

  1. **5-year net-buyback yield** = sum of `clean_NetBuyback_AfterSBC`
     over the last 5 years, divided by the most recent year's revenue.
     (Revenue is the denominator, not market cap, because we don't have
     historical market caps. Yield-to-revenue is structurally smaller
     than yield-to-market-cap but the cross-ticker ranking is the same.)

  2. **Consistency** = (number of years with positive net buybacks) /
     (number of years observed). Captures whether the company is
     systematically returning capital or sporadically dipping in.

Score 7 = high consistent yield (top-quintile capital returns + every-
year discipline). Score 1 = consistent net dilution.

A future code_version=2 will replace this with a multiple-aware measure
once historical price data is in the cleaning pipeline.
"""

from __future__ import annotations

import hashlib
from typing import List, Optional, Tuple

import pandas as pd

from . import ComputerResult


_FORMULA_VERSION = "buyback_discipline_v1"
_LOOKBACK_YEARS = 5


def _bucket(yield_pct: float, consistency: float, has_dilution: bool) -> int:
    """Map (yield, consistency, dilution_flag) to 1-7.

    Cutoffs reflect what consistent buyback yields look like in
    practice. AAPL/MSFT have run 4-6% yield-to-revenue with high
    consistency; CNC/most growth-stage tickers run 0-1%; serial
    diluters can show -3% or worse."""
    if has_dilution:
        # Net dilution over the window — bottom of the range
        if yield_pct < -0.03:  return 1     # severe ongoing dilution
        if yield_pct < -0.01:  return 2     # mild dilution
        return 3                            # near-zero net activity but with issuance years
    # No dilution-year sequences
    if yield_pct >= 0.04 and consistency >= 0.80: return 7
    if yield_pct >= 0.03 and consistency >= 0.60: return 6
    if yield_pct >= 0.02 and consistency >= 0.60: return 5
    if yield_pct >= 0.01 and consistency >= 0.40: return 4
    if yield_pct >= 0.005:                        return 3
    return 2  # near-zero buybacks; not actively returning capital


def _fingerprint(rows: List[Tuple[int, float, float]]) -> str:
    encoded = "|".join(f"{y}:{nb:.2f}:{rev:.2f}" for y, nb, rev in rows)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def compute_buyback_discipline(df: pd.DataFrame) -> Optional[ComputerResult]:
    if df.empty:
        return None

    df = df.sort_values("fiscal_year").tail(_LOOKBACK_YEARS)

    # First pass — separate "buyback known to be zero" from "buyback NaN
    # in the data" (cleaning gap). All-NaN means we can't say anything;
    # return None rather than mis-scoring as inactive.
    buyback_observations = 0
    rows: List[Tuple[int, float, float]] = []
    for _, row in df.iterrows():
        nb  = row.get("clean_NetBuyback_AfterSBC")
        rev = row.get("clean_Revenue")
        if rev is None or (isinstance(rev, float) and rev != rev) or rev <= 0:
            continue
        nb_is_nan = (nb is None) or (isinstance(nb, float) and nb != nb)
        if not nb_is_nan:
            buyback_observations += 1
            nb_v = float(nb)
        else:
            nb_v = 0.0   # provisional; if all-NaN we'll return None below
        rows.append((int(row["fiscal_year"]), nb_v, float(rev)))

    if len(rows) < 3:
        return ComputerResult(
            score=None,
            source_payload={"formula": _FORMULA_VERSION,
                            "reason": "insufficient_history"},
            input_fingerprint=_fingerprint(rows),
            reason=f"Need at least 3 years of revenue data; got {len(rows)}",
        )
    if buyback_observations < 3:
        # Data quality threshold. With <3 actual observations we can't
        # distinguish "ticker doesn't buy back" from "cleaning pipeline
        # didn't capture it under standard tags" (BRK-B / NEE / TSM /
        # TSLA all show all-NaN under v1 ingestion). Returning None
        # surfaces the gap honestly rather than confidently scoring a
        # noisy single observation.
        return ComputerResult(
            score=None,
            source_payload={"formula": _FORMULA_VERSION,
                            "reason": "buyback_data_unavailable",
                            "buyback_observations": buyback_observations,
                            "years_used": [r[0] for r in rows]},
            input_fingerprint=_fingerprint(rows),
            reason=(
                f"Only {buyback_observations} of {len(rows)} years have "
                f"buyback data; need 3+ observations to score reliably."
            ),
        )

    total_buybacks = sum(nb for _, nb, _ in rows)
    revenues = [r[2] for r in rows]
    # Median 5y revenue is a more stable denominator than the most-recent
    # year — last-year revenue can be unusually high or low for one-off
    # reasons, distorting the yield ratio.
    revenues_sorted = sorted(revenues)
    median_revenue = revenues_sorted[len(revenues_sorted) // 2]
    yield_pct = total_buybacks / median_revenue if median_revenue > 0 else 0.0

    positive_years = sum(1 for _, nb, _ in rows if nb > 0)
    dilution_years = sum(1 for _, nb, _ in rows if nb < 0)
    consistency = positive_years / len(rows)
    has_dilution = (dilution_years >= 2) or yield_pct < 0

    score = _bucket(yield_pct, consistency, has_dilution)

    return ComputerResult(
        score=score,
        source_payload={
            "formula":               _FORMULA_VERSION,
            "lookback_years":        _LOOKBACK_YEARS,
            "yield_pct_of_median_revenue":  round(yield_pct, 4),
            "consistency":           round(consistency, 3),
            "positive_years":        positive_years,
            "dilution_years":        dilution_years,
            "buyback_observations":  buyback_observations,
            "total_buybacks_5y":     round(total_buybacks, 2),
            "median_revenue":        round(median_revenue, 2),
            "years_used":            [r[0] for r in rows],
            "v1_limitation":         (
                "Multiple-aware buyback timing not implemented; v1 uses "
                "yield-to-median-revenue as proxy because historical "
                "per-year price data is not in the cleaning pipeline."
            ),
        },
        input_fingerprint=_fingerprint(rows),
    )
