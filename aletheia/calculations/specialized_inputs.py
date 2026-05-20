"""Loader for analyst-curated valuation inputs (rate base, allowed ROE,
DDM payout, embedded value parameters, etc.).

The unified config lives at ``config/specialized_valuation_inputs.json``.
This module exposes:

  - ``load_specialized_inputs(ticker)`` — returns a frozen
    ``SpecializedInputs`` dataclass or ``None`` if the ticker has no
    entry / model isn't supported yet.
  - ``get_all_specialized_tickers()`` — returns the set of tickers
    in the config. Used by the architecture-lock test to enforce
    completeness against ``ticker_classification`` (every
    routing_required / ddm_required / embedded_value_required
    ticker must have an entry).
  - ``staleness_days_remaining(inputs)`` — UI helper for the "next
    review" badge.

The file is read once + cached at module load. PR-reviewed; the
architecture-lock test re-reads to catch divergence.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Set


_CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "config" / "specialized_valuation_inputs.json"
)


@dataclass(frozen=True)
class SpecializedInputs:
    """Analyst-curated inputs for one non-FCFF ticker.

    ``params`` is intentionally a plain dict — the schema varies per
    model (rate_base needs different fields than ddm). Engine
    modules read the keys they need; the loader doesn't enforce a
    model-specific schema (that lives in the engine).

    ``has_required_inputs`` is the gate that engines check before
    running — if any required param is ``None`` (placeholder
    state), the engine returns a ``ValuationResult`` with
    ``intrinsic_per_share=None`` plus a warning, rather than
    fabricating a value.
    """

    ticker: str
    model: str                                # "rate_base" / "ddm" / "embedded_value"
    params: Dict[str, Any] = field(default_factory=dict)
    as_of_date: Optional[str] = None
    next_review_date: Optional[str] = None
    source: str = ""
    analyst_notes: str = ""

    def has_required_inputs(self, *required_keys: str) -> bool:
        """Returns True iff every required_keys entry exists in
        ``params`` AND is not None. Engines call this before
        running."""
        return all(
            self.params.get(k) is not None for k in required_keys
        )

    def days_until_review(self) -> Optional[int]:
        """Days remaining before ``next_review_date``. Negative
        means overdue. ``None`` when no next_review_date set."""
        if not self.next_review_date:
            return None
        try:
            d = datetime.date.fromisoformat(self.next_review_date)
            return (d - datetime.date.today()).days
        except (TypeError, ValueError):
            return None


@lru_cache(maxsize=1)
def _load_config() -> Dict[str, Any]:
    """Cached config-file read. ``lru_cache`` makes this safe to
    call repeatedly; test fixtures that need a fresh read can call
    ``_load_config.cache_clear()``."""
    if not _CONFIG_PATH.exists():
        return {"$schema_version": 0, "tickers": {}}
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_specialized_inputs(ticker: str) -> Optional[SpecializedInputs]:
    """Return analyst inputs for ``ticker`` or ``None`` when no
    entry exists. Engines call this and decide how to handle the
    None / placeholder cases."""
    cfg = _load_config()
    entry = cfg.get("tickers", {}).get(ticker.upper())
    if entry is None:
        return None
    return SpecializedInputs(
        ticker=ticker.upper(),
        model=entry.get("model", "unknown"),
        params=dict(entry.get("params") or {}),
        as_of_date=entry.get("as_of_date"),
        next_review_date=entry.get("next_review_date"),
        source=entry.get("source") or "",
        analyst_notes=entry.get("analyst_notes") or "",
    )


def get_all_specialized_tickers() -> Set[str]:
    """Every ticker with an entry in the config. Used by the
    architecture-lock test to assert that every classification with
    ``business_model in {routing_required, ddm_required,
    embedded_value_required}`` has a matching entry."""
    cfg = _load_config()
    return set(cfg.get("tickers", {}).keys())


__all__ = [
    "SpecializedInputs",
    "load_specialized_inputs",
    "get_all_specialized_tickers",
]
