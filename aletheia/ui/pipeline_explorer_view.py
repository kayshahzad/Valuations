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


_CLEANING_EFFECT_TOLERANCE = 0.005  # 0.5% — matches FMP-compare ok-tier


def _classify_cleaning_effect(
    raw_v: Optional[float], clean_v: Optional[float],
) -> str:
    """How the cleaning engine altered the raw XBRL value.

    Mirrors FieldComparison.cleaning_effect on the backend dataclass
    so the UI can run the same classification without round-tripping
    through the API.

      "passthrough"  raw ≈ cleaned within 0.5%
      "adjusted"     raw and cleaned differ materially (sign / unit /
                     FX / NCI / derived adjustment)
      "raw_only"     only raw value present (cleaning didn't materialise)
      "cleaned_only" only cleaned present (derived / multi-tag aggregate)
      "none"         both unavailable
    """
    if raw_v is None and clean_v is None:
        return "none"
    if raw_v is None:
        return "cleaned_only"
    if clean_v is None:
        return "raw_only"
    denom = max(abs(raw_v), abs(clean_v), 1.0)
    if abs(raw_v - clean_v) / denom <= _CLEANING_EFFECT_TOLERANCE:
        return "passthrough"
    return "adjusted"


def _cleaning_effect_chip(effect: str) -> str:
    """Single-character chip rendered after the Cleaned value in
    the comparison table. Empty for passthrough so the common case
    doesn't add visual noise."""
    return {
        "passthrough":  "",
        "adjusted":     " ~",
        "raw_only":     "",
        "cleaned_only": " ⤴︎",
        "none":         "",
    }.get(effect, "")


_DRIFT_TIER_ICON = {
    "ok":         "🟢",
    "minor":      "🟡",
    "notable":    "🟠",
    "material":   "🔴",
    "incomplete": "⚫",
}


def _render_stage2_fmp_comparison(payload: Dict[str, Any]) -> None:
    """XBRL-vs-FMP side-by-side comparison. Renders one expander per
    period_label (historical FYs first, then current-year quarters).
    Each expander groups rows by Income Statement / Balance Sheet /
    Cash Flow categories.

    Reads the comparison payload from the caller — both this renderer
    and ``_render_stage2_xbrl_extracted`` are driven by the same
    payload to keep the period axis consistent across views.
    """
    period_labels: List[str] = payload.get("period_labels") or []
    cells = payload.get("cells") or []
    if not cells:
        st.caption(
            "No FMP comparison data — likely the FMP cache files "
            "for this ticker aren't on disk yet. Run Stage 1 with "
            "`--force-refresh` to repopulate FMP endpoints."
        )
        return

    # Distribution headline: how many cells in each drift tier.
    tier_counts: Dict[str, int] = {}
    for cell in cells:
        t = cell.get("tier", "incomplete")
        tier_counts[t] = tier_counts.get(t, 0) + 1
    summary_chips = "  ·  ".join(
        f"{_DRIFT_TIER_ICON.get(t, '·')} {t} {n}"
        for t, n in sorted(tier_counts.items(), key=lambda kv: -kv[1])
    )
    st.markdown(
        f"**XBRL ↔ FMP comparison** — {len(cells)} cells across "
        f"{len(period_labels)} periods · drift distribution: {summary_chips}"
    )
    st.caption(
        "🟢 ≤ 0.5% drift   🟡 ≤ 2%   🟠 ≤ 5%   🔴 > 5%   ⚫ one side missing"
    )

    # Group cells by (fiscal_year, period) so historical FYs and
    # current-year quarters each get their own expander. The period
    # axis comes from the comparison payload's ``period_labels``,
    # which the backend ordered chronologically (oldest FY → newest
    # FY → current-year quarters Q1..Q4).
    cells_by_period: Dict[tuple, List[Dict[str, Any]]] = {}
    for cell in cells:
        key = (cell["fiscal_year"], cell.get("period") or "FY")
        cells_by_period.setdefault(key, []).append(cell)

    from aletheia.pipeline._field_catalog import CATEGORIES

    # Render most-recent period first — analyst's usual focus is the
    # in-progress quarter, then the last completed FY, then history.
    ordered_keys = sorted(
        cells_by_period.keys(),
        key=lambda k: (k[0], 0 if k[1] == "FY" else int(k[1][1:])),
        reverse=True,
    )
    latest_key = ordered_keys[0] if ordered_keys else None

    for key in ordered_keys:
        fy, period = key
        period_label = f"FY{fy}" if period == "FY" else f"FY{fy} {period}"
        with st.expander(
            f"{period_label} — XBRL ↔ FMP",
            expanded=(key == latest_key),
        ):
            cells_in_period = cells_by_period[key]
            for category in CATEGORIES:
                cat_cells = [c for c in cells_in_period if c.get("category") == category]
                if not cat_cells:
                    continue
                st.markdown(f"**{category}**")
                rows: List[Dict[str, Any]] = []
                for cell in cat_cells:
                    raw_v = cell.get("xbrl_raw_value")
                    clean_v = cell.get("xbrl_cleaned_value")
                    fmp = cell.get("fmp_value")
                    drift_pct = cell.get("drift_pct")
                    tier = cell.get("tier", "incomplete")
                    effect = _classify_cleaning_effect(raw_v, clean_v)
                    rows.append({
                        "": _DRIFT_TIER_ICON.get(tier, "·"),
                        "Field": cell.get("field_label", ""),
                        "Raw XBRL": _fmt_usd(raw_v),
                        "Cleaned": _fmt_usd(clean_v) + _cleaning_effect_chip(effect),
                        "FMP": _fmt_usd(fmp),
                        "drift": _fmt_pct(drift_pct, decimals=2) if drift_pct is not None else "—",
                        "tier": tier,
                    })
                st.dataframe(rows, hide_index=True, use_container_width=True)
            st.caption(
                "Cleaned column annotations:   ~ cleaning altered raw "
                "(sign / unit / FX / NCI / derived adjustment)  ·  "
                "⤴︎ cleaned didn't materialise — raw XBRL only  ·  "
                "✗ unavailable in any source"
            )


def _render_stage2_xbrl_extracted(payload: Dict[str, Any]) -> None:
    """Render the XBRL-extracted financials per period, grouped by
    Income Statement / Balance Sheet / Cash Flow.

    Period axis (driven by the comparison payload):
       FY2021 · FY2022 · FY2023 · FY2024 · FY2025  ← historical annuals
       FY2026 Q1 · FY2026 Q2 · ...                  ← current-year quarters

    Reads the same comparison payload as ``_render_stage2_fmp_compare``
    to keep the period axis consistent across both views. The XBRL
    column shows whichever value the resolver landed (cleaned dict
    or raw-XBRL fallback); the ``xbrl_source`` flag is rendered on
    the side-by-side panel below.
    """
    from aletheia.pipeline._field_catalog import CATEGORIES

    period_labels: List[str] = payload.get("period_labels") or []
    cells = payload.get("cells") or []
    if not period_labels or not cells:
        st.caption(
            "No periods available — neither cleaned records nor "
            "FMP cache files yield a comparable period axis."
        )
        return

    # Build a lookup: (period_label, field_label) → xbrl_value.
    by_period_field: Dict[tuple, Optional[float]] = {}
    for cell in cells:
        fy = cell.get("fiscal_year")
        period = cell.get("period") or "FY"
        plabel = f"FY{fy}" if period == "FY" else f"FY{fy} {period}"
        by_period_field[(plabel, cell.get("field_label"))] = cell.get("xbrl_value")

    st.markdown(
        "**Extracted from XBRL** — historical annuals (FY) plus the "
        "current in-progress year's quarters. Quarterly values come "
        "from raw SEC XBRL directly (cleaning currently materialises "
        "FY only)."
    )

    # Field labels in catalog order, grouped by category. Filter to
    # rows where at least one period has a non-None xbrl_value.
    catalog_fields = list(payload.get("fields") or [])
    if not catalog_fields:
        # Fallback when the payload's ``fields`` is empty.
        seen = []
        for cell in cells:
            label = cell.get("field_label")
            if label and label not in seen:
                seen.append(label)
        catalog_fields = seen

    # Need per-field category from catalog payload — derive by walking
    # the cells.
    field_meta: Dict[str, str] = {}
    for cell in cells:
        label = cell.get("field_label")
        if label and label not in field_meta:
            field_meta[label] = cell.get("category", "")

    any_rendered = False
    for category in CATEGORIES:
        cat_fields = [
            label for label in catalog_fields
            if field_meta.get(label) == category
        ]
        if not cat_fields:
            continue
        rows: List[Dict[str, Any]] = []
        for label in cat_fields:
            row: Dict[str, Any] = {"Field": label}
            any_present = False
            for plabel in period_labels:
                v = by_period_field.get((plabel, label))
                row[plabel] = _fmt_usd(v) if v is not None else "—"
                if v is not None:
                    any_present = True
            if any_present:
                rows.append(row)
        if not rows:
            continue
        any_rendered = True
        st.markdown(f"#### {category}")
        st.dataframe(rows, hide_index=True, use_container_width=True)

    if not any_rendered:
        st.caption(
            "No catalog fields resolved across any category — "
            "likely a tag_resolver gap for this filer's XBRL "
            "taxonomy. Inspect the raw XBRL at "
            "`valuation_data/raw/sec/companyfacts/CIK*.json`."
        )
        return

    st.caption(
        "Historical FY columns mostly come from `record.clean` "
        "(cleaning + tag_resolver). Current-year quarter columns "
        "come from the raw SEC companyfacts JSON via the period-"
        "aware fallback (10-Q fp=Q1/Q2/Q3 lookup). The side-by-"
        "side comparison below tags each cell's source explicitly."
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

    # Layer-1 tier-C critical violations (block Stage 3 if present)
    from aletheia.calculations._schema_contract import (
        is_tier_c_violation,
    )
    tier_c_violations = [
        v
        for r in records
        for v in (r.get("validation", {}).get("schema_violations") or [])
        if is_tier_c_violation(v)
    ]

    cols = st.columns(5)
    cols[0].metric("FY records", len(fy_records))
    cols[1].metric("TTM records", len(records) - len(fy_records))
    cols[2].metric("avg quality", f"{avg_quality:.2f}" if avg_quality is not None else "—")
    cols[3].metric("Schema violations", total_violations)
    cols[4].metric("🚫 Tier-C (block S3)", len(tier_c_violations))

    if tier_c_violations:
        st.error(
            f"**Stage 3 will refuse to run.** {len(tier_c_violations)} "
            "Tier-C schema-contract violation(s) detected — truly invalid "
            "states (A ≠ L + E or missing Tier-1 field). Add an entry to "
            "`aletheia/calculations/_overrides.OVERRIDES` to waive if "
            "this is a documented edge case."
        )
        with st.expander(
            f"🚫 Tier-C blocking violations ({len(tier_c_violations)})",
            expanded=True,
        ):
            for v in tier_c_violations:
                st.markdown(
                    f"- **{v.get('field', '?')}** "
                    f"(category=`{v.get('category', '?')}`) — "
                    f"{(v.get('message') or '')[:200]}"
                )

        # Cross-link: copy-paste-ready OVERRIDES waiver snippet pre-
        # filled with the blocking field(s) for this ticker. The
        # analyst still has to fill in the reason + dates, which is
        # the point — keeps the analyst in the loop on waiver
        # rationale rather than auto-waiving.
        _render_overrides_snippet(
            ticker=records[0].get("ticker", "TICKER"),
            tier_c_violations=tier_c_violations,
        )

    # Both the XBRL-extracted table and the XBRL ↔ FMP side-by-side
    # are driven by the same /pipeline/fmp-compare/{ticker} endpoint
    # result — single source of truth, ensures the period axis
    # (historical FYs + current-year quarters) stays consistent
    # between the two views.
    ticker = records[0].get("ticker") if records else None
    if ticker:
        compare_res = _api_get(f"/pipeline/fmp-compare/{ticker}")
        if compare_res["ok"]:
            payload = compare_res["data"]
            _render_stage2_xbrl_extracted(payload)
            _render_stage2_fmp_comparison(payload)
        else:
            st.caption(
                "FMP comparison endpoint unreachable: "
                + compare_res["error"]
            )

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


def _render_overrides_snippet(
    ticker: str, tier_c_violations: List[Dict[str, Any]],
) -> None:
    """Surface a copy-paste-ready OVERRIDES dict entry for the blocking
    fields. The analyst pastes this into
    ``aletheia/calculations/_overrides.py`` after filling in the
    reason + dates, and the next Stage 2 run downgrades the violation
    to a soft-flag.
    """
    fields = sorted({v.get("field") for v in tier_c_violations if v.get("field")})
    field_list = ", ".join(f'"{f}"' for f in fields)
    from datetime import timezone as _tz
    now = datetime.now(_tz.utc)
    today = now.strftime("%Y-%m-%d")
    # Default review_by_date = 12 months out; analyst should shorten
    # when this is a stopgap rather than a stable edge case.
    review_by = now.replace(year=now.year + 1).strftime("%Y-%m-%d")
    snippet = f'''"{ticker}": {{
    "accounting_equation_waiver": {{
        "reason": "FILL IN — specific rationale, what's wrong with the "
                  "data, why is the violation legitimate, path to real fix.",
        "created_date":   "{today}",
        "review_by_date": "{review_by}",
        "fields":         [{field_list}],
    }},
}},'''
    with st.expander(
        "📝 OVERRIDES waiver snippet (copy-paste into _overrides.py)",
        expanded=False,
    ):
        st.caption(
            "Paste this into ``aletheia/calculations/_overrides.py`` "
            "after the existing entries. Fill in the ``reason`` field "
            "with a specific rationale; the registry refuses to load "
            "entries with placeholder text. Shorten ``review_by_date`` "
            "to 3-6 months when this is a stopgap for a real fix; "
            "leave at 12 months for stable edge cases."
        )
        st.code(snippet, language="python")


_FMP_CACHE_DIR_FOR_CALCS = Path("valuation_data/macro/fmp")


def _load_fmp_calc_metrics(ticker: str) -> Dict[str, Any]:
    """Load FMP TTM ``key_metrics`` + ``ratios`` payloads for the
    ticker. Returns a flat dict merging both sources so callers can
    look up FMP's published metric by its native field name. Empty
    dict when files are missing or malformed.
    """
    merged: Dict[str, Any] = {}
    for endpoint in ("key_metrics_ttm", "ratios_ttm"):
        path = _FMP_CACHE_DIR_FOR_CALCS / f"{ticker.upper()}__{endpoint}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text()).get("data") or []
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            merged.update(data)
    return merged


def _fmt_metric(v: Any, kind: str) -> str:
    """Format a metric value according to its kind: ``pct`` (decimal
    fraction → %), ``usd`` (large-number with $/B/M suffix),
    ``ratio`` (raw numeric, 2-decimal), ``text`` (passthrough)."""
    if v is None:
        return "—"
    if kind == "text":
        return str(v)
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return "—"
    if not (fv == fv):  # NaN
        return "—"
    if kind == "pct":
        return f"{fv * 100:.2f}%"
    if kind == "pct_raw":
        # value is already in percent units (e.g. ScreeningEngine
        # returns 19.98 to mean 19.98%, not 1998%).
        return f"{fv:.2f}%"
    if kind == "usd":
        return _fmt_usd(fv)
    if kind == "ratio":
        return f"{fv:.2f}"
    return f"{fv:g}"


def _drift_pct(ours: Any, fmp: Any) -> Optional[float]:
    """Relative drift = (ours − fmp) / |fmp|. None when either side
    isn't numeric or fmp is too close to zero to be a meaningful
    denominator."""
    try:
        a = float(ours); b = float(fmp)
    except (TypeError, ValueError):
        return None
    if abs(b) < 1e-9:
        return None
    return (a - b) / abs(b)


# Catalogue of every Stage 3 calculation we surface. Each entry:
#   (category, label, bundle_path, kind, fmp_field)
# bundle_path is a "/"-separated path within the bundle dict; values
# resolve via _bundle_get. fmp_field is None when FMP has no published
# equivalent — the FMP column then shows "—".
_STAGE3_CALC_SPEC: List[Dict[str, Any]] = [
    # ── DCF inputs ──────────────────────────────────────────────────
    {"cat": "DCF",     "label": "WACC",                     "path": "dcf/wacc_base",                "kind": "pct",   "fmp": None},
    {"cat": "DCF",     "label": "Beta",                     "path": "dcf/beta",                     "kind": "ratio", "fmp": "betaTTM"},
    {"cat": "DCF",     "label": "Risk-free rate",           "path": "dcf/risk_free_rate",           "kind": "pct",   "fmp": None},
    {"cat": "DCF",     "label": "ROIC",                     "path": "dcf/roic",                     "kind": "pct",   "fmp": "returnOnInvestedCapitalTTM"},
    {"cat": "DCF",     "label": "FCF",                      "path": "dcf/fcf",                      "kind": "usd",   "fmp": "freeCashFlowToFirmTTM"},
    {"cat": "DCF",     "label": "Revenue (base)",           "path": "dcf/revenue",                  "kind": "usd",   "fmp": None},
    {"cat": "DCF",     "label": "EBITDA (base)",            "path": "dcf/ebitda",                   "kind": "usd",   "fmp": None},
    {"cat": "DCF",     "label": "EBIT (base)",              "path": "dcf/ebit",                     "kind": "usd",   "fmp": None},
    {"cat": "DCF",     "label": "NOPAT",                    "path": "dcf/nopat",                    "kind": "usd",   "fmp": None},
    {"cat": "DCF",     "label": "Net debt",                 "path": "dcf/net_debt",                 "kind": "usd",   "fmp": None},
    {"cat": "DCF",     "label": "Market cap",               "path": "dcf/market_cap",               "kind": "usd",   "fmp": "marketCap"},
    {"cat": "DCF",     "label": "Shares diluted",           "path": "dcf/shares_diluted",           "kind": "ratio", "fmp": None},
    {"cat": "DCF",     "label": "IV per share (base)",      "path": "dcf/base_intrinsic_per_share", "kind": "usd",   "fmp": None},
    {"cat": "DCF",     "label": "IV per share (bull)",      "path": "dcf/bull_intrinsic_per_share", "kind": "usd",   "fmp": None},
    {"cat": "DCF",     "label": "IV per share (bear)",      "path": "dcf/bear_intrinsic_per_share", "kind": "usd",   "fmp": None},
    {"cat": "DCF",     "label": "Upside (base)",            "path": "dcf/base_upside",              "kind": "pct",   "fmp": None},
    # ── Reverse DCF ─────────────────────────────────────────────────
    {"cat": "RevDCF",  "label": "Current EV / EBITDA",      "path": "reverse_dcf/current_ev_ebitda", "kind": "ratio", "fmp": "evToEBITDATTM"},
    {"cat": "RevDCF",  "label": "Implied 10Y CAGR",         "path": "reverse_dcf/implied_cagr_10y", "kind": "pct",   "fmp": None},
    {"cat": "RevDCF",  "label": "Implied 5Y CAGR",          "path": "reverse_dcf/implied_cagr_5y",  "kind": "pct",   "fmp": None},
    {"cat": "RevDCF",  "label": "Historical 5Y CAGR",       "path": "reverse_dcf/historical_cagr_5y","kind": "pct",  "fmp": None},
    {"cat": "RevDCF",  "label": "EBIT margin",              "path": "reverse_dcf/ebit_margin",      "kind": "pct",   "fmp": "ebitMarginTTM"},
    {"cat": "RevDCF",  "label": "Forward EV/EBITDA (justified)", "path": "reverse_dcf/forward_ev_ebitda_justified", "kind": "ratio", "fmp": None},
    {"cat": "RevDCF",  "label": "Signal",                   "path": "reverse_dcf/signal",           "kind": "text",  "fmp": None},
    # ── Multiple Decomposition ──────────────────────────────────────
    {"cat": "MultDec", "label": "Market P/E",               "path": "multiple_decomposition/market_p_e",     "kind": "ratio", "fmp": "priceToEarningsRatioTTM"},
    {"cat": "MultDec", "label": "Market PEG",               "path": "multiple_decomposition/market_peg",     "kind": "ratio", "fmp": "priceEarningsToGrowthRatioTTM"},
    {"cat": "MultDec", "label": "Market P/Sales",           "path": "multiple_decomposition/market_p_sales", "kind": "ratio", "fmp": "priceToSalesRatioTTM"},
    {"cat": "MultDec", "label": "Market EV/EBITDA",         "path": "multiple_decomposition/market_ev_ebitda","kind": "ratio","fmp": "evToEBITDATTM"},
    {"cat": "MultDec", "label": "Market EV/EBIT",           "path": "multiple_decomposition/market_ev_ebit", "kind": "ratio", "fmp": None},
    {"cat": "MultDec", "label": "Justified EV/EBITDA",      "path": "multiple_decomposition/justified_ev_ebitda","kind": "ratio","fmp": None},
    {"cat": "MultDec", "label": "Sector median EV/EBITDA",  "path": "multiple_decomposition/sector_median_ev_ebitda","kind": "ratio","fmp": None},
    {"cat": "MultDec", "label": "EV/EBITDA premium %",      "path": "multiple_decomposition/ev_ebitda_premium_pct","kind": "pct","fmp": None},
    {"cat": "MultDec", "label": "vs-sector premium %",      "path": "multiple_decomposition/vs_sector_premium","kind": "pct","fmp": None},
    {"cat": "MultDec", "label": "ROIC",                     "path": "multiple_decomposition/roic",  "kind": "pct",   "fmp": "returnOnInvestedCapitalTTM"},
    {"cat": "MultDec", "label": "WACC",                     "path": "multiple_decomposition/wacc",  "kind": "pct",   "fmp": None},
    {"cat": "MultDec", "label": "ROIC − WACC spread",       "path": "multiple_decomposition/roic_wacc_spread","kind": "pct","fmp": None},
    # NB: FMP's cashConversionCycleTTM is the cash-conversion CYCLE in
    # days, not the cash-conversion RATIO (OCF / NI). Different concepts,
    # so we leave the FMP column empty rather than print a misleading drift.
    {"cat": "MultDec", "label": "Cash conversion ratio",    "path": "multiple_decomposition/cash_conversion_ratio","kind": "ratio","fmp": None},
    {"cat": "MultDec", "label": "Growth rate",              "path": "multiple_decomposition/growth_rate","kind": "pct","fmp": None},
    {"cat": "MultDec", "label": "Signal",                   "path": "multiple_decomposition/signal","kind": "text",  "fmp": None},
    # ── Screening ───────────────────────────────────────────────────
    {"cat": "Screen",  "label": "P/E",                      "path": "screening/p_per_e_ratio",      "kind": "ratio", "fmp": "priceToEarningsRatioTTM"},
    {"cat": "Screen",  "label": "PEG",                      "path": "screening/peg_ratio",          "kind": "ratio", "fmp": "priceEarningsToGrowthRatioTTM"},
    {"cat": "Screen",  "label": "P/B",                      "path": "screening/p_per_b_ratio",      "kind": "ratio", "fmp": "priceToBookRatioTTM"},
    {"cat": "Screen",  "label": "EV/EBITDA (clean)",        "path": "screening/ev_per_ebitda_(clean)","kind": "ratio","fmp": "evToEBITDATTM"},
    {"cat": "Screen",  "label": "EV/EBIT (normalized)",     "path": "screening/ev_per_ebit_(normalized)","kind": "ratio","fmp": None},
    {"cat": "Screen",  "label": "EV/FCF",                   "path": "screening/ev_per_fcf",         "kind": "ratio", "fmp": "evToFreeCashFlowTTM"},
    {"cat": "Screen",  "label": "Margin of safety",         "path": "screening/margin_of_safety",   "kind": "pct_raw","fmp": None},
    {"cat": "Screen",  "label": "Revenue CAGR (robust)",    "path": "screening/revenue_cagr_(robust)","kind": "pct_raw","fmp": None},
    {"cat": "Screen",  "label": "EPS growth rate",          "path": "screening/eps_growth_rate",    "kind": "pct_raw","fmp": None},
    {"cat": "Screen",  "label": "Current price",            "path": "screening/current_price",      "kind": "usd",   "fmp": None},
    {"cat": "Screen",  "label": "Market cap ($B)",          "path": "screening/market_cap_bn",      "kind": "ratio", "fmp": None},
    {"cat": "Screen",  "label": "Passes / flags / fails",   "path": None,                           "kind": "text",  "fmp": None, "computed": "screening_counts"},
    # ── Moat / Cyclicality / Reality ────────────────────────────────
    {"cat": "Moat",    "label": "Moat score",               "path": "moat_fingerprint/score",       "kind": "ratio", "fmp": None},
    {"cat": "Cyclic",  "label": "Z-score",                  "path": "cyclicality/z_score",          "kind": "ratio", "fmp": None},
    {"cat": "Cyclic",  "label": "Is peak",                  "path": "cyclicality/is_peak",          "kind": "text",  "fmp": None},
    {"cat": "Cyclic",  "label": "3-yr average",             "path": "cyclicality/avg_3yr",          "kind": "usd",   "fmp": None},
    # ── FMP-published metrics not in our calcs (cross-references) ──
    {"cat": "FMP×",    "label": "ROE (FMP)",                "path": None, "kind": "pct",   "fmp": "returnOnEquityTTM", "computed": "fmp_only"},
    {"cat": "FMP×",    "label": "ROA (FMP)",                "path": None, "kind": "pct",   "fmp": "returnOnAssetsTTM", "computed": "fmp_only"},
    {"cat": "FMP×",    "label": "Debt / Equity (FMP)",      "path": None, "kind": "ratio", "fmp": "debtToEquityRatioTTM", "computed": "fmp_only"},
    {"cat": "FMP×",    "label": "Current ratio (FMP)",      "path": None, "kind": "ratio", "fmp": "currentRatioTTM", "computed": "fmp_only"},
    {"cat": "FMP×",    "label": "Gross margin (FMP)",       "path": None, "kind": "pct",   "fmp": "grossProfitMarginTTM", "computed": "fmp_only"},
    {"cat": "FMP×",    "label": "Net margin (FMP)",         "path": None, "kind": "pct",   "fmp": "bottomLineProfitMarginTTM", "computed": "fmp_only"},
    {"cat": "FMP×",    "label": "Effective tax rate (FMP)", "path": None, "kind": "pct",   "fmp": "effectiveTaxRateTTM", "computed": "fmp_only"},
    {"cat": "FMP×",    "label": "Interest coverage (FMP)",  "path": None, "kind": "ratio", "fmp": "interestCoverageRatioTTM", "computed": "fmp_only"},
    {"cat": "FMP×",    "label": "FCF yield (FMP)",          "path": None, "kind": "pct",   "fmp": "freeCashFlowYieldTTM", "computed": "fmp_only"},
    {"cat": "FMP×",    "label": "Enterprise value (FMP)",   "path": None, "kind": "usd",   "fmp": "enterpriseValueTTM", "computed": "fmp_only"},
]


def _bundle_get(bundle: Dict[str, Any], path: str) -> Any:
    """Resolve a ``a/b/c`` path within nested dicts. Returns None when
    any segment is missing or the leaf isn't present."""
    cur: Any = bundle
    for seg in path.split("/"):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _render_stage3_calculations_panel(
    bundle: Dict[str, Any], ticker: str,
) -> None:
    """Comprehensive table of every Stage 3 calculation alongside the
    FMP-published equivalent (where one exists). Built from the
    ``_STAGE3_CALC_SPEC`` catalogue so adding a new calc to the bundle
    is a one-line catalog entry, not a UI rewrite.

    Layer 2 methodology: each row's "Calculation" column gets a 📐 chip
    when the derivation_registry flags it as Category-D (expected
    methodology divergence from FMP). Below the table, a "📖 How each
    value is derived" expander shows the registry entry for every row
    — inputs, formula, methodology citation, alternates, and the
    documented FMP divergence note.
    """
    from aletheia.calculations.derivation_registry import (
        lookup_by_label,
    )

    fmp = _load_fmp_calc_metrics(ticker)
    screening = bundle.get("screening") or {}

    rows: List[Dict[str, str]] = []
    methodology_seen: List[str] = []  # labels with registry entries (for the expander)
    for spec in _STAGE3_CALC_SPEC:
        if spec.get("computed") == "screening_counts":
            ours_val = (
                f"{screening.get('passes', '?')}✓  "
                f"{screening.get('flags', '?')}⚠  "
                f"{screening.get('fails', '?')}✗"
            )
            ours_disp = ours_val
            fmp_disp = "—"
            drift_disp = "—"
        elif spec.get("computed") == "fmp_only":
            ours_disp = "—"
            fmp_val = fmp.get(spec["fmp"]) if spec.get("fmp") else None
            fmp_disp = _fmt_metric(fmp_val, spec["kind"])
            drift_disp = "—"
        else:
            ours_val = _bundle_get(bundle, spec["path"]) if spec.get("path") else None
            ours_disp = _fmt_metric(ours_val, spec["kind"])
            fmp_val = fmp.get(spec["fmp"]) if spec.get("fmp") else None
            fmp_disp = _fmt_metric(fmp_val, spec["kind"]) if spec.get("fmp") else "—"
            drift = _drift_pct(ours_val, fmp_val) if spec.get("fmp") else None
            drift_disp = (
                f"{drift * 100:+.1f}%" if drift is not None else "—"
            )
        # Layer 2: registry lookup for methodology chip
        entry = lookup_by_label(spec["label"])
        calc_label = spec["label"]
        if entry is not None:
            methodology_seen.append(spec["label"])
            if entry.category_d:
                calc_label = f"📐 {calc_label}"
        rows.append({
            "Category": spec["cat"],
            "Calculation": calc_label,
            "Ours": ours_disp,
            "FMP": fmp_disp,
            "Drift": drift_disp,
        })

    if not fmp:
        st.caption(
            f"FMP cache for **{ticker}** not found — FMP columns will be "
            "empty. Refresh the FMP ingest to populate."
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption(
        "📐 = Category-D (documented methodology divergence from FMP — "
        "not a bug). See methodology expander below for details."
    )

    # Layer 2: per-value derivation methodology expander + live trace
    if methodology_seen:
        _render_methodology_expander(methodology_seen, bundle=bundle)


def _render_methodology_expander(
    labels: List[str], bundle: Optional[Dict[str, Any]] = None,
) -> None:
    """Show registry entry details for each calc row in an expander.

    Renders the documented methodology + the live runtime trace when a
    bundle is provided (V1 derivation traces). The trace pairs each
    declared input with its actual value pulled from the Stage 3
    bundle. Inputs not in the bundle (cleaning-side upstream values)
    show as ``<from upstream>``; that's V2 territory.
    """
    from aletheia.calculations.derivation_registry import (
        lookup_by_label, trace_value,
    )

    with st.expander(
        "📖 How each value is derived (Layer-2 methodology + live trace)",
        expanded=False,
    ):
        st.caption(
            "Each entry documents the formula, methodology citation, "
            "alternates, and any documented divergence from FMP. Where "
            "the bundle carries the inputs, a 💎 live trace below shows "
            "the actual numbers that produced the value. Rows marked "
            "📐 are expected-divergence (Category D), not bugs."
        )
        for label in labels:
            entry = lookup_by_label(label)
            if entry is None:
                continue
            chip = "📐 " if entry.category_d else ""
            st.markdown(
                f"##### {chip}{entry.label}  ·  `{entry.name}`  ·  "
                f"*{entry.category}*"
            )
            st.markdown(f"**Formula**: `{entry.formula}`")
            if entry.inputs:
                st.markdown(
                    "**Inputs**: " + ", ".join(f"`{i}`" for i in entry.inputs)
                )
            st.markdown(f"**Methodology**: {entry.methodology}")
            if entry.alternates:
                st.markdown("**Alternates**:")
                for alt in entry.alternates:
                    st.markdown(f"  - {alt}")
            if entry.fmp_equivalent:
                st.markdown(f"**FMP divergence**: {entry.fmp_equivalent}")

            # V1 live trace: resolve inputs from the Stage 3 bundle
            if bundle is not None:
                trace = trace_value(entry.name, bundle)
                if trace and trace.get("input_values"):
                    rows = []
                    for inp, val in trace["input_values"]:
                        if isinstance(val, (int, float)):
                            v_disp = _fmt_metric(val, "usd") if abs(val) >= 1e6 else f"{val:.4g}"
                        else:
                            v_disp = str(val)
                        rows.append({"Input": inp, "Live value": v_disp})
                    result = trace.get("result")
                    if isinstance(result, (int, float)):
                        result_disp = (
                            _fmt_metric(result, "usd")
                            if abs(result) >= 1e6 else f"{result:.4g}"
                        )
                    else:
                        result_disp = str(result) if result is not None else "—"
                    st.markdown(f"**💎 Live trace**  ·  result = **{result_disp}**")
                    st.dataframe(
                        rows, use_container_width=True, hide_index=True,
                    )
            st.markdown("---")


def _render_stage3_validation(bundle: Dict[str, Any], ticker: str) -> None:
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

    # All Stage 3 calculations alongside FMP equivalents — a single
    # comprehensive table that walks every engine's output and shows
    # the FMP-published value side-by-side where one exists.
    with st.expander(
        "All Stage 3 calculations vs FMP", expanded=False,
    ):
        _render_stage3_calculations_panel(bundle, ticker)

    # Accounting-identity audit (seven identities). Promoted from the
    # standalone Days 1-7 audit script — runs in Stage 3 for every ticker.
    _render_identity_audit_panel(bundle.get("accounting_identities") or {})

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


def _render_identity_audit_panel(identities: Dict[str, Any]) -> None:
    """Render the seven-identity audit results inside Stage 3.

    Layout: a 4-metric strip (total / passed / failed / skipped),
    a failed-checks expander grouped by identity, and a small
    caption flagging the known formula limitations the analyst
    should keep in mind when reading discrepancies.
    """
    summary = identities.get("summary") or {}
    if not summary:
        st.caption("Identity audit: no results in this bundle.")
        return

    cols = st.columns(5)
    cols[0].metric("Identity checks", summary.get("n_checks", 0))
    cols[1].metric("✓ Passed", summary.get("n_passed", 0))
    cols[2].metric("⚠️ Expected exceptions", summary.get("n_expected_exception", 0))
    cols[3].metric("✗ Failed", summary.get("n_failed", 0))
    cols[4].metric("Skipped", summary.get("n_skipped", 0))

    results = identities.get("results") or []
    non_passing = [
        r for r in results
        if not r.get("passed")
        and not (r.get("notes") or "").startswith("skipped:")
    ]
    flagged = [r for r in non_passing if r.get("exception_category")]
    unflagged = [r for r in non_passing if not r.get("exception_category")]

    # Unflagged failures get prominent treatment — these are the
    # genuinely unexplained gaps the analyst needs to investigate.
    if unflagged:
        with st.expander(
            f"❌ Unflagged failures ({len(unflagged)}) — investigate",
            expanded=True,
        ):
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for r in unflagged:
                grouped.setdefault(r.get("identity_name", "?"), []).append(r)
            for name in sorted(grouped):
                rows = grouped[name]
                tol_pct = (rows[0].get("tolerance_pct") or 0.0) * 100.0
                st.markdown(
                    f"**{name}** — tolerance {tol_pct:.1f}%   ·   "
                    f"{len(rows)} failure(s)"
                )
                st.dataframe([{
                    "FY": r.get("fiscal_year"),
                    "Period": r.get("period"),
                    "Discrepancy %": f"{r.get('discrepancy_pct'):.2f}%"
                        if isinstance(r.get("discrepancy_pct"), (int, float)) else "—",
                    "Notes": (r.get("notes") or "")[:120],
                } for r in rows], use_container_width=True, hide_index=True)

    # Expected exceptions get a separate, less alarming expander.
    if flagged:
        # Group by exception_category so the analyst sees the pattern.
        by_cat: Dict[str, List[Dict[str, Any]]] = {}
        for r in flagged:
            by_cat.setdefault(r["exception_category"], []).append(r)
        with st.expander(
            f"⚠️ Expected exceptions ({len(flagged)}, "
            f"{len(by_cat)} categor{'y' if len(by_cat)==1 else 'ies'}) — "
            "documented structural reasons",
            expanded=False,
        ):
            for cat in sorted(by_cat):
                rows = by_cat[cat]
                st.markdown(f"**`{cat}`** — {len(rows)} occurrence(s)")
                st.dataframe([{
                    "Identity": r.get("identity_name"),
                    "FY": r.get("fiscal_year"),
                    "Period": r.get("period"),
                    "Discrepancy %": f"{r.get('discrepancy_pct'):.2f}%"
                        if isinstance(r.get("discrepancy_pct"), (int, float)) else "—",
                } for r in rows], use_container_width=True, hide_index=True)

    if not non_passing:
        st.caption("All identity checks passed (or were skipped on missing data).")


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
        _render_stage3_validation(bundle, ticker)
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
