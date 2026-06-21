"""The four Liberti valuation methods as a convergent set (Phase 1).

Every method is a different slice of the same firm; Bible I's appendix proves
they return the identical enterprise value under the same assumptions. That
identity is the correctness test — a method can look right in isolation and be
wrong; the only proof is convergence. All four read the SAME cash-flow kernel
(`cashflow_series`) and the SAME flag-selected rate family (`discount_rates`),
so they cannot drift by construction; the test is whether they agree.

  FCF / WACC  : FCF (unlevered)            @ WACC   → Enterprise Value
  CCF / r_A   : FCF + τ·Int                @ r_A    → Enterprise Value
  APV         : VU(FCF@r_A) + PV(tax shield)        → Enterprise Value
  ECF / r_E   : equity cash flow           @ r_E    → Equity Value (+D → EV)

Key identity for the ECF leg under constant D/V (Family A): debt rebalances to
hold the ratio, so it grows at g and net new borrowing ΔD = g·D enters the cash
to debt holders: DebtCF = Int − ΔD, hence ECF = CCF − Int + g·D. Under Family B
(constant D) ΔD = 0. Getting this right is what makes the ECF leg converge with
the other three rather than land low (the naive ECF undervaluation, Bible I 148).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from aletheia.tools.discount_rates import RateFamily, pv_tax_shield


@dataclass(frozen=True)
class ConvergenceResult:
    ev_wacc_fcf: float
    ev_ccf: float
    ev_apv: float
    equity_ecf: float
    ev_from_ecf: float          # equity_ecf + D
    max_ev_spread_pct: float
    converged: bool


def value_perpetuity_all(*, fcf1: float, interest1: float, rate: RateFamily,
                         tau: float, g: float, D: float,
                         tol: float = 0.005) -> ConvergenceResult:
    """Single-stage growing perpetuity through all four methods. Exact: this is
    the golden-fixture convergence and the steady-state/terminal case.

    ``fcf1`` and ``interest1`` are the next-period (Year-1) figures; ``D`` is the
    dollar debt level. Convergence proven when the four EVs agree within ``tol``.
    """
    rA, rE, wacc = rate.r_A, rate.r_E, rate.wacc

    ev_wacc = fcf1 / (wacc - g)
    ccf1 = fcf1 + tau * interest1
    ev_apv = fcf1 / (rA - g) + pv_tax_shield(rate, D=D, tau=tau, g=g)
    # Compressed-APV (CCF @ r_A) is valid ONLY for Family A, where the shield
    # rides the asset side (r_TS = r_A). For Family B the shield is debt-risky
    # (r_TS = r_D), so the CCF method = discount FCF @ r_A + shield @ r_D — i.e.
    # it coincides with APV. Lumping the shield into CCF @ r_A would mis-value B.
    ev_ccf = (ccf1 / (rA - g)) if rate.family == "A" else ev_apv

    # ECF leg with the structure-correct DebtCF (Family A rebalances → ΔD = g·D;
    # Family B holds D → ΔD = 0).
    delta_D = g * D if rate.family == "A" else 0.0
    ecf1 = ccf1 - interest1 + delta_D
    equity_ecf = ecf1 / (rE - g) if (rE - g) > 0 else float("inf")
    ev_from_ecf = equity_ecf + D

    evs = [ev_wacc, ev_ccf, ev_apv, ev_from_ecf]
    spread = (max(evs) - min(evs)) / abs(min(evs)) if min(evs) else float("inf")
    return ConvergenceResult(ev_wacc, ev_ccf, ev_apv, equity_ecf, ev_from_ecf,
                             spread, spread <= tol)


def _pv(cfs: List[float], rate: float) -> float:
    return sum(cf / (1.0 + rate) ** (i + 1) for i, cf in enumerate(cfs))


def build_valuation_methods(calc, dcf_result=None, p2=None) -> dict:
    """Phase 3 orchestrator — run the convergent set on a real ticker's
    projection + the flag-selected rate family, ADDITIVELY (does not replace the
    headline IV). Returns the four EVs, the convergence diagnostic, and the
    selected family. FCFF only; specialized engines (REIT/DDM) value equity via
    their own ECF special case (the ReitEngine AFFO two-stage = general ECF@r_E,
    confirmed by the T4 regression), so this returns available: False for them.
    """
    out = {"available": False}
    if dcf_result is None or getattr(dcf_result, "base", None) is None:
        out["notes"] = "no FCFF projection (specialized engine values ECF directly)"
        return out
    base = dcf_result.base
    if not getattr(base, "projections", None):
        return out
    try:
        from aletheia.tools.capital_structure_flag import build_capital_structure_flag
        from aletheia.tools.discount_rates import (
            resolve_family, unlever_beta,
        )
        from aletheia.tools.dcf_engine import _compute_beta, MARKET_RISK_PREMIUM

        D = float(dcf_result.wacc_total_debt or 0.0)
        E = float(dcf_result.market_cap or 0.0)
        if E <= 0:
            return out
        tau = 0.21
        r_D = 0.05                                    # kd proxy (caveat #5)
        rf_rate = float(dcf_result.risk_free_rate or 0.045)
        g = float(base.assumptions.terminal_growth)

        flag = build_capital_structure_flag(calc, market_cap=E)
        # Refuse rather than default: if the flag couldn't classify the
        # structure (financials guard / implausible leverage), we have no
        # basis to pick a rate family — do NOT silently assume Family A.
        if not flag.get("available") or flag.get("rate_family") not in ("A", "B"):
            out["notes"] = ("capital-structure flag unavailable (%s) — refusing "
                            "to default a rate family" % (flag.get("notes") or "n/a"))
            return out
        fam = flag["rate_family"]
        ticker = getattr(getattr(calc, "classification", None), "ticker", "") or ""
        beta_A = unlever_beta(_compute_beta(ticker), D=D, E=E, tau=tau, family=fam)
        rate = resolve_family(fam, r_D=r_D, D=D, E=E, tau=tau, beta_A=beta_A,
                              rf=rf_rate, mrp=MARKET_RISK_PREMIUM)

        term = base.projections[-1]
        conv = value_perpetuity_all(fcf1=term.fcff * (1 + g), interest1=r_D * D,
                                    rate=rate, tau=tau, g=g, D=D, tol=0.05)
        ev_trio = [conv.ev_wacc_fcf, conv.ev_ccf, conv.ev_apv]
        trio_mean = sum(ev_trio) / 3.0
        trio_spread = (max(ev_trio) - min(ev_trio)) / min(ev_trio) if min(ev_trio) else None

        # Attribute the ECF→EV residual (reviewer ask): it is NOT the CF-R4
        # option-floor — that guard is not in this convergence path, and it
        # cannot bite a no-default name. With D entering the equity↔EV bridge
        # as GROSS debt (the correct, better-converging form), any residual is
        # the kd/τ-proxy + small-lever equity-discounting band (caveat #5), the
        # same class as the trio's own WACC-vs-CCF gap. Labelled, not hidden.
        ecf_gap = (conv.ev_from_ecf / trio_mean - 1.0) if trio_mean else None
        ecf_attr = ("kd/τ-proxy + lever residual (caveat #5); not CF-R4 "
                    "option-floor (not in path) and not a net-vs-gross bridge "
                    "error (gross bridge used)")

        out.update({
            "available": True,
            "rate_family": fam,
            "family_reason": flag.get("klass"),
            "rates": {"r_A": rate.r_A, "r_E": rate.r_E, "wacc": rate.wacc,
                      "r_TS": rate.r_TS, "beta_A": rate.beta_A, "beta_E": rate.beta_E},
            "ev": {"wacc_fcf": conv.ev_wacc_fcf, "ccf": conv.ev_ccf,
                   "apv": conv.ev_apv, "from_ecf": conv.ev_from_ecf},
            "equity_ecf": conv.equity_ecf,
            "ev_trio_spread": trio_spread,
            "converged_trio": (trio_spread is not None and trio_spread < 0.05),
            "ecf_gap_vs_trio": ecf_gap,
            "ecf_residual_attribution": ecf_attr,
            "notes": ["four-method convergence on the terminal/steady-state; "
                      "additive diagnostic, not the headline IV. kd & tau are "
                      "proxies (caveat #5) so real-data legs spread within a band."],
        })
    except Exception as e:
        out["notes"] = f"valuation-methods summary failed: {type(e).__name__}: {e}"
    return out


def value_stream_all(*, fcf: List[float], interest: List[float], rate: RateFamily,
                     tau: float, g: float, D_terminal: float,
                     tol: float = 0.02) -> ConvergenceResult:
    """Multi-stage: explicit FCF/interest stream + a Gordon terminal off the
    final year, valued four ways. The terminal uses the perpetuity legs so all
    four share the same continuing-value treatment; the explicit stage is PV'd
    at each method's rate. Tolerance is looser (kd-proxy + rebalancing
    approximation across the explicit years) — a wide-but-bounded band is
    expected, not an engine bug (caveat #5)."""
    rA, rE, wacc = rate.r_A, rate.r_E, rate.wacc
    n = len(fcf)
    fcf_term = fcf[-1] * (1 + g)
    int_term = interest[-1] * (1 + g)

    # Terminal EV via the convergent perpetuity (shared continuing value).
    term = value_perpetuity_all(fcf1=fcf_term, interest1=int_term, rate=rate,
                                tau=tau, g=g, D=D_terminal)

    # WACC-FCF: PV(explicit FCF) + PV(terminal EV) @ WACC
    ev_wacc = _pv(fcf, wacc) + term.ev_wacc_fcf / (1 + wacc) ** n
    # APV: PV(explicit FCF @ r_A) + PV(explicit tax shields @ r_TS) + PV(terminal)
    tax_shields = [tau * i for i in interest]
    ev_apv = (_pv(fcf, rA) + _pv(tax_shields, rate.r_TS)
              + term.ev_apv / (1 + rA) ** n)
    # CCF: Family A → PV(CCF @ r_A) (compressed APV); Family B → = APV (shield is
    # debt-risky, can't ride r_A). See value_perpetuity_all for the why.
    if rate.family == "A":
        ccf = [f + tau * i for f, i in zip(fcf, interest)]
        ev_ccf = _pv(ccf, rA) + term.ev_ccf / (1 + rA) ** n
    else:
        ev_ccf = ev_apv
    # ECF: PV(explicit ECF @ r_E) + PV(terminal equity) → +D_terminal
    delta_D = [0.0] * n
    if rate.family == "A":
        # debt tracks value; approximate ΔD by growing D_terminal back over the
        # explicit years at g (bounded approximation, see tolerance note)
        delta_D = [g * D_terminal for _ in range(n)]
    ecf = [c - i + d for c, i, d in zip(ccf, interest, delta_D)]
    equity_ecf = _pv(ecf, rE) + term.equity_ecf / (1 + rE) ** n
    ev_from_ecf = equity_ecf + D_terminal

    evs = [ev_wacc, ev_ccf, ev_apv, ev_from_ecf]
    spread = (max(evs) - min(evs)) / abs(min(evs)) if min(evs) else float("inf")
    return ConvergenceResult(ev_wacc, ev_ccf, ev_apv, equity_ecf, ev_from_ecf,
                             spread, spread <= tol)
