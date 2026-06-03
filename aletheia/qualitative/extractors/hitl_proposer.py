"""LLM proposer for HITL dimensions.

Phase 1 of the HITL auto-fill workflow. Reads the 10-K (Item 1 + Item 1A
+ Item 7) plus the catalog's structured prompts and proposes sub-scores,
narratives, and confidence ratings for every HITL dimension. Analysts
then review and adjust — provenance + review-state tracking lets
subsequent LLM runs compute a drift report without overwriting analyst
edits.

Workflow:

    hitl_proposer(ticker, source_text)
        ↓ ChatGoogleGenerativeAI + with_structured_output(HitlProposalBundle)
        ↓ retry once on validation failure
    HitlProposalBundle
        ↓ build_assessment_records()
    {dim_id: AssessmentRecord}   (9 entries, one per HITL dim)
        ↓ db.upsert_qualitative_assessment (per record)

One LLM call covers all 9 HITL dimensions (~38 sub-questions). Cost:
~$0.40-0.60 per ticker via Gemini 3.1 Pro. The bundle keeps the
auto-fill economy in line with the existing 10-K extraction bundle.

Composite score per dim is computed from sub_scores using catalog
weights — the LLM does NOT propose composites directly. This keeps the
score consistent with how the analyst would compute it manually in the
HITL input form.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from aletheia.qualitative.extractors._llm_invoke import (
    fingerprint_source_text as _fingerprint,
    invoke_with_retry,
)
from aletheia.qualitative.extractors.llm_extractor import (
    LLM_MODEL_NAME, LLM_TEMPERATURE, LLM_PROVIDER,
)
from aletheia.qualitative.runner import _GIT_SHA
from aletheia.qualitative.types import AssessmentRecord, SourceCategory


# ── Pydantic schema ────────────────────────────────────────────────


class EvidenceQuote(BaseModel):
    """One quote from the filing supporting a sub-score."""
    question_id: str = Field(
        description="ID of the sub-question this quote supports."
    )
    quote: str = Field(
        description="Direct quote from the 10-K (≤ 200 chars). No paraphrasing.",
        max_length=200,
    )
    source: str = Field(
        description="Document section: e.g. '10-K Item 1', '10-K Item 1A', '10-K Item 7'."
    )


class HitlDimProposal(BaseModel):
    """One LLM-proposed HITL dim. sub_scores keys must match the
    catalog's question IDs for the dimension; values are 1-7 integers."""

    dimension_id: str = Field(
        description="Catalog dimension ID (e.g. 'moat_strength', 'pricing_power')."
    )
    sub_scores: Dict[str, int] = Field(
        description=(
            "Per-sub-question score 1-7. Keys MUST match the catalog's "
            "question IDs exactly. Use 1/4/7 anchors as calibration points."
        )
    )
    narrative: str = Field(
        description=(
            "2-3 sentence rationale tying scores to filing evidence. "
            "≤ 500 chars."
        ),
        max_length=500,
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description=(
            "Self-rated confidence in the proposal. "
            "'high' = filing has explicit, quantifiable evidence. "
            "'medium' = inferential from disclosure language. "
            "'low' = sparse disclosure, analyst should re-score."
        )
    )
    evidence_quotes: List[EvidenceQuote] = Field(
        default_factory=list,
        description="Citations from the filing. ≥ 1 per sub-score is ideal.",
    )


class HitlProposalBundle(BaseModel):
    """All HITL proposals from one LLM call."""
    proposals: List[HitlDimProposal] = Field(
        description="One entry per HITL dimension in the catalog (9 total)."
    )


# ── Prompt construction ───────────────────────────────────────────


def _build_catalog_block() -> str:
    """Build the catalog spec for prompt injection — every HITL dim's
    questions + score anchors. Done once per process; the catalog is
    static so caching is safe."""
    from config.qualitative_dimensions import DIMENSIONS

    lines: List[str] = []
    for dim_id, dim in DIMENSIONS.items():
        if dim.source_category != SourceCategory.HITL:
            continue
        lines.append(f"\n=== {dim_id} — {dim.title} ===")
        lines.append(f"Description: {dim.description}")
        lines.append("Sub-questions (sub_scores keys MUST match these IDs):")
        for q in dim.questions:
            lines.append(f"  - {q.id} (weight {q.weight:.2f}): {q.text}")
            for anchor_score in sorted(q.score_anchors.keys()):
                anchor_text = q.score_anchors[anchor_score]
                lines.append(f"      score {anchor_score} = {anchor_text}")
    return "\n".join(lines)


_PROMPT_TEMPLATE = """You are a senior equity analyst auto-filling qualitative
dimension scores for {ticker} based on its SEC filings. For each HITL
dimension in the catalog below, propose 1-7 sub-scores using the score
anchors as calibration, a 2-3 sentence narrative tying scores to filing
evidence, a confidence rating, and direct quotes supporting the scores.

Critical rules:
1. sub_scores keys MUST exactly match the catalog's question IDs.
2. Use the 1/4/7 anchors as the calibration scale — interpolate for 2/3/5/6.
3. Never inflate scores when the filing is silent on a dimension. Use
   confidence="low" instead. The analyst will adjust.
4. Quote directly from the filing (≤200 chars per quote). No paraphrasing.
5. Output ALL 9 HITL dimensions even if confidence is low.

Catalog (9 HITL dimensions, ~38 sub-questions total):
{catalog_block}

Filing source text (10-K Item 1 + Item 1A + Item 7):
{source_text}
"""


def _make_prompt() -> str:
    """Inject the catalog block once and return the full template.

    The catalog block grows the prompt by ~3-4KB but it's static. The
    source_text placeholder accepts the per-ticker 10-K extract."""
    catalog_block = _build_catalog_block()
    return _PROMPT_TEMPLATE.format(
        ticker="{ticker}",  # leave LangChain placeholder intact
        catalog_block=catalog_block,
        source_text="{source_text}",
    )


# ── Validation against the catalog ────────────────────────────────


def _validate_against_catalog(
    proposal: HitlDimProposal,
) -> Tuple[Optional[float], Optional[str]]:
    """Compute composite score from sub_scores using catalog weights.
    Returns ``(composite, error_message)``.

    Errors flagged:
      - dimension_id not in catalog
      - sub_scores keys don't match catalog questions
      - any sub-score outside [1, 7]
    """
    from config.qualitative_dimensions import DIMENSIONS
    entry = DIMENSIONS.get(proposal.dimension_id)
    if entry is None or entry.source_category != SourceCategory.HITL:
        return None, f"dimension_id {proposal.dimension_id!r} not in HITL catalog"

    expected_ids = {q.id for q in entry.questions}
    got_ids = set(proposal.sub_scores.keys())
    missing = expected_ids - got_ids
    extra = got_ids - expected_ids
    if missing or extra:
        return None, (
            f"sub_scores mismatch for {proposal.dimension_id!r}: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )

    composite = 0.0
    for q in entry.questions:
        v = proposal.sub_scores[q.id]
        if not (1 <= v <= 7):
            return None, (
                f"{proposal.dimension_id}.{q.id} score {v} out of [1,7]"
            )
        composite += float(v) * q.weight
    # Clamp to catalog bounds for safety against float drift
    composite = max(1.0, min(7.0, round(composite, 2)))
    return composite, None


# ── Build AssessmentRecords ───────────────────────────────────────


def build_assessment_records(
    bundle: HitlProposalBundle,
    ticker: str,
    fingerprint: str,
) -> Dict[str, AssessmentRecord]:
    """Convert proposals into AssessmentRecord objects keyed by dim_id.

    Each record is persisted with:
      - source_category = HITL (matches catalog declaration)
      - analyst_id      = 'llm_proposer'
      - provenance      = 'llm_proposed'
      - review_state    = 'unreviewed'
      - llm_proposal    = the raw proposal snapshot (preserved across edits)

    Invalid proposals (composite=None) become empty-state records — the
    UI's "Awaiting analyst" badge surfaces them honestly.
    """
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    out: Dict[str, AssessmentRecord] = {}
    for p in bundle.proposals:
        composite, err = _validate_against_catalog(p)
        proposal_snapshot = {
            "sub_scores":      p.sub_scores,
            "narrative":       p.narrative,
            "confidence":      p.confidence,
            "evidence_quotes": [eq.model_dump() for eq in p.evidence_quotes],
            "llm_provider":    LLM_PROVIDER,
            "llm_model":       LLM_MODEL_NAME,
            "validation_error": err,
        }
        out[p.dimension_id] = AssessmentRecord(
            assessment_id=str(uuid.uuid4()),
            ticker=ticker.upper(),
            dimension_id=p.dimension_id,
            score=composite,
            sub_scores=(
                {k: float(v) for k, v in p.sub_scores.items()}
                if composite is not None else None
            ),
            narrative=(p.narrative if composite is not None else
                       f"(validation error: {err[:200]})" if err else None),
            source_category=SourceCategory.HITL,
            source_payload={
                "extractor":   "hitl_proposer_v1",
                "llm_provider": LLM_PROVIDER,
                "llm_model":   LLM_MODEL_NAME,
            },
            assessed_at=now,
            analyst_id="llm_proposer",
            code_git_sha=_GIT_SHA,
            input_fingerprint=fingerprint,
            provenance="llm_proposed",
            review_state="unreviewed",
            confidence=p.confidence,
            llm_proposal=proposal_snapshot,
        )
    return out


# ── Public factory ────────────────────────────────────────────────


HitlProposer = Callable[[str, str], Dict[str, AssessmentRecord]]


def make_hitl_proposer(
    *,
    llm_factory: Optional[Callable[[], object]] = None,
) -> HitlProposer:
    """Build the HITL proposer callable. Same factory pattern as
    bundle_extractor — production callers pass nothing and get the
    default Gemini construction; tests inject a mock."""
    if llm_factory is None:
        llm_factory = lambda: ChatGoogleGenerativeAI(
            model=LLM_MODEL_NAME, temperature=LLM_TEMPERATURE,
        )

    prompt_template = _make_prompt()

    def _propose(
        ticker: str, source_text: str,
    ) -> Dict[str, AssessmentRecord]:
        fingerprint = _fingerprint(source_text or "")
        bundle, last_error = invoke_with_retry(
            llm_factory=llm_factory,
            schema=HitlProposalBundle,
            prompt_template=prompt_template,
            ticker=ticker,
            source_text=source_text,
        )
        if bundle is None:
            # Failed after retries — return empty dict; caller decides
            # whether to log + skip persistence.
            return {}
        return build_assessment_records(bundle, ticker, fingerprint)

    _propose.__name__ = "hitl_proposer"
    _propose.__doc__ = (
        "HITL auto-fill proposer — proposes scores for all 9 HITL dims in "
        "one bundled Gemini call. Returns {dim_id: AssessmentRecord}."
    )
    return _propose


__all__ = [
    "make_hitl_proposer",
    "HitlProposer",
    "build_assessment_records",
    "HitlDimProposal",
    "HitlProposalBundle",
    "EvidenceQuote",
]
