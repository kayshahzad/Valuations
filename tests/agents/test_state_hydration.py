"""Tests for serving-JSON state hydration.

Verifies that hydration produces a state dict the synthesizer's
`_summarize_*` projectors can consume without errors, and that field
shapes (denormalization, reshaping) are correct.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from aletheia.agents.state_hydration import (
    hydrate_state_from_report,
    load_serving_json,
    serving_json_path,
)


# ── Synthetic report fixture ─────────────────────────────────────────────

def _synthetic_report() -> Dict[str, Any]:
    """Minimal report shape mirroring lead.py's serving-JSON output."""
    return {
        "ticker": "TEST",
        "1_economic_reality": {
            "moat": {
                "score": 7.0,
                "has_pricing_power": True,
                "evidence": "switching costs.",
                "pricing_power_evidence": "5% price hikes accepted.",
            },
            "value_chain": {
                "upstream_leak": False,
                "strategic_leverage": 8,
                "substitution_risk_score": 3,
                "power_ratio": 1.5,
                "bottleneck_analysis": "key supplier",
                "top_substitutes": "competitor X",
                "pricing_power_assessment": "strong",
                "pass_through_capability": True,
                "analysis_summary": "robust position",
            },
            "strategic_context": {
                "deferred_revenue_trend": "growing",
                "quality_of_growth_risk": False,
                "intangible_risk_assessment": "low decay",
                "revenue_at_risk_percent": 0.05,
                "terminal_haircut": False,
                "summary": "stable",
            },
            "industry_structure": {
                "cyclicality_z_score": 1.2,
                "is_peak": False,
            },
            "business_model": {
                "operating_leverage_score": 4.5,
                "cost_structure": "asset-light",
            },
        },
        "3_capital_structure_risk": {
            "concentration_risk": False,
        },
        "4_valuation_synthesis": {
            "phase2_valuation": {
                "wacc": 0.08,
                "three_scenario_dcf": {
                    "bear": {"intrinsic_per_share": 50.0, "margin_of_safety": -0.3},
                    "base": {"intrinsic_per_share": 100.0, "margin_of_safety": 0.05},
                    "bull": {"intrinsic_per_share": 150.0, "margin_of_safety": 0.4},
                },
                "reverse_dcf": {
                    "implied_cagr_10y": 0.15,
                    "historical_cagr": 0.10,
                    "signal": "fair_value",
                },
                "multiple_decomposition": {
                    "premium_pct": 0.2, "signal": "fair", "value_creation": "yes",
                },
            },
            "contrarian_analysis": {
                "bias_detected": "extrapolation",
                "sentiment_score": 1,
                "bear_case_summary": "Margin compression risk.",
            },
            "investment_thesis": {
                "pillar_scores": {
                    "position_tier":     "starter",
                    "position_size_pct": 5,
                    "conviction_score":  4,
                    "capped_total":      15,
                },
            },
            "agent_scenarios": [
                {"name": "Test scenario", "scenario_type": "bull",
                 "proposed_by": "test", "rationale": "rationale",
                 "intrinsic_per_share_base": 110.0, "upside_pct_base": 10.0},
            ],
        },
    }


# ── Hydration shape ──────────────────────────────────────────────────────

def test_hydrate_returns_all_state_keys():
    report = _synthetic_report()
    state = hydrate_state_from_report("TEST", report)
    expected_keys = {
        "ticker", "phase2_valuation", "cyclicality",
        "forensic_report", "value_chain_report", "strategic_context_report",
        "contrarian_report", "scenario_results", "conviction",
    }
    assert expected_keys.issubset(state.keys())


def test_hydrate_denormalizes_implied_cagr_from_reverse_dcf():
    report = _synthetic_report()
    state = hydrate_state_from_report("TEST", report)
    assert state["phase2_valuation"]["implied_cagr"] == 0.15
    assert state["phase2_valuation"]["historical_cagr"] == 0.10
    assert state["phase2_valuation"]["reverse_dcf_signal"] == "fair_value"


def test_hydrate_remaps_value_chain_keys():
    """Synthesizer reads upstream_value_leak / strategic_position /
    substitution_pressure; serving JSON has upstream_leak / strategic_leverage
    / substitution_risk_score."""
    report = _synthetic_report()
    state = hydrate_state_from_report("TEST", report)
    vc = state["value_chain_report"]
    assert vc["upstream_value_leak"] is False
    assert vc["strategic_position"] == 8
    assert vc["substitution_pressure"] == 3


def test_hydrate_wraps_contrarian_in_structured_analysis():
    """Synthesizer reads contrarian_report.structured_analysis.X; serving
    JSON has contrarian_analysis flat."""
    report = _synthetic_report()
    state = hydrate_state_from_report("TEST", report)
    sa = state["contrarian_report"]["structured_analysis"]
    assert sa["bias_detected"] == "extrapolation"
    assert sa["sentiment_score"] == 1


def test_hydrate_extracts_cyclicality_from_industry_structure():
    report = _synthetic_report()
    state = hydrate_state_from_report("TEST", report)
    assert state["cyclicality"]["z_score"] == 1.2
    assert state["cyclicality"]["is_peak"] is False


def test_hydrate_pulls_conviction_from_pillar_scores():
    report = _synthetic_report()
    state = hydrate_state_from_report("TEST", report)
    c = state["conviction"]
    assert c["position_tier"] == "starter"
    assert c["conviction_score"] == 4
    assert c["capped_total"] == 15


def test_hydrate_does_not_carry_raw_10k():
    """Partial reruns must NEVER re-fetch the 10-K."""
    report = _synthetic_report()
    state = hydrate_state_from_report("TEST", report)
    assert "raw_10k_text" not in state


def test_hydrate_handles_empty_sections_gracefully():
    """Missing optional sections shouldn't crash the projector."""
    report = {"ticker": "TEST", "4_valuation_synthesis": {}}
    state = hydrate_state_from_report("TEST", report)
    assert state["phase2_valuation"] == {}
    assert state["cyclicality"] == {}
    assert state["scenario_results"] == []


# ── Projector compatibility ──────────────────────────────────────────────

def test_hydrated_state_works_with_synthesizer_projectors():
    """The output of hydrate_state must be consumable by the synthesizer's
    _summarize_* functions without errors."""
    from aletheia.agents.thesis_synthesizer import (
        _summarize_phase2,
        _summarize_qualitative,
        _summarize_contrarian,
        _summarize_scenarios,
        _summarize_conviction,
    )
    report = _synthetic_report()
    state = hydrate_state_from_report("TEST", report)
    p2 = _summarize_phase2(state)
    qual = _summarize_qualitative(state)
    contra = _summarize_contrarian(state)
    scen = _summarize_scenarios(state)
    conv = _summarize_conviction(state)
    # Each must produce non-trivial output, not the "(unavailable)" fallback
    assert "WACC" in p2
    assert "Forensic" in qual or "Value chain" in qual
    assert "bias_detected" in contra
    assert "Test scenario" in scen
    assert "starter" in conv


# ── load_serving_json ────────────────────────────────────────────────────

def test_load_serving_json_returns_none_for_missing_ticker(tmp_path, monkeypatch):
    """Missing serving JSON → None, no exception."""
    import aletheia.agents.state_hydration as sh_mod
    monkeypatch.setattr(sh_mod, "_SERVING_DIR", tmp_path)
    assert sh_mod.load_serving_json("ZZZZZ") is None


def test_serving_json_path_is_uppercase_per_lead_convention():
    p = serving_json_path("nvda")
    assert p.name == "NVDA_report.json"
