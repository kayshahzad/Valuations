"""qualitative_extraction_node — runs LLM_AUGMENTED extractors over 10-K text.

Sits in the LangGraph workflow between ``calc_node`` and
``qualitative_synthesis``. For every dimension with catalog
``source_category = LLM_AUGMENTED``, this node produces an
``AssessmentRecord`` via the appropriate extractor and persists it
into ``qualitative_assessments`` so the downstream
``dashboard_fetch_node`` sees fresh values.

Two extractor paths the node knows about:

  1. **Bundle path** (Phase B — current). One Gemini call returns all
     three top-level LLM_AUGMENTED dims in a single
     ``QualitativeExtractionBundle``. Saves cost — Stage 4 adds 1
     LLM call total (4 happy-path vs 3 before), not 3. Source text
     is the librarian's full ``raw_10k_text`` (Item 1 + Item 1A).

  2. **Per-dim registry path** (Phase A foundation, kept for future).
     ``EXTRACTORS`` registry maps dim_id → single-dim extractor.
     Reserved for Phase C (DEF 14A management dims) and any future
     extractor whose source filing differs from the 10-K — that's
     a separate LLM call by necessity.

Failure mode: if extraction raises, the node logs and continues.
Downstream agents fall back to whatever's in the DB (potentially
stale), matching the dashboard_fetch_node's "service unavailable
shouldn't block the pipeline" policy.
"""

from __future__ import annotations

import traceback
from typing import Any, Dict, List, Mapping


# Maps dim_id → 10-K section identifier for the PER-DIM registry path
# (Phase A foundation). The bundle path doesn't use this — it reads
# the full raw_10k_text in one go.
_DIM_TO_SECTION: Dict[str, str] = {
    # Reserved for future per-dim extractors. Empty in Phase B because
    # all three LLM_AUGMENTED dims go through the bundle path.
}


def _slice_section(raw_10k_text: str, section: str) -> str:
    """Pull one 10-K section from the librarian's concatenated string.

    Used only by the per-dim registry path. The bundle extractor takes
    the full ``raw_10k_text`` and lets the prompt do its own targeting.

    The librarian produces text shaped like::

        ITEM 1: BUSINESS
        <item 1 content...>

        ITEM 1A: RISK FACTORS
        <item 1a content...>
    """
    if not raw_10k_text:
        return ""

    markers = {
        "item_1":  "ITEM 1: BUSINESS",
        "item_1a": "ITEM 1A: RISK FACTORS",
    }
    marker = markers.get(section)
    if marker is None:
        return raw_10k_text

    start = raw_10k_text.find(marker)
    if start < 0:
        return raw_10k_text

    body_start = start + len(marker)
    next_marker = raw_10k_text.find("\n\nITEM ", body_start)
    if next_marker < 0:
        return raw_10k_text[body_start:].strip()
    return raw_10k_text[body_start:next_marker].strip()


def _build_section_texts(
    raw_10k_text: str,
    section_map: Mapping[str, str] = _DIM_TO_SECTION,
) -> Dict[str, str]:
    """Per-dim path helper — slice ``raw_10k_text`` once per section
    and reuse across dims that share a section. Empty when the
    section_map is empty (Phase B default)."""
    cache: Dict[str, str] = {}
    out: Dict[str, str] = {}
    for dim_id, section in section_map.items():
        if section not in cache:
            cache[section] = _slice_section(raw_10k_text, section)
        out[dim_id] = cache[section]
    return out


def qualitative_extraction_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """LangGraph node — extract LLM_AUGMENTED dims from filings.

    Reads:  ``state["ticker"]``, ``state["raw_10k_text"]``,
            ``state["raw_def14a_text"]``
    Writes: ``state["qualitative_extraction_results"]`` (list of
            per-dim outcome dicts for observability/logging)
    Side-effect: appends rows to ``qualitative_assessments``.

    Order of operations:
      1. **10-K bundle** (Phase B) — one LLM call against Item 1 +
         Item 1A → 3 dims (competitor_identification,
         regulatory_exposure, customer_concentration).
      2. **DEF 14A bundle** (Phase C) — one LLM call against proxy
         statement → 2 management dims (tenure_continuity,
         alignment).
      3. **Per-dim registry** (Phase A foundation; reserved for
         future non-bundle extractors).

    Total LLM calls: 2 (one per bundle, both run in parallel-shape
    sequence). Stage 4 cost: 5 calls happy-path (3 narrative agents
    + 2 extraction bundles).
    """
    print("---QUALITATIVE EXTRACTION NODE---")

    ticker = (state.get("ticker") or "").upper()
    raw_10k = state.get("raw_10k_text") or ""
    raw_def14a = state.get("raw_def14a_text") or ""

    if not ticker:
        return {"qualitative_extraction_results": []}

    from aletheia.qualitative.extractors import (
        BUNDLE_DIMS, DEF14A_BUNDLE_DIMS, EXTRACTORS,
    )

    have_10k_bundle = bool(BUNDLE_DIMS)
    have_def14a_bundle = bool(DEF14A_BUNDLE_DIMS)
    have_per_dim = bool(EXTRACTORS)

    if not (have_10k_bundle or have_def14a_bundle or have_per_dim):
        print("  (no extractors registered — foundation phase)")
        return {"qualitative_extraction_results": []}

    results: List[Dict[str, Any]] = []

    # ── 10-K bundle path (Phase B) ─────────────────────────────────
    if have_10k_bundle:
        if not raw_10k.strip():
            print(f"  ⚠ {ticker}: no 10-K text — skipping 10-K bundle")
            results.extend([
                {"dimension_id": dim_id, "status": "no_data",
                 "reason": "no_10k_text"}
                for dim_id in BUNDLE_DIMS
            ])
        else:
            try:
                results.extend(_run_10k_bundle(ticker, raw_10k))
            except Exception as exc:
                print(f"  ⚠ 10-K bundle failed for {ticker}: {exc}")
                traceback.print_exc()
                results.extend([
                    {"dimension_id": dim_id, "status": "error",
                     "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
                    for dim_id in BUNDLE_DIMS
                ])

    # ── Bottom-up business extraction (memo §4, Phase 2) ───────────
    # One structured call against the 10-K already in hand → themes A+B
    # (product/contracts, TAM/share). Cached to disk; read on the free
    # path by business_analysis. Never fails the node.
    if raw_10k.strip():
        try:
            from aletheia.agents.business_extraction import extract_business_ab
            company = state.get("company") or state.get("company_name") or ""
            ba = extract_business_ab(ticker, company, raw_10k, force=True)
            results.append({"dimension_id": "business_ab",
                            "status": "ok" if ba else "no_data"})
        except Exception as exc:
            print(f"  ⚠ business_ab extraction failed for {ticker}: {exc}")
            results.append({"dimension_id": "business_ab", "status": "error",
                            "reason": f"{type(exc).__name__}: {str(exc)[:200]}"})

    # ── HITL proposer (Phase 1 of HITL auto-fill) ──────────────────
    # Reuses the 10-K text from the bundle above. One LLM call
    # proposes scores for all 9 HITL dimensions. Analyst-overridden
    # / analyst-adjusted records are preserved automatically.
    if raw_10k.strip():
        try:
            results.extend(_run_hitl_proposer(ticker, raw_10k))
        except Exception as exc:
            print(f"  ⚠ HITL proposer failed for {ticker}: {exc}")
            traceback.print_exc()
            # Don't enumerate dims here — the proposer is the single
            # source of truth for which dims it tried. An error means
            # zero rows were touched.

    # ── DEF 14A bundle path (Phase C) ──────────────────────────────
    if have_def14a_bundle:
        if not raw_def14a.strip():
            print(f"  ⚠ {ticker}: no DEF 14A text — skipping proxy bundle")
            results.extend([
                {"dimension_id": dim_id, "status": "no_data",
                 "reason": "no_def14a_text"}
                for dim_id in DEF14A_BUNDLE_DIMS
            ])
        else:
            try:
                results.extend(_run_def14a_bundle(ticker, raw_def14a))
            except Exception as exc:
                print(f"  ⚠ DEF 14A bundle failed for {ticker}: {exc}")
                traceback.print_exc()
                results.extend([
                    {"dimension_id": dim_id, "status": "error",
                     "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
                    for dim_id in DEF14A_BUNDLE_DIMS
                ])

    # ── Per-dim registry path (Phase A foundation / future) ────────
    if have_per_dim:
        try:
            section_texts = _build_section_texts(raw_10k)
            from aletheia.qualitative.extraction_runner import run_extractors
            results.extend(run_extractors(
                ticker=ticker, section_texts=section_texts,
            ))
        except Exception as exc:
            print(f"  ⚠ per-dim extraction failed for {ticker}: {exc}")
            traceback.print_exc()
            results.extend([
                {"dimension_id": dim_id, "status": "error",
                 "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
                for dim_id in EXTRACTORS
            ])

    n_written = sum(1 for r in results if r["status"] == "written")
    n_unchanged = sum(1 for r in results if r["status"] == "unchanged")
    n_error = sum(1 for r in results if r["status"] == "error")
    n_no_data = sum(1 for r in results if r["status"] == "no_data")
    print(
        f"  ✓ {ticker}: {n_written} written, {n_unchanged} unchanged, "
        f"{n_no_data} no_data, {n_error} errored "
        f"({len(results)} dims total)"
    )
    return {"qualitative_extraction_results": results}


def _run_bundle_generic(
    ticker: str,
    source_text: str,
    extractor_factory,
) -> List[Dict[str, Any]]:
    """Shared bundle-run plumbing — invoke the extractor, persist
    each result, return outcomes for the workflow node's log.

    Both the 10-K bundle (Phase B) and the DEF 14A bundle (Phase C)
    route through here. The factory + result shape differ; the
    persistence flow is identical.
    """
    from aletheia.data.database import InvestmentDatabase
    from aletheia.qualitative.extraction_runner import persist_extraction

    extractor = extractor_factory()   # default Gemini factory inside
    bundle_results = extractor(ticker, source_text)

    outcomes: List[Dict[str, Any]] = []
    db = InvestmentDatabase(verbose=False)
    try:
        for dim_id, result in bundle_results.items():
            status = persist_extraction(
                ticker=ticker, dim_id=dim_id, result=result, db=db,
            )
            outcomes.append({
                "dimension_id": dim_id,
                "score":        result.score,
                "status":       status,
                "reason":       result.reason,
            })
    finally:
        db.close()
    return outcomes


def _run_10k_bundle(ticker: str, raw_10k: str) -> List[Dict[str, Any]]:
    """Phase B — 10-K bundle covering 3 dims."""
    from aletheia.qualitative.extractors.bundle_extractor import (
        make_bundle_extractor,
    )
    return _run_bundle_generic(ticker, raw_10k, make_bundle_extractor)


def _run_def14a_bundle(ticker: str, raw_def14a: str) -> List[Dict[str, Any]]:
    """Phase C — DEF 14A bundle covering 2 management dims."""
    from aletheia.qualitative.extractors.def14a_bundle_extractor import (
        make_def14a_bundle_extractor,
    )
    return _run_bundle_generic(ticker, raw_def14a, make_def14a_bundle_extractor)


# ── HITL proposer (Phase 1 of HITL auto-fill) ──────────────────────


def _run_hitl_proposer(
    ticker: str, raw_10k: str,
) -> List[Dict[str, Any]]:
    """One bundled LLM call proposes scores for all 9 HITL dimensions
    (moat_strength, pricing_power, brand_strength, switching_costs,
    capital_allocation_track_record, reinvestment_opportunity,
    market_position, competitive_trajectory, technology_disruption_risk).

    Different from the 10-K extraction bundle in two ways:
      1. Returns AssessmentRecord objects with provenance already
         stamped — the proposer's job is to produce reviewable HITL
         proposals, not raw extractions.
      2. Persistence honors analyst-overridden records: when the
         existing row has provenance in {"analyst_adjusted",
         "analyst_overridden"}, we DO NOT overwrite. The Phase 4
         drift-report layer (separate) computes the divergence between
         the new proposal and the persisted analyst version.
    """
    from aletheia.qualitative.extractors.hitl_proposer import (
        make_hitl_proposer,
    )
    from aletheia.qualitative.runner import _GIT_SHA
    from aletheia.data.database import InvestmentDatabase

    propose = make_hitl_proposer()
    record_by_dim = propose(ticker, raw_10k)

    outcomes: List[Dict[str, Any]] = []
    if not record_by_dim:
        return outcomes

    db = InvestmentDatabase(verbose=False)
    try:
        for dim_id, rec in record_by_dim.items():
            prior = db.get_latest_assessment(ticker.upper(), dim_id)

            # Honor analyst overrides — never overwrite analyst work,
            # but persist a drift-snapshot of the new LLM proposal in
            # ``llm_proposal_latest`` so the UI can render a "LLM
            # disagrees" banner.
            if prior and prior.get("provenance") in (
                "analyst_adjusted", "analyst_overridden",
            ):
                _write_drift_snapshot(db, prior, rec)
                outcomes.append({
                    "dimension_id": dim_id,
                    "score":        rec.score,
                    "status":       "drift_recorded",
                    "reason":       f"prior={prior.get('provenance')}; "
                                    f"new LLM proposal stored as drift snapshot",
                })
                continue

            # Idempotency: skip when source fingerprint + git SHA match
            # the prior LLM proposal — re-running on the same 10-K
            # doesn't burn a write or re-propose.
            if (prior
                    and prior.get("provenance") == "llm_proposed"
                    and prior.get("input_fingerprint") == rec.input_fingerprint
                    and prior.get("code_git_sha") == _GIT_SHA):
                outcomes.append({
                    "dimension_id": dim_id,
                    "score":        rec.score,
                    "status":       "unchanged",
                    "reason":       None,
                })
                continue

            # New proposal becomes the latest record. Also stamp it as
            # the drift baseline so the UI's drift comparison treats
            # this proposal as the reference until the analyst reviews.
            rec_with_latest = _attach_latest_snapshot(rec)
            db.upsert_qualitative_assessment(rec_with_latest)
            outcomes.append({
                "dimension_id": dim_id,
                "score":        rec.score,
                "status":       "written",
                "reason":       None,
            })
    finally:
        db.close()
    return outcomes


def _attach_latest_snapshot(rec):
    """Set llm_proposal_latest = llm_proposal so freshly-proposed records
    have zero drift between proposal and latest. Returns a new
    AssessmentRecord via dataclasses.replace (frozen dataclass)."""
    import dataclasses
    return dataclasses.replace(rec, llm_proposal_latest=rec.llm_proposal)


def _write_drift_snapshot(db, prior_record_dict, new_proposal_record) -> None:
    """Persist a new LLM proposal as a drift snapshot on the analyst's
    existing record. We rewrite the latest row (insert new versioned
    row identical to prior, with updated ``llm_proposal_latest_json``).
    Analyst's score / sub_scores / narrative are preserved exactly.

    Updating in place keeps the analyst's review state untouched —
    no need for them to re-review; the banner just appears when they
    next open the qualitative tab."""
    import datetime, uuid, dataclasses
    from aletheia.qualitative.runner import _GIT_SHA
    from aletheia.qualitative.types import AssessmentRecord, SourceCategory

    # Reconstitute prior as AssessmentRecord (its source_category came
    # through as a string from the DB read).
    sc = prior_record_dict.get("source_category")
    if isinstance(sc, str):
        sc = SourceCategory(sc)

    refreshed = AssessmentRecord(
        assessment_id=str(uuid.uuid4()),
        ticker=prior_record_dict["ticker"],
        dimension_id=prior_record_dict["dimension_id"],
        score=prior_record_dict.get("score"),
        sub_scores=prior_record_dict.get("sub_scores"),
        narrative=prior_record_dict.get("narrative"),
        source_category=sc,
        source_payload=prior_record_dict.get("source_payload") or {},
        assessed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        analyst_id=prior_record_dict.get("analyst_id", "primary"),
        code_git_sha=_GIT_SHA,
        input_fingerprint=prior_record_dict.get("input_fingerprint"),
        provenance=prior_record_dict.get("provenance"),
        review_state=prior_record_dict.get("review_state"),
        confidence=prior_record_dict.get("confidence"),
        llm_proposal=prior_record_dict.get("llm_proposal"),
        llm_proposal_latest=new_proposal_record.llm_proposal,
    )
    db.upsert_qualitative_assessment(refreshed)


__all__ = ["qualitative_extraction_node"]
