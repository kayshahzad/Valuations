"""Shares-diluted FMP fallback resolver — anomaly A14 fix.

Some filers' XBRL doesn't expose a usable diluted-share tag and the
EPS-derived path (``shares = NetIncome / DilutedEPS``) also fails. The
canonical example is V (Visa): every FY 2009-2025 affected.

This module supplies the third-step fallback: read
``weightedAverageShsOutDil`` (or ``weightedAverageShsOut``) from FMP's
annual income statement for the matching fiscal_year.

Design:
  - Pure helper; no global state, no side effects beyond reading FMP
    cache (which the fmp_client already manages).
  - Returns ``(value, source)`` so callers can attach the source to
    the cleaned record's audit trail. ``source`` is one of:
      "fmp_income_statement_diluted",
      "fmp_income_statement_basic_fallback",
      "unavailable".
  - Cache-friendly: defers to ``fmp_client.fetch_income_statements``
    which respects the 7-day TTL. Network only fires if cache is
    cold or stale.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from aletheia.data import fmp_client


log = logging.getLogger(__name__)


# Field names FMP uses for the diluted / basic share counts on the
# annual income-statement endpoint. ``weightedAverageShsOutDil`` is
# preferred; ``weightedAverageShsOut`` is the basic fallback (used
# only when diluted is missing on FMP's side).
_DILUTED_FIELD = "weightedAverageShsOutDil"
_BASIC_FIELD = "weightedAverageShsOut"


def resolve_shares_diluted_from_fmp(
    *,
    ticker: str,
    fiscal_year: int,
) -> Tuple[Optional[float], str]:
    """Return (shares_diluted, source_label) for ``ticker`` at
    ``fiscal_year`` using FMP's annual income statement.

    Returns ``(None, "unavailable")`` if FMP has no entry for the year
    or if the income statement endpoint returned nothing (no API key,
    quota exhausted, etc.). Callers should treat ``None`` as "no
    fallback available" and propagate to the next step (or surface the
    gap to the analyst).
    """
    try:
        statements = fmp_client.fetch_income_statements(ticker, period="annual")
    except Exception as exc:  # noqa: BLE001 — defensive boundary
        log.warning(
            "shares_diluted_resolver: FMP fetch raised for %s/FY%d: %s",
            ticker, fiscal_year, exc,
        )
        return None, "unavailable"

    if not statements:
        return None, "unavailable"

    # FMP's annual statement uses ``calendarYear`` for the FY label.
    # Some filers (V, KO) report the prior-year-anchored fiscal_year
    # under ``fiscalYear`` as a string; calendarYear is the more
    # reliable integer field. Walk both as a guard.
    for stmt in statements:
        cy = stmt.get("calendarYear")
        fy = stmt.get("fiscalYear")
        try:
            cy_int = int(cy) if cy is not None else None
        except (TypeError, ValueError):
            cy_int = None
        try:
            fy_int = int(fy) if fy is not None else None
        except (TypeError, ValueError):
            fy_int = None
        if cy_int != fiscal_year and fy_int != fiscal_year:
            continue

        diluted = stmt.get(_DILUTED_FIELD)
        if diluted and float(diluted) > 0:
            log.info(
                "shares_diluted_resolver: FMP diluted shares used for "
                "%s/FY%d: %.0f (A14 fallback)",
                ticker, fiscal_year, float(diluted),
            )
            return float(diluted), "fmp_income_statement_diluted"

        basic = stmt.get(_BASIC_FIELD)
        if basic and float(basic) > 0:
            log.warning(
                "shares_diluted_resolver: FMP diluted shares unavailable "
                "for %s/FY%d; using basic shares=%.0f as last-resort "
                "fallback. Quantify dilution risk separately.",
                ticker, fiscal_year, float(basic),
            )
            return float(basic), "fmp_income_statement_basic_fallback"

        # Statement matched the year but neither field is usable.
        return None, "unavailable"

    return None, "unavailable"


__all__ = ["resolve_shares_diluted_from_fmp"]
