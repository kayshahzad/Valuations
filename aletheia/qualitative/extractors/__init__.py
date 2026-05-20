"""LLM-augmented extractors for the qualitative-analysis framework.

Mirrors the `aletheia.qualitative.computers` pattern but for dimensions
sourced from filing-text extraction (catalog `source_category =
LLM_AUGMENTED`) rather than deterministic computation over DB rows.

Each extractor is a callable:

    extract(ticker: str, source_text: str, dim_id: str) -> ExtractionResult | None

where `source_text` is the relevant 10-K section (Item 1 Competition,
Item 1A Risk Factors, etc. — the caller targets the section before
invoking the extractor to keep prompt size manageable).

Returning `None` means "extractor ran but couldn't produce a score for
this ticker" — distinct from "extractor wasn't run." The caller writes
the assessment regardless, so the empty state is explicit in the DB.

The LLM provider is acquired via the central `aletheia.llm` factory
(today: Gemini only; the local-LLM plan adds DeepSeek when wired). All
extractors share the same provider — no per-extractor provider override.
"""

from __future__ import annotations

from typing import Dict

from aletheia.qualitative.extractors.base import (
    ExtractionResult,
    Extractor,
)


# Registry: dimension_id → extractor function. The single-dim path
# (one LLM call per dim) is kept for future DEF 14A and other per-
# filing extractors in Phase C.
#
# Phase B uses the consolidated BUNDLE_EXTRACTOR (one LLM call → three
# dims) instead — see ``bundle_extractor.make_bundle_extractor`` and
# ``BUNDLE_DIMS`` below. The workflow node prefers the bundle path
# when the dims it wants overlap with BUNDLE_DIMS, falling back to
# the single-dim registry for everything else.
EXTRACTORS: Dict[str, Extractor] = {}


# Dimensions handled by the 10-K consolidated bundle extractor
# (Phase B). Adding a new dim here must be paired with extending
# ``QualitativeExtractionBundle`` in ``schemas.py`` and the prompt.
BUNDLE_DIMS: tuple = (
    "competitor_identification",
    "regulatory_exposure",
    "customer_concentration",
)


# Dimensions handled by the DEF 14A (proxy statement) consolidated
# bundle extractor (Phase C). Same pattern as BUNDLE_DIMS but a
# different source filing → separate LLM call.
DEF14A_BUNDLE_DIMS: tuple = (
    "management_tenure_continuity",
    "management_alignment",
)


__all__ = [
    "ExtractionResult", "Extractor", "EXTRACTORS",
    "BUNDLE_DIMS", "DEF14A_BUNDLE_DIMS",
]
