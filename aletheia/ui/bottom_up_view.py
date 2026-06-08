"""Bottom-up business analysis tab.

A dedicated home for the §4 bottom-up layer — the six themes (A–F), the growth
decomposition, the sector emphasis, and the assumption-grounding keystone — read
from the live /dcf payload (`business_analysis` + `assumption_grounding`).
Deterministic content renders immediately; the LLM-extracted A/B/C/E fields fill
in after a Stage-4 run.
"""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd
import streamlit as st


_THEMES = [
    ("A", "What the company sells", "The product-level reality behind the revenue line", "A. What it sells"),
    ("B", "Market size & capture", "Where the company sits in the market, room to grow", "B. Market size"),
    ("C", "Unit economics", "The economic structure beneath the aggregate margins", "C. Unit economics"),
    ("D", "Growth source decomposition", "Is the company growing the pie or taking share?", "D. Growth source"),
    ("E", "Innovation & trend positioning", "Position relative to technology and demand shifts", "E. Innovation"),
    ("F", "Industry context & dynamics", "Where the industry is in its lifecycle", "F. Industry"),
]


def _pct(v, dp=1):
    return f"{v*100:.{dp}f}%" if isinstance(v, (int, float)) else "—"


def _theme_content(letter: str, ex: Dict[str, Any], gd: Dict[str, Any],
                   ba: Dict[str, Any]) -> bool:
    """Render one theme's content. Returns True if anything was rendered."""
    rendered = False
    if letter == "A":
        for p in (ex.get("product_lines") or [])[:8]:
            st.markdown(f"- **{p.get('name','')}**"
                        + (f" — {p.get('pricing_model')}" if p.get('pricing_model') else "")
                        + (f"  _({p.get('segment')})_" if p.get('segment') else ""))
            rendered = True
        for c in (ex.get("major_customers") or [])[:8]:
            line = f"- 🤝 **{c.get('name','')}**"
            if c.get("relationship"): line += f" — {c['relationship']}"
            if c.get("recompete_or_renewal"): line += f" · recompete {c['recompete_or_renewal']}"
            if c.get("pct_revenue"): line += f" · {c['pct_revenue']} of rev"
            st.markdown(line); rendered = True
        if ex.get("notable_customers"):
            st.markdown("**Notable customers:** " + ", ".join(ex["notable_customers"])); rendered = True
        if ex.get("industry_verticals"):
            st.markdown("**Industry verticals:** " + ", ".join(ex["industry_verticals"])); rendered = True
        for label, key in (("Customer concentration", "customer_concentration"),
                           ("Net retention", "net_retention")):
            if ex.get(key):
                st.markdown(f"**{label}:** {ex[key]}"); rendered = True
        if ex.get("distribution_channels"):
            st.markdown("**Channels:** " + ", ".join(ex["distribution_channels"])); rendered = True
    elif letter == "B":
        tam = ba.get("tam") or {}
        if tam.get("tam_estimate"):
            band = ""
            if tam.get("tam_low") or tam.get("tam_high"):
                band = f"  [{tam.get('tam_low') or '?'} – {tam.get('tam_high') or '?'}]"
            extra = []
            if tam.get("tam_approach"): extra.append(f"approach: {tam['tam_approach']}")
            if tam.get("tam_confidence"): extra.append(f"confidence: {tam['tam_confidence']}")
            if tam.get("implied_share") is not None:
                sh = f"implied share {_pct(tam['implied_share'])}"
                if tam.get("implied_share_low") is not None and tam.get("implied_share_high") is not None:
                    sh += f" [{_pct(tam['implied_share_low'])}–{_pct(tam['implied_share_high'])}]"
                extra.append(sh)
            st.markdown(f"**TAM:** {tam['tam_estimate']}{band}"
                        + (f"  _({'; '.join(extra)})_" if extra else ""))
            if tam.get("tam_methodology"):
                st.caption(tam["tam_methodology"])
            rendered = True
        for label, key in (("Market share", "market_share"),
                           ("Whitespace runway", "whitespace_runway")):
            if ex.get(key):
                st.markdown(f"**{label}:** {ex[key]}"); rendered = True
        if ex.get("adjacent_tams"):
            st.markdown("**Adjacent TAMs:** " + ", ".join(ex["adjacent_tams"])); rendered = True
    elif letter == "C":
        for label, key in (("Contract economics", "contract_economics"),
                           ("CAC / LTV", "cac_ltv"), ("Unit cost", "unit_cost"),
                           ("Segment margin trajectory", "segment_margin_trajectory"),
                           ("Operating leverage", "operating_leverage")):
            if ex.get(key):
                st.markdown(f"**{label}:** {ex[key]}"); rendered = True
        seg = ba.get("segment_economics") or {}
        if seg.get("available") and seg.get("segments"):
            st.markdown(f"**Segment economics** _(FY{seg.get('fiscal_year','')} rev mix from "
                        f"FMP; margins fill on Stage-4)_")
            df = pd.DataFrame([{
                "Segment": s.get("segment", ""),
                "% rev": _pct(s.get("rev_pct")),
                "YoY": _pct(s.get("yoy_growth")),
                "Op margin": s.get("margin") or "—",
                "Trend": s.get("margin_trend") or "—",
            } for s in seg["segments"]])
            st.dataframe(df, hide_index=True, use_container_width=True)
            rendered = True
    elif letter == "D":
        if gd.get("available"):
            breaks = gd.get("break_years") or []
            if gd.get("ma_separable") is False:
                m = gd.get("ma_spend") or {}
                yrs = ", ".join(f"FY{x['year']}" for x in m.get("years", []))
                st.markdown(
                    f"**Growth source:** raw {_pct(gd.get('raw_cagr'))}; "
                    f"**M&A spend material** (${(m.get('total_spend') or 0)/1e9:.0f}B over "
                    f"{yrs}, {_pct(m.get('cum_pct_of_revenue'))} of revenue) — organic "
                    f"≤ {_pct(gd.get('organic_cagr'))} _(not separable from M&A via "
                    f"revenue trends)_")
            else:
                st.markdown(
                    f"**Growth source:** raw {_pct(gd.get('raw_cagr'))} = organic "
                    f"{_pct(gd.get('organic_cagr'))} + M&A {_pct(gd.get('ma_contribution_pp'))}"
                    + (f" (breaks FY{breaks})" if breaks else "")
                    + f" — _{gd.get('split','')}_")
            rendered = True
            if gd.get("share_gain_pp") is not None:
                st.markdown(
                    f"**Market vs share:** organic {_pct(gd.get('organic_cagr'))} vs "
                    f"sector market {_pct(gd.get('market_growth_ref'))} → share "
                    f"{_pct(gd.get('share_gain_pp'))} (_{gd.get('share_label','')}_)")
                st.caption(gd.get("market_ref_basis", ""))
            elif gd.get("market_ref_basis") is None and gd.get("available"):
                st.caption("Market-vs-share: insufficient same-peer-group history "
                           "in the universe to compute a market-growth reference.")
    elif letter == "E":
        for label, key in (("R&D pipeline", "rd_pipeline"),
                           ("Trend positioning", "trend_positioning"),
                           ("Disruption risk", "disruption_risk"),
                           ("Acquisition strategy", "acquisition_strategy")):
            if ex.get(key):
                st.markdown(f"**{label}:** {ex[key]}"); rendered = True
        for l in (ex.get("new_product_launches") or [])[:6]:
            st.markdown(f"- 🚀 **{l.get('name','')}**"
                        + (f" ({l.get('timing')})" if l.get('timing') else "")
                        + (f" — {l.get('traction')}" if l.get('traction') else ""))
            rendered = True
    elif letter == "F":
        if ba.get("lifecycle"):
            st.markdown(f"**Industry lifecycle stage:** {ba['lifecycle']}"); rendered = True
    return rendered


def render_bottom_up_view(ticker: str, dcf: Dict[str, Any]) -> None:
    """Render the bottom-up analysis tab for a ticker."""
    if not ticker:
        st.info("Select a ticker from the sidebar to begin analysis.")
        return
    ba = (dcf or {}).get("business_analysis") or {}
    ag = (dcf or {}).get("assumption_grounding") or {}
    if not ba.get("available"):
        st.info(f"No bottom-up analysis available for {ticker}. Ensure the "
                "ticker is ingested (the deterministic layer needs cleaned data).")
        return

    st.markdown(f"## {ticker} — Bottom-up business analysis")
    st.caption("The business reality beneath the revenue line, by theme. "
               "Deterministic content is live; ★-marked extracted fields fill in "
               "on a Stage-4 run.")

    ex = ba.get("extracted") or {}
    gd = ba.get("growth_decomposition") or {}
    cov = ba.get("coverage") or []
    tpl = ba.get("sector_template") or {}

    # Header: sector emphasis + coverage + extraction status.
    c1, c2 = st.columns([3, 1])
    if tpl.get("emphasis"):
        c1.markdown(f"**Sector emphasis — {tpl.get('label','')}"
                    + (f" ({tpl.get('peer_group')})" if tpl.get('peer_group') else "")
                    + f":**  {' · '.join(tpl['emphasis'])}")
    c2.metric("Dimensions populated", f"{ba.get('n_present','?')}/{ba.get('n_total','?')}")

    # Curated peer set (true peers via FMP) — drives market-vs-share & multiple.
    ps = ba.get("peer_stats") or {}
    if ps.get("available"):
        peers = ", ".join(ps.get("peers") or [])
        p1, p2, p3 = st.columns(3)
        mg = ps.get("market_growth_median")
        ev = ps.get("ev_ebitda_median")
        om = ps.get("op_margin_median")
        p1.metric("Peer rev CAGR (median)", f"{mg*100:.1f}%" if mg is not None else "—")
        p2.metric("Peer EV/EBITDA (median)", f"{ev:.1f}×" if ev is not None else "—")
        p3.metric("Peer op margin (median)", f"{om*100:.1f}%" if om is not None else "—")
        st.caption(f"Peer set ({ps.get('source','')}): {peers}")

    if not ex:
        st.info("ℹ️ Themes A/B/C/E (product, market, unit economics, innovation) "
                "are populated by a structured 10-K extraction that runs on a "
                "Stage-4 agent run. Deterministic themes (D growth source, F "
                "lifecycle) and grounding are shown now.")

    # The six themes A–F.
    for letter, title, subtitle, cov_prefix in _THEMES:
        with st.container(border=True):
            st.markdown(f"#### {letter} · {title}")
            st.caption(subtitle)
            had = _theme_content(letter, ex, gd, ba)
            theme_cov = [c for c in cov if c.get("theme", "").startswith(cov_prefix)]
            pend = [c["dimension"] for c in theme_cov if c.get("status") == "pending"]
            na = [c for c in theme_cov if c.get("status") == "n_a"]
            prio = {c["dimension"] for c in theme_cov if c.get("priority")}
            if pend:
                marked = ["★ " + d if d in prio else d for d in pend]
                st.caption("Pending extraction: " + ", ".join(marked))
            if na:
                st.caption("Not applicable: " + ", ".join(
                    c["dimension"] + (f" ({c['reason']})" if c.get("reason") else "")
                    for c in na))
            if not had and not pend and not na:
                st.caption("—")

    # Assumption grounding — the bottom-up → top-down bridge (keystone).
    if ag.get("available"):
        with st.container(border=True):
            st.markdown("#### 🧲 Assumption grounding — business → top-down bridge")
            st.caption("Each top-down DCF assumption vs a business-grounded "
                       "reference, with the computation shown. Apply a grounded "
                       "value to push it (validated) into the DCF as an override.")
            def _basis(r):
                note = r.get("note", "")
                b = r.get("build_up") or {}
                if b.get("band_low") is not None and b.get("band_high") is not None:
                    note += f" · band {_pct(b['band_low'])}–{_pct(b['band_high'])}"
                return note
            rows = [{
                "Assumption": r.get("assumption", ""),
                "Engine": _pct(r.get("engine_value")),
                "Grounded": _pct(r.get("grounded_value")),
                "Δ": _pct(r.get("delta")),
                "Computation": r.get("computation", ""),
                "Basis": _basis(r),
            } for r in (ag.get("rows") or [])]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            if ag.get("material_divergences"):
                st.caption(f"⚠ {ag['material_divergences']} material divergence(s) "
                           "(≥2pp) between engine and business-grounded references.")
            rec = ag.get("reconciliation") or {}
            if rec.get("terminal_margin_verdict") or rec.get("capex_verdict"):
                traj = rec.get("segment_margin_trajectory")
                st.caption(
                    f"**Margin/CapEx reconciliation (memo #7):** terminal margin "
                    f"_{rec.get('terminal_margin_verdict')}_"
                    + (f" (segments {traj})" if traj else "")
                    + f"; forward capex _{rec.get('capex_verdict')}_.")

            # Apply-grounded affordance: push a grounded value into the DCF via
            # the existing validated PUT /dcf/overrides endpoint.
            appliable = [r for r in (ag.get("rows") or [])
                         if r.get("override_field") and r.get("override_value") is not None]
            if appliable:
                st.markdown("**Apply a grounded value as a DCF override**")
                for r in appliable:
                    c1, c2 = st.columns([4, 1])
                    c1.markdown(
                        f"{r['assumption']}: engine {_pct(r.get('engine_value'))} → "
                        f"grounded **{_pct(r.get('grounded_value'))}** "
                        f"(`{r['override_field']}`)")
                    if c2.button("Apply", key=f"apply_{ticker}_{r['override_field']}"):
                        from aletheia.ui.financials_view import _dcf_api
                        body = {r["override_field"]: r["override_value"],
                                "updated_by": "analyst",
                                "note": f"Grounded: {r.get('computation','')}"[:200]}
                        _, err = _dcf_api("PUT", f"/ticker/{ticker}/dcf/overrides", body)
                        if err:
                            st.error(f"Rejected: {err}")
                        else:
                            st.cache_data.clear()
                            st.success(f"Applied {r['override_field']} — recomputing…")
                            st.rerun()


__all__ = ["render_bottom_up_view"]
