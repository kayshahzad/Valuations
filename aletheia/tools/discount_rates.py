"""Flag-selected discount-rate engine (Valuation-Methods plan, Phase 0c).

The capital-structure stability flag picks ONE formula family (CF-R8), and that
family sets r_A, r_E, r_TS, WACC, the unlever/relever forms, AND PV(tax shield)
together — never mixed. Two families:

  * Family A — constant D/V (rebalancing): tax shield rides the asset side, so
    r_TS = r_A and the (1−τ) factor VANISHES.
  * Family B — constant D (fixed level/schedule, distress/LBO): tax shield is as
    risky as debt, so r_TS = r_D and the (1−τ) factor APPEARS.

Beta is bound to the production path (CF-R7): the rate engine imports
``_compute_beta`` and unlevers/relevers its output — it does NOT re-implement a
regression. Pure functions throughout so the golden fixture (T1/T1b) tests the
exact math the engine uses.

PV(tax-shield) note: §2's closed forms are the no-growth perpetuity. With growth
g the Family-A shield grows with the firm → divide by (r_A − g); the Family-B
shield is fixed at τ·D regardless of g. The compressed-APV identity
(CCF discounted at r_A = VU + PV(TS)) only holds with the growth-aware form.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RateFamily:
    family: str            # "A" or "B"
    r_A: float             # unlevered / asset cost of capital
    r_E: float             # levered cost of equity
    r_D: float             # cost of debt
    r_TS: float            # tax-shield discount rate (= r_A for A, r_D for B)
    wacc: float            # slide-138 back-out (verified == standard WACC)
    beta_A: Optional[float]
    beta_E: Optional[float]


def unlever_beta(beta_levered: float, *, D: float, E: float, tau: float,
                 family: str, beta_D: float = 0.0) -> float:
    """Observed levered equity beta → asset beta, per the active family."""
    if E <= 0:
        return beta_levered
    if family == "A":
        V = D + E
        return beta_D * (D / V) + beta_levered * (E / V)
    Dt = (1.0 - tau) * D
    return beta_D * (Dt / (Dt + E)) + beta_levered * (E / (Dt + E))


def _family_a(*, r_D: float, D: float, E: float, tau: float,
              beta_A: Optional[float] = None, beta_D: float = 0.0,
              rf: Optional[float] = None, mrp: Optional[float] = None,
              r_A: Optional[float] = None) -> RateFamily:
    V = D + E
    if r_A is None:
        if beta_A is None or rf is None or mrp is None:
            raise ValueError("Family A needs r_A or (beta_A, rf, mrp)")
        r_A = rf + beta_A * mrp
    r_E = r_A + (D / E) * (r_A - r_D) if E > 0 else r_A
    wacc = r_A * (1 - tau * (r_D / r_A) * (D / V)) if (r_A > 0 and V > 0) else r_A
    beta_E = None
    if beta_A is not None and E > 0:
        beta_E = (beta_A - beta_D * (D / V)) / (E / V)
    return RateFamily("A", r_A, r_E, r_D, r_A, wacc, beta_A, beta_E)


def _family_b(*, r_D: float, D: float, E: float, tau: float,
              beta_A: Optional[float] = None, beta_D: float = 0.0,
              rf: Optional[float] = None, mrp: Optional[float] = None,
              r_A: Optional[float] = None) -> RateFamily:
    V = D + E
    Dt = (1.0 - tau) * D
    if r_A is None:
        if beta_A is None or rf is None or mrp is None:
            raise ValueError("Family B needs r_A or (beta_A, rf, mrp)")
        r_A = rf + beta_A * mrp
    r_E = r_A + (Dt / E) * (r_A - r_D) if E > 0 else r_A
    wacc = r_A * (1 - tau * (D / V)) if V > 0 else r_A
    beta_E = None
    if beta_A is not None and E > 0:
        beta_E = (beta_A * (Dt + E) - beta_D * Dt) / E
    return RateFamily("B", r_A, r_E, r_D, r_D, wacc, beta_A, beta_E)


def resolve_family(flag, **inputs) -> RateFamily:
    """Atomic family resolution (CF-R8). ``flag`` is a rate-family string
    ('A'/'B') or any object exposing ``.rate_family``. Returns ALL rate forms
    of the selected family together — never a per-form lookup."""
    fam = flag if isinstance(flag, str) else getattr(flag, "rate_family", "A")
    fam = (fam or "A").upper()[:1]
    return _family_b(**inputs) if fam == "B" else _family_a(**inputs)


def pv_tax_shield(rf: RateFamily, *, D: float, tau: float, g: float = 0.0) -> float:
    """PV of the interest tax shield on dollar debt ``D`` (growth-aware).
    Family A (shield grows with the firm): τ·r_D·D/(r_A − g).
    Family B (fixed-level shield): τ·D, independent of g.
    The Family-A form is what makes compressed-APV (CCF@r_A) == VU + PV(TS)."""
    if rf.family == "A":
        denom = rf.r_A - g
        return (tau * rf.r_D * D / denom) if denom > 0 else float("inf")
    return tau * D


def rate_family_for_ticker(ticker: str, flag, *, D: float, E: float, tau: float,
                           rf: float, mrp: float, r_D: float,
                           beta_D: float = 0.0) -> RateFamily:
    """Production entry (CF-R7): pull the levered equity beta from the SHARED
    ``_compute_beta`` path, unlever per the active family, then assemble the
    full family. No second beta engine."""
    from aletheia.tools.dcf_engine import _compute_beta
    fam = flag if isinstance(flag, str) else getattr(flag, "rate_family", "A")
    fam = (fam or "A").upper()[:1]
    beta_levered = _compute_beta(ticker)
    beta_A = unlever_beta(beta_levered, D=D, E=E, tau=tau, family=fam, beta_D=beta_D)
    return resolve_family(fam, r_D=r_D, D=D, E=E, tau=tau, beta_A=beta_A,
                          beta_D=beta_D, rf=rf, mrp=mrp)
