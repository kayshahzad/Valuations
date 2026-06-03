"""Mezzanine / temporary-equity FMP fallback resolver.

Some filers carry redeemable convertible preferred stock as mezzanine
(temporary) equity — a line that sits between liabilities and permanent
stockholders' equity on the balance sheet (ASC 480-10-S99). XBRL tags it
under ``TemporaryEquityCarryingAmountAttributableToParent``, but SEC's
companyfacts API only exposes the us-gaap/dei/srt/ecd namespaces — when a
filer reports the line under a company *extension* tag, companyfacts drops
it entirely and the resolver sees nothing.

Canonical example — CELH (Celsius Holdings):
  - FY2022-2024: PepsiCo's 2022 $550M Series A convertible preferred is
    tagged ``TemporaryEquityCarryingAmountAttributableToParent`` = $824.49M
    and resolves cleanly from companyfacts.
  - FY2025 year-end: the carrying amount grows to $1.76B (Alani Nu
    acquisition consideration) but the XBRL tag stops reporting; the line
    survives only as the residual in ``LiabilitiesAndStockholdersEquity``.
    Without it, A ≠ L + E by $1.76B and Stage 3 hard-blocks.

FMP exposes the full carrying amount under ``preferredStock`` on the annual
balance sheet across every year (verified on CELH: $824M FY2022-2024,
$1.76B FY2025). This module supplies the fallback: when XBRL doesn't
populate ``TemporaryEquityCarryingAmount``, read FMP's ``preferredStock``
for the matching fiscal year.

Design mirrors ``shares_diluted_resolver`` (anomaly A14):
  - Pure helper; no global state. Reads the FMP disk cache (7-day TTL)
    via ``fmp_client``; network only fires on a cold/stale cache.
  - Returns ``(value, source)`` so callers can attach provenance.
  - Fires only as a SECOND source — when SEC/XBRL misses. FMP's
    ``preferredStock`` is 0/absent for the vast majority of the universe
    (no preferred), so this is a no-op for everyone except the handful of
    filers with mezzanine convertible preferred.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from aletheia.data import fmp_client


log = logging.getLogger(__name__)

# FMP balance-sheet field carrying the redeemable / mezzanine preferred
# stock carrying amount. FMP folds this into ``totalStockholdersEquity``
# but also exposes it discretely here.
_PREFERRED_FIELD = "preferredStock"


def resolve_temporary_equity_from_fmp(
    *,
    ticker: str,
    fiscal_year: int,
) -> Tuple[Optional[float], str]:
    """Return (temporary_equity, source_label) for ``ticker`` at
    ``fiscal_year`` from FMP's annual balance sheet ``preferredStock``.

    Returns ``(None, "unavailable")`` when FMP has no entry for the year,
    the field is missing/zero, or the balance endpoint returned nothing
    (no API key, quota exhausted, etc.). Callers treat ``None`` as "no
    fallback available".
    """
    try:
        statements = fmp_client.fetch_balance_sheets(ticker, period="annual")
    except Exception as exc:  # noqa: BLE001 — defensive boundary
        log.warning(
            "preferred_equity_resolver: FMP fetch raised for %s/FY%d: %s",
            ticker, fiscal_year, exc,
        )
        return None, "unavailable"

    if not statements:
        return None, "unavailable"

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

        preferred = stmt.get(_PREFERRED_FIELD)
        try:
            pv = float(preferred) if preferred is not None else 0.0
        except (TypeError, ValueError):
            pv = 0.0
        if pv > 0:
            log.info(
                "preferred_equity_resolver: FMP preferredStock used for "
                "%s/FY%d: %.0f (XBRL temporary-equity tag missing)",
                ticker, fiscal_year, pv,
            )
            return pv, "fmp_balance_sheet_preferred"

        # Statement matched the year but no usable preferred line.
        return None, "unavailable"

    return None, "unavailable"


__all__ = ["resolve_temporary_equity_from_fmp"]
