"""Phase 0 kernel — discount-rate families, cash-flow identities, stability flag.

The golden tier (T1) proves the Family-A math hits the Bible-I appendix value AND
that the three EV methods converge; T1c is the non-negotiable anti-test that a
WRONG family is detectable (different EV), not silently convergent.
"""

import pytest

from aletheia.tools.discount_rates import resolve_family, pv_tax_shield
from aletheia.tools.cashflow_series import cashflows


# ── T1 — golden Family A arithmetic ──────────────────────────────────────────
def _golden_A():
    return resolve_family("A", r_D=0.10, D=0.40, E=0.60, tau=0.40,
                          beta_A=1.0, rf=0.10, mrp=0.08)


def test_t1_family_a_rates():
    f = _golden_A()
    assert abs(f.r_A - 0.18) < 1e-9
    assert abs(f.beta_E - (1.0 / 0.6)) < 1e-9          # exact 5/3, not rounded 1.67
    assert abs(f.r_E - (0.10 + (1.0 / 0.6) * 0.08)) < 1e-9   # 0.23333…
    assert abs(f.wacc - 0.164) < 1e-4
    assert f.r_TS == f.r_A                              # Family A: r_TS = r_A


def test_t1_three_method_convergence():
    """WACC-FCF == compressed-APV (CCF@r_A) == APV (VU + PV(TS)) == 9868."""
    f = _golden_A()
    FCF, g, V = 1125.0, 0.05, 9868.4
    D = 0.40 * V
    ev_wacc = FCF / (f.wacc - g)
    ev_capv = (FCF + 0.40 * f.r_D * D) / (f.r_A - g)
    ev_apv = FCF / (f.r_A - g) + pv_tax_shield(f, D=D, tau=0.40, g=g)
    for ev in (ev_wacc, ev_capv, ev_apv):
        assert abs(ev - 9868.4) < 2.0


# ── T1b — Family B exercises the (1−τ) form ──────────────────────────────────
def test_t1b_family_b_forms():
    f = resolve_family("B", r_D=0.10, D=0.40, E=0.60, tau=0.40,
                       beta_A=1.0, rf=0.10, mrp=0.08)
    assert f.r_TS == f.r_D                              # Family B: r_TS = r_D
    # WACC = r_A·(1 − τ·D/V) — the (1−τ) appears via the level form
    assert abs(f.wacc - 0.18 * (1 - 0.40 * 0.40)) < 1e-9
    assert f.wacc < _golden_A().wacc                   # B discounts harder here
    # PV(TS) is the level τ·D, growth-independent
    assert abs(pv_tax_shield(f, D=100.0, tau=0.40, g=0.05) - 40.0) < 1e-9


# ── T1c — anti-test: wrong family is DETECTABLE ──────────────────────────────
def test_t1c_wrong_family_diverges_from_truth():
    """Force the constant-D/V golden fixture through Family B → EV materially
    ≠ 9868. This is the only tier that catches 'converged but wrong family'."""
    fa = _golden_A()
    fb = resolve_family("B", r_D=0.10, D=0.40, E=0.60, tau=0.40,
                        beta_A=1.0, rf=0.10, mrp=0.08)
    g = 0.05
    ev_true = 1125.0 / (fa.wacc - g)
    ev_wrong = 1125.0 / (fb.wacc - g)
    assert abs(ev_true - 9868.4) < 2.0
    assert abs(ev_wrong - 9868.4) > 500.0              # misselection is loud, not silent


# ── Kernel no-drift: the two CCF versions reconcile ──────────────────────────
def test_cashflow_ccf_versions_reconcile():
    """When NI = (1−t)(EBIT − Int), the EBIT-CCF and NI-CCF are identical —
    the kernel cannot drift apart at the cash-flow layer."""
    ebit, dep, capx, d_nwc, interest, tax = 1000.0, 100.0, 150.0, 50.0, 80.0, 0.40
    ni = (1 - tax) * (ebit - interest)
    cf = cashflows(ebit=ebit, dep=dep, capx=capx, d_nwc=d_nwc,
                   interest=interest, tax=tax, ni=ni)
    assert abs(cf.ccf_ebit - cf.ccf_ni) < 1e-9
    # FCF identity
    assert abs(cf.fcf - ((1 - tax) * ebit + dep - capx - d_nwc)) < 1e-9
    # ECF = CCF − DebtCashFlow
    assert abs(cf.ecf - (cf.ccf - cf.debt_cash_flow)) < 1e-9


def test_cads_is_cfo_minus_capex():
    cf = cashflows(ebit=1000, dep=100, capx=150, d_nwc=50, interest=80, tax=0.40,
                   cfo=900)
    assert cf.cads == 900 - 150


# ── Flag calibration (provisional, seed labels) ──────────────────────────────
@pytest.fixture(scope="module")
def _calc():
    from dotenv import load_dotenv
    load_dotenv()
    from aletheia.utils.calc_input_builder import make_calc_input
    return make_calc_input


def test_flag_adbe_family_a(_calc):
    """ADBE re-levers off net cash while returning capital → Family A (CF-R10)."""
    from aletheia.tools.capital_structure_flag import build_capital_structure_flag
    f = build_capital_structure_flag(_calc("ADBE"), market_cap=2.0e11)
    assert f["available"]
    assert f["rate_family"] == "A"
    assert "discretionary" in f["klass"]


def test_flag_eqix_classifies_differently(_calc):
    """EQIX (levered REIT, net-issuer, rising net-debt/EBITDA) → not Family A."""
    from aletheia.tools.capital_structure_flag import build_capital_structure_flag
    f = build_capital_structure_flag(_calc("EQIX"), market_cap=9.0e10)
    assert f["available"]
    assert f["rate_family"] == "B"          # different from ADBE → the flag discriminates
