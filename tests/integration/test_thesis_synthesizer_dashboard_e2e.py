"""End-to-end test: thesis_synthesizer + dashboard wiring across coverage states.

Bypasses the LLM by patching the chain to return pre-built ThesisSynthesis
candidates. Verifies the full integration path:

  - dashboard projection enters the agent via state
  - per-call schema validator accepts citable paths and rejects unassessed
  - confidence-floor clamp engages per coverage bucket
  - _metadata stamp records catalog hash + coverage receipts
  - quality flags surface (vocabulary, coverage_zero, clamp)

No DB I/O — coverage states are seeded directly into
`state["qualitative_dashboard"]` matching the shape the real
dashboard_fetch_node emits.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

import aletheia.agents.thesis_synthesizer as ts_mod


# ── Helpers ──────────────────────────────────────────────────────────────

def _seeded_dashboard(coverage_state: str,
                      citable_dim_paths: List[str] = None,
                      citable_composite_paths: List[str] = None,
                      stale_paths: List[str] = None,
                      n_assessed: int = None,
                      available: bool = True) -> Dict[str, Any]:
    """Build a minimal dashboard projection matching `dashboard_fetch_node`'s
    output shape. Only the fields the synthesizer reads need to be set."""
    citable_dim_paths = citable_dim_paths or []
    citable_composite_paths = citable_composite_paths or []
    stale_paths = stale_paths or []

    if n_assessed is None:
        n_assessed = {"zero": 0, "low": 4, "medium": 8, "high": 14}.get(coverage_state, 0)

    return {
        "ticker":     "TEST",
        "available":  available,
        "coverage": {
            "n_assessable":   16,
            "n_assessed":     n_assessed,
            "n_stale":        len(stale_paths),
            "n_pending":      3,
            "n_not_assessed": 16 - n_assessed,
            "coverage_state": coverage_state,
            "stale_paths":    stale_paths,
            "citable_dim_paths": citable_dim_paths,
        },
        "dimensions": {},
        "categories": {},
        "citable_dim_paths":       citable_dim_paths,
        "citable_composite_paths": citable_composite_paths,
        "stale_paths":             stale_paths,
    }


def _baseline_state(qd: Dict[str, Any]) -> Dict[str, Any]:
    """Minimal state that the synthesizer's projectors can survive on."""
    return {
        "ticker": "TEST",
        "messages": [],
        "phase2_valuation": {
            "wacc": 0.085,
            "implied_cagr": 0.12,
            "historical_cagr": 0.08,
            "reverse_dcf_signal": "fair_value",
            "three_scenario_dcf": {
                "base": {"intrinsic_per_share": 100.0, "margin_of_safety": 0.05},
                "bear": {"intrinsic_per_share": 70.0, "margin_of_safety": -0.30},
                "bull": {"intrinsic_per_share": 130.0, "margin_of_safety": 0.30},
            },
            "multiple_decomposition": {
                "premium_pct": 0.10, "signal": "fair", "value_creation": "yes",
            },
        },
        "cyclicality": {"z_score": 0.5, "is_peak": False},
        "forensic_report": {"moat_score": 7, "has_pricing_power": True,
                            "concentration_risk": False},
        "value_chain_report": {"upstream_value_leak": False,
                               "strategic_position": "strong",
                               "substitution_pressure": "low"},
        "strategic_context_report": {"cyclicality_classification": "moderate",
                                     "growth_quality": "high",
                                     "intangible_decay_severity": "low",
                                     "terminal_haircut": False},
        "contrarian_report": {"structured_analysis": {
            "bias_detected": "extrapolation",
            "sentiment_score": 2,
            "bear_case_summary": "Mock bear case for testing.",
        }},
        "scenario_results": [
            {"name": "Test scenario", "scenario_type": "bull",
             "proposed_by": "test", "rationale": "rationale",
             "intrinsic_per_share_base": 110.0, "upside_pct_base": 10.0},
        ],
        "conviction": {"position_tier": "starter", "position_size_pct": 5,
                       "conviction_score": 3, "capped_total": 12},
        "qualitative_dashboard": qd,
    }


def _candidate_payload(thesis_confidence: str = "high",
                       cited_signals_per_case: List[List[str]] = None) -> Dict[str, Any]:
    """Build a valid ThesisSynthesis payload using statically-citable paths
    by default. Override `cited_signals_per_case` to test per-call validator
    behaviour."""
    if cited_signals_per_case is None:
        cited_signals_per_case = [
            ["phase2.three_scenario_dcf.bull"],
            ["contrarian.bias_detected", "phase2.implied_cagr"],
            ["phase2.three_scenario_dcf.base.intrinsic_per_share"],
        ]
    bull_cs, bear_cs, base_cs = cited_signals_per_case
    return {
        "thesis_statement":
            "Test thesis weaving phase2 cyclicality and contrarian signals.",
        "bull_case": {
            "claim": "Bull case anchored on the bull scenario per phase2 outputs.",
            "cited_signals": bull_cs,
        },
        "bear_case": {
            "claim": "Bear case driven by contrarian bias and phase2.implied_cagr.",
            "cited_signals": bear_cs,
        },
        "base_case": {
            "claim": "Base case rests on phase2.three_scenario_dcf.base.",
            "cited_signals": base_cs,
        },
        "decision_conditions": [
            {"trigger": "implied CAGR > 25%",
             "observable": "phase2.implied_cagr > 0.25",
             "action": "trim", "priority": "amber"},
            {"trigger": "MoS turns negative",
             "observable": "phase2.three_scenario_dcf.base.margin_of_safety < 0",
             "action": "exit", "priority": "red"},
            {"trigger": "Reverse-DCF flips to caution",
             "observable": "reverse_dcf.signal == 'caution'",
             "action": "hold", "priority": "green"},
        ],
        "thesis_confidence": thesis_confidence,
        "time_horizon": "1_year",
        "position_sizing_implications":
            "Cite conviction.position_tier; size as starter pending coverage.",
        "required_analyst_judgment": ["Strategic value not in calc layer."],
        "update_conditions": ["Reverse-DCF signal flips from caution to flag."],
    }


class _FakeChain:
    """Stub LangChain chain whose .invoke() returns a pre-built model
    instance. The agent code does `chain = _PROMPT | structured_llm;
    candidate = chain.invoke(invoke_args)` — we replace `chain` entirely."""

    def __init__(self, model_class, payload):
        self._cls = model_class
        self._payload = payload

    def invoke(self, _args):
        return self._cls(**self._payload)


def _patched_with_structured_output(model_class, payload):
    """Build a stand-in for `LLM.with_structured_output(cls)` that captures
    the model class and returns a chain whose invoke instantiates it from
    `payload`."""
    fake_chain = _FakeChain(model_class, payload)
    fake_llm = type("FakeLLM", (), {})()
    # `_PROMPT | structured_llm` invokes ChatPromptTemplate.__or__; the
    # right-hand side just needs an .invoke that takes the prompt result.
    # Simpler: replace the whole chain creation by monkey-patching
    # `make_thesis_synthesis_class` so we control the class, AND patch
    # ChatGoogleGenerativeAI to return a stub.
    return fake_chain


@pytest.fixture
def fake_google_api_key(monkeypatch):
    """The agent gates on GOOGLE_API_KEY being set — it returns a mock
    if absent. Set a dummy value so the live code path runs (we patch
    the LLM call below)."""
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy-test-key")


def _patch_chain(monkeypatch, payload):
    """Replace the chain construction so chain.invoke returns a model
    instance built from `payload`. The model class is whatever
    `make_thesis_synthesis_class` returns for that call."""
    real_factory = ts_mod.make_thesis_synthesis_class
    captured = {}

    def wrapped_factory(citable_dashboard_paths, stale_dashboard_paths):
        cls = real_factory(citable_dashboard_paths, stale_dashboard_paths)
        captured["cls"] = cls
        return cls

    monkeypatch.setattr(ts_mod, "make_thesis_synthesis_class", wrapped_factory)

    class FakeStructuredLLM:
        def __or__(self, _other):
            return self
        def invoke(self, _args):
            return captured["cls"](**payload)

    class FakeLLM:
        def __init__(self, *args, **kwargs):
            pass
        def with_structured_output(self, _cls):
            return FakeStructuredLLM()

    monkeypatch.setattr(ts_mod, "ChatGoogleGenerativeAI", FakeLLM)

    # Replace the prompt's __or__ behaviour to short-circuit to our chain
    class FakePrompt:
        def __or__(self, structured_llm):
            return structured_llm
        @staticmethod
        def from_template(_):
            return FakePrompt()
    monkeypatch.setattr(ts_mod, "_PROMPT", FakePrompt())
    monkeypatch.setattr(ts_mod, "ChatPromptTemplate", FakePrompt)


# ── Coverage-state matrix ────────────────────────────────────────────────

def test_high_coverage_no_clamp(monkeypatch, fake_google_api_key):
    qd = _seeded_dashboard("high", n_assessed=14)
    state = _baseline_state(qd)
    _patch_chain(monkeypatch, _candidate_payload(thesis_confidence="high"))
    out = ts_mod.thesis_synthesizer_agent(state)
    syn = out["thesis_synthesis"]
    assert syn["thesis_confidence"] == "high"
    assert syn["_metadata"]["coverage_state"] == "high"
    assert syn["_metadata"]["n_assessed"] == 14
    flags = syn.get("_quality_flags") or []
    # No clamp flag, no zero-coverage flag at high coverage
    assert not any("clamped" in f for f in flags)
    assert "coverage_zero_calc_only_thesis" not in flags


def test_medium_coverage_clamps_high_to_medium(monkeypatch, fake_google_api_key):
    qd = _seeded_dashboard("medium", n_assessed=8)
    state = _baseline_state(qd)
    _patch_chain(monkeypatch, _candidate_payload(thesis_confidence="high"))
    out = ts_mod.thesis_synthesizer_agent(state)
    syn = out["thesis_synthesis"]
    assert syn["thesis_confidence"] == "medium", \
        "LLM emitted 'high' on medium coverage — must clamp down"
    flags = syn.get("_quality_flags") or []
    assert any("clamped_to_medium" in f for f in flags)
    assert syn["_metadata"]["coverage_state"] == "medium"


def test_low_coverage_clamps_high_to_low(monkeypatch, fake_google_api_key):
    qd = _seeded_dashboard("low", n_assessed=4)
    state = _baseline_state(qd)
    _patch_chain(monkeypatch, _candidate_payload(thesis_confidence="high"))
    out = ts_mod.thesis_synthesizer_agent(state)
    syn = out["thesis_synthesis"]
    assert syn["thesis_confidence"] == "low"
    flags = syn.get("_quality_flags") or []
    assert any("clamped_to_low" in f for f in flags)


def test_zero_coverage_forces_insufficient_signal(monkeypatch, fake_google_api_key):
    qd = _seeded_dashboard("zero", n_assessed=0)
    state = _baseline_state(qd)
    _patch_chain(monkeypatch, _candidate_payload(thesis_confidence="high"))
    out = ts_mod.thesis_synthesizer_agent(state)
    syn = out["thesis_synthesis"]
    assert syn["thesis_confidence"] == "insufficient_signal"
    flags = syn.get("_quality_flags") or []
    assert "coverage_zero_calc_only_thesis" in flags
    assert any("clamped_to_insufficient_signal" in f for f in flags)


def test_low_coverage_does_not_raise_when_llm_picks_low(monkeypatch, fake_google_api_key):
    """LLM choosing lower than ceiling must be preserved (floor-only)."""
    qd = _seeded_dashboard("medium", n_assessed=8)
    state = _baseline_state(qd)
    _patch_chain(monkeypatch, _candidate_payload(thesis_confidence="low"))
    out = ts_mod.thesis_synthesizer_agent(state)
    syn = out["thesis_synthesis"]
    assert syn["thesis_confidence"] == "low"
    flags = syn.get("_quality_flags") or []
    assert not any("clamped" in f for f in flags)


# ── Citation-path enforcement (per-call schema) ──────────────────────────

def test_dashboard_dim_citation_accepted_when_assessed(monkeypatch, fake_google_api_key):
    qd = _seeded_dashboard("low", n_assessed=4,
                           citable_dim_paths=["qualitative.moat_strength"])
    state = _baseline_state(qd)
    payload = _candidate_payload(thesis_confidence="low",
                                 cited_signals_per_case=[
                                     ["qualitative.moat_strength"],
                                     ["contrarian.bias_detected"],
                                     ["phase2.three_scenario_dcf.base.intrinsic_per_share"],
                                 ])
    _patch_chain(monkeypatch, payload)
    out = ts_mod.thesis_synthesizer_agent(state)
    syn = out["thesis_synthesis"]
    # Dashboard citation made it through validation
    assert "qualitative.moat_strength" in syn["bull_case"]["cited_signals"]
    assert "qualitative.moat_strength" in syn["_metadata"]["cited_paths_resolved"]


def test_unassessed_dim_citation_falls_back_to_mock(monkeypatch, fake_google_api_key):
    """Citing an unassessed dim raises ValidationError on both retries —
    agent falls back to mock emission."""
    qd = _seeded_dashboard("low", n_assessed=4,
                           citable_dim_paths=["qualitative.moat_strength"])
    state = _baseline_state(qd)
    # Cite an UNASSESSED dim — not in citable_dim_paths
    payload = _candidate_payload(cited_signals_per_case=[
        ["qualitative.brand_strength"],   # not in citable set
        ["contrarian.bias_detected"],
        ["phase2.three_scenario_dcf.base.intrinsic_per_share"],
    ])
    _patch_chain(monkeypatch, payload)
    out = ts_mod.thesis_synthesizer_agent(state)
    syn = out["thesis_synthesis"]
    # Mock fallback should have triggered
    assert syn.get("_quality_flags") == ["mock_fallback"] \
        or "mock" in syn["thesis_statement"].lower()


def test_stale_citation_records_in_metadata(monkeypatch, fake_google_api_key):
    qd = _seeded_dashboard("low", n_assessed=4,
                           citable_dim_paths=["qualitative.cyclicality"],
                           stale_paths=["qualitative.cyclicality"])
    state = _baseline_state(qd)
    payload = _candidate_payload(thesis_confidence="low",
                                 cited_signals_per_case=[
                                     ["phase2.three_scenario_dcf.bull"],
                                     ["qualitative.cyclicality"],
                                     ["phase2.three_scenario_dcf.base.intrinsic_per_share"],
                                 ])
    _patch_chain(monkeypatch, payload)
    out = ts_mod.thesis_synthesizer_agent(state)
    syn = out["thesis_synthesis"]
    assert "qualitative.cyclicality" in syn["_metadata"]["stale_citations"]
    assert "qualitative.cyclicality" in syn["_metadata"]["stale_paths"]


# ── Metadata stamp ───────────────────────────────────────────────────────

def test_metadata_stamps_catalog_hash(monkeypatch, fake_google_api_key):
    qd = _seeded_dashboard("high", n_assessed=14)
    state = _baseline_state(qd)
    _patch_chain(monkeypatch, _candidate_payload(thesis_confidence="high"))
    out = ts_mod.thesis_synthesizer_agent(state)
    md = out["thesis_synthesis"]["_metadata"]
    # 16-char hex string
    assert isinstance(md["dashboard_catalog_hash"], str)
    assert len(md["dashboard_catalog_hash"]) == 16
    assert md["per_dim_catalog_hashes"]   # non-empty when catalog loaded
    assert md["dashboard_available"] is True


def test_metadata_when_dashboard_unavailable(monkeypatch, fake_google_api_key):
    """If dashboard_fetch_node failed, projection.available=False; the
    synthesizer still runs and stamps metadata.dashboard_available=False."""
    qd = _seeded_dashboard("zero", n_assessed=0, available=False)
    state = _baseline_state(qd)
    _patch_chain(monkeypatch, _candidate_payload(thesis_confidence="high"))
    out = ts_mod.thesis_synthesizer_agent(state)
    syn = out["thesis_synthesis"]
    assert syn["_metadata"]["dashboard_available"] is False
    # Coverage-zero floor still engages
    assert syn["thesis_confidence"] == "insufficient_signal"
