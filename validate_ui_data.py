"""
validate_ui_data.py

Validates that every field the Streamlit UI reads from the report JSON
is present and correctly typed for a given ticker.

Run: python3 validate_ui_data.py MSFT
"""

import json
import sys
from pathlib import Path

TICKER = sys.argv[1] if len(sys.argv) > 1 else "MSFT"
REPORT_PATH = f"valuation_data/serving/latest/{TICKER}_report.json"

# ─────────────────────────────────────────────────────────────────────────────
# Load report
# ─────────────────────────────────────────────────────────────────────────────

try:
    report = json.loads(Path(REPORT_PATH).read_text())
    print(f"✅ Report loaded: {REPORT_PATH}\n")
except FileNotFoundError:
    print(f"❌ Report not found: {REPORT_PATH}")
    print(f"   Run: python3 main.py --ticker {TICKER}")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

passes = []
warnings = []
fails = []

def check(tab, field_path, expected_type=None, non_empty=False, min_val=None):
    """Navigate a dot-separated path and validate the value."""
    keys = field_path.split(".")
    obj  = report
    for k in keys:
        if isinstance(obj, dict):
            obj = obj.get(k)
        else:
            obj = None
            break

    label = f"[{tab}] {field_path}"

    if obj is None:
        fails.append(f"❌ {label} — MISSING")
        return

    if expected_type and not isinstance(obj, expected_type):
        type_name = expected_type.__name__ if not isinstance(expected_type, tuple) else " or ".join([t.__name__ for t in expected_type])
        fails.append(f"❌ {label} — wrong type: got {type(obj).__name__}, expected {type_name}. Value: {repr(obj)[:60]}")
        return

    if non_empty:
        if isinstance(obj, (str, list, dict)) and len(obj) == 0:
            warnings.append(f"⚠️  {label} — present but EMPTY")
            return
        if isinstance(obj, (int, float)) and obj == 0:
            warnings.append(f"⚠️  {label} — present but ZERO")
            return

    if min_val is not None and isinstance(obj, (int, float)) and obj < min_val:
        warnings.append(f"⚠️  {label} — value {obj} below minimum {min_val}")
        return

    val_preview = repr(obj)[:60] if not isinstance(obj, (int, float, bool)) else obj
    passes.append(f"✅ {label} = {val_preview}")


def check_list(tab, field_path, min_items=0):
    """Check a list field has at least min_items."""
    keys = field_path.split(".")
    obj  = report
    for k in keys:
        obj = obj.get(k) if isinstance(obj, dict) else None
    label = f"[{tab}] {field_path}"
    if obj is None:
        fails.append(f"❌ {label} — MISSING")
    elif not isinstance(obj, list):
        fails.append(f"❌ {label} — not a list: {type(obj).__name__}")
    elif len(obj) < min_items:
        warnings.append(f"⚠️  {label} — list has {len(obj)} items (expected >= {min_items})")
    else:
        passes.append(f"✅ {label} — list with {len(obj)} items")


# ─────────────────────────────────────────────────────────────────────────────
# TAB: Universe (reads from /universe endpoint — not report JSON directly)
# ─────────────────────────────────────────────────────────────────────────────

print("═" * 60)
print("UNIVERSE TAB — fields read from report JSON via /universe")
print("═" * 60)

check("Universe", "4_valuation_synthesis.phase2_valuation.reverse_dcf.implied_cagr_10y", float)
check("Universe", "4_valuation_synthesis.phase2_valuation.reverse_dcf.historical_cagr", float)
check("Universe", "4_valuation_synthesis.phase2_valuation.three_scenario_dcf.base.intrinsic_per_share", (int, float))
check("Universe", "4_valuation_synthesis.phase2_valuation.three_scenario_dcf.base.margin_of_safety", float)
check("Universe", "4_valuation_synthesis.phase2_valuation.multiple_decomposition.market_ev_ebitda", (int, float))
check("Universe", "4_valuation_synthesis.phase2_valuation.multiple_decomposition.justified_ev_ebitda", (int, float))
check("Universe", "4_valuation_synthesis.phase2_valuation.multiple_decomposition.premium_pct", float)
check("Universe", "4_valuation_synthesis.phase2_valuation.multiple_decomposition.signal", str)
check("Universe", "4_valuation_synthesis.phase2_valuation.multiple_decomposition.roic", (int, float))
check("Universe", "4_valuation_synthesis.investment_thesis.conviction_score", (int, float))
check("Universe", "1_economic_reality.moat.score", (int, float))
check("Universe", "2_financial_translation.clean_financials.fcf_bn", (int, float))


# ─────────────────────────────────────────────────────────────────────────────
# TAB: Deep Dive
# ─────────────────────────────────────────────────────────────────────────────

print("\n" + "═" * 60)
print("DEEP DIVE TAB — header metrics")
print("═" * 60)

check("DeepDive/Header", "4_valuation_synthesis.investment_thesis.conviction_score", (int, float))
check("DeepDive/Header", "4_valuation_synthesis.phase2_valuation.three_scenario_dcf.base.intrinsic_per_share", (int, float))
check("DeepDive/Header", "4_valuation_synthesis.phase2_valuation.three_scenario_dcf.base.margin_of_safety", float)
check("DeepDive/Header", "2_financial_translation.ratios.roic", (int, float))
check("DeepDive/Header", "4_valuation_synthesis.phase2_valuation.wacc", (int, float))
check("DeepDive/Header", "4_valuation_synthesis.phase2_valuation.multiple_decomposition.signal", str)

print("\n" + "─" * 60)
print("DEEP DIVE TAB — business profile (NEW)")
print("─" * 60)

check("DeepDive/BusinessProfile", "1_economic_reality.business_model.business_description", str, non_empty=True)
check("DeepDive/BusinessProfile", "1_economic_reality.business_model.operating_leverage_score", float)
check("DeepDive/BusinessProfile", "1_economic_reality.business_model.competitive_landscape", str, non_empty=True)
check("DeepDive/BusinessProfile", "1_economic_reality.business_model.regulatory_risk", str, non_empty=True)
check_list("DeepDive/BusinessProfile", "1_economic_reality.business_model.revenue_segments", min_items=1)
check_list("DeepDive/BusinessProfile", "1_economic_reality.business_model.key_customers", min_items=1)

print("\n" + "─" * 60)
print("DEEP DIVE TAB — 3-scenario DCF")
print("─" * 60)

for scenario in ["bear", "base", "bull"]:
    check("DeepDive/DCF", f"4_valuation_synthesis.phase2_valuation.three_scenario_dcf.{scenario}.intrinsic_per_share", (int, float))
    check("DeepDive/DCF", f"4_valuation_synthesis.phase2_valuation.three_scenario_dcf.{scenario}.margin_of_safety", float)

print("\n" + "─" * 60)
print("DEEP DIVE TAB — DCF adjustments audit trail (NEW)")
print("─" * 60)

check("DeepDive/Adjustments", "4_valuation_synthesis.phase2_valuation.dcf_adjustments.wacc_penalty", (int, float))
check("DeepDive/Adjustments", "4_valuation_synthesis.phase2_valuation.dcf_adjustments.growth_decay_reduction", (int, float))
check("DeepDive/Adjustments", "4_valuation_synthesis.phase2_valuation.dcf_adjustments.terminal_growth_adj", (int, float))
check("DeepDive/Adjustments", "4_valuation_synthesis.phase2_valuation.dcf_adjustments.concentration_risk", bool)
check("DeepDive/Adjustments", "4_valuation_synthesis.phase2_valuation.dcf_adjustments.has_pricing_power", bool)
check("DeepDive/Adjustments", "4_valuation_synthesis.phase2_valuation.dcf_adjustments.strategic_leverage_score", (int, float))
check("DeepDive/Adjustments", "4_valuation_synthesis.phase2_valuation.dcf_adjustments.rules", dict, non_empty=True)

print("\n" + "─" * 60)
print("DEEP DIVE TAB — moat breakdown (NEW)")
print("─" * 60)

check("DeepDive/Moat", "1_economic_reality.moat.score", (int, float), non_empty=True)
check("DeepDive/Moat", "1_economic_reality.moat.switching_costs", (int, float))
check("DeepDive/Moat", "1_economic_reality.moat.network_effects", (int, float))
check("DeepDive/Moat", "1_economic_reality.moat.cost_advantage", (int, float))
check("DeepDive/Moat", "1_economic_reality.moat.intangibles", (int, float))
check("DeepDive/Moat", "1_economic_reality.moat.evidence", str, non_empty=True)
check("DeepDive/Moat", "1_economic_reality.moat.has_pricing_power", bool)
check("DeepDive/Moat", "1_economic_reality.moat.pricing_power_evidence", str, non_empty=True)

print("\n" + "─" * 60)
print("DEEP DIVE TAB — ROIC vs WACC")
print("─" * 60)

check("DeepDive/ROIC", "2_financial_translation.ratios.roic", (int, float))
check("DeepDive/ROIC", "4_valuation_synthesis.phase2_valuation.wacc", (int, float))

print("\n" + "─" * 60)
print("DEEP DIVE TAB — value chain (extended)")
print("─" * 60)

check("DeepDive/ValueChain", "1_economic_reality.value_chain.strategic_leverage", (int, float))
check("DeepDive/ValueChain", "1_economic_reality.value_chain.power_ratio", (int, float))
check("DeepDive/ValueChain", "1_economic_reality.value_chain.upstream_leak", bool)
check("DeepDive/ValueChain", "1_economic_reality.value_chain.substitution_risk_score", (int, float))
check("DeepDive/ValueChain", "1_economic_reality.value_chain.bottleneck_analysis", str, non_empty=True)
check("DeepDive/ValueChain", "1_economic_reality.value_chain.analysis_summary", str, non_empty=True)

print("\n" + "─" * 60)
print("DEEP DIVE TAB — fundamentals")
print("─" * 60)

check("DeepDive/Fundamentals", "2_financial_translation.clean_financials.revenue_bn", (int, float))
check("DeepDive/Fundamentals", "2_financial_translation.clean_financials.ebitda_bn", (int, float))
check("DeepDive/Fundamentals", "2_financial_translation.clean_financials.fcf_bn", (int, float))
check("DeepDive/Fundamentals", "2_financial_translation.ratios.fcf_margin_pct", (int, float))

print("\n" + "─" * 60)
print("DEEP DIVE TAB — reverse DCF")
print("─" * 60)

check("DeepDive/RDCF", "4_valuation_synthesis.phase2_valuation.reverse_dcf.implied_cagr_10y", float)
check("DeepDive/RDCF", "4_valuation_synthesis.phase2_valuation.reverse_dcf.historical_cagr", float)
check("DeepDive/RDCF", "4_valuation_synthesis.phase2_valuation.reverse_dcf.signal", str)
check_list("DeepDive/RDCF", "4_valuation_synthesis.phase2_valuation.reverse_dcf.reasons")

print("\n" + "─" * 60)
print("DEEP DIVE TAB — 5-pillar conviction (NEW)")
print("─" * 60)

check("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.p1_moat", (int, float))
check("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.p2_health", (int, float))
check("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.p3_tailwind", (int, float))
check("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.p4_mos", (int, float))
check("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.p5_leadership", (int, float))
check("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.raw_total", (int, float))
check("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.capped_total", (int, float))
check("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.cap_applied", bool)
check("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.position_tier", str)
check_list("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.p1_reasons", min_items=1)
check_list("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.p2_reasons", min_items=1)
check_list("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.p3_reasons", min_items=1)
check_list("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.p4_reasons", min_items=1)
check_list("DeepDive/Pillars", "4_valuation_synthesis.investment_thesis.pillar_scores.p5_reasons", min_items=1)

print("\n" + "─" * 60)
print("DEEP DIVE TAB — contrarian analysis (NEW)")
print("─" * 60)

check("DeepDive/Contrarian", "4_valuation_synthesis.contrarian_analysis.bias_detected", str, non_empty=True)
check("DeepDive/Contrarian", "4_valuation_synthesis.contrarian_analysis.bear_case_summary", str, non_empty=True)
check("DeepDive/Contrarian", "4_valuation_synthesis.contrarian_analysis.sentiment_score", (int, float))
check("DeepDive/Contrarian", "4_valuation_synthesis.contrarian_analysis.quant_challenge", str, non_empty=True)

print("\n" + "─" * 60)
print("DEEP DIVE TAB — strategic context (NEW)")
print("─" * 60)

check("DeepDive/StratContext", "1_economic_reality.strategic_context.deferred_revenue_trend", str, non_empty=True)
check("DeepDive/StratContext", "1_economic_reality.strategic_context.quality_of_growth_risk", bool)
check("DeepDive/StratContext", "1_economic_reality.strategic_context.intangible_risk_assessment", str, non_empty=True)
check("DeepDive/StratContext", "1_economic_reality.strategic_context.revenue_at_risk_percent", (int, float))
check("DeepDive/StratContext", "1_economic_reality.strategic_context.terminal_haircut", bool)
check("DeepDive/StratContext", "1_economic_reality.strategic_context.summary", str, non_empty=True)

print("\n" + "─" * 60)
print("DEEP DIVE TAB — investment thesis narrative")
print("─" * 60)

check("DeepDive/Thesis", "4_valuation_synthesis.investment_thesis.narrative", str, non_empty=True)
check_list("DeepDive/Thesis", "4_valuation_synthesis.investment_thesis.constitution_checks", min_items=1)


# ─────────────────────────────────────────────────────────────────────────────
# TAB: Screening (reads from /ticker/{ticker}/screening — not report JSON)
# ─────────────────────────────────────────────────────────────────────────────

print("\n" + "═" * 60)
print("SCREENING TAB — sourced from /screening endpoint, not report JSON")
print("═" * 60)
print("  ℹ Screening data comes from quantitative_screens.py via API.")
print("  ℹ Cannot validate from report JSON — verify manually in UI.")


# ─────────────────────────────────────────────────────────────────────────────
# TAB: Constitution
# ─────────────────────────────────────────────────────────────────────────────

print("\n" + "═" * 60)
print("CONSTITUTION TAB")
print("═" * 60)

check("Constitution", "4_valuation_synthesis.investment_thesis.constitution_checks", list, non_empty=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB: Thesis Builder
# ─────────────────────────────────────────────────────────────────────────────

print("\n" + "═" * 60)
print("THESIS BUILDER TAB")
print("═" * 60)

check("ThesisBuilder", "4_valuation_synthesis.phase2_valuation.three_scenario_dcf.base.intrinsic_per_share", (int, float))
check("ThesisBuilder", "4_valuation_synthesis.phase2_valuation.three_scenario_dcf.base.margin_of_safety", float)
check("ThesisBuilder", "4_valuation_synthesis.phase2_valuation.multiple_decomposition.roic_wacc_spread", (int, float))
check("ThesisBuilder", "4_valuation_synthesis.investment_thesis.pillar_scores.lifecycle_stage", (str, type(None)))
check("ThesisBuilder", "4_valuation_synthesis.investment_thesis.narrative", str, non_empty=True)
check("ThesisBuilder", "4_valuation_synthesis.phase2_valuation.reverse_dcf.implied_cagr_10y", float)
check("ThesisBuilder", "4_valuation_synthesis.phase2_valuation.reverse_dcf.historical_cagr", float)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print("\n" + "═" * 60)
print(f"VALIDATION SUMMARY — {TICKER}")
print("═" * 60)
print(f"  ✅ PASS:    {len(passes)}")
print(f"  ⚠️  WARNING: {len(warnings)}")
print(f"  ❌ FAIL:    {len(fails)}")

if warnings:
    print("\nWARNINGS (present but empty/zero):")
    for w in warnings:
        print(f"  {w}")

if fails:
    print("\nFAILURES (missing or wrong type):")
    for f in fails:
        print(f"  {f}")

if not fails and not warnings:
    print("\n🎉 All fields present and correctly typed.")
    print("   UI should render correctly for all tabs.")
elif not fails:
    print("\n✅ No failures. Warnings are non-critical — UI will render with fallbacks.")
else:
    print(f"\n⚠️  {len(fails)} failures found. Run pipeline first or check lead.py schema.")

sys.exit(0 if not fails else 1)
