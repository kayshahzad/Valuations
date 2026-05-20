"""
aletheia/ui/financials_view.py

Re-imagined Financials tab. Replaces dense plain-text dataframes with:

  • Hero strip — 4 large metric cards (Revenue, EBITDA, Net Income, FCF)
    with year-over-year delta and validation status dot.
  • Validation banner — one-line summary of how much of this ticker's
    data has been externally cross-checked vs SEC + FMP.
  • Statement cards — Income Statement, Balance Sheet, Capital Structure,
    Lease Items each rendered as a styled table with a color-dot status
    column, bold current FY, muted prior FY, and a YoY% column.
  • Multi-year history — same fiscal history as before, but with proper
    formatting and a heatmap-style quality score column.
  • DCF section — preserved but visually consistent with the rest.

Validation status comes from `aletheia.ui.validation_badge` and is shown
as a colored dot in its own column (instead of being concatenated into
the metric label as a plain-text marker).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st


# ── Status dot palette ─────────────────────────────────────────────────────

_STATUS_DOT = {
    "validated": "🟢",   # SEC byte-perfect or FMP within 1%
    "near":      "🟡",   # within 1–5%, documented definitional difference
    "drift":     "🔴",   # >5% drift — investigate
    "missing":   "⚪",   # not present on either source
    "unknown":   "·",    # not validated yet
}

_STATUS_HELP = {
    "validated": "Externally validated within 1%",
    "near":      "Within 1–5%; documented definitional difference",
    "drift":     ">5% drift — investigate",
    "missing":   "Field absent on validator side",
    "unknown":   "Not yet validated",
}


# ── Formatters ─────────────────────────────────────────────────────────────

def _bn(v: Optional[float], dp: int = 2) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1e12:
        return f"${v/1e12:,.{dp}f}T"
    if abs(v) >= 1e9:
        return f"${v/1e9:,.{dp}f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:,.{dp}f}M"
    return f"${v:,.0f}"


def _pct(v: Optional[float], dp: int = 1) -> str:
    if v is None:
        return "—"
    # Heuristic: if the value is already in % units (>1.0), don't multiply.
    return f"{v:.{dp}f}%" if abs(v) > 1.0 else f"{v*100:.{dp}f}%"


def _yoy(curr: Optional[float], prior: Optional[float]) -> Optional[float]:
    if curr is None or prior is None or prior == 0:
        return None
    return (curr - prior) / abs(prior)


def _yoy_str(curr: Optional[float], prior: Optional[float]) -> str:
    d = _yoy(curr, prior)
    if d is None:
        return "—"
    return f"{d*100:+.1f}%"


# ── Validation lookup ──────────────────────────────────────────────────────

def _status_for(ticker: Optional[str], label: str) -> str:
    if not ticker:
        return "unknown"
    try:
        from aletheia.ui.validation_badge import lookup_status
        return lookup_status(ticker, label)
    except Exception:
        return "unknown"


# ── Hero cards ─────────────────────────────────────────────────────────────

def _hero_strip(ticker: str, inc: Dict[str, Any], history: List[Dict[str, Any]]) -> None:
    """4 large metric cards: Revenue, EBITDA, Net Income, FCF — each with
    YoY delta and validation status icon."""
    prior = history[-2] if len(history) >= 2 else {}

    cards = [
        ("Revenue",    inc.get("Revenue"),     prior.get("Revenue"),    "Revenue"),
        ("EBITDA",     inc.get("EBITDA"),      prior.get("EBITDA"),     "EBITDA"),
        ("Net Income", inc.get("NetIncome"),   prior.get("NetIncome"),  "Net Income"),
        ("FCF",        inc.get("FCF"),         prior.get("FCF"),        "FCF"),
    ]

    cols = st.columns(4)
    for col, (label, curr, prior_v, badge_key) in zip(cols, cards):
        status = _status_for(ticker, badge_key)
        dot = _STATUS_DOT.get(status, "·")
        delta = _yoy_str(curr, prior_v) if prior_v is not None else None
        with col:
            st.metric(
                label=f"{dot} {label}",
                value=_bn(curr) if curr is not None else "—",
                delta=delta,
                help=_STATUS_HELP.get(status),
            )


# ── Validation banner ──────────────────────────────────────────────────────

def _fmt_period_end(period_end: Optional[str]) -> str:
    """Render a 'YYYY-MM-DD' period-end string as 'Mon DD, YYYY'."""
    if not period_end:
        return ""
    try:
        from datetime import datetime as _dt
        return _dt.strptime(period_end[:10], "%Y-%m-%d").strftime("%b %d, %Y")
    except Exception:
        return period_end[:10] if period_end else ""


def _freshness_banner(freshness: Dict[str, Any]) -> None:
    """Phase Q-7: surface filing freshness so the analyst knows how
    stale the data is and when the next refresh lands. Shows nothing
    when no period-end is recorded (legacy DBs)."""
    if not freshness:
        return
    period_end  = freshness.get("latest_period_end_date")
    period      = freshness.get("latest_period") or "FY"
    days_since  = freshness.get("days_since_filing")
    next_date   = freshness.get("next_expected_date")
    days_until  = freshness.get("days_until_next_filing")
    if not period_end:
        return

    period_label = "TTM (latest 10-Q)" if period == "TTM" else "FY (last 10-K)"
    age_phrase = (
        f"{days_since} days ago" if days_since is not None else "—"
    )
    if days_until is None:
        next_phrase = ""
    elif days_until <= 0:
        next_phrase = " · next filing expected any day"
    else:
        next_phrase = f" · next ~{next_date} ({days_until} days)"

    st.caption(
        f"📅 **Latest data:** {period_label} · ended {period_end} · {age_phrase}{next_phrase}"
    )


def _validation_banner(ticker: str, fy: int, ident: Dict[str, Any]) -> None:
    """One-line snapshot — quality score + validation pass count.

    Includes the period-end date so the analyst can see at a glance that
    e.g. NVDA's FY2026 actually ended Jan 25, 2026 (non-calendar fiscal
    year), not some future point.
    """
    try:
        from aletheia.ui.validation_badge import get_validation_status
        statuses = get_validation_status(ticker)
        n_validated = sum(1 for v in statuses.values() if v == "validated")
        n_near      = sum(1 for v in statuses.values() if v == "near")
        n_drift     = sum(1 for v in statuses.values() if v == "drift")
        total = len(statuses)
    except Exception:
        n_validated = n_near = n_drift = total = 0

    quality = ident.get("quality_score") or 0
    warnings = ident.get("warning_count") or 0
    errors = ident.get("error_count") or 0
    period_end_str = _fmt_period_end(ident.get("period_end_date"))

    pass_pct = (n_validated + n_near) / total * 100 if total else 0
    pass_color = "#10b981" if pass_pct >= 75 else "#f59e0b" if pass_pct >= 50 else "#ef4444"

    fy_display = f"FY{fy}"
    if period_end_str:
        fy_display = f"FY{fy} <span style='opacity:0.7;font-weight:400'>· ended {period_end_str}</span>"

    st.markdown(
        f"""
<div style="
    display:flex; gap:16px; align-items:center; padding:12px 16px;
    background:rgba(128,128,128,0.08); border-left:3px solid {pass_color};
    border-radius:4px; font-family:'DM Mono',monospace; font-size:12px;
    color:#a1a1aa; margin:8px 0 16px 0;
">
    <span><strong style="color:#e5e5e5">{fy_display}</strong></span>
    <span style="color:#71717a">·</span>
    <span>Quality <strong style="color:#e5e5e5">{quality:.2f}</strong></span>
    <span style="color:#71717a">·</span>
    <span>Validation <strong style="color:{pass_color}">{n_validated + n_near}/{total}</strong>
        <span style="color:#10b981">{n_validated} ✓</span>
        <span style="color:#f59e0b">{n_near} ≈</span>
        <span style="color:#ef4444">{n_drift} ⚠</span>
    </span>
    <span style="color:#71717a">·</span>
    <span>{warnings} warnings · {errors} errors</span>
</div>
        """,
        unsafe_allow_html=True,
    )


# ── Statement table renderer ───────────────────────────────────────────────

def _pp_str(curr: Optional[float], prior: Optional[float]) -> str:
    """Year-over-year delta for percentage metrics, expressed in
    percentage points (pp). Robust to mixed unit conventions: if the
    field stores a fraction (<=1.0) it's converted to % first."""
    if curr is None or prior is None:
        return "—"
    c = curr if abs(curr) > 1.0 else curr * 100
    p = prior if abs(prior) > 1.0 else prior * 100
    return f"{c - p:+.1f}pp"


_EX_UNUSUAL_SOURCE_LABEL = {
    "xbrl_discrete_tags":        ("XBRL discrete tags", "green"),
    "fmp_other_expenses_bucket": ("FMP otherExpenses", "orange"),
}


def _ex_unusual_provenance(inc: Dict[str, Any]) -> None:
    """Small caption under the income statement explaining where the
    ex-unusual add-back came from. Renders only when the row actually
    has a value — keeps the income statement quiet for the common case
    of "no material one-time items this year"."""
    src = inc.get("OperatingIncome_ExUnusual_Source")
    val = inc.get("OperatingIncome_ExUnusual")
    if not src or val is None:
        return
    label, color = _EX_UNUSUAL_SOURCE_LABEL.get(src, (src, "gray"))
    explainer = {
        "xbrl_discrete_tags": (
            "Add-back sums AssetImpairmentCharges, GoodwillImpairmentLoss, "
            "IntangibleAssetImpairmentCharge, ImpairmentOfLongLivedAssetsHeldForUse, "
            "and RestructuringCharges from SEC XBRL companyfacts. "
            "Capital-IQ-aligned precision."
        ),
        "fmp_other_expenses_bucket": (
            "Add-back uses FMP's ``otherExpenses`` field (one-time + other "
            "non-recurring items), gated by a 5%-of-revenue materiality "
            "threshold. Approximate — switch this ticker to the hybrid "
            "provider for XBRL-discrete-tag precision."
        ),
    }.get(src, "")
    cols = st.columns([1, 4])
    with cols[0]:
        st.badge(label, color=color)
    with cols[1]:
        st.caption(explainer)


def _statement_table(
    title: str,
    ticker: str,
    rows: List[Tuple[str, str, Optional[float], Optional[float], str]],
    *,
    hide_empty: bool = True,
    current_header: str = "Current FY",
    prior_header: str = "Prior FY",
) -> None:
    """Render a statement section.

    Each row: (display_label, badge_key, current_value, prior_value, fmt_kind)
      fmt_kind: "$" → dollar, "%" → percent, "n" → number, "x" → multiple

    When ``hide_empty`` is True (default), rows where both Current and
    Prior values are None are omitted — keeps non-applicable metrics
    (e.g. lease items for pre-ASC842 filers, growth-CapEx that the
    cleaner couldn't split) from cluttering the table.
    """
    st.markdown(f"#### {title}")
    df_rows = []
    for label, badge_key, curr, prior, fmt in rows:
        if hide_empty and curr is None and prior is None:
            continue
        status = _status_for(ticker, badge_key) if badge_key else "unknown"
        dot = _STATUS_DOT.get(status, "·")
        if fmt == "$":
            curr_s = _bn(curr); prior_s = _bn(prior)
            yoy_s = _yoy_str(curr, prior)
        elif fmt == "%":
            curr_s = _pct(curr); prior_s = _pct(prior)
            yoy_s = _pp_str(curr, prior)
        elif fmt == "x":
            curr_s = f"{curr:,.2f}x" if curr is not None else "—"
            prior_s = f"{prior:,.2f}x" if prior is not None else "—"
            yoy_s = _yoy_str(curr, prior)
        else:
            curr_s = f"{curr:,.2f}" if curr is not None else "—"
            prior_s = f"{prior:,.2f}" if prior is not None else "—"
            yoy_s = _yoy_str(curr, prior)

        df_rows.append({
            "": dot,
            "Metric": label,
            current_header: curr_s,
            prior_header:   prior_s,
            "YoY":          yoy_s,
        })

    if not df_rows:
        st.caption("No data for this section.")
        return

    df = pd.DataFrame(df_rows)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "":             st.column_config.TextColumn("", width="small", help="Validation status"),
            "Metric":       st.column_config.TextColumn("Metric", width="medium"),
            current_header: st.column_config.TextColumn(current_header, width="small"),
            prior_header:   st.column_config.TextColumn(prior_header, width="small"),
            "YoY":          st.column_config.TextColumn("YoY", width="small",
                                help="Δ vs prior FY. $ rows: % change. % rows: percentage-point change."),
        },
    )


# ── Fiscal history table ──────────────────────────────────────────────────

def _fiscal_history_table(history: List[Dict[str, Any]],
                          ttm: Optional[Dict[str, Any]] = None) -> None:
    from aletheia.ui.validation_badge import (
        convention_flag as _convention_flag,
        CONVENTION_TOOLTIP as _CONVENTION_TOOLTIP,
    )
    if not history:
        return
    # YoY math is computed against the chronologically prior row, then
    # rows are reversed at render time so the most recent FY sits at the
    # top — matches how 10-K comparative statements present periods
    # (current → oldest, top-to-bottom).
    fy_rows: List[Dict[str, Any]] = []
    for i, r in enumerate(history):
        rev = r.get("Revenue")
        prev_rev = history[i - 1].get("Revenue") if i > 0 else None
        rev_growth = (
            (rev / prev_rev - 1.0) * 100
            if rev and prev_rev else None
        )
        fy_rows.append({
            "FY":            r["fiscal_year"],
            "Period":        "FY",
            "Period end":    r.get("period_end_date") or "",
            "Revenue":       (rev/1e9) if rev else None,
            "Rev Growth":    rev_growth,
            "EBITDA":        (r["EBITDA"]/1e9) if r["EBITDA"] else None,
            "Net Income":    (r["NetIncome"]/1e9) if r["NetIncome"] else None,
            "CapEx":         (r["CapEx"]/1e9) if r["CapEx"] else None,
            "FCF":           (r["FCF"]/1e9) if r["FCF"] else None,
            "ROIC":          (r["ROIC"]*100) if r["ROIC"] else None,
            "Quality":       r["QualityScore"],
            "FMP":           _fmp_status_glyph(r.get("FMPStatus")),
        })

    # Reverse FY rows so the most recent FY is at the top of the table.
    # The YoY (Rev Growth) values were already computed against the
    # chronologically prior row, so reversing only affects display order.
    fy_rows.reverse()

    rows: List[Dict[str, Any]] = []
    # TTM (when ingested) sits above the FY rows — it's the freshest
    # snapshot in the table. Rev-growth here is TTM-vs-prior-TTM (sum of
    # FMP quarters [4..7]) so the comparison spans matching 12-month
    # windows and avoids the seasonality trap of TTM-vs-FY-end.
    if ttm and ttm.get("Revenue"):
        ttm_rev = ttm["Revenue"]
        prior_ttm_rev = ttm.get("PriorYearRevenue")
        ttm_growth = (
            (ttm_rev / prior_ttm_rev - 1.0) * 100
            if ttm_rev and prior_ttm_rev else None
        )
        rows.append({
            "FY":            ttm["fiscal_year"],
            "Period":        "TTM",
            "Period end":    ttm.get("period_end_date") or "",
            "Revenue":       (ttm_rev / 1e9) if ttm_rev else None,
            "Rev Growth":    ttm_growth,
            "EBITDA":        (ttm["EBITDA"] / 1e9) if ttm.get("EBITDA") else None,
            "Net Income":    (ttm["NetIncome"] / 1e9) if ttm.get("NetIncome") else None,
            "CapEx":         (ttm["CapEx"] / 1e9) if ttm.get("CapEx") else None,
            "FCF":           (ttm["FCF"] / 1e9) if ttm.get("FCF") else None,
            "ROIC":          (ttm["ROIC"] * 100) if ttm.get("ROIC") else None,
            "Quality":       None,
            "FMP":           _fmp_status_glyph(ttm.get("FMPStatus")),
        })
    rows.extend(fy_rows)
    df = pd.DataFrame(rows)

    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "FY":         st.column_config.NumberColumn(
                "Year", format="%d",
                help=(
                    "Calendar year of the period end. For TTM rows this is "
                    "the year of the latest 10-Q's period_end (e.g. 2026 "
                    "for a TTM ending Mar 2026), NOT a closed fiscal year. "
                    "Read this column together with the Period column."
                ),
            ),
            "Period":     st.column_config.TextColumn(
                "Period", width="small",
                help="TTM = trailing twelve months (latest filing); FY = fiscal year (audited).",
            ),
            "Period end": st.column_config.TextColumn(
                "Period end",
                width="small",
                help="Fiscal-year end date — useful for non-calendar filers like NVDA (Jan), HD/LOW (Feb), COST (Aug/Sep)",
            ),
            "Revenue":    st.column_config.NumberColumn("Revenue", format="$%.1fB"),
            "Rev Growth": st.column_config.NumberColumn(
                "Rev Growth", format="%+.1f%%",
                help=(
                    "FY rows: YoY vs prior FY (closing balance to closing "
                    "balance, 12 months apart). "
                    "TTM row: YoY vs the TTM ending one year earlier (sum "
                    "of quarters [4..7] from FMP), so the comparison spans "
                    "matching 12-month windows — no seasonality bias. "
                    "Suppressed when FMP has fewer than 8 quarters of "
                    "data on file."
                ),
            ),
            "EBITDA":     st.column_config.NumberColumn("EBITDA", format="$%.1fB"),
            "Net Income": st.column_config.NumberColumn("Net Income", format="$%.1fB"),
            "CapEx":      st.column_config.NumberColumn("CapEx", format="$%.1fB"),
            "FCF":        st.column_config.NumberColumn("FCF", format="$%.1fB"),
            "ROIC":       st.column_config.ProgressColumn(
                f"ROIC{_convention_flag('ROIC')}",
                format="%.1f%%", min_value=-10.0, max_value=80.0,
                help=_CONVENTION_TOOLTIP if _convention_flag('ROIC') else None,
            ),
            "Quality":    st.column_config.ProgressColumn(
                "Quality", format="%.2f", min_value=0.0, max_value=1.0,
            ),
            "FMP":        st.column_config.TextColumn(
                "FMP", width="small",
                help=(
                    "Per-FY FMP cross-check: ✓ validated, ⚠ drift recorded "
                    "(non-blocking on historical FYs), ⛔ blocking drift on "
                    "latest FY, — skipped (no FMP data or non-USD filer)."
                ),
            ),
        },
    )


def _render_schema_quality_panel(schema_violations: Dict[str, Any]) -> None:
    """Phase 6 Migration Step 2b — surface schema-contract violations.

    Reads the ``schema_violations`` block on the bundle (populated from
    ``schema_violations_json`` column in DuckDB, captured at
    persistence time by validate_cleaned_record_schema_contract). When
    the framework's shadow/soft mode found violations on this ticker's
    records, this panel renders them as a Data Quality expander so the
    analyst sees exactly what was flagged and on which period."""
    fy_v = schema_violations.get("fy") or []
    ttm_v = schema_violations.get("ttm") or []
    if not fy_v and not ttm_v:
        return  # clean record — nothing to surface

    total = len(fy_v) + len(ttm_v)
    label = (
        f"⚠ Data Quality — {total} schema-contract "
        f"flag{'s' if total != 1 else ''} (non-blocking, click for detail)"
    )
    with st.expander(label, expanded=False):
        st.caption(
            "These flags came from the calculation-layer validation framework "
            "(shadow mode). They indicate data anomalies the framework "
            "detected at persistence time — missing required fields, identity "
            "violations (e.g. A=L+E off), or out-of-range ratios. "
            "Non-blocking: the record is persisted and the dashboard renders "
            "normally; this panel surfaces what was flagged so an analyst "
            "can drill in."
        )
        for label_str, vios in (("Latest FY", fy_v), ("TTM", ttm_v)):
            if not vios:
                continue
            st.markdown(f"**{label_str}**")
            rows = []
            for v in vios:
                rows.append({
                    "Category": v.get("category", "—"),
                    "Field":    v.get("field", "—"),
                    "Value":    str(v.get("value", ""))[:50],
                    "Expected": str(v.get("expected", ""))[:50],
                })
            df_v = pd.DataFrame(rows)
            st.dataframe(df_v, hide_index=True, use_container_width=True)


def _render_validation_drift_panel(
    history: List[Dict[str, Any]],
    ttm: Optional[Dict[str, Any]],
) -> None:
    """Surface non-blocking validation drifts that are stamped on
    the receipt but invisible in the table cells.

    The Multi-year history table already shows a status glyph
    (✓/⚠/⛔/—) per row, but ⚠ alone doesn't tell the analyst WHICH
    metric drifted. This expander surfaces the receipt detail (worst-
    drifting fields per row) so signal that the pipeline already has
    reaches the user. Renders nothing when there are no drifts."""
    drifted_rows: List[Tuple[str, List[Dict[str, Any]]]] = []
    if ttm and ttm.get("FMPDrifts"):
        period_end = ttm.get("period_end_date") or "—"
        drifted_rows.append((f"TTM · ended {period_end}", ttm["FMPDrifts"]))
    for r in history:
        drifts = r.get("FMPDrifts") or []
        if not drifts:
            continue
        label = f"FY{r['fiscal_year']} · ended {r.get('period_end_date') or '—'}"
        drifted_rows.append((label, drifts))

    if not drifted_rows:
        return

    n_rows = len(drifted_rows)
    n_fields = sum(len(d) for _, d in drifted_rows)
    with st.expander(
        f"⚠ {n_fields} field-level drift{'s' if n_fields > 1 else ''} on "
        f"{n_rows} period{'s' if n_rows > 1 else ''} — non-blocking, click for detail",
        expanded=False,
    ):
        st.caption(
            "These drifts were caught by the validation gates and recorded "
            "on the per-row receipt. They're non-blocking by design (the "
            "primary tier owns the gate; these are second-source regression "
            "detectors), but worth a manual cross-check against the latest "
            "filing or an 8-K. P0 fields would have blocked the row write."
        )
        for label, drifts in drifted_rows:
            st.markdown(f"**{label}**")
            rows = []
            for d in drifts[:6]:   # cap at top 6 per period
                ours = d.get("ours")
                fmp  = d.get("fmp")
                drift_pct = d.get("drift_pct")
                rows.append({
                    "Field":  d.get("field"),
                    "Ours":   _fmt_drift_value(ours),
                    "FMP":    _fmt_drift_value(fmp),
                    "Drift":  (f"{drift_pct * 100:+.1f}%"
                               if isinstance(drift_pct, (int, float)) else "—"),
                    "Tier":   d.get("tier") or "—",
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _fmt_drift_value(v: Any) -> str:
    """Compact formatter for the drift detail panel — handles billions
    (>1B), millions, ratios, and scalar fractions in the same column."""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:   # nan
        return "—"
    abs_f = abs(f)
    if abs_f > 1e9:
        return f"${f / 1e9:+,.2f}B"
    if abs_f > 1e6:
        return f"${f / 1e6:+,.1f}M"
    if abs_f > 10:
        return f"{f:+,.2f}"
    return f"{f:.4f}"


def _fmp_status_glyph(status: Optional[str]) -> str:
    """Compact glyph for the FMP per-FY validation column."""
    if status == "validated":
        return "✓"
    if status == "drift":
        return "⚠"
    if status == "blocking_drift":
        return "⛔"
    if status == "skipped":
        return "—"
    return "·"


# ── Main entry point ───────────────────────────────────────────────────────

def render_financials_view(ticker: str, bundle: Dict[str, Any]) -> None:
    """Render the redesigned Financials tab for `ticker`. `bundle` is the
    payload returned by `/ticker/<ticker>/financials`."""
    if not ticker or not bundle or bundle.get("error"):
        st.error(bundle.get("error") if bundle else "No financials available.")
        return

    ident     = bundle["identity"]
    inc       = bundle["income_statement"]
    bs        = bundle["balance_sheet"]
    ret       = bundle["returns_capital"]
    leases    = bundle["lease_items"]
    prior_inc    = bundle.get("prior_income_statement") or {}
    prior_bs     = bundle.get("prior_balance_sheet") or {}
    prior_ret    = bundle.get("prior_returns_capital") or {}
    prior_leases = bundle.get("prior_lease_items") or {}
    prior_fy     = bundle.get("prior_fiscal_year")
    history   = bundle.get("fiscal_history") or []
    ttm       = bundle.get("ttm_snapshot")
    freshness = bundle.get("freshness") or {}

    # ── Header: ticker name + validation banner ──────────────────────────
    st.markdown(f"## {ticker}")
    _validation_banner(ticker, ident["fiscal_year"], ident)

    # ── Filing freshness banner (Phase Q-7 minimal) ─────────────────────
    _freshness_banner(freshness)

    # ── Hero strip ────────────────────────────────────────────────────────
    _hero_strip(ticker, inc, history)

    # ── Quality issues, if any ────────────────────────────────────────────
    if ident.get("warnings") or ident.get("errors") or bundle.get("bypass"):
        with st.expander("Quality issues", expanded=bool(ident.get("errors"))):
            if ident.get("errors"):
                for e in ident["errors"]:
                    st.error(e)
            if ident.get("warnings"):
                for w in ident["warnings"]:
                    st.warning(w)
            if bundle.get("bypass"):
                st.info(f"DCF bypass: {bundle['bypass']}")

    st.markdown("---")

    # ── Two-column layout: Income + Balance ───────────────────────────────
    # Prior-FY snapshots are built server-side from the second-to-last FY
    # row so every line (not just the 5 fields tracked in fiscal_history)
    # has a Prior FY + YoY value.
    def pi(field: str) -> Optional[float]:
        return prior_inc.get(field)

    def pb(field: str) -> Optional[float]:
        return prior_bs.get(field)

    def pr(field: str) -> Optional[float]:
        return prior_ret.get(field)

    def pl(field: str) -> Optional[float]:
        return prior_leases.get(field)

    # Split the prior "Income Statement & Cash Flow" mega-table into two
    # focused sections. Income Statement walks Revenue → EPS; Cash Flow
    # & CapEx is the funds-flow + reinvestment view. Keeps each table
    # short enough to scan top-to-bottom without a scroll.
    inc_rows = [
        # (display_label, validation badge key, curr, prior, format)
        ("Revenue",            "Revenue",            inc["Revenue"],          pi("Revenue"),           "$"),
        ("COGS",               "COGS",               inc["COGS"],             pi("COGS"),              "$"),
        ("Gross Margin",       "Gross Margin",       inc["GrossMargin_Pct"],  pi("GrossMargin_Pct"),   "%"),
        ("R&D",                None,                 inc["RnD"],              pi("RnD"),               "$"),
        ("SG&A",               None,                 inc["SGnA"],             pi("SGnA"),              "$"),
        ("Operating Income",   "Operating Income",   inc["OperatingIncome"],  pi("OperatingIncome"),   "$"),
        # Ex-unusual: strips impairment + restructuring from reported OpInc.
        # Capital-IQ-aligned when source = xbrl_discrete_tags; FMP-only
        # approximation (gated by 5% materiality threshold) otherwise.
        ("Operating Income — ex unusual", None, inc.get("OperatingIncome_ExUnusual"), pi("OperatingIncome_ExUnusual"), "$"),
        ("EBIT Margin",        "EBIT Margin",        inc["EBIT_Margin_Pct"],  pi("EBIT_Margin_Pct"),   "%"),
        ("EBITDA",             "EBITDA",             inc["EBITDA"],           pi("EBITDA"),            "$"),
        ("EBITDA — ex unusual", None, inc.get("EBITDA_ExUnusual"), pi("EBITDA_ExUnusual"), "$"),
        ("EBITDA Margin",      "EBITDA Margin",      inc["EBITDA_Margin_Pct"],pi("EBITDA_Margin_Pct"), "%"),
        ("D&A",                None,                 inc["DepreciationAmortization"], pi("DepreciationAmortization"), "$"),
        ("NOPAT",              None,                 inc["NOPAT"],            pi("NOPAT"),             "$"),
        ("Net Income",         "Net Income",         inc["NetIncome"],        pi("NetIncome"),         "$"),
        ("Diluted EPS",        "Diluted EPS",        inc["DilutedEPS"],       pi("DilutedEPS"),        "n"),
    ]

    cf_rows = [
        ("Operating CF",       "Operating CF",       inc["OperatingCF"],      pi("OperatingCF"),       "$"),
        ("Investing CF",       None,                 inc["InvestingCF"],      pi("InvestingCF"),       "$"),
        ("Financing CF",       None,                 inc["FinancingCF"],      pi("FinancingCF"),       "$"),
        ("FCF",                "FCF",                inc["FCF"],              pi("FCF"),               "$"),
        ("FCFF",               None,                 inc["FCFF"],             pi("FCFF"),              "$"),
        ("FCF Margin",         None,                 inc["FCF_Margin_Pct"],   pi("FCF_Margin_Pct"),    "%"),
        ("CapEx",              "CapEx",              inc["CapEx"],            pi("CapEx"),             "$"),
        ("Maintenance CapEx",  None,                 inc["MaintenanceCapEx"], pi("MaintenanceCapEx"),  "$"),
        ("Growth CapEx",       None,                 inc["GrowthCapEx"],      pi("GrowthCapEx"),       "$"),
    ]

    bs_rows = [
        ("Total Assets",         "Total Assets",         bs["TotalAssets"],         pb("TotalAssets"),         "$"),
        ("Cash",                 "Cash",                 bs["Cash"],                pb("Cash"),                "$"),
        ("Short-Term Investments","Short-Term Investments", bs["ShortTermInvestments"], pb("ShortTermInvestments"), "$"),
        ("Accounts Receivable",  "Accounts Receivable",  bs["AccountsReceivable"],  pb("AccountsReceivable"),  "$"),
        ("Inventory",            "Inventory",            bs["Inventory"],           pb("Inventory"),           "$"),
        ("PPE Net",              "PPE Net",              bs["PPE_Net"],             pb("PPE_Net"),             "$"),
        ("PPE Gross",            None,                   bs["PPE_Gross"],           pb("PPE_Gross"),           "$"),
        ("Accum. Depreciation",  None,                   bs["AccumulatedDepreciation"], pb("AccumulatedDepreciation"), "$"),
        ("Total Liabilities",    "Total Liabilities",    bs["TotalLiabilities"],    pb("TotalLiabilities"),    "$"),
        ("Current Liabilities",  "Current Liabilities",  bs["LiabilitiesCurrent"],  pb("LiabilitiesCurrent"),  "$"),
        ("Short-Term Debt",      "Short-Term Debt",      bs["ShortTermDebt"],       pb("ShortTermDebt"),       "$"),
        ("Long-Term Debt",       "Long-Term Debt",       bs["LongTermDebt"],        pb("LongTermDebt"),        "$"),
        ("Accounts Payable",     "Accounts Payable",     bs["AccountsPayable"],     pb("AccountsPayable"),     "$"),
        ("Total Equity",         "Total Equity",         bs["TotalEquity"],         pb("TotalEquity"),         "$"),
        ("Net Working Capital",  None,                   bs["NWC"],                 pb("NWC"),                 "$"),
        ("Net Debt",             None,                   bs["NetDebt"],             pb("NetDebt"),             "$"),
    ]

    # Column-header labels carry the actual fiscal years so analysts don't
    # have to mentally map "Current FY / Prior FY" to dates — matters most
    # for non-calendar filers (NVDA, ASML, ORCL). Section titles drop the
    # FY suffix now that the column headers carry it.
    curr_header = f"FY{ident['fiscal_year']}"
    prior_header = f"FY{prior_fy}" if prior_fy else "Prior FY"

    # Row 1: Income Statement (P&L walk) | Balance Sheet
    ls, rs = st.columns(2)
    with ls:
        _statement_table(
            "Income Statement", ticker, inc_rows,
            current_header=curr_header, prior_header=prior_header,
        )
        _ex_unusual_provenance(inc)
    with rs:
        _statement_table(
            "Balance Sheet", ticker, bs_rows,
            current_header=curr_header, prior_header=prior_header,
        )

    # ── Cash flow & capital allocation ────────────────────────────────────
    from aletheia.ui.validation_badge import convention_flag
    cap_rows = [
        (f"ROIC{convention_flag('ROIC')}",                "ROIC",         ret["ROIC"],          pr("ROIC"),          "%"),
        ("ROE",                 "ROE",          ret["ROE"],           pr("ROE"),           "%"),
        (f"Invested Capital{convention_flag('Invested Capital')}",    "Invested Capital", ret["InvestedCapital"], pr("InvestedCapital"), "$"),
        ("Diluted Shares",      "Diluted Shares", ret["SharesDiluted"], pr("SharesDiluted"), "n"),
        ("Basic Shares",        None,           ret["SharesBasic"],    pr("SharesBasic"),   "n"),
        ("Outstanding Shares",  None,           ret["SharesOutstanding"], pr("SharesOutstanding"), "n"),
        ("Buybacks",            "Buybacks",     ret["Buybacks"],       pr("Buybacks"),      "$"),
        ("SBC",                 None,           ret["SBC"],            pr("SBC"),           "$"),
        ("Net Buyback after SBC", None,         ret["NetBuyback_AfterSBC"], pr("NetBuyback_AfterSBC"), "$"),
        ("SBC % of FCF",        None,           ret["SBC_PctFCF"],     pr("SBC_PctFCF"),    "%"),
        ("Dilution %",          None,           ret["DilutionPct"],    pr("DilutionPct"),   "%"),
        ("Dividends Paid",      "Dividends Paid", ret["DividendsPaid"], pr("DividendsPaid"), "$"),
    ]

    # Row 2: Cash Flow & Reinvestment | Returns & Capital Structure
    ls2, rs2 = st.columns(2)
    with ls2:
        _statement_table(
            "Cash Flow & Reinvestment", ticker, cf_rows,
            current_header=curr_header, prior_header=prior_header,
        )
    with rs2:
        _statement_table(
            "Returns & Capital Structure", ticker, cap_rows,
            current_header=curr_header, prior_header=prior_header,
        )

    # ── Lease items (hidden when filer has no lease disclosures) ─────────
    lease_rows = [
        ("ROU Asset (Operating)", None, leases["ROUAsset_Operating"],         pl("ROUAsset_Operating"),         "$"),
        ("ROU Asset (Finance)",   None, leases["ROUAsset_Finance"],           pl("ROUAsset_Finance"),           "$"),
        ("Lease Liab Operating",  None, leases["LeaseLiability_Operating_Total"], pl("LeaseLiability_Operating_Total"), "$"),
        ("Lease Liab Finance",    None, leases["LeaseLiability_Finance_Total"],   pl("LeaseLiability_Finance_Total"),   "$"),
        ("Lease Cost (annual)",   None, leases["LeaseCost"],                  pl("LeaseCost"),                  "$"),
    ]
    has_lease_data = any(
        curr is not None or prior is not None
        for _, _, curr, prior, _ in lease_rows
    )
    if has_lease_data:
        with st.expander("Lease items (ASC 842)", expanded=False):
            _statement_table(
                "Lease Items", ticker, lease_rows,
                current_header=curr_header, prior_header=prior_header,
            )

    # ── Fiscal history ────────────────────────────────────────────────────
    if history:
        st.markdown("---")
        st.markdown("#### Multi-year history")
        _fiscal_history_table(history, ttm=ttm)
        _render_validation_drift_panel(history, ttm)
        # Phase 6 Step 2b: Data Quality panel sourced from
        # validate_cleaned_record_schema_contract persistence-time output.
        _render_schema_quality_panel(bundle.get("schema_violations") or {})

    # ── Validation legend ─────────────────────────────────────────────────
    st.markdown("---")
    st.caption(
        "🟢 validated against SEC/FMP within 1% · "
        "🟡 within 5% (documented difference) · "
        "🔴 >5% drift · "
        "⚪ field not present on validator side · "
        "· not yet validated"
    )

    # ── DCF section preserved (delegated to existing block) ──────────────
    if bundle.get("dcf_inputs"):
        _render_dcf_section(bundle)


def _render_dcf_section(bundle: Dict[str, Any]) -> None:
    """Render the DCF subsections (inputs, scenarios, projections, terminal)."""
    st.markdown("---")
    st.markdown("#### DCF Analysis")

    dcfi    = bundle["dcf_inputs"]
    scens   = bundle["dcf_scenarios"]
    projs   = bundle.get("projections") or []

    a, b = st.columns([1, 1])
    with a:
        st.markdown("##### Inputs")
        inp_df = pd.DataFrame([
            {"Field": "Current Price",   "Value": f"${dcfi['current_price']:,.2f}"},
            {"Field": "Market Cap",      "Value": _bn(dcfi['market_cap'])},
            {"Field": "Diluted Shares",  "Value": _bn(dcfi['shares_diluted'], dp=3)},
            {"Field": "Risk-free Rate",  "Value": _pct(dcfi['risk_free_rate'])},
            {"Field": "Beta",            "Value": f"{dcfi['beta']:,.2f}"},
            {"Field": "WACC (base)",     "Value": _pct(dcfi['wacc_base'])},
            {"Field": "Tax Rate",        "Value": _pct(dcfi['tax_rate'])},
        ])
        st.dataframe(inp_df, hide_index=True, use_container_width=True)

    with b:
        st.markdown("##### Scenarios")
        scen_rows = []
        for name in ("bull", "base", "bear"):
            s = scens.get(name) or {}
            if s:
                scen_rows.append({
                    "Scenario":    s["name"],
                    "EV":          (s["EV"]/1e9) if s["EV"] else None,
                    "IPS":         s["IPS"],
                    "Upside":      s["Upside_Pct"],
                    "WACC":        (s["WACC"]*100) if s["WACC"] else None,
                    "g_term":      (s["TerminalGrowth"]*100) if s["TerminalGrowth"] else None,
                    "TV/EV":       (s["TV_Pct_EV"]*100) if s["TV_Pct_EV"] else None,
                    "EV/EBITDA":   s["ImpliedEV_EBITDA"],
                })
        if scen_rows:
            df = pd.DataFrame(scen_rows)
            st.dataframe(
                df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "EV":        st.column_config.NumberColumn("EV", format="$%.0fB"),
                    "IPS":       st.column_config.NumberColumn("IPS", format="$%.2f"),
                    "Upside":    st.column_config.NumberColumn("Upside", format="%+.1f%%"),
                    "WACC":      st.column_config.NumberColumn("WACC", format="%.2f%%"),
                    "g_term":    st.column_config.NumberColumn("g_term", format="%.2f%%"),
                    "TV/EV":     st.column_config.NumberColumn("TV/EV", format="%.1f%%"),
                    "EV/EBITDA": st.column_config.NumberColumn("EV/EBITDA", format="%.1fx"),
                },
            )

    if projs:
        st.markdown("##### Base-case projections")
        proj_df = pd.DataFrame([{
            "Yr":      p["year"],
            "FY":      p["fiscal_year"],
            "Revenue": (p["revenue"]/1e9) if p["revenue"] else None,
            "EBIT":    (p["ebit"]/1e9) if p["ebit"] else None,
            "NOPAT":   (p["nopat"]/1e9) if p["nopat"] else None,
            "CapEx":   (p["capex"]/1e9) if p["capex"] else None,
            "FCFF":    (p["fcff"]/1e9) if p["fcff"] else None,
            "PV(FCFF)":(p["pv_fcff"]/1e9) if p["pv_fcff"] else None,
        } for p in projs])
        st.dataframe(
            proj_df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Revenue":  st.column_config.NumberColumn("Revenue", format="$%.1fB"),
                "EBIT":     st.column_config.NumberColumn("EBIT", format="$%.1fB"),
                "NOPAT":    st.column_config.NumberColumn("NOPAT", format="$%.1fB"),
                "CapEx":    st.column_config.NumberColumn("CapEx", format="$%.1fB"),
                "FCFF":     st.column_config.NumberColumn("FCFF", format="$%.1fB"),
                "PV(FCFF)": st.column_config.NumberColumn("PV(FCFF)", format="$%.1fB"),
            },
        )

    # Terminal value + base assumptions side-by-side
    tv = bundle.get("terminal_value") or {}
    asn = bundle.get("assumptions") or {}
    if tv or asn:
        c, d = st.columns(2)
        if tv:
            with c:
                st.markdown("##### Terminal value (base)")
                tv_df = pd.DataFrame([
                    {"Metric": "Gordon TV",            "Value": _bn(tv.get("gordon_tv"), dp=0)},
                    {"Metric": "Reinvestment TV",      "Value": _bn(tv.get("reinvestment_tv"), dp=0)},
                    {"Metric": "TV used",              "Value": _bn(tv.get("tv_used"), dp=0)},
                    {"Metric": "PV of TV",             "Value": _bn(tv.get("pv_tv"), dp=0)},
                    {"Metric": "TV % of EV",           "Value": _pct(tv.get("tv_pct_of_ev"))},
                    {"Metric": "Implied Terminal EV/EBITDA",
                     "Value": (f"{tv['implied_tv_ebitda_multiple']:.1f}x"
                               if tv.get("implied_tv_ebitda_multiple") else "—")},
                ])
                st.dataframe(tv_df, hide_index=True, use_container_width=True)
        if asn:
            with d:
                st.markdown("##### Base assumptions")
                asn_df = pd.DataFrame([
                    {"Field": "CAGR Y1-5",          "Value": _pct(asn.get("revenue_cagr_y1_5"))},
                    {"Field": "CAGR Y6-10",         "Value": _pct(asn.get("revenue_cagr_y6_10"))},
                    {"Field": "EBIT margin start",  "Value": _pct(asn.get("ebit_margin_current"))},
                    {"Field": "EBIT margin term.",  "Value": _pct(asn.get("ebit_margin_terminal"))},
                    {"Field": "CapEx % revenue",    "Value": _pct(asn.get("capex_pct_revenue"))},
                    {"Field": "D&A % revenue",      "Value": _pct(asn.get("da_pct_revenue"))},
                    {"Field": "NWC % revenue",      "Value": _pct(asn.get("nwc_pct_revenue"))},
                    {"Field": "Tax rate",           "Value": _pct(asn.get("tax_rate"))},
                    {"Field": "WACC",               "Value": _pct(asn.get("wacc"))},
                    {"Field": "Terminal growth",    "Value": _pct(asn.get("terminal_growth"))},
                    {"Field": "Terminal ROIC",      "Value": _pct(asn.get("terminal_roic"))},
                    {"Field": "Base ROIC",          "Value": _pct(asn.get("base_roic"))},
                ])
                st.dataframe(asn_df, hide_index=True, use_container_width=True)
                if asn.get("justification"):
                    st.caption(asn["justification"])
