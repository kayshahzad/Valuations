"""
aletheia/ui/agents_view.py

"Agents" view for the Streamlit dashboard. Renders the LLM-driven analysis
that the LangGraph workflow (librarian → calc_node → forensic →
value_chain → context → scenario_eval → strategist → contrarian → lead)
produces for the active ticker, with a prominent refresh action.

Why a dedicated view: by default the analyst-facing app shows numbers
(DCF, screening, financials). The narrative agents (forensic, strategist,
contrarian, lead synthesis) live in a separate report payload served by
the API. This view surfaces them in one place and lets the analyst trigger
a fresh run on demand — useful when the company has reported new
fundamentals or filed an 8-K and the cached narrative is stale.

The "Refresh agent analysis" button shells out to `main.py --ticker X`
(the same entry point used by `_run_pipeline_subprocess` in
streamlit_app.py) and streams output into a status panel. On completion
the API caches are busted so subsequent fetches see the new report.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st


def _fetch_full(ticker: str) -> Optional[Dict[str, Any]]:
    """Pull the full per-ticker report from the API. Lazy import so this
    module remains importable when the API helpers aren't on the path."""
    try:
        # streamlit_app.py defines the fetchers; reach in if available.
        import streamlit_app as app  # type: ignore
        if hasattr(app, "fetch_ticker"):
            return app.fetch_ticker(ticker)
    except Exception:
        pass
    # Fallback: direct httpx hit.
    try:
        import httpx
        r = httpx.get(f"http://localhost:8000/ticker/{ticker}", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _fetch_narrative(ticker: str) -> Optional[Dict[str, Any]]:
    try:
        import streamlit_app as app  # type: ignore
        if hasattr(app, "fetch_narrative"):
            return app.fetch_narrative(ticker)
    except Exception:
        pass
    try:
        import httpx
        r = httpx.get(f"http://localhost:8000/ticker/{ticker}/narrative", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _last_run_metadata(ticker: str) -> Optional[Dict[str, Any]]:
    """Pull the most recent audit trace for the ticker, if any."""
    from pathlib import Path
    audits_dir = Path("audits")
    if not audits_dir.exists():
        return None
    # Filenames look like: trace_AAPL_1777762405.json
    matches = sorted(audits_dir.glob(f"trace_{ticker.upper()}_*.json"), reverse=True)
    if not matches:
        return None
    newest = matches[0]
    try:
        ts = int(newest.stem.split("_")[-1])
    except (ValueError, IndexError):
        ts = None
    return {
        "path":      str(newest),
        "timestamp": ts,
        "size_kb":   newest.stat().st_size / 1024,
    }


# ── Agent display blocks ─────────────────────────────────────────────────

def _block_strategic_context(er: Dict[str, Any]) -> None:
    sc = er.get("strategic_context") or {}
    if not sc:
        st.caption("_strategic context not yet generated_")
        return
    ind = sc.get("industry_dynamics") or sc.get("industry") or {}
    macro = sc.get("macro_dynamics") or {}
    if isinstance(ind, dict):
        for k, v in ind.items():
            if isinstance(v, str) and v.strip():
                st.markdown(f"- **{k.replace('_', ' ').title()}**: {v}")
    if isinstance(macro, dict):
        for k, v in macro.items():
            if isinstance(v, str) and v.strip():
                st.markdown(f"- **{k.replace('_', ' ').title()}**: {v}")


def _block_value_chain(er: Dict[str, Any]) -> None:
    vc = er.get("value_chain") or {}
    if not vc:
        st.caption("_value chain analysis not yet generated_")
        return
    for k, v in vc.items():
        if isinstance(v, str) and v.strip():
            st.markdown(f"**{k.replace('_', ' ').title()}**")
            st.markdown(v)
        elif isinstance(v, list) and v:
            st.markdown(f"**{k.replace('_', ' ').title()}**")
            for item in v:
                st.markdown(f"- {item}")


def _block_moat(er: Dict[str, Any]) -> None:
    moat = er.get("moat") or {}
    if not moat:
        st.caption("_moat analysis not yet generated_")
        return
    score = moat.get("score") or moat.get("moat_score")
    if score is not None:
        st.markdown(f"**Moat score:** {score}/10")
    for k, v in moat.items():
        if k in ("score", "moat_score"):
            continue
        if isinstance(v, str) and v.strip():
            st.markdown(f"- **{k.replace('_', ' ').title()}**: {v}")


def _block_contrarian(val4: Dict[str, Any]) -> None:
    ca = val4.get("contrarian_analysis") or {}
    if not ca:
        st.caption("_contrarian analysis not yet generated_")
        return
    for k, v in ca.items():
        if isinstance(v, str) and v.strip():
            st.markdown(f"**{k.replace('_', ' ').title()}**")
            st.markdown(v)
        elif isinstance(v, list) and v:
            st.markdown(f"**{k.replace('_', ' ').title()}**")
            for item in v:
                st.markdown(f"- {item}")


def _block_lead_synthesis(val4: Dict[str, Any]) -> None:
    it = val4.get("investment_thesis") or {}
    if not it:
        st.caption("_lead synthesis not yet generated_")
        return
    conv = it.get("conviction_score")
    pillars = it.get("pillar_scores") or {}
    cap = pillars.get("capped_total")
    tier = pillars.get("position_tier")
    a, b, c = st.columns(3)
    with a:
        st.metric("Conviction", f"{conv:+d}/10" if conv is not None else "—")
    with b:
        st.metric("Pillar score", f"{cap}/25" if cap is not None else "—")
    with c:
        st.metric("Tier", (tier or "—").upper())
    narrative = it.get("narrative")
    if narrative:
        st.markdown("**Synthesis**")
        st.markdown(narrative)


# ── Public render ────────────────────────────────────────────────────────

def render_agents_view(ticker: Optional[str]) -> None:
    if not ticker:
        st.info("Select a ticker from the sidebar to view agent outputs.")
        return

    st.markdown(f"## Agent analysis — {ticker.upper()}")
    st.caption(
        "LLM-driven narrative pipeline: librarian → calc_node → forensic → "
        "value_chain → context → scenario_eval → strategist → contrarian → lead. "
        "Click **Refresh** to re-run the full chain when fundamentals or news change."
    )

    # ── Last-run metadata + refresh control ──────────────────────────────
    meta = _last_run_metadata(ticker)
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        if meta and meta.get("timestamp"):
            from datetime import datetime
            ts_h = datetime.fromtimestamp(meta["timestamp"]).strftime("%Y-%m-%d %H:%M")
            st.metric("Last run", ts_h)
        else:
            st.metric("Last run", "never")
    with c2:
        st.metric("Trace size", f"{meta['size_kb']:.0f} KB" if meta else "—")
    with c3:
        clicked = st.button(
            f"▶ Refresh agent analysis for {ticker.upper()}",
            key=f"agents_refresh_{ticker}",
            use_container_width=True,
            type="primary",
        )

    if clicked:
        # Reuse the existing pipeline runner (defined in streamlit_app.py).
        try:
            import streamlit_app as app  # type: ignore
            if hasattr(app, "_run_pipeline_subprocess"):
                app._run_pipeline_subprocess(ticker)
            else:
                st.error("Pipeline runner not available in this build.")
        except Exception as e:
            st.error(f"Failed to launch pipeline: {e}")
        # After completion, re-fetch + render below
        try:
            import streamlit_app as app  # type: ignore
            for clear_fn in ("fetch_ticker", "fetch_narrative", "fetch_dcf",
                             "fetch_fundamentals", "fetch_screening"):
                fn = getattr(app, clear_fn, None)
                if fn is not None and hasattr(fn, "clear"):
                    fn.clear()
        except Exception:
            pass

    # ── Pull current report ──────────────────────────────────────────────
    full = _fetch_full(ticker)
    narr = _fetch_narrative(ticker)
    if not full:
        st.warning(
            "No report payload available from the API. "
            "Click **Refresh** above to run the full pipeline, "
            "or start the API: `PYTHONPATH=. uvicorn api.main:app --reload --port 8000`."
        )
        return

    er   = full.get("1_economic_reality", {}) or {}
    val4 = full.get("4_valuation_synthesis", {}) or {}

    st.markdown("---")

    # ── Lead synthesis (most important — show first) ────────────────────
    st.markdown("### 🎯 Lead synthesis (final agent)")
    _block_lead_synthesis(val4)

    # ── Strategist + Context ────────────────────────────────────────────
    st.markdown("### 📍 Strategic context (context agent)")
    with st.expander("Industry & macro dynamics", expanded=False):
        _block_strategic_context(er)

    # ── Value Chain ────────────────────────────────────────────────────
    st.markdown("### 🔗 Value chain (value_chain agent)")
    with st.expander("Position in value chain", expanded=False):
        _block_value_chain(er)

    # ── Moat (forensic + value_chain output) ─────────────────────────────
    st.markdown("### 🏰 Moat (forensic agent)")
    with st.expander("Competitive durability", expanded=False):
        _block_moat(er)

    # ── Contrarian ──────────────────────────────────────────────────────
    st.markdown("### ⚔️ Contrarian (contrarian agent)")
    with st.expander("Bear case + rebuttal", expanded=False):
        _block_contrarian(val4)

    # ── Raw narrative (if separately exposed) ────────────────────────────
    if narr:
        st.markdown("---")
        st.markdown("### 📝 Raw narrative bundle")
        with st.expander("Full text from /ticker/X/narrative endpoint", expanded=False):
            for k, v in narr.items():
                if isinstance(v, str) and v.strip():
                    st.markdown(f"**{k}**")
                    st.markdown(v)
                elif isinstance(v, dict):
                    st.markdown(f"**{k}**")
                    st.json(v)
