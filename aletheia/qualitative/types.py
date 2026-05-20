"""Typed primitives for the qualitative-analysis framework.

Two layers:

1. **Catalog types** (`SourceCategory`, `SubQuestion`, `QualitativeDimension`)
   describe the *structure* of dimensions. They live in code via
   `config.qualitative_dimensions` and are versioned through git.

2. **Record types** (`AssessmentRecord`) describe the *content* of a single
   submitted assessment. They live in the DuckDB `qualitative_assessments`
   table.

The split mirrors the architectural rule used throughout the codebase:
schema is code, content is data. A QualitativeDimension defines what an
assessment of that dimension means; an AssessmentRecord is the answer
captured at a point in time.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


class SourceCategory(str, enum.Enum):
    """How a dimension's score is produced.

    Values are strings (not auto-int) so they round-trip through JSON
    cleanly when stored in `qualitative_assessments.source_category`.
    """

    DETERMINISTIC = "deterministic"   # computed from DB rows by a pure function
    HITL          = "hitl"            # analyst-answered structured prompts
    LLM_AUGMENTED = "llm_augmented"   # extracted from filings (deferred)
    PENDING_DATA  = "pending_data"    # slot exists but data infra not wired


@dataclass(frozen=True)
class SubQuestion:
    """One question inside a HITL dimension's structured prompt.

    The analyst answers each sub-question with a 1-7 score; the dimension's
    composite is the weighted average across all sub-questions. Score
    anchors describe what each level means so the analyst calibrates
    consistently across sessions.

    Anchors are optional but strongly recommended — without them, a "5"
    on switching costs and a "5" on moat strength mean different things,
    which defeats the comparability rationale for a structured framework.
    """

    id: str                                      # stable identifier within the dimension
    text: str                                    # question shown to the analyst
    weight: float                                # contribution to the dimension composite (0-1)
    score_anchors: Dict[int, str] = field(default_factory=dict)  # {1: "...", 4: "...", 7: "..."}


@dataclass(frozen=True)
class QualitativeDimension:
    """The structural definition of one analytical dimension.

    Lives in the catalog (`config.qualitative_dimensions.DIMENSIONS`).
    `code_version` increments when bucket cutoffs, weights, or sub-questions
    change — prior assessments captured under the old version remain
    interpretable but auto-flag stale via `code_git_sha` mismatch in the UI.

    `staleness_days` is the time-based trigger; deterministic dimensions
    additionally flag stale when `input_fingerprint` changes (input rows
    were re-cleaned). HITL dimensions trigger on time only for week 1;
    "new filing landed" trigger is deferred.
    """

    id: str                                # snake_case stable identifier
    category: str                          # "quality" | "capital_allocation" | "competitive" | "risk" | "management"
    title: str                             # human-readable label for the UI
    source_category: SourceCategory
    staleness_days: int                    # > 0
    code_version: int = 1                  # bump when meaning changes
    description: str = ""                  # 1-2 sentence summary for the UI

    # HITL-only — empty for deterministic / pending / LLM dimensions
    questions: Tuple[SubQuestion, ...] = field(default_factory=tuple)

    # DETERMINISTIC-only — citation for the formula used by the computer
    formula_citation: str = ""

    def __post_init__(self):
        # Catalog-load assertions — caught at module import, not at
        # assessment time. Saves us from shipping a misweighted dimension.
        if self.source_category == SourceCategory.HITL:
            if not self.questions:
                raise ValueError(
                    f"Dimension {self.id!r} declared as HITL but has no questions"
                )
            total_weight = sum(q.weight for q in self.questions)
            if not (0.99 <= total_weight <= 1.01):  # float tolerance
                raise ValueError(
                    f"Dimension {self.id!r} sub-question weights sum to "
                    f"{total_weight:.4f}, must equal 1.0"
                )
        if self.source_category == SourceCategory.DETERMINISTIC:
            if not self.formula_citation:
                raise ValueError(
                    f"Dimension {self.id!r} declared as deterministic but "
                    f"has no formula_citation"
                )
        if self.staleness_days <= 0:
            raise ValueError(
                f"Dimension {self.id!r}: staleness_days must be positive"
            )

    def catalog_hash(self) -> str:
        """Stable hash over the structural fields that affect what an
        assessment of this dimension *means*. Used as part of the
        localStorage draft key — when this hash changes (e.g. a question
        is reworded), in-progress drafts cleanly invalidate."""
        payload = {
            "id":              self.id,
            "code_version":    self.code_version,
            "source_category": self.source_category.value,
            "questions": [
                {"id": q.id, "text": q.text, "weight": q.weight,
                 "anchors": q.score_anchors}
                for q in self.questions
            ],
            "formula_citation": self.formula_citation,
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]


@dataclass(frozen=True)
class AssessmentRecord:
    """One submitted assessment for one (ticker, dimension) pair.

    Persisted into `qualitative_assessments`. Versioned by `assessed_at`;
    the latest record is exposed via the `qualitative_assessments_latest`
    view.

    `score` is the composite 1-7 (or None for `pending_data`).
    `sub_scores` is HITL-specific — the per-question 1-7 inputs that
    composed the score.
    `source_payload` is category-specific:
      - DETERMINISTIC: {"inputs": {...db fields used...}, "formula": "<name>_v<n>"}
      - HITL:          {"prompts_version": str, "questions_answered": int}
      - LLM_AUGMENTED: {"document": "10-K FY25", "section": "Item 1A", "extracted_at": "..."}
      - PENDING_DATA:  {"reason": "DEF 14A parser not yet built"}
    """

    assessment_id: str          # UUID4
    ticker: str
    dimension_id: str
    score: Optional[float]
    sub_scores: Optional[Dict[str, float]]
    narrative: Optional[str]
    source_category: SourceCategory
    source_payload: Dict[str, Any]
    assessed_at: str            # ISO8601 UTC
    analyst_id: str             # "primary" by default; multi-analyst-ready
    code_git_sha: Optional[str]
    input_fingerprint: Optional[str]   # only set for DETERMINISTIC


__all__ = [
    "SourceCategory",
    "SubQuestion",
    "QualitativeDimension",
    "AssessmentRecord",
]
