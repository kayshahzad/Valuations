"""Streamlit view: Stage Explorer — per-ticker pipeline depth.

Lets an analyst walk a single ticker through Stage 1 → 2 → 3 →
optional Stage 4, validating each stage's output before proceeding.
The primary tool for Phase B deep verification (KO / NVDA / ASML /
JPM / ABT walks) and for Category A/B/C/D triage when the identity
audit flags something.

Companion view: ``pipeline_status_view`` (universe matrix). The two
views deliberately do NOT share rendering code — see
``docs/pipeline_ui_design.md`` for the architectural division.

Commit-3 scope: per-stage validation panels surface "is this stage's
output trustworthy?" without requiring the analyst to drill into raw
JSON. Stage 1 surfaces source-list completeness + per-source size +
sha — answers "where's the XBRL data?" at a glance. Stage 2 surfaces
schema_violations + overrides + FMP cross-check receipt. Stage 3
surfaces WACC / IV / RDCF / screening + tax_rate_source + identity
checks. Stage 4 surfaces thesis structural completeness + LLM cost.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
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
# Validation-panel helpers — "is this stage's output trustworthy?"
# ─────────────────────────────────────────────────────────────────────

def _fmt_size(n_bytes: Optional[int]) -> str:
    if n_bytes is None:
        return "—"
    if n_bytes >= 1_000_000:
        return f"{n_bytes / 1e6:.1f} MB"
    if n_bytes >= 1_000:
        return f"{n_bytes / 1e3:.0f} KB"
    return f"{n_bytes} B"


def _fmt_pct(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_usd(v: Optional[float], unit: str = "") -> str:
    if v is None:
        return "—"
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(fv) >= 1e9:
        return f"${fv / 1e9:.2f}B{unit}"
    if abs(fv) >= 1e6:
        return f"${fv / 1e6:.1f}M{unit}"
    return f"${fv:,.0f}{unit}"


def _render_stage1_validation(bundle: Dict[str, Any]) -> None:
    """Stage 1 panel: source-list completeness + per-source size +
    sha + classification snapshot. Answers 'where's the XBRL data?'
    at a glance."""
    sources = bundle.get("sources") or {}
    total = len(sources)

    # Quick aggregates for the headline line.
    sec_present = "sec_companyfacts" in sources
    fmp_endpoints = sum(1 for k in sources if k.startswith("fmp_"))
    market_present = "market_snapshot" in sources
    st.markdown(
        f"**Sources fetched: {total}**  "
        f"·  SEC XBRL: {'✓' if sec_present else '✗'}  "
        f"·  FMP endpoints: {fmp_endpoints}  "
        f"·  Market snapshot: {'✓' if market_present else '—'}"
    )

    rows: List[Dict[str, Any]] = []
    for src_id, src in sources.items():
        path_str = src.get("payload_path") or ""
        size_bytes: Optional[int]
        try:
            size_bytes = Path(path_str).stat().st_size if path_str else None
        except OSError:
            size_bytes = None
        sha = (src.get("payload_sha256") or "")[:12]
        rows.append({
            "source": src_id,
            "size": _fmt_size(size_bytes),
            "sha (first 12)": f"{sha}…" if sha else "—",
            "fetched_at": (src.get("fetched_at") or "")[:19],
            "path": path_str,
        })
    if rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)

    cs = bundle.get("classification_snapshot") or {}
    if cs:
        st.caption(
            "Classification at fetch time — "
            f"sector: **{cs.get('sector', '—')}**   "
            f"industry: **{cs.get('industry', '—')}**   "
            f"lifecycle: **{cs.get('lifecycle', '—')}**   "
            f"business_model: **{cs.get('business_model', '—')}**   "
            f"is_ifrs_filer: **{cs.get('is_ifrs_filer', '—')}**"
        )


def _render_stage2_validation(records: List[Dict[str, Any]]) -> None:
    """Stage 2 panel: cleaned record count + quality score + schema
    violations + overrides + the FMP cross-check receipt. The
    overrides + FMP receipt are the high-signal items — they're
    where 'XBRL extracted, compared to FMP, here's the drift'
    surfaces."""
    if not records:
        st.caption("No cleaned records returned.")
        return

    fy_records = [r for r in records if r.get("period") == "FY"]
    quality_scores = [
        r.get("overall_quality_score") for r in fy_records
        if r.get("overall_quality_score") is not None
    ]
    avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else None
    total_violations = sum(
        len(r.get("validation", {}).get("schema_violations") or [])
        for r in records
    )
    overrides_active = sorted({
        o
        for r in records
        for o in (r.get("validation", {}).get("overrides_applied") or [])
    })

    cols = st.columns(4)
    cols[0].metric("FY records", len(fy_records))
    cols[1].metric("TTM records", len(records) - len(fy_records))
    cols[2].metric("avg quality", f"{avg_quality:.2f}" if avg_quality is not None else "—")
    cols[3].metric("schema violations", total_violations)

    if overrides_active:
        st.markdown("**Overrides applied** (these relax a schema check for documented reason):")
        for ovr_key in overrides_active:
            st.markdown(f"- `{ovr_key}`")
        st.caption(
            "Each override is an explicit exception captured in "
            "`aletheia/calculations/_overrides.py` — the cleaned data "
            "passes only because the registry permits the deviation."
        )
    else:
        st.caption("No overrides active. Schema contract passed without exceptions.")

    # FMP cross-check receipt from Gate A.TTM. Lives on the latest
    # record's validation.fmp_validation field.
    latest = max(records, key=lambda r: r.get("fiscal_year") or 0)
    fmp_receipt = (latest.get("validation") or {}).get("fmp_validation") or {}
    if fmp_receipt:
        with st.expander(
            f"FMP cross-check receipt (FY{latest.get('fiscal_year')}, "
            f"period={latest.get('period')})",
            expanded=False,
        ):
            st.json(fmp_receipt)
    else:
        st.caption(
            "Gate A.TTM receipt not present on the latest record — "
            "this is the cleaning-engine slot where XBRL-vs-FMP drift "
            "shows up. Empty here is expected when fmp_validation "
            "hasn't been wired through the typed contract yet (Week-5 "
            "follow-up)."
        )


def _render_stage3_validation(bundle: Dict[str, Any]) -> None:
    """Stage 3 panel: WACC, intrinsic value spread, RDCF implied,
    multiple decomposition signal, screening counts, moat score,
    tax_rate_source. The tax_rate_source value is the immediate
    answer to 'did A11 produce a plausible rate?' for this ticker."""
    dcf = bundle.get("dcf") or {}
    rdcf = bundle.get("reverse_dcf") or {}
    md = bundle.get("multiple_decomposition") or {}
    screening = bundle.get("screening") or {}
    moat = bundle.get("moat_fingerprint") or {}
    violations = bundle.get("schema_violations") or []

    # Top row: WACC + intrinsic values.
    wacc = dcf.get("wacc") or dcf.get("wacc_base")
    cols = st.columns(4)
    cols[0].metric("WACC", _fmt_pct(wacc, decimals=2))
    # DCFResult.to_dict exposes wacc_base at the top level; per-
    # scenario intrinsic values live in nested base/bull/bear blocks.
    base = dcf.get("base") or {}
    bull = dcf.get("bull") or {}
    bear = dcf.get("bear") or {}

    def _per_share(d: Dict[str, Any]) -> Optional[float]:
        return d.get("intrinsic_per_share") if isinstance(d, dict) else None

    cols[1].metric("IV base", _fmt_usd(_per_share(base)))
    cols[2].metric("IV bull", _fmt_usd(_per_share(bull)))
    cols[3].metric("IV bear", _fmt_usd(_per_share(bear)))

    # ReverseDCF + MD + screening row.
    cols = st.columns(4)
    cols[0].metric(
        "RDCF implied 10Y CAGR",
        _fmt_pct(rdcf.get("implied_cagr_10y") or rdcf.get("implied_revenue_cagr_10y")),
    )
    cols[1].metric(
        "MD signal", md.get("signal") or "—",
    )
    cols[2].metric(
        "Screening", (
            f"{screening.get('passes', '?')}✓ "
            f"{screening.get('flags', '?')}⚠ "
            f"{screening.get('fails', '?')}✗"
        ),
    )
    moat_score = moat.get("score")
    cols[3].metric(
        "Moat score",
        f"{moat_score:.1f}/10" if isinstance(moat_score, (int, float)) else "—",
    )

    # Tax-rate-source surfacing — this is the immediate "did A11
    # land a plausible rate or fall through to statutory?" view.
    tax_sources_seen = []
    for sub_name, sub in (("dcf", dcf), ("reverse_dcf", rdcf),
                          ("multiple_decomposition", md)):
        src = sub.get("tax_rate_source") if isinstance(sub, dict) else None
        if src:
            tax_sources_seen.append((sub_name, src))
    if tax_sources_seen:
        chips = "  ·  ".join(
            f"{name}: `{src}`" for name, src in tax_sources_seen
        )
        st.caption(f"tax_rate_source — {chips}")

    # Calc-layer schema violations (output sanity failures).
    if violations:
        with st.expander(
            f"Calc-layer schema violations ({len(violations)})",
            expanded=False,
        ):
            for v in violations:
                engine = v.get("engine", "?")
                category = v.get("category", "?")
                msg = v.get("message", "")
                st.markdown(f"- **{engine}** · `{category}` · {msg}")
    else:
        st.caption("No calc-layer schema violations.")


def _render_stage4_validation(bundle: Dict[str, Any]) -> None:
    """Stage 4 panel: thesis structural completeness, cited_signals,
    contrarian sentiment, qualitative reports count, LLM cost."""
    qs = bundle.get("qualitative_synthesis") or {}
    contrarian = bundle.get("contrarian") or {}
    thesis = bundle.get("thesis") or {}

    cols = st.columns(4)
    cols[0].metric(
        "Thesis present",
        "✓" if thesis else "—",
    )

    def _cited_signals_count(d: Dict[str, Any]) -> int:
        total = 0
        for k in ("bull", "base", "bear"):
            sec = d.get(k) or {}
            signals = sec.get("cited_signals") if isinstance(sec, dict) else None
            if isinstance(signals, list):
                total += len(signals)
        return total

    cols[1].metric("Cited signals", _cited_signals_count(thesis))
    cols[2].metric(
        "Qualitative reports",
        f"{sum(1 for k in ('forensic_report', 'value_chain_report', 'strategic_context_report') if k in qs)}/3",
    )
    llm_cost = bundle.get("llm_cost_usd")
    cols[3].metric(
        "LLM cost",
        f"${llm_cost:.2f}" if isinstance(llm_cost, (int, float)) else "—",
    )

    if contrarian:
        sentiment = contrarian.get("sentiment") or contrarian.get("bias", "—")
        bear_case = contrarian.get("bear_case")
        st.caption(
            f"Contrarian sentiment: **{sentiment}**   ·   "
            f"bear case: {'present' if bear_case else '—'}"
        )

    if bundle.get("raw_10k_excerpt"):
        excerpt_len = len(bundle["raw_10k_excerpt"])
        st.caption(f"10-K excerpt captured ({excerpt_len:,} chars).")


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
        st.caption(
            f"bundle_fingerprint: `{bundle.get('bundle_fingerprint', '')[:16]}…`   "
            f"last shown: {_read_bundle_fetched_at(ticker, 'stage1_ingest')}"
        )
        _render_stage1_validation(bundle)
        with st.expander("Raw bundle JSON (full payload)", expanded=False):
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
        _render_stage2_validation(bundle if isinstance(bundle, list) else [])
        with st.expander("Raw records JSON (full payload)", expanded=False):
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
        st.caption(
            f"base_period: **{bundle.get('base_period', '—')}**   "
            f"fiscal_year: **{bundle.get('fiscal_year', '—')}**   "
            f"bundle_fingerprint: `{bundle.get('bundle_fingerprint', '')[:16]}…`   "
            f"last shown: {_read_bundle_fetched_at(ticker, 'stage3_calculate')}"
        )
        _render_stage3_validation(bundle)
        with st.expander("Raw bundle JSON (full payload)", expanded=False):
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
        st.caption(
            f"bundle_fingerprint: `{bundle.get('bundle_fingerprint', '')[:16]}…`   "
            f"last shown: {_read_bundle_fetched_at(ticker, 'stage4_agents')}"
        )
        _render_stage4_validation(bundle)
        with st.expander("Raw bundle JSON (full payload)", expanded=False):
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
