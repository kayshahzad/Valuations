"""Bundle extractor — one LLM call returns all three LLM_AUGMENTED dims.

Phase B.1. Sibling of ``llm_extractor.make_llm_extractor`` (the
single-dim factory). The bundle path keeps Stage 4 cost +33% instead
of +100% by consolidating three dims into one Gemini round-trip.

Workflow:

    bundle_extractor(ticker, source_text)
        ↓ ChatGoogleGenerativeAI + with_structured_output(BUNDLE_SCHEMA)
        ↓ retry once on validation failure
    QualitativeExtractionBundle
        ↓ fan_out_bundle()
    {dim_id: ExtractionResult}   (3 entries)
        ↓ extraction_runner.persist_extraction (per dim)
    qualitative_assessments rows

The fan-out lives in this module because it needs the schema-to-
ExtractionResult mapping to stay co-located with the schema
definition. The persistence layer (extraction_runner) is generic — it
takes ExtractionResult objects and writes them.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from langchain_google_genai import ChatGoogleGenerativeAI

from aletheia.qualitative.extractors._llm_invoke import (
    fingerprint_source_text as _fingerprint,
    invoke_with_retry,
)
from aletheia.qualitative.extractors.base import ExtractionResult
from aletheia.qualitative.extractors.llm_extractor import (
    LLM_MODEL_NAME, LLM_TEMPERATURE, LLM_PROVIDER,
)
from aletheia.qualitative.extractors.prompts import BUNDLE_PROMPT
from aletheia.qualitative.extractors.schemas import (
    CompetitorExtraction,
    CustomerExtraction,
    QualitativeExtractionBundle,
    RegulatoryExtraction,
)


# Per-dim extractor-name (used in error messages + provenance trail)
_DIM_IDS = (
    "competitor_identification",
    "regulatory_exposure",
    "customer_concentration",
)


def fan_out_bundle(
    bundle: QualitativeExtractionBundle,
    fingerprint: str,
) -> Dict[str, ExtractionResult]:
    """Convert one bundle into three ExtractionResult objects keyed
    by dim_id. Each result is suitable for direct persistence via
    ``extraction_runner.persist_extraction``.

    The bundle's nested Pydantic models carry the per-dim score +
    narrative + structured payload. This function projects each
    onto the runner-compatible shape — no LLM calls, pure
    transformation.
    """
    return {
        "competitor_identification": _competitor_to_result(
            bundle.competitor_identification, fingerprint,
        ),
        "regulatory_exposure": _regulatory_to_result(
            bundle.regulatory_exposure, fingerprint,
        ),
        "customer_concentration": _customer_to_result(
            bundle.customer_concentration, fingerprint,
        ),
    }


def _competitor_to_result(
    c: CompetitorExtraction, fingerprint: str,
) -> ExtractionResult:
    return ExtractionResult(
        score=c.score,
        narrative=c.narrative,
        source_payload={
            "named_competitors":      list(c.named_competitors),
            "competitive_intensity":  c.competitive_intensity,
        },
        input_fingerprint=fingerprint,
        llm_provider=LLM_PROVIDER,
        llm_model=LLM_MODEL_NAME,
    )


def _regulatory_to_result(
    r: RegulatoryExtraction, fingerprint: str,
) -> ExtractionResult:
    return ExtractionResult(
        score=r.score,
        narrative=r.narrative,
        source_payload={
            "material_exposures": [
                {"regulator": x.regulator, "area": x.area,
                 "severity": x.severity}
                for x in r.material_exposures
            ],
        },
        input_fingerprint=fingerprint,
        llm_provider=LLM_PROVIDER,
        llm_model=LLM_MODEL_NAME,
    )


def _customer_to_result(
    c: CustomerExtraction, fingerprint: str,
) -> ExtractionResult:
    return ExtractionResult(
        score=c.score,
        narrative=c.narrative,
        source_payload={
            "concentration_disclosed": c.concentration_disclosed,
            "named_customers": [
                {"name": x.name, "revenue_share_pct": x.revenue_share_pct}
                for x in c.named_customers
            ],
        },
        input_fingerprint=fingerprint,
        llm_provider=LLM_PROVIDER,
        llm_model=LLM_MODEL_NAME,
    )


def _failure_results(
    fingerprint: str, reason: str,
) -> Dict[str, ExtractionResult]:
    """When the LLM call fails after retries, produce three
    None-scored results so the runner still persists empty-state rows
    with provenance — keeps the dashboard projection honest about
    what was attempted and why it didn't produce a score."""
    return {
        dim_id: ExtractionResult(
            score=None,
            narrative=f"(extraction failed: {reason[:200]})",
            source_payload={"error": reason[:500]},
            input_fingerprint=fingerprint,
            llm_provider=LLM_PROVIDER,
            llm_model=LLM_MODEL_NAME,
            reason=reason,
        )
        for dim_id in _DIM_IDS
    }


# ── Public factory ─────────────────────────────────────────────────


# Bundle extractor signature: (ticker, source_text) → {dim_id: ExtractionResult}
BundleExtractor = Callable[[str, str], Dict[str, ExtractionResult]]


def make_bundle_extractor(
    *,
    llm_factory: Optional[Callable[[], object]] = None,
    prompt_template: str = BUNDLE_PROMPT,
) -> BundleExtractor:
    """Build the consolidated three-dim extractor.

    Args:
        llm_factory: Zero-arg callable that returns a LangChain
            ``BaseChatModel`` (Gemini today). Injecting it makes the
            extractor unit-testable with a mock LLM — production
            callers pass nothing and get the default
            ``ChatGoogleGenerativeAI`` construction.
        prompt_template: Override the default prompt. Useful for
            tests or for future prompt iteration.

    Returns:
        Callable matching ``BundleExtractor`` signature. Empty
        source text returns three skip results without invoking the
        LLM. Validation failures after retries return three failure
        results — caller (workflow node) persists them all so the
        empty state is auditable.
    """
    if llm_factory is None:
        llm_factory = lambda: ChatGoogleGenerativeAI(
            model=LLM_MODEL_NAME, temperature=LLM_TEMPERATURE,
        )

    def _extract(
        ticker: str, source_text: str,
    ) -> Dict[str, ExtractionResult]:
        fingerprint = _fingerprint(source_text or "")

        bundle, last_error = invoke_with_retry(
            llm_factory=llm_factory,
            schema=QualitativeExtractionBundle,
            prompt_template=prompt_template,
            ticker=ticker,
            source_text=source_text,
        )

        if bundle is None:
            if isinstance(last_error, ValueError) and str(last_error) == "empty_source_text":
                return _failure_results(fingerprint, "empty_source_text")
            reason = (
                f"{type(last_error).__name__}: {str(last_error)[:200]}"
                if last_error else "unknown"
            )
            return _failure_results(fingerprint, reason)

        return fan_out_bundle(bundle, fingerprint)

    _extract.__name__ = "bundle_extractor"
    _extract.__doc__ = (
        "Consolidated extractor — three LLM_AUGMENTED dims in one "
        "Gemini call. Returns {dim_id: ExtractionResult}."
    )
    return _extract


__all__ = [
    "make_bundle_extractor",
    "fan_out_bundle",
    "BundleExtractor",
]
