"""Phase 4 unit tests — cost of capital + valuation multiples.

Pins behavior of WACC (and its CAPM + Kd components), justified
EV/EBITDA, and the standard valuation multiples. The multiples were
IDENTICAL on both call sites pre-migration (screening_ratios and
multiple_decomposition); WACC moved from a single site
(dcf_engine.compute_wacc) into the central module.
"""
from __future__ import annotations

import pytest

from aletheia.calculations.formulas import (
    cash_conversion_ratio,
    cost_of_debt,
    cost_of_equity,
    current_ratio,
    debt_to_equity,
    dividend_yield,
    ev_to_ebit,
    ev_to_ebitda,
    ev_to_fcf,
    interest_coverage,
    justified_ev_ebitda,
    net_debt_to_ebitda,
    price_to_book,
    price_to_earnings,
    price_to_sales,
    wacc,
)
from aletheia.calculations.formulas.cost_of_capital import (
    DEFAULT_WACC,
    KD_CAP,
    KD_FALLBACK_SPREAD,
    WACC_CEILING,
    WACC_FLOOR_MIN,
)


# ── Cost of equity (CAPM) ──────────────────────────────────────────


def test_ke_capm_basic():
    # Rf 4% + Beta 1.2 × MRP 6% = 11.2%
    assert cost_of_equity(
        risk_free_rate=0.04, beta=1.2, market_risk_premium=0.06,
    ) == pytest.approx(0.112)


def test_ke_returns_none_when_inputs_missing():
    assert cost_of_equity(
        risk_free_rate=None, beta=1.2, market_risk_premium=0.06,
    ) is None
    assert cost_of_equity(
        risk_free_rate=0.04, beta=None, market_risk_premium=0.06,
    ) is None


# ── Cost of debt ───────────────────────────────────────────────────


def test_kd_basic():
    # Interest 10 / Debt 200 = 5%
    assert cost_of_debt(
        interest_expense=10, total_debt=200, risk_free_rate=0.04,
    ) == pytest.approx(0.05)


def test_kd_caps_at_15_pct():
    # Interest 50 / Debt 200 = 25%, capped at KD_CAP
    assert cost_of_debt(
        interest_expense=50, total_debt=200, risk_free_rate=0.04,
    ) == pytest.approx(KD_CAP)


def test_kd_fallback_when_debt_missing():
    # Falls back to Rf + 150bps
    assert cost_of_debt(
        interest_expense=None, total_debt=None, risk_free_rate=0.04,
    ) == pytest.approx(0.04 + KD_FALLBACK_SPREAD)


def test_kd_returns_none_when_no_fallback_possible():
    # Neither primary nor fallback computable
    assert cost_of_debt(
        interest_expense=None, total_debt=None, risk_free_rate=None,
    ) is None


# ── WACC ───────────────────────────────────────────────────────────


def test_wacc_basic():
    # Capital structure: 80% equity, 20% debt
    # WACC = 0.8 × 0.112 + 0.2 × 0.05 × (1 - 0.21)
    #      = 0.0896 + 0.0079 = 0.0975
    result = wacc(
        cost_of_equity=0.112, cost_of_debt=0.05,
        total_equity=800, total_debt=200,
        tax_rate=0.21, risk_free_rate=0.04,
    )
    assert result == pytest.approx(0.0975, abs=1e-4)


def test_wacc_returns_default_when_capital_zero():
    assert wacc(
        cost_of_equity=0.10, cost_of_debt=0.05,
        total_equity=0, total_debt=0,
        tax_rate=0.21, risk_free_rate=0.04,
    ) == DEFAULT_WACC


def test_wacc_floor_kicks_in():
    # Very low CAPM components — WACC would be below Rf + 1%; floor lifts it
    result = wacc(
        cost_of_equity=0.02, cost_of_debt=0.01,
        total_equity=800, total_debt=200,
        tax_rate=0.21, risk_free_rate=0.04,
    )
    expected_floor = max(WACC_FLOOR_MIN, 0.04 + 0.01)
    assert result == pytest.approx(expected_floor)


def test_wacc_ceiling_kicks_in():
    # Very high CAPM — WACC capped at 18%
    result = wacc(
        cost_of_equity=0.30, cost_of_debt=0.20,
        total_equity=800, total_debt=200,
        tax_rate=0.21, risk_free_rate=0.04,
    )
    assert result == pytest.approx(WACC_CEILING)


def test_wacc_returns_default_when_components_missing():
    assert wacc(
        cost_of_equity=None, cost_of_debt=0.05,
        total_equity=800, total_debt=200,
        tax_rate=0.21,
    ) == DEFAULT_WACC


# ── Valuation multiples ────────────────────────────────────────────


def test_pe_basic():
    assert price_to_earnings(price=100.0, eps=5.0) == pytest.approx(20.0)


def test_pe_returns_none_on_zero_or_negative_eps():
    # Loss-year companies — P/E undefined
    assert price_to_earnings(price=100.0, eps=0.0) is None
    assert price_to_earnings(price=100.0, eps=-5.0) is None


def test_pb_returns_none_on_negative_equity():
    # Aggressive-buyback filers (LOW, HD post-treasury)
    assert price_to_book(market_cap=200.0, book_equity=-50.0) is None


def test_ev_ebitda_basic():
    assert ev_to_ebitda(enterprise_value=200.0, ebitda=20.0) == pytest.approx(10.0)


def test_ev_ebitda_returns_none_on_negative_ebitda():
    # Loss-EBITDA years
    assert ev_to_ebitda(enterprise_value=200.0, ebitda=-10.0) is None


def test_ev_ebit_basic():
    assert ev_to_ebit(enterprise_value=200.0, ebit=15.0) == pytest.approx(13.333, abs=1e-3)


def test_ev_fcf_basic():
    assert ev_to_fcf(enterprise_value=200.0, fcf=10.0) == pytest.approx(20.0)


def test_price_to_sales_basic():
    assert price_to_sales(market_cap=1000.0, revenue=400.0) == pytest.approx(2.5)


def test_nd_ebitda_negative_for_net_cash_filer():
    # Net cash position — ratio is negative, correct
    assert net_debt_to_ebitda(net_debt=-50.0, ebitda=20.0) == pytest.approx(-2.5)


def test_de_returns_none_on_non_positive_equity():
    assert debt_to_equity(total_debt=100.0, total_equity=-50.0) is None
    assert debt_to_equity(total_debt=100.0, total_equity=0.0) is None


def test_interest_coverage_uses_magnitude():
    # Sign of interest_expense doesn't matter
    assert interest_coverage(ebit=100.0, interest_expense=10.0) == pytest.approx(10.0)
    assert interest_coverage(ebit=100.0, interest_expense=-10.0) == pytest.approx(10.0)


def test_interest_coverage_returns_none_on_zero():
    # Debt-free filer — display layer should show "∞" or "—"
    assert interest_coverage(ebit=100.0, interest_expense=0.0) is None


def test_current_ratio_basic():
    assert current_ratio(
        current_assets=200.0, current_liabilities=100.0,
    ) == pytest.approx(2.0)


def test_dividend_yield_basic():
    assert dividend_yield(
        dividends_paid=10.0, market_cap=500.0,
    ) == pytest.approx(0.02)


# ── Justified EV/EBITDA (Liberti decomposition) ────────────────────


def test_justified_ev_ebitda_basic():
    # NOPAT 100, EBITDA 150, ROIC 20%, WACC 9%, g 3%
    # cash_conv = 100 × (1 - 0.03/0.20) / 150 = 100 × 0.85 / 150 = 0.5667
    # justified = 0.5667 / (0.09 - 0.03) = 9.44x
    result = justified_ev_ebitda(
        nopat=100.0, ebitda=150.0, roic=0.20,
        wacc=0.09, terminal_growth=0.03,
    )
    assert result == pytest.approx(9.444, abs=0.01)


def test_justified_returns_none_when_wacc_below_g():
    # WACC ≤ g → Gordon growth model diverges
    assert justified_ev_ebitda(
        nopat=100.0, ebitda=150.0, roic=0.20,
        wacc=0.03, terminal_growth=0.05,
    ) is None


def test_justified_returns_none_on_negative_ebitda():
    assert justified_ev_ebitda(
        nopat=100.0, ebitda=-50.0, roic=0.20,
        wacc=0.09, terminal_growth=0.03,
    ) is None


def test_cash_conversion_ratio_basic():
    result = cash_conversion_ratio(
        nopat=100.0, ebitda=150.0, roic=0.20, terminal_growth=0.03,
    )
    # 100 × (1 − 0.03/0.20) / 150 = 100 × 0.85 / 150
    assert result == pytest.approx(0.5667, abs=0.001)


def test_cash_conversion_uses_roic_floor():
    # ROIC below 8% gets floored — cash-conversion stays sensible
    result = cash_conversion_ratio(
        nopat=100.0, ebitda=150.0, roic=0.04, terminal_growth=0.03,
    )
    # With 8% floor: 100 × (1 − 0.03/0.08) / 150
    expected = 100.0 * (1 - 0.03 / 0.08) / 150.0
    assert result == pytest.approx(expected)


# ── Cross-call-site parity (regression net) ────────────────────────


def test_parity_multiple_decomposition_uses_central_formula():
    """The legacy _compute_justified_ev_ebitda wrapper should produce
    the same tuple as direct central-formula calls."""
    from aletheia.tools.multiple_decomposition import _compute_justified_ev_ebitda

    legacy = _compute_justified_ev_ebitda(
        nopat=100.0, ebitda=150.0, roic=0.20,
        wacc=0.09, g_terminal=0.03,
    )
    direct_just = justified_ev_ebitda(
        nopat=100.0, ebitda=150.0, roic=0.20,
        wacc=0.09, terminal_growth=0.03,
    )
    direct_cc = cash_conversion_ratio(
        nopat=100.0, ebitda=150.0, roic=0.20, terminal_growth=0.03,
    )
    assert legacy == pytest.approx((direct_just, direct_cc))


def test_parity_legacy_returns_zeros_on_degenerate_inputs():
    """The legacy contract was tuple-of-floats with 0.0 on
    degenerate inputs (not None) — preserved through Phase 4."""
    from aletheia.tools.multiple_decomposition import _compute_justified_ev_ebitda
    # WACC ≤ g (degenerate)
    result = _compute_justified_ev_ebitda(
        nopat=100.0, ebitda=150.0, roic=0.20,
        wacc=0.03, g_terminal=0.05,
    )
    # Justified returns None → coerced to 0.0; cash_conv still
    # computes for the central formula (no WACC dependence)
    assert result[0] == 0.0
