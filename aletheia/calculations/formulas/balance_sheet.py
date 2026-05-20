"""Balance-sheet derived quantities — NetDebt + helpers.

Phase 2 of the centralization refactor. NetDebt previously diverged
between paths:

  - cleaning_engine: gross debt included finance leases + current
    portion of LT debt; liquid assets included short-term AND long-term
    investments. The richest, enterprise-value-aligned definition.
  - fmp_stage3_adapter: plain ``TotalDebt − Cash − ShortTermInvestments``.
    Missed finance leases (AAPL ~$13B), current LT portion (~$13B for
    AAPL), and long-term marketable securities (AAPL ~$77B).

The central formula is the canonical EV-aligned definition. The
provider-specific bit (finance-lease fallback ladder — XBRL has a
maturity-schedule fallback that FMP can't replicate) stays at the
caller, which passes a pre-computed ``finance_lease_total`` to the
``gross_debt`` helper.
"""

from __future__ import annotations

from typing import Optional


def gross_debt(
    *,
    long_term_debt: Optional[float],
    short_term_debt: Optional[float] = None,
    current_portion_lt_debt: Optional[float] = None,
    finance_lease_total: Optional[float] = None,
) -> Optional[float]:
    """LongTermDebt + ShortTermDebt + CurrentPortionLongTermDebt + FinanceLeaseTotal

    All components except ``long_term_debt`` default to zero when
    missing — most filers don't carry every component. Returns
    ``None`` only when LongTermDebt itself is missing AND every other
    component is also missing (i.e. no debt data at all).
    """
    components = [
        long_term_debt,
        short_term_debt,
        current_portion_lt_debt,
        finance_lease_total,
    ]
    if all(c is None for c in components):
        return None
    return sum(c or 0.0 for c in components)


def liquid_assets(
    *,
    cash: Optional[float],
    short_term_investments: Optional[float] = None,
    long_term_investments: Optional[float] = None,
) -> Optional[float]:
    """Cash + ShortTermInvestments + LongTermInvestments

    Long-term marketable securities (AAPL ~$77B) are included as
    debt-equivalent offsets per EV convention — they're liquid in the
    debt-repayment sense even when classified non-current on the
    balance sheet.

    Returns ``None`` only when every input is missing.
    """
    if cash is None and short_term_investments is None and long_term_investments is None:
        return None
    return (cash or 0.0) + (short_term_investments or 0.0) + (long_term_investments or 0.0)


def net_debt(
    *,
    gross_debt: Optional[float],
    liquid_assets: Optional[float],
) -> Optional[float]:
    """GrossDebt − LiquidAssets

    Negative values are correct and meaningful — they signal a net
    cash position (the filer's liquid assets exceed its debt; common
    for cash-rich tech). The formula doesn't clamp.
    """
    if gross_debt is None and liquid_assets is None:
        return None
    return (gross_debt or 0.0) - (liquid_assets or 0.0)
