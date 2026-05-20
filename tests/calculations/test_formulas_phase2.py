"""Phase 2 unit + parity tests — FCF, FCFF, NetDebt + helpers.

Pins behavior of the central cash-flow and balance-sheet formulas and
asserts FMP-path / cleaning-engine parity. Companion to
``test_formulas_phase1.py`` covering the second wave of centralization.
"""
from __future__ import annotations

import pytest

from aletheia.calculations.formulas import (
    fcf,
    fcff,
    gross_debt,
    liquid_assets,
    net_debt,
)


# ── FCF ─────────────────────────────────────────────────────────────


def test_fcf_basic():
    assert fcf(operating_cf=100.0, capex=30.0) == pytest.approx(70.0)


def test_fcf_handles_negative_capex_sign():
    # XBRL files CapEx negative (cash outflow); FMP positive. Both must
    # produce the same FCF.
    assert fcf(operating_cf=100.0, capex=-30.0) == pytest.approx(70.0)
    assert fcf(operating_cf=100.0, capex=30.0) == pytest.approx(70.0)


def test_fcf_no_capex_defaults_to_zero():
    # Rare but legitimate — asset-light filer with no capex
    assert fcf(operating_cf=100.0, capex=None) == pytest.approx(100.0)


def test_fcf_returns_none_when_ocf_missing():
    assert fcf(operating_cf=None, capex=30.0) is None


# ── FCFF ────────────────────────────────────────────────────────────


def test_fcff_full_formula():
    # NOPAT 80 + D&A 20 - CapEx 30 - ΔNWC 5 = 65
    assert fcff(
        nopat=80.0, depreciation=20.0, capex=30.0, delta_nwc=5.0,
    ) == pytest.approx(65.0)


def test_fcff_negative_delta_nwc_frees_cash():
    # NWC shrinks (positive cash effect) → ΔNWC < 0 → FCFF higher
    assert fcff(
        nopat=80.0, depreciation=20.0, capex=30.0, delta_nwc=-10.0,
    ) == pytest.approx(80.0)


def test_fcff_delta_nwc_defaults_to_zero():
    # Some early-history filers don't disclose ΔNWC — formula falls
    # through to zero rather than returning None
    assert fcff(
        nopat=80.0, depreciation=20.0, capex=30.0, delta_nwc=None,
    ) == pytest.approx(70.0)


def test_fcff_returns_none_when_nopat_missing():
    assert fcff(
        nopat=None, depreciation=20.0, capex=30.0, delta_nwc=5.0,
    ) is None


def test_fcff_returns_none_when_depreciation_missing():
    assert fcff(
        nopat=80.0, depreciation=None, capex=30.0, delta_nwc=5.0,
    ) is None


def test_fcff_returns_none_when_capex_missing():
    # CapEx None is meaningfully different from CapEx=0 — None means
    # "data unavailable", 0 means "no capex this period".
    assert fcff(
        nopat=80.0, depreciation=20.0, capex=None, delta_nwc=5.0,
    ) is None


def test_fcff_handles_negative_capex_sign():
    # Sign-invariant on capex (same as fcf)
    assert fcff(
        nopat=80.0, depreciation=20.0, capex=-30.0, delta_nwc=5.0,
    ) == pytest.approx(65.0)


# ── GrossDebt ──────────────────────────────────────────────────────


def test_gross_debt_sums_components():
    assert gross_debt(
        long_term_debt=100.0,
        short_term_debt=20.0,
        current_portion_lt_debt=10.0,
        finance_lease_total=15.0,
    ) == pytest.approx(145.0)


def test_gross_debt_handles_missing_components_as_zero():
    # Most filers don't carry every component — missing pieces are
    # treated as zero, not None-propagation.
    assert gross_debt(long_term_debt=100.0) == pytest.approx(100.0)


def test_gross_debt_returns_none_when_everything_missing():
    assert gross_debt(
        long_term_debt=None,
        short_term_debt=None,
        current_portion_lt_debt=None,
        finance_lease_total=None,
    ) is None


# ── LiquidAssets ───────────────────────────────────────────────────


def test_liquid_assets_sums_cash_and_investments():
    # AAPL-style: $30B cash + $30B ST-inv + $80B LT-inv = $140B
    # liquid (LT marketable securities are debt-equivalent offsets
    # under EV convention)
    assert liquid_assets(
        cash=30.0, short_term_investments=30.0, long_term_investments=80.0,
    ) == pytest.approx(140.0)


def test_liquid_assets_handles_missing_investments():
    assert liquid_assets(cash=50.0) == pytest.approx(50.0)


def test_liquid_assets_returns_none_when_everything_missing():
    assert liquid_assets(
        cash=None, short_term_investments=None, long_term_investments=None,
    ) is None


# ── NetDebt ────────────────────────────────────────────────────────


def test_net_debt_basic():
    assert net_debt(gross_debt=145.0, liquid_assets=160.0) == pytest.approx(-15.0)


def test_net_debt_negative_means_net_cash_position():
    # Cash-rich filer (GOOGL-style): net debt < 0 is the correct,
    # meaningful representation — formula does not clamp.
    assert net_debt(gross_debt=50.0, liquid_assets=120.0) == pytest.approx(-70.0)


def test_net_debt_handles_one_side_missing():
    # If gross debt is None but liquid assets is present, the formula
    # treats gross_debt as 0 — implies "filer has no debt", which is
    # the right semantic for a debt-free balance sheet.
    assert net_debt(gross_debt=None, liquid_assets=50.0) == pytest.approx(-50.0)
    assert net_debt(gross_debt=100.0, liquid_assets=None) == pytest.approx(100.0)


def test_net_debt_returns_none_when_both_missing():
    assert net_debt(gross_debt=None, liquid_assets=None) is None


# ── Cross-provider parity (regression net) ─────────────────────────


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
}


def test_parity_fmp_fcff_uses_full_formula():
    """Post-Phase-2, the FMP adapter's FCFF should match the
    NOPAT + D&A − CapEx − ΔNWC formula (no longer aliased to FCF)."""
    from aletheia.validation.fmp_stage3_adapter import _compute_derived
    fmp_derived = _compute_derived(GOOGL_FIXTURE)

    nopat_val = fmp_derived["NOPAT"]
    expected_fcff = fcff(
        nopat=nopat_val,
        depreciation=GOOGL_FIXTURE["Depreciation_Total"],
        capex=GOOGL_FIXTURE["CapEx"],
        delta_nwc=GOOGL_FIXTURE["ChangeInWorkingCapital"],
    )
    assert fmp_derived["FCFF"] == pytest.approx(expected_fcff)


def test_parity_fmp_fcff_now_diverges_from_fcf():
    """Sanity check that Phase 2 actually changed behavior — FCFF
    should NOT equal FCF when ΔNWC and D&A inputs are present."""
    from aletheia.validation.fmp_stage3_adapter import _compute_derived
    fmp_derived = _compute_derived(GOOGL_FIXTURE)
    # With non-trivial ΔNWC + D&A, FCFF differs from FCF by the
    # ΔNWC and (D&A - effective-tax-on-D&A) magnitude
    assert fmp_derived["FCFF"] != pytest.approx(fmp_derived["FCF"])


def test_parity_fmp_net_debt_includes_finance_leases_and_lt_inv():
    """Post-Phase-2, FMP NetDebt should include finance leases +
    current LT-debt + long-term investments (was previously
    TotalDebt − Cash − ShortTermInvestments only)."""
    from aletheia.validation.fmp_stage3_adapter import _compute_derived
    fmp_derived = _compute_derived(GOOGL_FIXTURE)

    expected_gd = gross_debt(
        long_term_debt=GOOGL_FIXTURE["LongTermDebt"],
        short_term_debt=GOOGL_FIXTURE["ShortTermDebt"],
        current_portion_lt_debt=GOOGL_FIXTURE["CurrentPortionLongTermDebt"],
        finance_lease_total=GOOGL_FIXTURE["FinanceLeaseLiability_Total"],
    )
    expected_la = liquid_assets(
        cash=GOOGL_FIXTURE["Cash"],
        short_term_investments=GOOGL_FIXTURE["ShortTermInvestments"],
        long_term_investments=GOOGL_FIXTURE["LongTermInvestments"],
    )
    expected_nd = net_debt(gross_debt=expected_gd, liquid_assets=expected_la)
    assert fmp_derived["NetDebt"] == pytest.approx(expected_nd)


def test_parity_googl_net_debt_is_negative():
    """GOOGL is cash-rich (~$100B in cash+ST-inv against ~$47B debt).
    Post-Phase-2 NetDebt should be negative — pre-Phase-2 the same
    direction held but the magnitude was smaller because FMP missed
    LT investments and finance leases."""
    from aletheia.validation.fmp_stage3_adapter import _compute_derived
    fmp_derived = _compute_derived(GOOGL_FIXTURE)
    assert fmp_derived["NetDebt"] < 0, (
        f"Expected negative NetDebt (cash-rich filer); "
        f"got {fmp_derived['NetDebt']:,.0f}"
    )
