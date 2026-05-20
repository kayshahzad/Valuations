"""Phase A foundation tests for the LLM-augmented extractor protocol.

Covers:
  - ``ExtractionResult`` shape + immutability
  - Empty source text → graceful skip (no LLM call)
  - Mismatched dim_id raises ValueError (caller bug)
  - ``_slice_section`` correctly pulls Item 1 / Item 1A from the
    librarian's concatenated text format
  - ``qualitative_extraction_node`` is a no-op when EXTRACTORS is
    empty (foundation state)

No LLM is invoked — the LLM extractor factory is tested in Phase B once
per-dim prompts + schemas land.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from aletheia.qualitative.extractors import EXTRACTORS, ExtractionResult
from aletheia.qualitative.extractors.llm_extractor import (
    LLM_MODEL_NAME, LLM_PROVIDER, make_llm_extractor,
)


# ── ExtractionResult ────────────────────────────────────────────────


def test_extraction_result_is_frozen():
    """ExtractionResult is a frozen dataclass — prevents accidental
    mutation between extractor and persistence layer."""
    r = ExtractionResult(
        score=5, narrative="ok", source_payload={},
        input_fingerprint="abc", llm_provider="gemini",
        llm_model="test-model",
    )
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        r.score = 6  # type: ignore[misc]


def test_extraction_result_accepts_none_score():
    """Score=None is the legitimate "extractor ran but couldn't
    score" signal — distinct from "extractor wasn't run." """
    r = ExtractionResult(
        score=None, narrative="(no signal)",
        source_payload={"reason": "empty_source"},
        input_fingerprint="def", llm_provider="gemini",
        llm_model="test-model", reason="empty_source",
    )
    assert r.score is None
    assert r.reason == "empty_source"


# ── make_llm_extractor: graceful skips ─────────────────────────────


class _FixtureSchema(BaseModel):
    """Minimal schema for testing the extractor factory."""
    score: int = Field(..., ge=1, le=7)
    narrative: str = Field(..., max_length=500)
    named_things: list = Field(default_factory=list)


def test_extractor_skips_on_empty_source_text():
    """No LLM call should fire when source text is empty/whitespace —
    saves Gemini dollars and produces a clean empty-state row."""
    extractor = make_llm_extractor(
        dim_id="fixture_dim",
        prompt_template="Score {ticker}: {source_text}",
        output_schema=_FixtureSchema,
    )
    result = extractor("AAPL", "", "fixture_dim")
    assert result is not None
    assert result.score is None
    assert result.reason == "empty_source_text"
    assert result.llm_provider == LLM_PROVIDER
    assert result.llm_model == LLM_MODEL_NAME


def test_extractor_skips_on_whitespace_source_text():
    extractor = make_llm_extractor(
        dim_id="fixture_dim",
        prompt_template="Score {ticker}: {source_text}",
        output_schema=_FixtureSchema,
    )
    result = extractor("AAPL", "   \n\n  ", "fixture_dim")
    assert result is not None
    assert result.score is None


def test_extractor_raises_on_dim_id_mismatch():
    """Mismatched dim_id is a caller bug — fail loudly, don't silently
    persist the wrong dimension."""
    extractor = make_llm_extractor(
        dim_id="fixture_dim",
        prompt_template="Score {ticker}: {source_text}",
        output_schema=_FixtureSchema,
    )
    with pytest.raises(ValueError, match="mismatched"):
        extractor("AAPL", "some text", "wrong_dim_id")


# ── Section slicing ────────────────────────────────────────────────


_FIXTURE_10K = """ITEM 1: BUSINESS
We make widgets. Our competitors are Acme Corp and Globex Inc.
This is the business description.

ITEM 1A: RISK FACTORS
Our business is subject to regulation by the FCC and SEC.
Litigation risk from ongoing antitrust review.

ITEM 7: MD&A
Revenue grew 12% YoY."""


def test_slice_section_item_1():
    from aletheia.agents.qualitative_extraction import _slice_section
    out = _slice_section(_FIXTURE_10K, "item_1")
    assert "We make widgets" in out
    assert "competitors are Acme" in out
    # Should stop before Item 1A
    assert "FCC" not in out
    assert "Revenue grew" not in out


def test_slice_section_item_1a():
    from aletheia.agents.qualitative_extraction import _slice_section
    out = _slice_section(_FIXTURE_10K, "item_1a")
    assert "FCC and SEC" in out
    assert "antitrust" in out
    # Should stop before Item 7
    assert "Revenue grew" not in out
    # Should not include Item 1
    assert "widgets" not in out


def test_slice_section_missing_marker_returns_full_text():
    """When a section header isn't found (older filings, foreign
    issuers), the slicer falls back to the full text so the extractor
    still has something to work with."""
    from aletheia.agents.qualitative_extraction import _slice_section
    text_without_markers = "Some financial filing without standard headers."
    out = _slice_section(text_without_markers, "item_1")
    assert out == text_without_markers


def test_slice_section_empty_text():
    from aletheia.agents.qualitative_extraction import _slice_section
    assert _slice_section("", "item_1") == ""
    assert _slice_section(None, "item_1") == ""  # type: ignore[arg-type]


def test_build_section_texts_caches_per_section():
    """Multiple dims reading the same section share one slice — the
    cache means we don't re-walk the string for each."""
    from aletheia.agents.qualitative_extraction import _build_section_texts
    section_map = {
        "competitor_identification": "item_1",
        "customer_concentration":    "item_1",  # same section as above
        "regulatory_exposure":       "item_1a",
    }
    out = _build_section_texts(_FIXTURE_10K, section_map=section_map)
    # Same dim should get same content
    assert out["competitor_identification"] == out["customer_concentration"]
    assert "FCC" not in out["competitor_identification"]
    assert "FCC" in out["regulatory_exposure"]


# ── Workflow node: foundation no-op ────────────────────────────────


def test_per_dim_registry_still_empty_in_phase_b():
    """Single-dim ``EXTRACTORS`` registry stays empty in Phase B —
    bundle path replaces it. Reserved for Phase C (DEF 14A
    management dims) where the source filing differs from the 10-K
    and a separate LLM call is unavoidable."""
    assert EXTRACTORS == {}, (
        "Single-dim EXTRACTORS registry must stay empty in Phase B. "
        "Bundle dims (competitor_identification, regulatory_exposure, "
        "customer_concentration) flow through BUNDLE_DIMS instead. "
        "Phase C will populate this registry for DEF 14A extractors."
    )


def test_node_skips_when_no_ticker():
    from aletheia.agents.qualitative_extraction import (
        qualitative_extraction_node,
    )
    state = {"raw_10k_text": _FIXTURE_10K}
    out = qualitative_extraction_node(state)
    assert out == {"qualitative_extraction_results": []}


def test_node_returns_no_data_for_all_bundle_dims_when_filings_missing(monkeypatch):
    """If librarian failed to fetch BOTH 10-K and DEF 14A, every
    bundle dim logs no_data — but the node doesn't crash and
    doesn't call any LLM. After Phase C the node runs two bundles
    (10-K + DEF 14A) so the empty-state covers all 5 dims."""
    from aletheia.agents.qualitative_extraction import (
        qualitative_extraction_node,
    )
    from aletheia.qualitative.extractors import (
        BUNDLE_DIMS, DEF14A_BUNDLE_DIMS,
    )
    state = {"ticker": "AAPL", "raw_10k_text": "", "raw_def14a_text": ""}
    out = qualitative_extraction_node(state)
    results = out["qualitative_extraction_results"]

    all_expected = set(BUNDLE_DIMS) | set(DEF14A_BUNDLE_DIMS)
    assert len(results) == len(all_expected)
    assert {r["dimension_id"] for r in results} == all_expected
    for r in results:
        assert r["status"] == "no_data"
        # Reason names the missing filing
        if r["dimension_id"] in BUNDLE_DIMS:
            assert r["reason"] == "no_10k_text"
        else:
            assert r["reason"] == "no_def14a_text"
