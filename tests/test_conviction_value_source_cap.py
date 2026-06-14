"""Build 5 — value-source conviction gate as an additive, most-restrictive cap.

Verifies the gate ONLY ever lowers a tier (decision #1) and that when several
caps fire, the minimum (most restrictive) wins (R13). Hermetic — drives the
deterministic scorer with synthetic decomposition payloads in state.
"""

import pytest

from aletheia.tools.conviction_scorer import ConvictionScorer


@pytest.fixture(scope="module")
def calc_input():
    """A real calc_input supplies valid lifecycle_thresholds (DB-only,
    deterministic). We control the cap via the injected decomposition, not the
    pillar inputs, so the choice of ticker doesn't affect what we assert."""
    from dotenv import load_dotenv
    load_dotenv()
    from aletheia.utils.calc_input_builder import make_calc_input
    return make_calc_input("AAPL")


def _score(calc_input, vsd=None, strong=True):
    """Run _compute, optionally injecting a value-source decomposition."""
    scorer = ConvictionScorer()
    state = {"phase2_valuation": {"value_source_decomposition": vsd}} if vsd else None
    if strong:
        kw = dict(moat_score=9.5, roic=0.35, wacc=0.09, fcf_margin=0.30,
                  net_debt_bn=0.0, ebitda_bn=10.0, rev_cagr=0.15, hist_cagr=0.15,
                  sector="Technology", base_mos=0.35, op_leverage=5)
    else:
        kw = dict(moat_score=3.0, roic=0.04, wacc=0.10, fcf_margin=-0.05,
                  net_debt_bn=20.0, ebitda_bn=5.0, rev_cagr=-0.02, hist_cagr=-0.02,
                  sector="Tobacco", base_mos=-0.40, op_leverage=1)
    return scorer._compute(
        ticker="TEST", data_quality=None, cyclicality_z=0.0, is_peak=False,
        sbc_pct_fcf=2.0, upstream_leak=None, strategic_lev=None,
        multiple_premium=None, implied_cagr=None, calc_input=calc_input,
        roe=None, state=state, **kw,
    )


def test_no_decomposition_is_noop(calc_input):
    """Absent a decomposition, the gate must not touch the score."""
    r = _score(calc_input, None)
    assert r.value_source_cap is False
    assert r.value_source_tier_ceiling is None


def test_operating_dominant_not_capped(calc_input):
    """op ≥60% & mult ≤25% → no value-source cap (ADBE-like)."""
    base = _score(calc_input, None).capped_total
    r = _score(calc_input, {"available": True, "operating_share": 0.75,
                            "multiple_share": 0.08, "gov_modifier": 1})
    assert r.value_source_cap is False
    assert r.capped_total == base          # unchanged


def test_multiple_dominant_capped_to_pass(calc_input):
    """mult >40% → PASS ceiling (≤14), and only ever lowers."""
    r = _score(calc_input, {"available": True, "operating_share": 0.40,
                            "multiple_share": 0.45, "gov_modifier": 0})
    assert r.value_source_cap is True
    assert r.capped_total <= 14
    assert r.position_tier == "pass"


def test_gate_never_raises(calc_input):
    """A weak (low-score) name is not promoted by a favorable decomposition."""
    r = _score(calc_input, {"available": True, "operating_share": 0.95,
                            "multiple_share": 0.02, "gov_modifier": 1},
               strong=False)
    assert r.value_source_cap is False
    assert r.position_tier in ("pass", "monitor")


def test_most_restrictive_cap_wins_R13(calc_input):
    """Share rule says PASS (14); narrative override says MONITOR (19).
    final = min → PASS. An override must never raise a ceiling."""
    r = _score(calc_input, {"available": True, "operating_share": 0.40,
                            "multiple_share": 0.45,        # → share ceiling 14 (PASS)
                            "gov_modifier": 0,
                            "contrarian_bias": "fomo"})    # → override ceiling 19
    assert r.capped_total <= 14
    assert r.position_tier == "pass"


def test_governance_downgrade_one_tier(calc_input):
    """gov −1 downgrades one tier below the share allowance."""
    r = _score(calc_input, {"available": True, "operating_share": 0.70,
                            "multiple_share": 0.15, "gov_modifier": -1})
    assert r.value_source_cap is True
    assert r.capped_total <= 19
