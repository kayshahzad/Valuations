"""Cash-flow kernel (Valuation-Methods plan, Phase 0a).

Emits all four streams — FCF (unlevered), CCF (capital cash flow), ECF (equity
cash flow), CADS (cash available for debt service) — from ONE constructor off
identical accounting inputs (§1 identities), so the four valuation methods can
never drift apart at the cash-flow layer. The only proof the engines are right
is that they converge; that proof is only honest if every method reads the same
kernel.

Identities (plan §1):
    FCF   = (1−t)·EBIT + Dep − CAPX − ΔNWC
    CCF   = FCF + t·Int                                  (EBIT version)
          = NI + Int + Dep − CAPX − ΔNWC                 (Net-Income version, CF-R2)
    ECF   = NI + Dep − CAPX − ΔNWC + (DebtIssues − DebtPayments)
          = CCF − DebtCashFlow
    CADS  = CFO − CapEx

CF-R2: real tickers use the Net-Income CCF (avoids estimating t under messy/holdco
tax structures); the golden T1 fixture uses the EBIT version. When NI is supplied
consistently — NI = (1−t)(EBIT − Int) — the two CCF versions reconcile exactly,
which is the kernel's no-drift self-test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class CashFlows:
    fcf: float
    ccf_ebit: float
    ccf_ni: Optional[float]
    ccf: float                 # active CCF (NI basis for real tickers, EBIT for golden)
    ecf: float
    debt_cash_flow: float      # cash to debt holders = Int + (payments − issues)
    cads: Optional[float]


def cashflows(*, ebit: float, dep: float, capx: float, d_nwc: float,
              interest: float, tax: float, ni: Optional[float] = None,
              debt_issues: float = 0.0, debt_payments: float = 0.0,
              cfo: Optional[float] = None, ccf_basis: str = "ni") -> CashFlows:
    """One-period four-stream constructor (the §1 identities).

    ``ccf_basis`` selects the active CCF: 'ni' (CF-R2 default, real tickers) or
    'ebit' (golden fixture). ``ni`` falls back to (1−t)(EBIT − Int) when not
    supplied so ECF is always defined.
    """
    fcf = (1.0 - tax) * ebit + dep - capx - d_nwc
    ccf_ebit = fcf + tax * interest
    ccf_ni = (ni + interest + dep - capx - d_nwc) if ni is not None else None

    if ccf_basis == "ni" and ccf_ni is not None:
        ccf = ccf_ni
    else:
        ccf = ccf_ebit

    ni_eff = ni if ni is not None else (1.0 - tax) * (ebit - interest)
    ecf = ni_eff + dep - capx - d_nwc + (debt_issues - debt_payments)
    debt_cash_flow = ccf - ecf
    cads = (cfo - capx) if cfo is not None else None

    return CashFlows(fcf, ccf_ebit, ccf_ni, ccf, ecf, debt_cash_flow, cads)


def cashflow_series(rows: List[dict], *, ccf_basis: str = "ni") -> List[CashFlows]:
    """Map a list of per-year input dicts through ``cashflows``. Each row carries
    the kwargs of ``cashflows`` (ebit, dep, capx, d_nwc, interest, tax, …). Used
    to turn a forward projection into the four streams in lockstep."""
    return [cashflows(ccf_basis=ccf_basis, **r) for r in rows]
