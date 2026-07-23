"""Phase 3.5 — composite per-field confidence score.

Combines the dimensions the numeric-confidence plan surfaced into ONE per-field
signal, so a number that *balances an identity* but is fabricated or
SEC-contradicted is visibly distinguished from one that is filing-verified:

  1. Source authority — raw (reported) / derived / missing      (provenance)
  2. Cross-source     — SEC filing agrees / near / drifts        (3.3 agreement)
  3. Fallback distance — was a constant substituted for a real 0? (Phase-0 instr.)

The point (report §06): *"identities balance" and "the number is trustworthy" are
different claims.* A book-weighted WACC balances no identity yet moves every IV;
a statutory-fallback tax rate breaks no identity yet is fabricated. The
fallback-distance dimension is what makes those visible.

Designed to AUGMENT the 🟢🟡🔴 dots (locked decision) — not replace them until the
score and the dot agree ≥95% of the time.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Ordinal levels, worst→best for the fabricated/suspect cases surfaced first.
LEVELS = ("fabricated", "missing", "suspect", "low", "medium", "high")


def field_confidence(
    *,
    provenance: Optional[str],
    cross_source_flag: Optional[str],
    fallback_applied: bool = False,
    identity_closed: Optional[bool] = None,
) -> Tuple[str, int, List[str]]:
    """Return ``(level, score_0_100, reasons)`` for one field.

    - ``provenance`` ∈ {raw, derived, missing, None}
    - ``cross_source_flag`` ∈ {validated, near, drift, sec_missing, ours_missing, None}
    - ``fallback_applied`` — a fallback constant was substituted for a real 0 /
      missing (the A1 fabrication; from the Phase-0 ``fallbacks_applied`` trace).
    - ``identity_closed`` — optional: the field satisfies the identity it
      participates in (small boost) / violates it (penalty). None = unknown.
    """
    reasons: List[str] = []

    # A fabricated value is the strongest signal — it's made up, regardless of
    # whether it happens to balance an identity or match a lone source.
    if fallback_applied:
        return "fabricated", 12, ["value substituted by a fallback constant"]

    if provenance in (None, "missing") or cross_source_flag == "ours_missing":
        return "missing", 0, ["no value"]

    score = 70 if provenance == "raw" else 50 if provenance == "derived" else 35
    reasons.append(f"source={provenance}")

    if cross_source_flag == "validated":
        score += 30
        reasons.append("SEC filing agrees (<1%)")
    elif cross_source_flag == "near":
        score += 10
        reasons.append("SEC filing near (<5%)")
    elif cross_source_flag == "drift":
        # Two independent sources disagree — the most actionable red flag.
        score = min(score, 35)
        reasons.append("SEC filing DISAGREES (>5%)")
    else:  # sec_missing / None
        reasons.append("no authoritative SEC cross-check")

    if identity_closed is True:
        score += 5
        reasons.append("identity closed")
    elif identity_closed is False:
        score = min(score, 40)
        reasons.append("identity violated")

    score = max(0, min(100, score))

    if cross_source_flag == "drift" or identity_closed is False:
        level = "suspect"
    elif score >= 90:
        level = "high"
    elif score >= 65:
        level = "medium"
    else:
        level = "low"
    return level, score, reasons


def build_confidence_map(
    fields: List[str],
    *,
    provenance: Dict[str, str],
    cross_source: Dict[str, Dict[str, Any]],
    fabricated_fields: Optional[set] = None,
    identity_closed: Optional[Dict[str, bool]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Per-field confidence for a record. Inputs are kept decoupled (each from
    its own layer) so the scorer stays pure and testable:

    - ``provenance``   — field → raw/derived/missing (CleanedRecord.get_with_provenance)
    - ``cross_source`` — field → {flag,…}            (build_cross_source_agreement, 3.3)
    - ``fabricated_fields`` — fields where a fallback constant fired (fallbacks_applied)
    - ``identity_closed`` — optional field → bool     (schema-contract / identity_checks)
    """
    fab = fabricated_fields or set()
    idc = identity_closed or {}
    out: Dict[str, Dict[str, Any]] = {}
    for f in fields:
        level, score, reasons = field_confidence(
            provenance=provenance.get(f),
            cross_source_flag=(cross_source.get(f) or {}).get("flag"),
            fallback_applied=(f in fab),
            identity_closed=idc.get(f),
        )
        out[f] = {"level": level, "score": score, "reasons": reasons}
    return out


def summarize(confidence_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Roll a per-field map into a record-level summary (histogram + mean +
    the fields that need attention)."""
    hist: Dict[str, int] = {}
    scores: List[int] = []
    attention: List[str] = []
    for f, c in confidence_map.items():
        hist[c["level"]] = hist.get(c["level"], 0) + 1
        scores.append(c["score"])
        if c["level"] in ("fabricated", "suspect"):
            attention.append(f)
    return {
        "levels": hist,
        "mean_score": round(sum(scores) / len(scores), 1) if scores else None,
        "needs_attention": sorted(attention),
        "n_fields": len(confidence_map),
    }
