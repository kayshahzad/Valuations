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
from typing import Any, Dict, List, Optional

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
    """Make a GET request to the FastAPI backend.

    A 404 means the ticker hasn't had its agent pipeline run yet (pending
    tickers don't have a `_report.json`); we return `None` silently so
    downstream renderers can fall back to placeholder content without a
    red error banner. Other HTTP errors and connection failures still
    surface to the user.
    """
    try:
        r = httpx.get(f"{API_BASE}{path}", timeout=TIMEOUT)
        if r.status_code == 404:
            return None
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


# Short TTL — staleness becomes interesting immediately after a dashboard
# submit; we want the banner to reflect current state without forcing
# manual cache clears.
@st.cache_data(ttl=30)
def fetch_thesis_staleness(ticker: str):
    return api_get(f"/ticker/{ticker}/thesis_synthesis/staleness")


def render_thesis_staleness_banner(ticker: str, key_suffix: str = "") -> None:
    """Show a staleness banner with a 'Refresh thesis' button when the
    current dashboard fingerprint differs from the thesis's stamped one.

    Uses a session-state defer pattern so the click → rerun → render
    full-width progress → POST flow gives the user clear visual feedback
    instead of a tiny spinner buried in a side column.

    `key_suffix` is appended to the widget keys so the same banner can
    render on multiple tabs without Streamlit duplicate-key errors.
    """
    pending_key = f"thesis_refresh_pending_{ticker}"
    result_key  = f"thesis_refresh_result_{ticker}"

    # Pass 2 — refresh was queued on a prior click; run the POST now so
    # the page below renders normally first (visible st.status above it),
    # not blocked behind a hidden spinner.
    if st.session_state.get(pending_key):
        st.session_state[pending_key] = False
        with st.status(
            "🔄 Refreshing thesis with current dashboard state…",
            expanded=True,
        ) as status:
            status.write("Loading cached upstream outputs from serving JSON…")
            status.write("Computing fresh dashboard projection (DuckDB read)…")
            status.write("Calling thesis_synthesizer (one LLM call, ≈30 s)…")
            try:
                resp = httpx.post(
                    f"{API_BASE}/ticker/{ticker}/thesis_synthesis/refresh",
                    timeout=120,
                )
                if resp.status_code == 200:
                    body = resp.json()
                    status.update(
                        label=(
                            f"✓ Thesis refreshed (agent_runs v"
                            f"{body.get('agent_runs_version', '?')})"
                        ),
                        state="complete",
                    )
                    st.session_state[result_key] = ("ok", "Thesis refreshed.")
                    fetch_ticker.clear()
                    fetch_thesis_staleness.clear()
                    st.rerun()
                else:
                    try:
                        detail = resp.json().get("detail", resp.text)
                    except Exception:
                        detail = resp.text or f"HTTP {resp.status_code}"
                    status.update(
                        label=f"Refresh failed (HTTP {resp.status_code})",
                        state="error",
                    )
                    status.write(f"**Detail:** {detail}")
                    if resp.status_code == 503 and "GOOGLE_API_KEY" in detail:
                        status.write(
                            "Set `GOOGLE_API_KEY` in the API server "
                            "environment and restart, then retry. The "
                            "existing thesis on disk was NOT modified."
                        )
                    st.session_state[result_key] = ("error", detail)
            except httpx.TimeoutException:
                status.update(
                    label="Refresh timed out (>120 s)",
                    state="error",
                )
                status.write(
                    "The synthesizer didn't return within the timeout. "
                    "The existing thesis on disk was NOT modified. "
                    "If this persists, run the full pipeline instead."
                )
                st.session_state[result_key] = ("error", "timeout")
            except Exception as exc:
                status.update(label=f"Refresh error: {exc}", state="error")
                st.session_state[result_key] = ("error", str(exc))
        return

    try:
        st_state = fetch_thesis_staleness(ticker)
    except Exception:
        return
    if not st_state or not st_state.get("thesis_present"):
        return
    if not st_state.get("is_stale"):
        return

    col_banner, col_btn = st.columns([4, 1])
    with col_banner:
        st.warning(
            "**Thesis is stale** — the qualitative dashboard has changed "
            "since this thesis was generated. Refresh to incorporate the "
            "latest assessments (≈30 s, one LLM call).",
            icon="⚠️",
        )
    with col_btn:
        if st.button(
            "🔄 Refresh thesis",
            key=f"refresh_thesis_btn_{ticker}_{key_suffix}",
            use_container_width=True,
            type="primary",
        ):
            # Pass 1 — defer the POST to the next rerun so the full-width
            # st.status above can render before the script blocks on the
            # synthesizer call. Without this defer, the spinner sat in a
            # tiny column and the page below appeared to "go blank."
            st.session_state[pending_key] = True
            st.rerun()

@st.cache_data(ttl=300)
def fetch_health():
    return api_get("/health")

@st.cache_data(ttl=120)
def fetch_qualitative(ticker: str):
    """All 19 qualitative dimensions for a ticker. 2-min TTL because
    HITL submissions need to appear quickly without a manual refresh."""
    return api_get(f"/ticker/{ticker}/qualitative")

@st.cache_data(ttl=120)
def fetch_qualitative_categories(ticker: str):
    return api_get(f"/qualitative/categories/{ticker}")

def trigger_qualitative_recompute(ticker: str):
    """POST to recompute deterministic dimensions and invalidate the
    fetch caches so the UI reflects the new state on next render."""
    try:
        r = httpx.post(f"{API_BASE}/qualitative/recompute/{ticker}", timeout=30)
        r.raise_for_status()
        fetch_qualitative.clear()
        fetch_qualitative_categories.clear()
        return r.json()
    except Exception as e:
        st.error(f"Recompute failed: {e}")
        return None


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


def _run_pipeline_subprocess(ticker: str) -> None:
    """
    Spawn `python3 main.py --ticker {ticker}` and stream output to a
    Streamlit progress UI. Stores result in session_state for sidebar render.
    On completion, clears the API and financials caches so the next view
    refresh picks up the new report.
    """
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent
    cmd = [sys.executable, "main.py", "--ticker", ticker]
    env = {**os.environ, "PYTHONPATH": str(repo_root)}

    progress_container = st.empty()
    log_lines: list[str] = []
    started = time.time()

    progress_container.info(f"▶ Running pipeline for {ticker}…")

    proc = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    with st.expander("Live pipeline output", expanded=True):
        log_box = st.empty()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                log_lines.append(line.rstrip("\n"))
                # Render a tail of the log to keep the box manageable
                log_box.code("\n".join(log_lines[-60:]), language="text")
        finally:
            proc.wait()

    elapsed = time.time() - started
    rc = proc.returncode

    if rc == 0:
        progress_container.success(f"✓ Pipeline finished in {elapsed:.1f}s")
        # Bust caches so subsequent views show the freshly written report
        try:
            fetch_ticker.clear()
            fetch_dcf.clear()
            fetch_fundamentals.clear()
            fetch_screening.clear()
            fetch_narrative.clear()
            fetch_financials_bundle.clear()
        except Exception:
            pass
    else:
        progress_container.error(f"✗ Pipeline failed (exit code {rc}) after {elapsed:.1f}s")

    st.session_state.last_pipeline_run = {
        "ticker": ticker,
        "returncode": rc,
        "elapsed_s": elapsed,
        "log_tail": "\n".join(log_lines[-40:]),
    }


def _run_add_ticker_pipeline_ui(ticker_input: str) -> None:
    """
    Drive the `add_ticker_pipeline` orchestrator from the Streamlit UI:
    stream step updates into a status panel, render the SEC + FMP
    validation tables inline, and stash a summary into `session_state`
    for the sidebar.
    """
    import time
    from aletheia.ui.add_ticker_pipeline import (
        run_add_ticker_pipeline, StepUpdate, PipelineResult,
    )

    started = time.time()
    final: Optional["PipelineResult"] = None
    step_log: list[str] = []

    with st.status(f"Adding {ticker_input.upper()}…", expanded=True) as status:
        for evt in run_add_ticker_pipeline(ticker_input):
            if isinstance(evt, StepUpdate):
                glyph = {"running": "▷", "ok": "✓", "warning": "⚠", "error": "✗"}.get(evt.status, "•")
                step_log.append(f"{glyph} **{evt.step}** — {evt.message}")
                st.markdown(f"{glyph} **{evt.step}** — {evt.message}")
                if evt.status == "running":
                    pass
                elif evt.status == "error":
                    status.update(label=f"Failed on {evt.step}", state="error")
            elif isinstance(evt, PipelineResult):
                final = evt

        elapsed = time.time() - started
        if final and final.success:
            status.update(label=f"✓ {final.ticker} added in {elapsed:.1f}s", state="complete")
        else:
            status.update(label=f"✗ Pipeline did not complete", state="error")

    # Render validation results inline so the user sees them immediately
    if final and final.success:
        st.markdown(f"### Validation — {final.ticker} FY{final.fiscal_year}")
        cols = st.columns(2)
        with cols[0]:
            st.markdown("**SEC XBRL** (raw bottom-line fields)")
            _render_validation_table(final.sec_validation, source="sec")
        with cols[1]:
            st.markdown("**FMP** (statements + derived ratios)")
            _render_validation_table(final.fmp_validation, source="fmp")

        # Bust the API caches so the new ticker shows up in selectors etc.
        try:
            fetch_universe.clear()
            fetch_health.clear()
            fetch_ticker.clear()
            fetch_dcf.clear()
            fetch_fundamentals.clear()
            fetch_screening.clear()
        except Exception:
            pass

    # Stash a compact summary for the sidebar expander
    st.session_state.last_add_ticker = {
        "ticker": (final.ticker if final else ticker_input.upper()),
        "success": bool(final and final.success),
        "elapsed_s": elapsed,
        "summary_md": "\n\n".join(step_log[-12:]) if step_log else "_no log_",
    }

    # Stash the full pipeline result + step log so the Quality Report screen
    # can render them in detail. Auto-switch the active ticker + view to land
    # the user on the report immediately.
    if final and final.success:
        st.session_state.last_add_ticker_full = {
            "ticker": final.ticker,
            "success": True,
            "elapsed_s": elapsed,
            "fiscal_year": final.fiscal_year,
            "step_log": step_log,
            "sec_validation": final.sec_validation,
            "fmp_validation": final.fmp_validation,
        }
        st.session_state.active_ticker = final.ticker
        st.session_state.active_view = "◊  Quality Report"
        st.info(
            f"**{final.ticker}** added. Switched view to **Quality Report** — "
            "scroll down to see the full validation breakdown."
        )


def _render_validation_table(payload: Optional[Dict[str, Any]], source: str) -> None:
    """Render either the SEC or FMP validation result as a compact table."""
    if not payload:
        st.caption("_no result_")
        return
    if payload.get("error"):
        st.warning(payload["error"])
        return

    # Build a unified row list with section context.
    if source == "sec":
        rows = [{"section": "raw", **r} for r in (payload.get("rows") or [])]
    else:
        rows = []
        for sect in ("income", "balance", "cashflow", "derived"):
            for r in (payload.get(sect) or []):
                rows.append({"section": sect, **r})

    if not rows:
        st.caption("_no rows_")
        return

    table = pd.DataFrame([
        {
            "section": r.get("section"),
            "metric":  r.get("label"),
            "value":   r.get("ours"),
            "ref":     r.get("sec") if source == "sec" else r.get("fmp"),
            "drift":   (f"{r['drift']*100:+.2f}%"
                        if isinstance(r.get("drift"), (int, float)) and r["drift"] != float("inf")
                        else "—"),
            "flag":    r.get("flag"),
        }
        for r in rows
    ])
    st.dataframe(table, hide_index=True, use_container_width=True)


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

    # ── Fetch universe ────────────────────────────────────────────────────────
    # The `/universe` endpoint returns the full union of ready + pending +
    # not-ingested tickers (see api_main.py). We use it as the source of
    # truth for the sidebar selector instead of the older `tickers_available`
    # list from `/health` (which only counts tickers with *_report.json
    # files and would hide pending tickers from the picker).
    universe_data = fetch_universe()
    if not universe_data:
        return
    ranked = universe_data.get("ranked", [])
    df = pd.DataFrame(ranked)

    # Sidebar selector lists every ticker, with a status icon prefix so the
    # analyst can see at a glance which are ready vs. pending agent-run.
    available = [r["ticker"] for r in ranked]
    status_by_ticker = {r["ticker"]: r.get("agents_status", "ready") for r in ranked}

    # ── Global State Initialization ───────────────────────────────────────────
    # Note: '🤖 Agents' tab removed — agent outputs are now surfaced on Deep
    # Dive (lead thesis, contrarian, value chain, moat, strategic context all
    # render there), and agent runs can be triggered from the Universe tab's
    # ▶ Run agents footer or the sidebar's per-ticker pipeline button.
    views = ["▷  Dashboard", "◈  Universe", "◉  Deep Dive", "▦  Financials", "◇  Scenarios", "◧  Screening", "◨  Constitution", "◭  Qualitative", "📝  Thesis", "◩  Reports", "◊  Quality Report"]
    if "active_ticker" not in st.session_state:
        st.session_state.active_ticker = available[0] if available else None
    if "active_view" not in st.session_state:
        st.session_state.active_view = views[0]

    # ── Sidebar Global Selector ───────────────────────────────────────────────
    st.sidebar.markdown("### 🎯 Target Company")
    current_index = available.index(st.session_state.active_ticker) if st.session_state.active_ticker in available else 0
    _STATUS_ICON = {"ready": "🟢", "pending": "🟡", "not_ingested": "⚪"}

    def _format_ticker(t: str) -> str:
        return f"{_STATUS_ICON.get(status_by_ticker.get(t, 'ready'), '·')}  {t}"

    st.session_state.active_ticker = st.sidebar.selectbox(
        "Select Ticker",
        options=available,
        index=current_index,
        format_func=_format_ticker,
        label_visibility="collapsed",
        help="🟢 ready (agents complete) · 🟡 pending agents · ⚪ not ingested",
    )

    if st.session_state.active_ticker:
        report = fetch_ticker(st.session_state.active_ticker)
        if report:
            ps = report.get("4_valuation_synthesis", {}).get("investment_thesis", {}).get("pillar_scores", {})
            mos = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {}).get("three_scenario_dcf", {}).get("base", {}).get("margin_of_safety", 0)
            mos_pct = f"{mos:+.1%}" if mos else "—"
            mos_color = "#10b981" if (mos is not None and mos > 0) else "#ef4444" if (mos is not None and mos < -0.1) else "#f59e0b"
            
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

    # ── Sidebar Run Pipeline ─────────────────────────────────────────────────
    if st.session_state.active_ticker:
        st.sidebar.markdown("<hr style='margin: 16px 0'>", unsafe_allow_html=True)
        run_label = f"▶ Run pipeline for {st.session_state.active_ticker}"
        if st.sidebar.button(run_label, use_container_width=True, key="run_pipeline_btn"):
            _run_pipeline_subprocess(st.session_state.active_ticker)
        if "last_pipeline_run" in st.session_state:
            last = st.session_state.last_pipeline_run
            if last["ticker"] == st.session_state.active_ticker:
                if last["returncode"] == 0:
                    st.sidebar.success(
                        f"✓ Pipeline ran ({last['elapsed_s']:.1f}s). "
                        "Refresh the active view to see new outputs."
                    )
                else:
                    st.sidebar.error(f"✗ Exit code {last['returncode']}")
                with st.sidebar.expander("Pipeline log (tail)", expanded=False):
                    st.code(last["log_tail"], language="text")

    # ── Sidebar: validation legend ────────────────────────────────────────────
    st.sidebar.markdown("<hr style='margin: 16px 0'>", unsafe_allow_html=True)
    with st.sidebar.expander("ℹ︎ Visual legend (badges, status, methods)", expanded=False):
        from aletheia.ui.validation_badge import render_full_legend
        render_full_legend()

    # Add Ticker control consolidated into the Universe tab — see top of
    # the ◈ Universe view. Removed from sidebar to avoid duplicate entry
    # points and keep ticker-lifecycle actions next to the universe table
    # they affect.

    # ── Sidebar Top Investable ────────────────────────────────────────────────
    st.sidebar.markdown("<hr style='margin: 16px 0'>", unsafe_allow_html=True)
    st.sidebar.markdown("### 🏆 Top Investable")

    if not df.empty and "base_mos" in df.columns:
        # Pending tickers have conviction=None, which becomes NaN in the
        # DataFrame and breaks int() below. The "investable" ranking only
        # makes sense for ready tickers (those with a full agent run); we
        # filter to that cohort here.
        candidates = df.copy()
        if "agents_status" in candidates.columns:
            candidates = candidates[candidates["agents_status"] == "ready"]
        candidates = candidates[
            pd.to_numeric(candidates.get("base_mos"), errors="coerce") > 0
        ]
        candidates = candidates[pd.notna(candidates.get("conviction"))]

        if not candidates.empty:
            top_5 = candidates.sort_values(
                by=["conviction", "base_mos"], ascending=[False, False],
            ).head(5)

            for _, row in top_5.iterrows():
                conv_val = row.get("conviction")
                conv_str = f"{int(conv_val):+d}" if pd.notna(conv_val) else "—"
                mos_val = row.get("base_mos")
                mos_str = f"{mos_val:+.1%}" if pd.notna(mos_val) else "—"
                st.sidebar.markdown(f"""
                    <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px;">
                        <span style="font-weight: 700; font-size: 14px; font-family: 'DM Mono', monospace;">{row['ticker']}</span>
                        <span style="font-size: 12px; color: var(--text-color); opacity: 0.8;">Conv Score {conv_str} • <span style="color: #059669; font-weight: 600;">{mos_str} MoS</span></span>
                    </div>
                """, unsafe_allow_html=True)

            n_ready = int((df.get("agents_status", pd.Series(dtype=str)) == "ready").sum()) \
                      if "agents_status" in df.columns else len(df)
            st.sidebar.caption(
                f"Showing top 5 of {len(candidates)} positive-MoS names "
                f"(of {n_ready} ready)"
            )
        else:
            st.sidebar.info(
                "No ready tickers currently offer a positive Margin of Safety."
            )

    # ── Header ────────────────────────────────────────────────────────────────
    n_ready   = sum(1 for s in status_by_ticker.values() if s == "ready")
    n_pending = sum(1 for s in status_by_ticker.values() if s == "pending")
    n_total   = len(available)

    hdr_col, btn_col = st.columns([4, 1])
    with hdr_col:
        st.markdown('<div class="aletheia-header">Aletheia</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="aletheia-subtitle">'
            f'Investment Intelligence · {n_ready} of {n_total} agents ready'
            + (f' · {n_pending} pending' if n_pending else '')
            + '</div>',
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

    # Soft hint (not an alert) when there are pending tickers — they show
    # calc-layer numbers in the Universe tab and can be promoted to ready
    # via the ▶ Run agents footer there.
    pending_tickers = [t for t, s in status_by_ticker.items() if s == "pending"]
    if pending_tickers:
        st.caption(
            f"⏳ {len(pending_tickers)} ticker(s) pending agent run — "
            f"calc-layer numbers visible in **◈ Universe**, promote via "
            f"the ▶ Run agents footer there. "
            f"({', '.join(pending_tickers[:6])}"
            f"{'…' if len(pending_tickers) > 6 else ''})"
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
    # TAB 0 — DASHBOARD (Phase 5: per-ticker analyst dashboard)
    # ──────────────────────────────────────────────────────────────────────────
    if active_view == "▷  Dashboard":
        from aletheia.ui.dashboard import render_dashboard
        render_dashboard(st.session_state.active_ticker)
        return

    # ──────────────────────────────────────────────────────────────────────────
    # TAB 1 — UNIVERSE
    # ──────────────────────────────────────────────────────────────────────────
    if active_view == "◈  Universe":

        # ── Status partitions ─────────────────────────────────────────────
        ready_rows    = [r for r in ranked if r.get("agents_status") == "ready"]
        pending_rows  = [r for r in ranked if r.get("agents_status") == "pending"]
        notingst_rows = [r for r in ranked if r.get("agents_status") == "not_ingested"]
        n_total = len(ranked)

        # ── Stats strip — partition by status ─────────────────────────────
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.metric("Total in universe", n_total)
        with c2:
            st.metric("✓ Ready (agents run)", len(ready_rows))
        with c3:
            st.metric("⏳ Pending agents", len(pending_rows),
                      delta=f"{len(notingst_rows)} not ingested" if notingst_rows else None,
                      delta_color="off")
        with c4:
            ready_mos = [r.get("base_mos") for r in ready_rows if r.get("base_mos") is not None]
            avg_mos = sum(ready_mos) / len(ready_mos) if ready_mos else 0
            st.metric("Avg MoS (ready)", f"{avg_mos:+.1%}")
        with c5:
            ready_conv = [r.get("conviction") for r in ready_rows if r.get("conviction") is not None]
            avg_conv = sum(ready_conv) / len(ready_conv) if ready_conv else 0
            st.metric("Avg conviction", f"{avg_conv:+.1f}", delta="out of ±10", delta_color="off")

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Add ticker bar ────────────────────────────────────────────────
        with st.container(border=True):
            ac1, ac2 = st.columns([4, 1])
            with ac1:
                new_ticker = st.text_input(
                    "Add a new ticker",
                    key="universe_add_ticker_input",
                    placeholder="e.g. NFLX — pulls SEC filings, runs cleaning + DCF + validation",
                    max_chars=10,
                    label_visibility="collapsed",
                )
            with ac2:
                add_clicked = st.button(
                    "▶ Add ticker",
                    key="universe_add_ticker_btn",
                    use_container_width=True,
                    disabled=not new_ticker,
                )
        if add_clicked and new_ticker:
            _run_add_ticker_pipeline_ui(new_ticker)
            fetch_universe.clear()
            fetch_health.clear()
            st.rerun()

        # ── Ranked table ──────────────────────────────────────────────────
        st.markdown(f"#### Universe rankings — {n_total} tickers")

        display_rows = []
        for r in ranked:
            status = r.get("agents_status") or "ready"
            status_icon = {"ready": "🟢", "pending": "🟡", "not_ingested": "⚪"}.get(status, "·")
            hist = r.get("historical_cagr")
            impl = r.get("implied_cagr")
            ratio = impl / hist if hist and hist > 0 and impl is not None else None
            last_run = r.get("last_agent_run")
            last_run_short = last_run[:10] if last_run else "—"
            display_rows.append({
                "Status":    status_icon,
                "Ticker":    r["ticker"],
                "Last run":  last_run_short,
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
                "_status":   status,   # used for styling, hidden via column_config
            })

        rankings_df = pd.DataFrame(display_rows)

        # Style: pending/not_ingested rows render at reduced opacity so the
        # ready cohort visually leads.
        def _row_style(row: pd.Series) -> List[str]:
            base = [""] * len(row)
            if row.get("_status") in ("pending", "not_ingested"):
                base = ["color: rgba(120,120,128,0.65)"] * len(row)
            return base

        styler = rankings_df.style.apply(_row_style, axis=1)
        st.dataframe(
            styler,
            use_container_width=True,
            hide_index=True,
            column_config={
                "_status": None,  # hide the helper column
                "Status":  st.column_config.TextColumn("●", width="small",
                                                      help="🟢 ready · 🟡 pending agents · ⚪ not ingested"),
                "Last run": st.column_config.TextColumn("Last run", width="small"),
            },
        )

        # ── Run agents footer ─────────────────────────────────────────────
        if pending_rows:
            with st.container(border=True):
                rc1, rc2 = st.columns([4, 1])
                with rc1:
                    pending_tickers = [r["ticker"] for r in pending_rows]
                    selected_pending = st.selectbox(
                        f"Run agents for one of {len(pending_tickers)} pending tickers",
                        options=pending_tickers,
                        key="universe_run_agents_select",
                        help="Runs the LangGraph workflow (~2-3 min, costs LLM tokens). After completion the ticker promotes from ⏳ to ✓.",
                    )
                with rc2:
                    run_clicked = st.button(
                        "▶ Run agents",
                        key="universe_run_agents_btn",
                        use_container_width=True,
                        type="primary",
                    )
                if run_clicked and selected_pending:
                    _run_pipeline_subprocess(selected_pending)
                    fetch_universe.clear()
                    fetch_health.clear()
                    st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Charts (ready tickers only — pending lacks the required inputs) ─
        if ready_rows:
            st.caption("Charts below show **ready tickers only** — pending tickers don't yet have the LLM-derived inputs (implied CAGR, justified multiple).")
            ch1, ch2 = st.columns(2)
            tickers = [r["ticker"] for r in ready_rows]

            with ch1:
                st.markdown("#### Implied vs Historical CAGR")
                fig = go.Figure()
                fig.add_bar(
                    name="Historical", x=tickers,
                    y=[r.get("historical_cagr") or 0 for r in ready_rows],
                    marker_color="#3b82f6", opacity=0.85,
                )
                fig.add_bar(
                    name="Implied (market)", x=tickers,
                    y=[r.get("implied_cagr") or 0 for r in ready_rows],
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
                ev_vals   = [r.get("ev_ebitda") for r in ready_rows]
                just_vals = [r.get("justified_ev_ebitda") for r in ready_rows]
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
                for r in ready_rows:
                    if r.get("ev_ebitda") and r.get("justified_ev_ebitda"):
                        fig2.add_shape(
                            type="line",
                            x0=r["ticker"], x1=r["ticker"],
                            y0=r["justified_ev_ebitda"], y1=r["ev_ebitda"],
                            line=dict(color="rgba(120,120,128,0.4)", width=1.5, dash="dot"),
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

        dcf_data  = fetch_dcf(selected)
        fund_data = fetch_fundamentals(selected)
        full      = fetch_ticker(selected)
        if not dcf_data:
            # /dcf returns 404 (no cleaned data) or 422 (schema-mismatch
            # filer like a card network or bank). api_get has already
            # surfaced the error banner; this empty-state info clarifies
            # next steps for the operator.
            st.info(
                f"**No DCF available for {selected}.** This ticker either "
                "has not been ingested or uses a non-FCFF valuation "
                "framework (banks, REITs, card networks). The Financials "
                "tab still works."
            )
            return

        # Universe row for ranking-derived signals (multiple_signal, value_creation, etc.)
        row = next((r for r in ranked if r["ticker"] == selected), {})

        # Staleness banner — surfaces when the qualitative dashboard has
        # changed since this thesis was generated (Phase 7).
        render_thesis_staleness_banner(selected, key_suffix="deep_dive")

        from aletheia.ui.deep_dive_view import render_deep_dive_view
        render_deep_dive_view(
            ticker=selected,
            dcf=dcf_data,
            fund=fund_data or {},
            full_report=full or {},
            universe_row=row,
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

        from aletheia.ui.financials_view import render_financials_view
        render_financials_view(selected, bundle)

    # ──────────────────────────────────────────────────────────────────────────
    # TAB 4 — SCENARIOS (typed agent-proposed scenarios with full provenance)
    # ──────────────────────────────────────────────────────────────────────────
    elif active_view == "◇  Scenarios":

        selected = st.session_state.active_ticker
        if not selected:
            st.info("Select a ticker from the sidebar.")
            return

        full_report = fetch_ticker(selected)
        if not full_report:
            st.warning(
                f"No agent report yet for {selected}. "
                "Click '▶ Run pipeline for {selected}' in the sidebar to generate one."
            )
            return

        val4 = full_report.get("4_valuation_synthesis", {}) or {}
        scenarios = val4.get("agent_scenarios", []) or []
        p2v = val4.get("phase2_valuation", {}) or {}
        base_dcf = p2v.get("three_scenario_dcf", {}).get("base", {}) or {}
        # IPS sometimes isn't persisted under three_scenario_dcf.base; the
        # full DCFResult dict (under val4 / Financials tab path) has the
        # canonical base_intrinsic_per_share. Use it as a fallback so the
        # reference card never displays "—" when base data exists.
        base_ips = (
            base_dcf.get("intrinsic_per_share")
            or fetch_financials_bundle(selected).get("dcf_scenarios", {})
                                                .get("base", {}).get("IPS")
        )
        base_mos = base_dcf.get("margin_of_safety")
        base_ev = (
            base_dcf.get("ev")
            or fetch_financials_bundle(selected).get("dcf_scenarios", {})
                                                .get("base", {}).get("EV")
        )

        # ── Header ──────────────────────────────────────────────────────────
        st.markdown(f"### Agent-proposed scenarios — {selected}")
        st.caption(
            "Typed, bounded hypotheses produced by forensic / value_chain / "
            "context agents. Each scenario is evaluated by DCFEngine on a cloned "
            "ValuationProfile — agents propose, calc layer computes."
        )

        # Base reference card
        if base_ips is not None or base_ev is not None:
            c1, c2, c3 = st.columns(3)
            c1.metric("Base IPS", f"${base_ips:,.2f}" if base_ips else "—")
            c2.metric(
                "Base MoS",
                f"{base_mos*100:+.1f}%" if base_mos is not None else "—",
            )
            c3.metric("Base EV", f"${base_ev/1e9:,.1f}B" if base_ev else "—")
            st.markdown("---")

        if not scenarios:
            st.info(
                "No agent-proposed scenarios in the saved report. This is "
                "valid — agents prefer empty when no high-conviction "
                "alternate hypothesis exists. Re-run the pipeline if you "
                "want fresh narrative output."
            )
            return

        # ── Summary table ────────────────────────────────────────────────────
        rows = []
        for s in scenarios:
            ips = s.get("intrinsic_per_share_base")
            ups = s.get("upside_pct_base")
            ovs = s.get("overrides_applied", {}) or {}
            override_keys = ", ".join(sorted(ovs.keys())) if ovs else "—"
            rows.append({
                "Name":          s.get("name", ""),
                "Type":          s.get("scenario_type", ""),
                "Proposed by":   s.get("proposed_by", ""),
                "IPS":           ips,
                "Upside (%)":    ups,
                "Δ vs Base IPS": (ips - base_ips) if (ips is not None and base_ips is not None) else None,
                "Overrides":     override_keys,
                "Error":         s.get("error") or "",
            })
        summary_df = pd.DataFrame(rows)
        st.markdown("#### Summary")
        st.dataframe(
            summary_df.style.format(
                {
                    "IPS": "${:,.2f}",
                    "Upside (%)": "{:+.1f}%",
                    "Δ vs Base IPS": "{:+,.2f}",
                },
                na_rep="—",
            ),
            hide_index=True,
            use_container_width=True,
        )

        # ── Per-scenario detail expanders ────────────────────────────────────
        st.markdown("#### Detail")
        type_color = {"bull": "#10b981", "bear": "#ef4444", "base_alternative": "#f59e0b"}
        for s in scenarios:
            name = s.get("name", "(unnamed)")
            stype = s.get("scenario_type", "")
            proposer = s.get("proposed_by", "")
            color = type_color.get(stype, "#71717a")
            ips = s.get("intrinsic_per_share_base")
            ups = s.get("upside_pct_base")

            head = (
                f"**{name}** · "
                f"<span style='color:{color}; font-weight:600'>{stype.upper()}</span> · "
                f"by `{proposer}`"
            )
            if ips is not None:
                head += f"  →  IPS = ${ips:,.2f}"
            if ups is not None:
                head += f" ({ups:+.1f}%)"
            err = s.get("error")

            with st.expander(name, expanded=(len(scenarios) <= 3)):
                st.markdown(head, unsafe_allow_html=True)
                st.markdown("**Rationale**")
                st.write(s.get("rationale", "(no rationale provided)"))

                ovs = s.get("overrides_applied", {}) or {}
                if ovs:
                    st.markdown("**Overrides applied**")
                    ov_rows = []
                    for k, v in sorted(ovs.items()):
                        if k.endswith("_pct") or k in ("revenue_growth_y1_5", "revenue_growth_y6_10", "terminal_growth"):
                            display = f"{v*100:.2f}%"
                        elif k == "terminal_margin_decay":
                            display = f"{v:.2f} (terminal/current)"
                        elif k == "base_revenue_normalization":
                            display = f"${v/1e9:,.2f}B"
                        else:
                            display = str(v)
                        ov_rows.append((k, display))
                    st.dataframe(
                        pd.DataFrame(ov_rows, columns=["Field", "Value"]),
                        hide_index=True,
                        use_container_width=True,
                    )

                if err:
                    st.error(f"DCF eval error: {err}")
                    continue

                dcf = s.get("dcf") or {}
                if dcf:
                    st.markdown("**DCF outcomes (this scenario)**")
                    sc_rows = [
                        ("Bull EV ($B)",  dcf.get("bull_ev")/1e9 if dcf.get("bull_ev") else None),
                        ("Base EV ($B)",  dcf.get("base_ev")/1e9 if dcf.get("base_ev") else None),
                        ("Bear EV ($B)",  dcf.get("bear_ev")/1e9 if dcf.get("bear_ev") else None),
                        ("Base IPS",      dcf.get("base_intrinsic_per_share")),
                        ("Base WACC",     dcf.get("base_wacc")),
                        ("Base g_term",   dcf.get("base_terminal_g")),
                        ("Base TV%EV",    dcf.get("base_tv_pct_of_ev")),
                        ("Base EV/EBITDA", dcf.get("base_ev_ebitda")),
                    ]
                    sc_df = pd.DataFrame(sc_rows, columns=["Metric", "Value"])

                    def _fmt(row):
                        m, v = row["Metric"], row["Value"]
                        if v is None:
                            return "—"
                        if "$B" in m:
                            return f"{v:,.1f}B"
                        if "IPS" in m:
                            return f"${v:,.2f}"
                        if "WACC" in m or "g_term" in m or "TV%EV" in m:
                            return f"{v*100:.2f}%"
                        if "EV/EBITDA" in m:
                            return f"{v:.1f}x"
                        return f"{v}"

                    sc_df["Value"] = sc_df.apply(_fmt, axis=1)
                    st.dataframe(sc_df, hide_index=True, use_container_width=True)

        st.markdown("---")
        st.caption(
            f"Provenance: each scenario is a typed `ScenarioOverride` produced by "
            f"the named agent during the most recent pipeline run, evaluated by "
            f"`scenario_eval_node` against `DCFEngine`. Architecture invariant: "
            f"agents propose only bounded forward-looking assumption fields "
            f"(growth, margin decay, terminal g, revenue normalization). They "
            f"never override WACC, ROIC, tax rate, or beta."
        )

    # ──────────────────────────────────────────────────────────────────────────
    # TAB 5 — SCREENING
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

                from aletheia.ui.validation_badge import status_marker
                for cat, metrics in by_cat.items():
                    st.markdown(f"**{cat}**")
                    for m in metrics:
                        sig = m["signal"]
                        dot = {"✓": "🟢", "⚠": "🟡", "✗": "🔴", "—": "⚪"}.get(sig, "⚪")
                        grade_color = SIGNAL_COLORS.get(sig, "#6c757d")

                        disp_val = m.get("display_value", f"{m['value']:.2f}" if m["value"] is not None else "N/A")
                        note_html = f"<div style='font-size:11px; color:#6c757d; font-style:italic; margin-top:2px'>← {m.get('note', '')}</div>" if m.get("note") else ""
                        validation_mark = status_marker(screen_ticker, m["name"])

                        st.markdown(f"""
                            <div style='margin-bottom:8px; padding-bottom:6px; border-bottom:1px solid #1c1c1f'>
                                <div style='display:flex; justify-content:space-between; align-items:baseline'>
                                    <span>
                                        {dot} <span style='font-size:13px; font-weight:500; margin-left:4px'>{m["name"]}<span style='color:#10b981; font-weight:700; margin-left:3px' title='Externally validated'>{validation_mark}</span></span>
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
    # TAB 6 — CONSTITUTION
    # ──────────────────────────────────────────────────────────────────────────
    elif active_view == "◨  Constitution":
        st.markdown("#### Constitution Compliance")
        st.markdown(
            '<span style="font-family:DM Mono,monospace;font-size:11px;color:#71717a">'
            "Framework rules enforcement — two FAILs = do not deploy capital.</span>",
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)

        # Skip pending/not_ingested tickers — constitution checks come
        # from the agent run, which doesn't exist for them. Showing
        # placeholder "no checks found" rows for 15 pending tickers
        # would just be noise.
        for r in [x for x in ranked if x.get("agents_status") == "ready"]:
            ticker = r["ticker"]
            narr = fetch_narrative(ticker)
            checks = narr.get("constitution_checks", []) if narr else []
            conv   = r.get("conviction")
            sig    = SIGNAL_LABEL.get(r.get("multiple_signal", ""), "")

            fail_count = sum(1 for c in checks if "FAIL" in str(c) or "❌" in str(c))
            warn_count = sum(1 for c in checks if "CAUTION" in str(c) or "⚠" in str(c))
            pass_count = len(checks) - fail_count - warn_count

            conv_str = f"{int(conv):+d}" if conv is not None else "—"
            with st.expander(
                f"{ticker}  —  {pass_count}✅ {warn_count}⚠️ {fail_count}❌  "
                f"| Conv {conv_str}  |  {sig}",
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
    # TAB — QUALITATIVE  (19-dimension analytical framework, week-1 read-only)
    # ──────────────────────────────────────────────────────────────────────────
    elif active_view == "◭  Qualitative":
        selected = st.session_state.active_ticker
        if not selected:
            st.info("Select a ticker from the sidebar.")
        else:
            qual_data        = fetch_qualitative(selected)
            categories_data  = fetch_qualitative_categories(selected)

            def _on_recompute():
                result = trigger_qualitative_recompute(selected)
                if result:
                    written = result.get("written_count", 0)
                    unchanged = result.get("unchanged_count", 0)
                    no_data = result.get("no_data_count", 0)
                    st.success(
                        f"Recompute done — written: {written}, "
                        f"unchanged: {unchanged}, no-data: {no_data}"
                    )
                    st.rerun()

            def _on_submit_success():
                fetch_qualitative.clear()
                fetch_qualitative_categories.clear()
                st.rerun()

            from aletheia.ui.qualitative_view import render_qualitative_view
            render_qualitative_view(
                ticker=selected,
                qual_data=qual_data,
                categories_data=categories_data,
                on_recompute=_on_recompute,
                api_base=API_BASE,
                on_submit_success=_on_submit_success,
            )

    # ──────────────────────────────────────────────────────────────────────────
    # TAB 7 — THESIS (read-only structured view)
    # ──────────────────────────────────────────────────────────────────────────
    # The free-text Thesis Builder form was retired in the dashboard-wiring
    # change. Structured analyst judgment now lives in the Qualitative tab
    # (per-dimension assessments); the integrated thesis is produced by the
    # thesis_synthesizer agent and rendered here in read-only form, plus
    # historical memos archived for reference.
    elif active_view == "📝  Thesis":
        st.markdown("#### Investment Thesis")
        st.markdown(
            '<span style="font-family:DM Mono,monospace;font-size:11px;color:#71717a">'
            "Structured thesis from thesis_synthesizer · "
            "free-text builder retired (use the Qualitative tab for analyst input)"
            "</span>",
            unsafe_allow_html=True,
        )

        thesis_ticker = st.session_state.active_ticker

        if not thesis_ticker:
            st.info("Select a ticker from the sidebar to begin analysis.")
        else:
            # Staleness banner (Phase 7)
            render_thesis_staleness_banner(thesis_ticker, key_suffix="thesis_tab")

            # Fetch report (read-only — no form submissions on this tab)
            report = fetch_ticker(thesis_ticker)
            if report:
                ts_synth = (report.get("4_valuation_synthesis", {})
                                  .get("thesis_synthesis") or {})

                # Read-only access to historical memo content (archived;
                # no longer written from this UI). Surfaced for reference.
                try:
                    existing = httpx.get(f"{API_BASE}/ticker/{thesis_ticker}/thesis", timeout=5).json()
                except Exception:
                    existing = {}

                if existing and existing.get("version"):
                    st.link_button(
                        f"⬇ Archived memo PDF (v{existing.get('version')})",
                        f"{API_BASE}/ticker/{thesis_ticker}/thesis/pdf",
                    )

                # ── Structured thesis (always visible) ───────────────────
                # Surfaces the full thesis_synthesizer output so the analyst
                # can lift specific decision conditions or cited signals
                # into their own thesis. Read-only — analyst edits the form
                # below; this panel is reference material. Rendered inline
                # (no expander) so analysts don't have to click to see what
                # the framework already produced.
                if ts_synth.get("thesis_statement"):
                    _GREEN_C, _AMBER_C, _RED_C = "#10b981", "#f59e0b", "#ef4444"
                    _MUTED_C = "rgba(120,120,128,0.85)"
                    _TRACK_C = "rgba(120,120,128,0.20)"
                    _PRIORITY_C = {"red": _RED_C, "amber": _AMBER_C, "green": _GREEN_C}
                    _ACTION_C = {
                        "BUY": _GREEN_C, "ACCUMULATE": _GREEN_C, "ADD": _GREEN_C,
                        "HOLD": _AMBER_C, "WATCH": _AMBER_C, "REVIEW": _AMBER_C,
                        "TRIM": _RED_C, "SELL": _RED_C, "EXIT": _RED_C, "PASS": _RED_C,
                    }

                    st.markdown("##### System suggestions")
                    st.caption("From thesis_synthesizer · reference for the form below")

                    confidence = (ts_synth.get("thesis_confidence") or "—").upper()
                    horizon = (ts_synth.get("time_horizon") or "—").replace("_", " ").upper()
                    conf_color = {"HIGH": _GREEN_C, "MEDIUM": _AMBER_C, "LOW": _RED_C}.get(confidence, _MUTED_C)
                    st.markdown(
                        f"<div style='font-family:DM Mono,monospace;font-size:11px;"
                        f"color:{_MUTED_C};margin-bottom:12px;letter-spacing:0.3px;'>"
                        f"Confidence: <span style='color:{conf_color};font-weight:700'>{confidence}</span>"
                        f"&nbsp;·&nbsp; Horizon: <span style='color:inherit;font-weight:700'>{horizon}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                    # Thesis statement (prominent block)
                    st.markdown(
                        f"<div style='border-left:4px solid {conf_color};padding:14px 20px;"
                        f"background:{_TRACK_C};border-radius:0 6px 6px 0;"
                        f"font-size:15px;line-height:1.7;color:inherit;"
                        f"margin-bottom:18px;font-weight:500;'>"
                        f"{ts_synth.get('thesis_statement', '')}</div>",
                        unsafe_allow_html=True,
                    )

                    # Bull / Base / Bear cards
                    for label, key, color in [
                        ("BULL CASE", "bull_case", _GREEN_C),
                        ("BASE CASE", "base_case", _AMBER_C),
                        ("BEAR CASE", "bear_case", _RED_C),
                    ]:
                        cc = ts_synth.get(key) or {}
                        claim = cc.get("claim", "")
                        if not claim:
                            continue
                        cites = cc.get("cited_signals") or []
                        cite_chips = ""
                        if cites:
                            chips = "".join(
                                f'<span style="font-family:DM Mono,monospace;font-size:10px;'
                                f'color:{_MUTED_C};background:{_TRACK_C};padding:2px 6px;'
                                f'border-radius:3px;margin-right:4px;">{s}</span>'
                                for s in cites[:6]
                            )
                            cite_chips = (
                                f'<div style="margin-top:8px;line-height:2;">'
                                f'<span style="font-family:DM Mono,monospace;font-size:10px;'
                                f'color:{_MUTED_C};text-transform:uppercase;letter-spacing:0.5px;'
                                f'margin-right:8px;">Cites:</span>{chips}</div>'
                            )
                        st.markdown(
                            f"<div style='border:1px solid {_TRACK_C};border-left:4px solid {color};"
                            f"padding:16px 20px;border-radius:0 6px 6px 0;color:inherit;"
                            f"margin-bottom:12px;'>"
                            f"<div style='font-family:DM Mono,monospace;font-size:11px;"
                            f"color:{color};letter-spacing:0.7px;text-transform:uppercase;"
                            f"margin-bottom:8px;font-weight:700;'>{label}</div>"
                            f"<div style='font-size:14px;line-height:1.7;'>{claim}</div>"
                            f"{cite_chips}</div>",
                            unsafe_allow_html=True,
                        )

                    # Decision conditions — card rows (matches Deep Dive style)
                    dcs = ts_synth.get("decision_conditions") or []
                    if dcs:
                        st.markdown("<div style='margin-top:18px;'></div>", unsafe_allow_html=True)
                        st.markdown("**Decision conditions** — what triggers a position change")
                        rows_html = []
                        for dc in dcs:
                            pri = (dc.get("priority") or "").lower()
                            act = (dc.get("action") or "").upper()
                            trigger = dc.get("trigger", "") or "—"
                            obs = dc.get("observable", "") or ""
                            p_color = _PRIORITY_C.get(pri, _MUTED_C)
                            a_color = _ACTION_C.get(act, _MUTED_C)
                            obs_html = (
                                f'<div style="font-family:DM Mono,monospace;font-size:11px;'
                                f'color:{_MUTED_C};margin-top:6px;line-height:1.5;">'
                                f'observable · {obs}</div>'
                            ) if obs else ""
                            rows_html.append(
                                f'<div style="border:1px solid {_TRACK_C};border-left:4px solid {p_color};'
                                f'border-radius:0 6px 6px 0;padding:12px 16px;margin-bottom:8px;'
                                f'color:inherit;display:flex;align-items:flex-start;gap:14px;">'
                                f'<div style="flex:1;min-width:0;">'
                                f'<div style="font-family:DM Mono,monospace;font-size:10px;font-weight:600;'
                                f'letter-spacing:0.5px;text-transform:uppercase;color:{p_color};'
                                f'margin-bottom:4px;">● {pri or "—"}</div>'
                                f'<div style="font-size:13px;line-height:1.6;">{trigger}</div>'
                                f'{obs_html}</div>'
                                f'<span style="display:inline-block;font-family:DM Mono,monospace;'
                                f'font-size:10px;font-weight:700;letter-spacing:0.6px;'
                                f'color:{a_color};border:1px solid {a_color};'
                                f'padding:3px 9px;border-radius:3px;white-space:nowrap;flex-shrink:0;">'
                                f'{act or "—"}</span>'
                                f'</div>'
                            )
                        st.markdown("".join(rows_html), unsafe_allow_html=True)

                    upd = ts_synth.get("update_conditions") or []
                    if upd:
                        st.markdown("<div style='margin-top:18px;'></div>", unsafe_allow_html=True)
                        st.markdown("**Update conditions** — would invalidate the thesis")
                        for u in upd:
                            st.markdown(f"- {u}")

                else:
                    st.info(
                        "No structured thesis available yet for this ticker. "
                        "Run the pipeline (sidebar → ▶ Run agents) to generate "
                        "a thesis_synthesizer output."
                    )

                # ── Archived memos (read-only) ──────────────────────────
                # The free-text Thesis Builder retired in the dashboard-
                # wiring change. Historical memos remain accessible for
                # reference but are no longer editable from this UI.
                # New analyst input lives in the Qualitative tab as
                # structured per-dimension assessments.
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("##### Archived memos")
                st.caption(
                    "Free-text Thesis Builder retired — these memos are "
                    "preserved for historical reference. New analyst "
                    "judgment lives in the Qualitative tab."
                )
                try:
                    history = httpx.get(
                        f"{API_BASE}/ticker/{thesis_ticker}/thesis/history",
                        timeout=5,
                    ).json()
                    if history:
                        for h in history:
                            created = (h.get("created_at") or "")[:10]
                            one_sent = h.get("one_sentence") or "(no summary)"
                            st.markdown(
                                f"**v{h.get('version', '?')}** "
                                f"({created}): {one_sent}"
                            )
                    else:
                        st.info("No archived memos for this ticker.")
                except Exception:
                    st.info("Could not fetch memo history.")

    # ──────────────────────────────────────────────────────────────────────────
    # TAB 8 — REPORTS
    # ──────────────────────────────────────────────────────────────────────────
    elif active_view == "◩  Reports":
        report_ticker = st.session_state.active_ticker
        
        if not report_ticker:
            st.info("Select a ticker from the sidebar to begin analysis.")
        else:
            # Executive MD dropped — was a strict subset of Detailed MD.
            # 4 download surfaces: HTML, Detailed MD, Raw JSON, DCF Excel.
            col_dl1, col_dl2, col_dl3, col_dl4 = st.columns(4)

            with col_dl1:
                html_bytes = httpx.get(f"{API_BASE}/ticker/{report_ticker}/report/html", timeout=10).content
                st.download_button("⬇ HTML Report", html_bytes,
                                   file_name=f"{report_ticker}_Executive_Report.html",
                                   mime="text/html", use_container_width=True)
            with col_dl2:
                det_bytes = httpx.get(f"{API_BASE}/ticker/{report_ticker}/report/detailed", timeout=10).content
                st.download_button("⬇ Detailed MD", det_bytes,
                                   file_name=f"{report_ticker}_Detailed_Report.md",
                                   mime="text/markdown", use_container_width=True)
            with col_dl3:
                json_bytes = httpx.get(f"{API_BASE}/ticker/{report_ticker}", timeout=10).content
                st.download_button("⬇ Raw JSON", json_bytes,
                                   file_name=f"{report_ticker}_report.json",
                                   mime="application/json", use_container_width=True)
            with col_dl4:
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

    elif active_view == "◊  Quality Report":
        from aletheia.ui.quality_report import render_quality_report
        render_quality_report(st.session_state.active_ticker)


if __name__ == "__main__":
    main()
