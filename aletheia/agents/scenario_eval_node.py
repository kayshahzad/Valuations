"""
aletheia/agents/scenario_eval_node.py

Phase C.3 — scenario evaluation node.

Reads alternate scenarios proposed by the forensic / value_chain / context
agents (each agent's report contains a `scenarios: List[ScenarioOverride]`
field) and runs the DCFEngine against each. The calc layer still does the
math; agents only propose typed, bounded inputs.

For each scenario:
  - Clone the base ValuationProfile, applying only the bounded overrides
    that the scenario specifies (the others stay at the lifecycle default).
  - Build a fresh CalculationInput with the cloned profile.
  - Run DCFEngine.run() and capture the result.

Output state:
  state["scenario_results"]: List[Dict] — one entry per evaluated scenario
    {
      "name":                str,
      "scenario_type":       Literal["bull","bear","base_alternative"],
      "proposed_by":         Literal["forensic","value_chain","context"],
      "rationale":           str,
      "overrides_applied":   {field: value, ...},
      "dcf":                 DCFResult.to_dict() if successful, else None,
      "error":               Optional[str] if the run failed,
      "intrinsic_per_share_base": Optional[float],
      "upside_pct_base":     Optional[float],
    }

Architecture:
  - This node runs AFTER the 3 proposing agents (forensic, value_chain,
    context) and BEFORE contrarian/lead, so contrarian can reference
    scenarios by name and lead can persist them in the final report.
  - If no scenarios are proposed, the node writes an empty list and exits
    fast — the cost is one short-circuit check per ticker.
  - Calc-layer determinism is preserved: identical scenarios run on
    identical data produce identical DCF outputs.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage

from aletheia.contracts.interfaces import (
    CalculationInput,
    ScenarioOverride,
    ValuationProfile,
)
from aletheia.utils.calc_input_builder import make_calc_input
from aletheia.utils.tracing import tracer


def _collect_scenarios(state: Dict[str, Any]) -> List[ScenarioOverride]:
    """Pull scenarios from all three proposing-agent reports."""
    scenarios: List[ScenarioOverride] = []
    for key in ("forensic_report", "value_chain_report", "strategic_context_report"):
        report = state.get(key) or {}
        # Reports come through as dicts when written to LangGraph state. Each
        # `scenarios` entry is itself a dict (from Pydantic's serialization).
        for raw in (report.get("scenarios") or []):
            try:
                if isinstance(raw, ScenarioOverride):
                    scenarios.append(raw)
                else:
                    scenarios.append(ScenarioOverride.model_validate(raw))
            except Exception as e:
                print(f"  ⚠ Skipping malformed scenario from {key}: {e}")
    return scenarios


def _clone_profile_with_overrides(
    base: ValuationProfile, ov: ScenarioOverride
) -> ValuationProfile:
    """
    Build a new ValuationProfile that reflects the scenario's bounded
    overrides. Fields not specified by the override stay at the base
    profile's lifecycle defaults.

    Mapping:
      - revenue_growth_y1_5    → profile.growth_rate
      - terminal_growth        → profile.terminal_growth
      - terminal_margin_decay  → profile.terminal_margin_decay
        (Profile field is the *complement*: 1 - margin_compression. We
         translate ov.terminal_margin_decay (terminal/current ratio in [0.5,
         1.0]) to profile.terminal_margin_decay (1 - ratio in [0.0, 0.5]).)
      - revenue_growth_y6_10:  no direct profile field; absorbed into the
        DCFEngine's decay logic via _build_assumptions where the y6_10 CAGR
        is `hist_revenue_cagr * decay_base`. We can't override that
        directly without re-introducing kwargs, so we leave y6_10 to flow
        from y1_5 × decay. Documented as a known v1 limitation.

    Note `base_revenue_normalization` does not map to a ValuationProfile
    field — it overrides the base-year revenue used by DCFEngine. We
    handle that separately by constructing the calc_input dataframe with
    the overridden base revenue.
    """
    updates: Dict[str, Any] = {}
    if ov.revenue_growth_y1_5 is not None:
        updates["growth_rate"] = ov.revenue_growth_y1_5
    if ov.terminal_growth is not None:
        updates["terminal_growth"] = ov.terminal_growth
    if ov.terminal_margin_decay is not None:
        # Profile stores compression amount: 1.0 means terminal == current,
        # 0.5 means terminal is half of current.
        updates["terminal_margin_decay"] = max(0.0, 1.0 - ov.terminal_margin_decay)

    # Phase 3 — direct calc-input overrides for analyst scenarios. Routed
    # through ValuationProfile so DCFEngine.run() reads them via the same
    # path as lifecycle defaults.
    if ov.terminal_ebit_margin is not None:
        updates["terminal_ebit_margin_override"] = ov.terminal_ebit_margin
    if ov.capex_pct_revenue is not None:
        updates["capex_pct_revenue_override"] = ov.capex_pct_revenue
    if ov.discount_rate is not None:
        updates["discount_rate_override"] = ov.discount_rate
    if ov.tax_rate is not None:
        updates["tax_rate_override"] = ov.tax_rate
    # Phase 3.5 — close the long-standing y6_10 routing gap. Previously
    # the override was captured but never reached the engine.
    if ov.revenue_growth_y6_10 is not None:
        updates["revenue_growth_y6_10_override"] = ov.revenue_growth_y6_10

    return replace(base, **updates)


def _apply_base_revenue_normalization(
    calc_input: CalculationInput, ov: ScenarioOverride
) -> CalculationInput:
    """
    If the scenario overrides the base-year revenue, mutate a copy of the
    dataframe to substitute the latest fiscal year's clean_Revenue. Other
    fields are left as-is (the engine recomputes margins from the substituted
    revenue downstream).
    """
    if ov.base_revenue_normalization is None:
        return calc_input
    df = calc_input.df.copy()
    if df.empty or "clean_Revenue" not in df.columns:
        return calc_input
    fy_max = df["fiscal_year"].max()
    df.loc[df["fiscal_year"] == fy_max, "clean_Revenue"] = (
        ov.base_revenue_normalization
    )
    return CalculationInput(
        df=df,
        classification=calc_input.classification,
        known_issues=calc_input.known_issues,
        valuation_profile=calc_input.valuation_profile,
        lifecycle_thresholds=calc_input.lifecycle_thresholds,
    )


def _evaluate_scenario(
    base_calc_input: CalculationInput, ov: ScenarioOverride
) -> Dict[str, Any]:
    """Run the DCF for a single scenario and return a serializable summary."""
    from aletheia.tools.dcf_engine import DCFEngine

    new_profile = _clone_profile_with_overrides(
        base_calc_input.valuation_profile, ov
    )
    scenario_calc_input = CalculationInput(
        df=base_calc_input.df,
        classification=base_calc_input.classification,
        known_issues=base_calc_input.known_issues,
        valuation_profile=new_profile,
        lifecycle_thresholds=base_calc_input.lifecycle_thresholds,
    )
    scenario_calc_input = _apply_base_revenue_normalization(scenario_calc_input, ov)

    overrides_applied = {
        f: getattr(ov, f)
        for f in (
            "revenue_growth_y1_5",
            "revenue_growth_y6_10",
            "terminal_margin_decay",
            "terminal_growth",
            "base_revenue_normalization",
            "terminal_ebit_margin",
            "capex_pct_revenue",
            "discount_rate",
            "tax_rate",
        )
        if getattr(ov, f) is not None
    }

    summary: Dict[str, Any] = {
        "name":              ov.name,
        "scenario_type":     ov.scenario_type,
        "proposed_by":       ov.proposed_by,
        "rationale":         ov.rationale,
        "overrides_applied": overrides_applied,
        "dcf":               None,
        "error":             None,
        "intrinsic_per_share_base": None,
        "upside_pct_base":   None,
    }

    try:
        engine = DCFEngine(verbose=False)
        result = engine.run(scenario_calc_input)
        summary["dcf"] = result.to_dict()
        if result.base and result.current_price:
            ips = result.intrinsic_per_share(
                result.base.enterprise_value, result.net_debt
            )
            summary["intrinsic_per_share_base"] = float(ips) if ips else None
            if ips:
                summary["upside_pct_base"] = (ips / result.current_price - 1) * 100
    except NotImplementedError as e:
        summary["error"] = f"bypass: {e}"
    except Exception as e:
        summary["error"] = f"{type(e).__name__}: {e}"

    return summary


def scenario_eval_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """LangGraph node — evaluate every agent-proposed scenario."""
    print("---SCENARIO EVAL NODE---")

    ticker = state.get("ticker", "UNKNOWN")
    scenarios = _collect_scenarios(state)

    if not scenarios:
        print("  ⊘ No scenarios proposed; skipping scenario_eval.")
        out = {"scenario_results": []}
        tracer.log_step("ScenarioEvalNode", state, out)
        return out

    print(f"  ✓ Found {len(scenarios)} proposed scenario(s)")

    try:
        base_calc_input = make_calc_input(ticker)
    except Exception as e:
        print(f"  ✗ CalculationInput build failed for {ticker}: {e}")
        out = {"scenario_results": []}
        tracer.log_step("ScenarioEvalNode", state, out)
        return out

    results: List[Dict[str, Any]] = []
    for ov in scenarios:
        summary = _evaluate_scenario(base_calc_input, ov)
        if summary.get("error"):
            print(f"  ⚠ '{ov.name}' ({ov.proposed_by}): {summary['error']}")
        else:
            ips = summary.get("intrinsic_per_share_base")
            ups = summary.get("upside_pct_base")
            print(
                f"  ✓ '{ov.name}' ({ov.scenario_type}, by {ov.proposed_by}): "
                f"IPS=${ips:,.2f} upside={ups:+.1f}%"
                if ips is not None and ups is not None
                else f"  ✓ '{ov.name}' ({ov.scenario_type}, by {ov.proposed_by})"
            )
        results.append(summary)

    output: Dict[str, Any] = {
        "scenario_results": results,
        "messages": [HumanMessage(
            content=f"ScenarioEval: {ticker} — {len(results)} scenario(s) evaluated"
        )],
    }
    tracer.log_step("ScenarioEvalNode", state, output)
    return output
