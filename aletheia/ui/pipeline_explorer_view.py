"""Streamlit view: Stage Explorer — per-ticker pipeline depth.

Lets an analyst walk a single ticker through Stage 1 → 2 → 3 →
optional Stage 4, validating each stage's output before proceeding.
The primary tool for Phase B deep verification (KO / NVDA / ASML /
JPM / ABT walks) and for Category A/B/C/D triage when the identity
audit flags something.

Companion view: ``pipeline_status_view`` (universe matrix). The two
views deliberately do NOT share rendering code — see
``docs/pipeline_ui_design.md`` for the architectural division.

Commit-2 scope: scaffolding (run buttons + status + raw bundle JSON).
Commit-3 layers in the per-stage validation panels.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
import streamlit as st


API_BASE = "http://localhost:8000"
_REQUEST_TIMEOUT = 120


# ─────────────────────────────────────────────────────────────────────
# API client helpers
# ─────────────────────────────────────────────────────────────────────

def _api_post(path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """POST helper. Returns ``{"ok": True, "data": <json>}`` or
    ``{"ok": False, "error": <message>}``. Never raises — UI surfaces
    the error inline next to the button."""
    try:
        r = httpx.post(
            f"{API_BASE}{path}",
            json=body or {},
            timeout=_REQUEST_TIMEOUT,
        )
    except httpx.RequestError as exc:
        return {"ok": False, "error": f"Connection failed: {exc}"}
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:  # noqa: BLE001
            detail = r.text
        return {"ok": False, "error": f"{r.status_code}: {detail}"}
    return {"ok": True, "data": r.json()}


def _api_get(path: str) -> Dict[str, Any]:
    try:
        r = httpx.get(f"{API_BASE}{path}", timeout=_REQUEST_TIMEOUT)
    except httpx.RequestError as exc:
        return {"ok": False, "error": f"Connection failed: {exc}"}
    if r.status_code >= 400:
        return {"ok": False, "error": f"{r.status_code}: {r.text[:200]}"}
    return {"ok": True, "data": r.json()}


# ─────────────────────────────────────────────────────────────────────
# Session-state helpers
# ─────────────────────────────────────────────────────────────────────

def _state_key(ticker: str, stage: str, field: str) -> str:
    """Namespace session state by (ticker, stage, field) so the
    analyst can navigate away and back without losing the latest
    bundle for each stage."""
    return f"pipeline_explorer__{ticker}__{stage}__{field}"


def _store_bundle(ticker: str, stage: str, bundle: Any) -> None:
    st.session_state[_state_key(ticker, stage, "bundle")] = bundle
    st.session_state[_state_key(ticker, stage, "fetched_at")] = (
        datetime.utcnow().isoformat(timespec="seconds")
    )


def _read_bundle(ticker: str, stage: str) -> Optional[Any]:
    return st.session_state.get(_state_key(ticker, stage, "bundle"))


def _read_bundle_fetched_at(ticker: str, stage: str) -> Optional[str]:
    return st.session_state.get(_state_key(ticker, stage, "fetched_at"))


def _invalidate_downstream(ticker: str, busted_stage: str) -> None:
    """When the analyst re-runs an upstream stage, drop the cached
    downstream bundles for this ticker so the UI doesn't display
    stale lineage."""
    order = ["stage1_ingest", "stage2_validate", "stage3_calculate", "stage4_agents"]
    if busted_stage not in order:
        return
    idx = order.index(busted_stage)
    for downstream in order[idx + 1:]:
        st.session_state.pop(_state_key(ticker, downstream, "bundle"), None)
        st.session_state.pop(_state_key(ticker, downstream, "fetched_at"), None)


# ─────────────────────────────────────────────────────────────────────
# Status row lookup (drives the status badge on each card header)
# ─────────────────────────────────────────────────────────────────────

_STATUS_ICON = {
    "ok":                     "🟢",
    "skipped_cached":         "⚪",
    "running":                "🟡",
    "failed":                 "🔴",
    "skipped_dependency":     "⚫",
    "stale_due_to_override":  "🟠",
    "pending":                "⚫",
}


def _fetch_status_rows(ticker: str) -> Dict[str, Dict[str, Any]]:
    res = _api_get(f"/pipeline/status/{ticker}")
    if not res["ok"]:
        return {}
    rows = res["data"]
    return {row["stage"]: row for row in rows}


def _render_status_badge(stage_id: str, status_rows: Dict[str, Dict[str, Any]]) -> str:
    row = status_rows.get(stage_id)
    if not row:
        return f"{_STATUS_ICON['pending']} not run"
    icon = _STATUS_ICON.get(row["status"], "·")
    fp = (row.get("fingerprint") or "")[:12]
    fp_part = f"  fp={fp}…" if fp else ""
    dur = row.get("duration_seconds")
    dur_part = f"  {dur:.1f}s" if dur is not None else ""
    return f"{icon} {row['status']}{fp_part}{dur_part}"


# ─────────────────────────────────────────────────────────────────────
# Per-stage card renderers
# ─────────────────────────────────────────────────────────────────────

def _render_stage1(ticker: str, status_rows: Dict[str, Dict[str, Any]]) -> None:
    st.markdown(f"### Stage 1 — Ingest   ·   {_render_status_badge('stage1_ingest', status_rows)}")
    col1, col2, _ = st.columns([1, 1, 4])
    if col1.button("▶ Run", key=f"s1_run_{ticker}"):
        with st.spinner(f"Running Stage 1 for {ticker}…"):
            res = _api_post(
                f"/pipeline/stages/{ticker}/ingest",
                {"force_refresh": False, "include_market_snapshot": True},
            )
        if res["ok"]:
            _store_bundle(ticker, "stage1_ingest", res["data"])
            _invalidate_downstream(ticker, "stage1_ingest")
            st.rerun()
        else:
            st.error(res["error"])
    if col2.button("⟳ Force refresh", key=f"s1_force_{ticker}"):
        with st.spinner(f"Force-refreshing Stage 1 for {ticker}…"):
            res = _api_post(
                f"/pipeline/stages/{ticker}/ingest",
                {"force_refresh": True, "include_market_snapshot": True},
            )
        if res["ok"]:
            _store_bundle(ticker, "stage1_ingest", res["data"])
            _invalidate_downstream(ticker, "stage1_ingest")
            st.rerun()
        else:
            st.error(res["error"])

    bundle = _read_bundle(ticker, "stage1_ingest")
    if bundle:
        sources = bundle.get("sources", {})
        st.caption(
            f"sources fetched: **{len(sources)}**   "
            f"bundle_fingerprint: `{bundle.get('bundle_fingerprint', '')[:16]}…`   "
            f"last shown: {_read_bundle_fetched_at(ticker, 'stage1_ingest')}"
        )
        with st.expander("Raw bundle JSON", expanded=False):
            st.json(bundle)


def _render_stage2(ticker: str, status_rows: Dict[str, Dict[str, Any]]) -> None:
    st.markdown(f"### Stage 2 — Validate   ·   {_render_status_badge('stage2_validate', status_rows)}")
    col1, col2, _ = st.columns([1, 1, 4])
    if col1.button("▶ Run", key=f"s2_run_{ticker}"):
        with st.spinner(f"Running Stage 2 for {ticker}…"):
            res = _api_post(f"/pipeline/stages/{ticker}/validate", {})
        if res["ok"]:
            _store_bundle(ticker, "stage2_validate", res["data"])
            _invalidate_downstream(ticker, "stage2_validate")
            st.rerun()
        else:
            st.error(res["error"])
    if col2.button("⟳ Bust cache", key=f"s2_bust_{ticker}"):
        bust_res = _api_post(
            f"/pipeline/bust-cache/{ticker}", {"stages": ["stage2_validate"]},
        )
        if not bust_res["ok"]:
            st.error(bust_res["error"])
        else:
            st.success("Cache busted — Stage 2 + downstream will re-run on next ▶ Run.")
            _invalidate_downstream(ticker, "stage2_validate")

    bundle = _read_bundle(ticker, "stage2_validate")
    if bundle:
        n_records = len(bundle) if isinstance(bundle, list) else 0
        st.caption(
            f"validated records: **{n_records}**   "
            f"last shown: {_read_bundle_fetched_at(ticker, 'stage2_validate')}"
        )
        with st.expander("Raw records JSON", expanded=False):
            st.json(bundle)


def _render_stage3(ticker: str, status_rows: Dict[str, Dict[str, Any]]) -> None:
    st.markdown(f"### Stage 3 — Calculate   ·   {_render_status_badge('stage3_calculate', status_rows)}")
    col1, col2, _ = st.columns([1, 1, 4])
    if col1.button("▶ Run", key=f"s3_run_{ticker}"):
        with st.spinner(f"Running Stage 3 for {ticker}…"):
            res = _api_post(f"/pipeline/stages/{ticker}/calculate", {})
        if res["ok"]:
            _store_bundle(ticker, "stage3_calculate", res["data"])
            _invalidate_downstream(ticker, "stage3_calculate")
            st.rerun()
        else:
            st.error(res["error"])
    if col2.button("⟳ Bust cache", key=f"s3_bust_{ticker}"):
        bust_res = _api_post(
            f"/pipeline/bust-cache/{ticker}", {"stages": ["stage3_calculate"]},
        )
        if not bust_res["ok"]:
            st.error(bust_res["error"])
        else:
            st.success("Cache busted — Stage 3 + downstream will re-run on next ▶ Run.")
            _invalidate_downstream(ticker, "stage3_calculate")

    bundle = _read_bundle(ticker, "stage3_calculate")
    if bundle:
        dcf = bundle.get("dcf") or {}
        wacc = dcf.get("wacc_base") or dcf.get("wacc")
        wacc_str = f"{wacc * 100:.2f}%" if isinstance(wacc, (int, float)) else "—"
        violations = bundle.get("schema_violations") or []
        st.caption(
            f"base_period: **{bundle.get('base_period', '—')}**   "
            f"fiscal_year: **{bundle.get('fiscal_year', '—')}**   "
            f"WACC: **{wacc_str}**   schema_violations: **{len(violations)}**   "
            f"last shown: {_read_bundle_fetched_at(ticker, 'stage3_calculate')}"
        )
        with st.expander("Raw bundle JSON", expanded=False):
            st.json(bundle)


def _render_stage4(ticker: str, status_rows: Dict[str, Dict[str, Any]]) -> None:
    st.markdown(f"### Stage 4 — Agents   ·   {_render_status_badge('stage4_agents', status_rows)}")
    st.caption("⚠ Stage 4 invokes LLM agents — incurs ~$1-3 in API cost per run.")
    confirm = st.checkbox(
        "I confirm this will incur LLM cost",
        key=f"s4_confirm_{ticker}",
    )
    col1, _ = st.columns([2, 4])
    run_disabled = not confirm
    if col1.button("▶ Run with agents", key=f"s4_run_{ticker}", disabled=run_disabled):
        with st.spinner(f"Running Stage 4 (LLM) for {ticker}…"):
            res = _api_post(
                f"/pipeline/stages/{ticker}/agents",
                {"confirm_llm_cost": True},
            )
        if res["ok"]:
            _store_bundle(ticker, "stage4_agents", res["data"])
            st.rerun()
        else:
            st.error(res["error"])

    bundle = _read_bundle(ticker, "stage4_agents")
    if bundle:
        cost = bundle.get("llm_cost_usd")
        cost_str = f"${cost:.2f}" if cost is not None else "—"
        st.caption(
            f"llm_cost: **{cost_str}**   "
            f"bundle_fingerprint: `{bundle.get('bundle_fingerprint', '')[:16]}…`   "
            f"last shown: {_read_bundle_fetched_at(ticker, 'stage4_agents')}"
        )
        with st.expander("Raw bundle JSON", expanded=False):
            st.json(bundle)


# ─────────────────────────────────────────────────────────────────────
# Top-level page renderer
# ─────────────────────────────────────────────────────────────────────

def render_pipeline_explorer_view(ticker: str) -> None:
    """Render the Stage Explorer for ``ticker``. Called from
    ``streamlit_app.py`` when the user picks this view from the
    sidebar."""
    st.markdown(f"## ⚙ Pipeline Explorer — `{ticker}`")
    st.markdown(
        "Walk this ticker through the four pipeline stages. Each "
        "stage's typed output is captured for inspection. Re-run any "
        "stage to re-compute that stage + every downstream stage "
        "(cascade-invalidation)."
    )

    # ── Top-level controls ───────────────────────────────────────────
    col1, col2, _ = st.columns([1, 2, 4])
    if col1.button("▶ Run all (stages 1-3)", key=f"run_all_{ticker}"):
        with st.spinner(f"Running full pipeline for {ticker}…"):
            res = _api_post(
                f"/pipeline/stages/{ticker}/run",
                {"auto_agents": False, "include_market_snapshot": True},
            )
        if not res["ok"]:
            st.error(res["error"])
        else:
            outcome = res["data"]
            # Re-fetch each stage's bundle so the cards populate. The
            # orchestrator endpoint only returns OrchestratorResult
            # (status summary); the individual bundles come from the
            # per-stage endpoints. This keeps the orchestrator path
            # cheap and the explorer's view-cache deterministic.
            for stage_path, stage_id in [
                ("ingest", "stage1_ingest"),
                ("validate", "stage2_validate"),
                ("calculate", "stage3_calculate"),
            ]:
                if outcome["stages"].get(stage_id, {}).get("status") in {"ok", "skipped_cached"}:
                    fetch = _api_post(f"/pipeline/stages/{ticker}/{stage_path}", {})
                    if fetch["ok"]:
                        _store_bundle(ticker, stage_id, fetch["data"])
            st.success(
                f"Pipeline ran in {(outcome['finished_at']) if outcome.get('finished_at') else '?'}; "
                f"all_ok: {outcome.get('all_ok')}"
            )
            st.rerun()

    if col2.caption(""):  # alignment spacer
        pass

    # Fetch status rows once per render — drives the badge per card.
    status_rows = _fetch_status_rows(ticker)

    st.markdown("---")
    _render_stage1(ticker, status_rows)
    st.markdown("---")
    _render_stage2(ticker, status_rows)
    st.markdown("---")
    _render_stage3(ticker, status_rows)
    st.markdown("---")
    _render_stage4(ticker, status_rows)
