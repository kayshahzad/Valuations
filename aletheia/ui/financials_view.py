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

def _statement_table(
    title: str,
    ticker: str,
    rows: List[Tuple[str, str, Optional[float], Optional[float], str]],
) -> None:
    """Render a statement section.

    Each row: (display_label, badge_key, current_value, prior_value, fmt_kind)
      fmt_kind: "$" → dollar, "%" → percent, "n" → number, "x" → multiple
    """
    st.markdown(f"#### {title}")
    df_rows = []
    for label, badge_key, curr, prior, fmt in rows:
        status = _status_for(ticker, badge_key) if badge_key else "unknown"
        dot = _STATUS_DOT.get(status, "·")
        if fmt == "$":
            curr_s = _bn(curr); prior_s = _bn(prior)
        elif fmt == "%":
            curr_s = _pct(curr); prior_s = _pct(prior)
        elif fmt == "x":
            curr_s = f"{curr:,.2f}x" if curr is not None else "—"
            prior_s = f"{prior:,.2f}x" if prior is not None else "—"
        else:
            curr_s = f"{curr:,.2f}" if curr is not None else "—"
            prior_s = f"{prior:,.2f}" if prior is not None else "—"

        df_rows.append({
            "": dot,
            "Metric": label,
            "Current FY": curr_s,
            "Prior FY":   prior_s,
            "YoY":        _yoy_str(curr, prior) if fmt in ("$", "n") else "—",
        })

    df = pd.DataFrame(df_rows)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "":           st.column_config.TextColumn("", width="small", help="Validation status"),
            "Metric":     st.column_config.TextColumn("Metric", width="medium"),
            "Current FY": st.column_config.TextColumn("Current FY", width="small"),
            "Prior FY":   st.column_config.TextColumn("Prior FY", width="small"),
            "YoY":        st.column_config.TextColumn("YoY", width="small"),
        },
    )


# ── Fiscal history table ──────────────────────────────────────────────────

def _fiscal_history_table(history: List[Dict[str, Any]],
                          ttm: Optional[Dict[str, Any]] = None) -> None:
    if not history:
        return
    rows = []
    # Phase Q-7: prepend a TTM row when ingested. Period column flags
    # the row as TTM so the table reader can spot it without reading
    # column-by-column. Rev-growth is YoY-TTM (TTM vs same-period FY)
    # only when the FY history has the matching prior year, otherwise
    # left blank to avoid misleading "growth vs last FY-end" framing
    # the user flagged as a seasonality trap.
    if ttm and ttm.get("Revenue"):
        ttm_rev = ttm["Revenue"]
        # YoY TTM-vs-prior-TTM: compare against the same-period TTM a
        # year earlier (computed from FMP quarterly statements at
        # bundle-build time). This eliminates the seasonality trap of
        # comparing a TTM-through-Q1 against a calendar-FY-end snapshot.
        # Falls through to None silently when FMP doesn't have ≥8
        # quarters of data — the Rev Growth cell renders blank rather
        # than misleading.
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
    for i, r in enumerate(history):
        rev = r.get("Revenue")
        prev_rev = history[i - 1].get("Revenue") if i > 0 else None
        rev_growth = (
            (rev / prev_rev - 1.0) * 100
            if rev and prev_rev else None
        )
        rows.append({
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
    df = pd.DataFrame(rows)

    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "FY":         st.column_config.NumberColumn("FY", format="%d"),
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
                "ROIC", format="%.1f%%", min_value=-10.0, max_value=80.0,
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
    prior = history[-2] if len(history) >= 2 else {}

    def p(field: str) -> Optional[float]:
        v = prior.get(field)
        return v

    inc_rows = [
        # (display_label, validation badge key, curr, prior, format)
        ("Revenue",            "Revenue",            inc["Revenue"],          p("Revenue"),           "$"),
        ("COGS",               "COGS",               inc["COGS"],             None,                   "$"),
        ("Gross Margin",       "Gross Margin",       inc["GrossMargin_Pct"],  None,                   "%"),
        ("R&D",                None,                 inc["RnD"],              None,                   "$"),
        ("SG&A",               None,                 inc["SGnA"],             None,                   "$"),
        ("Operating Income",   "Operating Income",   inc["OperatingIncome"],  None,                   "$"),
        ("EBIT Margin",        "EBIT Margin",        inc["EBIT_Margin_Pct"],  None,                   "%"),
        ("EBITDA",             "EBITDA",             inc["EBITDA"],           p("EBITDA"),            "$"),
        ("EBITDA Margin",      "EBITDA Margin",      inc["EBITDA_Margin_Pct"],None,                   "%"),
        ("D&A",                None,                 inc["DepreciationAmortization"], None,           "$"),
        ("NOPAT",              None,                 inc["NOPAT"],            None,                   "$"),
        ("Net Income",         "Net Income",         inc["NetIncome"],        p("NetIncome"),         "$"),
        ("Diluted EPS",        "Diluted EPS",        inc["DilutedEPS"],       None,                   "n"),
        ("Operating CF",       "Operating CF",       inc["OperatingCF"],      None,                   "$"),
        ("Investing CF",       None,                 inc["InvestingCF"],      None,                   "$"),
        ("Financing CF",       None,                 inc["FinancingCF"],      None,                   "$"),
        ("FCF",                "FCF",                inc["FCF"],              p("FCF"),               "$"),
        ("FCFF",               None,                 inc["FCFF"],             None,                   "$"),
        ("FCF Margin",         None,                 inc["FCF_Margin_Pct"],   None,                   "%"),
        ("CapEx",              "CapEx",              inc["CapEx"],            p("CapEx"),             "$"),
        ("Maintenance CapEx",  None,                 inc["MaintenanceCapEx"], None,                   "$"),
        ("Growth CapEx",       None,                 inc["GrowthCapEx"],      None,                   "$"),
    ]

    bs_rows = [
        ("Total Assets",         "Total Assets",         bs["TotalAssets"],         None, "$"),
        ("Cash",                 "Cash",                 bs["Cash"],                None, "$"),
        ("Short-Term Investments","Short-Term Investments", bs["ShortTermInvestments"], None, "$"),
        ("Accounts Receivable",  "Accounts Receivable",  bs["AccountsReceivable"],  None, "$"),
        ("Inventory",            "Inventory",            bs["Inventory"],           None, "$"),
        ("PPE Net",              "PPE Net",              bs["PPE_Net"],             None, "$"),
        ("PPE Gross",            None,                   bs["PPE_Gross"],           None, "$"),
        ("Accum. Depreciation",  None,                   bs["AccumulatedDepreciation"], None, "$"),
        ("Total Liabilities",    "Total Liabilities",    bs["TotalLiabilities"],    None, "$"),
        ("Current Liabilities",  "Current Liabilities",  bs["LiabilitiesCurrent"],  None, "$"),
        ("Short-Term Debt",      "Short-Term Debt",      bs["ShortTermDebt"],       None, "$"),
        ("Long-Term Debt",       "Long-Term Debt",       bs["LongTermDebt"],        None, "$"),
        ("Accounts Payable",     "Accounts Payable",     bs["AccountsPayable"],     None, "$"),
        ("Total Equity",         "Total Equity",         bs["TotalEquity"],         None, "$"),
        ("Net Working Capital",  None,                   bs["NWC"],                 None, "$"),
        ("Net Debt",             None,                   bs["NetDebt"],             None, "$"),
    ]

    # Section titles include the FY + period-end so the analyst sees at a
    # glance which 12-month window is being shown — matters for filers with
    # non-calendar fiscal years (NVDA Jan, HD/LOW Feb, COST Aug/Sep).
    period_end_str = _fmt_period_end(ident.get("period_end_date"))
    fy_suffix = (f" — FY{ident['fiscal_year']} · {period_end_str}"
                 if period_end_str else f" — FY{ident['fiscal_year']}")
    ls, rs = st.columns(2)
    with ls:
        _statement_table(f"Income Statement & Cash Flow{fy_suffix}", ticker, inc_rows)
    with rs:
        _statement_table(f"Balance Sheet{fy_suffix}", ticker, bs_rows)

    st.markdown("---")

    # ── Capital structure + leases ───────────────────────────────────────
    cap_rows = [
        ("ROIC",                "ROIC",         ret["ROIC"],          None, "%"),
        ("ROE",                 "ROE",          ret["ROE"],           None, "%"),
        ("Invested Capital",    "Invested Capital", ret["InvestedCapital"], None, "$"),
        ("Diluted Shares",      "Diluted Shares", ret["SharesDiluted"], None, "n"),
        ("Basic Shares",        None,           ret["SharesBasic"],    None, "n"),
        ("Outstanding Shares",  None,           ret["SharesOutstanding"], None, "n"),
        ("Buybacks",            "Buybacks",     ret["Buybacks"],       None, "$"),
        ("SBC",                 None,           ret["SBC"],            None, "$"),
        ("Net Buyback after SBC", None,         ret["NetBuyback_AfterSBC"], None, "$"),
        ("SBC % of FCF",        None,           ret["SBC_PctFCF"],     None, "%"),
        ("Dilution %",          None,           ret["DilutionPct"],    None, "%"),
        ("Dividends Paid",      "Dividends Paid", ret["DividendsPaid"], None, "$"),
    ]

    lease_rows = [
        ("ROU Asset (Operating)", None, leases["ROUAsset_Operating"],         None, "$"),
        ("ROU Asset (Finance)",   None, leases["ROUAsset_Finance"],           None, "$"),
        ("Lease Liab Operating",  None, leases["LeaseLiability_Operating_Total"], None, "$"),
        ("Lease Liab Finance",    None, leases["LeaseLiability_Finance_Total"],   None, "$"),
        ("Lease Cost (annual)",   None, leases["LeaseCost"],                  None, "$"),
    ]

    ls2, rs2 = st.columns(2)
    with ls2:
        _statement_table("Returns & Capital Structure", ticker, cap_rows)
    with rs2:
        _statement_table("Lease Items", ticker, lease_rows)

    # ── Fiscal history ────────────────────────────────────────────────────
    if history:
        st.markdown("---")
        st.markdown("#### Multi-year history")
        _fiscal_history_table(history, ttm=ttm)
        _render_validation_drift_panel(history, ttm)

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
