"""Deterministic computers for the qualitative-analysis framework.

Each computer is a pure function:

    compute(df: pd.DataFrame) -> ComputerResult | None

where `df` is the cleaned-records DataFrame for a single ticker (all
years, sorted by `fiscal_year`). The result includes:
  - `score`: 1-7 integer or None when insufficient data
  - `source_payload`: dict capturing the inputs and the formula version
    used (rendered as JSON in the DB)
  - `input_fingerprint`: hash of the underlying rows so the staleness
    check can detect re-cleans
  - `reason`: optional string for the UI when score is None

Returning None is the signal "computer ran but couldn't produce a score
for this ticker" — distinct from "computer wasn't run." The caller
(runner.py) writes the assessment regardless, so the empty state is
explicit in the DB rather than inferred from absence.

When a computer's bucket cutoffs or formula change, bump the
`code_version` field in the catalog entry; assessments captured under
the old version remain interpretable but auto-flag stale via
`code_git_sha` mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import pandas as pd


@dataclass(frozen=True)
class ComputerResult:
    score: Optional[int]                     # 1-7 or None when insufficient data
    source_payload: Dict[str, Any]           # inputs + formula identifier
    input_fingerprint: str                   # SHA-256 over the input rows used
    reason: Optional[str] = None             # explanation for None score


from .roiic_trend            import compute_roiic_trend
from .buyback_discipline     import compute_buyback_discipline
from .dividend_policy        import compute_dividend_policy
from .cyclicality            import compute_cyclicality
from .industry_concentration import compute_industry_concentration


# Registry: dimension_id -> computer function.
# `runner.recompute_deterministic` iterates this to produce one
# AssessmentRecord per (ticker, dimension_id) pair.
COMPUTERS: Dict[str, Callable[[pd.DataFrame], Optional[ComputerResult]]] = {
    "roiic_trend":            compute_roiic_trend,
    "buyback_discipline":     compute_buyback_discipline,
    "dividend_policy":        compute_dividend_policy,
    "cyclicality":            compute_cyclicality,
    "industry_concentration": compute_industry_concentration,
}


__all__ = ["ComputerResult", "COMPUTERS"]
