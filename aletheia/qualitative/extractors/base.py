"""Extractor protocol — types shared by every LLM_AUGMENTED extractor.

The contract:

  ``extract(ticker, source_text, dim_id) -> ExtractionResult | None``

mirrors the deterministic-computer contract
(``compute(df) -> ComputerResult | None``) so the persistence layer
treats the two source categories identically once the extractor has
run.

``ExtractionResult.source_payload`` is the structured byproduct of the
extraction — for example, the list of named competitors a competitor-
identification extractor produced, or the regulator/area pairs a
regulatory-exposure extractor surfaced. The dashboard projection in
``aletheia.qualitative.state_projection`` doesn't read these by default
(it reads only score + narrative), but they're persisted for audit and
for analyst review when overriding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


@dataclass(frozen=True)
class ExtractionResult:
    """One extractor's output for one (ticker, dimension) pair.

    Score is 1-7 or None when the extractor saw the source text but
    couldn't confidently score (rare — extractors should err on a
    middle score with a narrative caveat rather than return None).

    ``source_payload`` carries the structured data the extractor
    surfaced — schema is extractor-specific but stored as JSON in the
    DB. Examples:
      - competitor_identification: ``{"named_competitors": [...]}``
      - regulatory_exposure: ``{"material_exposures": [...]}``
      - customer_concentration: ``{"named_customers": [{"name": ...,
        "share_pct": ...}]}``

    ``input_fingerprint`` is a SHA-256 over the source text the
    extractor was given (truncated to 16 chars). Used the same way as
    the deterministic computer's fingerprint — when the source text
    changes (new filing), the fingerprint changes, a fresh row is
    written, and the dashboard projection picks it up.
    """

    score: Optional[int]                   # 1-7 or None when insufficient signal
    narrative: str                         # 1-500 chars, displayed in qualitative tab
    source_payload: Dict[str, Any]         # structured extraction byproduct
    input_fingerprint: str                 # SHA-256 over source text (16 chars)
    llm_provider: str                      # "gemini" / "local" — provenance trail
    llm_model: str                         # e.g. "gemini-3.1-pro-preview"
    reason: Optional[str] = None           # explanation when score is None


# Type alias for the extractor callable. Concrete extractors are
# usually built by ``llm_extractor.make_llm_extractor(prompt_template,
# schema)`` rather than written by hand.
Extractor = Callable[[str, str, str], Optional[ExtractionResult]]


__all__ = ["ExtractionResult", "Extractor"]
