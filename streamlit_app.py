"""
dashboard/app.py

Aletheia Streamlit Dashboard — API-backed version
==================================================
Consumes the FastAPI backend at localhost:8000.
All data comes through the API — no direct DB or file access.

Run (after starting the API):
    PYTHONPATH=. uvicorn api.main:app --reload --port 8000
    streamlit run dashboard/app.py
"""

import math
from typing import Optional, Dict, Any

import httpx
import streamlit as st
import plotly.graph_objects as go
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

API_BASE = "http://localhost:8000"
TIMEOUT  = 15  # seconds

st.set_page_config(
    page_title="Aletheia · Investment Intelligence",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─────────────────────────────────────────────────────────────────────────────
# Styles
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&family=DM+Sans:wght@300;400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
    background-color: var(--background-color);
}
footer { visibility: hidden; }
.block-container { padding-top: 1.5rem; max-width: 1400px; }

[data-testid="metric-container"] {
    background: var(--secondary-background-color); border: 1px solid rgba(128,128,128,0.2);
    border-radius: 8px; padding: 16px 20px;
}
[data-testid="metric-container"] label {
    font-family: 'DM Mono', monospace !important;
    font-size: 10px !important; text-transform: uppercase;
    letter-spacing: 0.1em; color: #71717a !important;
}
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    font-family: 'Syne', sans-serif !important;
    font-size: 22px !important; font-weight: 700 !important; color: var(--text-color) !important;
}
.stTabs [data-baseweb="tab-list"] { background: transparent; border-bottom: 1px solid rgba(128,128,128,0.2); gap: 0; }
.stTabs [data-baseweb="tab"] {
    font-family: 'DM Mono', monospace; font-size: 11px;
    letter-spacing: 0.08em; text-transform: uppercase;
    color: #71717a; background: transparent;
    border-bottom: 2px solid transparent; padding: 12px 24px;
}
.stTabs [aria-selected="true"] {
    color: #f59e0b !important; border-bottom-color: #f59e0b !important;
    background: transparent !important;
}
.aletheia-header {
    font-family: 'Syne', sans-serif; font-size: 28px; font-weight: 800;
    background: linear-gradient(135deg, #f59e0b, #fbbf24);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text; letter-spacing: -0.5px; margin-bottom: 4px;
    padding: 8px 0;
}
.aletheia-subtitle {
    font-family: 'DM Mono', monospace; font-size: 11px;
    color: #52525b; text-transform: uppercase; letter-spacing: 0.1em;
}
.api-badge {
    font-family: 'DM Mono', monospace; font-size: 10px;
    background: rgba(16,185,129,.12); color: #10b981;
    padding: 3px 10px; border-radius: 12px; border: 1px solid rgba(16,185,129,.2);
}
hr { border-color: rgba(128,128,128,0.2); margin: 16px 0; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# API client
# ─────────────────────────────────────────────────────────────────────────────

def api_get(path: str) -> Optional[Any]:
    """Make a GET request to the FastAPI backend."""
    try:
        r = httpx.get(f"{API_BASE}{path}", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        st.error(
            f"**Cannot connect to API at {API_BASE}.**  \n"
            "Start the backend with: `PYTHONPATH=. uvicorn api.main:app --reload --port 8000`"
        )
        st.stop()
    except httpx.HTTPStatusError as e:
        st.error(f"API error {e.response.status_code}: {e.response.json().get('detail', str(e))}")
        return None
    except Exception as e:
        st.error(f"Request failed: {e}")
        return None


@st.cache_data(ttl=30)
def fetch_universe():
    return api_get("/universe")

@st.cache_data(ttl=300)
def fetch_ticker(ticker: str):
    return api_get(f"/ticker/{ticker}")

@st.cache_data(ttl=300)
def fetch_dcf(ticker: str):
    return api_get(f"/ticker/{ticker}/dcf")

@st.cache_data(ttl=300)
def fetch_fundamentals(ticker: str):
    return api_get(f"/ticker/{ticker}/fundamentals")

@st.cache_data(ttl=300)
def fetch_screening(ticker: str):
    return api_get(f"/ticker/{ticker}/screening")

@st.cache_data(ttl=300)
def fetch_universe_screening():
    return api_get("/screens/universe")

@st.cache_data(ttl=300)
def fetch_narrative(ticker: str):
    return api_get(f"/ticker/{ticker}/narrative")

@st.cache_data(ttl=300)
def fetch_health():
    return api_get("/health")


@st.cache_data(ttl=600)
def fetch_financials_bundle(ticker: str):
    """
    Read-only ticker_detail bundle for the Financials tab.
    Reads from DuckDB and runs DCFEngine live. Cached for 10 min.
    Independent of the agent-pipeline reports — works for any ticker
    that has cleaned data in DuckDB, even if no agent run exists yet.
    """
    from aletheia.ui.financials import ticker_detail
    return ticker_detail(ticker)

def extract_moat_from_narrative(narrative: str) -> str:
    """Extract moat-relevant sentences from pipeline narrative."""
    if not narrative:
        return ""
    moat_keywords = ["moat", "switching cost", "network effect", "lock-in",
                     "competitive advantage", "pricing power", "barrier"]
    sentences = narrative.replace("\n", " ").split(". ")
    relevant  = [s.strip() for s in sentences
                 if any(kw in s.lower() for kw in moat_keywords)]
    return ". ".join(relevant[:4]) + ("." if relevant else "")

def extract_econ_from_narrative(narrative: str) -> str:
    """Extract unit economics / TAM sentences from pipeline narrative."""
    if not narrative:
        return ""
    econ_keywords = ["cagr", "tam", "market", "growth", "penetration",
                     "revenue", "margin", "multiple", "expansion"]
    sentences = narrative.replace("\n", " ").split(". ")
    relevant  = [s.strip() for s in sentences
                 if any(kw in s.lower() for kw in econ_keywords)]
    return ". ".join(relevant[:4]) + ("." if relevant else "")


# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────

SIGNAL_COLOR = {
    "undervalued":        "#10b981", "fairly_valued":   "#3b82f6",
    "fair_value":         "#3b82f6", "priced_for_growth":"#f59e0b",
    "caution":            "#f59e0b", "speculative_premium":"#ef4444",
    "flag":               "#dc2626", "deep_value":      "#10b981",
    "high_quality":       "#10b981", "neutral":         "#a1a1aa",
    "moderate_premium":   "#f59e0b", "high_premium":    "#f97316",
}

SIGNAL_LABEL = {
    "undervalued":        "UNDERVALUED",  "fairly_valued":    "FAIR VALUE",
    "fair_value":         "FAIR VALUE",   "priced_for_growth":"GROWTH PRICED",
    "caution":            "CAUTION",      "speculative_premium":"SPECULATIVE",
    "flag":               "FLAG",         "deep_value":       "DEEP VALUE",
    "high_quality":       "HIGH QUALITY", "neutral":          "NEUTRAL",
    "moderate_premium":   "MODERATE PREMIUM", "high_premium":"HIGH PREMIUM",
}

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="DM Mono, monospace", color="#71717a", size=11),
    margin=dict(l=0, r=0, t=24, b=0),
    xaxis=dict(gridcolor="#27272a", zerolinecolor="#27272a"),
    yaxis=dict(gridcolor="#27272a", zerolinecolor="#27272a"),
)

def conv_color(v):
    if v is None: return "#a1a1aa"
    if v > 0:  return "#10b981"
    if v == 0: return "#a1a1aa"
    if v >= -3: return "#f59e0b"
    return "#ef4444"

def pct(v, decimals=1):
    if v is None: return "—"
    return f"{v*100:.{decimals}f}%"

def money(v):
    if v is None: return "—"
    return f"${v:,.0f}"

def xfmt(v):
    if v is None: return "—"
    return f"{v:.1f}×"

def bn(v):
    if v is None: return "—"
    return f"${v:.1f}B"

def signal_html(s: str, small=False) -> str:
    color = SIGNAL_COLOR.get(s, "#a1a1aa")
    label = SIGNAL_LABEL.get(s, s.upper() if s else "—")
    size = "10px" if small else "11px"
    return (
        f'<span style="background:rgba({"16,185,129" if color=="#10b981" else "59,130,246" if color=="#3b82f6" else "245,158,11" if color in ["#f59e0b","#d97706"] else "239,68,68"},.15);'
        f'color:{color};padding:2px 7px;border-radius:3px;'
        f'font-family:DM Mono,monospace;font-size:{size};font-weight:500">{label}</span>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main app
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # ── Check API health ──────────────────────────────────────────────────────
    health = fetch_health()
    if not health:
        return

    available = health.get("tickers_available", [])
    missing   = health.get("tickers_missing", [])

    # ── Global State Initialization ───────────────────────────────────────────
    views = ["◈  Universe", "◉  Deep Dive", "▦  Financials", "◧  Screening", "◨  Constitution", "📝  Thesis Builder", "◩  Reports"]
    if "active_ticker" not in st.session_state:
        st.session_state.active_ticker = available[0] if available else None
    if "active_view" not in st.session_state:
        st.session_state.active_view = views[0]

    # ── Fetch universe ────────────────────────────────────────────────────────
    universe_data = fetch_universe()
    if not universe_data:
        return
    ranked = universe_data.get("ranked", [])
    df = pd.DataFrame(ranked)

    # ── Sidebar Global Selector ───────────────────────────────────────────────
    st.sidebar.markdown("### 🎯 Target Company")
    current_index = available.index(st.session_state.active_ticker) if st.session_state.active_ticker in available else 0
    st.session_state.active_ticker = st.sidebar.selectbox(
        "Select Ticker", 
        options=available, 
        index=current_index,
        label_visibility="collapsed"
    )

    if st.session_state.active_ticker:
        report = fetch_ticker(st.session_state.active_ticker)
        if report:
            ps = report.get("4_valuation_synthesis", {}).get("investment_thesis", {}).get("pillar_scores", {})
            mos = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {}).get("three_scenario_dcf", {}).get("base", {}).get("margin_of_safety", 0)
            mos_pct = f"{mos:+.1%}" if mos else "—"
            mos_color = "#10b981" if mos > 0 else "#ef4444" if mos < -0.1 else "#f59e0b"
            
            st.sidebar.markdown(f"""
                <div style="display: flex; justify-content: space-between; padding: 12px 10px; background: rgba(128, 128, 128, 0.15); border-radius: 6px; margin-top: 12px;">
                    <div style="text-align: center;">
                        <div style="font-size: 11px; color: var(--text-color); opacity: 0.7; margin-bottom: 4px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;">Score</div>
                        <div style="font-size: 18px; font-weight: 700; color: var(--text-color); font-family: 'DM Mono', monospace;">{ps.get('capped_total','?')}/25</div>
                    </div>
                    <div style="text-align: center;">
                        <div style="font-size: 11px; color: var(--text-color); opacity: 0.7; margin-bottom: 4px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;">Tier</div>
                        <div style="font-size: 18px; font-weight: 700; color: var(--text-color); font-family: 'DM Mono', monospace;">{ps.get('position_tier','—').upper()[:4]}</div>
                    </div>
                    <div style="text-align: center;">
                        <div style="font-size: 11px; color: var(--text-color); opacity: 0.7; margin-bottom: 4px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;">MoS</div>
                        <div style="font-size: 18px; font-weight: 700; color: {mos_color}; font-family: 'DM Mono', monospace;">{mos_pct}</div>
                    </div>
                </div>
            """, unsafe_allow_html=True)

    # ── Sidebar Top Investable ────────────────────────────────────────────────
    st.sidebar.markdown("<hr style='margin: 16px 0'>", unsafe_allow_html=True)
    st.sidebar.markdown("### 🏆 Top Investable")
    
    if not df.empty and "base_mos" in df.columns:
        investable = df[df["base_mos"] > 0]
        if not investable.empty:
            top_5 = investable.sort_values(by=["conviction", "base_mos"], ascending=[False, False]).head(5)
            
            for _, row in top_5.iterrows():
                st.sidebar.markdown(f"""
                    <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px;">
                        <span style="font-weight: 700; font-size: 14px; font-family: 'DM Mono', monospace;">{row['ticker']}</span>
                        <span style="font-size: 12px; color: var(--text-color); opacity: 0.8;">Conv Score {int(row['conviction']):+d} • <span style="color: #059669; font-weight: 600;">{row['base_mos']:+.1%} MoS</span></span>
                    </div>
                """, unsafe_allow_html=True)
                
            st.sidebar.caption(f"Showing top 5 of {len(investable)}/{len(df)} names with positive MoS")
        else:
            st.sidebar.info("No companies currently offer a positive Margin of Safety.")

    # ── Header ────────────────────────────────────────────────────────────────
    hdr_col, btn_col = st.columns([4, 1])
    with hdr_col:
        st.markdown('<div class="aletheia-header">Aletheia</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="aletheia-subtitle">'
            f'Investment Intelligence · {len(available)} of {len(available)+len(missing)} Companies Ready'
            f'</div>',
            unsafe_allow_html=True,
        )
    with btn_col:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("⟳  Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.markdown(
            f'<div style="text-align:center;margin-top:4px">'
            f'<span class="api-badge">API {API_BASE}</span></div>',
            unsafe_allow_html=True,
        )

    if missing:
        st.info(
            f"Missing reports: {', '.join(missing)}. "
            f"Run `python main.py --ticker {'|'.join(missing[:3])}` to generate."
        )

    st.markdown("---")

    # ── Navigation (Replaces Tabs) ────────────────────────────────────────────
    try:
        active_view = st.segmented_control(
            "Navigation", views,
            default=st.session_state.active_view,
            label_visibility="collapsed",
            key="nav"
        )
    except AttributeError:
        active_view = st.radio(
            "Navigation", views,
            index=views.index(st.session_state.active_view),
            horizontal=True,
            label_visibility="collapsed",
            key="nav"
        )
    st.session_state.active_view = active_view

    # ──────────────────────────────────────────────────────────────────────────
    # TAB 1 — UNIVERSE
    # ──────────────────────────────────────────────────────────────────────────
    if active_view == "◈  Universe":

        # Stats
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.metric("Tickers Available", len(ranked))
        with c2:
            avg_mos = df["base_mos"].mean() if "base_mos" in df.columns else 0
            st.metric("Avg Margin of Safety", f"{avg_mos:+.1%}")
        with c3:
            avg_conv = df["conviction"].mean() if "conviction" in df.columns else 0
            st.metric("Avg Conviction", f"{avg_conv:.1f}", delta="out of ±10")
        with c4:
            n_pos_mos = (df["base_mos"] > 0).sum() if "base_mos" in df.columns else 0
            st.metric("Positive MoS", int(n_pos_mos), delta="base IV > market price")
        with c5:
            n_underval = df["multiple_signal"].isin(["undervalued","fairly_valued"]).sum() if "multiple_signal" in df.columns else 0
            st.metric("Attractive Multiple", int(n_underval), delta="below justified")

        st.markdown("<br>", unsafe_allow_html=True)

        # Ranked table
        st.markdown("#### Universe Rankings")

        display_rows = []
        for r in ranked:
            hist = r.get("historical_cagr")
            impl = r.get("implied_cagr")
            ratio = impl / hist if hist and hist > 0 and impl is not None else None
            display_rows.append({
                "Ticker":    r["ticker"],
                "Conv":      f"{int(r['conviction']):+d}" if r.get("conviction") is not None else "—",
                "Base IV":   money(r.get("base_iv")),
                "MoS":       pct(r.get("base_mos")),
                "Impl CAGR": pct(impl),
                "Hist CAGR": pct(hist),
                "Ratio":     f"{ratio:.1f}×" if ratio else "—",
                "EV/EBITDA": xfmt(r.get("ev_ebitda")),
                "Justified": xfmt(r.get("justified_ev_ebitda")),
                "Premium":   f"{r['multiple_premium']:+.0%}" if r.get("multiple_premium") is not None else "—",
                "Signal":    SIGNAL_LABEL.get(r.get("multiple_signal",""), "—"),
                "ROIC":      pct(r.get("roic")),
                "Moat":      f"{r['moat']:.1f}" if r.get("moat") else "—",
                "FCF":       bn(r.get("fcf_bn")),
            })

        st.dataframe(
            pd.DataFrame(display_rows),
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("<br>", unsafe_allow_html=True)

        # Charts
        ch1, ch2 = st.columns(2)
        tickers = [r["ticker"] for r in ranked]

        with ch1:
            st.markdown("#### Implied vs Historical CAGR")
            fig = go.Figure()
            fig.add_bar(
                name="Historical", x=tickers,
                y=[r.get("historical_cagr") or 0 for r in ranked],
                marker_color="#3b82f6", opacity=0.85,
            )
            fig.add_bar(
                name="Implied (market)", x=tickers,
                y=[r.get("implied_cagr") or 0 for r in ranked],
                marker_color="#f59e0b", opacity=0.85,
            )
            layout = dict(CHART_LAYOUT)
            layout.update(
                barmode="group",
                yaxis=dict(**CHART_LAYOUT.get("yaxis", {}), tickformat=".0%"),
                legend=dict(orientation="h", y=1.1, font=dict(size=10)),
                height=260,
            )
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True)

        with ch2:
            st.markdown("#### EV/EBITDA — Market vs Justified")
            fig2 = go.Figure()
            ev_vals   = [r.get("ev_ebitda") for r in ranked]
            just_vals = [r.get("justified_ev_ebitda") for r in ranked]
            fig2.add_scatter(
                name="Market EV/EBITDA", x=tickers, y=ev_vals,
                mode="markers+text",
                marker=dict(size=14, color="#f59e0b", symbol="diamond"),
                text=[f"{v:.1f}×" if v else "" for v in ev_vals],
                textposition="top center", textfont=dict(size=10),
            )
            fig2.add_scatter(
                name="Justified (Liberti)", x=tickers, y=just_vals,
                mode="markers+text",
                marker=dict(size=10, color="#10b981", symbol="circle"),
                text=[f"{v:.1f}×" if v else "" for v in just_vals],
                textposition="bottom center", textfont=dict(size=10),
            )
            for r in ranked:
                if r.get("ev_ebitda") and r.get("justified_ev_ebitda"):
                    fig2.add_shape(
                        type="line",
                        x0=r["ticker"], x1=r["ticker"],
                        y0=r["justified_ev_ebitda"], y1=r["ev_ebitda"],
                        line=dict(color="#27272a", width=1.5, dash="dot"),
                    )
            fig2.update_layout(
                **CHART_LAYOUT,
                legend=dict(orientation="h", y=1.1, font=dict(size=10)),
                height=260,
            )
            st.plotly_chart(fig2, use_container_width=True)

    # ──────────────────────────────────────────────────────────────────────────
    # TAB 2 — DEEP DIVE
    # ──────────────────────────────────────────────────────────────────────────
    elif active_view == "◉  Deep Dive":

        selected = st.session_state.active_ticker
        if not selected:
            st.info("Select a ticker from the sidebar to begin analysis.")
            return

        # Fetch all sections from API
        dcf_data  = fetch_dcf(selected)
        fund_data = fetch_fundamentals(selected)
        narr_data = fetch_narrative(selected)
        full      = fetch_ticker(selected)

        if not dcf_data:
            return

        er = full.get("1_economic_reality", {}) if full else {}
        val4 = full.get("4_valuation_synthesis", {}) if full else {}
        p2v = val4.get("phase2_valuation", {})

        strategic_context = er.get("strategic_context", {})
        contrarian_analysis = val4.get("contrarian_analysis", {})
        adj = p2v.get("dcf_adjustments", {}) or {}
        investment_thesis = val4.get("investment_thesis", {})
        pillar_scores = investment_thesis.get("pillar_scores", {})

        vc = er.get("value_chain", {}) or {}
        moat = er.get("moat", {}) or {}

        # Row from universe
        row = next((r for r in ranked if r["ticker"] == selected), {})

        st.markdown("<br>", unsafe_allow_html=True)

        # Header
        st.markdown(
            f'<div style="font-family:Syne,sans-serif;font-size:36px;'
            f'font-weight:800;letter-spacing:-1px;color:var(--text-color)">{selected}</div>',
            unsafe_allow_html=True,
        )

        m1, m2, m3, m4 = st.columns(4)
        conv = investment_thesis.get("conviction_score") if investment_thesis else None
        m1.metric("Conviction", f"{int(conv):+d} / 10" if conv is not None else "—",
                  delta=row.get("value_creation","").upper() or None)
        m2.metric("Base IV", money(dcf_data["base"]["intrinsic_per_share"]) if dcf_data.get("base") else "—",
                  delta=pct(dcf_data["base"]["margin_of_safety"]) if dcf_data.get("base") else None)
        m3.metric("ROIC", pct(fund_data.get("roic")) if fund_data else "—",
                  delta=f"WACC {pct(dcf_data.get('wacc'))}")
        m4.metric("Multiple Signal", SIGNAL_LABEL.get(row.get("multiple_signal",""), "—"),
                  delta=f"{row.get('ev_ebitda',0):.1f}× vs {row.get('justified_ev_ebitda',0):.1f}× justified"
                  if row.get("ev_ebitda") else None, delta_color="inverse")

        st.markdown("---")

        if pillar_scores:
            p_cols = st.columns(5)
            pillars = [
                ("Moat", pillar_scores.get("p1_moat", 0)),
                ("Health", pillar_scores.get("p2_health", 0)),
                ("Tailwind", pillar_scores.get("p3_tailwind", 0)),
                ("MoS", pillar_scores.get("p4_mos", 0)),
                ("Leadership", pillar_scores.get("p5_leadership", 0)),
            ]
            for i, (name, score) in enumerate(pillars):
                with p_cols[i]:
                    sc_val = int(score) if score is not None else 0
                    sc_color = "#10b981" if sc_val > 3 else "#f59e0b" if sc_val > 1 else "#ef4444"
                    st.markdown(
                        f'<div style="text-align:center;font-family:DM Mono,monospace;font-size:11px;color:#71717a;margin-bottom:4px">{name}</div>'
                        f'<div style="text-align:center;font-weight:700;font-size:18px;color:{sc_color}">{sc_val}</div>',
                        unsafe_allow_html=True
                    )
            st.markdown("<br>", unsafe_allow_html=True)

        left, right = st.columns([1, 1.6])

        with left:
            st.markdown("##### 3-Scenario DCF")
            scenarios = [
                ("BEAR", dcf_data.get("bear", {}), "#ef4444"),
                ("BASE", dcf_data.get("base", {}), "#f59e0b"),
                ("BULL", dcf_data.get("bull", {}), "#10b981"),
            ]
            max_iv = max(
                (s.get("intrinsic_per_share", 0) for _, s, _ in scenarios if s),
                default=1,
            ) * 1.1 or 1

            for name, scenario, color in scenarios:
                if scenario:
                    iv = scenario.get("intrinsic_per_share", 0) or 0
                    pct_width = min(iv / max_iv * 100, 100)
                    mos_val = scenario.get("margin_of_safety")
                    mos_str = f" ({mos_val:+.1%})" if mos_val else ""
                    st.markdown(
                        f'<div style="display:flex;justify-content:space-between;'
                        f'font-family:DM Mono,monospace;font-size:11px;margin-bottom:3px">'
                        f'<span style="color:{color}">{name}</span>'
                        f'<span style="color:#fafafa">${iv:,.0f}{mos_str}</span></div>'
                        f'<div style="background:#27272a;border-radius:3px;height:6px;margin-bottom:10px">'
                        f'<div style="width:{pct_width:.1f}%;height:100%;background:{color};border-radius:3px"></div></div>',
                        unsafe_allow_html=True,
                    )

            st.markdown("<br>", unsafe_allow_html=True)

            # Moat
            moat_score = moat.get("score") or row.get("moat")
            moat_color = "#f59e0b" if moat_score and moat_score >= 9 else "#10b981" if moat_score and moat_score >= 7 else "#ef4444"
            st.markdown("##### Moat")
            st.markdown(
                f'<div style="text-align:center;padding:8px 0">'
                f'<div style="font-family:Syne,sans-serif;font-size:52px;'
                f'font-weight:800;color:{moat_color};line-height:1">'
                f'{moat_score:.1f}</div>'
                f'<div style="font-family:DM Mono,monospace;font-size:10px;'
                f'color:#71717a;margin-top:4px">/ 10  ·  {row.get("value_creation","").upper()}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            
            # Moat Breakdown
            ca = "✅" if moat.get("cost_advantage") else "❌"
            nt = "✅" if moat.get("network_effects") else "❌"
            sw = "✅" if moat.get("switching_costs") else "❌"
            it = "✅" if moat.get("intangibles") else "❌"
            
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;font-family:DM Mono,monospace;font-size:11px;margin-top:8px">'
                f'<span>Cost Adv: {ca}</span><span>Network: {nt}</span>'
                f'<span>Switching: {sw}</span><span>Intangible: {it}</span></div>',
                unsafe_allow_html=True
            )
            if moat.get("evidence"):
                st.markdown(f'<div style="font-size:11px;color:#a1a1aa;margin-top:12px;line-height:1.4"><i>"{moat.get("evidence")}"</i></div>', unsafe_allow_html=True)

            # ROIC vs WACC
            r_roic = fund_data.get("roic") or row.get("roic") or 0
            r_wacc = dcf_data.get("wacc") or row.get("wacc") or 0.09
            spread = r_roic - r_wacc
            st.markdown(
                f'<div style="font-family:DM Mono,monospace;font-size:11px;'
                f'display:flex;flex-direction:column;gap:6px;margin-top:16px">'
                f'<div style="display:flex;justify-content:space-between">'
                f'<span style="color:#71717a">ROIC</span>'
                f'<span style="color:#10b981">{r_roic*100:.1f}%</span></div>'
                f'<div style="display:flex;justify-content:space-between">'
                f'<span style="color:#71717a">WACC</span>'
                f'<span style="color:#fafafa">{r_wacc*100:.1f}%</span></div>'
                f'<div style="display:flex;justify-content:space-between;'
                f'border-top:1px solid #27272a;padding-top:6px">'
                f'<span style="color:#71717a">Spread</span>'
                f'<span style="color:{"#10b981" if spread > 0 else "#ef4444"}">'
                f'{spread*100:+.1f}pp</span></div></div>',
                unsafe_allow_html=True,
            )

            # Value chain
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("##### Value Chain (Porter)")
            st.markdown(
                f'<div style="font-family:DM Mono,monospace;font-size:11px;'
                f'display:flex;flex-direction:column;gap:5px">'
                + (f'<div style="display:flex;justify-content:space-between">'
                   f'<span style="color:#71717a">Strategic Leverage</span>'
                   f'<span>{vc.get("strategic_leverage","—")}</span></div>' if vc.get("strategic_leverage") else "")
                + (f'<div style="display:flex;justify-content:space-between">'
                   f'<span style="color:#71717a">Power Ratio</span>'
                   f'<span>{vc.get("power_ratio","—")}</span></div>' if vc.get("power_ratio") is not None else "")
                + (f'<div style="display:flex;justify-content:space-between">'
                   f'<span style="color:#71717a">Upstream Leak</span>'
                   f'<span style="color:{"#ef4444" if vc.get("upstream_leak") else "#10b981"}">'
                   f'{"YES ⚠" if vc.get("upstream_leak") else "NO ✓"}</span></div>' if vc else "")
                + '</div>',
                unsafe_allow_html=True,
            )
            
            # Strategic Context
            if strategic_context:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("##### Strategic Context")
                sc = strategic_context
                
                rev_at_risk = sc.get("revenue_at_risk_percent")
                rev_at_risk_str = f"{rev_at_risk*100:.1f}%" if rev_at_risk is not None else "N/A"
                q_risk = sc.get("quality_of_growth_risk", False)
                def_rev = sc.get("deferred_revenue_trend", "N/A")
                t_haircut = sc.get("terminal_haircut", False)
                
                st.markdown(
                    f'<div style="font-family:DM Mono,monospace;font-size:11px;display:flex;flex-direction:column;gap:5px">'
                    f'<div style="display:flex;justify-content:space-between"><span style="color:#71717a">Rev at Risk</span><span>{rev_at_risk_str}</span></div>'
                    f'<div style="display:flex;justify-content:space-between"><span style="color:#71717a">Quality Risk</span><span style="color:{"#ef4444" if q_risk else "#10b981"}">{q_risk}</span></div>'
                    f'<div style="display:flex;justify-content:space-between"><span style="color:#71717a">Def Rev Trend</span><span>{def_rev}</span></div>'
                    f'<div style="display:flex;justify-content:space-between"><span style="color:#71717a">Terminal Haircut</span><span>{t_haircut}</span></div>'
                    f'</div>',
                    unsafe_allow_html=True
                )
                if sc.get("summary"):
                    st.markdown(f'<div style="font-size:11px;color:#a1a1aa;margin-top:12px;line-height:1.4"><i>"{sc.get("summary")}"</i></div>', unsafe_allow_html=True)


        with right:
            # Fundamentals
            st.markdown("##### Fundamentals — Phase 1 Cleaned")
            if fund_data:
                fm1, fm2, fm3, fm4 = st.columns(4)
                fm1.metric("Revenue", bn(fund_data.get("revenue_bn")))
                fm2.metric("EBITDA",  bn(fund_data.get("ebitda_bn")))
                fm3.metric("FCF",     bn(fund_data.get("fcf_bn")))
                fm4.metric("FCF Margin", pct(fund_data.get("fcf_margin"), 1) if fund_data.get("fcf_margin") else "—")

            st.markdown("<br>", unsafe_allow_html=True)

            # Reverse DCF chart
            st.markdown("##### Reverse DCF — Growth Priced In")
            rdcf = dcf_data.get("reverse_dcf") or {}
            impl  = rdcf.get("implied_cagr_10y") or 0
            hist  = rdcf.get("historical_cagr") or 0

            fig_cagr = go.Figure()
            fig_cagr.add_bar(
                x=["Historical CAGR", "Market Implied CAGR"],
                y=[hist, impl],
                marker_color=["#3b82f6", "#f59e0b"],
                text=[f"{hist:.1%}", f"{impl:.1%}"],
                textposition="outside", textfont=dict(size=11),
            )
            layout_cagr = dict(CHART_LAYOUT)
            layout_cagr.update(
                showlegend=False,
                yaxis=dict(**CHART_LAYOUT.get("yaxis", {}), tickformat=".0%"),
                height=200,
            )
            fig_cagr.update_layout(**layout_cagr)
            st.plotly_chart(fig_cagr, use_container_width=True)

            rdcf_sig = rdcf.get("signal", "")
            ratio_str = f"{impl/hist:.1f}×" if hist and hist > 0 else "N/A"
            st.markdown(
                f'<div style="background:#111113;border:1px solid #27272a;border-radius:6px;'
                f'padding:12px;font-family:DM Mono,monospace;font-size:11px;color:#a1a1aa;margin-top:-8px">'
                f'Market implies <span style="color:#f59e0b">{impl:.1%}</span> CAGR vs '
                f'historical <span style="color:#3b82f6">{hist:.1%}</span> → '
                f'<span style="color:{"#ef4444" if hist > 0 and impl/hist > 2 else "#f59e0b" if hist > 0 and impl/hist > 1.3 else "#10b981"}">'
                f'{ratio_str} historical</span>. '
                + signal_html(rdcf_sig, small=True) + '</div>',
                unsafe_allow_html=True,
            )

            # DCF Adjustments
            if adj:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("##### DCF Overrides & Adjustments")
                adj_html = '<div style="background:#111113;border:1px solid #27272a;border-radius:6px;padding:12px;font-family:DM Mono,monospace;font-size:11px;display:flex;flex-direction:column;gap:6px">'
                for k, v in adj.items():
                    if k != "rules" and v is not None:
                        # format value nicely
                        disp_v = f"{v:.4f}" if isinstance(v, float) and v < 1 and v > -1 else f"{v}"
                        adj_html += f'<div style="display:flex;justify-content:space-between"><span style="color:#71717a">{k}</span><span style="color:#fafafa">{disp_v}</span></div>'
                adj_html += '</div>'
                st.markdown(adj_html, unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # Narrative
            narrative = investment_thesis.get("narrative") if investment_thesis else None
            if narrative:
                st.markdown("##### Lead Agent Investment Thesis")
                st.markdown(
                    f'<div style="background:#111113;border:1px solid #27272a;'
                    f'border-radius:8px;padding:16px;font-size:13px;'
                    f'color:#a1a1aa;line-height:1.7">{narrative}</div>',
                    unsafe_allow_html=True,
                )

            # Contrarian View
            if contrarian_analysis:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("##### Contrarian Bear Case")
                ca = contrarian_analysis
                st.markdown(
                    f'<div style="background:rgba(239, 68, 68, 0.05);border:1px solid rgba(239, 68, 68, 0.2);'
                    f'border-radius:8px;padding:16px;font-size:12px;'
                    f'color:#fca5a5;line-height:1.6">'
                    f'<div style="margin-bottom:8px"><b>Bias Detected:</b> {ca.get("bias_detected", "None")}</div>'
                    f'<div><b>Bear Case:</b> {ca.get("bear_case_summary", "")}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )


    # ──────────────────────────────────────────────────────────────────────────
    # TAB 3 — FINANCIALS (deterministic detail dump from DB + DCFEngine)
    # ──────────────────────────────────────────────────────────────────────────
    elif active_view == "▦  Financials":

        selected = st.session_state.active_ticker
        if not selected:
            st.info("Select a ticker from the sidebar.")
            return

        bundle = fetch_financials_bundle(selected)
        if not bundle or bundle.get("error"):
            st.error(bundle.get("error") if bundle else "Failed to load financials.")
            return

        ident = bundle["identity"]
        inc   = bundle["income_statement"]
        bs    = bundle["balance_sheet"]
        ret   = bundle["returns_capital"]
        leases = bundle["lease_items"]

        def _bn(v, dp=2):
            if v is None: return "—"
            return f"${v/1e9:,.{dp}f}B"

        def _pct(v, dp=1):
            if v is None: return "—"
            return f"{v:.{dp}f}%" if abs(v) > 1.0 else f"{v*100:.{dp}f}%"

        def _num(v, dp=2):
            if v is None: return "—"
            return f"{v:,.{dp}f}"

        # ── Identity bar ────────────────────────────────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Ticker", ident["ticker"])
        c2.metric("Latest FY", f"FY{ident['fiscal_year']}")
        c3.metric("Quality", _num(ident['quality_score']) if ident['quality_score'] else "—")
        c4.metric("Errors / Warnings", f"{ident['error_count']} / {ident['warning_count']}")

        if ident["warnings"]:
            with st.expander(f"⚠ {len(ident['warnings'])} warnings"):
                for w in ident["warnings"]:
                    st.markdown(f"- {w}")
        if ident["errors"]:
            with st.expander(f"❌ {len(ident['errors'])} errors", expanded=True):
                for e in ident["errors"]:
                    st.markdown(f"- {e}")
        if bundle.get("bypass"):
            st.warning(f"DCF not run: {bundle['bypass']}")

        st.markdown("---")

        # ── Income statement + balance sheet (side-by-side) ─────────────────
        ls, rs = st.columns(2)
        with ls:
            st.markdown(f"#### Income Statement — FY{ident['fiscal_year']}")
            inc_rows = [
                ("Revenue",          _bn(inc["Revenue"])),
                ("COGS",             _bn(inc["COGS"])),
                ("Gross Margin",     _pct(inc["GrossMargin_Pct"])),
                ("R&D",              _bn(inc["RnD"])),
                ("SG&A",             _bn(inc["SGnA"])),
                ("Operating Income", _bn(inc["OperatingIncome"])),
                ("EBIT Margin",      _pct(inc["EBIT_Margin_Pct"])),
                ("EBITDA",           _bn(inc["EBITDA"])),
                ("EBITDA Margin",    _pct(inc["EBITDA_Margin_Pct"])),
                ("D&A",              _bn(inc["DepreciationAmortization"])),
                ("NOPAT",            _bn(inc["NOPAT"])),
                ("Net Income",       _bn(inc["NetIncome"])),
                ("Diluted EPS",      f"${_num(inc['DilutedEPS'])}" if inc["DilutedEPS"] else "—"),
                ("OperatingCF",      _bn(inc["OperatingCF"])),
                ("InvestingCF",      _bn(inc["InvestingCF"])),
                ("FinancingCF",      _bn(inc["FinancingCF"])),
                ("FCF",              _bn(inc["FCF"])),
                ("FCFF",             _bn(inc["FCFF"])),
                ("FCF Margin",       _pct(inc["FCF_Margin_Pct"])),
                ("CapEx",            _bn(inc["CapEx"])),
                ("Maintenance CapEx", _bn(inc["MaintenanceCapEx"])),
                ("Growth CapEx",     _bn(inc["GrowthCapEx"])),
            ]
            st.dataframe(pd.DataFrame(inc_rows, columns=["Metric", "Value"]),
                         hide_index=True, use_container_width=True)

        with rs:
            st.markdown(f"#### Balance Sheet — FY{ident['fiscal_year']}")
            bs_rows = [
                ("Total Assets",        _bn(bs["TotalAssets"])),
                ("Cash",                _bn(bs["Cash"])),
                ("Short-term Invest.",  _bn(bs["ShortTermInvestments"])),
                ("AR",                  _bn(bs["AccountsReceivable"])),
                ("Inventory",           _bn(bs["Inventory"])),
                ("PPE (net)",           _bn(bs["PPE_Net"])),
                ("PPE (gross)",         _bn(bs["PPE_Gross"])),
                ("Accum. Depreciation", _bn(bs["AccumulatedDepreciation"])),
                ("Total Liabilities",   _bn(bs["TotalLiabilities"])),
                ("Current Liabilities", _bn(bs["LiabilitiesCurrent"])),
                ("Short-term Debt",     _bn(bs["ShortTermDebt"])),
                ("Long-term Debt",      _bn(bs["LongTermDebt"])),
                ("AP",                  _bn(bs["AccountsPayable"])),
                ("Total Equity",        _bn(bs["TotalEquity"])),
                ("NWC",                 _bn(bs["NWC"])),
                ("Net Debt",            _bn(bs["NetDebt"])),
            ]
            st.dataframe(pd.DataFrame(bs_rows, columns=["Metric", "Value"]),
                         hide_index=True, use_container_width=True)

        st.markdown("---")

        # ── Returns / capital + lease items ────────────────────────────────
        ls2, rs2 = st.columns(2)
        with ls2:
            st.markdown("#### Returns & Capital Structure")
            ret_rows = [
                ("ROIC",            _pct(ret["ROIC"])),
                ("ROE",             _pct(ret["ROE"])),
                ("Invested Capital", _bn(ret["InvestedCapital"])),
                ("Diluted Shares",   _bn(ret["SharesDiluted"], dp=3) if ret["SharesDiluted"] else "—"),
                ("Basic Shares",     _bn(ret["SharesBasic"], dp=3) if ret["SharesBasic"] else "—"),
                ("Outstanding",      _bn(ret["SharesOutstanding"], dp=3) if ret["SharesOutstanding"] else "—"),
                ("Buybacks",        _bn(ret["Buybacks"])),
                ("SBC",             _bn(ret["SBC"])),
                ("Net Buyback after SBC", _bn(ret["NetBuyback_AfterSBC"])),
                ("SBC % of FCF",    _pct(ret["SBC_PctFCF"])),
                ("Dilution %",      _pct(ret["DilutionPct"])),
                ("Dividends Paid",  _bn(ret["DividendsPaid"])),
            ]
            st.dataframe(pd.DataFrame(ret_rows, columns=["Metric", "Value"]),
                         hide_index=True, use_container_width=True)

        with rs2:
            st.markdown("#### Lease Items")
            lease_rows = [
                ("ROU Asset (Operating)", _bn(leases["ROUAsset_Operating"])),
                ("ROU Asset (Finance)",   _bn(leases["ROUAsset_Finance"])),
                ("Lease Liab Operating",  _bn(leases["LeaseLiability_Operating_Total"])),
                ("Lease Liab Finance",    _bn(leases["LeaseLiability_Finance_Total"])),
                ("Lease Cost",            _bn(leases["LeaseCost"])),
            ]
            st.dataframe(pd.DataFrame(lease_rows, columns=["Metric", "Value"]),
                         hide_index=True, use_container_width=True)

        st.markdown("---")

        # ── Fiscal-year history ────────────────────────────────────────────
        st.markdown("#### Fiscal-Year History")
        hist = bundle["fiscal_history"]
        if hist:
            hist_df = pd.DataFrame([{
                "FY":         r["fiscal_year"],
                "Revenue ($B)":  (r["Revenue"]/1e9 if r["Revenue"] else None),
                "EBITDA ($B)":   (r["EBITDA"]/1e9 if r["EBITDA"] else None),
                "Net Income ($B)": (r["NetIncome"]/1e9 if r["NetIncome"] else None),
                "CapEx ($B)":    (r["CapEx"]/1e9 if r["CapEx"] else None),
                "FCF ($B)":      (r["FCF"]/1e9 if r["FCF"] else None),
                "ROIC (%)":      (r["ROIC"]*100 if r["ROIC"] else None),
                "Quality":       r["QualityScore"],
            } for r in hist])
            st.dataframe(
                hist_df.style.format({
                    "Revenue ($B)": "{:,.2f}", "EBITDA ($B)": "{:,.2f}",
                    "Net Income ($B)": "{:,.2f}", "CapEx ($B)": "{:,.2f}",
                    "FCF ($B)": "{:,.2f}", "ROIC (%)": "{:.1f}", "Quality": "{:.2f}",
                }, na_rep="—"),
                hide_index=True, use_container_width=True
            )

        # ── DCF section (only if we ran) ────────────────────────────────────
        if bundle["dcf_inputs"]:
            st.markdown("---")
            st.markdown("#### DCF Analysis")

            ls3, rs3 = st.columns([1, 1])
            with ls3:
                st.markdown("##### Inputs")
                dcfi = bundle["dcf_inputs"]
                inp_rows = [
                    ("Current Price",   f"${_num(dcfi['current_price'])}"),
                    ("Market Cap",      _bn(dcfi["market_cap"])),
                    ("Diluted Shares",  _bn(dcfi["shares_diluted"], dp=3) if dcfi["shares_diluted"] else "—"),
                    ("Risk-free Rate",  _pct(dcfi["risk_free_rate"])),
                    ("Beta",            _num(dcfi["beta"])),
                    ("WACC (base)",     _pct(dcfi["wacc_base"])),
                    ("Tax Rate",        _pct(dcfi["tax_rate"])),
                ]
                st.dataframe(pd.DataFrame(inp_rows, columns=["Metric", "Value"]),
                             hide_index=True, use_container_width=True)

            with rs3:
                st.markdown("##### Scenarios")
                scens = bundle["dcf_scenarios"]
                scen_rows = []
                for name in ("bull", "base", "bear"):
                    s = scens.get(name) or {}
                    if s:
                        scen_rows.append({
                            "Scenario":     s["name"],
                            "EV ($B)":      (s["EV"]/1e9 if s["EV"] else None),
                            "IPS":          s["IPS"],
                            "Upside (%)":   s["Upside_Pct"],
                            "WACC (%)":     (s["WACC"]*100 if s["WACC"] else None),
                            "g_term (%)":   (s["TerminalGrowth"]*100 if s["TerminalGrowth"] else None),
                            "TV%EV":        (s["TV_Pct_EV"]*100 if s["TV_Pct_EV"] else None),
                            "EV/EBITDA":    s["ImpliedEV_EBITDA"],
                        })
                if scen_rows:
                    st.dataframe(
                        pd.DataFrame(scen_rows).style.format({
                            "EV ($B)": "{:,.0f}", "IPS": "${:,.2f}",
                            "Upside (%)": "{:+.1f}%", "WACC (%)": "{:.2f}",
                            "g_term (%)": "{:.2f}", "TV%EV": "{:.1f}",
                            "EV/EBITDA": "{:.1f}x",
                        }, na_rep="—"),
                        hide_index=True, use_container_width=True
                    )

            # Projections
            projs = bundle["projections"]
            if projs:
                st.markdown("##### Base-case Projections")
                proj_df = pd.DataFrame([{
                    "Yr": p["year"], "FY": p["fiscal_year"],
                    "Revenue ($B)": p["revenue"]/1e9 if p["revenue"] else None,
                    "EBIT ($B)":    p["ebit"]/1e9 if p["ebit"] else None,
                    "NOPAT ($B)":   p["nopat"]/1e9 if p["nopat"] else None,
                    "CapEx ($B)":   p["capex"]/1e9 if p["capex"] else None,
                    "FCFF ($B)":    p["fcff"]/1e9 if p["fcff"] else None,
                    "PV(FCFF) ($B)": p["pv_fcff"]/1e9 if p["pv_fcff"] else None,
                } for p in projs])
                st.dataframe(
                    proj_df.style.format({
                        "Revenue ($B)": "{:,.1f}", "EBIT ($B)": "{:,.1f}",
                        "NOPAT ($B)": "{:,.1f}", "CapEx ($B)": "{:,.1f}",
                        "FCFF ($B)": "{:,.1f}", "PV(FCFF) ($B)": "{:,.1f}",
                    }, na_rep="—"),
                    hide_index=True, use_container_width=True
                )

            ls4, rs4 = st.columns(2)
            with ls4:
                st.markdown("##### Terminal Value (Base)")
                tv = bundle["terminal_value"]
                tv_rows = [
                    ("Gordon TV",            _bn(tv.get("gordon_tv"), dp=0)),
                    ("Reinvestment TV",      _bn(tv.get("reinvestment_tv"), dp=0)),
                    ("TV used",              _bn(tv.get("tv_used"), dp=0)),
                    ("PV of TV",             _bn(tv.get("pv_tv"), dp=0)),
                    ("TV % of EV",           _pct(tv.get("tv_pct_of_ev"))),
                    ("Implied Terminal EV/EBITDA", f"{tv.get('implied_tv_ebitda_multiple', 0):.1f}x" if tv.get("implied_tv_ebitda_multiple") else "—"),
                ]
                st.dataframe(pd.DataFrame(tv_rows, columns=["Metric", "Value"]),
                             hide_index=True, use_container_width=True)

            with rs4:
                st.markdown("##### Base Assumptions")
                a = bundle["assumptions"]
                a_rows = [
                    ("CAGR Y1-5",         _pct(a.get("revenue_cagr_y1_5"))),
                    ("CAGR Y6-10",        _pct(a.get("revenue_cagr_y6_10"))),
                    ("EBIT margin start", _pct(a.get("ebit_margin_current"))),
                    ("EBIT margin term.", _pct(a.get("ebit_margin_terminal"))),
                    ("CapEx % revenue",   _pct(a.get("capex_pct_revenue"))),
                    ("D&A % revenue",     _pct(a.get("da_pct_revenue"))),
                    ("NWC % revenue",     _pct(a.get("nwc_pct_revenue"))),
                    ("Tax rate",          _pct(a.get("tax_rate"))),
                    ("WACC",              _pct(a.get("wacc"))),
                    ("Terminal growth",   _pct(a.get("terminal_growth"))),
                    ("Terminal ROIC",     _pct(a.get("terminal_roic"))),
                    ("Base ROIC",         _pct(a.get("base_roic"))),
                ]
                st.dataframe(pd.DataFrame(a_rows, columns=["Metric", "Value"]),
                             hide_index=True, use_container_width=True)
                if a.get("justification"):
                    st.caption(a["justification"])

    # ──────────────────────────────────────────────────────────────────────────
    # TAB 4 — SCREENING
    # ──────────────────────────────────────────────────────────────────────────
    elif active_view == "◧  Screening":

        st.markdown("#### The 34-Metric Unified Screen")
        st.markdown(
            '<span style="font-family:DM Mono,monospace;font-size:11px;color:#71717a">'
            "Graham + Lynch + Malkiel + Liberti · 34 metrics · "
            "● Pass  ● Flag  ● Fail</span>",
            unsafe_allow_html=True,
        )

        screen_ticker = st.session_state.active_ticker

        if not screen_ticker:
            st.info("Select a ticker from the sidebar to begin analysis.")
        else:
            card = fetch_screening(screen_ticker)
            if card:
                pa, fl, fa, av = card["passes"], card["flags"], card["fails"], card["available"]

                # Headline Score Display
                pass_pct = pa / av if av else 0
                color = "#10b981" if pass_pct > 0.75 else "#f59e0b" if pass_pct > 0.50 else "#ef4444"
                st.markdown(f"""
                    <div style='font-size:48px; font-weight:700; color:{color}; line-height:1'>{pa}/{av}</div>
                    <div style='color:#71717a; font-family:DM Mono,monospace; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; margin-top:4px'>Metrics Passing</div>
                """, unsafe_allow_html=True)

                st.markdown("<br>", unsafe_allow_html=True)

                # Group by category
                by_cat: Dict[str, list] = {}
                for m in card["metrics"]:
                    by_cat.setdefault(m["category"], []).append(m)

                SIGNAL_COLORS = {
                    "✓": "#10b981", # Green (Pass)
                    "⚠": "#f59e0b", # Amber (Flag)
                    "✗": "#ef4444", # Red (Fail)
                    "—": "#6c757d", # Grey (N/A)
                }

                for cat, metrics in by_cat.items():
                    st.markdown(f"**{cat}**")
                    for m in metrics:
                        sig = m["signal"]
                        dot = {"✓": "🟢", "⚠": "🟡", "✗": "🔴", "—": "⚪"}.get(sig, "⚪")
                        grade_color = SIGNAL_COLORS.get(sig, "#6c757d")
                        
                        disp_val = m.get("display_value", f"{m['value']:.2f}" if m["value"] is not None else "N/A")
                        note_html = f"<div style='font-size:11px; color:#6c757d; font-style:italic; margin-top:2px'>← {m.get('note', '')}</div>" if m.get("note") else ""
                        
                        st.markdown(f"""
                            <div style='margin-bottom:8px; padding-bottom:6px; border-bottom:1px solid #1c1c1f'>
                                <div style='display:flex; justify-content:space-between; align-items:baseline'>
                                    <span>
                                        {dot} <span style='font-size:13px; font-weight:500; margin-left:4px'>{m["name"]}</span>
                                        <span style='font-family:DM Mono,monospace; font-size:10px; color:#52525b; margin-left:8px'>[{m["authority"]}]</span>
                                    </span>
                                    <span style='font-family:DM Mono,monospace; font-size:15px; font-weight:700; color:{grade_color}'>{disp_val}</span>
                                </div>
                                <div style='font-size:11px; color:#71717a; margin-left:26px; margin-top:2px'>{m.get("threshold", "")}</div>
                                <div style='margin-left:26px'>{note_html}</div>
                            </div>
                        """, unsafe_allow_html=True)
                    st.markdown("<br>", unsafe_allow_html=True)

    # ──────────────────────────────────────────────────────────────────────────
    # TAB 5 — CONSTITUTION
    # ──────────────────────────────────────────────────────────────────────────
    elif active_view == "◨  Constitution":
        st.markdown("#### Constitution Compliance")
        st.markdown(
            '<span style="font-family:DM Mono,monospace;font-size:11px;color:#71717a">'
            "Framework rules enforcement — two FAILs = do not deploy capital.</span>",
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)

        for r in ranked:
            ticker = r["ticker"]
            narr = fetch_narrative(ticker)
            checks = narr.get("constitution_checks", []) if narr else []
            conv   = r.get("conviction")
            sig    = SIGNAL_LABEL.get(r.get("multiple_signal", ""), "")

            fail_count = sum(1 for c in checks if "FAIL" in str(c) or "❌" in str(c))
            warn_count = sum(1 for c in checks if "CAUTION" in str(c) or "⚠" in str(c))
            pass_count = len(checks) - fail_count - warn_count

            with st.expander(
                f"{ticker}  —  {pass_count}✅ {warn_count}⚠️ {fail_count}❌  "
                f"| Conv {int(conv):+d}  |  {sig}",
                expanded=(fail_count == 0 and ticker in ["MSFT", "CNC"])
            ):
                if not checks:
                    st.markdown(
                        f'<div style="font-family:DM Mono,monospace;font-size:11px;color:#52525b">'
                        f'No pipeline checks found. Re-run pipeline: '
                        f'`python main.py --ticker {ticker}`</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    for check in checks:
                        is_pass = "PASS" in str(check) or "✅" in str(check)
                        is_warn = "CAUTION" in str(check) or "⚠" in str(check)
                        bg     = "rgba(16,185,129,.06)"  if is_pass else "rgba(245,158,11,.06)"  if is_warn else "rgba(239,68,68,.06)"
                        border = "rgba(16,185,129,.2)"   if is_pass else "rgba(245,158,11,.2)"   if is_warn else "rgba(239,68,68,.2)"
                        icon   = "✅" if is_pass else "⚠️" if is_warn else "❌"
                        st.markdown(
                            f'<div style="background:{bg};border:1px solid {border};'
                            f'border-radius:6px;padding:10px 14px;margin-bottom:8px;'
                            f'font-family:DM Mono,monospace;font-size:11px;color:#a1a1aa;'
                            f'display:flex;gap:10px">'
                            f'<span style="font-size:16px">{icon}</span>'
                            f'<span>{str(check)[:150]}</span></div>',
                            unsafe_allow_html=True,
                        )

    # ──────────────────────────────────────────────────────────────────────────
    # ──────────────────────────────────────────────────────────────────────────
    # TAB 6 — THESIS BUILDER
    # ──────────────────────────────────────────────────────────────────────────
    elif active_view == "📝  Thesis Builder":
        st.markdown("#### Interactive Thesis Builder")
        st.markdown(
            '<span style="font-family:DM Mono,monospace;font-size:11px;color:#71717a">'
            "Liberti Framework Section 16.2 · Seven Required Components</span>",
            unsafe_allow_html=True,
        )

        thesis_ticker = st.session_state.active_ticker

        if not thesis_ticker:
            st.info("Select a ticker from the sidebar to begin analysis.")
        else:
            # Fetch context and existing thesis
            report = fetch_ticker(thesis_ticker)
            if report:
                snap_mos = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {}).get("three_scenario_dcf", {}).get("base", {}).get("margin_of_safety", 0)
                snap_iv = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {}).get("three_scenario_dcf", {}).get("base", {}).get("intrinsic_per_share", 0)
                snap_price = snap_iv / (1 + snap_mos) if (1 + snap_mos) != 0 else 0
                ps = report.get("4_valuation_synthesis", {}).get("investment_thesis", {}).get("pillar_scores", {})
                
                # Fetch existing thesis draft
                try:
                    existing = httpx.get(f"{API_BASE}/ticker/{thesis_ticker}/thesis", timeout=5).json()
                except Exception:
                    existing = {}

                # Extract AI-assisted drafts
                narrative = report.get("4_valuation_synthesis", {}).get("investment_thesis", {}).get("narrative", "")
                moat_draft = extract_moat_from_narrative(narrative)
                econ_draft = extract_econ_from_narrative(narrative)

                if existing and existing.get("version"):
                    st.link_button(f"⬇ Download Current PDF (v{existing.get('version')})", f"{API_BASE}/ticker/{thesis_ticker}/thesis/pdf")

                # Auto-Populated Metrics
                st.markdown("##### 1. Pipeline Math (Auto-Populated)")
                c1, c2, c3 = st.columns(3)
                c1.metric("Base IV", f"${snap_iv:.2f}", f"{snap_mos:+.1%} MoS")
                spread = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {}).get("multiple_decomposition", {}).get("roic_wacc_spread", 0)
                c2.metric("ROIC-WACC Spread", f"{spread*100:+.1f}pp")
                c3.metric("Entry Price", f"${snap_price:.2f}")

                st.markdown("<br>", unsafe_allow_html=True)

                with st.form("thesis_form", clear_on_submit=False):
                    st.markdown("##### 2. AI-Assisted Assessment")
                    st.markdown("<span style='font-size:12px; color:#a1a1aa; font-style:italic'>Pre-filled from LLM synthesis. Edit as needed to confirm.</span>", unsafe_allow_html=True)
                    
                    moat_score = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {}).get("multiple_decomposition", {}).get("moat_score", "")
                    st.markdown(f"<div style='font-size:12px; color:#f59e0b; margin-bottom:4px'>Context: Pipeline Moat Score = {moat_score}</div>", unsafe_allow_html=True)
                    moat_powers = st.text_area("Moat Assessment — 7 Powers *", value=existing.get("moat_powers", moat_draft), height=100)
                    
                    cagr_impl = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {}).get("reverse_dcf", {}).get("implied_cagr_10y", 0)
                    cagr_hist = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {}).get("reverse_dcf", {}).get("historical_cagr", 0)
                    ratio = (cagr_impl / cagr_hist) if cagr_hist else 0
                    st.markdown(f"<div style='font-size:12px; color:#f59e0b; margin-bottom:4px'>Context: Implied vs Hist CAGR Ratio = {ratio:.1f}x</div>", unsafe_allow_html=True)
                    unit_econ = st.text_area("Unit Economics / TAM *", value=existing.get("unit_economics", econ_draft), height=100)

                    st.markdown("<br>", unsafe_allow_html=True)
                    st.markdown("##### 3. Analyst Judgment")
                    st.markdown("<span style='font-size:12px; color:#a1a1aa; font-style:italic'>Core conviction parameters. Manual input required.</span>", unsafe_allow_html=True)
                    
                    st.markdown(f"<div style='font-size:12px; color:#f59e0b; margin-bottom:4px'>Context: Stage: {ps.get('lifecycle_stage','')}</div>", unsafe_allow_html=True)
                    one_sentence = st.text_area("One-Sentence Thesis *", placeholder="Why this company, why now, why at this price...", value=existing.get("one_sentence", ""))

                    p2_r = ps.get("p2_health_reasons", [])
                    p3_r = ps.get("p3_tailwind_reasons", [])
                    st.markdown(f"<div style='font-size:12px; color:#f59e0b; margin-bottom:4px'>Context: P2 Health {p2_r[0] if p2_r else ''} | P3 Tailwind {p3_r[0] if p3_r else ''}</div>", unsafe_allow_html=True)
                    assump1 = st.text_area("Assumption 1 *", placeholder="Specific metric that must remain true...", value=existing.get("assumption_1", ""))
                    assump2 = st.text_area("Assumption 2 *", value=existing.get("assumption_2", ""))
                    assump3 = st.text_area("Assumption 3 *", value=existing.get("assumption_3", ""))

                    conf_12m = st.text_area("12-Month Confirmation Signal *", value=existing.get("confirmation_12m", ""))

                    fails = report.get("3_constitution_checks", {}).get("fails", [])
                    fail_str = " | ".join(fails) if fails else "None"
                    st.markdown(f"<div style='font-size:12px; color:#ef4444; font-weight:700; margin-bottom:4px'>Current Constitution Fails: {fail_str}</div>", unsafe_allow_html=True)
                    falsification = st.text_area("Pre-Committed Exit Trigger *", placeholder="Exit immediately if: (a)...", value=existing.get("falsification", ""))

                    submitted = st.form_submit_button("Save & Export PDF", type="primary")

                if submitted:
                    payload = {
                        "one_sentence": one_sentence,
                        "assumption_1": assump1,
                        "assumption_2": assump2,
                        "assumption_3": assump3,
                        "confirmation_12m": conf_12m,
                        "falsification": falsification,
                        "moat_powers": moat_powers,
                        "unit_economics": unit_econ
                    }
                    with st.spinner("Saving thesis and generating PDF..."):
                        try:
                            resp = httpx.post(f"{API_BASE}/ticker/{thesis_ticker}/thesis", json=payload, timeout=15)
                            if resp.status_code == 200:
                                st.success(f"Thesis v{resp.json()['version']} saved successfully!")
                                
                                # Inline PDF Download
                                try:
                                    pdf_resp = httpx.get(f"{API_BASE}/ticker/{thesis_ticker}/thesis/pdf", timeout=10)
                                    if pdf_resp.status_code == 200:
                                        st.download_button("⬇ Download PDF Brief", pdf_resp.content, file_name=f"{thesis_ticker}_Thesis.pdf", mime="application/pdf")
                                except Exception as e:
                                    st.error("Failed to fetch PDF")
                            else:
                                st.error(f"Error saving thesis: {resp.json().get('detail', 'Unknown error')}")
                        except Exception as e:
                            st.error(f"API Error: {e}")

            # Version History
            st.markdown("<br>", unsafe_allow_html=True)
            with st.expander("Version History"):
                try:
                    history = httpx.get(f"{API_BASE}/ticker/{thesis_ticker}/thesis/history", timeout=5).json()
                    if history:
                        for h in history:
                            st.markdown(f"**v{h['version']}** ({h['created_at'][:10]}): {h['one_sentence']}")
                            st.markdown("---")
                    else:
                        st.info("No previous versions found.")
                except Exception:
                    st.info("Could not fetch version history.")

    # ──────────────────────────────────────────────────────────────────────────
    # TAB 7 — REPORTS
    # ──────────────────────────────────────────────────────────────────────────
    elif active_view == "◩  Reports":
        report_ticker = st.session_state.active_ticker
        
        if not report_ticker:
            st.info("Select a ticker from the sidebar to begin analysis.")
        else:
            col_dl1, col_dl2, col_dl3, col_dl4, col_dl5 = st.columns(5)
            
            # Download buttons — fetch from API
            with col_dl1:
                html_bytes = httpx.get(f"{API_BASE}/ticker/{report_ticker}/report/html", timeout=10).content
                st.download_button("⬇ HTML Report", html_bytes,
                                   file_name=f"{report_ticker}_Executive_Report.html",
                                   mime="text/html", use_container_width=True)
            with col_dl2:
                exec_bytes = httpx.get(f"{API_BASE}/ticker/{report_ticker}/report/executive", timeout=10).content
                st.download_button("⬇ Executive MD", exec_bytes,
                                   file_name=f"{report_ticker}_Executive_Report.md",
                                   mime="text/markdown", use_container_width=True)
            with col_dl3:
                det_bytes = httpx.get(f"{API_BASE}/ticker/{report_ticker}/report/detailed", timeout=10).content
                st.download_button("⬇ Detailed MD", det_bytes,
                                   file_name=f"{report_ticker}_Detailed_Report.md",
                                   mime="text/markdown", use_container_width=True)
            with col_dl4:
                json_bytes = httpx.get(f"{API_BASE}/ticker/{report_ticker}", timeout=10).content
                st.download_button("⬇ Raw JSON", json_bytes,
                                   file_name=f"{report_ticker}_report.json",
                                   mime="application/json", use_container_width=True)
            with col_dl5:
                try:
                    dcf_resp = httpx.get(f"{API_BASE}/ticker/{report_ticker}/report/dcf_excel", timeout=10)
                    if dcf_resp.status_code == 200:
                        st.download_button("⬇ DCF Excel", dcf_resp.content,
                                           file_name=f"{report_ticker}_DCF_Model.xlsx",
                                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
                    else:
                        st.download_button("⬇ DCF Excel", b"", disabled=True, use_container_width=True)
                except Exception:
                    st.download_button("⬇ DCF Excel", b"", disabled=True, use_container_width=True)

            # Render HTML inline
            st.markdown("---")
            st.markdown(f"#### {report_ticker} Executive Report")
            html_content = html_bytes.decode("utf-8")
            st.components.v1.html(html_content, height=900, scrolling=True)


if __name__ == "__main__":
    main()
