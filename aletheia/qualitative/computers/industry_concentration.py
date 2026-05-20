"""Industry concentration — top-3 cohort share within the ticker's sector.

Phase D MVP. The catalog dimension nominally wants Herfindahl-Hirschman
Index (HHI) or top-N share for the *actual* industry. We approximate
with **top-3 share across the in-universe peer cohort** — i.e. tickers
already in ``config.ticker_classification`` that share the current
ticker's ``sector``. Honest about the limitation in the narrative.

Why this MVP path (vs full HHI):
  - Zero new data dependency. Real industry HHI requires sector-wide
    revenue data (US Census, IBISWorld, FMP industry endpoints).
  - Unblocks the 16th and final catalog dim with a deterministic
    computer — analyst can override via HITL if the universe-cohort
    proxy is misleading.
  - Score direction is the standard 1=worst / 7=best framework:
    high concentration → high score (incumbent benefits structurally;
    moats / pricing power tend to follow). Caveat in the narrative
    that this cuts differently for the cohort's leader vs follower.

Score mapping (top-3 cohort share):
  > 80% → 7  (heavily concentrated; structural moats)
  60-80% → 6
  45-60% → 5
  30-45% → 4  (mid-cohort)
  20-30% → 3
  10-20% → 2
  < 10% → 1   (fragmented; commodity competition)

Small-cohort handling:
  - Cohort of 1 (TSLA in "Auto Manufacturers"): top-1 share = 100%,
    score = 7. We flag this in the narrative because the proxy is
    degenerate — the ticker is the only universe representative of
    the sector, not because the real industry is monopolized.
  - Cohort of 2-3: use top-2 share instead of top-3 (avoid degenerate
    100% for cohorts smaller than the N).

Locked at code_version=1.
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import ComputerResult


_FORMULA_VERSION = "industry_concentration_v1"


def _bucket(top_n_share: float) -> int:
    """Top-N share → 1-7. Higher share = more concentrated = higher score."""
    if top_n_share >= 0.80: return 7
    if top_n_share >= 0.60: return 6
    if top_n_share >= 0.45: return 5
    if top_n_share >= 0.30: return 4
    if top_n_share >= 0.20: return 3
    if top_n_share >= 0.10: return 2
    return 1


def _fingerprint(
    sector: str, cohort: List[Tuple[str, float]],
) -> str:
    """SHA over (sector, sorted-cohort-revenues). When the cohort
    membership changes (universe additions/removals) or any peer's
    revenue updates, the fingerprint changes — staleness check
    triggers a re-write."""
    encoded = sector + "|" + "|".join(
        f"{t}:{r:.0f}" for t, r in sorted(cohort)
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _peer_revenues_from_db(
    sector: str, exclude_ticker: str,
) -> Dict[str, float]:
    """Fetch latest FY revenue for every in-universe ticker classified
    under ``sector``. Returns ``{ticker: latest_fy_revenue}``. Tickers
    with no DB data are omitted (they're effectively pre-ingestion and
    shouldn't anchor the concentration estimate)."""
    from aletheia.data.database import InvestmentDatabase
    from config.ticker_classification import get_extended_universe

    peers = [
        t for t, c in get_extended_universe().items()
        if c.sector == sector
    ]

    out: Dict[str, float] = {}
    db = InvestmentDatabase(verbose=False)
    try:
        for peer in peers:
            try:
                hist = db.get_latest(peer)
            except Exception:
                continue
            if hist is None or hist.empty:
                continue
            fy_only = (
                hist[hist["period"] == "FY"]
                if "period" in hist.columns
                else hist
            )
            if fy_only.empty:
                continue
            latest = fy_only.sort_values("fiscal_year").iloc[-1]
            rev = latest.get("clean_Revenue")
            if rev is None or (isinstance(rev, float) and rev != rev) or rev <= 0:
                continue
            out[peer] = float(rev)
    finally:
        db.close()
    return out


def compute_industry_concentration(
    df: pd.DataFrame,
    *,
    peer_revenues: Optional[Dict[str, float]] = None,
) -> Optional[ComputerResult]:
    """Score the ticker's industry concentration via universe-cohort
    top-3 share.

    Args:
        df: Cleaned-records DataFrame for the current ticker. Used
            only for the ticker identifier — the calculation is
            cross-ticker (queries the universe + per-peer DB rows).
        peer_revenues: Optional override — ``{ticker: latest_fy_revenue}``
            for the ticker's sector cohort. Injectable for tests so
            the computer is unit-testable without a DB. Production
            calls pass ``None`` and the computer fetches from DB.

    Returns:
        ``ComputerResult`` with score 1-7 + cohort + share details, or
        ``None`` when the ticker isn't in the universe / the cohort
        is empty.
    """
    if df.empty:
        return None

    ticker = str(df["ticker"].iloc[0]).upper()

    # Resolve sector from the universe classification
    from config.ticker_classification import get_extended_universe
    universe = get_extended_universe()
    if ticker not in universe:
        return ComputerResult(
            score=None,
            source_payload={
                "formula": _FORMULA_VERSION,
                "reason": "ticker_not_in_universe",
            },
            input_fingerprint=hashlib.sha256(ticker.encode()).hexdigest()[:16],
            reason=f"{ticker} not in extended universe — no sector mapping",
        )
    sector = universe[ticker].sector

    # Fetch peer revenues (from DB by default; tests inject)
    if peer_revenues is None:
        peer_revenues = _peer_revenues_from_db(sector, exclude_ticker=ticker)

    if not peer_revenues:
        return ComputerResult(
            score=None,
            source_payload={
                "formula": _FORMULA_VERSION,
                "sector":  sector,
                "reason":  "empty_peer_cohort",
            },
            input_fingerprint=hashlib.sha256(
                (ticker + "|" + sector).encode()
            ).hexdigest()[:16],
            reason=f"No peer revenues available for sector={sector}",
        )

    # Cohort = peer_revenues. Always includes the current ticker
    # because `peer_revenues_from_db` doesn't actually exclude it
    # (the parameter is descriptive — the cohort IS the sector).
    cohort: List[Tuple[str, float]] = sorted(
        peer_revenues.items(), key=lambda x: x[1], reverse=True,
    )
    total_revenue = sum(r for _, r in cohort)
    if total_revenue <= 0:
        return ComputerResult(
            score=None,
            source_payload={
                "formula": _FORMULA_VERSION, "sector": sector,
                "reason":  "zero_total_revenue",
            },
            input_fingerprint=_fingerprint(sector, cohort),
            reason=f"Cohort total revenue is zero/negative for sector={sector}",
        )

    # Use top-3 share for cohorts ≥ 4; top-2 for cohorts of 2-3;
    # top-1 (= 100%) for a cohort of 1.
    cohort_size = len(cohort)
    if cohort_size >= 4:
        n_used = 3
    elif cohort_size >= 2:
        n_used = 2
    else:
        n_used = 1

    top_n_revenue = sum(r for _, r in cohort[:n_used])
    top_n_share = top_n_revenue / total_revenue
    score = _bucket(top_n_share)

    # Where this ticker sits in the cohort — affects analyst interpretation
    ticker_rank: Optional[int] = None
    ticker_share: Optional[float] = None
    for i, (t, r) in enumerate(cohort):
        if t == ticker:
            ticker_rank = i + 1
            ticker_share = r / total_revenue
            break

    return ComputerResult(
        score=score,
        source_payload={
            "formula":        _FORMULA_VERSION,
            "sector":         sector,
            "cohort_size":    cohort_size,
            "cohort":         [{"ticker": t, "revenue": r,
                                "share": r / total_revenue}
                               for t, r in cohort],
            "top_n_used":     n_used,
            "top_n_share":    round(top_n_share, 4),
            "ticker_rank":    ticker_rank,
            "ticker_share":   round(ticker_share, 4) if ticker_share else None,
            "note": (
                "MVP path — proxy uses the in-universe peer cohort, "
                "not the full industry. Real sector-wide HHI requires "
                "external data (US Census / IBISWorld / FMP industry "
                "endpoints). Score interpretation cuts differently for "
                "the cohort leader (high concentration = pricing power, "
                "good) vs followers (high concentration = dominated, "
                "less attractive). Use `ticker_rank` + `ticker_share` "
                "to interpret. Cohorts of 1-2 are degenerate; treat "
                "the score as 'no signal' rather than 'monopoly'."
            ),
        },
        input_fingerprint=_fingerprint(sector, cohort),
    )


__all__ = ["compute_industry_concentration"]
