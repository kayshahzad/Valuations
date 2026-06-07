"""Assumption grounding (memo §4→§6/§7 keystone) — Phase 1.

The bottom-up layer's payoff: compare each top-down DCF assumption (the engine
value, anchored to history) against a BUSINESS-GROUNDED reference, and surface
the gap. Shown for triangulation — NOT auto-applied to the headline IV (same
discipline as the WACC premia). This is what turns "historically extrapolated"
assumptions into business-grounded ones the analyst can interrogate.

Rows built from data we already have (no LLM, no new fetch beyond cached dims):
  - Y1-5 revenue CAGR   ← organic historical CAGR + forward consensus
  - Terminal growth     ← industry lifecycle stage
  - Idiosyncratic WACC  ← disruption-risk + customer-concentration dimensions
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def _row(assumption, engine, grounded, basis, source, note="", build_up=None,
         override_field=None, override_value=None, computation=""):
    delta = None
    if isinstance(engine, (int, float)) and isinstance(grounded, (int, float)):
        delta = engine - grounded
    # The value that would be pushed to the DCF override store if the analyst
    # accepts the grounded reference (defaults to the grounded value itself).
    ov = override_value if override_value is not None else grounded
    return {
        "assumption": assumption,
        "engine_value": engine,
        "grounded_value": grounded,
        "delta": delta,
        "grounded_basis": basis,
        "source": source,
        "note": note,
        "build_up": build_up,            # optional driver decomposition (P4)
        "computation": computation,      # how the grounded value was derived (shown)
        "override_field": override_field, # DCF override field this row can write
        "override_value": ov if (override_field and isinstance(ov, (int, float))) else None,
        "status": "grounded" if grounded is not None else "pending",
    }


def _parse_pct(text: str) -> Optional[float]:
    """Parse the first 'X%' in free text → decimal (6.2% → 0.062)."""
    if not isinstance(text, str):
        return None
    m = re.search(r"(-?[0-9]+(?:\.[0-9]+)?)\s*%", text)
    if not m:
        return None
    try:
        return float(m.group(1)) / 100.0
    except Exception:
        return None


def _hist_capex_pct(calc) -> Optional[float]:
    """Median |CapEx| / revenue over available FY history (the deterministic
    grounding for the forward capex % assumption)."""
    try:
        import statistics
        df = getattr(calc, "df", None)
        if df is None:
            return None
        cols = getattr(df, "columns", [])
        cap_col = "derived_CapEx" if "derived_CapEx" in cols else (
            "raw_CapEx" if "raw_CapEx" in cols else None)
        if cap_col is None or "clean_Revenue" not in cols:
            return None
        d = df
        if "period" in cols:
            d = d[d["period"] == "FY"]
        ratios = []
        for _, r in d.iterrows():
            cap, rev = r.get(cap_col), r.get("clean_Revenue")
            if isinstance(cap, (int, float)) and isinstance(rev, (int, float)) and rev > 0:
                ratios.append(abs(float(cap)) / float(rev))
        return statistics.median(ratios[-5:]) if len(ratios) >= 3 else None
    except Exception:
        return None


def build_assumption_grounding(
    calc, result, *,
    growth_decomposition: Optional[Dict[str, Any]] = None,
    current_state: Optional[Dict[str, Any]] = None,
    wacc_analysis: Optional[Dict[str, Any]] = None,
    segment_economics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the assumption-grounding comparison. ``available=False`` when the
    base scenario is missing."""
    out: Dict[str, Any] = {"available": False, "rows": []}
    base = getattr(result, "base", None)
    if base is None:
        return out
    asm = base.assumptions
    rows: List[Dict[str, Any]] = []

    # ── Y1-5 revenue CAGR ← organic historical + consensus ──────────────
    eng_cagr = float(getattr(asm, "revenue_cagr_y1_5", 0) or 0)
    gd = growth_decomposition or {}
    organic = gd.get("organic_cagr")
    cons = ((current_state or {}).get("consensus") or {})
    cons_cagr = cons.get("forward_cagr") or cons.get("y1_growth")
    # Grounded reference: blend organic-historical with consensus when both exist
    # (consensus is the forward check; organic is the undistorted base).
    refs = [v for v in (organic, cons_cagr) if isinstance(v, (int, float))]
    grounded_cagr = (sum(refs) / len(refs)) if refs else None
    note = []
    if isinstance(organic, (int, float)):
        note.append(f"organic {organic*100:.1f}%")
    if isinstance(cons_cagr, (int, float)):
        note.append(f"consensus {cons_cagr*100:.1f}%")
    if gd.get("break_years"):
        note.append(f"M&A breaks FY{gd['break_years']}")
    # P4 — driver build-up: market tailwind + share gain (peer-relative) + M&A
    # run-rate → a defensible forward band, vs the engine's single number.
    build_up = None
    if isinstance(organic, (int, float)):
        mkt = gd.get("market_growth_ref")
        share = gd.get("share_gain_pp")
        ma_run = gd.get("ma_contribution_pp")
        ma_run = ma_run if isinstance(ma_run, (int, float)) else 0.0
        band_low = organic                       # organic only, no future M&A
        band_high = organic + max(ma_run, 0.0)   # + continued M&A run-rate
        build_up = {
            "market_growth": mkt if isinstance(mkt, (int, float)) else None,
            "share_gain": share if isinstance(share, (int, float)) else None,
            "organic": organic,
            "ma_run_rate": ma_run,
            "band_low": band_low,
            "band_high": band_high,
        }
    cagr_comp = (f"mean({' , '.join(note)})" if refs else "no organic/consensus inputs")
    rows.append(_row(
        "Y1-5 revenue CAGR", eng_cagr, grounded_cagr,
        "organic historical + forward consensus",
        "growth decomposition + current-state consensus",
        " · ".join(note), build_up=build_up,
        override_field="revenue_growth_y1_5", computation=cagr_comp))

    # ── Terminal growth ← industry lifecycle stage ──────────────────────
    eng_tg = float(getattr(asm, "terminal_growth", 0) or 0)
    lifecycle = getattr(getattr(calc, "classification", None), "lifecycle", None)
    life_ref = None
    try:
        from config.valuation_defaults import LIFECYCLE_PROFILES
        prof = LIFECYCLE_PROFILES.get(lifecycle or "mature")
        life_ref = float(getattr(prof, "terminal_growth", None)) if prof else None
    except Exception:
        life_ref = None
    rows.append(_row(
        "Terminal growth", eng_tg, life_ref,
        f"lifecycle stage = {lifecycle or 'mature'}",
        "lifecycle profile",
        "engine derives terminal growth from the same lifecycle profile; a large "
        "gap means an override moved it off the structural anchor.",
        override_field="terminal_growth",
        computation=f"LIFECYCLE_PROFILES['{lifecycle or 'mature'}'].terminal_growth"))

    # ── Terminal EBIT margin ← current margin + segment mix (P4) ─────────
    eng_term_margin = getattr(asm, "ebit_margin_terminal", None)
    eng_term_margin = float(eng_term_margin) if isinstance(eng_term_margin, (int, float)) else None
    cur_margin = getattr(asm, "ebit_margin_current", None)
    cur_margin = float(cur_margin) if isinstance(cur_margin, (int, float)) else None
    seg = segment_economics or {}
    grounded_margin = cur_margin            # anchor: terminal ≈ current absent a reason
    margin_basis = "current EBIT margin"
    margin_note = []
    if isinstance(cur_margin, (int, float)):
        margin_note.append(f"current {cur_margin*100:.1f}%")
    # If segment margins are disclosed, weight them by revenue mix for a
    # mix-grounded reference and flag the directional drift.
    if seg.get("available") and seg.get("has_margins"):
        wsum = w = 0.0
        improving = declining = 0
        for s in seg.get("segments", []):
            m = _parse_pct(s.get("margin") or "")
            rp = s.get("rev_pct")
            if m is not None and isinstance(rp, (int, float)):
                wsum += m * rp; w += rp
            trend = (s.get("margin_trend") or "").lower()
            if "improv" in trend or "rising" in trend or "expand" in trend:
                improving += 1
            elif "declin" in trend or "falling" in trend or "compress" in trend:
                declining += 1
        if w > 0:
            grounded_margin = wsum / w
            margin_basis = "revenue-weighted segment margins"
            margin_note.append(f"mix-weighted {grounded_margin*100:.1f}%")
        if improving or declining:
            margin_note.append(f"segments improving {improving} / declining {declining}")
    margin_comp = ("Σ(segment margin × rev %)" if margin_basis.startswith("revenue-weighted")
                   else "current EBIT margin (held to terminal)")
    rows.append(_row(
        "Terminal EBIT margin", eng_term_margin, grounded_margin,
        margin_basis, "segment economics (FMP mix + extraction)",
        " · ".join(margin_note) or "no segment margins disclosed yet",
        override_field="terminal_ebit_margin", computation=margin_comp))

    # ── Idiosyncratic WACC premium ← disruption + concentration dims ────
    dims: Dict[str, Any] = {}
    try:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        try:
            for d in ("technology_disruption_risk", "customer_concentration"):
                a = db.get_latest_assessment(getattr(result, "ticker", "") or "", d)
                if a:
                    dims[d] = a
        finally:
            db.close()
    except Exception:
        dims = {}

    def _score(dim_id):
        a = dims.get(dim_id) or {}
        s = a.get("score")
        return float(s) if isinstance(s, (int, float)) else None

    disruption = _score("technology_disruption_risk")  # 1-7, higher = lower risk
    concentration = _score("customer_concentration")
    grounded_prem = None
    prem_note = []
    if disruption is not None or concentration is not None:
        grounded_prem = 0.0
        if disruption is not None:
            if disruption <= 3:
                grounded_prem += 0.010; prem_note.append(f"high disruption risk ({disruption:.0f}/7)")
            elif disruption <= 4:
                grounded_prem += 0.005; prem_note.append(f"moderate disruption ({disruption:.0f}/7)")
        if concentration is not None:
            if concentration <= 3:
                grounded_prem += 0.010; prem_note.append(f"high customer concentration ({concentration:.0f}/7)")
            elif concentration <= 4:
                grounded_prem += 0.005; prem_note.append(f"moderate concentration ({concentration:.0f}/7)")
        grounded_prem = min(grounded_prem, 0.025)
    eng_prem = ((wacc_analysis or {}).get("premia") or {}).get("idiosyncratic")
    rows.append(_row(
        "Idiosyncratic WACC premium", eng_prem, grounded_prem,
        "disruption-risk + customer-concentration dimensions",
        "qualitative dimensions",
        " · ".join(prem_note) or "no qualitative risk dimensions assessed yet"))

    # ── Y6-10 revenue CAGR ← fade toward market / terminal (bridge) ─────
    eng_y6_10 = float(getattr(asm, "revenue_cagr_y6_10", 0) or 0)
    mkt_ref = gd.get("market_growth_ref")
    grounded_y6_10 = y6_basis = y6_comp = None
    if isinstance(mkt_ref, (int, float)):
        grounded_y6_10 = mkt_ref
        y6_basis = "peer-set market growth (company-specific share gains fade by yr 6-10)"
        y6_comp = f"peer median revenue CAGR {mkt_ref*100:.1f}%"
    elif isinstance(grounded_cagr, (int, float)) and isinstance(life_ref, (int, float)):
        grounded_y6_10 = (grounded_cagr + life_ref) / 2.0
        y6_basis = "linear fade from Y1-5 toward terminal growth"
        y6_comp = f"mean(Y1-5 {grounded_cagr*100:.1f}%, terminal {life_ref*100:.1f}%)"
    rows.append(_row(
        "Y6-10 revenue CAGR", eng_y6_10, grounded_y6_10,
        y6_basis or "fade reference unavailable",
        "growth decomposition + lifecycle terminal",
        y6_comp or "no fade reference", override_field="revenue_growth_y6_10",
        computation=y6_comp or ""))

    # ── CapEx % of revenue ← historical median (bridge) ────────────────
    eng_capex = getattr(asm, "capex_pct_revenue", None)
    eng_capex = float(eng_capex) if isinstance(eng_capex, (int, float)) else None
    hist_capex = _hist_capex_pct(calc)
    rows.append(_row(
        "CapEx % of revenue", eng_capex, hist_capex,
        "trailing FY median capital intensity",
        "cleaned financials (FMP/SEC)",
        (f"median {hist_capex*100:.1f}%" if hist_capex is not None
         else "insufficient capex history"),
        override_field="capex_pct_revenue",
        computation="median(|CapEx| / revenue, last 5 FY)"))

    # ── Terminal ROIC ← half-fade base ROIC toward WACC (bridge) ───────
    # The engine HOLDS base ROIC into perpetuity (moat persists). The grounded
    # reference fades it halfway to WACC — the academic "returns erode" prior —
    # so the analyst sees the methodology gap explicitly and can override.
    eng_roic = float(getattr(asm, "terminal_roic", 0) or 0)
    base_roic = float(getattr(asm, "base_roic", 0) or 0)
    wacc = float(getattr(asm, "wacc", 0) or 0)
    grounded_roic = roic_comp = None
    if base_roic > 0 and wacc > 0:
        grounded_roic = (base_roic + wacc) / 2.0
        roic_comp = f"mean(base ROIC {base_roic*100:.1f}%, WACC {wacc*100:.1f}%)"
    rows.append(_row(
        "Terminal ROIC", eng_roic, grounded_roic,
        "half-fade of base ROIC toward WACC (returns-erode prior)",
        "engine base ROIC + WACC",
        (roic_comp + " — engine instead holds base ROIC" if roic_comp
         else "base ROIC / WACC unavailable"),
        override_field="terminal_roic", computation=roic_comp or ""))

    # ── Margin / CapEx reconciliation (memo #7) ─────────────────────────
    # Synthesize a verdict pairing the DCF's terminal-margin + capex path with
    # the business reality (segment-margin trajectory + historical capex).
    def _verdict(engine_v, grounded_v, tol=0.01):
        if not isinstance(engine_v, (int, float)) or not isinstance(grounded_v, (int, float)):
            return "n/a"
        if engine_v > grounded_v + tol:
            return "engine above business reference"
        if engine_v < grounded_v - tol:
            return "engine below business reference"
        return "aligned"
    seg_dir = None
    if seg.get("available") and seg.get("has_margins"):
        imp = sum(1 for s in seg.get("segments", [])
                  if "improv" in (s.get("margin_trend") or "").lower()
                  or "rising" in (s.get("margin_trend") or "").lower())
        dec = sum(1 for s in seg.get("segments", [])
                  if "declin" in (s.get("margin_trend") or "").lower()
                  or "compress" in (s.get("margin_trend") or "").lower())
        seg_dir = ("improving" if imp > dec else "declining" if dec > imp else "mixed")
    reconciliation = {
        "terminal_margin_verdict": _verdict(eng_term_margin, grounded_margin),
        "segment_margin_trajectory": seg_dir,
        "capex_verdict": _verdict(eng_capex, hist_capex),
        "note": ("Terminal margin reconciles to the revenue-weighted segment mix "
                 "and forward capex to trailing capital intensity (memo #7)."),
    }

    # Count rows where engine and grounded diverge materially.
    material = sum(1 for r in rows if isinstance(r["delta"], (int, float))
                   and abs(r["delta"]) >= 0.02)
    out.update({
        "available": True,
        "rows": rows,
        "reconciliation": reconciliation,
        "material_divergences": material,
        "note": "Business-grounded references are shown for triangulation; they "
                "are NOT auto-applied to the headline IV.",
    })
    return out


def compose_assumption_grounding(ticker: str) -> Optional[Dict[str, Any]]:
    """One-call composer for ticker-only surfaces (report rebuild). No LLM."""
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.dcf_engine import DCFEngine
        from aletheia.tools.business_analysis import (
            build_growth_decomposition, segment_economics as _seg_econ)
        from aletheia.tools.wacc_analysis import build_wacc_analysis
        from aletheia.agents.current_state import compose_current_state
        from aletheia.agents.business_extraction import cached_business_ab
        calc = make_calc_input(ticker)
        result = DCFEngine(verbose=False).run(calc)
        gd = build_growth_decomposition(calc)
        wa = build_wacc_analysis(result)
        cs = compose_current_state(ticker)
        seg = _seg_econ(ticker, (cached_business_ab(ticker) or {}).get("segment_economics"))
        return build_assumption_grounding(
            calc, result, growth_decomposition=gd,
            current_state=cs, wacc_analysis=wa, segment_economics=seg)
    except Exception:
        return None


__all__ = ["build_assumption_grounding", "compose_assumption_grounding"]
