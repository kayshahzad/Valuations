"""Vocabulary-pattern + per-call schema factory tests for thesis_synthesizer.

No LLM calls — these tests build the factory class and feed it raw
dicts to confirm the citation-path enforcement works correctly across
the locked rules:

  - assessed dim cited → accepted
  - stale dim cited → accepted, recorded in `_stale_citations`
  - not_assessed dim cited → ValidationError
  - statically-citable upstream path cited → accepted regardless of
    dashboard state
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from config.qualitative_dimensions import DIMENSIONS, CATEGORIES
from aletheia.agents.thesis_synthesizer import (
    _VOCABULARY_PATTERN,
    _aggregate_catalog_hash,
    _build_vocabulary_pattern,
    _is_statically_citable,
    make_thesis_synthesis_class,
)


# ── Vocabulary regex ─────────────────────────────────────────────────────

def test_vocabulary_pattern_matches_legacy_markers():
    text = "phase2 implied CAGR contrarian cyclicality"
    matches = set(m.lower() for m in _VOCABULARY_PATTERN.findall(text))
    # phase2 + cyclicality + contrarian + "implied CAGR"
    assert len(matches) >= 4


def test_vocabulary_pattern_matches_every_dim_id():
    """Every catalog dim_id must be a recognised marker."""
    for dim_id in DIMENSIONS.keys():
        matches = _VOCABULARY_PATTERN.findall(dim_id)
        assert matches, f"dim_id {dim_id!r} not matched by vocab pattern"


def test_vocabulary_pattern_matches_every_composite_marker():
    for cat_id, _ in CATEGORIES:
        marker = f"{cat_id}_composite"
        matches = _VOCABULARY_PATTERN.findall(marker)
        assert matches, f"composite marker {marker!r} not matched"


def test_vocabulary_pattern_rebuilds_from_catalog():
    """Sanity: rebuilding the pattern is equivalent to the cached one."""
    rebuilt = _build_vocabulary_pattern()
    assert rebuilt.pattern == _VOCABULARY_PATTERN.pattern


def test_vocabulary_pattern_falls_back_when_catalog_missing(monkeypatch):
    """If catalog import had failed, the regex still covers the legacy
    upstream markers and stays callable."""
    import aletheia.agents.thesis_synthesizer as ts_mod
    monkeypatch.setattr(ts_mod, "_CATALOG_LOADED", False)
    monkeypatch.setattr(ts_mod, "_DIMENSIONS", {})
    monkeypatch.setattr(ts_mod, "_CATEGORIES", ())

    fallback = ts_mod._build_vocabulary_pattern()
    # Legacy markers still match
    assert fallback.search("contrarian phase2 cyclicality")
    # Catalog-derived dim_id no longer matches
    # (since the dim list was monkey-patched empty)
    assert not fallback.search("moat_strength_composite")


# ── _is_statically_citable ───────────────────────────────────────────────

def test_static_citable_phase2_path():
    assert _is_statically_citable("phase2.implied_cagr")
    assert _is_statically_citable("phase2.three_scenario_dcf.base.intrinsic_per_share")


def test_static_citable_legacy_qualitative_agent_paths():
    """Q1 confirmed: keep both citation namespaces. forensic / value_chain /
    strategic_context cite the qualitative_synthesis LLM agent (different
    provenance from dashboard dim citations)."""
    assert _is_statically_citable("qualitative.forensic.moat.score")
    assert _is_statically_citable("qualitative.value_chain.power_ratio")
    assert _is_statically_citable("qualitative.strategic_context.terminal_haircut")


def test_static_citable_does_not_match_dashboard_dim():
    """qualitative.{dim_id} is the dashboard namespace, NOT statically
    citable — must be checked against the per-call set."""
    assert not _is_statically_citable("qualitative.moat_strength")
    assert not _is_statically_citable("qualitative.quality_composite")


# ── make_thesis_synthesis_class ──────────────────────────────────────────

_BASE_VALID_PAYLOAD = {
    "thesis_statement": "AAPL is fairly valued; cyclicality and phase2 signals converge.",
    "bull_case": {
        "claim": "Bull case based on services re-rating; cite phase2.three_scenario_dcf.bull.",
        "cited_signals": ["phase2.three_scenario_dcf.bull"],
    },
    "bear_case": {
        "claim": "Bear case based on contrarian.bias_detected and phase2.implied_cagr.",
        "cited_signals": ["contrarian.bias_detected", "phase2.implied_cagr"],
    },
    "base_case": {
        "claim": "Base case rests on phase2.three_scenario_dcf.base.intrinsic_per_share.",
        "cited_signals": ["phase2.three_scenario_dcf.base.intrinsic_per_share"],
    },
    "decision_conditions": [
        {"trigger": "implied CAGR exceeds 25%",
         "observable": "phase2.implied_cagr > 0.25",
         "action": "trim", "priority": "amber"},
        {"trigger": "MoS turns negative",
         "observable": "phase2.three_scenario_dcf.base.margin_of_safety < 0",
         "action": "exit", "priority": "red"},
        {"trigger": "Reverse-DCF flips to caution",
         "observable": "reverse_dcf.signal == 'caution'",
         "action": "hold", "priority": "green"},
    ],
    "thesis_confidence": "medium",
    "time_horizon": "1_year",
    "position_sizing_implications":
        "Cite conviction.position_tier; sized as starter given partial coverage.",
    "required_analyst_judgment": ["Strategic value of bundling not in calc layer."],
    "update_conditions": ["Reverse-DCF signal flips from caution to flag."],
}


def test_factory_accepts_static_only_citations():
    cls = make_thesis_synthesis_class(frozenset(), frozenset())
    obj = cls(**_BASE_VALID_PAYLOAD)
    assert obj.thesis_confidence == "medium"
    assert getattr(obj, "_stale_citations") == []


def test_factory_accepts_assessed_dashboard_dim_citation():
    citable = frozenset({"qualitative.moat_strength"})
    cls = make_thesis_synthesis_class(citable, frozenset())
    payload = dict(_BASE_VALID_PAYLOAD)
    payload["bull_case"] = {
        "claim": "Bull case anchored on qualitative.moat_strength.",
        "cited_signals": ["qualitative.moat_strength"],
    }
    cls(**payload)


def test_factory_rejects_unassessed_dashboard_dim_citation():
    """The locked rule (D1): citing a not_assessed dim is a ValidationError."""
    citable = frozenset({"qualitative.moat_strength"})
    cls = make_thesis_synthesis_class(citable, frozenset())
    payload = dict(_BASE_VALID_PAYLOAD)
    payload["bear_case"] = {
        "claim": "Bear case cites qualitative.brand_strength which is unassessed.",
        "cited_signals": ["qualitative.brand_strength"],
    }
    with pytest.raises(ValidationError) as exc_info:
        cls(**payload)
    err = str(exc_info.value)
    assert "qualitative.brand_strength" in err
    assert "non-citable" in err or "required_analyst_judgment" in err


def test_factory_accepts_stale_dim_and_records_it():
    """Stale dims are still citable, just flagged."""
    citable = frozenset({"qualitative.cyclicality"})
    stale = frozenset({"qualitative.cyclicality"})
    cls = make_thesis_synthesis_class(citable, stale)
    payload = dict(_BASE_VALID_PAYLOAD)
    payload["bear_case"] = {
        "claim": "Bear case cites qualitative.cyclicality (stale).",
        "cited_signals": ["qualitative.cyclicality"],
    }
    obj = cls(**payload)
    assert obj._stale_citations == ["qualitative.cyclicality"]


def test_factory_accepts_composite_citation_when_provided():
    citable = frozenset({"qualitative.quality_composite"})
    cls = make_thesis_synthesis_class(citable, frozenset())
    payload = dict(_BASE_VALID_PAYLOAD)
    payload["base_case"] = {
        "claim": "Base case anchored on qualitative.quality_composite of 5.4/7.",
        "cited_signals": ["qualitative.quality_composite"],
    }
    cls(**payload)


def test_factory_rejects_composite_when_not_in_citable_set():
    """Composite is None (no member assessed) → not in citable set →
    ValidationError if cited."""
    cls = make_thesis_synthesis_class(frozenset(), frozenset())
    payload = dict(_BASE_VALID_PAYLOAD)
    payload["base_case"] = {
        "claim": "Base case anchored on qualitative.management_composite.",
        "cited_signals": ["qualitative.management_composite"],
    }
    with pytest.raises(ValidationError):
        cls(**payload)


def test_factory_rejects_unknown_random_path():
    cls = make_thesis_synthesis_class(frozenset(), frozenset())
    payload = dict(_BASE_VALID_PAYLOAD)
    payload["bull_case"] = {
        "claim": "Bull case cites a fabricated path.",
        "cited_signals": ["totally.made.up.path"],
    }
    with pytest.raises(ValidationError):
        cls(**payload)


# ── Catalog hash ─────────────────────────────────────────────────────────

def test_aggregate_catalog_hash_is_stable():
    h1 = _aggregate_catalog_hash()
    h2 = _aggregate_catalog_hash()
    assert h1 == h2
    assert len(h1) == 16
    assert re.fullmatch(r"[0-9a-f]{16}", h1)


def test_aggregate_catalog_hash_empty_when_catalog_missing(monkeypatch):
    import aletheia.agents.thesis_synthesizer as ts_mod
    monkeypatch.setattr(ts_mod, "_CATALOG_LOADED", False)
    assert ts_mod._aggregate_catalog_hash() == ""


# ── Prompt-template invariant ────────────────────────────────────────────

def test_prompt_template_declares_only_expected_variables():
    """LangChain ChatPromptTemplate parses single-brace tokens as
    template variables. Any literal `{dim_id}` / `{agent}` / `{field}` /
    `{cat_id}` / `{cat}` etc. in the prompt body that's NOT meant to be
    substituted MUST be doubled (`{{dim_id}}`) — otherwise chain.invoke
    raises 'Input to ChatPromptTemplate is missing variables'.

    This test guards against future prompt edits introducing the same bug
    (which silently broke refresh by causing the synthesizer to crash on
    every attempt → mock fallback → 503)."""
    from langchain_core.prompts import ChatPromptTemplate
    from aletheia.agents.thesis_synthesizer import _PROMPT_BODY

    t = ChatPromptTemplate.from_template(_PROMPT_BODY)
    expected = {
        "ticker",
        "phase2_summary",
        "qualitative_summary",
        "contrarian_summary",
        "scenarios_summary",
        "conviction_summary",
        "dashboard_summary",
    }
    actual = set(t.input_variables)
    extra = actual - expected
    missing = expected - actual
    assert not extra, (
        f"Prompt template declares unexpected variables {extra}. "
        f"Likely cause: a literal placeholder like `{{dim_id}}` in the prompt "
        f"that should be escaped as `{{{{dim_id}}}}`. "
        f"Full var list: {sorted(actual)}"
    )
    assert not missing, (
        f"Prompt template is missing expected variables {missing}. "
        f"Either the projector contract changed or a `{{var}}` was "
        f"accidentally doubled. Full var list: {sorted(actual)}"
    )
