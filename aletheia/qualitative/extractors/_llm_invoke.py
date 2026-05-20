"""Shared LLM-call-with-retry helper for bundle extractors.

Both Phase B (10-K bundle covering 3 dims) and Phase C (DEF 14A bundle
covering 2 management dims) share this primitive. Each bundle factory
provides:

  - The Pydantic schema to enforce (passed to ``with_structured_output``)
  - The prompt template (with ``{ticker}`` and ``{source_text}`` placeholders)
  - The LLM factory (default Gemini; tests inject mocks)

The helper handles:
  - Two-attempt retry on validation failure (matching qualitative_synthesis
    and thesis_synthesizer)
  - Empty / whitespace source text → early return without LLM call
  - Failure-after-retries → returns ``None`` so caller can produce
    bundle-specific failure results

Source-text fingerprinting + result fan-out stay in the per-bundle
module because they're bundle-specific.
"""

from __future__ import annotations

import hashlib
from typing import Callable, Optional, Type, TypeVar

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ValidationError


# Same retry budget all narrative + extraction agents use.
MAX_RETRIES: int = 2


T = TypeVar("T", bound=BaseModel)


def fingerprint_source_text(source_text: str) -> str:
    """16-char SHA-256 over the source text.

    Bundle outputs share this fingerprint across their fanned-out
    results so the runner's idempotency guard treats all bundle dims
    as a single unit — re-running on the same source skips all of
    them or persists all of them; never partial.
    """
    return hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:16]


def invoke_with_retry(
    *,
    llm_factory: Callable[[], object],
    schema: Type[T],
    prompt_template: str,
    ticker: str,
    source_text: str,
) -> tuple[Optional[T], Optional[Exception]]:
    """Call the LLM with structured output + retry on validation
    failure. Returns ``(bundle, error)`` where exactly one is set:

      - ``(bundle, None)`` on success (possibly after retry)
      - ``(None, last_exception)`` after exhausting retries

    Empty source text returns ``(None, ValueError("empty_source_text"))``
    without invoking the LLM — caller checks this and produces
    appropriate failure results.
    """
    if not source_text or not source_text.strip():
        return None, ValueError("empty_source_text")

    llm = llm_factory()
    structured_llm = llm.with_structured_output(schema)
    prompt = ChatPromptTemplate.from_template(prompt_template)
    chain = prompt | structured_llm

    invoke_args: dict = {
        "ticker":      ticker,
        "source_text": source_text,
    }

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = chain.invoke(invoke_args)
            return result, None
        except (ValidationError, Exception) as exc:  # noqa: BLE001 boundary
            last_error = exc
            err_str = str(exc)[:1000]
            # Prepend error to prompt for the model to self-correct
            retry_prompt = ChatPromptTemplate.from_template(
                "The previous attempt failed validation with:\n"
                f"{err_str}\n\nProduce a valid response.\n\n"
                + prompt_template
            )
            chain = retry_prompt | structured_llm

    return None, last_error


__all__ = ["invoke_with_retry", "fingerprint_source_text", "MAX_RETRIES"]
