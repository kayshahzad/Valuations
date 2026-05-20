"""Derived primitives used by every higher-level formula.

NOPAT and InvestedCapital are the two inputs to ROIC. Centralizing them
here closes the cross-provider drift documented in
``docs/methodology_changes/2026-05-roic-invested-capital.md`` —
historically the FMP path used a full-cash IC definition while the
XBRL path used excess-cash netting with a 5%-of-revenue floor.

All functions are pure, keyword-only, and accept primitives (floats,
not records). This keeps the module independent of DB schema, provider
adapters, and DataFrame conventions — callers can wire it into any
calculation surface.
"""

from __future__ import annotations

from typing import Optional


def nopat(
    *,
    operating_income: Optional[float],
    tax_rate: Optional[float],
) -> Optional[float]:
    """NormalizedEBIT × (1 − tax_rate)

    Tax-rate resolution is the caller's responsibility — pass whatever
    rate the caller's tax-rate-resolver (typically
    ``aletheia.calculations._tax_rate.resolve_tax_rate``) returned.
    This function does not duplicate the fallback ladder; it just
    applies the rate.

    Returns ``None`` when ``operating_income`` is ``None`` or when the
    resolved rate is ``None`` — both inputs are required.
    """
    if operating_income is None or tax_rate is None:
        return None
    return operating_income * (1.0 - tax_rate)


# Excess-cash threshold: cash held above 2% of revenue is considered
# non-operating ("idle"). 2% reflects the working-cash requirement
# typical research uses (Damodaran convention).
EXCESS_CASH_REVENUE_RATIO: float = 0.02

# Invested-capital floor: 5% of revenue. Protects against pathological
# zero-or-negative IC for tickers with negative working capital + large
# cash hoards (some retailers, financial conglomerates).
INVESTED_CAPITAL_FLOOR_RATIO: float = 0.05


def invested_capital(
    *,
    total_equity: Optional[float],
    total_debt: Optional[float],
    cash: Optional[float],
    revenue: Optional[float],
) -> Optional[float]:
    """max(0.05 × Revenue, TotalEquity + TotalDebt − ExcessCash)

    Where ExcessCash = max(0, Cash − 0.02 × Revenue).

    The convention canonicalized 2026-05 (see
    ``docs/methodology_changes/2026-05-roic-invested-capital.md``):
    only idle cash above the working-cash threshold is netted out, and
    the result is floored at 5% of revenue.

    Returns ``None`` when required inputs are missing. ``revenue``
    being ``None`` defaults the excess-cash and floor to zero — the
    function still computes an unfloored ``Equity + Debt − Cash`` in
    that case for tickers without revenue context (rare, pre-IPO
    fixtures).
    """
    if total_equity is None or total_debt is None:
        return None
    cash = cash or 0.0
    rev = revenue or 0.0

    excess_cash = max(0.0, cash - rev * EXCESS_CASH_REVENUE_RATIO)
    raw_ic = total_debt + total_equity - excess_cash

    if rev > 0:
        floor = rev * INVESTED_CAPITAL_FLOOR_RATIO
        return max(floor, raw_ic)
    return raw_ic
