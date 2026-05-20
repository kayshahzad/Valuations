"""Phase B.1 tests for the consolidated extraction bundle.

Covers:
  - Pydantic schema validation (score bounds, narrative length, enum
    fields, nested item shapes)
  - ``QualitativeExtractionBundle`` round-trip — three dims at once
  - ``fan_out_bundle`` projects bundle → 3 ExtractionResult objects
  - ``make_bundle_extractor`` with a mock LLM:
      - happy path (1 call, valid bundle returned)
      - validation failure + retry (2 calls, second succeeds)
      - validation failure twice (caller gets failure results, all 3
        dims marked None-scored with reason)
  - Empty source text → graceful skip (no LLM call)

No live Gemini calls — the LLM is injected via ``llm_factory``.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from aletheia.qualitative.extractors.base import ExtractionResult
from aletheia.qualitative.extractors.bundle_extractor import (
    fan_out_bundle,
    make_bundle_extractor,
)
from aletheia.qualitative.extractors.llm_extractor import (
    LLM_MODEL_NAME, LLM_PROVIDER,
)
from aletheia.qualitative.extractors.schemas import (
    CompetitorExtraction,
    CustomerExtraction,
    NamedCustomer,
    QualitativeExtractionBundle,
    RegulatoryExposureItem,
    RegulatoryExtraction,
)


# ── Schema validation ──────────────────────────────────────────────


def _valid_competitor() -> CompetitorExtraction:
    return CompetitorExtraction(
        score=5,
        narrative="GOOGL faces competition from MSFT in cloud and AMZN in commerce.",
        named_competitors=["Microsoft", "Amazon", "Meta"],
        competitive_intensity="medium",
    )


def _valid_regulatory() -> RegulatoryExtraction:
    return RegulatoryExtraction(
        score=3,
        narrative="Multiple active antitrust reviews; EU DMA compliance ongoing.",
        material_exposures=[
            RegulatoryExposureItem(
                regulator="DOJ Antitrust Division",
                area="antitrust",
                severity="high",
            ),
            RegulatoryExposureItem(
                regulator="European Commission",
                area="antitrust",
                severity="medium",
            ),
        ],
    )


def _valid_customer() -> CustomerExtraction:
    return CustomerExtraction(
        score=6,
        narrative="No single customer accounted for more than 10% of revenue in FY24.",
        concentration_disclosed=False,
        named_customers=[],
    )


def test_competitor_extraction_score_bounds():
    with pytest.raises(ValidationError):
        CompetitorExtraction(
            score=0, narrative="x", named_competitors=[],
            competitive_intensity="low",
        )
    with pytest.raises(ValidationError):
        CompetitorExtraction(
            score=8, narrative="x", named_competitors=[],
            competitive_intensity="low",
        )


def test_competitor_extraction_intensity_enum():
    with pytest.raises(ValidationError):
        CompetitorExtraction(
            score=4, narrative="x", named_competitors=[],
            competitive_intensity="extreme",  # not in Literal
        )


def test_competitor_extraction_narrative_length_cap():
    """Narrative > 500 chars should fail — matches DB column constraint."""
    long_narr = "x" * 501
    with pytest.raises(ValidationError):
        CompetitorExtraction(
            score=4, narrative=long_narr, named_competitors=[],
            competitive_intensity="low",
        )


def test_regulatory_exposure_item_required_fields():
    with pytest.raises(ValidationError):
        RegulatoryExposureItem(regulator="FTC", area="antitrust")  # missing severity


def test_regulatory_exposure_item_severity_enum():
    with pytest.raises(ValidationError):
        RegulatoryExposureItem(
            regulator="FTC", area="antitrust", severity="critical",
        )


def test_customer_revenue_share_bounds():
    """Revenue share must be 0-100 percent."""
    with pytest.raises(ValidationError):
        NamedCustomer(name="x", revenue_share_pct=150.0)
    with pytest.raises(ValidationError):
        NamedCustomer(name="x", revenue_share_pct=-5.0)


def test_customer_revenue_share_none_allowed():
    """None is the legitimate "named but share not quantified" signal."""
    c = NamedCustomer(name="U.S. Government", revenue_share_pct=None)
    assert c.revenue_share_pct is None


def test_bundle_requires_all_three_dims():
    """Bundle validation fails when any of the three top-level fields
    is missing — partial output should retry, not persist."""
    with pytest.raises(ValidationError):
        QualitativeExtractionBundle(
            competitor_identification=_valid_competitor(),
            regulatory_exposure=_valid_regulatory(),
            # customer_concentration missing
        )


def test_bundle_full_construction():
    """Happy-path validation."""
    bundle = QualitativeExtractionBundle(
        competitor_identification=_valid_competitor(),
        regulatory_exposure=_valid_regulatory(),
        customer_concentration=_valid_customer(),
    )
    assert bundle.competitor_identification.score == 5
    assert bundle.regulatory_exposure.score == 3
    assert bundle.customer_concentration.score == 6
    assert len(bundle.regulatory_exposure.material_exposures) == 2


# ── fan_out_bundle ─────────────────────────────────────────────────


def test_fan_out_returns_three_results():
    """Bundle projects cleanly into three ExtractionResult objects,
    one per dim_id."""
    bundle = QualitativeExtractionBundle(
        competitor_identification=_valid_competitor(),
        regulatory_exposure=_valid_regulatory(),
        customer_concentration=_valid_customer(),
    )
    results = fan_out_bundle(bundle, fingerprint="abc123def4567890")
    assert set(results.keys()) == {
        "competitor_identification",
        "regulatory_exposure",
        "customer_concentration",
    }
    for dim_id, r in results.items():
        assert isinstance(r, ExtractionResult)
        assert r.llm_provider == LLM_PROVIDER
        assert r.llm_model == LLM_MODEL_NAME
        assert r.input_fingerprint == "abc123def4567890"


def test_fan_out_preserves_competitor_payload():
    bundle = QualitativeExtractionBundle(
        competitor_identification=_valid_competitor(),
        regulatory_exposure=_valid_regulatory(),
        customer_concentration=_valid_customer(),
    )
    results = fan_out_bundle(bundle, fingerprint="abcdef0123456789")
    competitor = results["competitor_identification"]
    assert competitor.score == 5
    assert competitor.source_payload["named_competitors"] == [
        "Microsoft", "Amazon", "Meta",
    ]
    assert competitor.source_payload["competitive_intensity"] == "medium"


def test_fan_out_preserves_regulatory_payload():
    bundle = QualitativeExtractionBundle(
        competitor_identification=_valid_competitor(),
        regulatory_exposure=_valid_regulatory(),
        customer_concentration=_valid_customer(),
    )
    results = fan_out_bundle(bundle, fingerprint="0011223344556677")
    reg = results["regulatory_exposure"]
    assert reg.score == 3
    exposures = reg.source_payload["material_exposures"]
    assert len(exposures) == 2
    assert exposures[0]["regulator"] == "DOJ Antitrust Division"
    assert exposures[0]["severity"] == "high"


def test_fan_out_preserves_customer_payload():
    bundle = QualitativeExtractionBundle(
        competitor_identification=_valid_competitor(),
        regulatory_exposure=_valid_regulatory(),
        customer_concentration=_valid_customer(),
    )
    results = fan_out_bundle(bundle, fingerprint="7766554433221100")
    cust = results["customer_concentration"]
    assert cust.score == 6
    assert cust.source_payload["concentration_disclosed"] is False
    assert cust.source_payload["named_customers"] == []


# ── make_bundle_extractor with mock LLM ────────────────────────────


def _build_mock_llm_factory(bundle_response: QualitativeExtractionBundle):
    """Build an llm_factory that returns a mock LangChain chat model.

    The factory hands back a fake LLM whose ``with_structured_output``
    returns a Runnable that's pipable from a ChatPromptTemplate.
    LangChain composes ``prompt | structured`` into a RunnableSequence
    whose ``invoke(args)`` calls our structured Runnable in turn — so
    we just need ``structured.invoke(formatted_prompt)`` to return
    the canned bundle.
    """
    from langchain_core.runnables import RunnableLambda

    def _factory():
        llm = MagicMock(name="MockLLM")
        structured_runnable = RunnableLambda(lambda _: bundle_response)
        llm.with_structured_output = MagicMock(return_value=structured_runnable)
        return llm
    return _factory


def test_bundle_extractor_happy_path():
    """Valid bundle returned on first attempt → three results, no retry."""
    valid_bundle = QualitativeExtractionBundle(
        competitor_identification=_valid_competitor(),
        regulatory_exposure=_valid_regulatory(),
        customer_concentration=_valid_customer(),
    )
    extractor = make_bundle_extractor(
        llm_factory=_build_mock_llm_factory(valid_bundle),
    )
    results = extractor("GOOGL", "Item 1: Business ...\n\nItem 1A: Risk Factors ...")

    assert set(results.keys()) == {
        "competitor_identification",
        "regulatory_exposure",
        "customer_concentration",
    }
    assert results["competitor_identification"].score == 5
    assert results["regulatory_exposure"].score == 3
    assert results["customer_concentration"].score == 6
    # No failure marker
    for r in results.values():
        assert r.reason is None


def test_bundle_extractor_empty_source_text_no_llm_call():
    """Empty source text returns three None-scored failure results
    WITHOUT invoking the LLM — saves Gemini dollars."""
    factory_was_called = []

    def _tracking_factory():
        factory_was_called.append(1)
        return MagicMock()

    extractor = make_bundle_extractor(llm_factory=_tracking_factory)
    results = extractor("GOOGL", "")

    assert factory_was_called == [], "LLM factory should not be called on empty source"
    assert len(results) == 3
    for dim_id, r in results.items():
        assert r.score is None
        assert r.reason == "empty_source_text"


def test_bundle_extractor_whitespace_source_text_no_llm_call():
    """Whitespace-only source text is treated like empty."""
    factory_was_called = []

    def _tracking_factory():
        factory_was_called.append(1)
        return MagicMock()

    extractor = make_bundle_extractor(llm_factory=_tracking_factory)
    results = extractor("GOOGL", "   \n\n\t  ")
    assert factory_was_called == []
    assert all(r.score is None for r in results.values())


def test_bundle_extractor_fingerprint_consistency():
    """All three dims share the same input_fingerprint since they
    share source text. Idempotency relies on this."""
    valid_bundle = QualitativeExtractionBundle(
        competitor_identification=_valid_competitor(),
        regulatory_exposure=_valid_regulatory(),
        customer_concentration=_valid_customer(),
    )
    extractor = make_bundle_extractor(
        llm_factory=_build_mock_llm_factory(valid_bundle),
    )
    results = extractor("GOOGL", "the same source text")
    fingerprints = {r.input_fingerprint for r in results.values()}
    assert len(fingerprints) == 1, (
        "All three dims should share the same source fingerprint"
    )


def test_bundle_extractor_failure_returns_three_none_results():
    """When the LLM raises an exception, the extractor produces three
    None-scored failure results so the persistence layer can still
    write empty-state rows with provenance."""
    failing_factory = lambda: _build_failing_llm()
    extractor = make_bundle_extractor(llm_factory=failing_factory)
    results = extractor("GOOGL", "some text")
    assert len(results) == 3
    for dim_id, r in results.items():
        assert r.score is None
        assert r.reason is not None
        assert "ValueError" in r.reason or "boom" in r.reason


def _build_failing_llm():
    """LLM whose structured-output runnable raises on every invoke.
    Simulates persistent failure (both attempts of the retry loop)."""
    from langchain_core.runnables import RunnableLambda

    def _raise(_):
        raise ValueError("boom")

    llm = MagicMock()
    structured_runnable = RunnableLambda(_raise)
    llm.with_structured_output = MagicMock(return_value=structured_runnable)
    return llm


# ── Registry integration ──────────────────────────────────────────


def test_bundle_dims_constant_matches_schema():
    """The BUNDLE_DIMS tuple in the package init must match the bundle
    schema's top-level fields. Phase B.2 wires the workflow node off
    this list — drift would cause silent omission of a dim."""
    from aletheia.qualitative.extractors import BUNDLE_DIMS
    schema_fields = set(QualitativeExtractionBundle.model_fields.keys())
    assert set(BUNDLE_DIMS) == schema_fields, (
        f"BUNDLE_DIMS {BUNDLE_DIMS} must match schema fields "
        f"{schema_fields}"
    )
