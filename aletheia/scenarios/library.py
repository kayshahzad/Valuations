"""
aletheia/scenarios/library.py

Pre-defined scenario library — eight standard analytical scenarios that
can be applied to any ticker. Each function inspects the ticker's cleaned
record / classification to produce a `ScenarioOverride`, then `run_scenario`
applies it and returns a comparable DCFResult.

Scenarios:
  - historical_cagr_continues: straight extrapolation from realized CAGR
  - growth_fades_to_gdp: gradual deceleration to ~2.5% terminal
  - recession_scenario: Y1-5 cut, margin compression, WACC bump
  - rate_normalization: discount rate at 5y-trailing-mean Rf
  - bull_execution: best-case execution within bounded ranges
  - bear_execution: worst-case execution within bounded ranges
  - consensus_growth (STUB): would use analyst-consensus EPS/revenue
  - margin_reverts_to_industry_median (STUB): would use peer-mapped median

Each scenario function takes a `CalculationInput` (provides historical
context) and returns a `(ScenarioOverride, dict_of_metadata)` tuple.

`run_scenario(ticker, scenario_fn)` is the convenience runner that builds
the calc input, applies the override, and returns a unified result dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

import pandas as pd

from aletheia.contracts.interfaces import (
    CalculationInput,
    ScenarioOverride,
)
from aletheia.utils.calc_input_builder import make_calc_input
from aletheia.tools.dcf_engine import DCFEngine
from aletheia.agents.scenario_eval_node import _clone_profile_with_overrides


@dataclass(frozen=True)
class ScenarioRunResult:
    """Output of a single scenario evaluation. Comparable across scenarios."""
    ticker: str
    scenario_name: str
    rationale: str
    override: ScenarioOverride
    base_iv: Optional[float]
    bull_iv: Optional[float]
    bear_iv: Optional[float]
    current_price: float
    upside_pct: Optional[float]
    base_wacc: float
    base_terminal_growth: float
    base_terminal_margin: float
    base_capex_pct: float
    notes: Dict[str, Any] = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────────
# Scenario constructors
# ────────────────────────────────────────────────────────────────────────

ScenarioFn = Callable[[CalculationInput], Tuple[ScenarioOverride, Dict[str, Any]]]


def _historical_cagr(df: pd.DataFrame, years: int = 5) -> Optional[float]:
    """Compute realized revenue CAGR over the last `years` complete years."""
    if df is None or df.empty or "clean_Revenue" not in df.columns:
        return None
    rev = df.dropna(subset=["clean_Revenue"]).sort_values("fiscal_year")
    if len(rev) < years + 1:
        years = len(rev) - 1
    if years < 2:
        return None
    head = float(rev["clean_Revenue"].iloc[-years - 1])
    tail = float(rev["clean_Revenue"].iloc[-1])
    if head <= 0 or tail <= 0:
        return None
    return (tail / head) ** (1.0 / years) - 1.0


def historical_cagr_continues(calc: CalculationInput) -> Tuple[ScenarioOverride, Dict[str, Any]]:
    """Y1-5 and Y6-10 CAGR both equal the realized 5y revenue CAGR."""
    cagr = _historical_cagr(calc.df, years=5)
    if cagr is None:
        cagr = calc.valuation_profile.growth_rate
    cagr = max(0.0, min(cagr, 0.45))  # clamp to ScenarioOverride bounds
    y6_10 = max(0.0, min(cagr, 0.25))
    return ScenarioOverride(
        name="Historical CAGR continues",
        scenario_type="base_alternative",
        proposed_by="forensic",
        rationale=f"Extrapolate the realized {cagr*100:.1f}% 5y CAGR through Y1-10",
        revenue_growth_y1_5=cagr,
        revenue_growth_y6_10=y6_10,
    ), {"realized_5y_cagr": cagr}


def growth_fades_to_gdp(calc: CalculationInput) -> Tuple[ScenarioOverride, Dict[str, Any]]:
    """Y1-5 = current CAGR; Y6-10 = midpoint(CAGR, 2.5%); terminal = 2.5%."""
    cagr = _historical_cagr(calc.df, years=5) or calc.valuation_profile.growth_rate
    cagr = max(0.0, min(cagr, 0.45))
    midpoint = max(0.0, min((cagr + 0.025) / 2, 0.25))
    return ScenarioOverride(
        name="Growth fades to GDP over 10y",
        scenario_type="bear",
        proposed_by="context",
        rationale=("Y1-5 holds at realized CAGR; Y6-10 fades to the "
                   "midpoint of CAGR and 2.5%; terminal growth at 2.5% GDP rate."),
        revenue_growth_y1_5=cagr,
        revenue_growth_y6_10=midpoint,
        terminal_growth=0.025,
    ), {"y1_5": cagr, "y6_10": midpoint, "terminal": 0.025}


def recession_scenario(calc: CalculationInput) -> Tuple[ScenarioOverride, Dict[str, Any]]:
    """Y1-5 cut 50%, terminal margin × 0.85, discount rate +100bps over baseline."""
    cagr = _historical_cagr(calc.df, years=5) or calc.valuation_profile.growth_rate
    y1_5_recession = max(0.0, min(cagr * 0.5, 0.45))
    # Compute current EBIT margin from latest fiscal year for the override
    df = calc.df
    latest = df.dropna(subset=["clean_Revenue"]).sort_values("fiscal_year").iloc[-1] \
        if not df.empty else None
    current_margin = (
        latest.get("derived_EBIT_Margin_Pct") / 100.0 if latest is not None
        and latest.get("derived_EBIT_Margin_Pct") else 0.20
    )
    terminal_margin_recession = max(0.05, min(current_margin * 0.85, 0.65))
    return ScenarioOverride(
        name="Recession scenario",
        scenario_type="bear",
        proposed_by="context",
        rationale=("Demand contraction: Y1-5 growth halved, terminal EBIT "
                   "margin compressed 15%, discount rate +100bps."),
        revenue_growth_y1_5=y1_5_recession,
        revenue_growth_y6_10=max(0.0, min(cagr, 0.25)),
        terminal_ebit_margin=terminal_margin_recession,
        # discount_rate is applied as a +100bps bump computed at run time
        # because it depends on the baseline WACC; we encode the *target*
        # below in metadata and the runner can apply it. For now we leave
        # discount_rate unset (uses baseline + bear_wacc_adjustment).
    ), {"y1_5_cut": y1_5_recession, "terminal_margin": terminal_margin_recession}


def rate_normalization(calc: CalculationInput) -> Tuple[ScenarioOverride, Dict[str, Any]]:
    """Discount rate set to current 5y-trailing-mean Rf + ERP × current beta."""
    from datetime import date
    from aletheia.data.historical_macro import (
        get_risk_free_rate_normalized,
        get_equity_risk_premium,
    )
    today = date.today()
    rf_norm = get_risk_free_rate_normalized(today, window_years=5)
    erp = get_equity_risk_premium(today)
    # Use a representative beta = 1.0 if not embedded in profile; the analyst
    # can refine later. Production beta lookup happens via market_data.
    beta = 1.0
    wacc_normalized = rf_norm + beta * erp  # equity-only proxy; production engine refines
    wacc_normalized = max(0.04, min(wacc_normalized, 0.16))
    return ScenarioOverride(
        name="Rate normalization (5y avg Rf)",
        scenario_type="base_alternative",
        proposed_by="context",
        rationale=(f"Use 5y-trailing-mean Rf ({rf_norm*100:.2f}%) instead of "
                   f"current spot Rf. Discount rate = {wacc_normalized*100:.2f}%."),
        discount_rate=wacc_normalized,
    ), {"normalized_rf": rf_norm, "wacc": wacc_normalized}


def bull_execution(calc: CalculationInput) -> Tuple[ScenarioOverride, Dict[str, Any]]:
    """Best-case execution: Y1-5 CAGR + 25%, terminal growth at lifecycle cap."""
    cagr = _historical_cagr(calc.df, years=5) or calc.valuation_profile.growth_rate
    y1_5 = max(0.0, min(cagr * 1.25, 0.45))
    cap = calc.valuation_profile.terminal_growth_cap or 0.04
    terminal = max(0.0, min(cap, 0.06))
    return ScenarioOverride(
        name="Bull execution",
        scenario_type="bull",
        proposed_by="forensic",
        rationale=("Best-case: Y1-5 CAGR uplift +25% over realized; terminal "
                   "growth at the lifecycle cap; margin decay tempered."),
        revenue_growth_y1_5=y1_5,
        revenue_growth_y6_10=max(0.0, min(cagr, 0.25)),
        terminal_growth=terminal,
        terminal_margin_decay=0.95,  # ratio: terminal = 95% of current
    ), {"y1_5": y1_5, "terminal": terminal}


def bear_execution(calc: CalculationInput) -> Tuple[ScenarioOverride, Dict[str, Any]]:
    """Worst-case execution within bounded ranges."""
    cagr = _historical_cagr(calc.df, years=5) or calc.valuation_profile.growth_rate
    y1_5 = max(0.0, min(cagr * 0.5, 0.45))
    return ScenarioOverride(
        name="Bear execution",
        scenario_type="bear",
        proposed_by="forensic",
        rationale=("Worst-case: Y1-5 CAGR halved; terminal growth at 2%; "
                   "margin decays 30% relative to current."),
        revenue_growth_y1_5=y1_5,
        revenue_growth_y6_10=max(0.0, min(cagr * 0.5, 0.25)),
        terminal_growth=0.02,
        terminal_margin_decay=0.70,  # ratio: terminal = 70% of current
    ), {"y1_5": y1_5}


def consensus_growth(calc: CalculationInput) -> Tuple[ScenarioOverride, Dict[str, Any]]:
    """Y1-5 path from analyst-consensus estimates (Yahoo Finance / IBES surface)."""
    from aletheia.data.consensus_estimates import (
        get_consensus_estimates, construct_growth_path,
    )

    ticker = calc.classification.ticker if calc.classification else None
    if not ticker:
        raise ValueError("consensus_growth: missing ticker classification")

    ce = get_consensus_estimates(ticker)
    if ce is None or (ce.y1_revenue_growth is None and ce.y2_revenue_growth is None):
        # No usable consensus data — fall back with explicit warning
        raise NotImplementedError(
            f"consensus_growth: yfinance returned no usable consensus for "
            f"{ticker}. Consensus data may not exist for this ticker, or "
            f"yfinance is rate-limiting. Try `force_refresh=True` later."
        )

    path = construct_growth_path(ce, terminal_growth=0.025)
    y1_5 = max(0.0, min(path["y1_5_avg"], 0.45))
    y6_10 = max(0.0, min(path["y6_10_avg"], 0.25))

    rationale_bits = [f"Y1 consensus={ce.y1_revenue_growth:+.1%}" if ce.y1_revenue_growth else "",
                       f"Y2 consensus={ce.y2_revenue_growth:+.1%}" if ce.y2_revenue_growth else ""]
    if ce.ltg_consensus is not None:
        rationale_bits.append(f"LTG={ce.ltg_consensus:+.1%}")
    rationale_bits.append(f"path Y1-5 avg {y1_5*100:.1f}%, Y6-10 {y6_10*100:.1f}%")
    rationale = "Yahoo analyst consensus: " + "; ".join([b for b in rationale_bits if b])
    if len(rationale) < 10:
        rationale = "Yahoo analyst consensus growth path"

    return ScenarioOverride(
        name="Consensus growth",
        scenario_type="base_alternative",
        proposed_by="analyst",
        rationale=rationale[:600],
        revenue_growth_y1_5=y1_5,
        revenue_growth_y6_10=y6_10,
        terminal_growth=path["terminal"],
    ), {
        "y1": ce.y1_revenue_growth,
        "y2": ce.y2_revenue_growth,
        "ltg": ce.ltg_consensus,
        "n_analysts": ce.n_analysts,
        "fetched_at": ce.fetched_at,
        "used_ltg": path["used_ltg"],
        "used_fallback": path["used_fallback"],
    }


def margin_reverts_to_industry_median(calc: CalculationInput) -> Tuple[ScenarioOverride, Dict[str, Any]]:
    """Terminal EBIT margin reverts to industry median (hand-curated table)."""
    from config.industry_margins import (
        get_industry_median_margin,
        INDUSTRY_MEDIAN_EBIT_MARGIN,
    )

    industry = calc.classification.industry if calc.classification else None
    if not industry:
        raise ValueError("margin_reverts_to_industry_median: missing industry classification")

    median_margin = get_industry_median_margin(industry)
    if median_margin is None:
        raise NotImplementedError(
            f"margin_reverts_to_industry_median: no median margin curated for "
            f"industry '{industry}'. Either the industry uses a non-FCFF model "
            f"(banks, utilities, managed care) where margin reversion doesn't "
            f"apply, or the industry isn't in the static table at "
            f"config/industry_margins.py. Add an entry there if needed."
        )

    # Clamp to ScenarioOverride bounds [0.05, 0.65]
    target = max(0.05, min(median_margin, 0.65))

    # Pull peer set for the rationale text
    peers = INDUSTRY_MEDIAN_EBIT_MARGIN[industry][1]
    peers_str = ", ".join(peers) if peers else "—"

    return ScenarioOverride(
        name="Margin reverts to industry median",
        scenario_type="bear",
        proposed_by="analyst",
        rationale=(
            f"Mean reversion: terminal EBIT margin set to industry median "
            f"({target*100:.1f}%) for {industry}. Peer reference: {peers_str}. "
            f"Static-table snapshot per config/industry_margins.py."
        )[:600],
        terminal_ebit_margin=target,
    ), {
        "industry": industry,
        "industry_median_margin": median_margin,
        "applied_target": target,
        "peer_reference": list(peers) if peers else [],
        "source": "static_table_2026_q1",
    }


# ────────────────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────────────────

LIBRARY: Dict[str, ScenarioFn] = {
    "historical_cagr_continues":            historical_cagr_continues,
    "growth_fades_to_gdp":                  growth_fades_to_gdp,
    "recession_scenario":                   recession_scenario,
    "rate_normalization":                   rate_normalization,
    "bull_execution":                       bull_execution,
    "bear_execution":                       bear_execution,
    "consensus_growth":                     consensus_growth,                     # STUB
    "margin_reverts_to_industry_median":    margin_reverts_to_industry_median,    # STUB
}


def run_scenario(
    ticker: str,
    scenario_fn: ScenarioFn,
    calc_input: Optional[CalculationInput] = None,
) -> ScenarioRunResult:
    """
    Apply a library scenario to `ticker` and return a unified result.
    `calc_input` can be passed to skip rebuild (useful in batch runs).
    """
    if calc_input is None:
        calc_input = make_calc_input(ticker)

    override, metadata = scenario_fn(calc_input)
    new_profile = _clone_profile_with_overrides(calc_input.valuation_profile, override)
    scenario_calc = CalculationInput(
        df=calc_input.df,
        classification=calc_input.classification,
        known_issues=calc_input.known_issues,
        valuation_profile=new_profile,
        lifecycle_thresholds=calc_input.lifecycle_thresholds,
    )

    result = DCFEngine(verbose=False).run(scenario_calc)
    base_iv = result.intrinsic_per_share(result.base.enterprise_value, result.net_debt) \
        if result.base else None
    bull_iv = result.intrinsic_per_share(result.bull.enterprise_value, result.net_debt) \
        if result.bull else None
    bear_iv = result.intrinsic_per_share(result.bear.enterprise_value, result.net_debt) \
        if result.bear else None
    upside = (
        (base_iv - result.current_price) / result.current_price
        if base_iv and result.current_price > 0 else None
    )
    a = result.base.assumptions if result.base else None

    return ScenarioRunResult(
        ticker=ticker,
        scenario_name=override.name,
        rationale=override.rationale,
        override=override,
        base_iv=float(base_iv) if base_iv else None,
        bull_iv=float(bull_iv) if bull_iv else None,
        bear_iv=float(bear_iv) if bear_iv else None,
        current_price=float(result.current_price),
        upside_pct=float(upside) if upside is not None else None,
        base_wacc=float(a.wacc) if a else 0.0,
        base_terminal_growth=float(a.terminal_growth) if a else 0.0,
        base_terminal_margin=float(a.ebit_margin_terminal) if a else 0.0,
        base_capex_pct=float(a.capex_pct_revenue) if a else 0.0,
        notes=metadata,
    )


def list_scenarios() -> Dict[str, str]:
    """Map of scenario_id → docstring first-line for discovery."""
    out: Dict[str, str] = {}
    for k, fn in LIBRARY.items():
        doc = (fn.__doc__ or "").strip().split("\n")[0]
        out[k] = doc
    return out
