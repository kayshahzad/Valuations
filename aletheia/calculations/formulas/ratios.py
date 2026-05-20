"""Ratios derived from primitive inputs.

Phase 1: ROIC only. Subsequent phases extend this module with the
other ratios in the centralization plan (gross/EBIT/EBITDA/FCF
margins, ROE, D/E, current ratio, interest coverage).
"""

from __future__ import annotations

from typing import Optional


def roic(
    *,
    nopat: Optional[float],
    invested_capital: Optional[float],
) -> Optional[float]:
    """NOPAT / InvestedCapital

    Both inputs must be supplied — typically computed via
    ``aletheia.calculations.formulas.nopat`` and
    ``aletheia.calculations.formulas.invested_capital`` respectively
    so the convention canonicalized 2026-05 applies.

    Returns ``None`` when either input is missing, or when
    ``invested_capital`` is zero or negative (a negative IC indicates
    pathological accounting — the formula would return a nonsense
    value with a positive sign).
    """
    if nopat is None or invested_capital is None:
        return None
    if invested_capital <= 0:
        return None
    return nopat / invested_capital


def roe(
    *,
    net_income: Optional[float],
    total_equity: Optional[float],
) -> Optional[float]:
    """NetIncome / TotalEquity

    Returns ``None`` when total_equity is None, zero, or negative.

    Aggressive-buyback companies (LOW, HD, AZO, DRI) drive book equity
    below zero via treasury-stock subtraction; the bare ratio
    NI/equity then produces a misleading negative percentage that
    suggests an operational problem the company doesn't have. ROIC
    (computed against invested capital) remains the meaningful
    return metric for these names — see ``ROE_suppressed_reason``
    in the cleaning_engine for the suppression flag callers can
    surface to analysts.
    """
    if net_income is None or total_equity is None:
        return None
    if total_equity <= 0:
        return None
    return net_income / total_equity
