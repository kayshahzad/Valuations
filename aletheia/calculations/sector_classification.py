"""Financial-filer predicates — keyed on the deposit-taking / bank-charter
reality, NOT a single GICS label.

The lesson SOFI taught: a fintech holding company that owns a bank (SoFi Bank,
N.A.) gets tagged "Financial Services" / "Technology" / "Consumer Finance" —
anything but a clean GICS "Banks". A guard that keys on ``sector == "Financials"``
catches JPM but lets SOFI slip through, and then the FCFF/working-capital math
mis-reads multi-billion deposit & loan flows as operating working capital
(SOFI: $2.9B of deposit growth in ONE quarter, larger than annual revenue → an
impossible −$7B ΔNWC/FCF). So the predicate matches on sector OR a specialized
financial model OR a banking/lending industry keyword.
"""

from __future__ import annotations

# Specialized (non-FCFF) financial valuation models.
_FINANCIAL_MODELS = (
    "ddm_required", "embedded_value_required", "residual_income_required",
)

# Industry substrings that signal a deposit-taker / lender / financial even when
# the sector label is "Technology" or "Consumer Finance" rather than "Banks".
_BANK_INDUSTRY_KW = (
    "bank", "credit", "capital market", "brokerage", "mortgage", "thrift",
    "savings", "consumer financ", "diversified financ", "lending",
)


def is_financial_filer(sector: str = "", industry: str = "",
                       business_model: str = "") -> bool:
    """True for a bank / insurer / lender whose cash flows DON'T map to a
    free-cash-flow framework and whose EBITDA-leverage is meaningless (deposits
    aren't debt). Broad by design — used by the financials *refusal* guards, so
    a false positive only declines an inappropriate FCFF treatment."""
    s = (sector or "").lower()
    i = (industry or "").lower()
    if "financ" in s or "bank" in s or "insurance" in s:
        return True
    if (business_model or "") in _FINANCIAL_MODELS:
        return True
    if any(k in i for k in _BANK_INDUSTRY_KW):
        return True
    return False


def is_bank_for_display(sector: str = "", business_model: str = "") -> bool:
    """Narrower: a financial-SECTOR name on a specialized (non-FCFF) model — gets
    the bank P&L table instead of the industrial → FCF waterfall. Deliberately
    EXCLUDES payment networks / ratings (V, MCO — Financials but fcff_compatible,
    normal income statements) and managed care (CNC — residual_income but a
    Healthcare sector with a COGS-style income statement)."""
    s = (sector or "").lower()
    return (("financ" in s or "bank" in s)
            and (business_model or "") in _FINANCIAL_MODELS)


__all__ = ["is_financial_filer", "is_bank_for_display"]
