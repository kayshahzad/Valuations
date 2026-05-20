"""Cost of capital formulas — WACC + components.

Phase 4 of the centralization refactor. Pre-Phase 4 these lived
exclusively in ``aletheia.tools.dcf_engine.compute_wacc``; centralizing
the math here (without the live-data fetches and ticker lookups) lets
both the DCFEngine and any future caller (multiple_decomposition,
reverse_dcf, scenario_eval) share one source of truth.

The DCFEngine retains the *orchestration* (fetching live risk-free
rate, computing beta from price history, looking up ticker classifier
for sector overrides) — it just calls these primitives to do the
actual math.

Bounds + caps are part of the formula's definition (e.g. WACC floor
at max(4%, Rf+1%) and ceiling at 18%) — keeping them here ensures any
caller gets the same guarded result.
"""

from __future__ import annotations

from typing import Optional


# Default fallback when WACC computation produces a degenerate result
# (capital structure all-zero, negative inputs). Matches the constant
# previously held only in dcf_engine.py.
DEFAULT_WACC: float = 0.09

# Kd cap — anything above 15% interest/debt indicates either a data
# error (lease-related items mis-tagged as interest) or junk-grade
# debt that shouldn't anchor a base-case discount rate.
KD_CAP: float = 0.15

# Kd fallback when interest/debt isn't computable: Rf + 150bps credit
# spread (investment-grade approximation).
KD_FALLBACK_SPREAD: float = 0.015

# WACC bounds. Floor prevents sub-Rf WACC for cash-flush utilities
# (CNC-class). Ceiling caps junk-grade tickers from blowing up models.
WACC_CEILING: float = 0.18
WACC_FLOOR_MIN: float = 0.04


def cost_of_equity(
    *,
    risk_free_rate: Optional[float],
    beta: Optional[float],
    market_risk_premium: float,
) -> Optional[float]:
    """Rf + Beta × MRP    (CAPM)

    Returns ``None`` when either ``risk_free_rate`` or ``beta`` is
    missing — caller must supply them (the DCFEngine resolves them
    from live market data; tests can pin them).
    """
    if risk_free_rate is None or beta is None:
        return None
    return risk_free_rate + beta * market_risk_premium


def cost_of_debt(
    *,
    interest_expense: Optional[float],
    total_debt: Optional[float],
    risk_free_rate: Optional[float] = None,
    fallback_spread: float = KD_FALLBACK_SPREAD,
) -> Optional[float]:
    """|InterestExpense| / TotalDebt    (capped at ``KD_CAP``)

    When interest expense or total debt is missing or zero, falls
    back to ``Rf + fallback_spread`` (investment-grade approximation).
    Returns ``None`` only when both the primary calculation and the
    fallback are infeasible (``risk_free_rate`` is also missing).
    """
    if total_debt and total_debt > 0 and interest_expense and interest_expense > 0:
        kd = abs(interest_expense) / total_debt
        return min(kd, KD_CAP)
    if risk_free_rate is not None:
        return risk_free_rate + fallback_spread
    return None


def wacc(
    *,
    cost_of_equity: Optional[float],
    cost_of_debt: Optional[float],
    total_equity: Optional[float],
    total_debt: Optional[float],
    tax_rate: float,
    risk_free_rate: Optional[float] = None,
) -> float:
    """(E/V × Ke) + (D/V × Kd × (1 − tax_rate))

    Standard book-value-weighted WACC. Bounds applied:
      - Floor: ``max(WACC_FLOOR_MIN, Rf + 1%)``
      - Ceiling: ``WACC_CEILING``
      - Returns ``DEFAULT_WACC`` when capital structure is non-positive

    The bounds are part of the formula's definition, not the caller's —
    every site that calls ``wacc()`` gets the same guarded result.
    """
    if cost_of_equity is None or cost_of_debt is None:
        return DEFAULT_WACC
    if total_equity is None or total_debt is None:
        return DEFAULT_WACC

    total_capital = total_equity + total_debt
    if total_capital <= 0:
        return DEFAULT_WACC

    we = total_equity / total_capital
    wd = total_debt / total_capital
    raw = we * cost_of_equity + wd * cost_of_debt * (1.0 - tax_rate)

    floor = max(WACC_FLOOR_MIN, (risk_free_rate or 0.04) + 0.01)
    if raw < floor:
        return floor
    if raw > WACC_CEILING:
        return WACC_CEILING
    return raw
