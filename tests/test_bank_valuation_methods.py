"""Bank convergent-set tests — the financial-sector analog of the FCFF
four-method convergence suite.

T1  golden steady-state convergence: RI ≡ justified-P/B·BVPS ≡ Gordon DDM
T1b the two-stage RI telescopes to the closed form (terminal_roe=roe, g=g)
T2  single-stage guards: g ≥ Ke → justified P/B / Gordon undefined
T3  JPM realistic: two-stage RI > justified-steady > DDM headline (understatement)
T4  isolation: non-financial business model → available=False, nothing renders
"""

from __future__ import annotations

import math

import pytest

from aletheia.tools.bank_valuation_methods import (
    build_bank_valuation_methods,
    gordon_ddm_value,
    justified_pb,
    residual_income_value,
    value_bank_steady_state,
)


# ─────────────────── T1: the golden steady-state identity ──────────────────

@pytest.mark.parametrize("roe,payout,ke,bvps", [
    (0.12, 0.50, 0.10, 100.0),     # g = 6% < Ke
    (0.15, 0.60, 0.11, 50.0),      # g = 6% < Ke
    (0.10, 0.40, 0.09, 80.0),      # g = 6% < Ke
    (0.14, 0.70, 0.12, 25.0),      # g = 4.2% < Ke
])
def test_t1_golden_convergence(roe, payout, ke, bvps):
    conv = value_bank_steady_state(bvps0=bvps, roe=roe, payout=payout, ke=ke)
    assert conv.converged, (
        f"bank convergent set did not agree: RI={conv.iv_residual_income:.4f} "
        f"jPB={conv.iv_justified_pb:.4f} Gordon={conv.iv_gordon_ddm:.4f} "
        f"spread={conv.max_spread_pct:.4%}")
    # all three within 0.5% — the bank analog of the $9,868 fixture
    assert conv.max_spread_pct < 0.005


def test_t1_closed_form_value():
    # BVPS 100, ROE 12%, payout 50% → g 6%, Ke 10% → (0.12-0.06)/(0.10-0.06)=1.5×
    conv = value_bank_steady_state(bvps0=100.0, roe=0.12, payout=0.50, ke=0.10)
    assert conv.iv_justified_pb == pytest.approx(150.0, rel=1e-6)
    assert conv.iv_residual_income == pytest.approx(150.0, rel=1e-3)
    assert conv.iv_gordon_ddm == pytest.approx(150.0, rel=1e-6)


def test_t1b_two_stage_telescopes_to_closed_form():
    # With terminal_roe == roe and terminal_growth == g₁, the finite two-stage
    # must equal the infinite closed form BVPS·(ROE−g)/(Ke−g).
    roe, ke, payout, bvps = 0.13, 0.10, 0.55, 60.0
    retention = 1 - payout
    g = roe * retention
    closed = bvps * (roe - g) / (ke - g)
    ri = residual_income_value(
        bvps0=bvps, roe=roe, ke=ke, retention=retention,
        explicit_years=80, terminal_roe=roe, terminal_growth=g)
    assert ri["iv"] == pytest.approx(closed, rel=1e-4)


# ─────────────────── T2: single-stage guards (g ≥ Ke) ──────────────────────

def test_t2_justified_pb_undefined_when_growth_exceeds_ke():
    # JPM-like: g 11.5% > Ke 10% → single-stage undefined
    assert justified_pb(roe=0.16, ke=0.10, growth=0.115) is None
    assert gordon_ddm_value(bvps0=130.0, roe=0.16, ke=0.10, payout=0.29) is None


def test_t2_justified_pb_defined_when_growth_below_ke():
    m = justified_pb(roe=0.16, ke=0.10, growth=0.04)
    assert m == pytest.approx((0.16 - 0.04) / (0.10 - 0.04), rel=1e-9)  # 2.0×


def test_t2_two_stage_handles_super_growth():
    # Near-term g > Ke must NOT blow up the two-stage (finite explicit sum).
    ri = residual_income_value(
        bvps0=130.0, roe=0.163, ke=0.10, retention=0.71,
        explicit_years=10, terminal_roe=0.163, terminal_growth=0.04)
    assert math.isfinite(ri["iv"]) and ri["iv"] > 130.0   # above book


# ─────────────────── T3: JPM realistic via the orchestrator ────────────────

def test_t3_jpm_residual_income_exceeds_ddm_headline():
    pytest.importorskip("pandas")
    from aletheia.utils.calc_input_builder import make_calc_input
    try:
        calc = make_calc_input("JPM")
    except Exception as e:
        pytest.skip(f"JPM not available in DB: {e}")

    # Stand in a DDM-shaped result so the reconciliation has a headline to read.
    class _R:
        engine = "ddm"
        intrinsic_per_share = 118.0
        inputs_snapshot = {"cost_of_equity": 0.10, "ke_override_used": True}

    out = build_bank_valuation_methods(calc, _R(), p2={"engine": "ddm"})
    assert out["available"], out.get("notes")

    ri = out["methods"]["residual_income"]["iv"]
    jpb_steady = out["methods"]["justified_pb"]["iv_steady_state"]
    assert ri > jpb_steady > 118.0, (ri, jpb_steady)          # RI > steady > DDM
    assert out["reconciliation"]["low_payout_understatement"] is True
    assert out["convergence"]["near_term_excess_growth"] is True   # g > Ke
    # Gordon single-stage must be flagged undefined for JPM (g > Ke).
    assert out["methods"]["gordon_ddm"]["valid"] is False
    # BVPS derived, not hand-fed.
    assert out["inputs"]["bvps0"] == pytest.approx(130.31, abs=2.0)
    assert "derived" in out["basis"]


# ─────────────────── T4: isolation (non-financials get nothing) ────────────

def test_t4_non_financial_returns_unavailable():
    class _Cls:
        business_model = "fcff_compatible"
        ticker = "ADBE"
        sector = "Technology"

    class _Calc:
        classification = _Cls()
        df = None

    out = build_bank_valuation_methods(_Calc(), None, None)
    assert out["available"] is False
    assert "four-method" in out["notes"]   # points FCFF names back to the right set
