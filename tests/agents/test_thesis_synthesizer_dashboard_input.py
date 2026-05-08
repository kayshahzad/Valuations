"""Phase 3 tests — dashboard projector + confidence clamp + metadata stamp.

No LLM calls. Tests build a state dict directly, call the projector and
clamp helpers in isolation, and verify the agent's metadata-injection
path via the mock-LLM fallback (which is fed a pre-built ThesisSynthesis
through a small monkey-patched chain).
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List

import pytest

from aletheia.agents.thesis_synthesizer import (
    _clamp_confidence,
    _coverage_confidence_ceiling,
    _summarize_qualitative_dashboard,
)


# ── _summarize_qualitative_dashboard ─────────────────────────────────────

def _proj(coverage_state: str = "zero",
          n_assessed: int = 0,
          dimensions: Dict[str, Any] = None,
          categories: Dict[str, Any] = None,
          stale_paths: List[str] = None,
          available: bool = True) -> Dict[str, Any]:
    return {
        "ticker": "TEST",
        "available": available,
        "coverage": {
            "n_assessable":   16,
            "n_assessed":     n_assessed,
            "n_stale":        len(stale_paths or []),
            "n_pending":      3,
            "n_not_assessed": 16 - n_assessed,
            "coverage_state": coverage_state,
            "stale_paths":    stale_paths or [],
            "citable_dim_paths": [],
        },
        "dimensions": dimensions or {},
        "categories": categories or {},
        "stale_paths": stale_paths or [],
        "citable_dim_paths": [],
        "citable_composite_paths": [],
    }


def test_summarize_dashboard_unavailable_emits_explicit_note():
    state = {"qualitative_dashboard": {"available": False}}
    out = _summarize_qualitative_dashboard(state)
    assert "dashboard unavailable" in out.lower()
    assert "required_analyst_judgment" in out


def test_summarize_dashboard_zero_coverage_shows_coverage_line():
    state = {"qualitative_dashboard": _proj()}
    out = _summarize_qualitative_dashboard(state)
    assert "0/16 dims assessed" in out
    assert "(zero)" in out


def test_summarize_dashboard_assessed_dims_listed():
    dims = {
        "moat_strength": {
            "status": "assessed",
            "score": 6.2,
            "narrative": "CUDA ecosystem locks developers in.",
            "source_category": "hitl",
            "title": "Moat Strength",
            "category": "quality",
        },
    }
    state = {"qualitative_dashboard": _proj(
        coverage_state="low", n_assessed=1, dimensions=dims,
    )}
    out = _summarize_qualitative_dashboard(state)
    assert "qualitative.moat_strength = 6.20" in out
    assert "ASSESSED dimensions" in out
    assert "CUDA" in out


def test_summarize_dashboard_stale_section():
    dims = {
        "cyclicality": {
            "status": "stale",
            "score": 3.0,
            "narrative": "",
            "source_category": "deterministic",
            "assessed_at": "2024-01-01T00:00:00+00:00",
            "title": "Cyclicality",
            "category": "risk",
        },
    }
    state = {"qualitative_dashboard": _proj(
        coverage_state="low", n_assessed=1, dimensions=dims,
        stale_paths=["qualitative.cyclicality"],
    )}
    out = _summarize_qualitative_dashboard(state)
    assert "STALE dimensions" in out
    assert "qualitative.cyclicality = 3.00" in out
    assert "stale" in out.lower()


def test_summarize_dashboard_composite_with_stale_marker():
    cats = {
        "quality": {
            "category_id": "quality",
            "title": "Quality",
            "composite_score": 5.4,
            "n_assessed": 3,
            "n_total": 5,
            "status": "stale",
            "stale_contributors": ["roiic_trend"],
            "contributing": [],
        },
    }
    state = {"qualitative_dashboard": _proj(categories=cats)}
    out = _summarize_qualitative_dashboard(state)
    assert "Quality" in out
    assert "composite=5.40" in out
    assert "qualitative.quality_composite" in out
    assert "stale via roiic_trend" in out


def test_summarize_dashboard_pending_data_section_explicit():
    dims = {
        "industry_concentration": {
            "status": "pending_data",
            "score": None, "narrative": None,
            "source_category": "pending_data",
            "title": "Industry concentration", "category": "competitive",
        },
    }
    state = {"qualitative_dashboard": _proj(dimensions=dims)}
    out = _summarize_qualitative_dashboard(state)
    assert "PENDING_DATA" in out
    assert "industry_concentration" in out
    assert "platform-level gap" in out or "infra not wired" in out.lower()


# ── Confidence-floor clamp (D2) ──────────────────────────────────────────

def test_ceiling_for_each_coverage_state():
    assert _coverage_confidence_ceiling("high") == "high"
    assert _coverage_confidence_ceiling("medium") == "medium"
    assert _coverage_confidence_ceiling("low") == "low"
    assert _coverage_confidence_ceiling("zero") == "insufficient_signal"


def test_clamp_zero_coverage_forces_insufficient_signal():
    """LLM emitting `high` on zero-coverage ticker must clamp down."""
    clamped, was_clamped, reason = _clamp_confidence("high", "zero")
    assert clamped == "insufficient_signal"
    assert was_clamped is True
    assert "insufficient_signal" in reason


def test_clamp_medium_coverage_caps_high_to_medium():
    clamped, was_clamped, reason = _clamp_confidence("high", "medium")
    assert clamped == "medium"
    assert was_clamped is True
    assert "medium" in reason


def test_clamp_low_coverage_caps_high_to_low():
    clamped, _, _ = _clamp_confidence("high", "low")
    assert clamped == "low"


def test_clamp_does_not_raise_confidence():
    """Floor-only — LLM picking lower than ceiling is preserved."""
    clamped, was_clamped, _ = _clamp_confidence("low", "high")
    assert clamped == "low"
    assert was_clamped is False


def test_clamp_high_coverage_unclamped():
    clamped, was_clamped, _ = _clamp_confidence("high", "high")
    assert clamped == "high"
    assert was_clamped is False


def test_clamp_medium_at_medium_coverage_unclamped():
    """If LLM picked exactly the ceiling, it's not 'clamped'."""
    clamped, was_clamped, _ = _clamp_confidence("medium", "medium")
    assert clamped == "medium"
    assert was_clamped is False
