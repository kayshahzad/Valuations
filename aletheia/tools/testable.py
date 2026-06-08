"""
aletheia/tools/testable.py

Stable testing surface for the calculation layer.
These wrappers provide explicit, deterministic interfaces to the underlying mathematics
without coupling the test suite to private methods or DuckDB ingestion flows.
"""

from typing import Tuple, List, Optional
from aletheia.tools.dcf_engine import _project_scenario, ScenarioAssumptions, YearProjection, TerminalValue
from aletheia.tools.multiple_decomposition import _compute_justified_ev_ebitda
from aletheia.tools.equity_bridge import EquityBridge, BridgeItem, CashAnalysis


def pure_compute_projection(
    assumptions: ScenarioAssumptions,
    base_revenue: float,
    base_roic: float,
    base_da: float,
    base_capex: float,
    base_nwc: float,
    latest_fy: int,
    forecast_years: int = 10,
) -> Tuple[List[YearProjection], TerminalValue, float]:
    """
    Stable public wrapper for testing DCF projection math.
    Returns: (projections, terminal_value, enterprise_value)
    """
    return _project_scenario(
        assumptions=assumptions,
        base_revenue=base_revenue,
        base_roic=base_roic,
        base_da=base_da,
        base_capex=base_capex,
        base_nwc=base_nwc,
        latest_fy=latest_fy,
        forecast_years=forecast_years
    )


def pure_compute_justified_multiple(
    nopat: float, ebitda: float, roic: float, wacc: float, g_terminal: float
) -> Tuple[float, float]:
    """
    Stable public wrapper for NorthWestern EV/EBITDA justified multiple math.
    Returns: (justified_ev_ebitda, cash_conversion_ratio)
    """
    return _compute_justified_ev_ebitda(
        nopat=nopat,
        ebitda=ebitda,
        roic=roic,
        wacc=wacc,
        g_terminal=g_terminal
    )


def pure_equity_bridge_math(
    enterprise_value: float,
    revenue: float,
    gross_cash: float,
    short_term_investments: float,
    long_term_debt: float,
    short_term_debt: float,
    minority_interest: float,
    pension_deficit: float,
    lease_debt: float,
    jva_income: float,
    wc_pct: float = 0.02,
    repatriation_tax: float = 0.15,
    overseas_cash_fraction: float = 0.60,
    restricted_cash: float = 0.0
) -> Tuple[float, CashAnalysis, List[BridgeItem]]:
    """
    Computes the equity bridge purely from given numbers without DB lookups.
    Returns: (intrinsic_equity_value, cash_analysis, list_of_bridge_items)
    """
    wc_requirement = revenue * wc_pct
    restricted = restricted_cash if restricted_cash > 0 else 0.0
    overseas_cash = gross_cash * overseas_cash_fraction
    repatriation_cost = overseas_cash * repatriation_tax
    net_cash = max(0.0, gross_cash - wc_requirement - restricted - repatriation_cost)
    total_cash_value = net_cash + short_term_investments

    cash_analysis = CashAnalysis(
        gross_cash=gross_cash,
        working_capital_haircut=wc_requirement,
        restricted_cash_haircut=restricted,
        overseas_tax_haircut=repatriation_cost,
        net_accessible_cash=net_cash,
        marketable_securities=short_term_investments,
        total_cash_value=total_cash_value,
    )

    jva_value = jva_income * 20 if jva_income > 0 else 0.0

    items = []
    if long_term_debt > 0:
        items.append(BridgeItem(name="Long-term debt", value=-long_term_debt, note="", confidence=0.95))
    if short_term_debt > 0:
        items.append(BridgeItem(name="Short-term debt", value=-short_term_debt, note="", confidence=0.90))
    if pension_deficit > 0:
        items.append(BridgeItem(name="Pension deficit", value=-pension_deficit, note="", confidence=0.85))
    if lease_debt > 0:
        items.append(BridgeItem(name="Capitalized leases", value=-lease_debt, note="", confidence=0.80))
    if minority_interest > 0:
        items.append(BridgeItem(name="Minority interest", value=-minority_interest, note="", confidence=0.80))
    if total_cash_value > 0:
        items.append(BridgeItem(name="Adjusted cash & equiv", value=total_cash_value, note="", confidence=0.95))
    if jva_value > 0:
        items.append(BridgeItem(name="JVA stakes", value=jva_value, note="", confidence=0.60))

    equity_value = enterprise_value + sum(item.value for item in items)
    return equity_value, cash_analysis, items


def pure_reverse_dcf_math(
    current_ev: float,
    base_revenue: float,
    ebit_margin: float,
    wacc: float,
    tax_rate: float = 0.21,
    capex_pct: float = 0.05,
    da_pct: float = 0.05,
    nwc_pct: float = 0.02,
    terminal_growth: float = 0.025,
    forecast_years: int = 10
) -> float:
    """
    Runs the reverse DCF brentq solver on deterministic inputs.
    Returns: implied_cagr
    """
    from scipy.optimize import brentq

    def pv_for_cagr(cagr: float) -> float:
        from aletheia.tools.dcf_engine import _project_scenario, ScenarioAssumptions
        assumptions = ScenarioAssumptions(
            name="reverse_solve",
            revenue_cagr_y1_5=cagr,
            revenue_cagr_y6_10=cagr,
            ebit_margin_current=ebit_margin,
            ebit_margin_terminal=ebit_margin,
            tax_rate=tax_rate,
            wacc=wacc,
            terminal_growth=terminal_growth,
            base_roic=0.15,
            revenue_growth_rates=[cagr]*10,
            capex_pct_revenue=capex_pct,
            da_pct_revenue=da_pct,
            nwc_pct_revenue=nwc_pct,
        )
        _, _, sum_pv = _project_scenario(
            assumptions=assumptions,
            base_revenue=base_revenue,
            base_roic=0.15,
            base_da=base_revenue * da_pct,
            base_capex=base_revenue * capex_pct,
            base_nwc=base_revenue * nwc_pct,
            latest_fy=2023,
            forecast_years=forecast_years
        )
        return sum_pv

    try:
        implied_cagr = brentq(lambda g: pv_for_cagr(g) - current_ev, -0.20, 0.50)
        return implied_cagr
    except ValueError:
        return 0.0
