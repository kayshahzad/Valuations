"""Bottom-up business analysis (memo §4 expansion) — Phase 0.

The deterministic / reuse half of the bottom-up layer:

  - Growth-source decomposition (organic vs M&A) — surfaces the split the DCF
    engine already computes internally (``_organic_cagr_ex_breaks``) but only
    prints to the console. Market-vs-share is deferred (needs a market-growth
    reference; Phase 5).
  - A coverage map of the 12 bottom-up dimensions: which are populated today
    (from existing extracted fields / qualitative dims) vs pending the Phase 2-3
    LLM extraction. Lets the memo show the bottom-up scaffold honestly.

No LLM call. The richer per-field content (TAM $, named contracts, R&D pipeline,
CAC/LTV) is added in later phases via structured extraction.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_growth_decomposition(calc) -> Dict[str, Any]:
    """Decompose historical revenue growth into organic vs M&A/regime-break.

    Reuses ``dcf_engine._organic_cagr_ex_breaks``. Returns ``available=False``
    when there's too little history. ``ma_contribution_pp`` is the percentage
    points of raw CAGR attributable to break years (raw − organic)."""
    out: Dict[str, Any] = {"available": False}
    try:
        from aletheia.tools.dcf_engine import _organic_cagr_ex_breaks
        df = getattr(calc, "df", None)
        if df is None or "clean_Revenue" not in getattr(df, "columns", []):
            return out
        d = df
        if "period" in d.columns:
            d = d[d["period"] == "FY"]
        d = d.dropna(subset=["clean_Revenue"]).sort_values("fiscal_year")
        revs = [float(x) for x in d["clean_Revenue"].tolist()]
        if len(revs) < 4:
            return out
        # Raw point-to-point CAGR over the last ≤5 fiscal years.
        k = min(5, len(revs) - 1)
        base = revs[-1 - k]
        raw_cagr = (revs[-1] / base) ** (1.0 / k) - 1.0 if base > 0 else None

        organic, break_years = _organic_cagr_ex_breaks(d)
        if organic is not None and break_years:
            ma_pp = (raw_cagr - organic) if raw_cagr is not None else None
            split = "organic + M&A/regime breaks"
        else:
            organic = raw_cagr  # no break → all organic
            ma_pp = 0.0
            break_years = []
            split = "all organic (no transformative break detected)"
        out.update({
            "available": raw_cagr is not None,
            "raw_cagr": raw_cagr,
            "organic_cagr": organic,
            "ma_contribution_pp": ma_pp,
            "break_years": break_years,
            "lookback_years": k,
            "split": split,
            # Market-vs-share split deferred — needs a market-growth reference.
            "market_growth_ref": None,
            "share_gain_pp": None,
            "source": "DCF organic/M&A break detection (deterministic)",
        })
    except Exception:
        return {"available": False}
    return out


# The 12 bottom-up dimensions, with how each is sourced today.
_COVERAGE = [
    ("A. What it sells", "Product / service portfolio", "business_model.revenue_segments"),
    ("A. What it sells", "Major customers / contracts", "business_model.key_customers"),
    ("A. What it sells", "Distribution channels", None),
    ("B. Market size", "TAM sizing", None),
    ("B. Market size", "Market share / position", "dim:market_position"),
    ("B. Market size", "Whitespace / adjacent TAMs", None),
    ("C. Unit economics", "Operating leverage", "business_model.operating_leverage_analysis"),
    ("C. Unit economics", "CAC / LTV / cohorts", None),
    ("C. Unit economics", "Margin trajectory by segment", None),
    ("D. Growth source", "Organic vs M&A", "growth_decomposition"),
    ("D. Growth source", "Market vs share", None),
    ("E. Innovation", "Disruption / R&D posture", "dim:technology_disruption_risk"),
    ("E. Innovation", "Acquisition strategy", "dim:capital_allocation_track_record"),
    ("E. Innovation", "New product launches", None),
    ("F. Industry", "Lifecycle stage", "classification.lifecycle"),
    ("F. Industry", "Competitive intensity", "dim:industry_concentration"),
    ("F. Industry", "Regulatory trajectory", "dim:regulatory_exposure"),
]


def _present(source: Optional[str], bm: Dict[str, Any], dims: Dict[str, Any],
             gd: Dict[str, Any], lifecycle: Optional[str]) -> bool:
    if not source:
        return False
    if source == "growth_decomposition":
        return bool(gd.get("available"))
    if source == "classification.lifecycle":
        return bool(lifecycle)
    if source.startswith("dim:"):
        return source.split(":", 1)[1] in dims
    if source.startswith("business_model."):
        key = source.split(".", 1)[1]
        v = bm.get(key)
        return bool(v)
    return False


def build_business_analysis(report: Optional[Dict[str, Any]], ticker: str,
                            calc=None) -> Dict[str, Any]:
    """Assemble the bottom-up block: growth decomposition + a coverage map of the
    12 dimensions (present today vs pending LLM extraction)."""
    out: Dict[str, Any] = {"available": False}
    bm = (((report or {}).get("1_economic_reality") or {}).get("business_model") or {})
    lifecycle = getattr(getattr(calc, "classification", None), "lifecycle", None)

    dims: Dict[str, Any] = {}
    try:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        try:
            dims = db.get_all_assessments_for_ticker(ticker) or {}
        finally:
            db.close()
    except Exception:
        dims = {}

    gd = build_growth_decomposition(calc) if calc is not None else {"available": False}

    coverage = []
    n_present = 0
    for theme, dimension, source in _COVERAGE:
        present = _present(source, bm, dims, gd, lifecycle)
        n_present += int(present)
        coverage.append({
            "theme": theme, "dimension": dimension,
            "status": "present" if present else "pending",
            "source": source or "needs extraction",
        })

    out.update({
        "available": True,
        "growth_decomposition": gd,
        "coverage": coverage,
        "n_present": n_present,
        "n_total": len(coverage),
        "lifecycle": lifecycle,
    })
    return out


def compose_business_analysis(ticker: str,
                              report: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """One-call composer for ticker-only surfaces (report rebuild). No LLM."""
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        calc = make_calc_input(ticker)
        return build_business_analysis(report, ticker, calc=calc)
    except Exception:
        return None


__all__ = ["build_growth_decomposition", "build_business_analysis",
           "compose_business_analysis"]
