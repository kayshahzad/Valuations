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

from typing import Any, Dict, List, Optional


def _row(assumption, engine, grounded, basis, source, note=""):
    delta = None
    if isinstance(engine, (int, float)) and isinstance(grounded, (int, float)):
        delta = engine - grounded
    return {
        "assumption": assumption,
        "engine_value": engine,
        "grounded_value": grounded,
        "delta": delta,
        "grounded_basis": basis,
        "source": source,
        "note": note,
        "status": "grounded" if grounded is not None else "pending",
    }


def build_assumption_grounding(
    calc, result, *,
    growth_decomposition: Optional[Dict[str, Any]] = None,
    current_state: Optional[Dict[str, Any]] = None,
    wacc_analysis: Optional[Dict[str, Any]] = None,
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
    rows.append(_row(
        "Y1-5 revenue CAGR", eng_cagr, grounded_cagr,
        "organic historical + forward consensus",
        "growth decomposition + current-state consensus",
        " · ".join(note)))

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
        "gap means an override moved it off the structural anchor."))

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

    # Count rows where engine and grounded diverge materially.
    material = sum(1 for r in rows if isinstance(r["delta"], (int, float))
                   and abs(r["delta"]) >= 0.02)
    out.update({
        "available": True,
        "rows": rows,
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
        from aletheia.tools.business_analysis import build_growth_decomposition
        from aletheia.tools.wacc_analysis import build_wacc_analysis
        from aletheia.agents.current_state import compose_current_state
        calc = make_calc_input(ticker)
        result = DCFEngine(verbose=False).run(calc)
        gd = build_growth_decomposition(calc)
        wa = build_wacc_analysis(result)
        cs = compose_current_state(ticker)
        return build_assumption_grounding(
            calc, result, growth_decomposition=gd,
            current_state=cs, wacc_analysis=wa)
    except Exception:
        return None


__all__ = ["build_assumption_grounding", "compose_assumption_grounding"]
