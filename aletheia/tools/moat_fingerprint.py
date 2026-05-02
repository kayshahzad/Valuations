"""
aletheia/tools/moat_fingerprint.py

Deterministic moat fingerprint v1.0. Implementation of the spec at
`aletheia/tools/MOAT_FINGERPRINT_METHODOLOGY.md`. The MD is the source of
truth; code follows. Any threshold or weight change must update the MD,
bump the version header, and re-run the determinism gate.

No LLM calls. No randomness. No time-dependent thresholds. Same input data
always produces the same fingerprint.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

import numpy as np

from aletheia.contracts.interfaces import CalculationInput

logger = logging.getLogger(__name__)


# Hard configuration — must match the methodology MD.
WINDOW_MAX_YEARS = 10
WINDOW_MIN_YEARS = 5

ROIC_THRESHOLD = 0.08  # ROIC must clear 8% to count as a "good" year

# Component weights (sum to 100)
WEIGHT_ROIC_PERSISTENCE = 50
WEIGHT_GM_STABILITY = 30
WEIGHT_CAPEX_INTENSITY = 20

# Cyclical sectors that trigger sector-relative GM scoring
CYCLICAL_SECTORS = frozenset({"Energy", "Materials", "Industrials"})
CYCLICAL_LIFECYCLES = frozenset({"cyclical_industrial"})

CYCLICAL_PEER_SET_MIN = 3  # below this, fall back to absolute thresholds


@dataclass(frozen=True)
class MoatFingerprint:
    score: Optional[int]                      # 1-5, or None if insufficient history
    roic_persistence_score: Optional[int]     # 1-5
    gm_stability_score: Optional[int]         # 1-5
    capex_intensity_score: Optional[int]      # 1-5
    window_years: int
    is_null_due_to_history: bool
    fallback_used: Optional[str]              # e.g. "absolute_gm_thresholds"
    rationale: str

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "roic_persistence_score": self.roic_persistence_score,
            "gm_stability_score": self.gm_stability_score,
            "capex_intensity_score": self.capex_intensity_score,
            "window_years": self.window_years,
            "is_null_due_to_history": self.is_null_due_to_history,
            "fallback_used": self.fallback_used,
            "rationale": self.rationale,
        }


def _select_window(df: pd.DataFrame) -> pd.DataFrame:
    """Last N years (most recent), capped at WINDOW_MAX_YEARS."""
    sorted_df = df.sort_values("fiscal_year")
    return sorted_df.tail(WINDOW_MAX_YEARS)


def _score_roic_persistence(roic_series: List[float]) -> int:
    """
    Component 1 — ROIC persistence (50% weight).
    Equal-year weighting per Simplification 1 in the MD.
    """
    if not roic_series:
        return 1
    n = len(roic_series)
    above_threshold_count = sum(1 for r in roic_series if r >= ROIC_THRESHOLD)
    pct_above = above_threshold_count / n
    median_roic = float(statistics.median(roic_series))

    if pct_above == 1.0 and median_roic >= 0.15:
        return 5
    if pct_above >= 0.80 and median_roic >= 0.12:
        return 4
    if pct_above >= 0.50:
        return 3
    if pct_above >= 0.25:
        return 2
    return 1


def _coefficient_of_variation(series: List[float]) -> Optional[float]:
    """std / mean. Returns None when not computable (n<2 or mean<=0)."""
    if len(series) < 2:
        return None
    mean = float(np.mean(series))
    if mean <= 0:
        return None
    std = float(np.std(series, ddof=0))
    return std / mean


def _score_gm_stability_absolute(cv: Optional[float]) -> int:
    """Component 2 — absolute thresholds (non-cyclical or cyclical fallback)."""
    if cv is None:
        return 1
    if cv <= 0.05:
        return 5
    if cv <= 0.10:
        return 4
    if cv <= 0.20:
        return 3
    if cv <= 0.35:
        return 2
    return 1


def _score_gm_stability_relative(cv: Optional[float], peer_median_cv: float) -> int:
    """
    Component 2 — sector-relative thresholds. A ticker scoring well below the
    peer median CV (more stable than peers) gets a high score.

    score 5/5: cv ≤ 0.7 × peer_median
    score 4/5: cv ≤ 0.85 × peer_median
    score 3/5: cv ≤ 1.0 × peer_median
    score 2/5: cv ≤ 1.25 × peer_median
    score 1/5: cv > 1.25 × peer_median
    """
    if cv is None or peer_median_cv <= 0:
        return 1
    ratio = cv / peer_median_cv
    if ratio <= 0.70:
        return 5
    if ratio <= 0.85:
        return 4
    if ratio <= 1.00:
        return 3
    if ratio <= 1.25:
        return 2
    return 1


def _score_capex_intensity(capex_pct_series: List[float]) -> int:
    """Component 3 — median capex/revenue across window."""
    if not capex_pct_series:
        return 1
    median_pct = float(statistics.median(capex_pct_series))
    if median_pct <= 0.02:
        return 5
    if median_pct <= 0.05:
        return 4
    if median_pct <= 0.10:
        return 3
    if median_pct <= 0.15:
        return 2
    return 1


def _is_cyclical(classification) -> bool:
    if classification is None:
        return False
    if classification.lifecycle in CYCLICAL_LIFECYCLES:
        return True
    return classification.sector in CYCLICAL_SECTORS


def compute_moat_fingerprint(
    calc_input: CalculationInput,
    cyclical_peer_gm_cv_median: Optional[float] = None,
) -> MoatFingerprint:
    """
    Compute the moat fingerprint per the locked methodology.

    Returns a `MoatFingerprint` with score=None when history is insufficient
    (`is_null_due_to_history=True`). Cyclical tickers fall back to absolute
    GM thresholds with a logged warning when the peer set is too small.
    """
    df = calc_input.df
    classification = calc_input.classification
    ticker = classification.ticker if classification else "UNKNOWN"

    if df is None or df.empty:
        return MoatFingerprint(
            score=None,
            roic_persistence_score=None,
            gm_stability_score=None,
            capex_intensity_score=None,
            window_years=0,
            is_null_due_to_history=True,
            fallback_used=None,
            rationale="No history available.",
        )

    window = _select_window(df)
    window_years = len(window)

    if window_years < WINDOW_MIN_YEARS:
        return MoatFingerprint(
            score=None,
            roic_persistence_score=None,
            gm_stability_score=None,
            capex_intensity_score=None,
            window_years=window_years,
            is_null_due_to_history=True,
            fallback_used=None,
            rationale=(
                f"Only {window_years} years of clean data; minimum is "
                f"{WINDOW_MIN_YEARS}. Insufficient history."
            ),
        )

    # Series extraction; drop NaN values per component (each component has its
    # own missingness tolerance).
    def _series(col: str) -> List[float]:
        if col not in window.columns:
            return []
        return [
            float(v) for v in window[col]
            if v is not None and not (isinstance(v, float) and np.isnan(v))
        ]

    roic_series = _series("derived_ROIC")
    gm_series = _series("derived_GrossMargin_Pct")

    # capex/revenue series
    capex_series_raw = _series("derived_CapEx")
    rev_series = _series("clean_Revenue")
    capex_pct_series: List[float] = []
    if capex_series_raw and rev_series:
        # Re-zip from window directly to keep year alignment
        for capex, rev in zip(window["derived_CapEx"], window["clean_Revenue"]):
            if capex is None or rev is None:
                continue
            if isinstance(capex, float) and np.isnan(capex):
                continue
            if isinstance(rev, float) and np.isnan(rev):
                continue
            if rev <= 0:
                continue
            capex_pct_series.append(float(capex) / float(rev))

    # ── Component scores ────────────────────────────────────────────────────
    roic_score = _score_roic_persistence(roic_series)
    capex_score = _score_capex_intensity(capex_pct_series)

    cv = _coefficient_of_variation(gm_series) if gm_series else None

    fallback_used: Optional[str] = None
    if _is_cyclical(classification):
        # The cyclical peer median is INJECTED by the caller (calc_node) — the
        # calc layer must not import config to discover peers. When the caller
        # didn't supply a median (peer set too small per the methodology
        # MD's CYCLICAL_PEER_SET_MIN rule), we fall back to absolute GM
        # thresholds and log the deterministic warning.
        if cyclical_peer_gm_cv_median is None:
            logger.warning(
                "[WARN] cyclical peer set too small for %s, "
                "using absolute GM thresholds", ticker
            )
            print(
                f"[WARN] cyclical peer set too small for {ticker}, "
                f"using absolute GM thresholds"
            )
            gm_score = _score_gm_stability_absolute(cv)
            fallback_used = "absolute_gm_thresholds"
        else:
            gm_score = _score_gm_stability_relative(cv, cyclical_peer_gm_cv_median)
    else:
        gm_score = _score_gm_stability_absolute(cv)

    # ── Weighted total (1-5 integer) ────────────────────────────────────────
    weighted = (
        roic_score * WEIGHT_ROIC_PERSISTENCE
        + gm_score * WEIGHT_GM_STABILITY
        + capex_score * WEIGHT_CAPEX_INTENSITY
    ) / (WEIGHT_ROIC_PERSISTENCE + WEIGHT_GM_STABILITY + WEIGHT_CAPEX_INTENSITY)
    final_score = max(1, min(5, int(round(weighted))))

    # ── Rationale ───────────────────────────────────────────────────────────
    rationale_bits = []
    if roic_series:
        n = len(roic_series)
        above = sum(1 for r in roic_series if r >= ROIC_THRESHOLD)
        median_roic = float(statistics.median(roic_series))
        rationale_bits.append(
            f"{above}/{n} years ROIC≥{ROIC_THRESHOLD:.0%} (median {median_roic:.1%})"
        )
    if cv is not None:
        rationale_bits.append(f"GM CV={cv:.2f}")
    if capex_pct_series:
        median_capex_pct = float(statistics.median(capex_pct_series))
        rationale_bits.append(f"capex intensity {median_capex_pct:.1%}")
    rationale = "; ".join(rationale_bits) if rationale_bits else "(no series available)"
    if fallback_used:
        rationale = f"[fallback={fallback_used}] {rationale}"

    return MoatFingerprint(
        score=final_score,
        roic_persistence_score=roic_score,
        gm_stability_score=gm_score,
        capex_intensity_score=capex_score,
        window_years=window_years,
        is_null_due_to_history=False,
        fallback_used=fallback_used,
        rationale=rationale,
    )
