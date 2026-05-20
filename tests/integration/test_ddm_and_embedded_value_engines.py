"""Phase A.7 tests — DDM + Embedded Value engines.

Covers:
  - DDM central formula math + edge cases
  - Embedded-value (book-value compounding) formula math
  - Engines load specialized inputs correctly
  - Router dispatches JPM/AXP/UNH/CNC → DdmEngine, BRK-B → EmbeddedValueEngine
  - Empty-state handling: CNC ($0 dividend) returns None gracefully
  - Cost-of-equity override behavior
"""
from __future__ import annotations

import pytest

from aletheia.calculations.formulas import (
    book_value_compounding_decomposition,
    book_value_compounding_intrinsic,
    ddm_decomposition,
    ddm_intrinsic_value,
)


# ── DDM formula ────────────────────────────────────────────────────


def test_ddm_basic():
    """JPM-style inputs: $6 DPS, 7% explicit growth × 5y, 10% Ke,
    4% terminal. Should produce a finite positive IV."""
    ips = ddm_intrinsic_value(
        current_dps=6.00,
        cost_of_equity=0.10,
        explicit_growth=0.07,
        explicit_years=5,
        terminal_growth=0.04,
    )
    assert ips is not None
    assert 50.0 < ips < 250.0   # Sanity range for JPM-style filers


def test_ddm_zero_dps_returns_none():
    """Centene-class filer (no dividend) → DDM undefined → None."""
    assert ddm_intrinsic_value(
        current_dps=0.0,
        cost_of_equity=0.10,
        explicit_growth=0.10,
        explicit_years=5,
    ) is None


def test_ddm_negative_dps_returns_none():
    assert ddm_intrinsic_value(
        current_dps=-1.0,
        cost_of_equity=0.10,
        explicit_growth=0.05,
        explicit_years=5,
    ) is None


def test_ddm_ke_below_terminal_g_returns_none():
    """Gordon divergence guard — same convention as rate-base formula."""
    assert ddm_intrinsic_value(
        current_dps=5.0,
        cost_of_equity=0.03,
        explicit_growth=0.05,
        explicit_years=5,
        terminal_growth=0.04,
    ) is None


def test_ddm_higher_growth_increases_value():
    low = ddm_intrinsic_value(
        current_dps=5.0, cost_of_equity=0.10,
        explicit_growth=0.05, explicit_years=5,
    )
    high = ddm_intrinsic_value(
        current_dps=5.0, cost_of_equity=0.10,
        explicit_growth=0.15, explicit_years=5,
    )
    assert high > low


def test_ddm_decomposition_shape():
    d = ddm_decomposition(
        current_dps=6.00, cost_of_equity=0.10,
        explicit_growth=0.07, explicit_years=5,
    )
    assert d is not None
    assert len(d["yearly"]) == 5
    assert "terminal_value" in d
    assert "pv_terminal" in d
    assert "intrinsic_per_share" in d
    # Reconciles
    assert abs(d["pv_explicit"] + d["pv_terminal"] - d["intrinsic_per_share"]) < 0.01


# ── Embedded-value (book-value compounding) ───────────────────────


def test_bvc_basic():
    """BRK-B-style: $337 BVPS, 11% ROE, 1.4× target P/B, 5y horizon,
    9% Ke. Should produce finite positive IV."""
    ips = book_value_compounding_intrinsic(
        book_value_per_share=337.15,
        expected_roe=0.11,
        cost_of_equity=0.09,
        target_pb=1.4,
        horizon_years=5,
        payout_ratio=0.0,
    )
    assert ips is not None
    assert 400 < ips < 700   # Sanity range


def test_bvc_higher_roe_increases_value():
    low = book_value_compounding_intrinsic(
        book_value_per_share=100.0, expected_roe=0.08,
        cost_of_equity=0.09, target_pb=1.5, horizon_years=5,
    )
    high = book_value_compounding_intrinsic(
        book_value_per_share=100.0, expected_roe=0.15,
        cost_of_equity=0.09, target_pb=1.5, horizon_years=5,
    )
    assert high > low


def test_bvc_higher_payout_reduces_value():
    """Higher payout → less retained-earnings compounding → lower IV."""
    no_payout = book_value_compounding_intrinsic(
        book_value_per_share=100.0, expected_roe=0.12,
        cost_of_equity=0.09, target_pb=1.5, horizon_years=5,
        payout_ratio=0.0,
    )
    high_payout = book_value_compounding_intrinsic(
        book_value_per_share=100.0, expected_roe=0.12,
        cost_of_equity=0.09, target_pb=1.5, horizon_years=5,
        payout_ratio=0.50,
    )
    assert no_payout > high_payout


def test_bvc_returns_none_on_missing_inputs():
    assert book_value_compounding_intrinsic(
        book_value_per_share=None, expected_roe=0.10,
        cost_of_equity=0.09, target_pb=1.5, horizon_years=5,
    ) is None
    assert book_value_compounding_intrinsic(
        book_value_per_share=337.0, expected_roe=None,
        cost_of_equity=0.09, target_pb=1.5, horizon_years=5,
    ) is None


def test_bvc_returns_none_on_zero_horizon():
    assert book_value_compounding_intrinsic(
        book_value_per_share=337.0, expected_roe=0.11,
        cost_of_equity=0.09, target_pb=1.4, horizon_years=0,
    ) is None


def test_bvc_decomposition_shape():
    d = book_value_compounding_decomposition(
        book_value_per_share=337.15, expected_roe=0.11,
        cost_of_equity=0.09, target_pb=1.4, horizon_years=5,
        payout_ratio=0.0,
    )
    assert d is not None
    assert d["starting_bvps"] == 337.15
    assert len(d["yearly_bvps"]) == 5
    assert d["future_bvps"] > d["starting_bvps"]   # compounding worked
    assert d["target_equity_per_share"] > d["future_bvps"]   # P/B > 1
    assert d["intrinsic_per_share"] > 0


# ── Router dispatch end-to-end ────────────────────────────────────


def test_router_dispatches_jpm_to_ddm_engine():
    from aletheia.tools.valuation_router import ValuationRouter
    from aletheia.utils.calc_input_builder import make_calc_input
    result = ValuationRouter().execute(make_calc_input("JPM"))
    assert result.engine == "ddm"
    assert result.intrinsic_per_share is not None
    assert result.current_price is not None


def test_router_dispatches_axp_to_ddm_engine():
    from aletheia.tools.valuation_router import ValuationRouter
    from aletheia.utils.calc_input_builder import make_calc_input
    result = ValuationRouter().execute(make_calc_input("AXP"))
    assert result.engine == "ddm"
    assert result.intrinsic_per_share is not None


def test_router_dispatches_unh_to_ddm_engine():
    from aletheia.tools.valuation_router import ValuationRouter
    from aletheia.utils.calc_input_builder import make_calc_input
    result = ValuationRouter().execute(make_calc_input("UNH"))
    assert result.engine == "ddm"
    assert result.intrinsic_per_share is not None


def test_router_cnc_returns_empty_state_no_dividend():
    """CNC pays no dividend → DDM engine returns empty-state
    ValuationResult with informative warning, not an exception."""
    from aletheia.tools.valuation_router import ValuationRouter
    from aletheia.utils.calc_input_builder import make_calc_input
    result = ValuationRouter().execute(make_calc_input("CNC"))
    assert result.engine == "ddm"
    assert result.intrinsic_per_share is None
    assert result.warnings
    # Either "no dividend" or "incomplete inputs" wording is acceptable
    assert any(
        ("no dividend" in w.lower() or "incomplete" in w.lower()
         or "missing" in w.lower())
        for w in result.warnings
    )


def test_router_dispatches_brkb_to_embedded_value_engine():
    from aletheia.tools.valuation_router import ValuationRouter
    from aletheia.utils.calc_input_builder import make_calc_input
    result = ValuationRouter().execute(make_calc_input("BRK-B"))
    assert result.engine == "embedded_value"
    assert result.intrinsic_per_share is not None
    assert result.current_price is not None


# ── DDM engine — cost-of-equity override ──────────────────────────


def test_ddm_engine_respects_cost_of_equity_override():
    """JPM's config sets cost_of_equity_override_pct=10. Verify the
    engine uses 10% Ke, not the CAPM-derived value."""
    from aletheia.tools.valuation_engines import DdmEngine
    from aletheia.utils.calc_input_builder import make_calc_input
    result = DdmEngine().compute_intrinsic_value(make_calc_input("JPM"))
    assert result.engine == "ddm"
    assert result.intrinsic_per_share is not None
    snap = result.inputs_snapshot
    assert snap["ke_override_used"] is True
    assert snap["cost_of_equity"] == pytest.approx(0.10, abs=1e-9)


def test_ddm_engine_uses_capm_when_no_override():
    """AXP's config has cost_of_equity_override_pct=null → engine
    computes Ke via central CAPM formula."""
    from aletheia.tools.valuation_engines import DdmEngine
    from aletheia.utils.calc_input_builder import make_calc_input
    result = DdmEngine().compute_intrinsic_value(make_calc_input("AXP"))
    assert result.engine == "ddm"
    snap = result.inputs_snapshot
    assert snap["ke_override_used"] is False
    # CAPM value depends on live β; just check it's in a sane range
    assert 0.04 < snap["cost_of_equity"] < 0.15


# ── EmbeddedValue engine ──────────────────────────────────────────


def test_embedded_value_engine_decomposition_in_engine_specific():
    from aletheia.tools.valuation_engines import EmbeddedValueEngine
    from aletheia.utils.calc_input_builder import make_calc_input
    result = EmbeddedValueEngine().compute_intrinsic_value(
        make_calc_input("BRK-B"),
    )
    decomp = result.engine_specific["decomposition"]
    assert decomp["starting_bvps"] == 337.15
    assert len(decomp["yearly_bvps"]) == 5
    assert decomp["intrinsic_per_share"] > 0


# ── Backward-compat: FCFF + rate-base still work ──────────────────


def test_fcff_ticker_still_routes_correctly():
    from aletheia.tools.valuation_router import ValuationRouter
    from aletheia.utils.calc_input_builder import make_calc_input
    result = ValuationRouter().execute(make_calc_input("AAPL"))
    assert result.engine == "fcff"
    assert result.intrinsic_per_share is not None


def test_rate_base_ticker_still_routes_correctly():
    from aletheia.tools.valuation_router import ValuationRouter
    from aletheia.utils.calc_input_builder import make_calc_input
    result = ValuationRouter().execute(make_calc_input("NEE"))
    assert result.engine == "rate_base"
    assert result.intrinsic_per_share is not None
