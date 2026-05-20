"""Recompute orchestrator for LLM_AUGMENTED dimensions.

Sibling to ``aletheia.qualitative.runner`` (which handles
DETERMINISTIC dimensions). Iterates the extractor registry, runs each
against the appropriate 10-K section, and writes one AssessmentRecord
per (ticker, dimension_id) into ``qualitative_assessments``.

Idempotency rule mirrors the deterministic side: if the latest stored
row's ``input_fingerprint`` (SHA over the source text) matches the
freshly-computed fingerprint AND the ``code_git_sha`` is unchanged,
re-running the extractor would produce an identical row, so we skip
the write — and the LLM call. This is the cost-saving guard: a Stage 4
re-run on the same filing doesn't spend Gemini dollars repeating
extraction.

The runner does NOT decide which 10-K section feeds which extractor —
that's the caller's responsibility. The workflow node
(``aletheia.agents.qualitative_extraction``) is responsible for
section targeting; this module persists the result of the extraction
call without policy.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, List, Mapping, Optional

from aletheia.data.database import InvestmentDatabase
from aletheia.qualitative.extractors import EXTRACTORS, ExtractionResult
from aletheia.qualitative.runner import _GIT_SHA
from aletheia.qualitative.types import AssessmentRecord, SourceCategory


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _is_unchanged(
    prior: Optional[Dict[str, Any]],
    result: ExtractionResult,
    current_git_sha: Optional[str],
) -> bool:
    """Skip-write check — same shape as deterministic runner's
    ``_is_unchanged``. Compares input fingerprint + code SHA so a
    re-run on the same source text + same code produces no row churn
    and burns no LLM dollars."""
    if prior is None:
        return False
    return (
        prior.get("input_fingerprint") == result.input_fingerprint
        and prior.get("code_git_sha")  == current_git_sha
    )


def persist_extraction(
    *,
    ticker: str,
    dim_id: str,
    result: ExtractionResult,
    db: InvestmentDatabase,
) -> str:
    """Write an extraction result as an AssessmentRecord. Returns
    "written" or "unchanged" string describing the outcome.

    The caller (runner / workflow node) is responsible for catching
    failures upstream — this helper assumes ``result`` is the output of
    a successful extractor call. ``result.reason`` being set is NOT a
    failure signal; it's the extractor explaining why ``score`` is
    None (e.g. "empty_source_text"). The empty-state row gets persisted
    so the dashboard projection shows the gap explicitly.
    """
    prior = db.get_latest_assessment(ticker.upper(), dim_id)
    if _is_unchanged(prior, result, _GIT_SHA):
        return "unchanged"

    record = AssessmentRecord(
        assessment_id=str(uuid.uuid4()),
        ticker=ticker.upper(),
        dimension_id=dim_id,
        score=float(result.score) if result.score is not None else None,
        sub_scores=None,
        narrative=result.narrative,
        source_category=SourceCategory.LLM_AUGMENTED,
        source_payload={
            **result.source_payload,
            # Provenance trail — which model produced this. Aids
            # debugging when comparing extractions across Gemini /
            # future local LLM, and lets the UI show a "🤖 Gemini"
            # provenance pill.
            "llm_provider": result.llm_provider,
            "llm_model":    result.llm_model,
            "reason":       result.reason,
        },
        assessed_at=_now_iso(),
        analyst_id="system",
        code_git_sha=_GIT_SHA,
        input_fingerprint=result.input_fingerprint,
    )
    db.upsert_qualitative_assessment(record)
    return "written"


def run_extractors(
    *,
    ticker: str,
    section_texts: Mapping[str, str],
    db: Optional[InvestmentDatabase] = None,
) -> List[Dict[str, Any]]:
    """Run every registered extractor and persist results.

    Args:
        ticker: Upper-cased ticker symbol.
        section_texts: Maps ``dim_id`` → the 10-K section text that
            extractor should consume. The workflow node assembles this
            dict by slicing the librarian's full 10-K text into the
            relevant sections (Item 1 Competition, Item 1A Risk
            Factors, etc.).
        db: Optional pre-opened database connection (lets callers batch
            many tickers without repeated connect overhead).

    Returns:
        Per-extractor outcome dicts:
            {
                "dimension_id": str,
                "score":        int | None,
                "status":       "written" | "unchanged" | "no_data" | "error",
                "reason":       str | None,
            }
    """
    own_db = db is None
    if db is None:
        db = InvestmentDatabase(verbose=False)

    out: List[Dict[str, Any]] = []
    try:
        for dim_id, extractor in EXTRACTORS.items():
            source_text = section_texts.get(dim_id) or ""
            if not source_text.strip():
                out.append({
                    "dimension_id": dim_id,
                    "score":        None,
                    "status":       "no_data",
                    "reason":       "no_section_text_provided",
                })
                continue

            try:
                result = extractor(ticker.upper(), source_text, dim_id)
            except Exception as e:  # noqa: BLE001 — defensive boundary
                out.append({
                    "dimension_id": dim_id,
                    "score":        None,
                    "status":       "error",
                    "reason":       f"{type(e).__name__}: {str(e)[:200]}",
                })
                continue

            if result is None:
                out.append({
                    "dimension_id": dim_id,
                    "score":        None,
                    "status":       "no_data",
                    "reason":       "extractor_returned_none",
                })
                continue

            status = persist_extraction(
                ticker=ticker, dim_id=dim_id, result=result, db=db,
            )
            out.append({
                "dimension_id": dim_id,
                "score":        result.score,
                "status":       status,
                "reason":       result.reason,
            })
        return out
    finally:
        if own_db:
            db.close()


__all__ = ["persist_extraction", "run_extractors"]
