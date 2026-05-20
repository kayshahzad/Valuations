"""Phase C tests for the DEF 14A extraction bundle.

Mirrors ``test_extraction_bundle.py`` (Phase B) but for the proxy-
statement bundle covering the 2 management dims.

Covers:
  - Schema validation (score bounds, ownership %, comp component enum)
  - Bundle requires both dims
  - ``fan_out_def14a_bundle`` projects to 2 ExtractionResults with
    correct payload shape
  - ``make_def14a_bundle_extractor`` with mocked LLM:
      - happy path
      - empty / whitespace source → 2 None-scored skips without LLM
      - LLM failure → 2 None-scored failure results
  - DEF14A_BUNDLE_DIMS aligned with schema fields
  - Catalog reflects LLM_AUGMENTED for both mgmt dims
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from aletheia.qualitative.extractors.base import ExtractionResult
from aletheia.qualitative.extractors.def14a_bundle_extractor import (
    fan_out_def14a_bundle,
    make_def14a_bundle_extractor,
)
from aletheia.qualitative.extractors.def14a_schemas import (
    AlignmentExtraction,
    CompPlanComponent,
    Def14aExtractionBundle,
    DirectorTenureItem,
    TenureContinuityExtraction,
)
from aletheia.qualitative.extractors.llm_extractor import (
    LLM_MODEL_NAME, LLM_PROVIDER,
)


# ── Fixture builders ──────────────────────────────────────────────


def _valid_tenure() -> TenureContinuityExtraction:
    return TenureContinuityExtraction(
        score=6,
        narrative=(
            "Tim Cook has served as CEO since 2011 (15-year tenure). "
            "Board has multiple long-tenured independent directors. "
            "Documented succession plan in place."
        ),
        ceo_name="Timothy D. Cook",
        ceo_years_tenure=15,
        median_director_tenure_years=8.5,
        recent_turnover_events=[],
        notable_directors=[
            DirectorTenureItem(name="Arthur D. Levinson", role="Chair",
                               years_tenure=14),
            DirectorTenureItem(name="James A. Bell", role="Lead Independent Director",
                               years_tenure=10),
        ],
    )


def _valid_alignment() -> AlignmentExtraction:
    return AlignmentExtraction(
        score=5,
        narrative=(
            "CEO holds ~0.02% of shares; insiders collectively ~0.1%. "
            "Comp is equity-heavy with PSUs tied to relative TSR vs S&P 500."
        ),
        ceo_ownership_pct=0.02,
        insider_ownership_pct=0.10,
        comp_structure=[
            CompPlanComponent(component="base_salary", weight_pct=5.0),
            CompPlanComponent(component="annual_bonus", weight_pct=25.0),
            CompPlanComponent(component="performance_shares", weight_pct=70.0),
        ],
        performance_metrics=[
            "Relative TSR vs S&P 500 (3-year)",
            "Net Sales (1-year)",
            "Operating Income (1-year)",
        ],
    )


def _valid_bundle() -> Def14aExtractionBundle:
    return Def14aExtractionBundle(
        management_tenure_continuity=_valid_tenure(),
        management_alignment=_valid_alignment(),
    )


# ── Schema validation ─────────────────────────────────────────────


def test_tenure_score_bounds():
    with pytest.raises(ValidationError):
        TenureContinuityExtraction(
            score=0, narrative="x", recent_turnover_events=[],
            notable_directors=[],
        )
    with pytest.raises(ValidationError):
        TenureContinuityExtraction(
            score=8, narrative="x", recent_turnover_events=[],
            notable_directors=[],
        )


def test_director_tenure_item_optional_years():
    """years_tenure=None is legitimate (proxy biographical paragraph
    may not state the number explicitly)."""
    d = DirectorTenureItem(name="X", role="Director", years_tenure=None)
    assert d.years_tenure is None


def test_director_tenure_item_year_range():
    with pytest.raises(ValidationError):
        DirectorTenureItem(name="X", role="D", years_tenure=-1)
    with pytest.raises(ValidationError):
        DirectorTenureItem(name="X", role="D", years_tenure=100)


def test_alignment_ownership_pct_bounds():
    """Ownership % must be 0-100. Negative or >100 is data error."""
    with pytest.raises(ValidationError):
        AlignmentExtraction(
            score=4, narrative="x", ceo_ownership_pct=150.0,
            comp_structure=[], performance_metrics=[],
        )
    with pytest.raises(ValidationError):
        AlignmentExtraction(
            score=4, narrative="x", insider_ownership_pct=-5.0,
            comp_structure=[], performance_metrics=[],
        )


def test_comp_plan_component_enum():
    with pytest.raises(ValidationError):
        CompPlanComponent(component="random_thing", weight_pct=20.0)


def test_comp_plan_weight_pct_bounds():
    with pytest.raises(ValidationError):
        CompPlanComponent(component="base_salary", weight_pct=150.0)


def test_bundle_requires_both_dims():
    with pytest.raises(ValidationError):
        Def14aExtractionBundle(
            management_tenure_continuity=_valid_tenure(),
            # management_alignment missing
        )


def test_bundle_full_construction():
    bundle = _valid_bundle()
    assert bundle.management_tenure_continuity.score == 6
    assert bundle.management_alignment.score == 5
    assert bundle.management_tenure_continuity.ceo_name == "Timothy D. Cook"
    assert len(bundle.management_alignment.comp_structure) == 3


# ── fan_out_def14a_bundle ────────────────────────────────────────


def test_fan_out_returns_two_results():
    results = fan_out_def14a_bundle(_valid_bundle(), fingerprint="proxyabc1234567")
    assert set(results.keys()) == {
        "management_tenure_continuity",
        "management_alignment",
    }
    for r in results.values():
        assert isinstance(r, ExtractionResult)
        assert r.llm_provider == LLM_PROVIDER
        assert r.llm_model == LLM_MODEL_NAME
        assert r.input_fingerprint == "proxyabc1234567"


def test_fan_out_preserves_tenure_payload():
    results = fan_out_def14a_bundle(_valid_bundle(), fingerprint="abc0")
    tenure = results["management_tenure_continuity"]
    assert tenure.score == 6
    assert tenure.source_payload["ceo_name"] == "Timothy D. Cook"
    assert tenure.source_payload["ceo_years_tenure"] == 15
    assert tenure.source_payload["median_director_tenure_years"] == 8.5
    assert len(tenure.source_payload["notable_directors"]) == 2
    assert tenure.source_payload["notable_directors"][0]["name"] == "Arthur D. Levinson"


def test_fan_out_preserves_alignment_payload():
    results = fan_out_def14a_bundle(_valid_bundle(), fingerprint="abc1")
    align = results["management_alignment"]
    assert align.score == 5
    assert align.source_payload["ceo_ownership_pct"] == 0.02
    assert align.source_payload["insider_ownership_pct"] == 0.10
    assert len(align.source_payload["comp_structure"]) == 3
    perf_metrics = align.source_payload["performance_metrics"]
    assert any("TSR" in m for m in perf_metrics)


# ── make_def14a_bundle_extractor with mock LLM ───────────────────


def _build_mock_factory(bundle_response: Def14aExtractionBundle):
    def _factory():
        llm = MagicMock(name="MockLLM")
        structured = RunnableLambda(lambda _: bundle_response)
        llm.with_structured_output = MagicMock(return_value=structured)
        return llm
    return _factory


def test_extractor_happy_path():
    extractor = make_def14a_bundle_extractor(
        llm_factory=_build_mock_factory(_valid_bundle()),
    )
    results = extractor("AAPL", "SCHEDULE 14A PROXY STATEMENT ...")

    assert set(results.keys()) == {
        "management_tenure_continuity",
        "management_alignment",
    }
    assert results["management_tenure_continuity"].score == 6
    assert results["management_alignment"].score == 5
    for r in results.values():
        assert r.reason is None


def test_extractor_empty_source_no_llm_call():
    """Empty proxy text → 2 None-scored skips without invoking LLM.
    Saves Gemini dollars on tickers without DEF 14A filings."""
    called = []
    extractor = make_def14a_bundle_extractor(
        llm_factory=lambda: called.append(1) or MagicMock(),
    )
    results = extractor("AAPL", "")
    assert called == []
    assert len(results) == 2
    for r in results.values():
        assert r.score is None
        assert r.reason == "empty_source_text"


def test_extractor_whitespace_source_no_llm_call():
    called = []
    extractor = make_def14a_bundle_extractor(
        llm_factory=lambda: called.append(1) or MagicMock(),
    )
    results = extractor("AAPL", "  \n\n  ")
    assert called == []
    assert all(r.score is None for r in results.values())


def test_extractor_fingerprint_consistency():
    """Both dims share the same source fingerprint — idempotency
    relies on this (same as the Phase B bundle)."""
    extractor = make_def14a_bundle_extractor(
        llm_factory=_build_mock_factory(_valid_bundle()),
    )
    results = extractor("AAPL", "the same proxy text")
    fingerprints = {r.input_fingerprint for r in results.values()}
    assert len(fingerprints) == 1


def test_extractor_failure_returns_two_none_results():
    def _failing_factory():
        llm = MagicMock()

        def _raise(_):
            raise ValueError("simulated LLM failure")
        structured = RunnableLambda(_raise)
        llm.with_structured_output = MagicMock(return_value=structured)
        return llm

    extractor = make_def14a_bundle_extractor(llm_factory=_failing_factory)
    results = extractor("AAPL", "some proxy text")
    assert len(results) == 2
    for r in results.values():
        assert r.score is None
        assert r.reason is not None
        assert "simulated" in r.reason


# ── Registry + catalog alignment ─────────────────────────────────


def test_def14a_bundle_dims_match_schema():
    from aletheia.qualitative.extractors import DEF14A_BUNDLE_DIMS
    schema_fields = set(Def14aExtractionBundle.model_fields.keys())
    assert set(DEF14A_BUNDLE_DIMS) == schema_fields, (
        f"DEF14A_BUNDLE_DIMS {DEF14A_BUNDLE_DIMS} must match schema "
        f"fields {schema_fields}"
    )


def test_catalog_mgmt_dims_are_llm_augmented():
    """Phase C flips management_tenure_continuity + management_alignment
    from PENDING_DATA to LLM_AUGMENTED. Architecture-lock-style test
    so a future revert is caught loudly."""
    from config.qualitative_dimensions import DIMENSIONS
    from aletheia.qualitative.types import SourceCategory
    for dim_id in ("management_tenure_continuity", "management_alignment"):
        assert dim_id in DIMENSIONS, f"Catalog missing {dim_id}"
        assert DIMENSIONS[dim_id].source_category == SourceCategory.LLM_AUGMENTED, (
            f"{dim_id} must be LLM_AUGMENTED post-Phase-C, got "
            f"{DIMENSIONS[dim_id].source_category}"
        )
