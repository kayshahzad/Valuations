"""Residual-income equity valuation — book value + the PV of excess returns.

The canonical home for the residual-income / justified-P/B math, shared by:
  - ``bank_valuation_methods`` (the financial-sector convergent-set diagnostic), and
  - ``ResidualIncomeEngine`` (the routed headline for no-dividend float/managed-care
    names like Centene, where DDM is undefined and FCFF mis-frames a thin-margin
    pass-through-revenue business).

Residual income reframes equity value as today's book plus the present value of
the returns earned ABOVE the cost of equity:

    IV = BVPS₀ + Σ PV[(ROE − Ke)·BVPSₜ₋₁] + PV[terminal residual income]

Under clean-surplus accounting this is algebraically identical to the dividend
discount value, but it front-loads value into book (less terminal-dependent) and
stays well-posed when the near-term sustainable growth g = ROE·retention exceeds
Ke — the two-stage form sums a finite super-growth explicit phase, then fades to
a terminal g < Ke. In steady state (constant ROE, g < Ke) it equals the justified
price-to-book closed form, (ROE − g)/(Ke − g), times book.
"""

from __future__ import annotations

from typing import Optional


def justified_pb(*, roe: float, ke: float, growth: float) -> Optional[float]:
    """Steady-state justified price-to-book = (ROE − g)/(Ke − g).

    Returns None when g ≥ Ke (the multiple is undefined — a firm cannot grow
    book faster than its cost of equity in perpetuity)."""
    if ke is None or roe is None or growth is None:
        return None
    if ke - growth <= 0:
        return None
    return (roe - growth) / (ke - growth)


def gordon_ddm_value(*, bvps0: float, roe: float, ke: float,
                     payout: float) -> Optional[float]:
    """Single-stage Gordon DDM off the steady-state book/ROE: P₀ = DPS₁/(Ke−g),
    DPS₁ = payout·ROE·BVPS₀, g = ROE·(1−payout). Undefined when g ≥ Ke."""
    if None in (bvps0, roe, ke, payout):
        return None
    g = roe * (1.0 - payout)
    if ke - g <= 0:
        return None
    dps1 = payout * roe * bvps0
    return dps1 / (ke - g)


def residual_income_value(*, bvps0: float, roe: float, ke: float,
                          retention: float, explicit_years: int,
                          terminal_roe: float,
                          terminal_growth: float) -> dict:
    """Two-stage residual income per share.

    Stage 1 (explicit): book compounds at g₁ = ROE·retention (may exceed Ke —
    that is fine over a finite sum). Each year RIₜ = (ROE − Ke)·BVPSₜ₋₁, PV'd @ Ke.
    Stage 2 (terminal): residual income on the terminal book, with ROE persisting
    at ``terminal_roe`` and growing at ``terminal_growth`` (< Ke), as a Gordon
    continuing value.

    Identity check (the golden test): with terminal_roe == roe and
    terminal_growth == g₁ < Ke, this telescopes EXACTLY to the closed form
    BVPS₀·(ROE − g)/(Ke − g)."""
    g1 = roe * retention
    bv = bvps0
    pv_explicit = 0.0
    schedule = []
    for t in range(1, explicit_years + 1):
        ri = (roe - ke) * bv
        pv = ri / (1.0 + ke) ** t
        pv_explicit += pv
        schedule.append({"year": t, "bvps_begin": bv, "ri": ri, "pv": pv})
        bv = bv * (1.0 + g1)
    bvps_terminal = bv                      # book at start of the terminal year

    pv_terminal = None
    if ke - terminal_growth > 0:
        ri_next = (terminal_roe - ke) * bvps_terminal
        cv = ri_next / (ke - terminal_growth)
        pv_terminal = cv / (1.0 + ke) ** explicit_years

    iv = bvps0 + pv_explicit + (pv_terminal or 0.0)
    return {
        "iv": iv,
        "implied_pb": (iv / bvps0) if bvps0 else None,
        "near_term_growth": g1,
        "decomposition": {
            "bvps0": bvps0,
            "pv_explicit_ri": pv_explicit,
            "pv_terminal_ri": pv_terminal,
            "bvps_terminal": bvps_terminal,
            "explicit_years": explicit_years,
            "schedule": schedule,
        },
    }


__all__ = ["justified_pb", "gordon_ddm_value", "residual_income_value"]
