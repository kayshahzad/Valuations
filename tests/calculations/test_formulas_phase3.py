"""Phase 3 unit tests — margins, ROE, EBITDA synthesis.

These formulas were IDENTICAL on both adapter paths pre-migration, so
the phase shouldn't move any numbers in the universe. Tests here pin
the formula semantics so future tweaks (e.g. policy on negative
revenue) don't silently regress.
"""
from __future__ import annotations

import pytest

from aletheia.calculations.formulas import (
    ebit_margin_pct,
    ebitda,
    ebitda_margin_pct,
    fcf_margin_pct,
    gross_margin_pct,
    roe,
)


# ── EBITDA synthesis ──────────────────────────────────────────────


def test_ebitda_basic():
    assert ebitda(operating_income=100.0, depreciation_total=20.0) == pytest.approx(120.0)


def test_ebitda_no_depreciation_defaults_to_zero():
    # Asset-light filer with no D&A line → EBITDA equals OperatingIncome
    assert ebitda(operating_income=100.0, depreciation_total=None) == pytest.approx(100.0)


def test_ebitda_returns_none_when_op_income_missing():
    assert ebitda(operating_income=None, depreciation_total=20.0) is None


# ── Margins ───────────────────────────────────────────────────────


@pytest.mark.parametrize("fn,kwarg_name", [
    (gross_margin_pct, "gross_profit"),
    (ebit_margin_pct, "ebit"),
    (ebitda_margin_pct, "ebitda"),
    (fcf_margin_pct, "fcf"),
])
def test_margins_basic(fn, kwarg_name):
    """All four margins use the same shape; one test parameterized."""
    result = fn(**{kwarg_name: 25.0}, revenue=100.0)
    assert result == pytest.approx(25.0)   # 25% margin


@pytest.mark.parametrize("fn,kwarg_name", [
    (gross_margin_pct, "gross_profit"),
    (ebit_margin_pct, "ebit"),
    (ebitda_margin_pct, "ebitda"),
    (fcf_margin_pct, "fcf"),
])
def test_margins_return_none_on_zero_revenue(fn, kwarg_name):
    """Zero or negative revenue → margin undefined."""
    assert fn(**{kwarg_name: 25.0}, revenue=0.0) is None
    assert fn(**{kwarg_name: 25.0}, revenue=-100.0) is None


@pytest.mark.parametrize("fn,kwarg_name", [
    (gross_margin_pct, "gross_profit"),
    (ebit_margin_pct, "ebit"),
    (ebitda_margin_pct, "ebitda"),
    (fcf_margin_pct, "fcf"),
])
def test_margins_return_none_when_numerator_missing(fn, kwarg_name):
    assert fn(**{kwarg_name: None}, revenue=100.0) is None


def test_margins_negative_numerator_yields_negative_margin():
    # Loss-making years — margin should be negative, not None
    assert ebit_margin_pct(ebit=-25.0, revenue=100.0) == pytest.approx(-25.0)


# ── ROE ────────────────────────────────────────────────────────────


def test_roe_basic():
    assert roe(net_income=15.0, total_equity=100.0) == pytest.approx(0.15)


def test_roe_returns_none_on_negative_equity():
    """Aggressive-buyback filers (LOW, HD, AZO) drive book equity
    below zero — the bare NI/Equity ratio becomes misleading."""
    assert roe(net_income=20.0, total_equity=-50.0) is None


def test_roe_returns_none_on_zero_equity():
    assert roe(net_income=20.0, total_equity=0.0) is None


def test_roe_returns_none_when_inputs_missing():
    assert roe(net_income=None, total_equity=100.0) is None
    assert roe(net_income=20.0, total_equity=None) is None


def test_roe_negative_income_propagates():
    # Loss years — ROE is negative, not suppressed
    assert roe(net_income=-15.0, total_equity=100.0) == pytest.approx(-0.15)


# ── Cross-provider parity (regression net) ──────────────────────────


GOOGL_FIXTURE = {
    "OperatingIncome": 134e9,
    "PretaxIncome":    140e9,
    "TaxExpense":       22e9,
    "NetIncome":       113e9,
    "Revenue":         403e9,
    "Cash":             31e9,
    "ShortTermInvestments": 70e9,
    "LongTermInvestments":  30e9,
    "TotalEquity":     415e9,
    "LongTermDebt":     47e9,
    "TotalDebt":        47e9,
    "ShortTermDebt":     0.0,
    "CurrentPortionLongTermDebt": 5e9,
    "FinanceLeaseLiability_Total": 13e9,
    "OperatingCF":     126e9,
    "CapEx":            53e9,
    "Depreciation_Total": 19e9,
    "ChangeInWorkingCapital": 3e9,
    "GrossProfit":     230e9,
    "EBITDA":          153e9,
}


def test_parity_googl_margins_via_central_formulas():
    """FMP path should produce the same margins as direct central
    formula calls. Identical inputs → identical outputs."""
    from aletheia.validation.fmp_stage3_adapter import _compute_derived
    fmp_derived = _compute_derived(GOOGL_FIXTURE)

    rev = GOOGL_FIXTURE["Revenue"]
    assert fmp_derived["GrossMargin_Pct"] == pytest.approx(
        gross_margin_pct(gross_profit=GOOGL_FIXTURE["GrossProfit"], revenue=rev)
    )
    assert fmp_derived["EBIT_Margin_Pct"] == pytest.approx(
        ebit_margin_pct(ebit=GOOGL_FIXTURE["OperatingIncome"], revenue=rev)
    )
    assert fmp_derived["EBITDA_Margin_Pct"] == pytest.approx(
        ebitda_margin_pct(ebitda=GOOGL_FIXTURE["EBITDA"], revenue=rev)
    )


def test_parity_googl_roe_matches_central_formula():
    from aletheia.validation.fmp_stage3_adapter import _compute_derived
    fmp_derived = _compute_derived(GOOGL_FIXTURE)
    expected = roe(
        net_income=GOOGL_FIXTURE["NetIncome"],
        total_equity=GOOGL_FIXTURE["TotalEquity"],
    )
    assert fmp_derived["ROE"] == pytest.approx(expected)


def test_parity_ebitda_synthesis_when_filer_omits():
    """When the filer doesn't disclose EBITDA, the FMP adapter
    synthesizes it via the central function (OpIncome + D&A)."""
    fixture_no_ebitda = {**GOOGL_FIXTURE}
    del fixture_no_ebitda["EBITDA"]
    from aletheia.validation.fmp_stage3_adapter import _compute_derived
    fmp_derived = _compute_derived(fixture_no_ebitda)

    expected = ebitda(
        operating_income=fixture_no_ebitda["OperatingIncome"],
        depreciation_total=fixture_no_ebitda["Depreciation_Total"],
    )
    assert fmp_derived["EBITDA"] == pytest.approx(expected)
