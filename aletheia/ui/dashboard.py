"""
aletheia/ui/dashboard.py

Per-ticker analyst dashboard. Five sections, top-to-bottom:
  1. Header strip — ticker, classification, price, filing date, quality, macro
  2. 5-year fundamentals table
  3. Five scenarios visible (bull / base / bear + 2 library scenarios)
  4. Plain-language synthesis (template-based, deterministic — no LLM)
  5. Top 5 ratios + Override visibility + Data quality warnings

Design rules (from Phase 5 plan):
  - No BUY/SELL/HOLD verdicts. Numbers and assumptions only.
  - Upside/downside shown as signed %, not colored stoplights.
  - "Pass/flag/fail" status labels uncolored — the analyst reads value vs threshold.
  - Synthesis is template-deterministic, not LLM-generated.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from aletheia.ui.cache import (
    cached_calc_df,
    cached_classification,
    cached_dcf_summary,
    cached_known_issues,
    cached_library_scenario,
    cached_macro_context,
    cached_screening_card,
    cached_valuation,
)


# ────────────────────────────────────────────────────────────────────────
# Visual tokens — theme-agnostic colors matching financials_view +
# deep_dive_view. Body text uses `inherit` / opacity-modulated overlays
# so the dashboard reads cleanly in both light and dark themes.
# ────────────────────────────────────────────────────────────────────────

_GREEN, _AMBER, _RED = "#10b981", "#f59e0b", "#ef4444"
_MUTED_TEXT = "rgba(120,120,128,0.85)"
_BAR_BG     = "rgba(120,120,128,0.20)"
_PANEL_BG_NEUTRAL = "rgba(120,120,128,0.06)"
_PANEL_BG_AMBER   = "rgba(245,158,11,0.08)"
_PANEL_BG_GREEN   = "rgba(16,185,129,0.06)"
_PANEL_BG_RED     = "rgba(239,68,68,0.06)"

_STATUS_DOT = {
    "validated": "🟢",
    "near":      "🟡",
    "drift":     "🔴",
    "missing":   "⚪",
    "unknown":   "·",
}


def _panel(html_body: str, accent_color: str, bg: str = _PANEL_BG_NEUTRAL) -> None:
    """Reusable bordered-panel wrapper used by Synthesis + Override blocks.
    Visual semantics match the deep-dive thesis/contrarian panels."""
    st.markdown(
        f"""
<div style='background:{bg};border-left:4px solid {accent_color};
            padding:14px 18px;border-radius:0 6px 6px 0;color:inherit;
            font-size:14px;line-height:1.7;margin:6px 0 10px 0'>
{html_body}
</div>
        """,
        unsafe_allow_html=True,
    )


def _status_dot_for(ticker: Optional[str], label: str) -> str:
    """Look up the validation status of a metric label and return its emoji."""
    if not ticker:
        return _STATUS_DOT["unknown"]
    try:
        from aletheia.ui.validation_badge import lookup_status
        return _STATUS_DOT.get(lookup_status(ticker, label), _STATUS_DOT["unknown"])
    except Exception:
        return _STATUS_DOT["unknown"]


# ────────────────────────────────────────────────────────────────────────
# Formatting helpers (shared style with streamlit_app.py)
# ────────────────────────────────────────────────────────────────────────

def _money(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"${v:,.2f}"


def _bn(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"${v / 1e9:,.1f}B"


def _pct(v: Optional[float], decimals: int = 1) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v * 100:+.{decimals}f}%"


def _pct_unsigned(v: Optional[float], decimals: int = 1) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v * 100:.{decimals}f}%"


def _signed_dollars(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"${v:+,.2f}"


# ────────────────────────────────────────────────────────────────────────
# Section 1 — Header strip
# ────────────────────────────────────────────────────────────────────────

def _business_description(ticker: str) -> Optional[str]:
    """
    Pull a brief business description for the header. Two sources, fallback
    chain:
      1. The most recent agent-produced description in
         `1_economic_reality.business_model.business_description` (if the
         lead pipeline has run for this ticker).
      2. FMP `/profile` description (cached; available for any ticker that's
         been added through the ingest pipeline).
    Truncates to ~250 chars so the header stays compact.
    """
    # 1) Agent run output via the ticker report
    try:
        import httpx
        r = httpx.get(f"http://localhost:8000/ticker/{ticker}", timeout=5)
        if r.status_code == 200:
            payload = r.json()
            desc = (
                (payload.get("1_economic_reality") or {})
                .get("business_model", {})
                .get("business_description")
            )
            if desc and len(desc.strip()) > 30:
                return _truncate(desc, 260)
    except Exception:
        pass

    # 2) FMP profile description — works for pending tickers too
    try:
        from aletheia.data import fmp_client
        profile = fmp_client.fetch_profile(ticker)
        if profile and profile.get("description"):
            return _truncate(profile["description"], 260)
    except Exception:
        pass

    return None


def _truncate(s: str, max_chars: int) -> str:
    s = s.strip()
    if len(s) <= max_chars:
        return s
    # Prefer to break at a sentence boundary (period followed by space).
    cut = s[:max_chars]
    last_period = cut.rfind(". ")
    if last_period > max_chars * 0.6:
        return cut[: last_period + 1]
    return cut.rstrip(",;: ") + "…"


def render_header(ticker: str, dcf: Dict[str, Any], df: pd.DataFrame) -> None:
    cls = cached_classification(ticker)
    macro = cached_macro_context()

    latest = df.sort_values("fiscal_year").iloc[-1] if not df.empty else None
    period_end = latest.get("period_end_date") if latest is not None else None
    quality = float(latest.get("overall_quality_score") or 0) if latest is not None else 0.0
    warning_count = int(latest.get("warning_count") or 0) if latest is not None else 0

    today = date.today()
    days_since_filing = None
    if period_end:
        try:
            d = pd.to_datetime(period_end).date()
            days_since_filing = (today - d).days
        except Exception:
            pass

    # First row: ticker + name + classification pill + price
    col1, col2, col3 = st.columns([3, 2, 2])
    with col1:
        st.markdown(f"## {ticker}")
        if cls:
            pill = f"{cls.get('sector', '?')} · {cls.get('industry', '?')} · `{cls.get('lifecycle', '?')}`"
            st.caption(pill)
            if cls.get("notes"):
                st.caption(f"_{cls['notes']}_")
    with col2:
        # Stock price — current market quote pulled at DCF run time.
        # Today's date is shown as a delta hint so the analyst sees how
        # fresh the quote is.
        from datetime import datetime as _dt
        current_price = dcf.get("current_price")
        price_label = _dt.now().strftime("Price · %b %d, %Y")
        st.metric(
            price_label,
            _money(current_price),
            delta=None,
        )
    with col3:
        mkt_cap = dcf.get("market_cap")
        st.metric("Market cap", _bn(mkt_cap) if mkt_cap else "—")

    # Brief business description — full-width row below the headline metrics.
    # Sourced from the agent run when available, FMP profile otherwise.
    description = _business_description(ticker)
    if description:
        st.markdown(
            f"""
<div style='background:rgba(120,120,128,0.06);border-left:3px solid rgba(120,120,128,0.4);
            padding:10px 14px;border-radius:0 4px 4px 0;color:inherit;
            font-size:13px;line-height:1.6;margin:6px 0 12px 0'>
{description}
</div>
            """,
            unsafe_allow_html=True,
        )

    # Second row: filing date / data quality / macro context
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(
            "Last fiscal year",
            f"FY{dcf.get('fiscal_year', '?')}",
            delta=str(period_end)[:10] if period_end else None,
        )
    with col2:
        days_str = f"{days_since_filing}d" if days_since_filing is not None else "—"
        st.metric("Days since filing", days_str)
    with col3:
        st.metric(
            "Data quality",
            f"{quality:.2f}",
            delta=f"{warning_count} warnings" if warning_count else None,
            delta_color="off",
        )
    with col4:
        rf = macro.get("rf", 0)
        erp = macro.get("erp", 0)
        st.metric(
            "Macro context",
            f"WACC {dcf.get('wacc_base', 0)*100:.2f}%",
            delta=f"Rf {rf*100:.2f}% · ERP {erp*100:.2f}% · β {dcf.get('beta', 0):.2f}",
            delta_color="off",
        )

    # Layer-11 credit regime (market-wide, portfolio-level — not per-ticker).
    # A tight regime is itself a risk signal: default is being underwritten
    # cheaply, so any financial-resilience read is graded on an easy curve.
    credit = macro.get("credit") or {}
    if credit:
        hy = credit.get("hy_oas")
        ig = credit.get("ig_oas")
        regime = credit.get("regime", "normal")
        glyph = {"tight": "🟢", "normal": "⚪", "stressed": "🔴"}.get(regime, "⚪")
        # Direction-explicit: "tighter than 81% of history", never a bare
        # "19th percentile" that a reader could parse as "not extreme".
        bits = []
        if hy is not None:
            phrase = credit.get("position", "")
            bits.append(f"HY OAS {hy:.2f}%" + (f", {phrase}" if phrase else ""))
        if ig is not None:
            bits.append(f"IG OAS {ig:.2f}%")
        line = f"{glyph} Credit regime: **{regime}** — " + " · ".join(bits)
        if credit.get("as_of"):
            line += f"  ·  as of {credit['as_of']}"
        st.caption(line)
        if credit.get("caveat"):
            st.caption(f"⚠️ {credit['caveat']}")


# ────────────────────────────────────────────────────────────────────────
# Section 2 — 5-year fundamentals
# ────────────────────────────────────────────────────────────────────────

_FUND_CSS = """
<style>
.fund-wrap { overflow-x:auto; margin:2px 0 6px; }
table.fund { border-collapse:collapse; width:100%; font-size:14px; }
table.fund th, table.fund td { padding:7px 14px; white-space:nowrap;
  border-bottom:1px solid rgba(128,128,128,0.13); }
table.fund thead th { font-size:11.5px; text-transform:uppercase; letter-spacing:.03em;
  opacity:.6; font-weight:600; text-align:right; vertical-align:bottom; line-height:1.35;
  border-bottom:2px solid rgba(128,128,128,0.55); }
table.fund thead th.lbl { text-align:left; }
table.fund td.lbl { text-align:left; }
table.fund td.num { text-align:right;
  font-family:ui-monospace,'DM Mono',SFMono-Regular,Menlo,monospace;
  font-variant-numeric:tabular-nums; }
table.fund tr.subtotal td { border-top:2px solid rgba(128,128,128,0.55); font-weight:700; }
table.fund tr.sub td.lbl { padding-left:34px; opacity:.6; font-size:12.5px; }
table.fund tr.sub td.num { opacity:.7; font-size:12.5px; }
table.fund tbody tr:hover td { background:rgba(128,128,128,0.06); }
</style>
"""


def _fundamentals_html(headers: List[str], rows: List, subtotals: set) -> str:
    """Excel-style ruled HTML table: thick rules above subtotals + under the
    header, thin gridlines elsewhere, right-aligned mono numbers, ↳ sub-rows
    (growth / margins) muted, growth coloured green/red by sign."""
    from html import escape

    def _is_subtotal(lbl: str) -> bool:
        s = lbl.strip().lstrip("·🔴🟡🟢⚪ ").strip()
        return any(s.startswith(k) for k in subtotals)

    out = ['<div class="fund-wrap"><table class="fund"><thead><tr>',
           '<th class="lbl">Metric</th>']
    for h in headers:
        out.append(f'<th class="num">{escape(h).replace(chr(10), "<br>")}</th>')
    out.append('</tr></thead><tbody>')
    for label, vals in rows:
        s = label.strip()
        is_sub = "↳" in s
        is_growth = "growth" in s
        cls = "sub" if is_sub else ("subtotal" if _is_subtotal(label) else "")
        out.append(f'<tr class="{cls}"><td class="lbl">{escape(s)}</td>')
        for v in vals:
            v = escape(str(v))
            style = ""
            if is_sub and is_growth and v not in ("—", ""):
                style = ' style="color:#059669"' if v.startswith("+") else (
                    ' style="color:#dc2626"' if v.startswith("-") else "")
            out.append(f'<td class="num"{style}>{v}</td>')
        out.append('</tr>')
    out.append('</tbody></table></div>')
    return "".join(out)


def render_trends_table(df: pd.DataFrame, ticker: Optional[str] = None) -> None:
    """5-year fundamentals — pivoted by FY for at-a-glance trend reading.

    Shows status dots inline with metric names so the analyst can see
    which fields are externally validated. FY column headers carry the
    period-end date so non-calendar fiscal years (NVDA Jan, HD Feb,
    COST Aug/Sep) are unambiguous.
    """
    st.markdown("### 5-year fundamentals")
    if df.empty:
        st.info("No fundamentals available.")
        return

    # Drop TTM rows whose period_end_date is the same as the latest
    # FY row's — this happens for tickers like NVDA (FY ends Jan, TTM
    # synthesised from last-4-quarters also ends Jan = same data
    # point). Without this guard, the YoY column for the duplicated
    # period renders as "+0.0%" comparing identical revenues.
    sorted_all = df.sort_values("fiscal_year").copy()
    fy_only = sorted_all[sorted_all["period"] == "FY"]
    if not fy_only.empty:
        latest_fy_end = fy_only.iloc[-1]["period_end_date"]
        sorted_all = sorted_all[
            ~(
                (sorted_all["period"] == "TTM")
                & (sorted_all["period_end_date"] == latest_fy_end)
            )
        ]
    sorted_df = sorted_all.tail(5).copy().reset_index(drop=True)

    # FY column headers: include period-end date so the 12-month window
    # is unambiguous on non-calendar filers.
    def _fmt_period_end(v: Any) -> str:
        try:
            from datetime import datetime as _dt
            return _dt.strptime(str(v)[:10], "%Y-%m-%d").strftime("%b %d, %Y")
        except Exception:
            return str(v)[:10] if v else ""

    headers = []
    for _, r in sorted_df.iterrows():
        fy = int(r["fiscal_year"])
        end = _fmt_period_end(r.get("period_end_date"))
        headers.append(f"FY{fy}\n{end}" if end else f"FY{fy}")

    # Status-dot prefix on key metric rows, sourced from validation_badge.
    rev_dot   = _status_dot_for(ticker, "Revenue")
    ebit_dot  = _status_dot_for(ticker, "EBIT Margin")

    # Income-statement → FCF waterfall, in $B. Every line is a directly-cleaned
    # field except Interest (net) and Δ Net working capital, which are derived
    # from accounting identities (raw interest expense and the working-capital
    # roll-forward aren't cleaned columns):
    #   Interest (net) = NOPAT − Net income  (the levered-vs-unlevered earnings gap)
    #   Δ NWC          = NOPAT + D&A − CapEx − FCFF  (the FCFF identity)
    n = len(sorted_df)

    def _ser(col: str) -> List[Optional[float]]:
        if col not in sorted_df.columns:
            return [None] * n
        return [sorted_df[col].iloc[i] for i in range(n)]

    def _v(x: Any) -> Optional[float]:
        try:
            return float(x) if (x is not None and pd.notna(x)) else None
        except (TypeError, ValueError):
            return None

    rev    = _ser("clean_Revenue")
    cogs   = _ser("raw_COGS")
    sga    = _ser("clean_SGA_Combined")
    ebitda = _ser("derived_EBITDA")
    da     = _ser("derived_Depreciation_Total")
    ebit   = _ser("derived_OperatingIncome")
    ni     = _ser("raw_NetIncome")
    capex  = _ser("derived_CapEx")
    fcf    = _ser("derived_FCF")
    fcff   = _ser("derived_FCFF")
    nopat  = _ser("clean_NOPAT")

    # Post-EBIT bridge: EBIT − Interest&non-op − Tax = Net income. Raw interest
    # and tax expense aren't cleaned columns, so derive a consistent bridge off
    # the effective tax rate: Pretax = NI/(1−t); Tax = Pretax − NI; Interest&
    # non-op = EBIT − Pretax.
    taxr = _ser("clean_GAAP_TaxRate")

    def _t(x: Any) -> float:
        v = _v(x)
        return 0.21 if v is None else max(-0.5, min(0.6, v))

    intr: List[Optional[float]] = []
    tax: List[Optional[float]] = []
    for i in range(n):
        ni_i, ebit_i, t = _v(ni[i]), _v(ebit[i]), _t(taxr[i])
        if ni_i is None or (1.0 - t) == 0:
            intr.append(None); tax.append(None); continue
        pretax = ni_i / (1.0 - t)
        tax.append(pretax - ni_i)
        intr.append((ebit_i - pretax) if ebit_i is not None else None)

    dnwc = [(_v(nopat[i]) + _v(da[i]) - _v(capex[i]) - _v(fcff[i]))
            if all(_v(v) is not None for v in (nopat[i], da[i], capex[i], fcff[i]))
            else None for i in range(n)]

    # Per-row formatters ($B / $-per-share / %).
    def _b1(x: Any) -> str:
        v = _v(x)
        return f"{v / 1e9:,.1f}" if v is not None else "—"

    def _usd(x: Any) -> str:
        v = _v(x)
        return f"{v:,.2f}" if v is not None else "—"

    def _pct1(x: Any) -> str:
        v = _v(x)
        return f"{v * 100:,.1f}%" if v is not None else "—"

    def _row(series: List[Optional[float]], f=_b1):
        return [f(series[i]) if i < len(series) else "—" for i in range(n)]

    # $-billions with the unit inline ($27.0B), YoY growth, and % margins —
    # so the trend is legible without mental unit-tracking.
    def _bd(x: Any) -> str:
        v = _v(x)
        if v is None:
            return "—"
        return f"{'-' if v < 0 else ''}${abs(v) / 1e9:,.1f}B"

    def _yoy(series: List[Optional[float]]) -> List[Optional[float]]:
        out = [None]
        for i in range(1, n):
            a, b = _v(series[i - 1]), _v(series[i])
            out.append((b / a - 1.0) if (a and b is not None and a != 0) else None)
        return out

    def _marg(series: List[Optional[float]]) -> List[Optional[float]]:
        return [(_v(series[i]) / _v(rev[i]))
                if (_v(series[i]) is not None and _v(rev[i])) else None
                for i in range(n)]

    def _gpct(x: Any) -> str:          # signed growth: +126%
        v = _v(x)
        return f"{v * 100:+,.0f}%" if v is not None else "—"

    def _mpct(x: Any) -> str:          # margin: 58.5%
        v = _v(x)
        return f"{v * 100:,.1f}%" if v is not None else "—"

    # Banks (ddm/embedded-value + Financials) get a thin P&L + returns/capital
    # set — the industrial waterfall is meaningless for them (gross interest
    # revenue, interest expense as "COGS", no real EBITDA/CapEx/FCF). NIM /
    # efficiency / provisions / CET1 / tangible book need bank XBRL extraction
    # (deferred), so they're not faked here.
    _cls = None
    try:
        from config.ticker_classification import get_extended_universe
        _cls = get_extended_universe().get((ticker or "").upper()) if ticker else None
    except Exception:
        _cls = None
    from aletheia.calculations.sector_classification import is_bank_for_display
    _is_bank = bool(_cls and is_bank_for_display(
        getattr(_cls, "sector", ""), getattr(_cls, "business_model", "")))

    if _is_bank:
        shares = _ser("raw_SharesDiluted")
        if all(v is None for v in shares):
            shares = _ser("clean_SharesDiluted")
        equity = _ser("raw_TotalEquity")
        assets = _ser("raw_TotalAssets")
        roe = _ser("derived_ROE")
        dps = _ser("clean_DividendsPerShare")
        net_rev = [(_v(rev[i]) - _v(cogs[i]))
                   if (_v(rev[i]) is not None and _v(cogs[i]) is not None) else None
                   for i in range(n)]
        pretax = [(_v(ni[i]) + _v(tax[i]))
                  if (_v(ni[i]) is not None and _v(tax[i]) is not None) else None
                  for i in range(n)]
        eps = [(_v(ni[i]) / _v(shares[i]))
               if (_v(ni[i]) is not None and _v(shares[i])) else None for i in range(n)]
        roa = [(_v(ni[i]) / _v(assets[i]))
               if (_v(ni[i]) is not None and _v(assets[i])) else None for i in range(n)]
        bvps = [(_v(equity[i]) / _v(shares[i]))
                if (_v(equity[i]) is not None and _v(shares[i])) else None for i in range(n)]
        rows = [
            (f"{rev_dot}  Total revenue (gross, $B)", _row(rev)),
            ("      Net revenue (post interest exp., $B)", _row(net_rev)),
            ("      Pre-tax income ($B)",        _row(pretax)),
            ("      Tax † ($B)",                 _row(tax)),
            ("      Net income ($B)",            _row(ni)),
            ("      EPS ($)",                    _row(eps, _usd)),
            ("      ROE",                        _row(roe, _pct1)),
            ("      ROA",                        _row(roa, _pct1)),
            ("      Book value / share ($)",     _row(bvps, _usd)),
            ("      Dividend / share ($)",       _row(dps, _usd)),
        ]
        caption = (
            "Bank view (financial-sector). 'Net revenue' = total revenue − "
            "interest expense (FMP maps interest expense to COGS). † Tax derived = "
            "Pretax − NI. Net interest income / fee split, provisions, NIM, "
            "efficiency ratio, CET1 and tangible book need bank XBRL extraction "
            "(deferred). 🟢/🟡/🔴 validation vs SEC/FMP.")
    else:
        rows = [
            (f"{rev_dot}  Revenue",          _row(rev, _bd)),
            ("      ↳ YoY growth",           _row(_yoy(rev), _gpct)),
            ("      COGS",                   _row(cogs, _bd)),
            ("      SG&A",                   _row(sga, _bd)),
            ("      EBITDA",                 _row(ebitda, _bd)),
            ("      ↳ margin",               _row(_marg(ebitda), _mpct)),
            ("      D&A",                    _row(da, _bd)),
            (f"{ebit_dot}  EBIT",            _row(ebit, _bd)),
            ("      ↳ margin",               _row(_marg(ebit), _mpct)),
            ("      Interest & non-op, net †", _row(intr, _bd)),
            ("      Tax †",                  _row(tax, _bd)),
            ("      Net income",             _row(ni, _bd)),
            ("      ↳ margin",               _row(_marg(ni), _mpct)),
            ("      CapEx",                  _row(capex, _bd)),
            ("      Δ Net working capital †", _row(dnwc, _bd)),
            ("      FCF",                    _row(fcf, _bd)),
            ("      ↳ margin",               _row(_marg(fcf), _mpct)),
            ("      ↳ YoY growth",           _row(_yoy(fcf), _gpct)),
        ]
        caption = (
            "Values in $B (reported GAAP); ↳ rows are YoY growth (signed) and "
            "margins (% of revenue). † derived (raw interest/tax not cleaned "
            "fields): Pretax = NI/(1−tax rate); Tax = Pretax − NI; Interest & non-op "
            "= EBIT − Pretax; Δ NWC = NOPAT + D&A − CapEx − FCFF. "
            "🟢 validated SEC/FMP within 1% · 🟡 within 5% · 🔴 >5% drift · "
            "⚪ field absent · · not yet validated")

    # Excel-style ruled HTML table (st.dataframe can't draw section borders):
    # thick rules above the subtotals + under the header group the waterfall.
    subtotals = ({"Net income", "Pre-tax income", "Total revenue"} if _is_bank
                 else {"EBITDA", "EBIT", "Net income", "FCF"})
    st.markdown(_FUND_CSS + _fundamentals_html(headers, rows, subtotals),
                unsafe_allow_html=True)
    st.caption(caption)


# ────────────────────────────────────────────────────────────────────────
# Section 3 — Five scenarios visible
# ────────────────────────────────────────────────────────────────────────

def render_scenarios(ticker: str, dcf: Dict[str, Any]) -> List[Dict[str, Any]]:
    st.markdown("### Five scenarios")
    st.caption(
        "Production engine bull/base/bear plus two library scenarios "
        "(consensus growth, historical CAGR continues). Numbers, not verdicts."
    )

    library_consensus = cached_library_scenario(ticker, "consensus_growth")
    library_historical = cached_library_scenario(ticker, "historical_cagr_continues")

    scenarios: List[Dict[str, Any]] = []
    bull = dcf.get("bull")
    base = dcf.get("base")
    bear = dcf.get("bear")
    if bull:
        scenarios.append({**bull, "label": "Bull (engine)"})
    if base:
        scenarios.append({**base, "label": "Base (engine)"})
    if bear:
        scenarios.append({**bear, "label": "Bear (engine)"})
    if library_consensus and not library_consensus.get("error"):
        scenarios.append({**library_consensus, "label": "Consensus growth"})
    elif library_consensus and library_consensus.get("error") == "stub":
        scenarios.append({"label": "Consensus growth", "error": library_consensus.get("message")})
    if library_historical and not library_historical.get("error"):
        scenarios.append({**library_historical, "label": "Historical CAGR continues"})
    elif library_historical and library_historical.get("error"):
        scenarios.append({"label": "Historical CAGR", "error": library_historical.get("message")})

    cols = st.columns(len(scenarios))
    for col, s in zip(cols, scenarios):
        with col:
            label = s.get("label", "?")
            if s.get("error"):
                # Unavailable scenario — neutral-tinted card so the column
                # doesn't render empty (which would imply data is missing
                # rather than the scenario being a stub).
                st.markdown(
                    f"<div style='background:{_PANEL_BG_NEUTRAL};padding:14px 12px;"
                    f"border-radius:6px;height:100%;'>"
                    f"<div style='font-weight:600;font-size:13px'>{label}</div>"
                    f"<div style='color:{_MUTED_TEXT};font-size:12px;margin-top:8px'>"
                    f"<em>unavailable: {s['error'][:80]}</em></div></div>",
                    unsafe_allow_html=True,
                )
                continue

            iv = s.get("iv_per_share")
            upside = s.get("upside_pct")

            # Color by upside band: green > 0 (positive MoS), amber -20% to 0
            # (modest premium), red < -20% (significant premium). Matches the
            # severity-tinted panels used elsewhere on the page.
            if upside is None:
                accent, bg = _MUTED_TEXT, _PANEL_BG_NEUTRAL
            elif upside > 0:
                accent, bg = _GREEN, _PANEL_BG_GREEN
            elif upside > -0.20:
                accent, bg = _AMBER, _PANEL_BG_AMBER
            else:
                accent, bg = _RED, _PANEL_BG_RED

            iv_str = _money(iv) if iv else "—"
            upside_str = (f"{upside*100:+.1f}% vs price"
                          if upside is not None else "")
            upside_color = (accent if upside is not None else _MUTED_TEXT)

            # Compact assumptions footer
            wacc = s.get("wacc")
            tg = s.get("terminal_growth")
            tm = s.get("terminal_margin")
            y1_5 = s.get("y1_5_cagr")
            assumption_pairs: List[str] = []
            if y1_5 is not None:
                assumption_pairs.append(f"Y1-5 <b>{y1_5*100:.1f}%</b>")
            if tg is not None:
                assumption_pairs.append(f"g <b>{tg*100:.2f}%</b>")
            if wacc is not None:
                assumption_pairs.append(f"WACC <b>{wacc*100:.2f}%</b>")
            if tm is not None:
                assumption_pairs.append(f"margin <b>{tm*100:.1f}%</b>")
            assumption_html = " · ".join(assumption_pairs)

            st.markdown(
                f"""
<div style='background:{bg};border-top:3px solid {accent};
            padding:14px 14px 10px 14px;border-radius:6px;color:inherit;
            min-height:170px'>
  <div style='font-size:12px;font-weight:600;color:inherit;
              opacity:0.85;letter-spacing:0.02em;margin-bottom:8px'>{label}</div>
  <div style='font-family:Syne,sans-serif;font-size:26px;font-weight:800;
              line-height:1.2;color:inherit'>{iv_str}</div>
  <div style='font-size:12px;color:{upside_color};font-weight:600;
              font-family:DM Mono,monospace;margin-top:4px'>{upside_str}</div>
  <div style='font-size:11px;color:{_MUTED_TEXT};
              font-family:DM Mono,monospace;margin-top:10px;line-height:1.6'>
    {assumption_html}
  </div>
</div>
                """,
                unsafe_allow_html=True,
            )

    # ── Probability-weighted expected value ─────────────────────────────
    # Turn the five-scenario visual into an explicit expected-value calc:
    # the analyst assigns a probability to each scenario (defaults to equal
    # weighting); weights are normalized to 100% and used to compute the
    # probability-weighted expected IPS and expected return vs price.
    valid = [s for s in scenarios if s.get("iv_per_share") and not s.get("error")]
    price = dcf.get("current_price")
    if valid and price:
        st.markdown("#### Probability-weighted expected value")
        st.caption(
            "Assign a probability to each scenario (defaults to equal "
            "weighting). Weights are normalized to 100%; expected IPS = "
            "Σ(probability × scenario IPS)."
        )
        n = len(valid)
        default_w = round(100.0 / n, 1)
        wcols = st.columns(n)
        raw_weights: List[float] = []
        for wc, s in zip(wcols, valid):
            with wc:
                w = st.number_input(
                    f"{s['label']} (%)",
                    min_value=0.0, max_value=100.0, value=default_w, step=5.0,
                    key=f"scen_prob_{ticker}_{s['label']}",
                    help="Probability you assign to this scenario.",
                )
                raw_weights.append(w)

        total_w = sum(raw_weights)
        if total_w > 0:
            norm = [w / total_w for w in raw_weights]
            exp_ips = sum(wn * s["iv_per_share"] for wn, s in zip(norm, valid))
            exp_ret = (exp_ips - price) / price
            m1, m2, m3 = st.columns(3)
            m1.metric("Expected IPS", _money(exp_ips))
            m2.metric("Expected return", f"{exp_ret*100:+.1f}%",
                      help="(Expected IPS − current price) / current price.")
            m3.metric(
                "Σ weights",
                f"{total_w:.0f}%",
                help=("Raw weights entered above. Anything ≠ 100% is "
                      "normalized before the expected value is computed.")
                if abs(total_w - 100.0) > 0.5 else None,
            )
            # Per-scenario contribution to the expected IPS.
            contrib = " · ".join(
                f"{s['label'].split(' (')[0]} {wn*100:.0f}%→"
                f"${wn * s['iv_per_share']:,.0f}"
                for wn, s in zip(norm, valid)
            )
            st.caption(f"Contributions: {contrib}")
        else:
            st.caption("Set at least one non-zero probability to compute "
                       "the expected value.")

    return scenarios


# ────────────────────────────────────────────────────────────────────────
# Section 4 — Plain-language synthesis (template-based)
# ────────────────────────────────────────────────────────────────────────

def render_synthesis(ticker: str, dcf: Dict[str, Any], scenarios: List[Dict[str, Any]]) -> None:
    st.markdown("### Synthesis")
    st.caption("Auto-generated from scenario outputs. Describes what the model says, not what to do.")

    valid = [s for s in scenarios if s.get("iv_per_share") and not s.get("error")]
    price = dcf.get("current_price")
    if not valid or not price:
        st.info("Insufficient data for synthesis.")
        return

    above = [s for s in valid if s.get("iv_per_share", 0) > price]
    below = [s for s in valid if s.get("iv_per_share", 0) <= price]

    parts: List[str] = []
    parts.append(
        f"At <strong>${price:,.2f}</strong>, "
        f"<strong>{len(above)} of {len(valid)}</strong> scenarios put IV "
        f"above current price and <strong>{len(below)}</strong> put it at or below."
    )

    base = next((s for s in valid if s.get("label", "").startswith("Base")), None)
    consensus = next((s for s in valid if "Consensus" in s.get("label", "")), None)
    if base and consensus and base.get("y1_5_cagr") and consensus.get("y1_5_cagr"):
        engine_y1_5 = base["y1_5_cagr"]
        consensus_y1_5 = consensus["y1_5_cagr"]
        diff = (engine_y1_5 - consensus_y1_5) * 100
        if abs(diff) > 1.5:
            direction = "more aggressive than" if diff > 0 else "more conservative than"
            color = _AMBER if diff > 0 else _GREEN
            parts.append(
                f"The engine's base case Y1-5 growth "
                f"<strong style='color:{color}'>{engine_y1_5*100:.1f}%</strong> "
                f"is {direction} analyst consensus "
                f"<strong>{consensus_y1_5*100:.1f}%</strong>; applying consensus produces "
                f"IV <strong>${consensus['iv_per_share']:,.2f}</strong>."
            )

    ivs = [s["iv_per_share"] for s in valid]
    spread = max(ivs) - min(ivs)
    if price and spread > 0:
        parts.append(
            f"Cross-scenario IV spread is <strong>${spread:,.2f}</strong> "
            f"(<strong>{spread / price * 100:.0f}%</strong> of current price), "
            f"driven primarily by differences in terminal growth and discount-rate assumptions."
        )

    wacc = dcf.get("wacc_base")
    if wacc:
        parts.append(
            f"Base-case WACC is <strong>{wacc*100:.2f}%</strong> "
            f"(Rf {dcf.get('risk_free_rate', 0)*100:.2f}% + "
            f"β {dcf.get('beta', 0):.2f} × ERP). A 100bp WACC change typically moves IV by "
            f"10–20%; the sensitivity tornado on the Sensitivity tab quantifies this for {ticker}."
        )

    # Bordered panel matching the lead-thesis / contrarian style on Deep Dive.
    _panel(" ".join(parts), accent_color=_AMBER, bg=_PANEL_BG_AMBER)


# ────────────────────────────────────────────────────────────────────────
# Section 5 — Top 5 ratios + Override visibility + Data quality warnings
# ────────────────────────────────────────────────────────────────────────

def render_top_ratios(ticker: str) -> None:
    st.markdown("### Top 5 ratios")
    card = cached_screening_card(ticker)
    if card is None or card.get("error"):
        msg = card.get("message", "screening unavailable") if card else "screening unavailable"
        st.info(f"Screening engine: {msg}")
        return

    target_names = {
        "P/E Ratio", "EV/EBITDA (clean)", "ROIC vs WACC",
        "Net Debt / EBITDA", "FCF Margin %",
    }
    metrics = card.get("metrics", []) or []
    selected = [m for m in metrics if m.get("name") in target_names]
    if not selected:
        st.info("No matching ratios in screening output.")
        return

    rows = []
    for m in selected:
        v = m.get("value")
        name = m.get("name") or ""
        # Status-dot prefix (validation), separate signal column (pass/flag/fail).
        rows.append({
            "":         _status_dot_for(ticker, name),
            "Ratio":     name,
            "Value":     f"{v:.2f}" if isinstance(v, (int, float)) else (v if v is not None else "—"),
            "Threshold": m.get("threshold", "—"),
            "Signal":    m.get("signal", "—"),
        })
    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "":          st.column_config.TextColumn("", width="small",
                                                     help="Validation status"),
            "Ratio":     st.column_config.TextColumn("Ratio", width="medium"),
            "Value":     st.column_config.TextColumn("Value", width="small"),
            "Threshold": st.column_config.TextColumn("Threshold", width="medium"),
            "Signal":    st.column_config.TextColumn("Signal", width="small"),
        },
    )
    st.caption(
        "Status dots reflect external validation against SEC/FMP. "
        "Signal column (pass/flag/fail) is uncolored by design — "
        "analyst reads value vs threshold and forms own view."
    )


def render_override_visibility(ticker: str) -> None:
    """Active overrides — lifecycle profile, KNOWN_ISSUES, saved scenarios.

    Collapsed by default unless something material is present (KNOWN_ISSUES
    entries or saved scenarios). The lifecycle profile is informational and
    not enough to force expansion on its own.
    """
    issues = cached_known_issues(ticker)
    cls = cached_classification(ticker)

    saved = []
    try:
        from aletheia.scenarios.persistence import list_scenarios
        saved = list_scenarios(ticker) or []
    except Exception:
        saved = []

    material = bool(issues) or bool(saved)
    summary = []
    if cls:
        summary.append(f"`{cls.get('lifecycle', '?')}`")
    if issues:
        summary.append(f"{len(issues)} KNOWN_ISSUES")
    if saved:
        summary.append(f"{len(saved)} saved scenarios")
    summary_str = " · ".join(summary) if summary else "none"

    with st.expander(f"### Active overrides — {summary_str}", expanded=material):
        if cls:
            st.markdown(f"**Lifecycle profile:** `{cls.get('lifecycle', '?')}`")
            if cls.get("notes"):
                st.caption(f"_{cls['notes']}_")
        if issues:
            st.markdown(f"**KNOWN_ISSUES entries** ({len(issues)})")
            for it in issues:
                field = it.get("field") or "general"
                wkn = it.get("workaround") or "—"
                desc = (it.get("description") or "")[:160]
                st.markdown(f"- `{field}` *({wkn})*: {desc}")
        if saved:
            st.markdown(f"**Saved scenarios** ({len(saved)})")
            for s in saved[:5]:
                st.markdown(f"- {s.created_at}: {s.name}")
        if not material and not cls:
            st.caption("_No active overrides for this ticker._")


def render_quality_warnings(ticker: str, df: pd.DataFrame) -> None:
    """Data-quality flags from the cleaning engine.

    Only renders when there's something to show. Visual treatment matches
    the panel-tinted alerts elsewhere on the page so severity is obvious
    at a glance.
    """
    del ticker  # passed for symmetry with other render_* signatures
    if df.empty:
        return
    latest = df.sort_values("fiscal_year").iloc[-1]
    warning_count = int(latest.get("warning_count") or 0)
    error_count = int(latest.get("error_count") or 0)
    if warning_count == 0 and error_count == 0:
        return

    accent, bg = (_RED, _PANEL_BG_RED) if error_count else (_AMBER, _PANEL_BG_AMBER)
    icon = "❌" if error_count else "⚠"
    label = (
        f"{icon} Data quality — "
        f"<strong>{warning_count}</strong> warning{'s' if warning_count != 1 else ''}"
        f" · <strong>{error_count}</strong> error{'s' if error_count != 1 else ''}"
    )
    body = (
        f"<div style='font-weight:600;margin-bottom:6px'>{label}</div>"
        "<div style='font-size:12px;opacity:0.85'>"
        "Quality flags are emitted by the cleaning engine when it adjusts values, "
        "fills missing fields by derivation, or detects boundary conditions. "
        "These are informational; review when interpreting model outputs."
        "</div>"
    )
    _panel(body, accent_color=accent, bg=bg)


# ────────────────────────────────────────────────────────────────────────
# Specialized-model notice (banks, insurers, asset managers)
# ────────────────────────────────────────────────────────────────────────

_MODEL_LABEL = {
    "ddm_required":             "Dividend Discount Model",
    "embedded_value_required":  "Embedded Value (life insurance)",
    "routing_required":         "Specialized routing (bank / asset manager)",
}

_ENGINE_LABEL = {
    "fcff":           "FCFF DCF",
    "rate_base":      "Rate-base DCF (regulated utility)",
    "ddm":            "Dividend Discount Model",
    "embedded_value": "Embedded Value (book-value compounding)",
}

_ENGINE_DESCRIPTION = {
    "rate_base": (
        "Two-stage rate-base valuation: equity value = Σ (rate base × "
        "allowed ROE) discounted at cost of equity, plus terminal "
        "perpetuity. Inputs from the latest rate-case order."
    ),
    "ddm": (
        "Two-stage Dividend Discount Model: explicit DPS growth + "
        "Gordon-growth terminal. Used for bank / managed-care filers "
        "where FCFF doesn't apply."
    ),
    "embedded_value": (
        "Book-value compounding × target P/B horizon model. Used for "
        "insurance conglomerates where consolidated economics span "
        "underwriting, float, and operating subsidiaries."
    ),
}


def _render_specialized_engine_dashboard(
    ticker: str, valuation: Dict[str, Any], df: pd.DataFrame,
) -> None:
    """Dashboard for tickers valued by a non-FCFF engine (NEE rate-base,
    JPM/AXP/UNH DDM, BRK-B embedded value). Surfaces the engine's IV,
    MoS, year-by-year decomposition, and source citation."""
    engine = valuation.get("engine") or "specialized"
    engine_label = _ENGINE_LABEL.get(engine, engine)
    engine_desc = _ENGINE_DESCRIPTION.get(engine, "")
    # #4 method-appropriate headline: when the DDM was displaced for a low-payout
    # bank, the PRESENTED method is residual income (the headline IV is $315, not
    # the DDM $118), so lead with that label — the DDM is a displaced sub-leg.
    if valuation.get("headline_override"):
        engine_label = "Residual income — bank convergent set (DDM displaced)"

    cls = cached_classification(ticker)
    ips = valuation.get("intrinsic_per_share")
    price = valuation.get("current_price")
    mos = valuation.get("margin_of_safety")
    ke = valuation.get("cost_of_equity")

    # Header row
    col1, col2, col3 = st.columns([3, 2, 2])
    with col1:
        st.markdown(f"## {ticker}")
        if cls:
            pill = (f"{cls.get('sector', '?')} · {cls.get('industry', '?')} "
                    f"· `{cls.get('lifecycle', '?')}`")
            st.caption(pill)
        st.caption(f"Valuation engine: **{engine_label}**")
    with col2:
        st.metric("Price", _money(price))
    with col3:
        st.metric("Intrinsic value", _money(ips))

    # MoS / Ke / engine description
    col1, col2, col3 = st.columns([1, 1, 3])
    with col1:
        if mos is None:
            st.metric("Margin of safety", "—")
        else:
            accent = (_GREEN if mos > 0
                      else (_AMBER if mos > -0.20 else _RED))
            st.markdown(
                f"<div style='font-size:12px;color:{_MUTED_TEXT};"
                f"margin-bottom:4px'>Margin of safety</div>"
                f"<div style='font-size:22px;font-weight:700;color:{accent}'>"
                f"{_pct(mos)}</div>",
                unsafe_allow_html=True,
            )
    with col2:
        st.metric(
            "Cost of equity",
            f"{ke*100:.2f}%" if ke else "—",
        )
    with col3:
        if engine_desc:
            _panel(engine_desc, accent_color=_MUTED_TEXT)

    # Headline-method note: for a low-payout bank the displayed IV is the
    # residual-income fair value, NOT the structurally-low DDM (which is displaced
    # because it ignores the ROE-spread compounding on retained book). Tell the
    # reader the headline was swapped and show the band.
    _ho = valuation.get("headline_override") or {}
    if _ho:
        band = _ho.get("fair_band") or []
        band_txt = (f"\\${band[0]:,.0f}–\\${band[1]:,.0f}"
                    if len(band) == 2 else "see below")
        ddm = _ho.get("displaced_ddm")
        st.info(
            f"Headline = **residual income** (\\${ips:,.0f}), the method-appropriate "
            f"read for a low-payout bank. The routed DDM"
            f"{f' (\\${ddm:,.0f})' if ddm is not None else ''} is displaced — it sees "
            f"only the dividend, not the ROE-spread compounding on retained book, so it "
            f"structurally understates fair value. Convergent-set band **{band_txt}** "
            f"(justified-P/B floor → residual income; FCFE mid-band) — full breakdown below."
        )

    # Source citation
    source = valuation.get("source")
    as_of = valuation.get("as_of_date")
    if source:
        st.caption(
            f"_Inputs as of **{as_of or '—'}** · Source: {source}_"
        )

    # Engine-specific decomposition
    decomposition = valuation.get("decomposition")
    if decomposition:
        st.markdown("---")
        _render_engine_decomposition(engine, decomposition)

    # All bank valuations — the convergent set (RI / justified P/B / Gordon / FCFE)
    # alongside the routed headline, so the dashboard shows every method (and flags
    # the low-payout DDM understatement that makes the headline misleading for
    # JPM/AXP). Self-gates: non-banks carry no bank_valuation_methods and skip.
    bvm = valuation.get("bank_valuation_methods")
    if bvm and bvm.get("available"):
        from aletheia.ui.deep_dive_view import _bank_valuation_panel
        _bank_valuation_panel({"bank_valuation_methods": bvm})

    # Analyst warnings (decomposition pre-flight notes)
    warnings = valuation.get("warnings") or []
    for w in warnings:
        st.caption(f"⚠ {w}")

    # Still show 5-year fundamentals — useful context even when FCFF
    # doesn't apply.
    st.markdown("---")
    render_trends_table(df, ticker=ticker)

    # Quality warnings render conditionally (only if something to show).
    render_quality_warnings(ticker, df)


def _render_engine_decomposition(
    engine: str, decomp: Dict[str, Any],
) -> None:
    """Year-by-year breakdown — engine-specific shape:
      - rate_base: yearly = [{year, rate_base, earnings, pv}]
      - ddm:       yearly = [{year, dps, pv}]
      - embedded_value: yearly_bvps = [{year, bvps}]
    """
    st.markdown("### Year-by-year breakdown")
    if engine == "rate_base":
        yearly = decomp.get("yearly") or []
        rows = [{
            "Year":         y.get("year"),
            "Rate base":    _bn(y.get("rate_base")),
            "Earnings":     _bn(y.get("earnings")),
            "PV":           _bn(y.get("pv")),
        } for y in yearly]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True,
                         use_container_width=True)
        cols = st.columns(4)
        cols[0].metric("PV explicit", _bn(decomp.get("pv_explicit")))
        cols[1].metric("PV terminal", _bn(decomp.get("pv_terminal")))
        cols[2].metric("Equity value", _bn(decomp.get("equity_value")))
        cols[3].metric("TV share", _pct_unsigned(decomp.get("tv_share_of_equity")))
    elif engine == "ddm":
        yearly = decomp.get("yearly") or []
        rows = [{
            "Year":    y.get("year"),
            "DPS":     _money(y.get("dps")),
            "PV":      _money(y.get("pv")),
        } for y in yearly]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True,
                         use_container_width=True)
        cols = st.columns(3)
        cols[0].metric("PV explicit", _money(decomp.get("pv_explicit")))
        cols[1].metric("PV terminal", _money(decomp.get("pv_terminal")))
        cols[2].metric("Intrinsic / share",
                       _money(decomp.get("intrinsic_per_share")))
    elif engine == "embedded_value":
        yearly = decomp.get("yearly_bvps") or []
        rows = [{
            "Year": y.get("year"),
            "BVPS": _money(y.get("bvps")),
        } for y in yearly]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True,
                         use_container_width=True)
        cols = st.columns(3)
        cols[0].metric("Starting BVPS", _money(decomp.get("starting_bvps")))
        cols[1].metric("Future BVPS",   _money(decomp.get("future_bvps")))
        cols[2].metric("Intrinsic / share",
                       _money(decomp.get("intrinsic_per_share")))


def _render_specialized_model_notice(ticker: str, dcf: Dict[str, Any]) -> None:
    """Friendly fallback when the DCFEngine refuses to value a ticker
    because its business model needs a specialized framework we haven't
    shipped yet (UNH, CNC, banks, insurers, asset managers)."""
    bm = dcf.get("business_model") or "specialized"
    label = _MODEL_LABEL.get(bm, bm)
    reason = dcf.get("reason") or "Float-based or financial-services business; FCFF DCF inappropriate."
    st.markdown(f"## {ticker}")
    st.warning(
        f"**No FCFF DCF available for {ticker}.**\n\n"
        f"Classified as **{bm}** — requires {label}, which isn't implemented "
        f"in this build. Showing financials and scenarios for this ticker "
        f"would produce misleading numbers (cash-flow definitions don't map "
        f"to a free-cash-flow framework)."
    )
    st.caption(f"Classification rationale: {reason}")
    st.info(
        "What you can still use: the **Financials** tab for raw filings, "
        "and the **Quality Report** tab for ingestion validation. The "
        "Deep Dive and Scenarios tabs depend on the DCF and are "
        "intentionally hidden for this ticker."
    )


def _render_iv_suppressed_notice(ticker: str, dcf: Dict[str, Any], df) -> None:
    """Clean notice when an FCFF ticker's headline IV is suppressed as
    degenerate — pre-profitability (negative operating income) or a base
    scenario that produced a nonsensical value. Surfacing a fabricated tiny
    per-share number (NET's old ~$2 vs a ~$262 price) is worse than showing
    none, so we explain why and still render the financials."""
    reason = dcf.get("iv_suppressed_reason") or "base DCF scenario is degenerate."
    st.markdown(f"## {ticker}")
    st.warning(
        f"**FCFF DCF not meaningful for {ticker}.**\n\n"
        f"{reason.capitalize()}\n\n"
        "A discounted-cash-flow value requires positive, projectable operating "
        "cash flow. When the company isn't yet profitable, the model would seed "
        "a negative NOPAT and emit a fabricated near-zero per-share value — so "
        "the headline intrinsic value is intentionally withheld rather than shown."
    )
    st.caption(
        "For pre-profitability / high-growth names, value on an EV/revenue or "
        "path-to-profitability basis instead of FCFF."
    )
    # The financials are still trustworthy and worth showing.
    try:
        render_trends_table(df, ticker=ticker)
    except Exception:
        pass


# ────────────────────────────────────────────────────────────────────────
# Top-level entry
# ────────────────────────────────────────────────────────────────────────

def render_dashboard(ticker: str) -> None:
    """Main dashboard entry point. Called from streamlit_app.py routing."""
    if not ticker:
        st.info("Select a ticker from the sidebar to view its dashboard.")
        return

    df = cached_calc_df(ticker)
    dcf = cached_dcf_summary(ticker)

    # Specialized-model tickers (NEE, JPM/AXP/UNH, BRK-B, CNC) don't have
    # an FCFF DCF — they route to a dedicated engine via ValuationRouter.
    # Run that here and render an engine-specific dashboard. Falls back
    # to the legacy "no DCF available" notice only when the router
    # itself can't produce an IV (e.g. CNC's no-dividend empty-state,
    # KNOWN_ISSUES bypass).
    if isinstance(dcf, dict) and dcf.get("error") == "specialized_model_required":
        valuation = cached_valuation(ticker)
        if (isinstance(valuation, dict)
                and not valuation.get("error")
                and valuation.get("intrinsic_per_share") is not None):
            _render_specialized_engine_dashboard(ticker, valuation, df)
            return
        _render_specialized_model_notice(ticker, dcf)
        return

    # Pre-profitability / degenerate-DCF tickers (e.g. NET): the FCFF headline
    # IV was suppressed upstream. Show a clean explanation instead of scenario
    # cards full of $0.00 / fabricated near-zero per-share values.
    if isinstance(dcf, dict) and dcf.get("iv_suppressed"):
        _render_iv_suppressed_notice(ticker, dcf, df)
        return

    # 1. Header strip — ticker, classification, price, business description,
    #    quality, macro context.
    render_header(ticker, dcf, df)

    # 2. 5-year fundamentals — pivoted by FY with status dots inline.
    st.markdown("---")
    render_trends_table(df, ticker=ticker)

    # 3. Five scenarios — severity-tinted cards (green positive MoS, amber
    #    modest premium, red >20% premium).
    st.markdown("---")
    scenarios = render_scenarios(ticker, dcf)

    # 4. Synthesis — bordered panel matching the lead-thesis style.
    st.markdown("---")
    render_synthesis(ticker, dcf, scenarios)

    # 5. Diagnostics — top ratios + active overrides + quality flags.
    st.markdown("---")
    render_top_ratios(ticker)

    st.markdown("---")
    render_override_visibility(ticker)

    # Quality warnings render conditionally (only if something to show).
    render_quality_warnings(ticker, df)
