"""Utility-filer XBRL taxonomy extensions — A19 fix.

Some filers in the universe (NEE, future regulated-utility additions)
file under a utility-specific XBRL taxonomy and don't report the
standard tags the canonical tag_mappings resolves. This module
provides the framework + the verified mappings for those filers.

Currently shipped:
  - ``capex_from_construction_in_progress``: NEE CapEx fallback.
    NEE files ``ConstructionInProgressGross`` (a balance-sheet stock
    item) instead of a cash-flow CapEx tag. The annual flow is
    approximated as the year-over-year change in CIP plus the
    completed-additions component that rolled out of CIP into PPE.

Deferred to analyst-verification (not yet wired):
  - NEE TotalLiabilities aggregation. The override entry in
    ``aletheia/calculations/_overrides.OVERRIDES["NEE"]
    ["utility_total_liabilities_aggregation"]`` describes the gap,
    but empirical inspection shows the historical drift is NEGATIVE
    (A < L + E) — meaning Liabilities or Equity is OVER-counting,
    not under-counting as the override description suggested. The
    correct fix needs reconciliation against the actual filings;
    until then, the override absorbs the violation in the schema
    contract.

This module is consulted ONLY by the cleaning_engine (Stage 2).
Stage 1 stays pure: it stores raw XBRL bytes without interpretation.
The taxonomy decisions happen during the cleaning pass.
"""

from __future__ import annotations

import logging
from typing import Optional

from config.ticker_classification import UNIVERSE


log = logging.getLogger(__name__)


# Sector classification flag used to gate this module's logic. The
# universe presently uses ``sector="Utilities"`` for NEE; any future
# regulated-utility additions inherit this path automatically when
# tagged with the same sector. Non-utility filers are unaffected.
_UTILITY_SECTORS = frozenset({"Utilities"})


def is_utility_filer(ticker: str) -> bool:
    """True when the ticker's classification marks it as a regulated
    utility filer that needs the utility-specific tag handling."""
    cls = UNIVERSE.get(ticker)
    if cls is None:
        return False
    return cls.sector in _UTILITY_SECTORS


def capex_from_construction_in_progress(
    *,
    ticker: str,
    fiscal_year: int,
    cip_this_year: Optional[float],
    cip_prior_year: Optional[float],
    ppe_additions_complete: Optional[float] = None,
) -> Optional[float]:
    """Approximate annual CapEx flow for a regulated-utility filer
    that doesn't report ``PaymentsToAcquirePropertyPlantAndEquipment``.

    Method: CapEx_t ≈ (CIP_t − CIP_{t−1}) + completed_additions_t

    The first term captures plant under construction that's still
    in progress at year-end (cash spent but not yet placed in
    service). The second captures additions that completed during
    the year and rolled out of CIP into operating PP&E — i.e., cash
    spent in a prior period for plant that became operational this
    year. Together they approximate the cash CapEx flow that a
    non-utility filer would have reported under
    ``PaymentsToAcquirePropertyPlantAndEquipment``.

    Returns ``None`` when both CIP datapoints are unavailable. When
    prior-year CIP is missing but current is present, returns the
    current-year value alone as a fail-soft estimate (the first year
    of recorded data).

    Caller responsibility: this function is approximation, not
    derivation. Downstream consumers (DCF reverse-DCF) should flag a
    utility-CapEx source on the resulting record so the analyst
    understands it's not a direct XBRL value.
    """
    if not is_utility_filer(ticker):
        # Defensive: only applies to utility filers. Returning None
        # here makes the function a no-op for non-utility consumers.
        return None

    if cip_this_year is None and cip_prior_year is None:
        return None

    completed = ppe_additions_complete or 0.0
    if cip_this_year is None:
        # First-year edge case: no current CIP, just completed
        # additions. Rare; we don't have a value to combine.
        return None
    if cip_prior_year is None:
        # First recorded year — no prior to diff against. Return the
        # full CIP value as the conservative estimate.
        log.info(
            "utility_taxonomy: ticker=%s FY%d using CIP-only CapEx "
            "estimate (no prior-year CIP available)",
            ticker, fiscal_year,
        )
        return float(cip_this_year) + completed

    delta_cip = float(cip_this_year) - float(cip_prior_year)
    estimate = delta_cip + completed
    log.info(
        "utility_taxonomy: ticker=%s FY%d CapEx ≈ ΔCIP %.0f + "
        "completed %.0f = %.0f (utility-CapEx fallback, A19)",
        ticker, fiscal_year, delta_cip, completed, estimate,
    )
    return estimate


__all__ = [
    "is_utility_filer",
    "capex_from_construction_in_progress",
]
