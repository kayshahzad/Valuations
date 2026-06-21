"""Residual-income engine tests (Phase A.11) — formula reuse + CNC routing.

  T1  the steady-state identity (RI == justified-P/B × book) via the shared formula
  T2  two-stage handles super-growth (g > Ke) without blowing up
  T3  normalized-ROE resolution: config primary; ex-impairment fallback
  T4  CNC reclassified to residual_income_required and routes to the engine
  T5  CNC end-to-end: coherent IV (not the FCFF +591%), uses NORMALIZED ROE
  T6  architecture wiring: residual_income_required dispatches
"""

from __future__ import annotations

import pytest

from aletheia.calculations.formulas import (
    justified_pb,
    residual_income_value,
)


# ── T1/T2: the shared formula ───────────────────────────────────────

def test_t1_steady_state_identity():
    # constant ROE, g = ROE·retention < Ke → RI == justified-P/B × book
    roe, ke, payout, bvps = 0.11, 0.10, 0.50, 40.0
    retention = 1 - payout
    g = roe * retention                      # 5.5% < Ke
    ri = residual_income_value(
        bvps0=bvps, roe=roe, ke=ke, retention=retention,
        explicit_years=80, terminal_roe=roe, terminal_growth=g)["iv"]
    jpb = justified_pb(roe=roe, ke=ke, growth=g) * bvps
    assert ri == pytest.approx(jpb, rel=1e-4)


def test_t2_super_growth_two_stage_is_finite():
    # no-dividend → retention 1.0 → g = ROE 11% > Ke 10%: must NOT blow up
    ri = residual_income_value(
        bvps0=43.0, roe=0.11, ke=0.10, retention=1.0,
        explicit_years=5, terminal_roe=0.11, terminal_growth=0.025)
    import math
    assert math.isfinite(ri["iv"]) and ri["iv"] > 43.0     # above book (ROE>Ke)


# ── T3: normalized-ROE resolution ───────────────────────────────────

def test_t3_ex_impairment_roe_excludes_loss_years():
    from aletheia.tools.valuation_engines.residual_income_engine import ResidualIncomeEngine
    import pandas as pd

    class _Cls:
        ticker = "TST"; business_model = "residual_income_required"
        sector = "Healthcare Plans"
    df = pd.DataFrame({
        "fiscal_year": [2022, 2023, 2024, 2025, 2026],
        "derived_ROE": [0.10, 0.12, 0.11, -0.30, -0.28],   # impairment years negative
    })

    class _Calc:
        classification = _Cls(); df = None
    calc = _Calc(); calc.df = df
    roe, n = ResidualIncomeEngine()._ex_impairment_roe(calc)
    assert roe == pytest.approx((0.10 + 0.12 + 0.11) / 3, rel=1e-9)   # negatives dropped
    assert n == 3


# ── T4/T5: CNC through the engine ───────────────────────────────────

def test_t4_cnc_reclassified_and_routes():
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.valuation_router import ValuationRouter
    try:
        calc = make_calc_input("CNC")
    except Exception as e:
        pytest.skip(f"CNC not available: {e}")
    assert calc.classification.business_model == "residual_income_required"
    vr = ValuationRouter().execute(calc)
    assert vr.engine == "residual_income"
    assert vr.intrinsic_per_share is not None


def test_t5_cnc_iv_is_coherent_not_fcff_fantasy():
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.valuation_router import ValuationRouter
    try:
        calc = make_calc_input("CNC")
    except Exception as e:
        pytest.skip(f"CNC not available: {e}")

    vr = ValuationRouter().execute(calc)
    iv = vr.intrinsic_per_share
    price = vr.current_price
    # The whole point: a grounded, book-anchored value — NOT the FCFF +591%
    # ($422). Sits within a sane band of book/price, not multiples above it.
    assert 25.0 < iv < 110.0, iv
    if price:
        assert abs(iv / price - 1.0) < 1.0          # within ±100% of price, not 6.9×
    dec = vr.engine_specific["decomposition"]
    # NORMALIZED ROE used, not the GAAP −30%
    assert dec["roe_normalized"] > 0
    # internal consistency: two-stage RI ~ justified-P/B steady-state
    assert dec["iv_justified_pb_steady"] is not None
    assert vr.inputs_snapshot["ke_override_used"] is True   # CNC uses the 10% override


# ── T6: architecture wiring ─────────────────────────────────────────

def test_t6_residual_income_required_dispatches():
    from aletheia.tools.valuation_router import ValuationRouter
    factory = ValuationRouter()._dispatch.get("residual_income_required")
    assert factory is not None
    from aletheia.tools.valuation_engines import ResidualIncomeEngine
    assert isinstance(factory(), ResidualIncomeEngine)
