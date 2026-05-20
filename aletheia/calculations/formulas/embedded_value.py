"""Embedded-value framework — for ``embedded_value_required`` filers.

Phase A.7. Used by ``EmbeddedValueEngine``. Targets insurance
conglomerates / sum-of-parts holdings (BRK-B today) where:

  - Classical FCFF DCF doesn't apply (insurance float distorts cash
    flow; operating subsidiaries are diverse)
  - Classical DDM doesn't apply (no dividend at BRK-B; capital
    retained at the holdco compounds book value instead)
  - The operative valuation framework is **book-value compounding
    + target multiple**:

    ``Future_BVPS = BVPS_0 × (1 + ROE)^N``
    ``Target_Equity_per_Share = Future_BVPS × Target_P/B``
    ``IV_per_Share = Target_Equity_per_Share / (1 + Ke)^N``

  Rationale: BRK-B's economics are dominated by:
    1. Insurance underwriting profit + float (Geico, BHRG, etc.)
    2. Operating subsidiaries (BNSF, BHE, See's, Pilot, etc.)
    3. Equity portfolio at market value (~$280B at recent peaks)

  All three contribute to book-value growth via retained earnings.
  Modeling at the consolidated BVPS-compounding level is more
  robust than trying to value each component separately — Buffett
  himself wrote in the 2018 letter that BVPS understates true
  value but is "the most useful single yardstick" available.

Target P/B reflects the market's persistent premium to book:
  - BRK-B has historically traded 1.3-1.5× book over the past
    decade (current ~1.6×). Target P/B should reflect a sustainable
    multi-cycle average, not the current peak.

Note: this is intentionally a simplified framework. A full
embedded-value model (insurance ANW + PVFP + holdco subsidiaries
DCF) would be Phase A.8+ if needed. Phase A.7 ships the BVPS-
compounding MVP which captures ~80% of the valuation signal for
BRK-B.
"""

from __future__ import annotations

from typing import List, Optional, Tuple


def book_value_compounding_intrinsic(
    *,
    book_value_per_share: float,
    expected_roe: float,
    cost_of_equity: float,
    target_pb: float,
    horizon_years: int,
    payout_ratio: float = 0.0,
) -> Optional[float]:
    """Compound BVPS at retained-earnings rate for N years, apply
    target P/B, discount back at cost of equity.

    Args:
        book_value_per_share: Starting BVPS.
        expected_roe: Decimal, e.g. 0.13 = 13%. Sustainable consolidated
            return on equity across the explicit horizon.
        cost_of_equity: Decimal, CAPM Ke.
        target_pb: Multiple to apply at the end of the horizon
            (1.3-1.5× for BRK-B historically).
        horizon_years: Compounding period (typically 5-10 years).
        payout_ratio: Decimal, fraction of earnings paid out (0 for
            BRK-B; reduces the BVPS growth rate). Defaults to 0.

    Returns ``None`` for missing / degenerate inputs.
    """
    if (book_value_per_share is None or book_value_per_share <= 0
            or expected_roe is None
            or cost_of_equity is None or cost_of_equity <= 0
            or target_pb is None or target_pb <= 0
            or horizon_years is None or horizon_years <= 0):
        return None

    # Retained-earnings growth = ROE × (1 - payout). Drives BVPS
    # compounding when no equity is issued/bought back at premium.
    retention = max(0.0, 1.0 - payout_ratio)
    growth = expected_roe * retention

    future_bvps = book_value_per_share * (1.0 + growth) ** horizon_years
    target_equity = future_bvps * target_pb
    pv_equity = target_equity / (1.0 + cost_of_equity) ** horizon_years
    return pv_equity


def book_value_compounding_decomposition(
    *,
    book_value_per_share: float,
    expected_roe: float,
    cost_of_equity: float,
    target_pb: float,
    horizon_years: int,
    payout_ratio: float = 0.0,
) -> Optional[dict]:
    """Year-by-year BVPS trajectory + final-year multiple application,
    for audit / UI display."""
    if (book_value_per_share is None or book_value_per_share <= 0
            or expected_roe is None
            or cost_of_equity is None or cost_of_equity <= 0
            or target_pb is None or target_pb <= 0
            or horizon_years is None or horizon_years <= 0):
        return None

    retention = max(0.0, 1.0 - payout_ratio)
    growth = expected_roe * retention

    yearly: List[Tuple[int, float]] = []
    bvps = book_value_per_share
    for t in range(1, horizon_years + 1):
        bvps = book_value_per_share * (1.0 + growth) ** t
        yearly.append((t, bvps))

    future_bvps = yearly[-1][1]
    target_equity = future_bvps * target_pb
    pv_equity = target_equity / (1.0 + cost_of_equity) ** horizon_years

    return {
        "starting_bvps":            book_value_per_share,
        "growth_rate":              growth,
        "retention_ratio":          retention,
        "yearly_bvps": [
            {"year": t, "bvps": bv} for (t, bv) in yearly
        ],
        "future_bvps":              future_bvps,
        "target_pb":                target_pb,
        "target_equity_per_share":  target_equity,
        "discount_factor":          (1.0 + cost_of_equity) ** horizon_years,
        "intrinsic_per_share":      pv_equity,
    }


__all__ = [
    "book_value_compounding_intrinsic",
    "book_value_compounding_decomposition",
]
