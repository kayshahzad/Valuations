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
    fcfe_bank_value,
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


def test_t1c_fcfe_telescopes_to_justified_pb():
    # FCFE(bank) = NI − ΔRegCap. Under consistent growth (asset_growth = the
    # retention-implied g = ROE·retention), per-share FCFEₜ = BVPSₜ₋₁·(ROE−g),
    # so the two-stage FCFE must equal justified-P/B·BVPS = BVPS·(ROE−g)/(Ke−g).
    # This is the 4th leg of the golden identity: RI ≡ jPB ≡ Gordon ≡ FCFE.
    roe, ke, payout, bvps = 0.13, 0.10, 0.55, 60.0
    g = roe * (1 - payout)
    closed = bvps * (roe - g) / (ke - g)
    fcfe = fcfe_bank_value(
        bvps0=bvps, roe=roe, ke=ke, asset_growth=g,
        explicit_years=80, terminal_growth=g)
    assert fcfe["iv"] == pytest.approx(closed, rel=1e-4)
    assert fcfe["equity_reinvestment_rate"] == pytest.approx(g / roe, rel=1e-9)


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


# ─────── T3b: Deutsche Bank — real low-ROE (ROE<Ke) convergence fixture ─────

def test_t3b_deutsche_bank_low_roe_below_book_convergence():
    """Deutsche Bank (FY2025, EUR) is the LOW-ROE contrast pole to JPM: ROE 8.8%
    < Ke 10%, so the bank doesn't earn its cost of capital and the convergent set
    must price equity BELOW book (justified P/B < 1) — without forcing P/B=1.

    Real inputs (SEC XBRL ifrs-full CIK0001159508 ≡ FMP, both EUR): common equity
    €78.641B, net income €6.931B, 1.9545B shares, total assets €1.391T→€1.440T.
    The convergent set is FX-agnostic (ROE/payout/Ke unitless), so the golden
    identity holds in EUR; this is a clean steady-state case (ROE<Ke ⇒ single-stage
    well-posed, no super-growth), so RI ≡ jPB ≡ Gordon converge EXACTLY, and the
    FCFE leg at the real 3.5% asset growth lands a tight 4th value."""
    bvps0 = 78.641e9 / 1.9545e9          # €40.24
    roe = 6.931e9 / 78.641e9             # 8.81%
    ke, payout = 0.10, 0.50
    asset_growth = 1440e9 / 1391e9 - 1.0  # 3.52%

    conv = value_bank_steady_state(bvps0=bvps0, roe=roe, payout=payout, ke=ke)
    assert conv.converged and conv.max_spread_pct < 0.005
    assert conv.iv_justified_pb / bvps0 < 1.0          # BELOW book (ROE < Ke)
    assert conv.iv_residual_income < bvps0
    assert conv.iv_justified_pb == pytest.approx(31.70, abs=0.5)

    fcfe = fcfe_bank_value(bvps0=bvps0, roe=roe, ke=ke, asset_growth=asset_growth,
                           explicit_years=5, terminal_growth=0.02)
    assert fcfe["implied_pb"] < 1.0                    # 4th leg also below book
    # all four legs within a sane band — the real-bank 4-way spread
    legs = [conv.iv_residual_income, conv.iv_justified_pb, conv.iv_gordon_ddm, fcfe["iv"]]
    assert (max(legs) / min(legs) - 1.0) < 0.15


# ───── T3c: SOFI — RI-headline bank gets the convergent set, consistently ───

def test_t3c_sofi_residual_income_bank_in_convergent_set():
    """SOFI is a residual_income_required filer that OWNS SoFi Bank N.A. — the
    convergent set must apply to it (gated on bank reality, not the model), and
    its RI leg must MATCH the routed headline (same engine, same inputs), not a
    differently-recomputed number. Edge cases: no dividend → Gordon undefined;
    asset growth outpaces ROE → capital-deficit flag."""
    pytest.importorskip("pandas")
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.valuation_router import ValuationRouter
    try:
        calc = make_calc_input("SOFI")
        v = ValuationRouter().execute(calc)
    except Exception as e:
        pytest.skip(f"SOFI not available: {e}")
    if getattr(v, "engine", "") != "residual_income":
        pytest.skip("SOFI not routed to residual_income")

    out = build_bank_valuation_methods(calc, v, p2={"engine": "residual_income"})
    assert out["available"], out.get("notes")
    # RI leg reproduces the headline (consistency, not a second number).
    assert out["methods"]["residual_income"]["iv"] == pytest.approx(
        float(v.intrinsic_per_share), rel=1e-3)
    assert out["inputs"]["payout_source"] == "routed RI engine snapshot"
    # No dividend → Gordon undefined, not $0.
    assert out["methods"]["gordon_ddm"]["valid"] is False
    assert "no dividend" in out["methods"]["gordon_ddm"]["note"]
    # Non-payer must NOT be mislabelled super-growth: SOFI's g (ROE·1.0 ≈ 8%) is
    # BELOW Ke (~10.5%), so near_term_excess_growth must be False — the "g≥Ke"
    # reason is distinct from the no-dividend reason (regression: they were conflated).
    assert out["convergence"]["near_term_excess_growth"] is False
    # SOFI grows assets far faster than it earns → capital-deficit signal.
    assert out["reconciliation"]["capital_deficit"] is True
    assert out["methods"]["fcfe_bank"]["capital_deficit"] is True


def test_t3c_cnc_healthcare_ri_excluded_from_bank_set():
    """CNC is ALSO residual_income_required but a Healthcare filer — it must NOT
    get the bank convergent set (gate is bank reality, not the model)."""
    pytest.importorskip("pandas")
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.valuation_router import ValuationRouter
    try:
        calc = make_calc_input("CNC")
        v = ValuationRouter().execute(calc)
    except Exception as e:
        pytest.skip(f"CNC not available: {e}")
    out = build_bank_valuation_methods(calc, v)
    assert out["available"] is False


# ───── T3d: new banks auto-route to the bank model (classifier → set) ───────

def test_t3d_new_banks_classify_into_the_bank_set():
    """A bank added via the runtime profile classifier must land on a bank model
    (residual_income_required) and hit the convergent set — NOT routing_required
    (the utility RateBaseEngine), the SOFI mis-route bug generalized."""
    from config.ticker_classification import classify_from_profile
    from aletheia.calculations.sector_classification import is_bank_for_display
    bank_cases = [
        ("Financial Services", "Banks - Diversified", "bank holding, deposits and loans"),
        ("Financial Services", "Capital Markets", "investment bank and broker-dealer"),
        ("Financial Services", "Insurance - Diversified", "property-casualty insurer"),
        ("Financial Services", "Credit Services", "consumer lending and card issuer"),
    ]
    for sector, industry, desc in bank_cases:
        c = classify_from_profile({"sector": sector, "industry": industry,
                                   "country": "US", "description": desc})
        assert c["business_model"] == "residual_income_required", (industry, c)
        assert is_bank_for_display(sector, c["business_model"]) is True, industry


def test_t3d_non_banks_unaffected_by_the_routing_fix():
    """Utilities still route to the rate-base engine; pure payment networks and
    industrials stay FCFF — the bank fix must not capture them."""
    from config.ticker_classification import classify_from_profile
    assert classify_from_profile({"sector": "Utilities",
        "industry": "Utilities - Regulated Electric", "country": "US",
        "description": "regulated electric utility"})["business_model"] == "routing_required"
    # pure payment network (no lending signals) stays FCFF
    assert classify_from_profile({"sector": "Financial Services",
        "industry": "Credit Services", "country": "US",
        "description": "payments technology network, transaction processing"})[
        "business_model"] == "fcff_compatible"
    assert classify_from_profile({"sector": "Technology",
        "industry": "Consumer Electronics", "country": "US",
        "description": "designs devices"})["business_model"] == "fcff_compatible"


# ───── T3e: method-appropriate headline (CF-R19, DDM understatement swap) ───

def test_t3e_headline_override_swaps_ddm_for_residual_income():
    """A low-payout bank flagged with the DDM understatement must headline the
    residual-income fair value (so MoS reads roughly fair, not −60%), with the DDM
    preserved as displaced_ddm. Banks not understated / non-banks → None."""
    from aletheia.tools.bank_valuation_methods import bank_headline_override
    # Understated bank: DDM $118 « RI $315, price $329.
    bvm = {
        "available": True,
        "methods": {"residual_income": {"iv": 315.0}},
        "reconciliation": {"low_payout_understatement": True,
                           "fair_value_band": [267.0, 315.0]},
    }
    out = bank_headline_override(ddm_ips=118.0, price=329.0, bvm=bvm)
    assert out is not None
    assert out["intrinsic_per_share"] == pytest.approx(315.0)
    assert out["displaced_ddm"] == pytest.approx(118.0)
    assert out["margin_of_safety"] == pytest.approx(315.0 / 329.0 - 1.0)  # ≈ −4%, fair
    assert out["method"] == "residual_income"
    # Not understated → no override (caller keeps the engine IV).
    bvm2 = {"available": True, "methods": {"residual_income": {"iv": 315.0}},
            "reconciliation": {"low_payout_understatement": False}}
    assert bank_headline_override(ddm_ips=300.0, price=329.0, bvm=bvm2) is None
    # No convergent set (non-bank) → no override.
    assert bank_headline_override(ddm_ips=100.0, price=120.0, bvm=None) is None


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


def test_bank_scenario_band_real_bear_not_zero():
    """Bank bear/bull flex normalized ROE (cyclical reversion) — a real RI downside
    ordered bear < base < bull, NOT the fake $0.00 'structural error' bear."""
    from aletheia.tools.bank_valuation_methods import bank_scenario_band
    bvm = {
        "available": True,
        "methods": {"residual_income": {"iv": 315.0}},
        "inputs": {"bvps0": 130.0, "roe_normalized": 0.163, "ke": 0.10,
                   "retention": 0.71, "explicit_years": 10, "terminal_growth": 0.04},
    }
    band = bank_scenario_band(bvm=bvm, price=330.0)
    assert band is not None
    bear = band["bear"]["intrinsic_per_share"]
    bull = band["bull"]["intrinsic_per_share"]
    assert bear > 0                                  # not the fake $0.00 bear
    assert bear < 315.0 < bull                       # coherent with the RI base
    assert band["bear_roe"] < 0.163 < band["bull_roe"]
    # non-bank / unavailable → None
    assert bank_scenario_band(bvm=None, price=330.0) is None


def test_bank_ke_band_floor_cap_and_three_points():
    """CF-R28 Ke band: floor=sector β, cap=1.75×sector β; three Ke points; the cap
    binds on a meme beta, the floor binds on a too-low beta, neither in between."""
    from aletheia.tools.bank_valuation_methods import bank_ke_band
    rf, erp, sec = 0.045, 0.0423, 0.85
    # meme beta → cap binds
    hi = bank_ke_band(rf=rf, erp=erp, raw_beta=2.10, sector_beta=sec, operative_ke=0.105)
    assert hi["cap_binds"] and not hi["floor_binds"]
    assert hi["beta_floored"] == pytest.approx(1.75 * sec)          # capped
    assert hi["ke_sector"] < hi["ke_floored"] < hi["ke_raw"]        # 3-point spread ordered
    # too-low beta → floor binds (raised to sector)
    lo = bank_ke_band(rf=rf, erp=erp, raw_beta=0.62, sector_beta=sec)
    assert lo["floor_binds"] and lo["beta_floored"] == pytest.approx(sec)
    assert lo["ke_floored"] == pytest.approx(lo["ke_sector"])       # floored == sector
    # in-band beta → neither binds, floored == raw
    mid = bank_ke_band(rf=rf, erp=erp, raw_beta=1.23, sector_beta=sec)
    assert not mid["cap_binds"] and not mid["floor_binds"]
    assert mid["ke_floored"] == pytest.approx(mid["ke_raw"])


def test_bank_sector_beta_maps_industry_to_damodaran():
    from aletheia.tools.bank_valuation_methods import bank_sector_beta
    b_jpm, n_jpm = bank_sector_beta("Financials", "Banks")
    assert n_jpm == "Bank (Money Center)" and b_jpm == pytest.approx(0.76)
    b_sofi, n_sofi = bank_sector_beta("Financial Services", "Financial - Credit Services")
    assert "Non-bank" in n_sofi and b_sofi == pytest.approx(0.85)
