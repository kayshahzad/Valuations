"""MLP (midstream master limited partnership) valuation formulas.

Phase A.10. Used by ``MlpEngine`` in ``aletheia.tools.valuation_engines``.

A midstream MLP (Energy Transfer, Enterprise Products, etc.) is a fee-based
toll-road business: stable, contracted EBITDA, very high leverage, and a
distribution policy that returns cash to unitholders. FCFF is the WRONG frame —
the large growth capex that builds fee-generating assets reads as value
destruction, and the $60B+ net debt is invisible until you bridge EV→equity.

The right anchor is the **EV/EBITDA multiple**: the market values midstream on a
multiple of its stable EBITDA, and equity is what's left after the (large) net
debt. A distribution-discount leg (two-stage Gordon on distributions per unit)
is the complementary income view, computed via the shared DDM formula.

  Enterprise Value = EBITDA × target_multiple
  Equity Value     = Enterprise Value − Net Debt
  IV per unit      = Equity Value / units

Zero formula logic lives in the engine; every numeric step is here (Phase 5
architecture lock).
"""

from __future__ import annotations

from typing import Optional


def ev_ebitda_equity_value(
    *,
    ebitda: float,                 # $ EBITDA (stable, fee-based)
    ev_ebitda_multiple: float,     # target EV/EBITDA (peer-comp anchored)
    net_debt: float,               # $ net debt (EV → equity bridge)
) -> Optional[float]:
    """Equity value from an EV/EBITDA multiple, net of debt.

    Returns ``None`` when EBITDA or the multiple is missing/non-positive (a
    non-positive EBITDA can't be multiple-valued — the caller should fall back).
    ``net_debt`` is signed: positive = net borrower (the usual MLP case),
    negative = net cash. Equity may come out negative if leverage exceeds the
    enterprise value — reported honestly, never floored."""
    if ebitda is None or ev_ebitda_multiple is None or net_debt is None:
        return None
    if ebitda <= 0 or ev_ebitda_multiple <= 0:
        return None
    enterprise_value = ebitda * ev_ebitda_multiple
    return enterprise_value - net_debt


def ev_ebitda_decomposition(
    *,
    ebitda: float,
    ev_ebitda_multiple: float,
    net_debt: float,
    units: Optional[float] = None,
) -> Optional[dict]:
    """The EV→equity bridge for audit / UI display: EV, net debt, equity, and
    (if units given) per-unit IV + the leverage read. ``None`` when the
    underlying value is undefined."""
    equity = ev_ebitda_equity_value(
        ebitda=ebitda, ev_ebitda_multiple=ev_ebitda_multiple, net_debt=net_debt)
    if equity is None:
        return None
    enterprise_value = ebitda * ev_ebitda_multiple
    return {
        "ebitda": ebitda,
        "ev_ebitda_multiple": ev_ebitda_multiple,
        "enterprise_value": enterprise_value,
        "net_debt": net_debt,
        "net_debt_to_ebitda": (net_debt / ebitda) if ebitda else None,
        "equity_value": equity,
        "per_unit": (equity / units) if units else None,
    }


__all__ = ["ev_ebitda_equity_value", "ev_ebitda_decomposition"]
