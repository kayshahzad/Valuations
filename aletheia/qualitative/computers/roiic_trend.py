"""ROIIC Trend — Damodaran convention, 5-year rolling window.

Formula:
    ROIIC_t = (NOPAT_t − NOPAT_{t-1}) / (InvestedCapital_t − InvestedCapital_{t-1})

Edge cases:
  - ΔIC <= 1% of prior IC: treated as "capital release / capital-flat
    growth." If ΔNOPAT > 0 in the same year, that year contributes a
    capped 0.50 ROIIC to the series (signals exceptional capital
    efficiency without dividing by zero); if ΔNOPAT <= 0, the year is
    skipped (pure restructuring noise).
  - Insufficient years (< 4 valid ROIIC observations): returns None.
  - Absurd ΔIC denominators producing |ROIIC| > 1.0: clamped to ±1.0
    before bucketing. (A "10× return on incremental capital" is data
    noise, not signal.)

Score is derived from two statistics over the valid series:
  - median ROIIC (level)
  - linear-regression slope on year-index (trajectory, in pp/year)

Buckets blend level and slope so a high-level-but-decaying business
scores below a high-level-and-stable one, and a low-level-but-improving
business gets credit for the trend.

Locked at code_version=1. When buckets or edge-case logic changes,
bump `ROIIC_TREND.code_version` in the catalog AND update the
calibration baselines in tests/qualitative/calibration_baselines.json.
"""

from __future__ import annotations

import hashlib
import statistics
from typing import List, Optional, Tuple

import pandas as pd

from . import ComputerResult


_FORMULA_VERSION = "roiic_trend_v1"
_LOOKBACK_YEARS = 5
_MIN_OBSERVATIONS = 3   # need at least 3 year-pair ROIICs to compute slope


def _compute_roiic_series(nopat: List[float], ic: List[float]) -> List[float]:
    """Damodaran ROIIC for each consecutive year pair, with the
    capital-release / clamp rules described in the module docstring."""
    out: List[float] = []
    for i in range(1, len(nopat)):
        d_nopat = nopat[i] - nopat[i - 1]
        d_ic    = ic[i]    - ic[i - 1]
        prior_ic_abs = abs(ic[i - 1]) or 1.0
        if abs(d_ic) <= 0.01 * prior_ic_abs:
            # capital-flat — Damodaran undefined
            if d_nopat > 0:
                out.append(0.50)   # capped capital-light-growth signal
            # else: skip silently (uninformative)
            continue
        if d_ic <= 0:
            # capital release — undefined under Damodaran but if NOPAT
            # also grew, that's the signature of an Apple-style
            # compounder. Cap at 0.50.
            if d_nopat > 0:
                out.append(0.50)
            continue
        roiic = d_nopat / d_ic
        # Clamp absurd values from tiny-but-positive ΔIC denominators
        roiic = max(min(roiic, 1.0), -1.0)
        out.append(roiic)
    return out


def _slope_pp_per_year(series: List[float]) -> float:
    """Simple OLS slope over the year-index. Returns slope in
    percentage-points per year. For a 4-year series with values
    [0.20, 0.22, 0.24, 0.26] this is +0.02 (200bps/yr improvement).
    Falls back to 0.0 when the input is degenerate (all equal)."""
    n = len(series)
    if n < 2:
        return 0.0
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = sum(series) / n
    num = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, series))
    den = sum((xi - x_mean) ** 2 for xi in x)
    if den == 0:
        return 0.0
    return num / den


def _bucket(median: float, slope: float) -> int:
    """Map (level, trajectory) to 1-7. Cutoffs locked at code_version=1.

    Cutoffs reflect what 20%+ ROIIC sustained means in practice
    (top-decile compounders) vs ~10% (decent) vs negative (value-
    destroying growth)."""
    if median > 0.25 and slope > -0.01: return 7
    if median > 0.20 and slope > -0.02: return 6
    if median > 0.15 or  (median > 0.20 and abs(slope) < 0.05): return 5
    if median > 0.10 or  (median > 0.15 and slope < -0.02):    return 4
    if median > 0.05: return 3
    if median >= 0:   return 2
    return 1


def _fingerprint(rows: List[Tuple[int, float, float]]) -> str:
    """Stable hash over (fiscal_year, NOPAT, IC) tuples used as inputs.
    When inputs change (re-clean), this changes and the staleness check
    detects it."""
    encoded = "|".join(f"{y}:{n:.2f}:{c:.2f}" for y, n, c in rows)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def compute_roiic_trend(df: pd.DataFrame) -> Optional[ComputerResult]:
    """Compute the ROIIC trend score for one ticker.

    Args:
        df: All cleaned records for the ticker, sorted by fiscal_year.
            Must contain `clean_NOPAT` and `derived_InvestedCapital`.

    Returns:
        ComputerResult with score 1-7, or score=None when the ticker has
        fewer than _MIN_OBSERVATIONS valid year-pair ROIIC observations
        in its last _LOOKBACK_YEARS years.
    """
    if df.empty:
        return None

    df = df.sort_values("fiscal_year").tail(_LOOKBACK_YEARS + 1)
    nopat = df["clean_NOPAT"].tolist()
    ic    = df["derived_InvestedCapital"].tolist()
    years = df["fiscal_year"].tolist()

    # Drop NaN rows up front — keep only year-pairs where both ends valid
    valid_rows: List[Tuple[int, float, float]] = []
    for y, n, c in zip(years, nopat, ic):
        if n is not None and c is not None and not (
            (isinstance(n, float) and n != n) or (isinstance(c, float) and c != c)
        ):
            valid_rows.append((int(y), float(n), float(c)))

    if len(valid_rows) < _MIN_OBSERVATIONS + 1:
        return ComputerResult(
            score=None,
            source_payload={"formula": _FORMULA_VERSION,
                            "reason": "insufficient_history"},
            input_fingerprint=_fingerprint(valid_rows),
            reason=f"Need at least {_MIN_OBSERVATIONS + 1} years of NOPAT+IC; got {len(valid_rows)}",
        )

    series = _compute_roiic_series(
        [r[1] for r in valid_rows],
        [r[2] for r in valid_rows],
    )

    if len(series) < _MIN_OBSERVATIONS:
        return ComputerResult(
            score=None,
            source_payload={"formula": _FORMULA_VERSION,
                            "reason": "insufficient_valid_observations",
                            "raw_series": series},
            input_fingerprint=_fingerprint(valid_rows),
            reason=(
                f"Need at least {_MIN_OBSERVATIONS} valid ROIIC observations; "
                f"got {len(series)} after edge-case filtering."
            ),
        )

    median = statistics.median(series)
    slope  = _slope_pp_per_year(series)
    score  = _bucket(median, slope)

    return ComputerResult(
        score=score,
        source_payload={
            "formula":          _FORMULA_VERSION,
            "lookback_years":   _LOOKBACK_YEARS,
            "roiic_series":     [round(x, 4) for x in series],
            "median_roiic":     round(median, 4),
            "slope_pp_per_yr":  round(slope, 4),
            "years_used":       [r[0] for r in valid_rows],
        },
        input_fingerprint=_fingerprint(valid_rows),
    )
