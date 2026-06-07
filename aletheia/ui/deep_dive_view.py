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


# ── Inline subsection label (replaces former st.expander pattern) ────────
# Per UX feedback: collapsible expanders forced analysts to click to see
# content already on the page. Replaced with always-visible labeled
# subsections — same hierarchy, no click required.

def _inline_label(label: str, color: Optional[str] = None) -> None:
    c = color or _MUTED_TEXT
    st.markdown(
        f"<div style='font-family:DM Mono,monospace;font-size:11px;"
        f"color:{c};text-transform:uppercase;letter-spacing:0.6px;"
        f"font-weight:600;margin:14px 0 6px 0;'>{label}</div>",
        unsafe_allow_html=True,
    )


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


# Phase A.4 — engine provenance pill on Deep Dive
_ENGINE_LABEL = {
    "fcff":           "📐 FCFF DCF",
    "rate_base":      "📐 Rate-base DCF (regulated utility)",
    "ddm":            "📐 Dividend Discount Model",
    "embedded_value": "📐 Embedded Value (sum-of-parts)",
}

_ENGINE_DESCRIPTION = {
    "fcff": (
        "Standard corporate FCFF discounted cash flow. Three "
        "scenarios (bull / base / bear); WACC-discounted projection "
        "+ terminal value."
    ),
    "rate_base": (
        "Regulated-utility valuation: equity value = Σ (rate base × "
        "allowed ROE) discounted at cost of equity, plus terminal "
        "perpetuity. Allowed ROE + rate-base growth from the latest "
        "rate-case order."
    ),
    "ddm": (
        "Dividend Discount Model for float-based / bank filers where "
        "FCFF doesn't apply."
    ),
    "embedded_value": (
        "Embedded-value framework for insurance conglomerates."
    ),
}


def _engine_banner(p2v: Dict[str, Any]) -> None:
    """Single-line banner identifying which valuation engine produced
    the numbers on this page. Ahead of the hero strip so analysts can
    contextualize the IV/MoS before reading them.

    For unknown / missing engine values (legacy report rows from
    before Phase A.4), renders nothing — the hero strip is still
    the primary signal."""
    engine = p2v.get("engine")
    if not engine:
        return
    label = _ENGINE_LABEL.get(engine, f"📐 {engine}")
    desc = _ENGINE_DESCRIPTION.get(engine, "")
    st.markdown(
        f"<div style='padding:8px 0;'>"
        f"<span style='font-weight:600; font-size:14px;'>"
        f"Valuation engine: {label}</span>"
        f"<br><span style='font-size:12px; color:#888;'>{desc}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


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
            # `dict.get(k, default)` returns the default only when the key
            # is MISSING — not when the value is None. JNJ's universe_row
            # has value_creation explicitly set to None; without the
            # `or ""` coercion below, `None.upper()` crashed the entire
            # Deep Dive render.
            vc_label = (universe_row.get("value_creation") or "").upper() or None
            st.metric("Conviction", f"{int(conv):+d} / 10",
                      delta=vc_label, delta_color="off")
        else:
            st.metric("Conviction", "—")
    # Base IV / MoS
    with cols[1]:
        st.metric(f"{_dot(ticker, 'Base IV')} Base IV",
                  _money(base_iv) if base_iv else "—",
                  delta=(_signed_pct(base_mos) if base_mos is not None else None))
    # ROIC – WACC spread
    with cols[2]:
        from aletheia.ui.validation_badge import convention_flag
        _flag = convention_flag("ROIC")
        st.metric(f"{_dot(ticker, 'ROIC')} ROIC{_flag} − WACC spread",
                  _signed_pct(spread) if spread is not None else "—",
                  delta=(f"ROIC {_pct(roic)} · WACC {_pct(wacc)}"
                         if (roic is not None and wacc is not None) else None),
                  delta_color="off")
    # Multiple signal
    # Full signal taxonomy — mirrors streamlit_app.py:SIGNAL_LABEL so every
    # value the reverse_dcf module emits ("speculative_premium",
    # "priced_for_growth", "deep_value", etc.) renders with a human label.
    # Fallback: upper-case the raw signal so unmapped values still display.
    _SIG_LABELS = {
        "undervalued":         "UNDERVALUED",
        "fairly_valued":       "FAIR VALUE",
        "fair_value":          "FAIR VALUE",
        "deep_value":          "DEEP VALUE",
        "priced_for_growth":   "GROWTH PRICED",
        "speculative_premium": "SPECULATIVE",
        "moderate_premium":    "MODERATE PREMIUM",
        "premium":             "PREMIUM",
        "high_premium":        "HIGH PREMIUM",
        "caution":             "CAUTION",
        "flag":                "FLAG",
        "high_quality":        "HIGH QUALITY",
        "neutral":             "NEUTRAL",
    }
    with cols[3]:
        sig_label = _SIG_LABELS.get(sig) or (sig.upper() if sig else "—")
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


def _pillar_section(pillar_scores: Dict[str, Any], cs_severity: str = "NONE") -> None:
    if not pillar_scores:
        return
    capped = pillar_scores.get("capped_total")
    tier = (pillar_scores.get("position_tier") or "—").upper()
    # Current-State gate: a HIGH flag tags the tier 'FLAGS PENDING' so a clean
    # CONVICTION can't be read off the scorecard without reconciliation.
    tier_tag = ""
    if cs_severity == "HIGH":
        tier_tag = (" · <span style='color:#dc2626;font-weight:700'>"
                    "⛔ FLAGS PENDING</span>")
    elif cs_severity == "MEDIUM":
        tier_tag = (" · <span style='color:#d97706;font-weight:700'>"
                    "⚠ flags</span>")

    st.markdown("##### Pillar scores  ·  "
                f"<span style='color:inherit;opacity:0.7;"
                f"font-family:DM Mono,monospace;font-size:13px;font-weight:400'>"
                f"total {capped}/25 · tier {tier}{tier_tag}</span>",
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
            reason_items = "".join(
                f'<li style="margin-bottom:4px;">{r}</li>'
                for r in (reasons[:3] if isinstance(reasons, list) else [])
            )
            st.markdown(
                f'<ul style="margin:6px 0 14px 178px;padding-left:18px;'
                f'color:{_MUTED_TEXT};font-size:12px;line-height:1.6;">'
                f'{reason_items}</ul>',
                unsafe_allow_html=True,
            )


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
        _inline_label("Pricing power evidence")
        if moat.get("has_pricing_power"):
            st.markdown(
                f"<span style='color:{_GREEN};font-weight:600'>✓ Pricing power confirmed</span>",
                unsafe_allow_html=True,
            )
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
    # Verbose narrative fields rendered inline (no click-to-expand).
    if vc.get("bottleneck_analysis"):
        _inline_label("Bottleneck analysis")
        st.markdown(vc["bottleneck_analysis"])
    if vc.get("top_substitutes"):
        _inline_label("Top substitutes")
        st.markdown(vc["top_substitutes"])
    if vc.get("pricing_power_assessment"):
        _inline_label("Pricing power assessment")
        st.markdown(vc["pricing_power_assessment"])


# ── Strategic context block ───────────────────────────────────────────────

def _strategic_context_block(sc: Dict[str, Any]) -> None:
    if not sc:
        return
    st.markdown("##### Strategic Context")
    rev_at_risk = sc.get("revenue_at_risk_percent")
    # Use descriptive labels for the boolean risk flags instead of
    # "YES/NO + glyph" which reads contradictorily ("✓ NO" looks like
    # an affirmative when it actually means "no risk found").
    rows = [
        {"Field": "Revenue at risk", "Value": f"{rev_at_risk*100:.1f}%" if rev_at_risk is not None else "—"},
        {"Field": "Quality of growth",
         "Value": "⚠ Concern flagged" if sc.get("quality_of_growth_risk") else "✓ Clean"},
        {"Field": "Terminal haircut",
         "Value": "⚠ Applied" if sc.get("terminal_haircut") else "✓ Not applied"},
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if sc.get("summary"):
        st.caption(f"_{sc.get('summary')}_")
    # Verbose narrative fields rendered inline.
    if sc.get("deferred_revenue_trend") and len(str(sc.get("deferred_revenue_trend"))) > 20:
        _inline_label("Deferred-revenue trend")
        st.markdown(sc["deferred_revenue_trend"])
    if sc.get("intangible_risk_assessment"):
        _inline_label("Intangible / patent risk")
        st.markdown(sc["intangible_risk_assessment"])


# ── Reverse DCF chart ─────────────────────────────────────────────────────

# Direction of each valuation signal: +1 = cheap/bullish, -1 = expensive/
# bearish, 0 = neutral. Two unmapped/None signals → no verdict.
_REV_DCF_DIR = {
    "deep_value": 1, "fair_value": 0, "priced_for_growth": -1,
    "caution": -1, "flag": -1,
}
_MULT_DIR = {
    "undervalued": 1, "discount": 1, "deep_value": 1,
    "fair_value": 0, "fairly_valued": 0, "justified": 0,
    "priced_for_growth": -1, "premium": -1, "speculative_premium": -1,
    "overvalued": -1,
}


def _signal_reconciliation(rdcf: Dict[str, Any], md: Dict[str, Any]) -> None:
    """Surface agreement/conflict between the two primary valuation signals.

    Reverse-DCF reads the *top-line growth* the price embeds; multiple-
    decomposition reads the *EV/EBITDA* vs its justified level. They can
    genuinely point opposite ways (e.g. CHWY: reverse-DCF 'deep value' on
    revenue while the multiple screens 'speculative premium') — the framework
    had no layer that said so out loud. This makes the conflict explicit so
    an analyst never reads one signal in isolation."""
    rsig = (rdcf.get("signal") or "").strip().lower()
    msig = (md.get("signal") or "").strip().lower()
    rdir = _REV_DCF_DIR.get(rsig)
    mdir = _MULT_DIR.get(msig)
    if rdir is None or mdir is None:
        return

    def _lbl(s):
        return s.replace("_", " ").upper()

    if rdir * mdir < 0:
        st.warning(
            f"⚠ **Valuation signals diverge.** Reverse-DCF reads "
            f"**{_lbl(rsig)}** (on the *revenue growth* the price implies) "
            f"while multiple-decomposition reads **{_lbl(msig)}** (on "
            f"*EV/EBITDA* vs justified). Both can be right at once — it means "
            f"the market is paying a rich cash-flow multiple while pricing in "
            f"little top-line growth, so the thesis hinges on **margin / FCF "
            f"conversion**, not revenue. Don't read either signal alone."
        )
    elif rdir == mdir and rdir != 0:
        side = "cheap" if rdir > 0 else "expensive"
        st.caption(
            f"✓ Valuation signals agree ({side}): reverse-DCF "
            f"**{_lbl(rsig)}** and multiple **{_lbl(msig)}** point the same way."
        )


def _reverse_dcf_chart(rdcf: Dict[str, Any]) -> None:
    if not rdcf:
        return
    st.markdown("##### Reverse DCF — growth priced in")
    # Surface the data vintage. Reverse-DCF currently anchors to the
    # latest FY-end snapshot (not TTM) because TTM rows don't yet
    # populate clean_NormalizedEBIT / NOPAT / tax_rate — without that
    # the implied-CAGR solver silently produces garbage (MDT case
    # study). Label removes when TTM-aware normalization ships.
    based_on = rdcf.get("based_on_period", "FY")
    rdcf_fy  = rdcf.get("fiscal_year")
    if based_on == "FY" and rdcf_fy:
        st.caption(
            f"Based on FY{rdcf_fy} data — last audited 10-K. "
            "TTM-based reverse-DCF is gated on normalized-EBIT support "
            "(scheduled, see _process_one). Growth signal will lag fresh "
            "TTM filings until then."
        )
    impl = rdcf.get("implied_cagr_10y") or 0
    # The live `/dcf` endpoint emits `historical_cagr_5y` (from
    # ReverseDCFResult.to_dict), while the agent-written JSON report stores
    # it as `historical_cagr`. Read both so the chart populates regardless
    # of source.
    hist = rdcf.get("historical_cagr") or rdcf.get("historical_cagr_5y") or 0

    fig = go.Figure()
    fig.add_bar(
        x=["Historical CAGR (normalized)", "Market-implied CAGR"],
        y=[hist, impl],
        marker_color=["#3b82f6", _AMBER],
        text=[f"{hist*100:.1f}%", f"{impl*100:.1f}%"],
        textposition="outside",
        # textfont color is set per-mode by Streamlit's parent container; use
        # the same muted text token so it's legible in both themes.
        textfont=dict(size=13, color=_MUTED_TEXT),
    )
    # Y-range must include 0 AND any negative value — a market-implied CAGR can
    # be negative (price implies a *declining* business), and a [0, max] range
    # would clip that bar to nothing (it sits below the axis floor).
    _lo = min(0.0, hist, impl)
    _hi = max(0.0, hist, impl)
    _span = (_hi - _lo) or 1.0
    fig.update_layout(
        height=210,
        margin=dict(l=0, r=0, t=20, b=20),
        showlegend=False,
        xaxis=dict(showgrid=False, color=_MUTED_TEXT,
                   tickfont=dict(family="DM Mono", size=13)),
        yaxis=dict(showgrid=False, zeroline=True, zerolinecolor=_MUTED_TEXT,
                   visible=False,
                   range=[_lo - _span * 0.2, _hi + _span * 0.25]),
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
        st.caption(
            "Historical CAGR here is a **robust/normalized** anchor — the "
            "trimmed median across multiple lookback windows, which "
            "down-weights M&A and one-off hyper-growth years. It "
            "deliberately differs from the raw point-to-point 5Y CAGR shown "
            "in the ratios table (that one is undistorted-by-design and can "
            "be far higher for serial acquirers / post-IPO scalers)."
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


# ─────────────────────────────────────────────────────────────────────────────
# Structured thesis (from thesis_synthesizer agent)
# ─────────────────────────────────────────────────────────────────────────────

def _confidence_color(conf: Optional[str]) -> str:
    return {
        "high":                 _GREEN,
        "medium":               _AMBER,
        "low":                  _RED,
        "insufficient_signal":  _MUTED_TEXT,
    }.get(conf or "", _MUTED_TEXT)


def _priority_color(priority: Optional[str]) -> str:
    return {"red": _RED, "amber": _AMBER, "green": _GREEN}.get(
        priority or "", _MUTED_TEXT
    )


_OBSERVABLE_RE = __import__("re").compile(
    r"^\s*(?P<path>[A-Za-z_][\w.]*)\s*(?P<op>>=|<=|==|>|<)\s*(?P<value>-?\d+(?:\.\d+)?)\s*$"
)


def _evaluate_observable(
    observable: Optional[str], dcf: Dict[str, Any], p2v: Dict[str, Any]
) -> Optional[bool]:
    """Parse a simple ``phase2.field op value`` expression and evaluate
    against live data. Returns True when the trigger is CURRENTLY met,
    False when not met, None when the expression is free-form text or
    the field path doesn't resolve.

    Supports the canonical decision-condition format used by the
    thesis_synthesizer: ``phase2.implied_cagr > 0.0`` and similar.
    Anything more complex falls back to None so the UI doesn't lie
    about a state it can't actually compute."""
    if not observable or not isinstance(observable, str):
        return None
    m = _OBSERVABLE_RE.match(observable)
    if m is None:
        return None
    path = m.group("path")
    op = m.group("op")
    try:
        threshold = float(m.group("value"))
    except ValueError:
        return None

    # Resolve "phase2.X" against p2v dict; "dcf.X" against dcf dict.
    parts = path.split(".")
    if not parts:
        return None
    root_name, sub_path = parts[0], parts[1:]
    root = {"phase2": p2v, "dcf": dcf}.get(root_name)
    if root is None:
        return None
    cur: Any = root
    for k in sub_path:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            cur = getattr(cur, k, None)
        if cur is None:
            return None
    try:
        actual = float(cur)
    except (TypeError, ValueError):
        return None
    if op == ">":  return actual >  threshold
    if op == "<":  return actual <  threshold
    if op == ">=": return actual >= threshold
    if op == "<=": return actual <= threshold
    if op == "==": return actual == threshold
    return None


def _cited_signals_chips(signals: list) -> str:
    """Inline pills listing the upstream-signal field paths a claim cites.
    Renders compactly to keep the visual weight on the claim text."""
    if not signals:
        return ""
    chips = []
    for s in signals[:6]:   # cap at 6 to avoid overwhelm
        chips.append(
            f'<span style="font-family:DM Mono,monospace;font-size:10px;'
            f'color:{_MUTED_TEXT};background:{_BAR_BG};padding:2px 6px;'
            f'border-radius:3px;margin-right:4px;">{s}</span>'
        )
    extra = ""
    if len(signals) > 6:
        extra = (f' <span style="font-family:DM Mono,monospace;font-size:10px;'
                 f'color:{_MUTED_TEXT};">+{len(signals) - 6} more</span>')
    return (
        f'<div style="margin-top:8px;line-height:2;">'
        f'<span style="font-family:DM Mono,monospace;font-size:10px;'
        f'color:{_MUTED_TEXT};text-transform:uppercase;letter-spacing:0.5px;'
        f'margin-right:8px;">Cites:</span>'
        + "".join(chips) + extra +
        "</div>"
    )


def _case_card(label: str, color: str, claim_dict: Dict[str, Any]) -> None:
    """One bordered card for bull / bear / base case. Shows the claim text
    with the cited_signals chip row underneath."""
    if not isinstance(claim_dict, dict):
        return
    claim_text = claim_dict.get("claim", "") or ""
    signals = claim_dict.get("cited_signals") or []
    if not claim_text:
        return
    st.markdown(
        f"""
<div style='border:1px solid {_BAR_BG};border-left:4px solid {color};
            padding:16px 20px;border-radius:0 6px 6px 0;color:inherit;
            margin-bottom:12px;'>
  <div style='font-family:DM Mono,monospace;font-size:11px;
              color:{color};letter-spacing:0.7px;text-transform:uppercase;
              margin-bottom:8px;font-weight:700;'>{label}</div>
  <div style='font-size:15px;line-height:1.75;'>{claim_text}</div>
  {_cited_signals_chips(signals)}
</div>
        """,
        unsafe_allow_html=True,
    )


_ACTION_COLOR = {
    "BUY":       _GREEN,
    "ACCUMULATE": _GREEN,
    "ADD":        _GREEN,
    "HOLD":      _AMBER,
    "WATCH":     _AMBER,
    "REVIEW":    _AMBER,
    "TRIM":       _RED,
    "SELL":       _RED,
    "EXIT":       _RED,
    "PASS":       _RED,
}


def _decision_conditions_table(
    conditions: list, dcf: Dict[str, Any], p2v: Dict[str, Any]
) -> None:
    """Card-style decision-condition rows.

    Streamlit's `:color[text]` markdown only accepts named colors, so the
    earlier `st.dataframe` approach rendered raw hex markup as literal text.
    Switched to bordered cards: colored left-border encodes priority,
    action chip on the right encodes direction.

    Priority chip = analyst-assigned urgency (red/amber/green target).
    Status chip = live evaluation of the observable expression
    (● MET green / ● WATCHING amber). The two are distinct because the
    priority describes how seriously to take the condition, while the
    status describes whether it's actually firing right now.
    """
    if not conditions:
        return
    st.markdown("##### Decision conditions")
    st.caption("What triggers a position change")

    rows_html = []
    for dc in conditions:
        if not isinstance(dc, dict):
            continue
        priority = (dc.get("priority") or "").lower()
        action = (dc.get("action") or "").upper()
        trigger = dc.get("trigger", "") or "—"
        observable = dc.get("observable", "") or ""
        p_color = _priority_color(priority)
        a_color = _ACTION_COLOR.get(action, _MUTED_TEXT)

        # Live state evaluation: is the trigger met right now?
        is_met = _evaluate_observable(observable, dcf, p2v)
        if is_met is True:
            status_color = _GREEN
            status_label = "MET"
        elif is_met is False:
            status_color = _AMBER
            status_label = "WATCHING"
        else:
            status_color = None
            status_label = None

        observable_html = (
            f'<div style="font-family:DM Mono,monospace;font-size:11px;'
            f'color:{_MUTED_TEXT};margin-top:6px;line-height:1.5;">'
            f'observable · {observable}</div>'
        ) if observable else ""

        action_chip = (
            f'<span style="display:inline-block;font-family:DM Mono,monospace;'
            f'font-size:10px;font-weight:700;letter-spacing:0.6px;'
            f'color:{a_color};border:1px solid {a_color};'
            f'padding:3px 9px;border-radius:3px;white-space:nowrap;">'
            f'{action or "—"}</span>'
        )

        priority_chip = (
            f'<span style="display:inline-block;font-family:DM Mono,monospace;'
            f'font-size:10px;font-weight:600;letter-spacing:0.5px;'
            f'text-transform:uppercase;color:{p_color};margin-right:10px;">'
            f'● {priority or "—"}</span>'
        )

        status_chip = ""
        if status_label is not None:
            status_chip = (
                f'<span style="display:inline-block;font-family:DM Mono,monospace;'
                f'font-size:10px;font-weight:700;letter-spacing:0.5px;'
                f'color:{status_color};margin-right:10px;">'
                f'● {status_label}</span>'
            )

        rows_html.append(
            f'<div style="border:1px solid {_BAR_BG};border-left:4px solid {p_color};'
            f'border-radius:0 6px 6px 0;padding:14px 18px;margin-bottom:8px;'
            f'color:inherit;display:flex;align-items:flex-start;gap:14px;">'
            f'  <div style="flex:1;min-width:0;">'
            f'    <div style="margin-bottom:4px;">{priority_chip}{status_chip}</div>'
            f'    <div style="font-size:14px;line-height:1.6;">{trigger}</div>'
            f'    {observable_html}'
            f'  </div>'
            f'  <div style="flex-shrink:0;padding-top:2px;">{action_chip}</div>'
            f'</div>'
        )

    if not rows_html:
        return
    st.markdown("".join(rows_html), unsafe_allow_html=True)


def _structured_thesis_section(
    thesis: Dict[str, Any], dcf: Dict[str, Any], p2v: Dict[str, Any]
) -> None:
    """Render the thesis_synthesizer output: thesis statement, bull/bear/base
    cards with cited_signals, decision conditions, required analyst judgment,
    and update conditions.

    ``dcf`` and ``p2v`` are threaded down to the decision-conditions table
    so each row can show a live MET / WATCHING status alongside its
    priority chip. Without them the table would only encode the analyst's
    target severity, not the current state.

    Renders nothing if the section is empty (pre-thesis_synthesizer reports
    or schema-mismatched tickers where the thesis fell back to mock)."""
    if not thesis or not thesis.get("thesis_statement"):
        return

    st.markdown("##### Structured investment thesis")

    # Header strip — confidence + horizon
    confidence = thesis.get("thesis_confidence", "")
    horizon = thesis.get("time_horizon", "")
    conf_color = _confidence_color(confidence)
    st.markdown(
        f"""
<div style='font-family:DM Mono,monospace;font-size:11px;
            color:{_MUTED_TEXT};margin-bottom:12px;letter-spacing:0.3px;'>
  Confidence: <span style='color:{conf_color};font-weight:700'>{(confidence or "—").upper()}</span>
  &nbsp;·&nbsp; Horizon: <span style='color:inherit;font-weight:700'>{(horizon or "—").replace("_", " ").upper()}</span>
</div>
        """,
        unsafe_allow_html=True,
    )

    # Thesis statement (one sentence, prominent)
    st.markdown(
        f"""
<div style='border-left:4px solid {conf_color};padding:16px 22px;
            background:{_BAR_BG};border-radius:0 6px 6px 0;
            font-size:16px;line-height:1.7;color:inherit;
            margin-bottom:24px;font-weight:500;'>
{thesis.get('thesis_statement', '')}
</div>
        """,
        unsafe_allow_html=True,
    )

    # Bull / Base / Bear cards (in that order — most-positive first matches
    # the rest of the dashboard's bull→base→bear ordering)
    _case_card("BULL CASE", _GREEN, thesis.get("bull_case") or {})
    _case_card("BASE CASE", _AMBER, thesis.get("base_case") or {})
    _case_card("BEAR CASE", _RED,   thesis.get("bear_case") or {})

    # Decision conditions
    conditions = thesis.get("decision_conditions") or []
    if conditions:
        st.markdown("<div style='margin-top:24px;'></div>", unsafe_allow_html=True)
        _decision_conditions_table(conditions, dcf, p2v)

    # Required analyst judgment + update conditions in a two-column layout
    raj = thesis.get("required_analyst_judgment") or []
    upd = thesis.get("update_conditions") or []
    if raj or upd:
        st.markdown("<div style='margin-top:24px;'></div>", unsafe_allow_html=True)
        col_a, col_b = st.columns(2)
        with col_a:
            if raj:
                st.markdown("##### Required analyst judgment")
                st.caption("Gaps the framework defers to the analyst")
                for r in raj:
                    st.markdown(f"- {r}")
        with col_b:
            if upd:
                st.markdown("##### Update conditions")
                st.caption("What would invalidate this thesis")
                for u in upd:
                    st.markdown(f"- {u}")

    # Quality flag callout — only shown if the agent surfaced one
    flags = thesis.get("_quality_flags") or []
    if flags:
        st.warning(
            "Thesis quality flags: " + " · ".join(flags)
        )


def _contrarian_block(ca: Dict[str, Any], dcf: Optional[Dict[str, Any]] = None) -> None:
    if not ca:
        return
    st.markdown("##### Contrarian bear case")

    # Engine-bear vs constitutional-floor separation. The contrarian
    # narrative tends to cite a $0 "bear case" that is actually an
    # existential tail-risk scenario, not the engine's analytically
    # defensible bear. Surface both explicitly so a $0 floor is never
    # mistaken for the DCF bear. engine_bear_iv comes from the (fixed)
    # contrarian output; fall back to the live DCF bear IPS for older
    # reports that predate that field.
    engine_bear = ca.get("engine_bear_iv")
    # Treat 0/missing as "use the live DCF bear" — older reports stored
    # engine_bear_iv=0 from the wrong-path bug; the live /dcf bear is correct.
    if not engine_bear and dcf:
        engine_bear = (dcf.get("bear") or {}).get("intrinsic_per_share")
    tail_risk = ca.get("tail_risk_present")
    if engine_bear is not None or tail_risk:
        eb_str = _money(engine_bear) if engine_bear else "—"
        floor_html = (
            f"<span style='color:{_MUTED_TEXT}'> · </span>"
            f"<strong>Constitutional floor:</strong> $0.00 "
            f"<span style='color:{_MUTED_TEXT}'>(existential tail-risk, "
            f"not the DCF bear)</span>"
        ) if tail_risk else ""
        st.markdown(
            f"<div style='font-family:DM Mono,monospace;font-size:12px;"
            f"margin-bottom:8px;color:inherit'>"
            f"<strong>Engine bear:</strong> {eb_str} "
            f"<span style='color:{_MUTED_TEXT}'>(DCF, bear assumptions)</span>"
            f"{floor_html}</div>",
            unsafe_allow_html=True,
        )

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
        _inline_label("Quantitative adversarial challenge")
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
        _inline_label("Key customers")
        for c in custs:
            st.markdown(f"- {c}")

    # Competitive landscape, cost structure, regulatory risk inline.
    if bm.get("competitive_landscape"):
        _inline_label("Competitive landscape")
        st.markdown(bm["competitive_landscape"])
    if bm.get("cost_structure"):
        _inline_label("Cost structure")
        st.markdown(bm["cost_structure"])
    if bm.get("regulatory_risk"):
        _inline_label("Regulatory risk")
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
    st.caption(
        "Market EV/EBITDA computed against current price (see Analysis "
        "price banner above). FMP's screening ratios use FY-end price; "
        "drift between sources is price-timing, not methodology."
    )
    market = md.get("market_ev_ebitda")
    just = md.get("justified_ev_ebitda")
    premium = md.get("premium_pct")
    spread = md.get("roic_wacc_spread")
    creation = (md.get("value_creation") or "").upper()

    cols = st.columns(4)
    cols[0].metric("Market EV/EBITDA", f"{market:.1f}x" if market else "—",
                   help="Computed against current price.")
    cols[1].metric("Justified (Liberti)", f"{just:.1f}x" if just else "—",
                   help="Mathematically justified multiple at the same WACC + ROIC.")
    if premium is not None:
        cols[2].metric("Premium",
                       f"{premium*100:+.1f}%" if abs(premium) < 1 else f"{premium:+.1f}×")
    else:
        cols[2].metric("Premium", "—")
    if spread is not None:
        cols[3].metric("ROIC − WACC", f"{spread*100:+.1f}%",
                       delta=creation if creation else None,
                       delta_color="off")
    else:
        cols[3].metric("ROIC − WACC", "—")


# ── Reality Checks (Feature 1: GDP comparison) ───────────────────────────

# Severity → visual treatment. Reuses the same panel-tint/border tokens
# as the cyclicality alert and contrarian block so visual semantics stay
# consistent across the page.
_SEVERITY_STYLE = {
    "critical": {"icon": "🔴", "color": _RED,   "bg": _PANEL_BG_RED},
    "warning":  {"icon": "🟠", "color": _AMBER, "bg": _PANEL_BG_AMBER},
    "caution":  {"icon": "🟡", "color": _AMBER, "bg": _PANEL_BG_AMBER},
    "info":     {"icon": "🟢", "color": _GREEN, "bg": "rgba(16,185,129,0.04)"},
    "n_a":      {"icon": "⚪", "color": _MUTED_TEXT, "bg": "rgba(120,120,128,0.05)"},
    "skipped":  {"icon": "·",  "color": _MUTED_TEXT, "bg": "rgba(120,120,128,0.05)"},
}


def _reality_checks_section(
    ticker: str,
    base_revenue: Optional[float],
    cagr_y1_5: Optional[float],
    cagr_y6_10: Optional[float],
) -> None:
    """
    Reality Checks — interrogate the DCF projection against external
    constraints (GDP, TAM, history). Currently runs the GDP check
    (Feature 1); other checks (TAM, inflection, analogs) will plug in
    here as they ship.
    """
    try:
        from aletheia.tools.reality_checks import (
            run_all_checks, overall_severity,
        )
    except Exception:
        return

    results = run_all_checks(ticker, base_revenue, cagr_y1_5, cagr_y6_10)
    if not results:
        return

    overall = overall_severity(results)
    overall_style = _SEVERITY_STYLE.get(overall, _SEVERITY_STYLE["info"])

    # Section header — colored based on the highest severity in the section
    st.markdown(
        f"##### 🔍 Reality Checks  "
        f"<span style='color:inherit;opacity:0.7;font-family:DM Mono,monospace;"
        f"font-size:13px;font-weight:400'>"
        f"<span style='color:{overall_style['color']}'>● {overall.upper()}</span></span>",
        unsafe_allow_html=True,
    )

    for r in results:
        style = _SEVERITY_STYLE.get(r.severity, _SEVERITY_STYLE["info"])
        # Each check renders as a panel-bordered block with headline + detail.
        st.markdown(
            f"""
<div style='background:{style["bg"]};border-left:4px solid {style["color"]};
            padding:14px 16px;border-radius:0 6px 6px 0;color:inherit;
            font-size:14px;line-height:1.6;margin:8px 0'>
  <div style='font-weight:600;margin-bottom:6px'>
    <span style='color:{style["color"]}'>{style["icon"]} {r.headline}</span>
  </div>
  <div style='opacity:0.9;font-size:13px'>{r.detail}</div>
</div>
            """,
            unsafe_allow_html=True,
        )

        # Numeric data behind the headline — rendered inline.
        if r.data:
            _inline_label("Computation detail")
            rows = []
            for k, v in r.data.items():
                if k == "thresholds":
                    continue   # skip the threshold dict — render below
                if isinstance(v, float):
                    if abs(v) >= 1e9:
                        disp = f"${v/1e9:,.1f}B"
                    elif abs(v) < 1:
                        disp = f"{v*100:+.2f}%" if "ratio" in k or "to_gdp" in k else f"{v:.4f}"
                    else:
                        disp = f"{v:,.2f}"
                else:
                    disp = str(v)
                rows.append({"Field": k, "Value": disp})
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True,
                             use_container_width=True)
            if isinstance(r.data.get("thresholds"), dict):
                th = r.data["thresholds"]
                st.caption(
                    "Thresholds: "
                    + " · ".join(f"{lvl} ≥ {pct*100:.1f}%"
                                 for lvl, pct in th.items())
                )


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

    # Liquidity, downside, leverage detail tables — rendered inline.
    if liq:
        _inline_label("Liquidity detail")
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

    if down:
        _inline_label("Downside / floor analysis")
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

    if lev:
        _inline_label("Leverage detail")
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

    glyph = "✅" if n_fail == 0 else "⚠" if n_fail < 2 else "❌"
    st.markdown(
        f"##### {glyph} Constitution checks "
        f"<span style='font-family:DM Mono,monospace;font-size:12px;"
        f"color:{_MUTED_TEXT};font-weight:500;'>"
        f"&nbsp;&nbsp;<span style='color:{_GREEN}'>{n_pass} pass</span> · "
        f"<span style='color:{_AMBER}'>{n_warn} warn</span> · "
        f"<span style='color:{_RED}'>{n_fail} fail</span></span>",
        unsafe_allow_html=True,
    )
    for c in checks:
        st.markdown(f"- {c}")


# ── FMP validation banner — Gate A / B / D status ──────────────────────

def _validation_pill(label: str, status: str) -> str:
    """Render a colored pill for one validation sub-block."""
    color = {
        "validated":       _GREEN,
        "drift":           _AMBER,
        "blocking_drift":  _RED,
        "skipped":         _MUTED_TEXT,
        "not_run":         _MUTED_TEXT,
        "missing":         _MUTED_TEXT,
    }.get(status, _MUTED_TEXT)
    icon = {
        "validated":      "✓",
        "drift":          "⚠",
        "blocking_drift": "✗",
        "skipped":        "·",
        "not_run":        "·",
        "missing":        "·",
    }.get(status, "·")
    return (
        f'<span style="display:inline-block;font-family:DM Mono,monospace;'
        f'font-size:10px;font-weight:600;letter-spacing:0.5px;'
        f'text-transform:uppercase;color:{color};border:1px solid {color};'
        f'padding:3px 9px;border-radius:3px;margin-right:6px;">'
        f'{icon} {label}: {status.replace("_", " ")}</span>'
    )


# ── Price-provenance banner — what price drove the analysis ────────────

def _freshness_methodology_explainer(
    dcf: Dict[str, Any], p2v: Dict[str, Any],
) -> None:
    """One-paragraph note explaining what TTM means for this report's
    numbers. Renders only when TTM is the base period — for FY-base
    legacy reports the existing UX is unchanged. Collapsed by default
    so it doesn't add visual weight on every page view."""
    base_period = (
        (dcf or {}).get("base_period")
        or (p2v or {}).get("base_period")
        or "FY"
    )
    if base_period != "TTM":
        return

    fy_year = (
        (dcf or {}).get("fy_fiscal_year")
        or (p2v or {}).get("fy_fiscal_year")
    )
    fy_label = f"FY{fy_year}" if fy_year else "the most recent 10-K"

    with st.expander("ℹ How TTM-base affects these numbers", expanded=False):
        st.markdown(
            f"""
This report anchors every scenario IPS, margin of safety, reverse-DCF
implied growth, and live screening multiple to **trailing-twelve-month
financials** — the latest 10-Q rolled forward against the prior three
quarters. Refreshed within ~90 days of each filing.

The last full fiscal year ({fy_label}) is shown alongside on the
Multi-year history table so analysts can reconcile a moving target
against the audited annual base.

Why our number may differ from a screener you're looking at:
- Most screeners default to the last reported FY — they're 12 months
  behind the latest 10-Q until the next 10-K lands.
- Some screeners use FY-end price for multiples; we use current price.
  The "Multiple decomposition" caption above explains that drift.
- Ratios (ROIC, ROE, margins) are TTM-numerator / latest-balance-sheet-
  denominator. A pre-update screener using prior-FY components will
  diverge until it refreshes.
"""
        )


def _price_provenance_banner(
    ticker: str, dcf: Dict[str, Any], p2v: Dict[str, Any]
) -> None:
    """Surface the price + period snapshot every IPS/MoS/multiple was
    computed against. When the DCF anchored to TTM, also call out the
    latest filing date and the FY year being shown alongside for
    reconciliation (Phase Q-5)."""

    # Prefer live /dcf payload; fall back to serving-JSON phase2.
    price = (dcf or {}).get("current_price") or (p2v or {}).get("current_price")
    market_cap = (dcf or {}).get("market_cap") or (p2v or {}).get("market_cap")
    shares = (dcf or {}).get("shares_diluted") or (p2v or {}).get("shares_diluted")
    run_date = (dcf or {}).get("run_date") or (p2v or {}).get("run_date")

    # Period provenance (Phase Q-5)
    base_period   = (dcf or {}).get("base_period") or (p2v or {}).get("base_period") or "FY"
    base_pe_date  = (dcf or {}).get("base_period_end_date") or (p2v or {}).get("base_period_end_date")
    fy_recon_year = (dcf or {}).get("fy_fiscal_year") or (p2v or {}).get("fy_fiscal_year")

    if not price:
        return

    price_str  = f"${price:,.2f}"
    mkt_str    = f"${market_cap/1e9:,.1f}B" if market_cap else "—"
    shares_str = f"{shares/1e9:,.2f}B" if shares else "—"
    run_str = (run_date or "")[:19].replace("T", " ") if run_date else "—"

    # Period badge — TTM gets emphasis since it's the freshness signal.
    if base_period == "TTM":
        period_label = "TTM (latest 10-Q)"
        period_value = base_pe_date or "—"
        period_recon = (
            f" · FY{fy_recon_year} shown alongside" if fy_recon_year else ""
        )
    else:
        period_label = "FY (last 10-K)"
        period_value = base_pe_date or "—"
        period_recon = ""

    # Days-since-filing + next-expected estimate.  Same SEC-accelerated-
    # filer floor used on the Financials tab: period_end + 90 + 45 days
    # is the conservative "next 10-Q likely on disk" date.  Conservative
    # by design — actual filers usually beat the floor by 1-3 weeks.
    age_phrase = ""
    next_phrase = ""
    if base_pe_date:
        try:
            import datetime as _dt
            pe = _dt.date.fromisoformat(base_pe_date)
            today = _dt.date.today()
            days_since = (today - pe).days
            age_phrase = f" · {days_since}d ago"
            next_dt = pe + _dt.timedelta(days=135)
            days_until = (next_dt - today).days
            if days_until <= 0:
                next_phrase = " · next any day"
            else:
                next_phrase = f" · next ~{next_dt.isoformat()} ({days_until}d)"
        except (TypeError, ValueError):
            pass

    st.markdown(
        f"""
<div style='border:1px solid {_BAR_BG};border-left:4px solid #3b82f6;
            padding:10px 14px;border-radius:0 6px 6px 0;
            margin:8px 0 16px 0;color:inherit;
            display:flex;flex-wrap:wrap;gap:18px;align-items:baseline;'>
  <div>
    <span style='font-family:DM Mono,monospace;font-size:10px;
                 color:{_MUTED_TEXT};letter-spacing:0.5px;
                 text-transform:uppercase;'>Analysis price</span>
    <div style='font-size:18px;font-weight:700;'>{price_str}</div>
  </div>
  <div>
    <span style='font-family:DM Mono,monospace;font-size:10px;
                 color:{_MUTED_TEXT};letter-spacing:0.5px;
                 text-transform:uppercase;'>Market cap</span>
    <div style='font-size:14px;font-weight:600;'>{mkt_str}</div>
  </div>
  <div>
    <span style='font-family:DM Mono,monospace;font-size:10px;
                 color:{_MUTED_TEXT};letter-spacing:0.5px;
                 text-transform:uppercase;'>Diluted shares</span>
    <div style='font-size:14px;font-weight:600;'>{shares_str}</div>
  </div>
  <div>
    <span style='font-family:DM Mono,monospace;font-size:10px;
                 color:{_MUTED_TEXT};letter-spacing:0.5px;
                 text-transform:uppercase;'>Captured at</span>
    <div style='font-size:13px;font-weight:500;font-family:DM Mono,monospace;'>{run_str} UTC</div>
  </div>
  <div>
    <span style='font-family:DM Mono,monospace;font-size:10px;
                 color:{_MUTED_TEXT};letter-spacing:0.5px;
                 text-transform:uppercase;'>Base period</span>
    <div style='font-size:13px;font-weight:600;'>{period_label}</div>
    <div style='font-size:11px;color:{_MUTED_TEXT};font-family:DM Mono,monospace;'>ended {period_value}{age_phrase}{period_recon}</div>
    <div style='font-size:11px;color:{_MUTED_TEXT};font-family:DM Mono,monospace;'>{next_phrase.lstrip(" ·") if next_phrase else ""}</div>
  </div>
  <div style='flex:1;min-width:280px;font-size:11px;color:{_MUTED_TEXT};
              line-height:1.5;border-left:1px solid {_BAR_BG};
              padding-left:14px;'>
    All scenario IPS, margin of safety, reverse-DCF, and live screening
    multiples are computed against this price using {period_label}
    financials.
    <br/>
    FMP cross-checks may show drift because FMP screening data uses
    fiscal-year-end price.
  </div>
</div>
        """.strip(),
        unsafe_allow_html=True,
    )


def _fmp_validation_banner(validation: Dict[str, Any]) -> None:
    """One-line banner stamping the 3 validation sub-blocks at top of
    Deep Dive. Click expander for per-field drift detail.

    Empty / pre-Gate reports show 'not run' pill; new regen reports
    show validated/drift/blocking_drift per Gate A (ingestion), Gate B
    (calc), Gate D's final-assembly check.
    """
    if not validation:
        # Legacy report — pre-FMP-gate. Render a single muted pill so
        # analyst knows validation wasn't run vs ran-and-passed.
        st.markdown(
            f'<div style="margin:8px 0 16px 0;">'
            f'{_validation_pill("FMP", "not_run")}'
            f'<span style="font-family:DM Mono,monospace;font-size:11px;'
            f'color:{_MUTED_TEXT};margin-left:8px;">'
            f'pre-validation report — re-run pipeline to add stamps</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        return

    ing = (validation.get("ingestion") or {}).get("status", "missing")
    calc = (validation.get("calc") or {}).get("status", "missing")
    fa   = (validation.get("final_assembly") or {}).get("status", "missing")

    # Single-line banner with three pills
    st.markdown(
        f'<div style="margin:8px 0 12px 0;">'
        f'{_validation_pill("Ingest (Gate A)", ing)}'
        f'{_validation_pill("Calc (Gate B)", calc)}'
        f'{_validation_pill("Assembly (Gate D)", fa)}'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Click-to-expand detail. Only show when at least one block has any
    # signal (otherwise the expander is empty noise).
    has_detail = any(
        s not in ("not_run", "missing")
        for s in (ing, calc, fa)
    )
    if not has_detail:
        return

    with st.expander("FMP validation detail", expanded=False):
        # ── Gate A — ingestion ─────────────────────────────────────
        ing_block = validation.get("ingestion") or {}
        ing_fields = ing_block.get("fields") or {}
        if ing_fields:
            _inline_label("Ingestion (Gate A) — line items vs FMP")
            rows = []
            for fname, info in ing_fields.items():
                ours = info.get("ours")
                fmp  = info.get("fmp")
                drift = info.get("drift_pct")
                rows.append({
                    "Field":  fname,
                    "Ours":   "—" if ours is None else f"{ours:,.2f}" if abs(ours or 1) > 1 else f"{ours:.4f}",
                    "FMP":    "—" if fmp  is None else f"{fmp:,.2f}" if abs(fmp or 1) > 1 else f"{fmp:.4f}",
                    "Drift":  "—" if drift is None else f"{drift*100:+.2f}%",
                    "Status": info.get("status", "—"),
                    "Block":  "🔒" if info.get("blocking") and info.get("status") == "structural_drift" else "",
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        # ── Gate B — calc ──────────────────────────────────────────
        calc_block = validation.get("calc") or {}
        calc_fields = calc_block.get("fields") or {}
        if calc_fields:
            _inline_label("Calc (Gate B) — phase2_valuation vs FMP")
            rows = []
            for fname, info in calc_fields.items():
                ours = info.get("ours")
                fmp  = info.get("fmp")
                drift = info.get("drift_pct")
                rows.append({
                    "Field":  fname,
                    "Ours":   "—" if ours is None else f"{ours:,.4f}" if abs(ours or 1) < 1 else f"{ours:,.2f}",
                    "FMP":    "—" if fmp  is None else f"{fmp:,.4f}" if abs(fmp or 1) < 1 else f"{fmp:,.2f}",
                    "Drift":  "—" if drift is None else f"{drift*100:+.2f}%",
                    "Status": info.get("status", "—"),
                    "Block":  "🔒" if info.get("blocking") and info.get("status") == "structural_drift" else "",
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        # ── Gate D — final assembly ────────────────────────────────
        fa_block = validation.get("final_assembly") or {}
        fa_checks = fa_block.get("checks") or {}
        if fa_checks:
            _inline_label("Final assembly (Gate D)")
            nf = fa_checks.get("numeric_fidelity") or {}
            mono = fa_checks.get("scenario_monotonicity") or {}
            st.markdown(
                f"- **Numeric fidelity**: "
                f"{nf.get('violations', 0)} violation(s)"
            )
            for d in nf.get("details") or []:
                st.markdown(f"  - {d}")
            mono_ok = mono.get("ok")
            mono_label = "✓ monotonic" if mono_ok else "✗ inverted" if mono_ok is False else "— incomplete"
            mono_vals = mono.get("values") or {}
            st.markdown(
                f"- **Scenario monotonicity (bear ≤ base ≤ bull)**: {mono_label} "
                f"(bear={mono_vals.get('bear')}, base={mono_vals.get('base')}, bull={mono_vals.get('bull')})"
            )

        # Stamp metadata
        stamped = validation.get("stamped_at", "")
        if stamped:
            st.caption(f"Stamped at {stamped[:19]} · "
                       f"schema v{validation.get('schema_version', '?')}")


# ── Reverse-DCF reasons ──────────────────────────────────────────────────

def _rdcf_reasons(rdcf: Dict[str, Any]) -> None:
    reasons = rdcf.get("reasons") or []
    if not reasons:
        return
    _inline_label("Reverse-DCF reasoning")
    for r in reasons:
        st.markdown(f"- {r}")


_CS_API_BASE = "http://localhost:8000"
_ACK_DECISION_LABELS = {
    "override_applied":   "Override applied — assumptions edited to reflect this",
    "accepted_rationale": "Accept — immaterial / already priced (rationale required)",
    "rejected":           "Reject — flag is inaccurate (rationale required)",
    "needs_analysis":     "Needs more analysis — park (does NOT clear the gate)",
}


def _cs_api(method: str, path: str, body: Optional[dict] = None):
    """Minimal API client for the current-state ack endpoints.
    Returns (data, error_message)."""
    import httpx
    try:
        r = httpx.request(method, f"{_CS_API_BASE}{path}", json=body, timeout=30)
    except httpx.RequestError as exc:
        return None, f"Cannot reach API: {exc}"
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        return None, detail if isinstance(detail, str) else str(detail)
    return r.json(), None


def _render_flag_ack(ticker: str, f: Dict[str, Any]) -> None:
    """One flag row + its acknowledgment control. Acked flags show the
    decision/rationale + a Reopen button; open flags show a decision form."""
    fsev = f.get("severity")
    dot = "🔴" if fsev == "HIGH" else "🟠" if fsev == "MEDIUM" else "🟡"
    src = f" _({f.get('source')})_" if f.get("source") else ""
    key = f.get("key") or ""
    ack = f.get("ack")

    st.markdown(f"{dot} **{fsev}** · {f.get('message','')}{src}")
    if f.get("recommendation"):
        st.caption(f"↳ {f.get('recommendation')}")

    if f.get("acknowledged") and ack:
        when = (ack.get("decided_at") or "")[:10]
        st.success(
            f"✓ Resolved — **{_ACK_DECISION_LABELS.get(ack.get('decision'), ack.get('decision'))}**"
            + (f" · _{ack.get('rationale')}_" if ack.get("rationale") else "")
            + f"  ·  {ack.get('decided_by','analyst')}, {when}")
        if st.button("Reopen", key=f"cs_reopen_{key}"):
            _, err = _cs_api("DELETE",
                             f"/ticker/{ticker}/current_state/acknowledgments?flag_key={key}")
            if err:
                st.error(err)
            else:
                st.rerun()
        return

    # Parked (needs_analysis) — show state but keep the form open to resolve.
    if ack and ack.get("decision") == "needs_analysis":
        st.warning("⏳ Parked: needs more analysis — still pending for the gate.")

    with st.expander("Acknowledge this flag", expanded=(fsev == "HIGH" and not ack)):
        decision = st.selectbox(
            "Decision", list(_ACK_DECISION_LABELS),
            format_func=lambda d: _ACK_DECISION_LABELS[d],
            key=f"cs_dec_{key}")
        rationale = st.text_area(
            "Rationale" + (" (required)" if decision in ("accepted_rationale", "rejected") else ""),
            key=f"cs_rat_{key}", height=70,
            placeholder="Why this decision? Recorded in the audit trail.")
        if st.button("Save decision", key=f"cs_save_{key}", type="primary"):
            if decision in ("accepted_rationale", "rejected") and not rationale.strip():
                st.error("A written rationale is required for this decision.")
            else:
                _, err = _cs_api(
                    "PUT", f"/ticker/{ticker}/current_state/acknowledgments",
                    {"flag_key": key, "decision": decision,
                     "rationale": rationale.strip() or None,
                     "category": f.get("category"), "severity": fsev})
                if err:
                    st.error(err)
                else:
                    st.rerun()


def _business_analysis_panel(ba: Dict[str, Any], ag: Dict[str, Any]) -> None:
    """Bottom-up business analysis (growth decomposition + coverage) and the
    assumption-grounding keystone. No-op when unavailable."""
    def _p(v):
        return f"{v*100:+.1f}%" if isinstance(v, (int, float)) else "—"

    gd = (ba or {}).get("growth_decomposition") or {}
    if gd.get("available") or (ag or {}).get("available"):
        with st.container(border=True):
            st.markdown("#### 🔬 Bottom-up business analysis")
            if gd.get("available"):
                breaks = gd.get("break_years") or []
                st.markdown(
                    f"**Growth source:** raw {_p(gd.get('raw_cagr'))} = organic "
                    f"{_p(gd.get('organic_cagr'))} + M&A {_p(gd.get('ma_contribution_pp'))}"
                    + (f" (breaks FY{breaks})" if breaks else "")
                    + f" — _{gd.get('split','')}_")
            if ba and ba.get("available"):
                st.caption(f"Coverage: {ba.get('n_present','?')}/{ba.get('n_total','?')} "
                           "bottom-up dimensions populated (rest pending extraction)")
            # Assumption grounding (keystone).
            if (ag or {}).get("available"):
                st.markdown("**Assumption grounding** _(engine vs business-grounded; "
                            "shown, not applied)_")
                rows = [{
                    "Assumption": r.get("assumption", ""),
                    "Engine": _p(r.get("engine_value")),
                    "Grounded": _p(r.get("grounded_value")),
                    "Δ": _p(r.get("delta")),
                    "Basis": r.get("note", ""),
                } for r in (ag.get("rows") or [])]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _wacc_analysis_panel(wa: Dict[str, Any]) -> None:
    """Discount-rate detail panel (memo §7): build-up, premia, sensitivity,
    implied WACC. No-op when unavailable."""
    if not wa or not wa.get("available"):
        return

    def _p(v, dp=1):
        return f"{v*100:.{dp}f}%" if isinstance(v, (int, float)) else "—"

    c = wa.get("components") or {}
    pr = wa.get("premia") or {}
    with st.container(border=True):
        st.markdown("#### 📉 Discount-rate detail (WACC)")
        cc = st.columns(4)
        cc[0].metric("WACC (base)", _p(c.get("wacc_base")))
        cc[1].metric("Cost of equity", _p(c.get("cost_of_equity")))
        iw = wa.get("implied_wacc"); bps = wa.get("implied_vs_base_bps")
        cc[2].metric("Implied WACC", _p(iw) if iw is not None else "—",
                     f"{bps:+d} bps vs base" if bps is not None else None)
        cc[3].metric("Adjusted WACC", _p(wa.get("adjusted_wacc")),
                     f"+{_p(pr.get('total'))} premia")
        st.caption(
            f"Build-up: rf {_p(c.get('risk_free_rate'))} + β "
            f"{c.get('beta'):.2f}×ERP {_p(c.get('erp'))} = Ke "
            f"{_p(c.get('cost_of_equity'))} · weights E "
            f"{_p(c.get('equity_weight'),0)}/D {_p(c.get('debt_weight'),0)}")
        if pr.get("total"):
            st.caption(f"Premia — size {_p(pr.get('size'))}, country "
                       f"{_p(pr.get('country'))} ({pr.get('country_used') or '—'}), "
                       f"idiosyncratic {_p(pr.get('idiosyncratic'))} "
                       f"[{', '.join(pr.get('idiosyncratic_reasons') or []) or 'none'}] "
                       f"· shown for triangulation, not auto-applied")
        sens = wa.get("sensitivity") or []
        if sens:
            rows = [{
                "WACC Δ": f"{s.get('delta_bps'):+d} bps" + ("  (base)" if s.get("is_base") else ""),
                "WACC": _p(s.get("wacc")),
                "IV/sh": f"${s.get('iv'):,.2f}" if isinstance(s.get("iv"), (int, float)) else "—",
                "vs price": _p(s.get("vs_price_pct"), 0),
            } for s in sens]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        q = wa.get("quality") or {}
        st.caption(f"Discount-rate quality {q.get('score','—')}/{q.get('max','—')} · "
                   f"{q.get('notes','')}")


def _downside_protection_panel(dp: Dict[str, Any]) -> None:
    """Downside-protection panel (memo §8): asymmetry, downside ladder,
    required-MoS-by-risk, position-sizing band. No-op when unavailable."""
    if not dp or not dp.get("available"):
        return

    def _p(v):
        return f"{v*100:+.0f}%" if isinstance(v, (int, float)) else "—"

    asym = dp.get("asymmetry_ratio")
    verdict = dp.get("asymmetry_verdict") or "n/a"
    with st.container(border=True):
        st.markdown("#### 🛟 Downside protection")
        c1, c2, c3 = st.columns(3)
        c1.metric("Base upside", _p(dp.get("base_upside_pct")))
        c2.metric("Worst-case", _p(dp.get("worst_case_pct")))
        c3.metric("Asymmetry (up÷down)",
                  f"{asym:.1f}×" if isinstance(asym, (int, float)) else "—",
                  verdict)
        ladder = dp.get("downside_scenarios") or []
        if ladder:
            rows = [{
                "Downside scenario": e.get("name", ""),
                "Value/sh": f"${e.get('value', 0):,.2f}",
                "vs price": _p(e.get("vs_price_pct")),
                "Severity": e.get("severity", ""),
                "Basis": e.get("basis", ""),
            } for e in ladder]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        req = dp.get("required_mos") or {}
        mosv = (dp.get("mos_verdict") or "n/a").replace("_", " ")
        st.markdown(
            f"**Margin of safety:** actual {_p(dp.get('actual_mos'))} vs required "
            f"{_p(req.get('mos_good'))} (good) / {_p(req.get('mos_strong'))} (strong) "
            f"for a _{req.get('stage','')}_ business → **{mosv}**")
        siz = dp.get("position_sizing") or {}
        st.markdown(f"**Suggested size:** {siz.get('label','—')}  "
                    f"_({siz.get('basis','')})_")
        fms = dp.get("failure_modes") or []
        if fms:
            st.markdown("**Failure modes** _(permanent-impairment events to monitor)_")
            for m in fms[:5]:
                if isinstance(m, dict):
                    line = f"⚠ **{m.get('name','')}**"
                    if m.get("monitoring_metric"):
                        line += f" — watch: _{m['monitoring_metric']}_"
                    st.markdown(line)
                    if m.get("impact"):
                        st.caption(f"   ↳ {m['impact']}")
                else:
                    st.markdown(f"⚠ {m}")
        if dp.get("premortem"):
            st.info(f"🔮 **Pre-mortem:** {dp['premortem']}")


def _current_state_gate(cs: Dict[str, Any], ticker: str = "") -> str:
    """Render the Current-State gate above the conviction/hero and return the
    UNRESOLVED max severity ('HIGH'/'MEDIUM'/'LOW'/'NONE') — acknowledged flags
    no longer count. While any HIGH flag is unresolved the conviction tier is
    tagged 'FLAGS PENDING' downstream; once every HIGH flag is acknowledged
    (override / accept-with-rationale / reject) the gate clears."""
    if not cs or cs.get("error"):
        return "NONE"
    raw_sev = cs.get("max_severity", "NONE")
    eff_sev = cs.get("unresolved_severity", raw_sev)
    flags = cs.get("flags") or []
    # Nothing material at all → no gate.
    if raw_sev not in ("HIGH", "MEDIUM"):
        return eff_sev
    pillar = cs.get("pillar_score")
    n_unresolved_high = cs.get("unresolved_high", 0)

    if eff_sev == "HIGH":
        title = "⛔ Current-State: CONVICTION GATED — HIGH flags pending"
    elif n_unresolved_high == 0 and raw_sev == "HIGH":
        title = "✅ Current-State: HIGH flags acknowledged — gate cleared"
    elif eff_sev == "MEDIUM":
        title = "⚠️ Current-State: review flags before relying on the rating"
    else:
        title = "✅ Current-State: flags acknowledged"

    with st.container(border=True):
        st.markdown(f"#### {title}" + (f"  ·  pillar {pillar}/5" if pillar else ""))
        if eff_sev == "HIGH":
            st.error(
                f"Engine assumptions conflict with current signals — "
                f"**{n_unresolved_high} HIGH flag(s) unresolved**. The tier below "
                "is shown **FLAGS PENDING**. Acknowledge each (apply an override, "
                "accept with rationale, or reject) to clear the gate.")
        elif n_unresolved_high == 0 and raw_sev == "HIGH":
            st.success("All HIGH flags acknowledged. The decisions below are "
                       "recorded in the audit trail; the conviction tier is no "
                       "longer gated.")
        recs = cs.get("reconciliation") or []
        for r in recs:
            eng, sig, dl = r.get("engine"), r.get("signal"), r.get("delta")
            if isinstance(eng, (int, float)) and isinstance(sig, (int, float)):
                st.markdown(
                    f"- **{r.get('assumption','')}**: engine {eng*100:+.1f}% vs "
                    f"{r.get('signal_label','signal')} {sig*100:+.1f}% "
                    f"({dl*100:+.1f}pp) — {r.get('recommendation','')}")
        st.markdown("---")
        for f in flags:
            _render_flag_ack(ticker, f)
        if not (cs.get("events")):
            st.caption("Consensus-based flags only — open the Financials tab "
                       "and click **Events** to pull recent event coverage.")
    return eff_sev


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
    thesis_synth       = val.get("thesis_synthesis") or {}   # from thesis_synthesizer agent
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

    # ── Current-State gate (Phase 1.5) ──────────────────────────────────
    # Rendered BEFORE the hero/conviction so HIGH current-state flags confront
    # the analyst before the IV/MoS/tier. Gates a clean CONVICTION rating.
    _current_state = (dcf or {}).get("current_state") or {}
    _cs_severity = _current_state_gate(_current_state, ticker)
    # Reused signals (Phase A/B): sector-relative valuation + policy/regulatory
    # context. Display-only; no-ops when unavailable. Shown even when no gate.
    if _current_state and not _current_state.get("error"):
        _reused_keys = ("sector_valuation", "policy_regulatory",
                        "market_signal", "analyst_sentiment")
        if any((_current_state.get(k) or {}).get("available") for k in _reused_keys):
            from aletheia.ui.financials_view import _render_reused_signals
            with st.container(border=True):
                _render_reused_signals(_current_state)

    # ── Downside protection (memo §8) ───────────────────────────────────
    _downside_protection_panel((dcf or {}).get("downside_protection") or {})

    # ── Discount-rate detail (memo §7) ──────────────────────────────────
    _wacc_analysis_panel((dcf or {}).get("wacc_analysis") or {})

    # ── Bottom-up business analysis + assumption grounding (§4 keystone) ─
    _business_analysis_panel((dcf or {}).get("business_analysis") or {},
                             (dcf or {}).get("assumption_grounding") or {})

    # ── FMP validation banner (Gates A/B/D) ─────────────────────────────
    _fmp_validation_banner((full_report or {}).get("_validation") or {})

    # ── Price provenance — which price drove every calc on this page ─────
    _price_provenance_banner(ticker, dcf, p2v)

    # ── Freshness methodology explainer (one-time read for new users) ───
    _freshness_methodology_explainer(dcf, p2v)

    # ── Valuation engine banner (Phase A.4) ──────────────────────────────
    # Surfaces which engine produced the IV + MoS — FCFF (standard
    # corporate cash-flow DCF), rate-base (regulated utilities), DDM
    # (banks / managed care; Phase A.7), embedded-value (insurance
    # conglomerates; Phase A.7). Analysts shouldn't have to read the
    # underlying numbers to know which model produced them.
    _engine_banner(p2v)

    # ── Hero strip ────────────────────────────────────────────────────────
    _hero_strip(ticker, investment_thesis, dcf, fund, universe_row)

    # ── Pillar scorecard ──────────────────────────────────────────────────
    if pillar_scores:
        st.markdown("---")
        _pillar_section(pillar_scores, _cs_severity)

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
    _signal_reconciliation(rdcf, md)

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
        # Structured thesis from thesis_synthesizer (added in week-5
        # consolidation). Renders nothing for tickers without a populated
        # thesis_synthesis block (pre-consolidation reports / mock fallbacks).
        if thesis_synth.get("thesis_statement"):
            st.markdown("<br>", unsafe_allow_html=True)
            _structured_thesis_section(thesis_synth, dcf, p2v)
        st.markdown("<br>", unsafe_allow_html=True)
        _contrarian_block(contrarian, dcf)

    # ── Reality Checks (new — Feature 1: GDP comparison) ─────────────────
    # Pulls the actual base-case CAGR + base revenue from the financials
    # bundle's `assumptions` block. For tickers without a saved assumptions
    # set (pending / not ingested), the check skips with a neutral message.
    base_revenue = None
    cagr_y1_5 = None
    cagr_y6_10 = None
    try:
        # Prefer the financials bundle (has both raw revenue + DCF assumptions).
        from aletheia.ui.financials import ticker_detail
        bundle = ticker_detail(ticker)
        asn = bundle.get("assumptions") or {}
        cagr_y1_5  = asn.get("revenue_cagr_y1_5")
        cagr_y6_10 = asn.get("revenue_cagr_y6_10")
        base_revenue = (bundle.get("income_statement") or {}).get("Revenue")
    except Exception:
        pass

    st.markdown("---")
    _reality_checks_section(ticker, base_revenue, cagr_y1_5, cagr_y6_10)

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
