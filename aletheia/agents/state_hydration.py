"""Hydrate LangGraph state from a serving-JSON report.

Used by partial-rerun endpoints (e.g. `/ticker/{T}/thesis_synthesis/refresh`)
to reconstruct the upstream-agent state without re-running librarian /
calc_node / qualitative_synthesis / contrarian / strategist / scenario_eval.

The serving JSON at `valuation_data/serving/latest/{T}_report.json` is
the canonical artifact: it carries every field the synthesizer's
`_summarize_*` projectors read. This module reshapes those fields back
into the state dict shape the agents expect.

Hydration is best-effort: missing optional fields default to {} or N/A,
matching the projectors' existing tolerance for partial state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


# ── Paths ────────────────────────────────────────────────────────────────

_SERVING_DIR = Path("valuation_data/serving/latest")


def serving_json_path(ticker: str) -> Path:
    return _SERVING_DIR / f"{ticker.upper()}_report.json"


def load_serving_json(ticker: str) -> Optional[Dict[str, Any]]:
    """Read the latest serving JSON for `ticker`. Returns None if missing."""
    path = serving_json_path(ticker)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ── State reshaping ──────────────────────────────────────────────────────

def hydrate_state_from_report(
    ticker: str,
    report: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a LangGraph state dict from a serving-JSON report.

    Returns the state shape thesis_synthesizer's `_summarize_*` projectors
    expect. NOT included: `qualitative_dashboard` (computed live by
    dashboard_fetch_node), `messages` (initialized by caller).
    """
    er = report.get("1_economic_reality", {}) or {}
    val = report.get("4_valuation_synthesis", {}) or {}
    risk = report.get("3_capital_structure_risk", {}) or {}

    moat = er.get("moat", {}) or {}
    vc = er.get("value_chain", {}) or {}
    sc = er.get("strategic_context", {}) or {}
    industry = er.get("industry_structure", {}) or {}
    bm = er.get("business_model", {}) or {}

    p2 = dict(val.get("phase2_valuation", {}) or {})
    rdcf = p2.get("reverse_dcf", {}) or {}

    # The synthesizer's _summarize_phase2 reads `implied_cagr`,
    # `historical_cagr`, `reverse_dcf_signal` at the top of phase2.
    # Denormalize from reverse_dcf so the projector finds them — but only
    # when the source has a real value, so we don't pollute an empty
    # phase2 with None entries.
    if "implied_cagr" not in p2 and rdcf.get("implied_cagr_10y") is not None:
        p2["implied_cagr"] = rdcf["implied_cagr_10y"]
    if "historical_cagr" not in p2:
        hist = rdcf.get("historical_cagr") or rdcf.get("historical_cagr_5y")
        if hist is not None:
            p2["historical_cagr"] = hist
    if "reverse_dcf_signal" not in p2 and rdcf.get("signal") is not None:
        p2["reverse_dcf_signal"] = rdcf["signal"]

    # Cyclicality lives under industry_structure in the serving JSON; the
    # state key is `cyclicality` with `z_score` (not `cyclicality_z_score`).
    cyclicality = {}
    if industry.get("cyclicality_z_score") is not None:
        cyclicality["z_score"] = industry["cyclicality_z_score"]
    if industry.get("is_peak") is not None:
        cyclicality["is_peak"] = industry["is_peak"]

    # forensic_report reshape: synthesizer reads {moat_score,
    # has_pricing_power, concentration_risk}. The serving JSON splits
    # these across moat, business_model, and capital_structure_risk.
    forensic_report = {
        "moat_score":              moat.get("score"),
        "has_pricing_power":       moat.get("has_pricing_power"),
        "concentration_risk":      risk.get("concentration_risk"),
        "operating_leverage_score": bm.get("operating_leverage_score"),
        "operating_leverage_analysis": bm.get("cost_structure"),
        "evidence":                moat.get("evidence"),
        "pricing_power_evidence":  moat.get("pricing_power_evidence"),
    }

    # value_chain_report reshape: serving has `upstream_leak`,
    # `strategic_leverage`, `substitution_risk_score`; the synthesizer
    # reads `upstream_value_leak`, `strategic_position`, `substitution_pressure`.
    value_chain_report = {
        "upstream_value_leak":     vc.get("upstream_leak"),
        "strategic_position":      vc.get("strategic_leverage"),
        "substitution_pressure":   vc.get("substitution_risk_score"),
        "power_ratio":             vc.get("power_ratio"),
        "bottleneck_analysis":     vc.get("bottleneck_analysis"),
        "top_substitutes":         vc.get("top_substitutes"),
        "pricing_power_assessment": vc.get("pricing_power_assessment"),
        "pass_through_capability": vc.get("pass_through_capability"),
        "analysis_summary":        vc.get("analysis_summary"),
    }

    # strategic_context_report reshape — synthesizer reads
    # cyclicality_classification (lives in industry_structure),
    # growth_quality (derived from quality_of_growth_risk),
    # intangible_decay_severity (derived from intangible_risk_assessment),
    # terminal_haircut (lives in strategic_context).
    growth_quality = "high"
    if sc.get("quality_of_growth_risk") is True:
        growth_quality = "at_risk"
    intangible_severity = "low"
    intang = sc.get("intangible_risk_assessment", "") or ""
    if "high" in intang.lower() or "severe" in intang.lower():
        intangible_severity = "high"
    elif "moderate" in intang.lower() or "medium" in intang.lower():
        intangible_severity = "moderate"
    cyc_class = "non_cyclical"
    z = industry.get("cyclicality_z_score")
    if z is not None:
        if abs(z) >= 2:
            cyc_class = "highly_cyclical"
        elif abs(z) >= 1:
            cyc_class = "moderately_cyclical"
    strategic_context_report = {
        "cyclicality_classification":  cyc_class,
        "growth_quality":              growth_quality,
        "intangible_decay_severity":   intangible_severity,
        "terminal_haircut":            sc.get("terminal_haircut"),
        "deferred_revenue_trend":      sc.get("deferred_revenue_trend"),
        "intangible_risk_assessment":  sc.get("intangible_risk_assessment"),
        "revenue_at_risk_percent":     sc.get("revenue_at_risk_percent"),
        "summary":                     sc.get("summary"),
    }

    # contrarian_report — serving stores it flat under contrarian_analysis;
    # the synthesizer reads `state.contrarian_report.structured_analysis.X`.
    contrarian_flat = val.get("contrarian_analysis", {}) or {}
    contrarian_report = {"structured_analysis": dict(contrarian_flat)}

    # conviction — synthesizer reads {position_tier, position_size_pct,
    # conviction_score, capped_total}. These all live in pillar_scores.
    pillar_scores = (val.get("investment_thesis", {}) or {}).get("pillar_scores", {}) or {}
    conviction = {
        "position_tier":     pillar_scores.get("position_tier"),
        "position_size_pct": pillar_scores.get("position_size_pct"),
        "conviction_score":  pillar_scores.get("conviction_score"),
        "capped_total":      pillar_scores.get("capped_total"),
    }

    scenario_results = val.get("agent_scenarios", []) or []

    return {
        "ticker":                   ticker.upper(),
        "phase2_valuation":         p2,
        "cyclicality":              cyclicality,
        "forensic_report":          forensic_report,
        "value_chain_report":       value_chain_report,
        "strategic_context_report": strategic_context_report,
        "contrarian_report":        contrarian_report,
        "scenario_results":         scenario_results,
        "conviction":               conviction,
        # raw_10k_text is intentionally omitted — thesis_synthesizer
        # doesn't read it, and we never want a partial rerun to re-fetch
        # the 10-K (that's a full-pipeline-only operation).
    }


def hydrate_state(ticker: str) -> Optional[Dict[str, Any]]:
    """Convenience: load + hydrate. Returns None if no serving JSON exists."""
    report = load_serving_json(ticker)
    if report is None:
        return None
    return hydrate_state_from_report(ticker, report)


__all__ = [
    "serving_json_path",
    "load_serving_json",
    "hydrate_state_from_report",
    "hydrate_state",
]
