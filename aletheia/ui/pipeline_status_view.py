"""Pipeline Status Matrix — universe-glance view.

Complements the per-ticker Stage Explorer with a row-per-ticker view
of the entire universe's pipeline state. One row shows all four stage
status badges, the most recent run, and (when a Stage 3 bundle is in
session) the identity-audit summary.

Click a ticker row to navigate to the Stage Explorer for that ticker
(sets ``st.session_state._pending_active_ticker`` + ``_pending_active_view``
which the streamlit_app shell consumes on the next rerun).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
import streamlit as st


API_BASE = "http://localhost:8000"

# Order in which stages render across columns.
_STAGES = ["stage1_ingest", "stage2_validate", "stage3_calculate", "stage4_agents"]
_STAGE_LABELS = {
    "stage1_ingest":    "Stage 1",
    "stage2_validate":  "Stage 2",
    "stage3_calculate": "Stage 3",
    "stage4_agents":    "Stage 4",
}

# Status → display chip. Keys match the values written to the
# pipeline_status table by ``aletheia.pipeline.orchestrator`` via
# the ``StageStatus`` enum in ``aletheia.contracts.pipeline``.
# Legacy "success" / "stale" / "pending" labels kept for back-
# compatibility with any pre-migration rows still in the DB.
_STATUS_CHIPS = {
    # Orchestrator-written values (current)
    "ok":                  "🟢",
    "skipped_cached":      "🟢",
    "running":             "🟡",
    "failed":              "🔴",
    "skipped_dependency":  "🟠",
    "pending":             "⚪",
    # Legacy / fallback labels
    "success":             "🟢",
    "stale":               "🟠",
    "unknown":             "⬜",
}


def _fetch_status_matrix() -> List[Dict[str, Any]]:
    """Fetch the /pipeline/status matrix endpoint. Empty list on error."""
    try:
        r = httpx.get(f"{API_BASE}/pipeline/status", timeout=10)
        if r.status_code != 200:
            return []
        return r.json()
    except Exception:
        return []


def _state_key(ticker: str, stage: str, field: str) -> str:
    """Match the Stage Explorer's session-state key convention so we can
    read identity-audit summaries from bundles the analyst has fetched
    during this session."""
    return f"pipeline_explorer__{ticker}__{stage}__{field}"


def _read_stage3_bundle(ticker: str) -> Optional[Dict[str, Any]]:
    return st.session_state.get(_state_key(ticker, "stage3_calculate", "bundle"))


def _identity_audit_summary(ticker: str) -> Optional[Dict[str, Any]]:
    """Read the identity-audit summary from session-cached Stage 3 bundle.

    Returns None when no Stage 3 bundle is in session for this ticker.
    The Status Matrix can fetch one fresh via the API too — but a
    universe-wide refresh is expensive (40 × Stage 3 runs); we
    pessimistically rely on session-cached data here, with a "refresh"
    button for the analyst to populate explicitly.
    """
    bundle = _read_stage3_bundle(ticker)
    if not bundle:
        return None
    ai = (bundle.get("accounting_identities") or {}).get("summary")
    return ai


def _fmt_relative(iso_ts: Optional[str]) -> str:
    """Format an ISO timestamp as a relative age (5m ago, 2h ago, 3d ago).
    Returns "—" when missing."""
    if not iso_ts:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return iso_ts
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.utcnow()
    delta = now - dt
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _classification(ticker: str) -> Dict[str, str]:
    """Look up sector / lifecycle from ``config.ticker_classification.UNIVERSE``.
    Returns ``{}`` for tickers not in the universe (e.g., ad-hoc additions)."""
    try:
        from config.ticker_classification import UNIVERSE
    except ImportError:
        return {}
    cls = UNIVERSE.get(ticker.upper())
    if cls is None:
        return {}
    return {
        "sector": getattr(cls, "sector", "?") or "?",
        "lifecycle": getattr(cls, "lifecycle", "?") or "?",
    }


def _navigate_to_explorer(
    ticker: str, focus_stage: Optional[str] = None,
) -> None:
    """Hand the analyst over to the Stage Explorer for one ticker.
    Uses the same pending-state pattern as Streamlit's other intra-app
    nav so the sidebar selectbox picks up the change on next rerun.

    ``focus_stage`` (optional) hints which stage section the analyst
    wants to land on (e.g., a ticker with a Stage 3 failure routes to
    Stage 3 directly). The Stage Explorer reads
    ``st.session_state._pending_focus_stage`` and scrolls/expands
    that section; falls back to the default panel layout when unset.
    """
    st.session_state._pending_active_ticker = ticker
    st.session_state._pending_active_view = "⚙  Pipeline Explorer"
    if focus_stage:
        st.session_state._pending_focus_stage = focus_stage
    st.rerun()


def _suggest_focus_stage(stage_rows: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Pick the most-likely-interesting stage for the analyst to land
    on based on the ticker's current pipeline status.

    Priority order:
      1. Any failed stage → land there to investigate
      2. Any stale stage → land there to refresh
      3. Most-recent successful stage (analyst likely wants to drill in)
      4. None (Stage Explorer renders all stages by default)
    """
    # Failed / blocked first — analyst needs to investigate
    for stage in _STAGES:
        srow = stage_rows.get(stage)
        s = srow.get("status") if srow else None
        if s in ("failed", "skipped_dependency", "stale"):
            return stage
    # Otherwise: most-recent successful stage (drill-in target).
    # Orchestrator writes "ok" + "skipped_cached"; legacy writes "success".
    for stage in reversed(_STAGES):
        srow = stage_rows.get(stage)
        s = srow.get("status") if srow else None
        if s in ("ok", "skipped_cached", "success"):
            return stage
    return None


def render_pipeline_status_matrix() -> None:
    """Universe-wide pipeline status matrix.

    Renders:
      - Status legend (chip semantics)
      - Filter row (sector + lifecycle multiselect)
      - Refresh button
      - Per-ticker row with 4 stage badges + identity audit summary
        + last-run timestamp + nav button
    """
    st.markdown("## Pipeline Status Matrix")
    st.caption(
        "Universe-glance view. Each row shows the 4 stage badges, the "
        "identity-audit pass-rate (when the Stage 3 bundle is in session), "
        "and a navigate button to drill into the per-ticker Stage Explorer."
    )

    # Status chip legend
    legend = "  ·  ".join(
        f"{chip} {status}" for status, chip in _STATUS_CHIPS.items()
    )
    st.caption(f"Status legend: {legend}")

    # Universe tickers (fall back to whatever's in status table if not configured)
    try:
        from config.ticker_classification import UNIVERSE
        universe_tickers = sorted(UNIVERSE.keys())
    except ImportError:
        universe_tickers = []

    if st.button("🔄 Refresh matrix", key="status_matrix_refresh"):
        st.rerun()

    matrix_rows = _fetch_status_matrix()
    # Index by (ticker, stage)
    by_ticker_stage: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for row in matrix_rows:
        by_ticker_stage[row["ticker"].upper()][row["stage"]] = row

    # Tickers to display: union of universe + any with status rows.
    tickers = sorted(
        set(universe_tickers) | set(by_ticker_stage.keys())
    )

    # Filters
    classifications = {t: _classification(t) for t in tickers}
    all_sectors = sorted({
        c.get("sector", "?") for c in classifications.values() if c
    })
    all_lifecycles = sorted({
        c.get("lifecycle", "?") for c in classifications.values() if c
    })
    cols = st.columns([1, 1, 2])
    sector_filter = cols[0].multiselect(
        "Sector", all_sectors, default=[], key="status_matrix_sector_filter",
    )
    lifecycle_filter = cols[1].multiselect(
        "Lifecycle", all_lifecycles, default=[],
        key="status_matrix_lifecycle_filter",
    )

    # Apply filters
    if sector_filter:
        tickers = [
            t for t in tickers
            if classifications.get(t, {}).get("sector") in sector_filter
        ]
    if lifecycle_filter:
        tickers = [
            t for t in tickers
            if classifications.get(t, {}).get("lifecycle") in lifecycle_filter
        ]

    if not tickers:
        st.warning(
            "No tickers match the active filters. Clear the filters above "
            "to see all universe tickers."
        )
        return

    st.caption(f"Showing **{len(tickers)}** ticker(s)")

    # Summary counts across visible tickers — match orchestrator-
    # written status values, with legacy labels covered too.
    def _count_by_status(predicate) -> int:
        return sum(
            1 for t in tickers for s in _STAGES
            if predicate(
                by_ticker_stage.get(t, {}).get(s, {}).get("status")
            )
        )
    n_ok = _count_by_status(
        lambda s: s in ("ok", "skipped_cached", "success")
    )
    n_running = _count_by_status(lambda s: s == "running")
    n_failed = _count_by_status(lambda s: s == "failed")
    n_blocked = _count_by_status(
        lambda s: s in ("skipped_dependency", "stale")
    )
    cols = st.columns(5)
    cols[0].metric("Tickers", len(tickers))
    cols[1].metric("🟢 OK stages", n_ok)
    cols[2].metric("🟡 Running", n_running)
    cols[3].metric("🔴 Failed", n_failed)
    cols[4].metric("🟠 Blocked / stale", n_blocked)

    # Build the table rows. The "navigate" column is rendered as a
    # separate column of buttons below the dataframe because Streamlit's
    # st.dataframe doesn't support clickable rows natively without the
    # newer st.data_editor + LinkColumn (which we avoid to keep this
    # compatible with the existing app's Streamlit version).
    table_rows: List[Dict[str, Any]] = []
    for ticker in tickers:
        stage_rows = by_ticker_stage.get(ticker, {})
        row: Dict[str, Any] = {"Ticker": ticker}
        # Sector / lifecycle
        cls = classifications.get(ticker, {})
        row["Sector"] = cls.get("sector", "—")
        row["Lifecycle"] = cls.get("lifecycle", "—")
        # Stage badges
        for stage in _STAGES:
            srow = stage_rows.get(stage)
            status = srow.get("status") if srow else "unknown"
            chip = _STATUS_CHIPS.get(status, "⬜")
            row[_STAGE_LABELS[stage]] = chip
        # Identity audit summary (from session-cached Stage 3 bundle)
        ai = _identity_audit_summary(ticker)
        if ai:
            n_checks = ai.get("n_checks", 0)
            n_pass = ai.get("n_passed", 0)
            n_ee = ai.get("n_expected_exception", 0)
            n_fail = ai.get("n_failed", 0)
            row["Identity Audit"] = (
                f"{n_pass}✓ {n_ee}⚠ {n_fail}✗ of {n_checks}"
            )
        else:
            row["Identity Audit"] = "—"
        # Most recent last_run_at across stages
        last_runs = [
            srow.get("last_run_at")
            for srow in stage_rows.values()
            if srow and srow.get("last_run_at")
        ]
        row["Last run"] = (
            _fmt_relative(max(last_runs)) if last_runs else "—"
        )
        table_rows.append(row)

    st.dataframe(table_rows, use_container_width=True, hide_index=True)

    # Navigate buttons — one per visible ticker, in a grid below the table.
    st.markdown("##### Drill into Stage Explorer")
    n_cols = 6
    for i in range(0, len(tickers), n_cols):
        cols = st.columns(n_cols)
        for j, ticker in enumerate(tickers[i:i + n_cols]):
            if cols[j].button(
                f"→ {ticker}",
                key=f"status_matrix_nav_{ticker}",
                use_container_width=True,
            ):
                focus = _suggest_focus_stage(
                    by_ticker_stage.get(ticker, {})
                )
                _navigate_to_explorer(ticker, focus_stage=focus)


__all__ = ["render_pipeline_status_matrix"]
