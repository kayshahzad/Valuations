"""Phase 1 unit + parity tests for the centralized formula module.

Pins behavior of ``invested_capital``, ``nopat``, ``roic`` and asserts
that the FMP-provider path and the XBRL/cleaning-engine path produce
identical values when fed identical inputs — the regression gap that
allowed the pre-canonicalization ROIC divergence (GOOGL FMP=21.44% vs
XBRL=12.00%) to ship undetected.
"""
from __future__ import annotations

import math

import pytest

from aletheia.calculations.formulas import (
    invested_capital,
    nopat,
    roic,
)
from aletheia.calculations.formulas.derived_inputs import (
    EXCESS_CASH_REVENUE_RATIO,
    INVESTED_CAPITAL_FLOOR_RATIO,
)


# ── NOPAT ───────────────────────────────────────────────────────────


def test_nopat_basic():
    assert nopat(operating_income=100.0, tax_rate=0.21) == pytest.approx(79.0)


def test_nopat_zero_tax():
    assert nopat(operating_income=100.0, tax_rate=0.0) == pytest.approx(100.0)


def test_nopat_negative_income_propagates():
    # NOPAT can be negative (loss-making years); the formula doesn't
    # filter sign — that's the caller's responsibility.
    assert nopat(operating_income=-50.0, tax_rate=0.21) == pytest.approx(-39.5)


def test_nopat_returns_none_when_oi_missing():
    assert nopat(operating_income=None, tax_rate=0.21) is None


def test_nopat_returns_none_when_rate_missing():
    assert nopat(operating_income=100.0, tax_rate=None) is None


# ── InvestedCapital ────────────────────────────────────────────────


def test_ic_normal_balance_sheet():
    # Cash 5% of revenue → no excess cash (2% threshold)
    # IC = Equity + Debt − ExcessCash = 415 + 47 − 0 (excess) where
    # excess = max(0, 5 − 2) = 3 of revenue. Cash 5% of revenue means
    # cash = 0.05 * rev → excess = cash − 0.02*rev = 0.03*rev
    rev = 100.0
    ic = invested_capital(
        total_equity=200.0, total_debt=50.0,
        cash=0.05 * rev, revenue=rev,
    )
    expected_excess = (0.05 - EXCESS_CASH_REVENUE_RATIO) * rev
    expected = 200.0 + 50.0 - expected_excess
    assert ic == pytest.approx(expected)


def test_ic_floor_kicks_in_for_pathological_zero_ic():
    # Imagine a retailer with negative working capital + cash sitting
    # at the entire equity buffer → raw IC turns negative or near zero.
    # 5% revenue floor saves the ROIC from blowing up.
    rev = 1000.0
    ic = invested_capital(
        total_equity=10.0, total_debt=5.0,
        cash=200.0, revenue=rev,
    )
    floor = INVESTED_CAPITAL_FLOOR_RATIO * rev
    # Raw = 10 + 5 − (200 − 20) = -165, but floor = 50 → returns 50.
    assert ic == pytest.approx(floor)


def test_ic_no_excess_cash_when_below_threshold():
    # Cash exactly at the working-cash threshold → ExcessCash = 0
    rev = 100.0
    ic = invested_capital(
        total_equity=200.0, total_debt=50.0,
        cash=EXCESS_CASH_REVENUE_RATIO * rev, revenue=rev,
    )
    # Excess cash = 0 → IC = Equity + Debt, then floored at 5% of rev = 5
    assert ic == pytest.approx(250.0)


def test_ic_handles_missing_revenue():
    # No revenue context → no excess-cash netting, no floor
    ic = invested_capital(
        total_equity=200.0, total_debt=50.0,
        cash=30.0, revenue=None,
    )
    # Falls back to Equity + Debt - Cash (no floor, no excess)
    # Actually returns Equity + Debt - ExcessCash where excess = cash
    # since rev=0. excess = max(0, 30 - 0) = 30.
    assert ic == pytest.approx(220.0)


def test_ic_returns_none_when_required_inputs_missing():
    assert invested_capital(
        total_equity=None, total_debt=50.0, cash=10.0, revenue=100.0,
    ) is None
    assert invested_capital(
        total_equity=200.0, total_debt=None, cash=10.0, revenue=100.0,
    ) is None


# ── ROIC ────────────────────────────────────────────────────────────


def test_roic_basic():
    assert roic(nopat=20.0, invested_capital=100.0) == pytest.approx(0.20)


def test_roic_returns_none_when_ic_zero():
    assert roic(nopat=20.0, invested_capital=0.0) is None


def test_roic_returns_none_when_ic_negative():
    # Negative IC indicates a pathological balance sheet — the ratio
    # would be a misleading positive number. Caller should see None.
    assert roic(nopat=20.0, invested_capital=-50.0) is None


def test_roic_returns_none_when_inputs_missing():
    assert roic(nopat=None, invested_capital=100.0) is None
    assert roic(nopat=20.0, invested_capital=None) is None


# ── Cross-provider parity (regression net) ─────────────────────────
#
# These tests assert that the FMP-adapter path and the
# cleaning_engine path produce identical InvestedCapital and ROIC
# values for the same inputs. This is the test that would have
# caught the pre-canonicalization 21.44% vs 12.00% divergence.


GOOGL_FIXTURE = {
    "OperatingIncome": 134e9,
    "PretaxIncome":    140e9,
    "TaxExpense":       22e9,
    "NetIncome":       113e9,
    "Revenue":         403e9,
    "Cash":             31e9,
    "ShortTermInvestments": 70e9,
    "TotalEquity":     415e9,
    "LongTermDebt":     47e9,
    "TotalDebt":        47e9,    # cleaning_engine reads ShortTermDebt + LongTermDebt
    "ShortTermDebt":     0.0,
    "OperatingCF":     126e9,
    "CapEx":            53e9,
}


def test_parity_fmp_and_cleaning_engine_invested_capital_identical():
    """The same inputs → the same InvestedCapital regardless of which
    code path computes it. Phase 1 closes the GOOGL FMP=21.44% vs
    XBRL=12% drift."""
    # FMP adapter path
    from aletheia.validation.fmp_stage3_adapter import _compute_derived
    fmp_derived = _compute_derived(GOOGL_FIXTURE)

    # Direct central-formula path (the cleaning_engine code now calls
    # this same function with the same arg shape)
    direct_ic = invested_capital(
        total_equity=GOOGL_FIXTURE["TotalEquity"],
        total_debt=GOOGL_FIXTURE["LongTermDebt"]
                   + GOOGL_FIXTURE["ShortTermDebt"],
        cash=GOOGL_FIXTURE["Cash"],
        revenue=GOOGL_FIXTURE["Revenue"],
    )
    assert fmp_derived["InvestedCapital"] == pytest.approx(direct_ic)


def test_parity_fmp_and_cleaning_engine_roic_identical():
    """ROIC parity follows from IC + NOPAT parity. Pinned at the
    fixture's expected value so a methodology drift would also fail."""
    from aletheia.validation.fmp_stage3_adapter import _compute_derived
    fmp_derived = _compute_derived(GOOGL_FIXTURE)

    direct_nopat = nopat(
        operating_income=GOOGL_FIXTURE["OperatingIncome"],
        tax_rate=GOOGL_FIXTURE["TaxExpense"] / GOOGL_FIXTURE["PretaxIncome"],
    )
    direct_ic = invested_capital(
        total_equity=GOOGL_FIXTURE["TotalEquity"],
        total_debt=GOOGL_FIXTURE["LongTermDebt"]
                   + GOOGL_FIXTURE["ShortTermDebt"],
        cash=GOOGL_FIXTURE["Cash"],
        revenue=GOOGL_FIXTURE["Revenue"],
    )
    direct_roic = roic(nopat=direct_nopat, invested_capital=direct_ic)

    assert fmp_derived["ROIC"] == pytest.approx(direct_roic)


def test_googl_roic_in_post_canonical_range():
    """Pin the GOOGL fixture's ROIC to the post-canonicalization
    expectation. If a future change accidentally reverts the
    convention (e.g. drops the excess-cash netting), this test fails
    with a clear methodology signal rather than a silent drift."""
    from aletheia.validation.fmp_stage3_adapter import _compute_derived
    fmp_derived = _compute_derived(GOOGL_FIXTURE)
    roic_val = fmp_derived["ROIC"]
    # Post-canonical GOOGL ROIC on this fixture is ~25.7%. Pinned with
    # ±1% band — convention change would push it outside this band.
    assert 0.245 < roic_val < 0.270, (
        f"GOOGL fixture ROIC = {roic_val:.4f} outside post-canonical "
        f"band [24.5%, 27.0%]. Check Invested Capital formula in "
        f"aletheia/calculations/formulas/derived_inputs.py."
    )
