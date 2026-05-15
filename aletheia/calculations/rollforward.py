"""Layer 3 — Pure roll-forward primitives that link periods.

Per the 3-layer accounting encoding model:

  L1: Structural identities — laws of accounting (A = L + E, cash roll)
  L2: Derivational relationships — methodology-bearing formulas
  L3: Period-over-period flows — roll-forward functions (THIS MODULE)

Each function here is a pure mathematical mapping `(beginning_balance,
period_activity, *adjustments) → implied_ending_balance`. Two usage
patterns share the same primitives:

  AUDIT mode — given prior + current period data, compute the
    implied_end from beg + flows, compare against reported_end.
    Discrepancy outside tolerance signals data quality / cleaning gap
    / legitimate complexity. Used by ``identity_checks.py``.

  PROJECTION mode — given current period + forecast inputs, compute
    future_end. Used by ``DCFEngine`` for multi-year FCF / PP&E /
    cash projection. DCFEngine doesn't yet route through these
    primitives (its multi-year scenario logic is more complex); the
    refactor is deferred V2 work.

Six primitives correspond to the six audit roll-forwards:
  - ppe_rollforward
  - re_rollforward (equity-bridge model with buyback retirement)
  - cash_rollforward
  - debt_rollforward
  - wc_rollforward (working capital line-item: AR / Inventory / AP)
  - fcf_pathway_b (NOPAT-based FCF reconciliation pathway)

Design rules:
  - Pure functions: no I/O, no side effects, no logging.
  - Named keyword arguments for clarity; positional only for the
    minimum required inputs.
  - Return the implied ending value (or implied FCF_B for fcf_pathway_b).
  - Optional terms default to 0 so callers can use the simplified
    formula when extended-term inputs aren't available.

When the audit's tolerance-based pass/fail logic is needed, the
caller (identity_checks.py) wraps these primitives with the
discrepancy calculation + exception-flag classification. The
primitives themselves don't know about tolerances or categories.
"""

from __future__ import annotations


def ppe_rollforward(
    beg: float,
    capex: float,
    da: float,
    *,
    acquisitions: float = 0.0,
    impairments: float = 0.0,
    cip_additions: float = 0.0,
) -> float:
    """PP&E roll-forward.

    Basic:    PPE_end ≈ PPE_beg + CapEx − D&A
    Extended: + acquisitions − impairments + CIP_additions

    CapEx is positive-magnitude per the A6 sign convention; D&A is
    positive-magnitude (subtracted). Acquisitions add to PP&E without
    flowing through CapEx; impairments reduce PP&E without flowing
    through D&A.
    """
    return beg + capex - da + acquisitions - impairments + cip_additions


def re_rollforward(
    beg: float,
    ni: float,
    *,
    div: float = 0.0,
    buybacks: float = 0.0,
    tax_withhold: float = 0.0,
    sbc: float = 0.0,
    delta_apic: float = 0.0,
    excise_tax: float = 0.0,
    delta_aoci: float = 0.0,
) -> float:
    """Retained Earnings roll-forward — equity-bridge model.

    Basic:    RE_end ≈ RE_beg + NI − Div
    Extended: − abs(Buybacks) − abs(TaxWithhold) − ExciseTax
              + abs(SBC) − ΔAPIC + ΔAOCI

    Empirically derived from META FY2024 equity bridge (Phase 1.β).
    For share-retirement filers (META, AAPL, GOOGL, MSFT), buyback +
    tax-withholding charges draw down APIC first; the residual hits
    RE. SBC credits APIC. The observable ΔAPIC captures the net effect.
    """
    return (
        beg + ni - div
        - abs(buybacks) - abs(tax_withhold) - excise_tax
        + abs(sbc)
        - delta_apic + delta_aoci
    )


def cash_rollforward(
    beg: float,
    ocf: float,
    icf: float,
    fcf: float,
    *,
    fx_effect: float = 0.0,
) -> float:
    """Cash roll-forward — broad cash including restricted cash.

    Identity: Cash_end = Cash_beg + OCF + ICF + FCF + FX_effect

    Post-ASU-2016-18 (FY2018+) the CF statement reconciles to broad
    cash (``CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents``).
    Pre-ASU filers fall back to narrow cash + narrow FX-effect tag.
    """
    return beg + ocf + icf + fcf + fx_effect


def debt_rollforward(
    beg: float,
    *,
    issued: float = 0.0,
    repaid: float = 0.0,
    cp_net: float = 0.0,
    fx_translation: float = 0.0,
) -> float:
    """Total debt roll-forward — broad definition.

    Identity: Debt_end ≈ Debt_beg + Issued − Repaid + CP_net + FX_translation

    Total debt = LTD_Noncurrent + LTD_Current + CommercialPaper +
    FinanceLeaseLiabilityCurrent + FinanceLeaseLiabilityNoncurrent.
    CommercialPaper net flows captured via
    ``ProceedsFromRepaymentsOfCommercialPaper``. FX translation
    relevant for filers with multi-currency debt.
    """
    return beg + issued - repaid + cp_net + fx_translation


def wc_rollforward(
    bs_change: float,
    cf_reported_change: float,
) -> float:
    """Working-capital line-item reconciliation discrepancy.

    For XBRL filers, both ``IncreaseDecreaseIn*`` (CF) and BS Δ report
    the same positive-magnitude convention (positive = increase in
    asset/liability). Identity: ``bs_change == cf_reported_change``.
    Discrepancy = cf_reported_change − bs_change (zero when identity
    holds).

    Caller compares the return value to 0 within tolerance; non-zero
    indicates either an acquisition-driven WC distortion (acquired WC
    on BS without CF entry) or a per-line-item vs aggregate CF
    presentation divergence (META's CF aggregates AP into Trade +
    Other + Accrued; BS reports per-line).

    Strictly speaking this is a reconciliation, not a roll-forward.
    Kept in this module for grouping with the other audit primitives;
    the WC change IS what links one BS to the next, so it slots in.
    """
    return cf_reported_change - bs_change


def fcf_pathway_b(
    nopat: float,
    da: float,
    *,
    capex: float = 0.0,
    delta_nwc: float = 0.0,
    sbc: float = 0.0,
    deferred_tax: float = 0.0,
    other_non_cash: float = 0.0,
) -> float:
    """FCF Pathway B — NOPAT-based reconciliation target for Pathway A.

    Basic:    FCF_B = NOPAT + DA − abs(CapEx) − ΔNWC
    Extended: + SBC + Deferred_tax + Other_non_cash

    Phase 1 extended Pathway B with SBC (non-cash expense already
    subtracted from operating income to get to EBIT — must be added
    back when bridging from NOPAT to FCF). Deferred tax + other
    non-cash items are V3b territory; defaulted to 0 today.
    """
    return (
        nopat + da + sbc + deferred_tax + other_non_cash
        - abs(capex) - delta_nwc
    )


__all__ = [
    "ppe_rollforward",
    "re_rollforward",
    "cash_rollforward",
    "debt_rollforward",
    "wc_rollforward",
    "fcf_pathway_b",
]
