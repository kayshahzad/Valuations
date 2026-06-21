"""MLP engine tests (Phase A.10) — EV/EBITDA formula + ET routing + decomposition.

  T1  ev_ebitda_equity_value math + the EV→equity bridge
  T2  guards: non-positive EBITDA / multiple → None
  T3  ET reclassified to mlp_required and routes to MlpEngine
  T4  ET end-to-end: coherent per-unit IV, EV bridge, leverage read
  T5  distribution cross-check present + divergence framed
  T6  architecture: mlp_required dispatches (no UnknownBusinessModelError)
"""

from __future__ import annotations

import pytest

from aletheia.calculations.formulas import (
    ev_ebitda_decomposition,
    ev_ebitda_equity_value,
)


# ── T1/T2: the formula ──────────────────────────────────────────────

def test_ev_ebitda_equity_bridge():
    # EBITDA 14.7B × 9.5 = 139.65B EV; − 64.9B net debt = 74.75B equity
    eq = ev_ebitda_equity_value(ebitda=14.7e9, ev_ebitda_multiple=9.5, net_debt=64.9e9)
    assert eq == pytest.approx(14.7e9 * 9.5 - 64.9e9, rel=1e-9)


def test_ev_ebitda_decomposition_shape_and_leverage():
    dec = ev_ebitda_decomposition(
        ebitda=14.7e9, ev_ebitda_multiple=9.5, net_debt=64.9e9, units=3457.4e6)
    assert dec["enterprise_value"] == pytest.approx(139.65e9, rel=1e-6)
    assert dec["net_debt_to_ebitda"] == pytest.approx(64.9 / 14.7, rel=1e-6)
    assert dec["per_unit"] == pytest.approx(dec["equity_value"] / 3457.4e6, rel=1e-9)


def test_ev_ebitda_guards():
    assert ev_ebitda_equity_value(ebitda=-1.0, ev_ebitda_multiple=9.5, net_debt=1.0) is None
    assert ev_ebitda_equity_value(ebitda=10.0, ev_ebitda_multiple=0.0, net_debt=1.0) is None
    assert ev_ebitda_equity_value(ebitda=None, ev_ebitda_multiple=9.5, net_debt=1.0) is None


def test_high_net_debt_yields_thin_equity():
    """Leverage matters: same EBITDA/multiple, more net debt → less equity."""
    low = ev_ebitda_equity_value(ebitda=14.7e9, ev_ebitda_multiple=9.5, net_debt=30e9)
    high = ev_ebitda_equity_value(ebitda=14.7e9, ev_ebitda_multiple=9.5, net_debt=64.9e9)
    assert high < low


# ── T3/T4/T5: ET through the engine ─────────────────────────────────

def test_et_reclassified_to_mlp_required():
    from aletheia.utils.calc_input_builder import make_calc_input
    try:
        calc = make_calc_input("ET")
    except Exception as e:
        pytest.skip(f"ET not available: {e}")
    assert calc.classification.business_model == "mlp_required"


def test_et_routes_to_mlp_engine_with_coherent_iv():
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.valuation_router import ValuationRouter
    try:
        calc = make_calc_input("ET")
    except Exception as e:
        pytest.skip(f"ET not available: {e}")

    vr = ValuationRouter().execute(calc)
    assert vr.engine == "mlp"
    assert vr.intrinsic_per_share is not None
    assert 5.0 < vr.intrinsic_per_share < 60.0      # sane per-unit band

    dec = vr.engine_specific["decomposition"]
    # EV→equity bridge holds
    assert dec["enterprise_value"] == pytest.approx(
        dec["ebitda"] * dec["ev_ebitda_multiple"], rel=1e-9)
    assert dec["equity_value"] == pytest.approx(
        dec["enterprise_value"] - dec["net_debt"], rel=1e-9)
    # high leverage surfaced (ET ~4.4×)
    assert dec["net_debt_to_ebitda"] > 3.0
    assert vr.inputs_snapshot["valuation_basis"].startswith("EV/EBITDA")


def test_et_distribution_cross_check_present():
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.valuation_router import ValuationRouter
    try:
        calc = make_calc_input("ET")
    except Exception as e:
        pytest.skip(f"ET not available: {e}")

    dl = ValuationRouter().execute(calc).engine_specific.get("distribution_leg")
    assert dl is not None
    assert dl["current_dpu"] == pytest.approx(1.30)
    assert dl["intrinsic_per_unit"] is not None
    assert dl["distribution_yield"] is not None      # DPU / price


# ── T6: architecture wiring ─────────────────────────────────────────

def test_mlp_required_dispatches_without_error():
    """mlp_required must be a known model in the router (no
    UnknownBusinessModelError) and resolve to the MlpEngine."""
    from aletheia.tools.valuation_router import ValuationRouter
    router = ValuationRouter()
    factory = router._dispatch.get("mlp_required")
    assert factory is not None
    from aletheia.tools.valuation_engines import MlpEngine
    assert isinstance(factory(), MlpEngine)
