"""DEF 14A bundle extractor — one LLM call covers both management dims.

Phase C of the qualitative-tab wiring. Mirrors the Phase B
``bundle_extractor`` pattern but for the proxy statement filing
(DEF 14A) and the two management dimensions:

  - ``management_tenure_continuity``
  - ``management_alignment``

Shares the LLM-with-retry primitive (``_llm_invoke.invoke_with_retry``)
with the Phase B bundle. Differs only in schema + prompt + fan-out.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from langchain_google_genai import ChatGoogleGenerativeAI

from aletheia.qualitative.extractors._llm_invoke import (
    fingerprint_source_text as _fingerprint,
    invoke_with_retry,
)
from aletheia.qualitative.extractors.base import ExtractionResult
from aletheia.qualitative.extractors.def14a_schemas import (
    AlignmentExtraction,
    Def14aExtractionBundle,
    TenureContinuityExtraction,
)
from aletheia.qualitative.extractors.llm_extractor import (
    LLM_MODEL_NAME, LLM_TEMPERATURE, LLM_PROVIDER,
)
from aletheia.qualitative.extractors.prompts import DEF14A_BUNDLE_PROMPT


_DIM_IDS = (
    "management_tenure_continuity",
    "management_alignment",
)


def fan_out_def14a_bundle(
    bundle: Def14aExtractionBundle,
    fingerprint: str,
) -> Dict[str, ExtractionResult]:
    """Project a Def14aExtractionBundle into two ExtractionResult
    objects keyed by dim_id. Pure function — no LLM calls."""
    return {
        "management_tenure_continuity": _tenure_to_result(
            bundle.management_tenure_continuity, fingerprint,
        ),
        "management_alignment": _alignment_to_result(
            bundle.management_alignment, fingerprint,
        ),
    }


def _tenure_to_result(
    t: TenureContinuityExtraction, fingerprint: str,
) -> ExtractionResult:
    return ExtractionResult(
        score=t.score,
        narrative=t.narrative,
        source_payload={
            "ceo_name":                 t.ceo_name,
            "ceo_years_tenure":         t.ceo_years_tenure,
            "median_director_tenure_years": t.median_director_tenure_years,
            "recent_turnover_events":   list(t.recent_turnover_events),
            "notable_directors": [
                {"name": d.name, "role": d.role,
                 "years_tenure": d.years_tenure}
                for d in t.notable_directors
            ],
        },
        input_fingerprint=fingerprint,
        llm_provider=LLM_PROVIDER,
        llm_model=LLM_MODEL_NAME,
    )


def _alignment_to_result(
    a: AlignmentExtraction, fingerprint: str,
) -> ExtractionResult:
    return ExtractionResult(
        score=a.score,
        narrative=a.narrative,
        source_payload={
            "ceo_ownership_pct":     a.ceo_ownership_pct,
            "insider_ownership_pct": a.insider_ownership_pct,
            "comp_structure": [
                {"component": c.component, "weight_pct": c.weight_pct}
                for c in a.comp_structure
            ],
            "performance_metrics": list(a.performance_metrics),
        },
        input_fingerprint=fingerprint,
        llm_provider=LLM_PROVIDER,
        llm_model=LLM_MODEL_NAME,
    )


def _failure_results(
    fingerprint: str, reason: str,
) -> Dict[str, ExtractionResult]:
    """Two None-scored failure results — caller persists them so the
    empty state is auditable in the dashboard projection."""
    return {
        dim_id: ExtractionResult(
            score=None,
            narrative=f"(DEF 14A extraction failed: {reason[:200]})",
            source_payload={"error": reason[:500]},
            input_fingerprint=fingerprint,
            llm_provider=LLM_PROVIDER,
            llm_model=LLM_MODEL_NAME,
            reason=reason,
        )
        for dim_id in _DIM_IDS
    }


# ── Public factory ─────────────────────────────────────────────────


Def14aBundleExtractor = Callable[[str, str], Dict[str, ExtractionResult]]


def make_def14a_bundle_extractor(
    *,
    llm_factory: Optional[Callable[[], object]] = None,
    prompt_template: str = DEF14A_BUNDLE_PROMPT,
) -> Def14aBundleExtractor:
    """Build the two-dim management-extraction bundle for DEF 14A.

    Args:
        llm_factory: Zero-arg callable returning a LangChain
            ``BaseChatModel``. Injectable for tests. Default: Gemini.
        prompt_template: Override for tests / prompt iteration.

    Returns:
        Callable matching ``Def14aBundleExtractor`` signature.
        Returns two ExtractionResults per call. Empty source ⇒ two
        skip results without LLM invocation. Failure ⇒ two None-
        scored failure results.
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
            schema=Def14aExtractionBundle,
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

        return fan_out_def14a_bundle(bundle, fingerprint)

    _extract.__name__ = "def14a_bundle_extractor"
    _extract.__doc__ = (
        "Consolidated extractor — two LLM_AUGMENTED management dims "
        "from DEF 14A in one Gemini call."
    )
    return _extract


__all__ = [
    "make_def14a_bundle_extractor",
    "fan_out_def14a_bundle",
    "Def14aBundleExtractor",
]
