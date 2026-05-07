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
)


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


# ────────────────────────────────────────────────────────────────────────
# Section 2 — 5-year fundamentals
# ────────────────────────────────────────────────────────────────────────

def render_trends_table(df: pd.DataFrame, ticker: Optional[str] = None) -> None:
    st.markdown("### 5-year fundamentals")
    if df.empty:
        st.info("No fundamentals available.")
        return

    sorted_df = df.sort_values("fiscal_year").tail(5).copy()
    rev_yoy: List[Optional[float]] = []
    revs = sorted_df["clean_Revenue"].tolist()
    for i, r in enumerate(revs):
        if i == 0 or revs[i - 1] in (None, 0) or pd.isna(revs[i - 1]) or pd.isna(r):
            rev_yoy.append(None)
        else:
            rev_yoy.append(float(r) / float(revs[i - 1]) - 1)

    # Badge metric labels with their validation status (* / ~ / ⚠).
    from aletheia.ui.validation_badge import badge_label
    fy = [int(y) for y in sorted_df["fiscal_year"].tolist()]
    out = pd.DataFrame({
        "metric": [
            badge_label("Revenue", ticker) + " ($B)",
            "Revenue YoY %",
            badge_label("EBIT Margin", ticker) + " %",
            "FCF margin %",
            badge_label("ROIC", ticker) + " %",
            "Net Debt ($B)",
        ],
    })
    for i, year in enumerate(fy):
        col_name = f"FY{year}"
        out[col_name] = [
            _bn(sorted_df["clean_Revenue"].iloc[i] if i < len(sorted_df) else None).replace("$", "").replace("B", ""),
            _pct(rev_yoy[i]) if i < len(rev_yoy) else "—",
            _pct_unsigned((sorted_df["derived_EBIT_Margin_Pct"].iloc[i] or 0) / 100)
                if pd.notna(sorted_df["derived_EBIT_Margin_Pct"].iloc[i]) else "—",
            _pct_unsigned((sorted_df["derived_FCF_Margin_Pct"].iloc[i] or 0) / 100)
                if pd.notna(sorted_df["derived_FCF_Margin_Pct"].iloc[i]) else "—",
            _pct_unsigned(sorted_df["derived_ROIC"].iloc[i])
                if pd.notna(sorted_df["derived_ROIC"].iloc[i]) else "—",
            _bn(sorted_df["derived_NetDebt"].iloc[i] if i < len(sorted_df) else None).replace("$", "").replace("B", ""),
        ]
    st.dataframe(out, use_container_width=True, hide_index=True)


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
                st.markdown(f"**{label}**")
                st.caption(f"_unavailable: {s['error'][:80]}_")
                continue

            iv = s.get("iv_per_share")
            upside = s.get("upside_pct")
            st.markdown(f"**{label}**")
            st.markdown(f"### {_money(iv)}")
            if upside is not None:
                st.caption(f"vs price: {_pct(upside)}")

            wacc = s.get("wacc")
            tg = s.get("terminal_growth")
            tm = s.get("terminal_margin")
            y1_5 = s.get("y1_5_cagr") or s.get("y1_5_cagr")
            assumption_lines = []
            if y1_5 is not None:
                assumption_lines.append(f"Y1-5: {y1_5*100:.1f}%")
            if tg is not None:
                assumption_lines.append(f"g: {tg*100:.2f}%")
            if wacc is not None:
                assumption_lines.append(f"WACC: {wacc*100:.2f}%")
            if tm is not None:
                assumption_lines.append(f"margin: {tm*100:.1f}%")
            for line in assumption_lines:
                st.caption(line)

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
        f"At ${price:,.2f}, {len(above)} of {len(valid)} scenarios put IV above current price "
        f"and {len(below)} put it at or below."
    )

    base = next((s for s in valid if s.get("label", "").startswith("Base")), None)
    consensus = next((s for s in valid if "Consensus" in s.get("label", "")), None)
    if base and consensus and base.get("y1_5_cagr") and consensus.get("y1_5_cagr"):
        engine_y1_5 = base["y1_5_cagr"]
        consensus_y1_5 = consensus["y1_5_cagr"]
        diff = (engine_y1_5 - consensus_y1_5) * 100
        if abs(diff) > 1.5:
            direction = "more aggressive than" if diff > 0 else "more conservative than"
            parts.append(
                f"The engine's base case Y1-5 growth ({engine_y1_5*100:.1f}%) is {direction} "
                f"analyst consensus ({consensus_y1_5*100:.1f}%); applying consensus produces "
                f"IV ${consensus['iv_per_share']:,.2f}."
            )

    ivs = [s["iv_per_share"] for s in valid]
    spread = max(ivs) - min(ivs)
    if price and spread > 0:
        parts.append(
            f"Cross-scenario IV spread is ${spread:,.2f} "
            f"({spread / price * 100:.0f}% of current price), driven primarily by "
            f"differences in terminal growth and discount rate assumptions."
        )

    wacc = dcf.get("wacc_base")
    if wacc:
        parts.append(
            f"The base-case WACC is {wacc*100:.2f}% (Rf {dcf.get('risk_free_rate', 0)*100:.2f}% + "
            f"β {dcf.get('beta', 0):.2f} × ERP). A 100bp WACC change typically moves IV "
            f"by 10-20%; sensitivity tornado on the Sensitivity tab quantifies this for {ticker}."
        )

    st.markdown(" ".join(parts))


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

    from aletheia.ui.validation_badge import badge_label
    rows = []
    for m in selected:
        v = m.get("value")
        name = m.get("name") or ""
        rows.append({
            "ratio":     badge_label(name, ticker),
            "value":     v if v is not None else "—",
            "threshold": m.get("threshold", "—"),
            "status":    m.get("signal", "—"),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(
        "Status labels (pass / flag / fail) are uncolored by design — "
        "analyst reads value vs threshold and forms own view. "
        "Ratio names with `*` are externally validated against FMP."
    )


def render_override_visibility(ticker: str) -> None:
    st.markdown("### Active overrides")
    issues = cached_known_issues(ticker)
    cls = cached_classification(ticker)

    has_anything = False
    if cls:
        st.write(f"**Lifecycle profile:** `{cls.get('lifecycle', '?')}`")
        has_anything = True
    if issues:
        st.write(f"**KNOWN_ISSUES entries:** {len(issues)}")
        for it in issues:
            field = it.get("field") or "general"
            wkn = it.get("workaround") or "—"
            desc = (it.get("description") or "")[:120]
            st.markdown(f"  - `{field}` ({wkn}): {desc}")
        has_anything = True

    saved_paths = []
    try:
        from aletheia.scenarios.persistence import list_scenarios
        saved = list_scenarios(ticker)
        if saved:
            st.write(f"**Saved scenarios:** {len(saved)}")
            for s in saved[:5]:
                st.markdown(f"  - {s.created_at}: {s.name}")
            has_anything = True
    except Exception:
        pass

    if not has_anything:
        st.caption("_No active overrides for this ticker._")


def render_quality_warnings(ticker: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    latest = df.sort_values("fiscal_year").iloc[-1]
    warning_count = int(latest.get("warning_count") or 0)
    error_count = int(latest.get("error_count") or 0)
    if warning_count == 0 and error_count == 0:
        return

    with st.expander(
        f"⚠ Data quality: {warning_count} warnings, {error_count} errors", expanded=False
    ):
        st.caption(
            "Quality flags are emitted by the cleaning engine when it adjusts values, "
            "fills missing fields by derivation, or detects boundary conditions. "
            "These are informational; review when interpreting model outputs."
        )


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

    render_header(ticker, dcf, df)
    st.divider()
    render_trends_table(df, ticker=ticker)
    st.divider()
    scenarios = render_scenarios(ticker, dcf)
    st.divider()
    render_synthesis(ticker, dcf, scenarios)
    st.divider()

    render_top_ratios(ticker)
    st.divider()
    render_override_visibility(ticker)

    render_quality_warnings(ticker, df)
