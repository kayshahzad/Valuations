"""Valuation multiples — pure ratios over market + fundamental inputs.

Phase 4 of the centralization refactor. Pre-Phase 4 these formulas
were duplicated across ``aletheia.tools.screening_ratios`` and
``aletheia.tools.multiple_decomposition`` — both used the same math
(MarketCap / NetIncome for P/E, EV / EBITDA for EV/EBITDA, etc.) but
the math lived in two places.

All functions return ``None`` when the denominator is zero, negative,
or missing — the multiples are undefined in those cases. Callers that
want a "fallback" for chart display should handle the None at the
display layer, not by suppressing it here.

The justified-multiple formula (Liberti EV/EBITDA decomposition) also
lives here so all valuation-multiple math is co-located.
"""

from __future__ import annotations

from typing import Optional


def _safe_ratio(
    numerator: Optional[float], denominator: Optional[float],
    *, denom_must_be_positive: bool = True,
) -> Optional[float]:
    """Common safety helper — used by every ratio in this module."""
    if numerator is None or denominator is None:
        return None
    if denom_must_be_positive and denominator <= 0:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


def price_to_earnings(
    *,
    price: Optional[float],
    eps: Optional[float],
) -> Optional[float]:
    """Price / EPS

    Returns ``None`` when EPS ≤ 0 (loss-year companies — P/E is
    undefined; analysts use P/Sales or EV/EBITDA instead).
    """
    return _safe_ratio(price, eps)


def price_to_book(
    *,
    market_cap: Optional[float],
    book_equity: Optional[float],
) -> Optional[float]:
    """MarketCap / BookEquity

    Returns ``None`` for negative book equity (aggressive-buyback
    filers like LOW/HD post-treasury-stock; the bare ratio is
    misleading — same rationale as ROE suppression in Phase 3).
    """
    return _safe_ratio(market_cap, book_equity)


def ev_to_ebitda(
    *,
    enterprise_value: Optional[float],
    ebitda: Optional[float],
) -> Optional[float]:
    """EV / EBITDA"""
    return _safe_ratio(enterprise_value, ebitda)


def ev_to_ebit(
    *,
    enterprise_value: Optional[float],
    ebit: Optional[float],
) -> Optional[float]:
    """EV / EBIT"""
    return _safe_ratio(enterprise_value, ebit)


def ev_to_fcf(
    *,
    enterprise_value: Optional[float],
    fcf: Optional[float],
) -> Optional[float]:
    """EV / FCF"""
    return _safe_ratio(enterprise_value, fcf)


def price_to_sales(
    *,
    market_cap: Optional[float],
    revenue: Optional[float],
) -> Optional[float]:
    """MarketCap / Revenue"""
    return _safe_ratio(market_cap, revenue)


def net_debt_to_ebitda(
    *,
    net_debt: Optional[float],
    ebitda: Optional[float],
) -> Optional[float]:
    """NetDebt / EBITDA

    Negative result is meaningful (net-cash filer). EBITDA must be
    positive — for loss-EBITDA years the leverage ratio is undefined.
    """
    if net_debt is None or ebitda is None:
        return None
    if ebitda <= 0:
        return None
    return net_debt / ebitda


def debt_to_equity(
    *,
    total_debt: Optional[float],
    total_equity: Optional[float],
) -> Optional[float]:
    """TotalDebt / TotalEquity

    Returns ``None`` for non-positive equity — same suppression
    rationale as P/B and ROE.
    """
    return _safe_ratio(total_debt, total_equity)


def interest_coverage(
    *,
    ebit: Optional[float],
    interest_expense: Optional[float],
) -> Optional[float]:
    """EBIT / |InterestExpense|

    Interest expense is taken as magnitude (sign varies by source).
    Returns ``None`` when interest is zero (debt-free filer — ratio
    is "infinitely covered", display should show that explicitly,
    not a None).
    """
    if ebit is None or interest_expense is None:
        return None
    abs_interest = abs(interest_expense)
    if abs_interest == 0:
        return None
    return ebit / abs_interest


def current_ratio(
    *,
    current_assets: Optional[float],
    current_liabilities: Optional[float],
) -> Optional[float]:
    """CurrentAssets / CurrentLiabilities"""
    return _safe_ratio(current_assets, current_liabilities)


def dividend_yield(
    *,
    dividends_paid: Optional[float],
    market_cap: Optional[float],
) -> Optional[float]:
    """DividendsPaid / MarketCap"""
    return _safe_ratio(dividends_paid, market_cap)


# ── Justified-multiple math (Liberti EV/EBITDA decomposition) ─────


# Floor on ROIC used in the cash-conversion calculation. Below 8%
# real ROIC the formula becomes unstable (denominator near zero) and
# produces multiples that aren't operationally meaningful. The floor
# is part of the formula's definition.
JUSTIFIED_ROIC_FLOOR: float = 0.08


def justified_ev_ebitda(
    *,
    nopat: float,
    ebitda: float,
    roic: float,
    wacc: float,
    terminal_growth: float,
) -> Optional[float]:
    """[NOPAT × (1 − g/ROIC) / EBITDA] / (WACC − g)

    Liberti's mathematical justification: every multiple is implied
    by the trio (ROIC, WACC, terminal growth). When WACC ≤ g the
    formula diverges (the implicit perpetual growth exceeds the
    discount rate); returns ``None`` rather than a nonsense value.
    Returns 0 (not negative) when the cash-conversion ratio is
    negative — analysts read 0 as "no equity value to multiple."
    """
    effective_roic = max(roic, JUSTIFIED_ROIC_FLOOR)
    if ebitda <= 0 or wacc <= terminal_growth:
        return None
    cash_conv = nopat * (1.0 - terminal_growth / effective_roic) / ebitda
    return max(cash_conv / (wacc - terminal_growth), 0.0)


def cash_conversion_ratio(
    *,
    nopat: float,
    ebitda: float,
    roic: float,
    terminal_growth: float,
) -> Optional[float]:
    """NOPAT × (1 − g/ROIC) / EBITDA

    The reinvestment-adjusted cash conversion underlying the Liberti
    formula. Returns ``None`` when EBITDA is non-positive.
    """
    if ebitda <= 0:
        return None
    effective_roic = max(roic, JUSTIFIED_ROIC_FLOOR)
    return nopat * (1.0 - terminal_growth / effective_roic) / ebitda
