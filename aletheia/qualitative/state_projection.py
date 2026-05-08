"""Pure-function projection of qualitative dashboard state.

Projects raw `qualitative_assessments_latest` rows + the dimension catalog
into the shape `thesis_synthesizer` consumes. No I/O — pass in the raw
records dict (from `InvestmentDatabase.get_all_assessments_for_ticker`)
plus the runtime git SHA, get back a structured dict suitable for direct
LangGraph-state injection.

Why a separate module:
  - `thesis_synthesizer` is AST-locked against importing `aletheia.data.*`,
    so the projection lives outside it.
  - `api_main.py` already implements the same status-resolution logic
    inline at `_assessment_status` / `_compute_composite_score`; this
    module is the canonical home and the API can migrate to it later.
  - Pure functions are testable without a DB.

Coverage-state buckets (locked):
  - high   : n_assessed >= 12 (of 16 assessable)
  - medium : 6 <= n_assessed <= 11
  - low    : 1 <= n_assessed <= 5
  - zero   : n_assessed == 0

Composite-staleness rule (locked Q2):
  Composite freshness = worst-of contributing-dimension freshness. If any
  contributor is stale, the composite is flagged stale. The contributor
  causing the staleness is named in `stale_contributors`.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any, Dict, List, Optional

from aletheia.qualitative.types import QualitativeDimension, SourceCategory


def status_for(
    catalog_entry: QualitativeDimension,
    latest_record: Optional[Dict[str, Any]],
    runtime_git_sha: Optional[str],
) -> str:
    """Resolve assessment status for one (catalog, record) pair.

    Returns one of: "pending_data", "not_assessed", "stale", "assessed".
    Mirrors `api_main.py:_assessment_status` exactly so the synthesizer
    sees the same status the dashboard UI shows.
    """
    if catalog_entry.source_category == SourceCategory.PENDING_DATA:
        return "pending_data"
    if latest_record is None:
        return "not_assessed"

    try:
        assessed_at = datetime.datetime.fromisoformat(latest_record["assessed_at"])
        if assessed_at.tzinfo is None:
            assessed_at = assessed_at.replace(tzinfo=datetime.timezone.utc)
        age_days = (datetime.datetime.now(datetime.timezone.utc) - assessed_at).days
        if age_days > catalog_entry.staleness_days:
            return "stale"
    except (TypeError, ValueError, KeyError):
        pass

    if catalog_entry.source_category == SourceCategory.DETERMINISTIC:
        stored_sha = latest_record.get("code_git_sha")
        if stored_sha and runtime_git_sha and stored_sha != runtime_git_sha:
            return "stale"

    return "assessed"


def project_dimensions(
    catalog: Dict[str, QualitativeDimension],
    records: Dict[str, Dict[str, Any]],
    runtime_git_sha: Optional[str],
) -> Dict[str, Dict[str, Any]]:
    """One row per catalog dimension. Records is keyed by dimension_id.

    Output shape (per dim):
        {
          "dimension_id":     str,
          "category":         str,
          "title":            str,
          "source_category":  "deterministic"|"hitl"|"llm_augmented"|"pending_data",
          "status":           "assessed"|"stale"|"not_assessed"|"pending_data",
          "score":            float | None,
          "narrative":        str | None,
          "assessed_at":      ISO8601 str | None,
          "staleness_days":   int,
          "catalog_hash":     str,
          "citable":          bool,        # status in {"assessed", "stale"}
        }
    """
    out: Dict[str, Dict[str, Any]] = {}
    for dim_id, dim in catalog.items():
        rec = records.get(dim_id)
        status = status_for(dim, rec, runtime_git_sha)
        score: Optional[float] = None
        if rec is not None and rec.get("score") is not None:
            try:
                score = float(rec["score"])
            except (TypeError, ValueError):
                score = None
        out[dim_id] = {
            "dimension_id":    dim_id,
            "category":        dim.category,
            "title":           dim.title,
            "source_category": dim.source_category.value,
            "status":          status,
            "score":           score,
            "narrative":       (rec.get("narrative") if rec else None),
            "assessed_at":     (rec.get("assessed_at") if rec else None),
            "staleness_days":  dim.staleness_days,
            "catalog_hash":    dim.catalog_hash(),
            "citable":         status in ("assessed", "stale"),
        }
    return out


def project_categories(
    catalog: Dict[str, QualitativeDimension],
    categories: tuple,
    category_weights_fn,
    dim_projection: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Per-category composite computed from the dimension projection.

    Composite = renormalized weighted average over assessed-or-stale
    members. PENDING_DATA members are excluded entirely (the catalog
    `category_weights_fn` already excludes them). Composite is None if
    no member is assessed-or-stale.

    Composite freshness = worst-of contributing-dim freshness. If any
    contributor is stale, the composite is flagged stale and
    `stale_contributors` names them.

    Output shape (per category):
        {
          "category_id":        str,
          "title":              str,
          "composite_score":    float | None,
          "n_assessed":         int,
          "n_total":            int,        # excludes PENDING_DATA
          "status":             "assessed"|"stale"|"not_assessed",
          "stale_contributors": List[str],
          "contributing":       List[{dimension_id, score, weight,
                                      renormalized_weight, contribution,
                                      status}],
        }
    """
    out: Dict[str, Dict[str, Any]] = {}
    for cat_id, cat_label in categories:
        weights = category_weights_fn(cat_id)
        n_total = len(weights)

        contributing: List[Dict[str, Any]] = []
        stale_contributors: List[str] = []
        for dim_id, weight in weights.items():
            d = dim_projection.get(dim_id) or {}
            if not d.get("citable"):
                continue
            if d["score"] is None:
                continue
            contributing.append({
                "dimension_id": dim_id,
                "score":        d["score"],
                "weight":       weight,
                "status":       d["status"],
            })
            if d["status"] == "stale":
                stale_contributors.append(dim_id)

        if contributing and n_total > 0:
            assessed_weight_total = sum(c["weight"] for c in contributing)
            if assessed_weight_total > 0:
                for c in contributing:
                    c["renormalized_weight"] = round(
                        c["weight"] / assessed_weight_total, 4
                    )
                    c["contribution"] = round(
                        c["score"] * c["renormalized_weight"], 4
                    )
                composite = sum(c["contribution"] for c in contributing)
                composite_score: Optional[float] = round(composite, 2)
            else:
                composite_score = None
        else:
            composite_score = None

        if composite_score is None:
            cat_status = "not_assessed"
        elif stale_contributors:
            cat_status = "stale"
        else:
            cat_status = "assessed"

        out[cat_id] = {
            "category_id":        cat_id,
            "title":              cat_label,
            "composite_score":    composite_score,
            "n_assessed":         len(contributing),
            "n_total":            n_total,
            "status":             cat_status,
            "stale_contributors": stale_contributors,
            "contributing":       contributing,
        }
    return out


# ── Coverage thresholds (locked D2) ──────────────────────────────────────

_COVERAGE_HIGH = 12
_COVERAGE_MEDIUM_MIN = 6


def coverage_summary(dim_projection: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Bucket the ticker's coverage state. Pure function over dim_projection.

    `n_assessable` counts non-PENDING_DATA dims (today: 16 of 19).
    `n_assessed` counts dims with status in {"assessed", "stale"} —
    stale dims still contribute to confidence but are flagged separately.
    """
    n_assessable = 0
    n_assessed = 0
    n_stale = 0
    n_not_assessed = 0
    n_pending = 0
    stale_paths: List[str] = []
    citable_paths: List[str] = []

    for dim_id, d in dim_projection.items():
        if d["source_category"] == "pending_data":
            n_pending += 1
            continue
        n_assessable += 1
        if d["status"] == "assessed":
            n_assessed += 1
            citable_paths.append(f"qualitative.{dim_id}")
        elif d["status"] == "stale":
            n_assessed += 1
            n_stale += 1
            stale_paths.append(f"qualitative.{dim_id}")
            citable_paths.append(f"qualitative.{dim_id}")
        else:
            n_not_assessed += 1

    if n_assessed >= _COVERAGE_HIGH:
        coverage_state = "high"
    elif n_assessed >= _COVERAGE_MEDIUM_MIN:
        coverage_state = "medium"
    elif n_assessed >= 1:
        coverage_state = "low"
    else:
        coverage_state = "zero"

    return {
        "n_assessable":   n_assessable,
        "n_assessed":     n_assessed,
        "n_stale":        n_stale,
        "n_not_assessed": n_not_assessed,
        "n_pending":      n_pending,
        "coverage_state": coverage_state,
        "stale_paths":    stale_paths,
        "citable_dim_paths": citable_paths,
    }


def dashboard_state_fingerprint(
    dim_projection: Dict[str, Dict[str, Any]],
) -> str:
    """16-char SHA-256 over the citable content of every assessed/stale dim.

    Used as a staleness key on thesis runs. When ANY assessed dim changes
    (score, narrative, assessed_at, code_git_sha for deterministic) the
    fingerprint changes, signalling "thesis is stale relative to current
    dashboard state." Pure function over the projection — no I/O.

    Includes only `citable=True` dims (assessed + stale). NOT_ASSESSED and
    PENDING_DATA dims contribute nothing — they couldn't be cited by the
    thesis, so changes to their absence don't invalidate it.

    Sorted iteration over dim_id keys → deterministic output across runs.
    """
    payload: List[Dict[str, Any]] = []
    for dim_id in sorted(dim_projection.keys()):
        d = dim_projection[dim_id]
        if not d.get("citable"):
            continue
        payload.append({
            "dim_id":       dim_id,
            "score":        d.get("score"),
            "narrative":    d.get("narrative"),
            "assessed_at":  d.get("assessed_at"),
            "status":       d.get("status"),
            "catalog_hash": d.get("catalog_hash"),
        })
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


__all__ = [
    "status_for",
    "project_dimensions",
    "project_categories",
    "coverage_summary",
    "dashboard_state_fingerprint",
]
