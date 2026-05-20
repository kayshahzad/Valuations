"""Margin formulas — Gross / EBIT / EBITDA / FCF.

Phase 3 of the centralization refactor. All four margins use the same
shape (`numerator / revenue × 100`) and were identical across both
adapter paths pre-migration. Consolidating here so the formula lives
in one place even though no numerical behavior changes.

Returns ``None`` for zero or negative revenue (the ratio is undefined
or operationally meaningless). Returns the result in percentage units
(`12.34`), not fractional (`0.1234`) — both adapters were already
using the percentage convention.
"""

from __future__ import annotations

from typing import Optional


def _safe_margin_pct(
    numerator: Optional[float], revenue: Optional[float],
) -> Optional[float]:
    """Common helper — used by all four margin functions."""
    if numerator is None or revenue is None or revenue <= 0:
        return None
    return numerator / revenue * 100.0


def gross_margin_pct(
    *,
    gross_profit: Optional[float],
    revenue: Optional[float],
) -> Optional[float]:
    """GrossProfit / Revenue × 100"""
    return _safe_margin_pct(gross_profit, revenue)


def ebit_margin_pct(
    *,
    ebit: Optional[float],
    revenue: Optional[float],
) -> Optional[float]:
    """EBIT / Revenue × 100

    Callers pass either OperatingIncome or NormalizedEBIT depending on
    whether they want raw or normalized; both paths historically used
    NormalizedEBIT (with cleaning_engine doing the normalization).
    """
    return _safe_margin_pct(ebit, revenue)


def ebitda_margin_pct(
    *,
    ebitda: Optional[float],
    revenue: Optional[float],
) -> Optional[float]:
    """EBITDA / Revenue × 100"""
    return _safe_margin_pct(ebitda, revenue)


def fcf_margin_pct(
    *,
    fcf: Optional[float],
    revenue: Optional[float],
) -> Optional[float]:
    """FCF / Revenue × 100"""
    return _safe_margin_pct(fcf, revenue)
