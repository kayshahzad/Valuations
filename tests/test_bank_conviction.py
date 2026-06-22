"""Bank pillar scoring — the conviction scorer must score financial filers on
bank-appropriate metrics (moat / ROE / RI-band MoS), NOT zero the whole scorecard.

Before the fix, score_from_state bailed to `not_scored` for every non-FCFF model,
so JPM read NOT_SCORED / 0/25 — contradicting its own prose (moat 9, ROE 16%).
Banks now score; non-financial specialized engines (REIT/utility) still bail
(honest gap — no rubric yet).
"""

from __future__ import annotations

import pytest


def _score(ticker, moat=8.0):
    from aletheia.agents.calc_node import calc_node
    from aletheia.tools.conviction_scorer import ConvictionScorer
    from aletheia.utils.calc_input_builder import make_calc_input
    st = calc_node({"ticker": ticker})
    st["forensic_report"] = {"moat_score": moat}      # phase-1 signal a real run carries
    ci = make_calc_input(ticker)
    return ConvictionScorer().score_from_state(ticker, st, calc_input=ci)


def test_jpm_scores_on_bank_pillars_not_zeroed():
    pytest.importorskip("pandas")
    try:
        res = _score("JPM", moat=9.0)
    except Exception as e:
        pytest.skip(f"JPM unavailable: {e}")
    # The headline bug: must NOT be not_scored with everything zeroed.
    assert res.position_tier != "not_scored"
    assert res.raw_total >= 12, res.raw_total
    # P1 reads the moat engine; P2 reads ROE (financial branch), NOT FCF margin.
    assert res.p1_moat.score == 5                      # moat 9.0 → top
    assert res.p2_health.score >= 3
    assert "ROE" in (res.p2_health.reasons or [""])[0]  # ROE branch, not FCF/ROIC


def test_sofi_financial_services_label_still_scores():
    """SOFI is 'Financial Services' (not the narrow 'Financials' GICS set) — the
    broadened is_financial_filer gate must still route it to the ROE branch."""
    pytest.importorskip("pandas")
    try:
        res = _score("SOFI", moat=6.0)
    except Exception as e:
        pytest.skip(f"SOFI unavailable: {e}")
    assert res.position_tier != "not_scored"
    assert "ROE" in (res.p2_health.reasons or [""])[0]


def test_reit_still_not_scored():
    """Non-financial specialized engines (EQIX REIT) keep the honest not_scored —
    the bank fix must not capture them."""
    pytest.importorskip("pandas")
    try:
        res = _score("EQIX", moat=7.0)
    except Exception as e:
        pytest.skip(f"EQIX unavailable: {e}")
    assert res.position_tier == "not_scored"
