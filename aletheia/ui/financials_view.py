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


# ── DCF editable-assumptions support ────────────────────────────────────────

_FV_API_BASE = "http://localhost:8000"

# (label, assumptions-bundle key, ScenarioOverride field). The 7 wired
# editable fields. Order matches the original read-only table.
_DCF_EDITABLE = [
    ("CAGR Y1-5",          "revenue_cagr_y1_5",    "revenue_growth_y1_5"),
    ("CAGR Y6-10",         "revenue_cagr_y6_10",   "revenue_growth_y6_10"),
    ("EBIT margin term.",  "ebit_margin_terminal", "terminal_ebit_margin"),
    ("CapEx % revenue",    "capex_pct_revenue",    "capex_pct_revenue"),
    ("Tax rate",           "tax_rate",             "tax_rate"),
    ("WACC",               "wacc",                 "discount_rate"),
    ("Terminal growth",    "terminal_growth",      "terminal_growth"),
    ("Terminal ROIC",      "terminal_roic",        "terminal_roic"),
]
# Model-derived, not yet overridable (no ScenarioOverride field).
_DCF_READONLY = [
    ("EBIT margin start",  "ebit_margin_current"),
    ("D&A % revenue",      "da_pct_revenue"),
    ("NWC % revenue",      "nwc_pct_revenue"),
    ("Base ROIC",          "base_roic"),
]


def _dcf_api(method: str, path: str, body: Optional[dict] = None):
    """Minimal API client for the DCF-override endpoints. Returns
    (data, error_message). error_message is None on success."""
    import httpx
    try:
        r = httpx.request(method, f"{_FV_API_BASE}{path}",
                          json=body, timeout=30)
    except httpx.RequestError as exc:
        return None, f"Cannot reach API: {exc}"
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        return None, detail if isinstance(detail, str) else (
            (detail or {}).get("message", "") + " "
            + "; ".join((detail or {}).get("errors", []))
            if isinstance(detail, dict) else str(detail)
        )
    return r.json(), None


def _render_editable_assumptions(ticker: str, asn: Dict[str, Any]) -> None:
    """Editable Base-assumptions panel. Pre-fills with the ticker's effective
    assumptions (model-derived + persisted overrides), lets the analyst edit
    the 7 wired fields within bounds, then persists + recomputes via the API.
    Falls back to a read-only table if the assumptions endpoint is unavailable
    (e.g. specialized non-FCFF tickers)."""
    st.markdown("##### Base assumptions")

    payload, err = _dcf_api("GET", f"/ticker/{ticker}/dcf/assumptions")
    if err or not payload:
        # Read-only fallback — keeps the panel working if the endpoint is down.
        st.caption("Editing unavailable (assumptions endpoint not reachable); "
                   "showing model values.")
        ro = [{"Field": lbl, "Value": _pct(asn.get(k))}
              for lbl, k, _ in _DCF_EDITABLE] + \
             [{"Field": lbl, "Value": _pct(asn.get(k))}
              for lbl, k in _DCF_READONLY]
        st.dataframe(pd.DataFrame(ro), hide_index=True, use_container_width=True)
        return

    eff = payload.get("assumptions") or {}
    baseline = payload.get("baseline_assumptions") or {}
    bounds = payload.get("bounds") or {}
    overridden = set(payload.get("overridden_fields") or [])
    prov = payload.get("provenance") or {}
    validation = payload.get("validation") or {}

    if overridden:
        who = prov.get("updated_by", "analyst")
        when = (prov.get("updated_at") or "")[:10]
        st.info(f"✎ Analyst overrides active on {len(overridden)} field(s) "
                f"— set by {who}{(' on ' + when) if when else ''}.")

    with st.form(f"dcf_assumptions_{ticker}"):
        edits: Dict[str, float] = {}
        for label, akey, okey in _DCF_EDITABLE:
            cur = eff.get(akey)
            lo, hi = bounds.get(okey, [None, None])
            val_pct = float(cur * 100) if cur is not None else 0.0
            lo_pct = lo * 100 if lo is not None else None
            hi_pct = hi * 100 if hi is not None else None
            # The model value can legitimately sit outside the override
            # bounds — e.g. CHWY's terminal EBIT margin (~1.6%) is below the
            # 5% floor (thin-margin retailer). Streamlit raises if the
            # pre-filled value falls outside [min, max], so widen the input
            # range to include it. The PUT validator still enforces the
            # canonical bounds on save.
            if lo_pct is not None:
                lo_pct = min(lo_pct, val_pct)
            if hi_pct is not None:
                hi_pct = max(hi_pct, val_pct)
            badge = " ✎" if okey in overridden else ""
            val = st.number_input(
                f"{label}{badge} (%)",
                min_value=lo_pct,
                max_value=hi_pct,
                value=val_pct,
                step=0.1, format="%.2f",
                key=f"dcf_in_{ticker}_{okey}",
                help=("Analyst override active" if okey in overridden
                      else "Model-derived; edit to override"),
            )
            edits[okey] = val / 100.0

        # Read-only model-derived fields.
        st.caption("Model-derived (not editable): " + " · ".join(
            f"{lbl} {_pct(eff.get(k))}" for lbl, k in _DCF_READONLY))

        submitted = st.form_submit_button("↻ Recompute", type="primary")

    if submitted:
        # Only send fields that differ from the model baseline — overrides
        # capture genuine deviations, not pins on model-equal values.
        body: Dict[str, Any] = {"updated_by": "analyst",
                                "note": "Edited via Financials tab"}
        for label, akey, okey in _DCF_EDITABLE:
            base_v = baseline.get(akey)
            new_v = edits[okey]
            if base_v is None or abs(new_v - base_v) > 1e-6:
                body[okey] = new_v
        result, perr = _dcf_api("PUT", f"/ticker/{ticker}/dcf/overrides", body)
        if perr:
            st.error(f"Recompute rejected: {perr}")
        else:
            st.cache_data.clear()
            st.success("Assumptions saved — recomputing valuation…")
            st.rerun()

    # Reset (outside the form so it's a plain button).
    if overridden and st.button("⟲ Reset to model defaults",
                                key=f"dcf_reset_{ticker}"):
        _, derr = _dcf_api("DELETE", f"/ticker/{ticker}/dcf/overrides")
        if derr:
            st.error(f"Reset failed: {derr}")
        else:
            st.cache_data.clear()
            st.success("Reverted to model defaults — recomputing…")
            st.rerun()

    # Validation verdict (also drives the Stage 4 gate).
    vstatus = validation.get("status")
    if vstatus == "error":
        st.error("⛔ Assumptions invalid: " + "; ".join(validation.get("errors", [])))
    elif vstatus == "warn":
        st.warning("⚠ " + "; ".join(validation.get("warnings", [])))


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

    # Fields that got an actual external verdict (matched or mismatched).
    # The remainder are "missing" — no SEC/FMP source to compare against, which
    # is *unverified*, NOT *failed*. Conflating those two was the old banner's
    # core defect: it painted "0/16 cross-checked" bright red as if 16 fields
    # had failed, when zero had actually drifted.
    n_checked   = n_validated + n_near + n_drift
    n_unchecked = max(total - n_checked, 0)

    # Border/severity is driven by data-INTEGRITY only: hard errors, or fields
    # that were checked against SEC/FMP and drifted. Unverified data stays
    # neutral (grey, not red); a clean cross-check is green. Non-blocking
    # cleaning warnings get their own amber chip below but do NOT escalate the
    # border — nearly every ticker carries a routine note, so amber-everywhere
    # would drain the signal from the border.
    if errors > 0 or n_drift > 0:
        accent = "#ef4444"       # red — genuine mismatch / error
    elif n_checked > 0:
        accent = "#10b981"       # green — cross-checked clean
    else:
        accent = "#6b7280"       # neutral grey — clean but unverified

    # Cross-check chip: only frame it as a pass ratio when something was
    # actually checked; otherwise say plainly that no external source was found.
    if n_checked == 0:
        xcheck_html = (
            f'<span>Cross-check '
            f'<strong style="color:#a1a1aa">none available</strong>'
            f'<span style="color:#71717a"> ({total} fields, no SEC/FMP source)</span>'
            f'</span>'
        )
    else:
        drift_html = (
            f'<span style="color:#ef4444"> · {n_drift} drift ⚠</span>'
            if n_drift else ""
        )
        near_html = (
            f'<span style="color:#f59e0b"> · {n_near} near ≈</span>'
            if n_near else ""
        )
        unchecked_html = (
            f'<span style="color:#71717a"> · {n_unchecked} unverified</span>'
            if n_unchecked else ""
        )
        xcheck_html = (
            f'<span>Cross-checked '
            f'<strong style="color:{accent}">{n_validated + n_near}/{n_checked}</strong> '
            f'<span style="color:#10b981">{n_validated} ✓</span>'
            f'{near_html}{drift_html}{unchecked_html}'
            f'</span>'
        )

    warn_color = "#f59e0b" if warnings else "#a1a1aa"
    err_color = "#ef4444" if errors else "#a1a1aa"

    fy_display = f"FY{fy}"
    if period_end_str:
        fy_display = f"FY{fy} <span style='opacity:0.7;font-weight:400'>· ended {period_end_str}</span>"

    st.markdown(
        f"""
<div style="
    display:flex; gap:14px; align-items:center; flex-wrap:wrap;
    padding:12px 16px; background:rgba(128,128,128,0.10);
    border-left:3px solid {accent}; border-radius:4px;
    font-family:'DM Mono',monospace; font-size:12.5px;
    color:#c4c4cc; margin:8px 0 16px 0;
">
    <span><strong style="color:#f4f4f5">{fy_display}</strong></span>
    <span style="color:#71717a">·</span>
    <span>Quality <strong style="color:#f4f4f5">{quality:.2f}</strong></span>
    <span style="color:#71717a">·</span>
    {xcheck_html}
    <span style="color:#71717a">·</span>
    <span><strong style="color:{warn_color}">{warnings}</strong> warnings ·
        <strong style="color:{err_color}">{errors}</strong> errors</span>
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
        prev = history[i - 1] if i > 0 else None
        prev_rev = prev.get("Revenue") if prev else None
        # Only a CONSECUTIVE prior fiscal year is a valid YoY base. When the
        # ingested series has a gap (e.g. CRM jumps FY2005 → FY2011 with
        # 2006-2010 missing), a naive ratio yields a spurious multi-year
        # "+840%". Suppress growth across gaps rather than misreport it.
        consecutive = bool(prev) and (r.get("fiscal_year", 0) - prev.get("fiscal_year", 0) == 1)
        rev_growth = (
            (rev / prev_rev - 1.0) * 100
            if rev and prev_rev and consecutive else None
        )
        fy_rows.append({
            "FY":            r["fiscal_year"],
            "Period":        "FY",
            "Period end":    r.get("period_end_date") or "",
            "Revenue":       (rev/1e9) if rev else None,
            "Rev Growth":    rev_growth,
            "EBIT":          (r["EBIT"]/1e9) if r.get("EBIT") else None,
            "EBIT %":        (r["EBIT"]/rev*100) if (r.get("EBIT") and rev) else None,
            "Norm. EBIT":    (r["NormEBIT"]/1e9) if r.get("NormEBIT") else None,
            "EBITDA":        (r["EBITDA"]/1e9) if r["EBITDA"] else None,
            "Net Income":    (r["NetIncome"]/1e9) if r["NetIncome"] else None,
            "Tax rate":      (r["TaxRate"]*100) if r.get("TaxRate") is not None else None,
            "CapEx":         (r["CapEx"]/1e9) if r["CapEx"] else None,
            "CapEx %":       (r["CapEx"]/rev*100) if (r.get("CapEx") and rev) else None,
            "FCF":           (r["FCF"]/1e9) if r["FCF"] else None,
            "FCF %":         (r["FCF"]/rev*100) if (r.get("FCF") and rev) else None,
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
            "EBIT":          (ttm["EBIT"] / 1e9) if ttm.get("EBIT") else None,
            "EBIT %":        (ttm["EBIT"] / ttm_rev * 100) if (ttm.get("EBIT") and ttm_rev) else None,
            "Norm. EBIT":    (ttm["NormEBIT"] / 1e9) if ttm.get("NormEBIT") else None,
            "EBITDA":        (ttm["EBITDA"] / 1e9) if ttm.get("EBITDA") else None,
            "Net Income":    (ttm["NetIncome"] / 1e9) if ttm.get("NetIncome") else None,
            "Tax rate":      (ttm["TaxRate"]*100) if ttm.get("TaxRate") is not None else None,
            "CapEx":         (ttm["CapEx"] / 1e9) if ttm.get("CapEx") else None,
            "CapEx %":       (ttm["CapEx"] / ttm_rev * 100) if (ttm.get("CapEx") and ttm_rev) else None,
            "FCF":           (ttm["FCF"] / 1e9) if ttm.get("FCF") else None,
            "FCF %":         (ttm["FCF"] / ttm_rev * 100) if (ttm.get("FCF") and ttm_rev) else None,
            "ROIC":          (ttm["ROIC"] * 100) if ttm.get("ROIC") else None,
            "Quality":       None,
            "FMP":           _fmp_status_glyph(ttm.get("FMPStatus")),
        })
    rows.extend(fy_rows)

    # Average row across all FY records, pinned to the top. Computed from the
    # numeric FY values (before the dollar columns are stringified below) so
    # dollar averages format the same way. TTM is excluded — it's a current
    # snapshot, not a historical year.
    def _avg(col):
        vals = [r.get(col) for r in fy_rows
                if isinstance(r.get(col), (int, float))]
        return (sum(vals) / len(vals)) if vals else None

    def _median(col):
        # Robust central tendency for rate columns (e.g. Rev Growth), where the
        # arithmetic mean of YoY %s overweights early small-base years.
        vals = sorted(r.get(col) for r in fy_rows
                      if isinstance(r.get(col), (int, float)))
        if not vals:
            return None
        n = len(vals)
        return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2

    if fy_rows:
        # Keys MUST match the FY/TTM row order so pandas keeps column order
        # (this row is inserted first, so its key order wins).
        avg_row = {
            "FY":         None,
            "Period":     "Avg",
            "Period end": f"{len(fy_rows)} FY",
            "Revenue":    _avg("Revenue"),
            "Rev Growth": _median("Rev Growth"),   # median = typical-year growth
            "EBIT":       _avg("EBIT"),
            "EBIT %":     _avg("EBIT %"),
            "Norm. EBIT": _avg("Norm. EBIT"),
            "EBITDA":     _avg("EBITDA"),
            "Net Income": _avg("Net Income"),
            "Tax rate":   _avg("Tax rate"),
            "CapEx":      _avg("CapEx"),
            "CapEx %":    _avg("CapEx %"),
            "FCF":        _avg("FCF"),
            "FCF %":      _avg("FCF %"),
            "ROIC":       _avg("ROIC"),
            "Quality":    _avg("Quality"),
            "FMP":        "",
        }
        rows.insert(0, avg_row)

    df = pd.DataFrame(rows)

    # Adaptive $ formatting. The dollar columns above were scaled to
    # billions; a fixed "$%.1fB" format crushes small-cap / asset-light
    # values to "$0.0B" (e.g. CELH CapEx ~$36M, ~1.4% of revenue). Render
    # them as auto-scaled strings instead ($B for ≥$1B, $M below) so the
    # value is legible across the whole universe — mega-caps still read
    # "$390.0B", CELH reads "$36.1M". _bn expects raw dollars, so undo the
    # /1e9 scaling. Numeric columns (Rev Growth, ROIC, Quality) stay typed
    # for their NumberColumn / ProgressColumn rendering.
    _dollar_cols = ("Revenue", "EBIT", "Norm. EBIT", "EBITDA", "Net Income", "CapEx", "FCF")
    for _c in _dollar_cols:
        if _c in df.columns:
            df[_c] = df[_c].map(
                lambda x: _bn(x * 1e9, 1) if x is not None and x == x else ""
            )

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
            "Revenue":    st.column_config.TextColumn("Revenue", width="small"),
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
            "EBIT":       st.column_config.TextColumn("EBIT", width="small"),
            "EBIT %":     st.column_config.NumberColumn(
                "EBIT %", format="%.1f%%",
                help="EBIT (operating income) as % of revenue — the operating margin."),
            "Norm. EBIT": st.column_config.TextColumn(
                "Norm. EBIT", width="small",
                help=(
                    "Normalized EBIT — the operating income the DCF engine uses "
                    "as its base. Ex-unusual operating income (one-time "
                    "impairments / restructuring stripped) when available, else "
                    "reported EBIT. Differs from the reported EBIT column in "
                    "years with large one-time charges (e.g. a goodwill "
                    "writedown), which is why a low reported-EBIT year can still "
                    "anchor a healthy DCF base margin."
                )),
            "EBITDA":     st.column_config.TextColumn("EBITDA", width="small"),
            "Net Income": st.column_config.TextColumn("Net Income", width="small"),
            "Tax rate":   st.column_config.NumberColumn(
                "Tax rate", format="%.1f%%",
                help="GAAP effective tax rate. Can be negative (tax benefit) "
                     "or near-zero in years with large credits/one-offs."),
            "CapEx":      st.column_config.TextColumn("CapEx", width="small"),
            "CapEx %":    st.column_config.NumberColumn(
                "CapEx %", format="%.1f%%",
                help="CapEx as % of revenue — capital intensity."),
            "FCF":        st.column_config.TextColumn("FCF", width="small"),
            "FCF %":      st.column_config.NumberColumn(
                "FCF %", format="%.1f%%",
                help="Free cash flow as % of revenue — cash-conversion / FCF margin."),
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

def _render_reused_signals(cs: Dict[str, Any]) -> None:
    """Render the reused-signal sub-sections (Phase A/B): sector-relative
    valuation (from the MultipleDecomposition tool) and policy/regulatory
    context (from cached regulatory events + the regulatory_exposure 10-K
    dimension). Display-only — these don't gate conviction. No-ops when the
    underlying data isn't available."""
    sv = cs.get("sector_valuation") or {}
    if sv.get("available"):
        mkt = sv.get("market_ev_ebitda"); med = sv.get("sector_median_ev_ebitda")
        prem = sv.get("premium_pct"); sect = sv.get("sector")
        prem_s = f"{prem*100:+.0f}%" if isinstance(prem, (int, float)) else "—"
        line = (f"**🏭 Sector-relative valuation** — {sv.get('label','')}: "
                f"EV/EBITDA **{mkt:.1f}×** vs {sect or 'sector'} median "
                f"**{med:.1f}×** ({prem_s})")
        own = sv.get("own_5y_ev_ebitda"); own_p = sv.get("own_5y_premium_pct")
        if isinstance(own, (int, float)):
            own_s = f"{own_p*100:+.0f}%" if isinstance(own_p, (int, float)) else ""
            line += (f"; vs own 5Y avg **{own:.1f}×** ({own_s}, "
                     f"{sv.get('own_5y_label','')})")
        st.markdown(line + ".")
        if sv.get("note"):
            st.caption(f"  ↳ {sv['note']}")

    ms = cs.get("market_signal") or {}
    if ms.get("available"):
        bits = []
        r3 = ms.get("return_3m"); r6 = ms.get("return_6m"); dm = ms.get("dist_ma200")
        if isinstance(r3, (int, float)): bits.append(f"3M {r3*100:+.0f}%")
        if isinstance(r6, (int, float)): bits.append(f"6M {r6*100:+.0f}%")
        if isinstance(dm, (int, float)): bits.append(f"{dm*100:+.0f}% vs 200DMA")
        st.markdown(f"**📈 Market signal** — {ms.get('label','')}"
                    + (f" ({', '.join(bits)})" if bits else ""))

    sent = cs.get("analyst_sentiment") or {}
    if sent.get("available"):
        bits = []
        iu = sent.get("implied_upside")
        if isinstance(iu, (int, float)):
            bits.append(f"target {iu*100:+.0f}% vs price")
        rt = sent.get("ratings") or {}
        if any(rt.get(k) for k in ("buy", "hold", "sell")):
            bits.append(f"{rt.get('buy',0)}B/{rt.get('hold',0)}H/{rt.get('sell',0)}S")
        up, dn = sent.get("recent_upgrades", 0), sent.get("recent_downgrades", 0)
        if up or dn:
            bits.append(f"{up}↑/{dn}↓ recent")
        disp = sent.get("dispersion")
        if isinstance(disp, (int, float)):
            bits.append(f"PT spread {disp*100:.0f}% ({sent.get('dispersion_label','')})")
        st.markdown(f"**🗣️ Analyst sentiment** — {sent.get('label','')}"
                    + (f" ({' · '.join(bits)})" if bits else ""))

    pr = cs.get("policy_regulatory") or {}
    if pr.get("available"):
        bits = []
        if pr.get("exposure_label"):
            sc = pr.get("exposure_score")
            bits.append(f"10-K exposure: **{pr['exposure_label']}**"
                        + (f" ({sc:.0f}/7)" if isinstance(sc, (int, float)) else ""))
        actions = pr.get("recent_actions") or []
        if actions:
            bits.append(f"{len(actions)} recent regulatory event(s) "
                        f"(net {pr.get('net_event_direction','none')})")
        st.markdown("**⚖️ Policy / regulatory context** — " + " · ".join(bits)
                    if bits else "**⚖️ Policy / regulatory context**")
        if pr.get("exposure_narrative"):
            st.caption(f"  ↳ {str(pr['exposure_narrative'])[:300]}")
        for mx in (pr.get("material_exposures") or [])[:4]:
            if isinstance(mx, dict):
                st.caption(f"  • {mx.get('regulator','')}: {mx.get('area','')} "
                           f"_({mx.get('severity','')})_")
        for a in actions[:4]:
            arrow = ("🔻" if a.get("direction") == "adverse"
                     else "🟢" if a.get("direction") == "favorable" else "•")
            st.caption(f"  {arrow} {a.get('date','')} {a.get('headline','')} "
                       f"_({a.get('source','')})_")


def _render_current_state(cs: Dict[str, Any], ticker: str = "",
                          company: str = "") -> None:
    """Current-State Reconciliation panel (Phase 1.5). Surfaces conflicts
    between engine assumptions and current signals (forward consensus +
    material events) BEFORE the valuation, so an analyst can't anchor on a
    +137% MoS without first confronting that consensus expects a decline."""
    if not cs or cs.get("error"):
        return
    flags = cs.get("flags") or []
    sev = cs.get("unresolved_severity", cs.get("max_severity", "NONE"))
    raw_sev = cs.get("max_severity", "NONE")
    n_unresolved_high = cs.get("unresolved_high", 0)
    pillar = cs.get("pillar_score")
    events = cs.get("events") or []

    icon = "⛔" if sev == "HIGH" else "⚠️" if sev == "MEDIUM" else "✓"
    pill = f" · Current-State pillar {pillar}/5" if pillar else ""
    with st.container(border=True):
        hc, bc = st.columns([5, 1])
        hc.markdown(f"#### {icon} Current-State Reconciliation{pill}")
        # Refresh = the paid grounded events fetch (LLM + web search). Cached
        # 7 days; page loads are free. Always reachable so events can be
        # populated even when only the consensus flag exists.
        if bc.button("🔄 Events", key=f"cs_refresh_{ticker}",
                     help="Fetch material last-180-day events via LLM + web "
                          "search (~$). Cached 7 days."):
            from aletheia.agents.current_state_events import fetch_events
            with st.spinner("Searching recent events…"):
                fetch_events(ticker, company=company, force=True)
            st.cache_data.clear()
            st.rerun()

        # Reused signals (Phase A/B) — always shown when available, even for
        # tickers with no growth/event flags.
        _render_reused_signals(cs)

        if sev not in ("HIGH", "MEDIUM") and not flags:
            st.caption(
                f"No material current-state conflicts. {'Events checked.' if events else 'No event feed pulled yet — click Events.'}"
            )
            return
        if sev == "HIGH":
            st.error(
                f"**{n_unresolved_high} HIGH-severity flag(s) unresolved.** Engine "
                "assumptions conflict with current signals. Acknowledge each in the "
                "**Deep Dive → Current-State gate** (override / accept / reject) to "
                "clear (tier shows **FLAGS PENDING** until then)."
            )
        elif raw_sev == "HIGH" and n_unresolved_high == 0:
            st.success("All HIGH current-state flags acknowledged — gate cleared. "
                       "Decisions are recorded in the audit trail.")
        st.caption(
            "Signals from the last ~180 days that may not be reflected in the "
            "engine's historical-anchored assumptions. The engine math is "
            "unchanged — these flag where an analyst override may be needed."
        )

        # Reconciliation table (engine vs signal).
        recs = cs.get("reconciliation") or []
        if recs:
            rows = []
            for r in recs:
                eng = r.get("engine"); sig = r.get("signal"); dl = r.get("delta")
                rows.append({
                    "Assumption": r.get("assumption", ""),
                    "Engine": f"{eng*100:+.1f}%" if isinstance(eng, (int, float)) else "—",
                    f"Signal ({r.get('signal_label','')})": f"{sig*100:+.1f}%" if isinstance(sig, (int, float)) else "—",
                    "Delta": f"{dl*100:+.1f}pp" if isinstance(dl, (int, float)) else "—",
                    "Recommendation": r.get("recommendation", ""),
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        for f in flags:
            fsev = f.get("severity")
            bullet = "🔴" if fsev == "HIGH" else "🟠" if fsev == "MEDIUM" else "🟡"
            src = f" _({f.get('source')})_" if f.get("source") else ""
            if f.get("acknowledged"):
                bullet = "✅"  # resolved
            st.markdown(f"{bullet} **{fsev}** · {f.get('message','')}{src}")
            ack = f.get("ack")
            if ack:
                tag = "resolved" if f.get("acknowledged") else "parked"
                st.caption(
                    f"  ↳ _{tag}: {ack.get('decision')}"
                    + (f" — {ack.get('rationale')}" if ack.get("rationale") else "")
                    + f" ({ack.get('decided_by','analyst')})_")

        events = cs.get("events") or []
        if events:
            st.caption(f"Material events ({len(events)}):")
            for ev in events:
                st.markdown(
                    f"- {ev.get('date','')} · **{ev.get('category','')}** "
                    f"(materiality {ev.get('materiality','?')}/5): "
                    f"{ev.get('headline','')} _{ev.get('source','')}_"
                )
        elif sev == "HIGH":
            st.caption("No event feed wired yet — consensus-based flag only. "
                       "Run the events agent for trial/regulatory/competitive coverage.")


def _render_data_confidence_panel(ticker: str, latest_fy: Optional[int]) -> None:
    """Phase 3.5 surfacing: composite per-field confidence vs the SEC filing.

    Augments the row glyphs (locked "augment first" decision) with an explicit
    level, so a number that BALANCES an identity but the filing contradicts
    reads as 'suspect', not verified. Reuses the SEC cross-check (3.3/3.4) the
    badge path already runs, plus field_confidence (3.5). Renders nothing when
    no field could be cross-checked against SEC XBRL."""
    try:
        from scripts.validate_sec import validate_ticker
        from aletheia.data.field_confidence import field_confidence, summarize
    except Exception:
        return
    # The freshest row can be a TTM / not-yet-filed year with no SEC facts —
    # fall back to recent annual FYs until one has SEC data to compare against.
    res = None
    start = latest_fy or 0
    for y in (start, start - 1, start - 2):
        try:
            r = validate_ticker(ticker, y) if y else validate_ticker(ticker)
        except Exception:
            continue
        if any(x.get("sec") is not None for x in (r.get("rows") or [])):
            res = r
            break
    if res is None:
        return
    rows = res.get("rows") or []
    if not rows:
        return

    _MAP = {"✓": "validated", "≈": "near", "✗": "drift"}
    cmap: Dict[str, Any] = {}
    detail = []
    for r in rows:
        sec, ours = r.get("sec"), r.get("ours")
        xflag = ("sec_missing" if sec is None else
                 "ours_missing" if ours is None else _MAP.get(r.get("flag"), "drift"))
        # cross-source-checked fields are reported (raw) values.
        lvl, score, _reasons = field_confidence(provenance="raw", cross_source_flag=xflag)
        cmap[r["label"]] = {"level": lvl, "score": score}
        detail.append((r["label"], lvl, score, sec, ours, r.get("drift"), xflag))

    checked = [d for d in detail if d[6] not in ("sec_missing", "ours_missing")]
    if not checked:
        return
    s = summarize({k: v for k, v in cmap.items()
                   if k in {d[0] for d in checked}})
    attn = s["needs_attention"]

    _EMO = {"high": "🟢", "medium": "🟢", "low": "🟡", "near": "🟡",
            "suspect": "🔴", "fabricated": "🔴", "missing": "⚪"}
    hdr = f"Data confidence vs SEC filing: {s['mean_score']:.0f}/100 · {len(checked)} fields"
    if attn:
        hdr += f"  ·  ⚠ {len(attn)} need attention: {', '.join(attn)}"
    with st.expander(hdr, expanded=bool(attn)):
        st.caption(
            "Composite per-field confidence (Phase 3.5): source authority × SEC "
            "cross-source agreement (3.3/3.4). Augments the row glyphs — a value "
            "that balances an identity but the filing contradicts reads 'suspect', "
            "not verified. 🔴 suspect/fabricated · 🟡 low/near · 🟢 medium/high.")
        for label, lvl, score, sec, ours, drift, _xf in sorted(checked, key=lambda d: d[2]):
            dr = f"{drift * 100:+.1f}%" if isinstance(drift, (int, float)) else "—"
            st.markdown(
                f"{_EMO.get(lvl, '⚪')} **{label}** — {lvl} ({score}/100) · "
                f"SEC-vs-ours drift {dr}")


def render_financials_view(ticker: str, bundle: Dict[str, Any],
                           phase2: Dict[str, Any] = None) -> None:
    """Render the redesigned Financials tab for `ticker`. `bundle` is the
    payload returned by `/ticker/<ticker>/financials`; `phase2` is the
    phase2_valuation block (for the valuation-engines & signals overlays)."""
    if not ticker or not bundle or bundle.get("error"):
        st.error(bundle.get("error") if bundle else "No financials available.")
        return

    # Currency banner for non-USD filers (NVO/DKK, ASML/EUR, TSM/TWD).
    _cur = bundle.get("currency") or {}
    if _cur.get("converted"):
        st.info(
            f"💱 Filer reports in **{_cur.get('reporting')}** — all figures "
            f"below (and the DCF) are converted to **USD** at the current "
            f"spot rate (1 {_cur.get('reporting')} = ${_cur.get('fx_rate'):.4f}), "
            f"so they're comparable to the USD share price."
        )
    elif _cur.get("fx_unavailable"):
        st.warning(
            f"⚠ Filer reports in **{_cur.get('reporting')}** but the USD FX "
            f"rate couldn't be fetched — figures are shown in native currency "
            f"and are **not** comparable to the USD price. Valuation unreliable."
        )

    # Current-State Reconciliation (Phase 1.5) — surfaced BEFORE the valuation
    # so the analyst confronts current-reality conflicts before the IV/MoS.
    _render_current_state(
        bundle.get("current_state") or {}, ticker,
        (bundle.get("identity") or {}).get("name") or ticker,
    )

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
        # Phase 3.5: composite per-field confidence vs the SEC filing.
        _render_data_confidence_panel(ticker, ident.get("fiscal_year"))

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
        _render_dcf_section(ticker, bundle)
    else:
        # Specialized engines (REIT → AFFO, DDM → dividends) have no FCFF
        # dcf_inputs — render the two-stage per-share decomposition instead.
        _live, _ = _dcf_api("GET", f"/ticker/{ticker}/dcf")
        if _live and _live.get("engine") in ("reit", "ddm", "rate_base", "mlp", "residual_income"):
            st.markdown("---")
            st.markdown("#### Valuation")
            from aletheia.ui.deep_dive_view import (
                render_specialized_valuation_panel, _rate_base_sotp_panel,
                _mlp_valuation_panel, _residual_income_panel,
            )
            render_specialized_valuation_panel(_live)
            _rate_base_sotp_panel(_live)
            _mlp_valuation_panel(_live)
            _residual_income_panel(_live)

    # ── Valuation engines & signals overlays (VSD / SaaS / CADS / cap-structure
    #    flag / four-method convergence) — same data the memo & deep-dive use. ─
    _render_valuation_engines(phase2 or {})


def _render_valuation_engines(p2: Dict[str, Any]) -> None:
    """Surface the new deterministic overlays on the Financials tab so they live
    next to the statements they're computed from. Each panel self-gates on
    availability, so non-applicable tickers show only what's relevant."""
    if not p2:
        return
    have = any(p2.get(k, {}).get("available") for k in
               ("value_source_decomposition", "saas_metrics", "cads",
                "capital_structure_flag", "valuation_methods",
                "bank_valuation_methods"))
    if not have:
        return
    st.markdown("---")
    st.markdown("#### Valuation engines & signals")

    from aletheia.ui.deep_dive_view import (
        _value_source_panel, _saas_panel, _bank_valuation_panel,
    )
    _value_source_panel(p2)
    _bank_valuation_panel(p2)
    _saas_panel(p2)

    # Capital-structure stability flag → discount-rate family.
    csf = p2.get("capital_structure_flag") or {}
    if csf.get("available"):
        nd = csf.get("net_debt_to_ebitda") or {}
        st.markdown(
            f"**Capital-structure flag:** `{csf.get('klass')}` → discount-rate "
            f"**Family {csf.get('rate_family')}** "
            f"(net-debt/EBITDA {nd.get('first',0):.2f}→{nd.get('last',0):.2f})")
        for n in csf.get("notes") or []:
            st.caption(n)

    # Four-method convergence.
    vm = p2.get("valuation_methods") or {}
    if vm.get("available"):
        ev = vm.get("ev") or {}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("WACC-FCF EV", _bn(ev.get("wacc_fcf")))
        c2.metric("CCF/r_A EV", _bn(ev.get("ccf")))
        c3.metric("APV EV", _bn(ev.get("apv")))
        c4.metric("ECF→EV", _bn(ev.get("from_ecf")))
        sp = vm.get("ev_trio_spread")
        st.caption(f"Four-method convergence — EV trio spread "
                   f"{(sp*100):.1f}% ({'converged' if vm.get('converged_trio') else 'wide'}); "
                   f"family {vm.get('rate_family')}. Additive diagnostic, not the headline IV.")

    # CADS credit floor (Phase 2).
    cads = p2.get("cads") or {}
    if cads.get("available"):
        latest = cads.get("latest") or {}
        cov = cads.get("coverage")
        cov_s = f"{cov:.2f}×" if cov is not None else "n/a"
        line = (f"**CADS** (EBITDA − CapEx, FY{latest.get('year','')}): "
                f"{_bn(latest.get('cads'))} · debt-service coverage {cov_s}")
        if cads.get("trigger"):
            st.error("⚠ " + line + " — " + (cads.get("trigger_reason") or ""))
        else:
            st.markdown(line + " — adequate.")


def _render_dcf_section(ticker: str, bundle: Dict[str, Any]) -> None:
    """Render the DCF subsections (inputs, scenarios, projections, terminal)."""
    st.markdown("---")
    st.markdown("#### DCF Analysis")

    # Override provenance banner — make an analyst-overridden valuation
    # impossible to mistake for the model-derived one.
    _ov = bundle.get("dcf_overrides") or {}
    _ov_fields = [k for k in (
        "revenue_growth_y1_5", "revenue_growth_y6_10", "terminal_ebit_margin",
        "capex_pct_revenue", "tax_rate", "discount_rate", "terminal_growth",
    ) if _ov.get(k) is not None]
    if _ov_fields:
        who = _ov.get("updated_by", "analyst")
        when = (_ov.get("updated_at") or "")[:10]
        st.warning(
            f"✎ **Analyst-overridden valuation** — {len(_ov_fields)} assumption(s) "
            f"differ from the model"
            f"{' (set by ' + who + (' on ' + when if when else '') + ')' if who else ''}. "
            "Intrinsic values below reflect these overrides, not the model defaults."
        )

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
                _render_editable_assumptions(ticker, asn)
