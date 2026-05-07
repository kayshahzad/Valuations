"""
aletheia/ui/deep_dive_view.py

Re-imagined Deep Dive tab. The previous version was dense inline HTML
mixing pillar scores, DCF bars, moat detail, value chain, and three
narrative blocks. This redesign:

  • Hero strip — 4 KPIs with status dots: Conviction, Base IV / MoS,
    ROIC – WACC spread, Multiple signal.
  • Pillar scorecard — 5 horizontal progress bars (0-5) with hover-on
    pillar reasoning rather than tiny right-aligned numbers.
  • 3-scenario DCF triangle — Bear / Base / Bull intrinsic-per-share with
    bar-graph layout, MoS markers, and width proportional to spread.
  • Two-column body:
      Left  — Moat (score + 4 dimensions + evidence), Value Chain,
              Strategic Context
      Right — Fundamentals (with validation badges), Reverse DCF chart,
              Lead thesis narrative, Contrarian bear case
  • Validation status visible everywhere a number is shown.

Validation badges come from `aletheia.ui.validation_badge`. Status dots
are emoji glyphs (🟢/🟡/🔴/⚪/·) matching the Financials view so analysts
have one consistent visual language across the app.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# ── Status palette (matches Financials view) ──────────────────────────────

_STATUS_DOT = {
    "validated": "🟢",
    "near":      "🟡",
    "drift":     "🔴",
    "missing":   "⚪",
    "unknown":   "·",
}

_STATUS_HELP = {
    "validated": "Externally validated within 1%",
    "near":      "Within 1–5% (documented difference)",
    "drift":     ">5% drift — investigate",
    "missing":   "Field absent on validator side",
    "unknown":   "Not yet validated",
}


# ── Color tokens ──────────────────────────────────────────────────────────
# Semantic colors only — green/amber/red carry meaning (good/neutral/bad).
# Body text uses `inherit` / CSS variables so it follows the active theme
# (works in both light and dark mode without hardcoded grays).

_GREEN, _AMBER, _RED = "#10b981", "#f59e0b", "#ef4444"
# Secondary text — use the Streamlit theme variable; fallback if unset.
_MUTED_TEXT = "rgba(120,120,128,0.85)"   # legible on both light and dark backgrounds
_BAR_BG     = "rgba(120,120,128,0.20)"   # subtle track for progress bars
_PANEL_BG_AMBER = "rgba(245,158,11,0.08)"
_PANEL_BG_RED   = "rgba(239,68,68,0.06)"


# ── Formatters ────────────────────────────────────────────────────────────

def _money(v: Optional[float]) -> str:
    return f"${v:,.0f}" if v else "—"


def _bn(v: Optional[float], dp: int = 1) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1e3:
        return f"${v/1e3:,.{dp}f}T"
    return f"${v:,.{dp}f}B"


def _pct(v: Optional[float], dp: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v*100:.{dp}f}%" if abs(v) < 1 else f"{v:.{dp}f}%"


def _signed_pct(v: Optional[float], dp: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v*100:+.{dp}f}%" if abs(v) < 1 else f"{v:+.{dp}f}%"


# ── DCF-method badge ──────────────────────────────────────────────────────
# Maps `business_model` (from ticker classification) → user-facing label
# describing which valuation framework actually applies. Standard FCFF DCF
# breaks down for banks, insurers, utilities, conglomerates — the badge
# tells the analyst at a glance which numbers on the page are meaningful.

_DCF_METHOD_INFO = {
    "fcff_compatible": {
        "label":   "FCFF DCF",
        "long":    "Free Cash Flow to Firm DCF",
        "color":   _GREEN,
        "summary": ("Standard DCF: project FCFF → discount at WACC → "
                    "add terminal value → subtract net debt → equity value."),
        "valid":   True,
    },
    "ddm_required": {
        "label":   "Dividend Discount",
        "long":    "Dividend Discount Model required",
        "color":   _AMBER,
        "summary": ("Float-based business (insurance / managed care). FCFF DCF "
                    "is misleading because reserves and float distort cash "
                    "flow. Value via DDM on the dividend stream; for life "
                    "insurance use embedded value (in-force policies)."),
        "valid":   False,
    },
    "embedded_value_required": {
        "label":   "Embedded Value",
        "long":    "Embedded-value accounting (life insurance)",
        "color":   _AMBER,
        "summary": ("Life insurance specifically — value the present value "
                    "of in-force policies plus net asset value. Standard DCF "
                    "doesn't apply."),
        "valid":   False,
    },
    "routing_required": {
        "label":   "Schema-specific",
        "long":    "Specialized framework required",
        "color":   _RED,
        "summary": ("Bank / utility / conglomerate. FCFF DCF doesn't apply — "
                    "use efficiency ratio + NIM + ROTCE for banks, "
                    "rate-base + allowed-ROE for utilities, segment-level "
                    "analysis for conglomerates."),
        "valid":   False,
    },
}


def _classification_for(ticker: str) -> Dict[str, Any]:
    """Look up the ticker's classification (curated + runtime)."""
    try:
        from config.ticker_classification import get_extended_universe
        cls = get_extended_universe().get(ticker.upper())
        if cls is None:
            return {}
        return {
            "sector":         cls.sector,
            "industry":       cls.industry,
            "lifecycle":      cls.lifecycle,
            "business_model": cls.business_model,
            "is_ifrs_filer":  cls.is_ifrs_filer,
            "notes":          cls.notes,
        }
    except Exception:
        return {}


def _dcf_method_badge(ticker: str) -> None:
    """
    Render a small badge below the ticker title showing which DCF method
    actually applies, plus a one-line caption. For non-`fcff_compatible`
    tickers, also render a callout panel explaining why standard DCF
    numbers shown on this page should be interpreted with caution.
    """
    cls = _classification_for(ticker)
    bm = cls.get("business_model") or "fcff_compatible"
    info = _DCF_METHOD_INFO.get(bm, _DCF_METHOD_INFO["fcff_compatible"])

    sector = cls.get("sector") or "—"
    industry = cls.get("industry") or "—"
    ifrs = " · IFRS filer" if cls.get("is_ifrs_filer") else ""

    # Inline pill: method + sector/industry context
    st.markdown(
        f"""
<div style='display:flex;gap:10px;align-items:center;flex-wrap:wrap;
            margin:-8px 0 12px 0;font-size:13px;color:inherit'>
  <span style='display:inline-flex;align-items:center;gap:6px;
               background:rgba(120,120,128,0.12);border:1px solid {info["color"]};
               color:inherit;padding:3px 10px;border-radius:999px;
               font-family:DM Mono,monospace;font-size:11px;font-weight:600;
               letter-spacing:0.04em'>
    <span style='display:inline-block;width:6px;height:6px;border-radius:50%;
                 background:{info["color"]}'></span>
    Method: {info["label"]}
  </span>
  <span style='color:inherit;opacity:0.7;font-size:12px'>
    {sector} · {industry}{ifrs}
  </span>
</div>
        """,
        unsafe_allow_html=True,
    )

    # For non-FCFF tickers, add a panel callout so the analyst knows the
    # DCF numbers on this page (intrinsic-per-share, MoS, ROIC vs WACC)
    # are computed but **not the right valuation framework**.
    if not info["valid"]:
        st.markdown(
            f"""
<div style='background:rgba(245,158,11,0.06);border-left:4px solid {info["color"]};
            padding:14px 16px;border-radius:0 6px 6px 0;color:inherit;
            font-size:13px;line-height:1.6;margin-bottom:14px'>
  <strong style='color:{info["color"]}'>⚠ {info["long"]}</strong>
  <div style='margin-top:6px'>{info["summary"]}</div>
  <div style='margin-top:8px;opacity:0.8;font-size:12px'>
    The DCF-derived values shown below (Base IV, MoS, ROIC vs WACC, scenario
    triangle) are still computed — but they aren't the right valuation lens
    for this schema. Cross-check against the metrics named above.
  </div>
</div>
            """,
            unsafe_allow_html=True,
        )


# ── Validation lookup ─────────────────────────────────────────────────────

def _status_for(ticker: str, label: str) -> str:
    try:
        from aletheia.ui.validation_badge import lookup_status
        return lookup_status(ticker, label)
    except Exception:
        return "unknown"


def _dot(ticker: str, label: str) -> str:
    return _STATUS_DOT.get(_status_for(ticker, label), "·")


# ── Hero strip ────────────────────────────────────────────────────────────

def _hero_strip(
    ticker: str,
    investment_thesis: Dict[str, Any],
    dcf: Dict[str, Any],
    fund: Dict[str, Any],
    universe_row: Dict[str, Any],
) -> None:
    conv = investment_thesis.get("conviction_score") if investment_thesis else None
    base = (dcf or {}).get("base") or {}
    base_iv = base.get("intrinsic_per_share") or 0
    base_mos = base.get("margin_of_safety")
    roic = fund.get("roic") if fund else None
    wacc = (dcf or {}).get("wacc")
    spread = (roic - wacc) if (roic is not None and wacc is not None) else None
    sig = universe_row.get("multiple_signal", "—")
    ev_eb = universe_row.get("ev_ebitda")
    just = universe_row.get("justified_ev_ebitda")

    cols = st.columns(4)
    # Conviction
    with cols[0]:
        if conv is not None:
            st.metric("Conviction", f"{int(conv):+d} / 10",
                      delta=universe_row.get("value_creation", "").upper() or None,
                      delta_color="off")
        else:
            st.metric("Conviction", "—")
    # Base IV / MoS
    with cols[1]:
        st.metric(f"{_dot(ticker, 'Base IV')} Base IV",
                  _money(base_iv) if base_iv else "—",
                  delta=(_signed_pct(base_mos) if base_mos is not None else None))
    # ROIC – WACC spread
    with cols[2]:
        st.metric(f"{_dot(ticker, 'ROIC')} ROIC − WACC spread",
                  _signed_pct(spread) if spread is not None else "—",
                  delta=(f"ROIC {_pct(roic)} · WACC {_pct(wacc)}"
                         if (roic is not None and wacc is not None) else None),
                  delta_color="off")
    # Multiple signal
    with cols[3]:
        sig_label = {"undervalued": "UNDERVALUED",
                     "fairly_valued": "FAIR",
                     "premium": "PREMIUM",
                     "high_premium": "HIGH PREMIUM"}.get(sig, "—")
        st.metric(f"{_dot(ticker, 'EV/EBITDA')} Multiple signal", sig_label,
                  delta=(f"{ev_eb:.1f}× vs {just:.1f}× justified"
                         if (ev_eb and just) else None),
                  delta_color="inverse")


# ── Pillar scores as horizontal bars ──────────────────────────────────────

_PILLAR_DEFS = [
    ("Moat",        "p1_moat",       "Durability of competitive advantage"),
    ("Health",      "p2_health",     "Balance sheet + cash flow durability"),
    ("Tailwind",    "p3_tailwind",   "Industry/macro structural growth"),
    ("Margin of Safety", "p4_mos",   "Discount of price to intrinsic value"),
    ("Leadership",  "p5_leadership", "Capital allocation + management quality"),
]


def _pillar_section(pillar_scores: Dict[str, Any]) -> None:
    if not pillar_scores:
        return
    capped = pillar_scores.get("capped_total")
    tier = (pillar_scores.get("position_tier") or "—").upper()

    st.markdown("##### Pillar scores  ·  "
                f"<span style='color:inherit;opacity:0.7;"
                f"font-family:DM Mono,monospace;font-size:13px;font-weight:400'>"
                f"total {capped}/25 · tier {tier}</span>",
                unsafe_allow_html=True)

    for name, key, hint in _PILLAR_DEFS:
        raw = pillar_scores.get(key) or {}
        # Pillar can be a dict {score, reasons} or a flat number depending on API shape
        if isinstance(raw, dict):
            score = raw.get("score") or 0
            reasons = raw.get("reasons") or []
        else:
            score = float(raw or 0)
            reasons = []
        score_norm = max(0, min(score, 5))
        color = _GREEN if score >= 4 else _AMBER if score >= 2 else _RED
        # 5-segment bar — empty cells use neutral track color (theme-agnostic)
        bar_html = ""
        for i in range(1, 6):
            seg_color = color if i <= score_norm else _BAR_BG
            bar_html += (f"<span style='display:inline-block;width:36px;height:14px;"
                         f"background:{seg_color};margin-right:4px;border-radius:3px'></span>")
        score_str = f"{score:.0f}/5" if isinstance(score, (int, float)) else "—"
        # Body text uses `inherit` so it follows whichever theme is active.
        st.markdown(
            f"""
<div style='display:flex;align-items:center;gap:14px;padding:8px 0;
            border-bottom:1px solid {_BAR_BG}'>
  <div style='flex-basis:160px;font-size:14px;font-weight:600;color:inherit'>{name}</div>
  <div style='flex-basis:210px'>{bar_html}</div>
  <div style='flex-basis:55px;text-align:right;color:{color};
              font-family:DM Mono,monospace;font-weight:700;font-size:14px'>{score_str}</div>
  <div style='flex:1;color:{_MUTED_TEXT};font-size:13px'>{hint}</div>
</div>
            """,
            unsafe_allow_html=True,
        )
        if reasons:
            with st.expander("rationale", expanded=False):
                for r in (reasons[:3] if isinstance(reasons, list) else []):
                    st.markdown(f"- {r}")


# ── 3-scenario DCF triangle ───────────────────────────────────────────────

def _scenario_triangle(dcf: Dict[str, Any]) -> None:
    scenarios = [
        ("BEAR", dcf.get("bear") or {}, _RED),
        ("BASE", dcf.get("base") or {}, _AMBER),
        ("BULL", dcf.get("bull") or {}, _GREEN),
    ]
    rows = []
    for name, s, color in scenarios:
        iv = s.get("intrinsic_per_share")
        mos = s.get("margin_of_safety")
        rows.append({
            "Scenario": name,
            "IV/share": iv,
            "MoS":      mos,
            "_color":   color,
        })

    df = pd.DataFrame(rows)
    if df["IV/share"].isnull().all():
        st.caption("_no DCF scenarios available_")
        return

    fig = go.Figure()
    fig.add_bar(
        y=df["Scenario"], x=df["IV/share"], orientation="h",
        marker_color=[r["_color"] for r in rows],
        text=[f"${(v or 0):,.0f}" for v in df["IV/share"]],
        textposition="outside",
        textfont=dict(size=11),
        hovertemplate="%{y}: $%{x:,.2f}/share<extra></extra>",
    )
    fig.update_layout(
        height=170,
        margin=dict(l=0, r=20, t=0, b=0),
        xaxis=dict(showgrid=False, zeroline=False, visible=False),
        # Plotly doesn't read CSS variables — pick a tick color with enough
        # contrast on both light and dark themes.
        yaxis=dict(showgrid=False, color=_MUTED_TEXT,
                   tickfont=dict(family="DM Mono", size=13)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    # MoS row
    cols = st.columns(3)
    for col, r in zip(cols, rows):
        mos = r["MoS"]
        if mos is None:
            col.caption(f"{r['Scenario']}: —")
            continue
        mos_color = _GREEN if mos > 0 else _AMBER if mos > -0.2 else _RED
        col.markdown(
            f"<div style='font-family:DM Mono,monospace;font-size:13px;text-align:center'>"
            f"<span style='color:inherit;opacity:0.75'>{r['Scenario']} MoS </span>"
            f"<span style='color:{mos_color};font-weight:700'>{mos*100:+.1f}%</span>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ── Moat block ────────────────────────────────────────────────────────────

def _moat_block(moat: Dict[str, Any], universe_row: Dict[str, Any]) -> None:
    st.markdown("##### Moat")
    score = moat.get("score") or universe_row.get("moat") or 0
    color = _GREEN if score >= 7 else _AMBER if score >= 4 else _RED
    val_creation = (universe_row.get("value_creation", "") or "").upper()

    # Big centered score; subtitle uses `inherit` so it picks up the active
    # theme's foreground (only the score itself is semantic-colored).
    st.markdown(
        f"""
<div style='text-align:center;padding:12px 0'>
  <div style='font-family:Syne,sans-serif;font-size:56px;font-weight:800;
              color:{color};line-height:1'>{score:.1f}</div>
  <div style='font-family:DM Mono,monospace;font-size:12px;color:inherit;
              opacity:0.7;margin-top:6px;letter-spacing:0.05em'>
    / 10  ·  {val_creation}
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    # 4 dimensions — bigger, more legible
    dims = [
        ("Cost Adv.",   moat.get("cost_advantage")),
        ("Network",     moat.get("network_effects")),
        ("Switching",   moat.get("switching_costs")),
        ("Intangible",  moat.get("intangibles")),
    ]
    cols = st.columns(4)
    for col, (name, present) in zip(cols, dims):
        glyph = "✓" if present else "—"
        glyph_color = _GREEN if present else "inherit"
        glyph_opacity = "1" if present else "0.4"
        col.markdown(
            f"<div style='text-align:center'>"
            f"<div style='font-family:DM Mono,monospace;font-size:12px;"
            f"color:inherit;opacity:0.75'>{name}</div>"
            f"<div style='color:{glyph_color};opacity:{glyph_opacity};"
            f"font-size:24px;font-weight:700;line-height:1.4'>{glyph}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    if moat.get("evidence"):
        st.caption(f"_{moat.get('evidence')}_")

    # Pricing power — separate signal that often matters for the moat.
    if moat.get("has_pricing_power") or moat.get("pricing_power_evidence"):
        with st.expander("💪 Pricing power evidence", expanded=False):
            if moat.get("has_pricing_power"):
                st.markdown(f"<span style='color:{_GREEN};font-weight:600'>✓ Pricing power confirmed</span>",
                            unsafe_allow_html=True)
            if moat.get("pricing_power_evidence"):
                st.markdown(moat["pricing_power_evidence"])


# ── Value chain block ─────────────────────────────────────────────────────

def _value_chain_block(vc: Dict[str, Any]) -> None:
    if not vc:
        return
    st.markdown("##### Value Chain (Porter)")
    rows = []
    if vc.get("strategic_leverage"):
        rows.append({"Field": "Strategic leverage", "Value": f"{vc.get('strategic_leverage')}/10"})
    if vc.get("power_ratio") is not None:
        rows.append({"Field": "Power ratio", "Value": f"{vc.get('power_ratio')}"})
    if vc.get("substitution_risk_score") is not None:
        rows.append({"Field": "Substitution risk", "Value": f"{vc.get('substitution_risk_score')}/10"})
    if vc.get("upstream_leak") is not None:
        rows.append({"Field": "Upstream leak",
                     "Value": "YES ⚠" if vc.get("upstream_leak") else "NO ✓"})
    if vc.get("pass_through_capability") is not None:
        rows.append({"Field": "Pass-through pricing",
                     "Value": "YES ✓" if vc.get("pass_through_capability") else "NO"})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if vc.get("analysis_summary"):
        st.caption(f"_{vc.get('analysis_summary')}_")
    # Expandable detail blocks for the verbose narrative fields.
    if vc.get("bottleneck_analysis"):
        with st.expander("🔧 Bottleneck analysis", expanded=False):
            st.markdown(vc["bottleneck_analysis"])
    if vc.get("top_substitutes"):
        with st.expander("🔄 Top substitutes", expanded=False):
            st.markdown(vc["top_substitutes"])
    if vc.get("pricing_power_assessment"):
        with st.expander("💰 Pricing power assessment", expanded=False):
            st.markdown(vc["pricing_power_assessment"])


# ── Strategic context block ───────────────────────────────────────────────

def _strategic_context_block(sc: Dict[str, Any]) -> None:
    if not sc:
        return
    st.markdown("##### Strategic Context")
    rev_at_risk = sc.get("revenue_at_risk_percent")
    rows = [
        {"Field": "Revenue at risk", "Value": f"{rev_at_risk*100:.1f}%" if rev_at_risk is not None else "—"},
        {"Field": "Quality risk",    "Value": "YES ⚠" if sc.get("quality_of_growth_risk") else "NO ✓"},
        {"Field": "Terminal haircut", "Value": "YES" if sc.get("terminal_haircut") else "NO"},
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if sc.get("summary"):
        st.caption(f"_{sc.get('summary')}_")
    # Verbose narrative fields go into expanders.
    if sc.get("deferred_revenue_trend") and len(str(sc.get("deferred_revenue_trend"))) > 20:
        with st.expander("📈 Deferred-revenue trend", expanded=False):
            st.markdown(sc["deferred_revenue_trend"])
    if sc.get("intangible_risk_assessment"):
        with st.expander("🔐 Intangible / patent risk", expanded=False):
            st.markdown(sc["intangible_risk_assessment"])


# ── Reverse DCF chart ─────────────────────────────────────────────────────

def _reverse_dcf_chart(rdcf: Dict[str, Any]) -> None:
    if not rdcf:
        return
    st.markdown("##### Reverse DCF — growth priced in")
    impl = rdcf.get("implied_cagr_10y") or 0
    hist = rdcf.get("historical_cagr") or 0

    fig = go.Figure()
    fig.add_bar(
        x=["Historical CAGR", "Market-implied CAGR"],
        y=[hist, impl],
        marker_color=["#3b82f6", _AMBER],
        text=[f"{hist*100:.1f}%", f"{impl*100:.1f}%"],
        textposition="outside",
        # textfont color is set per-mode by Streamlit's parent container; use
        # the same muted text token so it's legible in both themes.
        textfont=dict(size=13, color=_MUTED_TEXT),
    )
    fig.update_layout(
        height=210,
        margin=dict(l=0, r=0, t=20, b=0),
        showlegend=False,
        xaxis=dict(showgrid=False, color=_MUTED_TEXT,
                   tickfont=dict(family="DM Mono", size=13)),
        yaxis=dict(showgrid=False, zeroline=False, visible=False,
                   range=[0, max(hist, impl) * 1.5] if max(hist, impl) > 0 else [0, 1]),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)

    if hist and hist > 0:
        ratio = impl / hist
        ratio_color = _RED if ratio > 2 else _AMBER if ratio > 1.3 else _GREEN
        sig_text = rdcf.get("signal", "")
        # Body text uses `inherit` so it reads cleanly on either theme; only
        # the colored numbers override.
        st.markdown(
            f"<div style='font-size:13px;color:inherit;line-height:1.6'>"
            f"Market implies <strong style='color:{_AMBER}'>{impl*100:.1f}%</strong> CAGR vs "
            f"historical <strong style='color:#3b82f6'>{hist*100:.1f}%</strong> "
            f"→ <strong style='color:{ratio_color}'>{ratio:.1f}× historical</strong>"
            + (f" · <em style='color:{_MUTED_TEXT}'>{sig_text}</em>" if sig_text else "")
            + "</div>",
            unsafe_allow_html=True,
        )


# ── Fundamentals row ──────────────────────────────────────────────────────

def _fundamentals_row(ticker: str, fund: Dict[str, Any]) -> None:
    if not fund:
        return
    st.markdown("##### Fundamentals — current FY")
    cols = st.columns(4)
    cells = [
        ("Revenue",     "Revenue",  _bn(fund.get("revenue_bn")) if fund.get("revenue_bn") else "—"),
        ("EBITDA",      "EBITDA",   _bn(fund.get("ebitda_bn"))  if fund.get("ebitda_bn") else "—"),
        ("FCF",         "FCF",      _bn(fund.get("fcf_bn"))     if fund.get("fcf_bn") else "—"),
        ("FCF Margin",  None,       _pct(fund.get("fcf_margin")) if fund.get("fcf_margin") else "—"),
    ]
    for col, (label, badge_key, value) in zip(cols, cells):
        dot = _dot(ticker, badge_key) if badge_key else ""
        full_label = f"{dot} {label}".strip()
        col.metric(full_label, value)


# ── DCF adjustments ───────────────────────────────────────────────────────

def _adjustments_block(adj: Dict[str, Any]) -> None:
    if not adj:
        return
    st.markdown("##### DCF overrides & adjustments")
    rows = []
    for k, v in adj.items():
        if k == "rules" or v is None:
            continue
        if isinstance(v, float) and abs(v) < 1:
            disp = f"{v:.4f}"
        else:
            disp = str(v)
        rows.append({"Field": k, "Value": disp})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# ── Narrative blocks ──────────────────────────────────────────────────────

def _thesis_narrative(narrative: str) -> None:
    if not narrative:
        return
    st.markdown("##### Lead agent thesis")
    # Use `inherit` so the text picks up Streamlit's foreground color in
    # whichever theme is active. Only the left border carries semantic color.
    st.markdown(
        f"""
<div style='background:{_PANEL_BG_AMBER};border-left:4px solid {_AMBER};
            padding:16px 18px;border-radius:0 6px 6px 0;color:inherit;
            font-size:14px;line-height:1.7'>
{narrative}
</div>
        """,
        unsafe_allow_html=True,
    )


def _contrarian_block(ca: Dict[str, Any]) -> None:
    if not ca:
        return
    st.markdown("##### Contrarian bear case")
    bias = ca.get("bias_detected", "None")
    bear = ca.get("bear_case_summary", "")
    sentiment = ca.get("sentiment_score")
    sentiment_str = ""
    if isinstance(sentiment, (int, float)):
        s_color = _RED if sentiment < -3 else _AMBER if sentiment < 3 else _GREEN
        sentiment_str = (f" · <span style='color:{s_color};font-weight:600'>"
                         f"sentiment {sentiment:+d}</span>")

    # Same pattern: panel-tint background + accent border, body text inherits
    # the active theme's foreground.
    st.markdown(
        f"""
<div style='background:{_PANEL_BG_RED};border-left:4px solid {_RED};
            padding:16px 18px;border-radius:0 6px 6px 0;color:inherit;
            font-size:14px;line-height:1.7'>
  <div style='margin-bottom:8px'>
    <strong style='color:{_RED}'>Bias detected:</strong> {bias}{sentiment_str}
  </div>
  <div>{bear}</div>
</div>
        """,
        unsafe_allow_html=True,
    )

    # Quant challenge — the formal adversarial reverse-DCF reasoning.
    if ca.get("quant_challenge"):
        with st.expander("🧮 Quantitative adversarial challenge", expanded=False):
            st.markdown(ca["quant_challenge"])


# ── Business Snapshot ─────────────────────────────────────────────────────

def _business_snapshot(bm: Dict[str, Any]) -> None:
    """Plain-language snapshot from the forensic/context business_model section.

    Surfaces description, revenue segments, customers, competitive landscape,
    regulatory risk, and operating-leverage score — all of which live in the
    agent output but were previously invisible to the analyst."""
    if not bm:
        return
    st.markdown("##### Business snapshot")

    desc = bm.get("business_description")
    if desc:
        st.markdown(
            f"""
<div style='background:rgba(120,120,128,0.05);padding:14px 16px;
            border-radius:6px;color:inherit;font-size:14px;line-height:1.6;
            margin-bottom:12px'>
{desc}
</div>
            """,
            unsafe_allow_html=True,
        )

    # KPI strip: operating leverage, segment count, customer count
    cols = st.columns(3)
    op_lev = bm.get("operating_leverage_score")
    cols[0].metric(
        "Operating leverage",
        f"{op_lev:.1f}/10" if isinstance(op_lev, (int, float)) else "—",
    )
    segs = bm.get("revenue_segments") or []
    cols[1].metric("Revenue segments", str(len(segs)) if segs else "—")
    custs = bm.get("key_customers") or []
    cols[2].metric("Disclosed key customers", str(len(custs)) if custs else "—")

    # Revenue segments table
    if segs:
        st.markdown("**Revenue segments**")
        seg_df = pd.DataFrame([{
            "Segment":      s.get("segment", "—"),
            "% of revenue": s.get("pct_revenue"),
            "Trend":        (s.get("growth_trend") or "—").upper(),
        } for s in segs])
        st.dataframe(
            seg_df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "% of revenue": st.column_config.ProgressColumn(
                    "% of revenue", format="%.1f%%", min_value=0.0, max_value=100.0,
                ),
            },
        )

    # Key customers as bullets
    if custs:
        with st.expander("🤝 Key customers", expanded=False):
            for c in custs:
                st.markdown(f"- {c}")

    # Competitive landscape, cost structure, regulatory risk in expanders
    if bm.get("competitive_landscape"):
        with st.expander("⚔️ Competitive landscape", expanded=False):
            st.markdown(bm["competitive_landscape"])
    if bm.get("cost_structure"):
        with st.expander("💸 Cost structure", expanded=False):
            st.markdown(bm["cost_structure"])
    if bm.get("regulatory_risk"):
        with st.expander("⚖️ Regulatory risk", expanded=False):
            st.markdown(bm["regulatory_risk"])


# ── Industry / cyclicality banner ─────────────────────────────────────────

def _cyclicality_banner(industry: Dict[str, Any]) -> None:
    """If the issuer is at a cyclical peak, surface that as a top-line warning.
    Z-score >2 means current cycle position is two standard deviations above
    long-run mean (NVDA's 3.22 z-score is a textbook peak signal)."""
    if not industry:
        return
    z = industry.get("cyclicality_z_score")
    is_peak = industry.get("is_peak")
    if z is None and not is_peak:
        return

    if is_peak:
        bg, border, label = _PANEL_BG_RED, _RED, "⚠ AT CYCLICAL PEAK"
    elif z is not None and abs(z) > 1:
        bg, border, label = _PANEL_BG_AMBER, _AMBER, "Cyclical position elevated"
    else:
        return  # not material

    z_str = f"z = {z:+.2f}σ from long-run mean" if z is not None else ""
    st.markdown(
        f"""
<div style='background:{bg};border-left:4px solid {border};
            padding:10px 14px;border-radius:0 4px 4px 0;color:inherit;
            font-size:13px;margin:8px 0'>
  <strong style='color:{border}'>{label}</strong>
  {f"&nbsp;·&nbsp;<span style='opacity:0.8'>{z_str}</span>" if z_str else ""}
</div>
        """,
        unsafe_allow_html=True,
    )


# ── Multiple decomposition ────────────────────────────────────────────────

def _multiple_decomposition(md: Dict[str, Any]) -> None:
    if not md:
        return
    st.markdown("##### Multiple decomposition")
    market = md.get("market_ev_ebitda")
    just = md.get("justified_ev_ebitda")
    premium = md.get("premium_pct")
    spread = md.get("roic_wacc_spread")
    creation = (md.get("value_creation") or "").upper()

    cols = st.columns(4)
    cols[0].metric("Market EV/EBITDA", f"{market:.1f}x" if market else "—")
    cols[1].metric("Justified (Liberti)", f"{just:.1f}x" if just else "—")
    if premium is not None:
        cols[2].metric("Premium",
                       f"{premium*100:+.1f}%" if abs(premium) < 1 else f"{premium:+.1f}×",
                       delta=creation if creation else None,
                       delta_color="off")
    else:
        cols[2].metric("Premium", "—")
    if spread is not None:
        cols[3].metric("ROIC − WACC", f"{spread*100:+.1f}%")
    else:
        cols[3].metric("ROIC − WACC", "—")


# ── Capital structure & risk ──────────────────────────────────────────────

def _capital_risk_section(section3: Dict[str, Any]) -> None:
    """Render section 3_capital_structure_risk — liquidity, leverage,
    downside floors, and concentration risk. Previously absent from Deep
    Dive even though the agents populate it richly."""
    if not section3:
        return

    st.markdown("##### Capital structure & risk")

    rf = section3.get("risk_factors") or {}
    liq = rf.get("liquidity") or {}
    down = rf.get("downside") or {}
    lev = rf.get("leverage") or {}

    # KPI strip
    cols = st.columns(4)
    # Liquidity ratio
    lq = liq.get("liquidity_ratio")
    if lq is not None:
        cols[0].metric(
            "Liquidity ratio",
            f"{lq:.2f}",
            delta=f"refi risk {liq.get('refinancing_risk_score', 0)}/10",
            delta_color="off",
        )
    else:
        cols[0].metric("Liquidity ratio", "—")
    # Tangible book
    tb = down.get("tangible_book_value")
    cols[1].metric("Tangible book", _bn(tb / 1e9) if tb else "—")
    # EPV / floor
    epv = down.get("earnings_power_value")
    cols[2].metric("Earnings Power Value", _bn(epv / 1e9) if epv else "—")
    # Operating leverage
    opl = lev.get("operating_leverage_score")
    cols[3].metric("Operating leverage", f"{opl:.1f}/10" if opl else "—")

    # Liquidity table
    if liq:
        with st.expander("💧 Liquidity detail", expanded=False):
            rows = [
                {"Field": "Cash", "Value": _bn((liq.get("cash") or 0) / 1e9)},
                {"Field": "Maturities next 2y",
                 "Value": _bn((liq.get("maturities_next_2y") or 0) / 1e9)},
                {"Field": "Liquidity ratio (cash / maturities)",
                 "Value": f"{liq.get('liquidity_ratio'):.3f}" if liq.get("liquidity_ratio") is not None else "—"},
                {"Field": "Refinancing risk",
                 "Value": f"{liq.get('refinancing_risk_score', 0)}/10"},
                {"Field": "Liquidity alert",
                 "Value": "YES ⚠" if liq.get("liquidity_alert") else "NO ✓"},
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # Downside detail
    if down:
        with st.expander("📉 Downside / floor analysis", expanded=False):
            rows = [
                {"Field": "Tangible book value",
                 "Value": _bn((down.get("tangible_book_value") or 0) / 1e9)},
                {"Field": "Crash FCF",
                 "Value": _bn((down.get("crash_fcf") or 0) / 1e9)},
                {"Field": "Earnings Power Value (EPV)",
                 "Value": _bn((down.get("earnings_power_value") or 0) / 1e9)},
                {"Field": "Floor value",
                 "Value": _bn((down.get("floor_value") or 0) / 1e9)},
                {"Field": "Floor price per share",
                 "Value": (f"${down.get('floor_price_per_share'):,.2f}"
                           if down.get("floor_price_per_share") else "—")},
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # Leverage detail
    if lev:
        with st.expander("⚖️ Leverage detail", expanded=False):
            rows = [
                {"Field": "Operating leverage score",
                 "Value": f"{lev.get('operating_leverage_score'):.2f}/10" if lev.get("operating_leverage_score") else "—"},
                {"Field": "Financial leverage score",
                 "Value": f"{lev.get('financial_leverage_score'):.4f}" if lev.get("financial_leverage_score") is not None else "—"},
                {"Field": "Double-leverage flag",
                 "Value": "YES ⚠" if lev.get("double_leverage_flag") else "NO ✓"},
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # Concentration risk — surface as a callout when True (NVDA / TSMC dependency case)
    if section3.get("concentration_risk"):
        details = section3.get("concentration_details") or "Concentration risk flagged."
        st.markdown(
            f"""
<div style='background:{_PANEL_BG_AMBER};border-left:4px solid {_AMBER};
            padding:14px 16px;border-radius:0 6px 6px 0;color:inherit;
            font-size:13px;line-height:1.6;margin-top:8px'>
  <strong style='color:{_AMBER}'>⚠ Concentration risk flagged</strong><br>
  <div style='margin-top:6px'>{details}</div>
</div>
            """,
            unsafe_allow_html=True,
        )


# ── Constitution checks ──────────────────────────────────────────────────

def _constitution_checks(checks: list) -> None:
    if not checks:
        return
    n_pass = sum(1 for c in checks if "PASS" in str(c) or "✅" in str(c))
    n_fail = sum(1 for c in checks if "FAIL" in str(c) or "❌" in str(c))
    n_warn = len(checks) - n_pass - n_fail

    label = (f"{'✅' if n_fail == 0 else '⚠' if n_fail < 2 else '❌'} "
             f"Constitution checks  ·  {n_pass} pass · {n_warn} warn · {n_fail} fail")
    with st.expander(label, expanded=(n_fail > 0)):
        for c in checks:
            st.markdown(f"- {c}")


# ── Reverse-DCF reasons ──────────────────────────────────────────────────

def _rdcf_reasons(rdcf: Dict[str, Any]) -> None:
    reasons = rdcf.get("reasons") or []
    if not reasons:
        return
    with st.expander("📋 Reverse-DCF reasoning", expanded=False):
        for r in reasons:
            st.markdown(f"- {r}")


# ── Main render ───────────────────────────────────────────────────────────

def render_deep_dive_view(
    ticker: str,
    dcf: Dict[str, Any],
    fund: Dict[str, Any],
    full_report: Dict[str, Any],
    universe_row: Dict[str, Any],
) -> None:
    if not ticker:
        st.info("Select a ticker from the sidebar to begin analysis.")
        return
    if not dcf:
        st.info(f"No DCF data available for {ticker}.")
        return

    er  = (full_report or {}).get("1_economic_reality", {}) or {}
    val = (full_report or {}).get("4_valuation_synthesis", {}) or {}
    section3 = (full_report or {}).get("3_capital_structure_risk", {}) or {}

    investment_thesis  = val.get("investment_thesis") or {}
    pillar_scores      = investment_thesis.get("pillar_scores") or {}
    contrarian         = val.get("contrarian_analysis") or {}
    p2v                = val.get("phase2_valuation") or {}
    adj                = p2v.get("dcf_adjustments") or {}
    md                 = p2v.get("multiple_decomposition") or {}
    rdcf               = (dcf or {}).get("reverse_dcf") or {}

    moat        = er.get("moat") or {}
    vc          = er.get("value_chain") or {}
    sc          = er.get("strategic_context") or {}
    bm          = er.get("business_model") or {}
    industry    = er.get("industry_structure") or {}

    # ── Title + DCF-method badge + cyclicality alert ────────────────────
    st.markdown(f"## {ticker}")
    _dcf_method_badge(ticker)
    _cyclicality_banner(industry)

    # ── Hero strip ────────────────────────────────────────────────────────
    _hero_strip(ticker, investment_thesis, dcf, fund, universe_row)

    # ── Pillar scorecard ──────────────────────────────────────────────────
    if pillar_scores:
        st.markdown("---")
        _pillar_section(pillar_scores)

    # ── Business snapshot (new) ──────────────────────────────────────────
    if bm:
        st.markdown("---")
        _business_snapshot(bm)

    # ── Three-scenario DCF + multiple decomposition ───────────────────────
    st.markdown("---")
    st.markdown("##### Three-scenario DCF — intrinsic value per share")
    _scenario_triangle(dcf)
    if md:
        st.markdown("<br>", unsafe_allow_html=True)
        _multiple_decomposition(md)

    # ── Two-column body ──────────────────────────────────────────────────
    st.markdown("---")
    left, right = st.columns([1, 1.4])

    with left:
        _moat_block(moat, universe_row)
        st.markdown("<br>", unsafe_allow_html=True)
        _value_chain_block(vc)
        st.markdown("<br>", unsafe_allow_html=True)
        _strategic_context_block(sc)

    with right:
        _fundamentals_row(ticker, fund)
        st.markdown("<br>", unsafe_allow_html=True)
        _reverse_dcf_chart(rdcf)
        _rdcf_reasons(rdcf)
        st.markdown("<br>", unsafe_allow_html=True)
        _adjustments_block(adj)
        st.markdown("<br>", unsafe_allow_html=True)
        _thesis_narrative(investment_thesis.get("narrative") or "")
        st.markdown("<br>", unsafe_allow_html=True)
        _contrarian_block(contrarian)

    # ── Capital structure & risk (new) ────────────────────────────────────
    if section3:
        st.markdown("---")
        _capital_risk_section(section3)

    # ── Constitution checks (compact) ─────────────────────────────────────
    checks = investment_thesis.get("constitution_checks") or []
    if checks:
        st.markdown("---")
        _constitution_checks(checks)

    # ── Validation legend ─────────────────────────────────────────────────
    st.markdown("---")
    st.caption(
        "🟢 validated against SEC/FMP within 1% · "
        "🟡 within 5% (documented difference) · "
        "🔴 >5% drift · "
        "⚪ field not present on validator side · "
        "· not yet validated"
    )
