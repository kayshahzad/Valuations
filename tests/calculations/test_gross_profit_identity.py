"""Phase 2.3a — gross-profit tie: Revenue − COGS − GrossProfit ≈ 0 (I4).

A basic income-statement identity that had no check anywhere. Net-revenue
presenters (insurers/airlines/banks) that don't split cost of revenue route to a
passing exception, not a failure.
"""
from __future__ import annotations

from aletheia.calculations.identity_checks import check_gross_profit_reconciliation


def _rec(**raw):
    return {"ticker": "T", "fiscal_year": 2024, "period": "FY",
            "clean": {}, "raw": raw}


def test_ties_when_all_reported():
    res = check_gross_profit_reconciliation(
        _rec(Revenue=100e9, COGS=60e9, GrossProfit=40e9))
    assert res.passed and res.exception_category is None


def test_real_residual_flagged():
    # COGS and GP both material but don't tie → a real residual to investigate.
    res = check_gross_profit_reconciliation(
        _rec(Revenue=100e9, COGS=60e9, GrossProfit=30e9))
    assert not res.passed
    assert res.exception_category == "gross_profit_residual"


def test_net_revenue_presenter_is_exception_not_failure():
    # Insurer/airline: no COGS/GP split (COGS ~0) → passing exception category.
    res = check_gross_profit_reconciliation(
        _rec(Revenue=100e9, COGS=0.0, GrossProfit=0.0))
    assert not res.passed
    assert res.exception_category == "no_cogs_gp_split_reported"


def test_skipped_when_gross_profit_absent():
    res = check_gross_profit_reconciliation(_rec(Revenue=100e9, COGS=60e9))
    assert res.was_skipped


# ── 2.3b: operating-income waterfall (GrossProfit − OperatingExpenses = OI) ──
from aletheia.calculations.identity_checks import (  # noqa: E402
    check_operating_income_reconciliation,
)


def test_operating_income_ties():
    res = check_operating_income_reconciliation(
        _rec(Revenue=100e9, GrossProfit=40e9, OperatingExpenses=25e9,
             OperatingIncome=15e9))
    assert res.passed and res.exception_category is None


def test_operating_income_residual_flagged():
    res = check_operating_income_reconciliation(
        _rec(Revenue=100e9, GrossProfit=40e9, OperatingExpenses=25e9,
             OperatingIncome=10e9))            # 40−25−10 = 5 (>2% of GP)
    assert not res.passed
    assert res.exception_category == "operating_income_residual"


def test_operating_income_skipped_when_opex_absent():
    res = check_operating_income_reconciliation(
        _rec(GrossProfit=40e9, OperatingIncome=15e9))
    assert res.was_skipped
