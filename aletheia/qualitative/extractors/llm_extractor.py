"""LLM extractor factory — turns a (prompt, schema) pair into an Extractor.

Phase A foundation. Concrete per-dimension extractors live in
``aletheia/qualitative/extractors/schemas/`` (Phase B) and use
``make_llm_extractor`` to wire themselves up — they specify only the
prompt template, the Pydantic output schema, and any input-text
preprocessing. The Gemini call + structured-output validation + retry
logic lives here so all extractors share the same plumbing.

Gemini-only for now. When the local-LLM provider layer ships, swap the
``ChatGoogleGenerativeAI`` construction for a call to
``aletheia.llm.get_llm()`` without touching the extractor schemas or
prompts. The contract this module exposes — ``make_llm_extractor`` →
``Extractor`` — is provider-agnostic.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Optional, Type

from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, ValidationError

from aletheia.qualitative.extractors.base import (
    Extractor,
    ExtractionResult,
)


# Same model + temperature the existing qualitative_synthesis,
# contrarian_v2, thesis_synthesizer agents use. Keeps the LLM cost
# profile of Stage 4 unchanged when extractors land.
LLM_MODEL_NAME: str = "gemini-3.1-pro-preview"
LLM_TEMPERATURE: float = 0.1
LLM_PROVIDER: str = "gemini"

# Retry budget mirrors qualitative_synthesis_agent's 2-attempt pattern.
# First attempt: clean prompt. Second attempt: prompt + "the previous
# response failed schema validation with X — produce valid output".
_MAX_RETRIES: int = 2


def _fingerprint_source_text(source_text: str) -> str:
    """16-char SHA-256 over the source text the extractor was given.

    Matches the deterministic-computer fingerprint convention so the
    runner's "unchanged" check works the same way for both source
    categories — when the source text (10-K section) doesn't change,
    re-running the extractor doesn't append a new row.
    """
    return hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:16]


def make_llm_extractor(
    *,
    dim_id: str,
    prompt_template: str,
    output_schema: Type[BaseModel],
    score_field: str = "score",
    narrative_field: str = "narrative",
    payload_fields: Optional[list] = None,
) -> Extractor:
    """Build an :class:`Extractor` callable from a prompt and schema.

    Args:
        dim_id: Dimension this extractor produces — recorded on the
            ``ExtractionResult`` so the runner can validate registry
            membership.
        prompt_template: A LangChain ``ChatPromptTemplate`` string with
            ``{ticker}`` and ``{source_text}`` placeholders. Per-dim
            prompts in ``extractors/prompts/`` follow this convention.
        output_schema: Pydantic model class that the LLM's structured
            output must match. Validation happens via LangChain's
            ``with_structured_output(schema)`` chain — invalid outputs
            trigger a retry per the ``_MAX_RETRIES`` policy.
        score_field: Name of the field on ``output_schema`` that holds
            the 1-7 composite score. Defaults to "score".
        narrative_field: Name of the analyst-facing narrative field.
            Defaults to "narrative".
        payload_fields: Field names to copy into
            ``ExtractionResult.source_payload`` for audit. Defaults to
            every non-score-non-narrative field on the schema.

    Returns:
        An :class:`Extractor` callable with signature
        ``(ticker, source_text, dim_id) -> ExtractionResult | None``.
        The returned callable raises ``ValueError`` only when the
        registered dim_id doesn't match the invocation dim_id (caller
        bug); LLM/validation failures return ``None`` with an error
        captured in ``ExtractionResult.reason``.
    """
    if payload_fields is None:
        payload_fields = [
            name for name in output_schema.model_fields
            if name not in (score_field, narrative_field)
        ]

    def _extract(
        ticker: str, source_text: str, invocation_dim_id: str,
    ) -> Optional[ExtractionResult]:
        if invocation_dim_id != dim_id:
            raise ValueError(
                f"Extractor for {dim_id!r} invoked with mismatched "
                f"dim_id={invocation_dim_id!r}"
            )
        if not source_text or not source_text.strip():
            return ExtractionResult(
                score=None,
                narrative="(no source text — extractor skipped)",
                source_payload={"reason": "empty_source_text"},
                input_fingerprint=_fingerprint_source_text(""),
                llm_provider=LLM_PROVIDER,
                llm_model=LLM_MODEL_NAME,
                reason="empty_source_text",
            )

        llm = ChatGoogleGenerativeAI(
            model=LLM_MODEL_NAME, temperature=LLM_TEMPERATURE,
        )
        structured_llm = llm.with_structured_output(output_schema)
        prompt = ChatPromptTemplate.from_template(prompt_template)
        chain = prompt | structured_llm

        invoke_args: dict = {
            "ticker":      ticker,
            "source_text": source_text,
        }
        last_error: Optional[Exception] = None
        result_obj: Optional[BaseModel] = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                result_obj = chain.invoke(invoke_args)
                break
            except (ValidationError, Exception) as exc:  # noqa: BLE001
                last_error = exc
                # On second attempt, include the validation error in the
                # prompt context so the LLM can self-correct. Same
                # pattern as qualitative_synthesis + thesis_synthesizer.
                err_str = str(exc)[:1000]
                retry_prompt = ChatPromptTemplate.from_template(
                    "The previous attempt failed validation with:\n"
                    f"{err_str}\n\nProduce a valid response.\n\n"
                    + prompt_template
                )
                chain = retry_prompt | structured_llm

        if result_obj is None:
            return ExtractionResult(
                score=None,
                narrative=f"(extraction failed: {type(last_error).__name__})",
                source_payload={"error": str(last_error)[:500]},
                input_fingerprint=_fingerprint_source_text(source_text),
                llm_provider=LLM_PROVIDER,
                llm_model=LLM_MODEL_NAME,
                reason=f"{type(last_error).__name__}: {str(last_error)[:200]}",
            )

        score_val = getattr(result_obj, score_field, None)
        narrative_val = getattr(result_obj, narrative_field, "") or ""
        payload = {
            field: getattr(result_obj, field, None)
            for field in payload_fields
        }
        return ExtractionResult(
            score=int(score_val) if score_val is not None else None,
            narrative=str(narrative_val)[:500],
            source_payload=payload,
            input_fingerprint=_fingerprint_source_text(source_text),
            llm_provider=LLM_PROVIDER,
            llm_model=LLM_MODEL_NAME,
            reason=None,
        )

    _extract.__name__ = f"extract_{dim_id}"
    _extract.__doc__ = f"LLM extractor for dimension {dim_id!r}."
    return _extract


__all__ = ["make_llm_extractor", "LLM_MODEL_NAME", "LLM_TEMPERATURE",
           "LLM_PROVIDER"]
