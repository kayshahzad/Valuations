"""
aletheia/tools/dcf_engine.py

Phase 2 — DCF Engine
=====================
Implements the Liberti methodology DCF directly from CleanedRecord data.

Core formula (Cash Flow from Assets):
    CFA = Revenue - Operating Costs - Taxes + D&A - CapEx - ΔNWC
      = NOPAT + D&A - CapEx - ΔNWC
      = FCFF

Three scenarios: bull / base / bear
Each scenario has explicit, documented assumptions stored in ScenarioAssumptions.

WACC is computed from live market inputs:
    Ke = Rf + Beta × MRP   (CAPM)
    Kd = Interest Expense / Avg Debt
    WACC = (E/V)×Ke + (D/V)×Kd×(1-T)

Terminal value — both methods computed and cross-checked:
    Gordon Growth : TV = FCF_final × (1+g) / (WACC - g)
    Reinvestment  : TV = NOPAT_final × (1 - g/ROIC) / (WACC - g)  [Liberti]

Usage:
    from aletheia.tools.dcf_engine import DCFEngine
    engine = DCFEngine()
    result = engine.run("TICKER")
    print(result.summary())
"""

import datetime
import warnings
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

import numpy as np
import pandas as pd



warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

MARKET_RISK_PREMIUM = 0.055       # Damodaran long-run ERP estimate
DEFAULT_WACC        = 0.09        # Fallback if WACC computation fails
DEFAULT_TERMINAL_G  = 0.025       # 2.5% long-run nominal GDP growth
MAX_TERMINAL_G      = 0.04        # Hard cap — requires megatrend justification
BETA_PERIOD         = "5y"        # 5-year weekly beta regression
BETA_INTERVAL       = "1wk"


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScenarioAssumptions:
    """Explicit, documented assumptions for one DCF scenario."""
    name: str                          # "bull", "base", "bear"

    # Revenue
    revenue_growth_rates: List[float]
    revenue_cagr_y1_5: float           # Years 1-5 revenue CAGR (for metadata)
    revenue_cagr_y6_10: float          # Years 6-10 revenue CAGR (for metadata)

    # Margins
    ebit_margin_terminal: float        # Terminal EBIT margin
    ebit_margin_current: float         # Starting EBIT margin (from DB)

    # Reinvestment
    capex_pct_revenue: float           # CapEx as % of revenue
    da_pct_revenue: float              # D&A as % of revenue
    nwc_pct_revenue: float             # Net working capital as % of revenue

    # Cost of capital
    wacc: float                        # Scenario-specific WACC
    terminal_growth: float             # Gordon growth rate

    # Tax
    tax_rate: float                    # Cash tax rate

    # ROIC Methodology
    base_roic: float = 0.10            # Historical starting ROIC

    # Justification (required for assumption discipline)
    justification: str = ""

    @property
    def terminal_roic(self) -> float:
        """
        Returns the terminal ROIC used to calculate the Liberti reinvestment-rate 
        terminal value.
        
        Methodology Choice: This framework rejects the standard academic assumption 
        that competitive advantages erode and ROIC fades to WACC over the projection 
        period. Instead, it explicitly holds the company's historical `base_roic` 
        constant into perpetuity (floored at 8%), assuming genuine moats persist.
        """
        return max(self.base_roic, 0.08)


@dataclass
class YearProjection:
    """One year of projected financials."""
    year: int
    fiscal_year: int
    revenue: float
    ebit_margin: float
    ebit: float
    tax_expense: float
    nopat: float
    da: float
    capex: float
    delta_nwc: float
    fcff: float                        # Free Cash Flow to Firm (CFA formula)
    pv_fcff: float                     # Present value of FCFF
    cumulative_pv: float


@dataclass
class TerminalValue:
    gordon_tv: float                   # Gordon Growth terminal value
    reinvestment_tv: float             # Liberti reinvestment-rate TV
    tv_used: float                     # Which one we use (reinvestment preferred)
    pv_tv: float                       # Present value of terminal value
    implied_tv_ebitda_multiple: float  # TV / terminal EBITDA (sanity check)
    tv_pct_of_ev: float                # Terminal value as % of enterprise value


@dataclass
class ScenarioResult:
    """Full result for one scenario."""
    assumptions: ScenarioAssumptions
    projections: List[YearProjection] = field(default_factory=list)
    terminal: Optional[TerminalValue] = None

    enterprise_value: float = 0.0
    pv_explicit_period: float = 0.0
    implied_ev_ebitda: float = 0.0    # EV / latest EBITDA
    implied_ev_ebit: float = 0.0

    # Liberti multiple decomposition
    # EV/EBITDA = NOPATn*(1-g/ROIC)/EBITDA / (WACC-g)
    justified_ev_ebitda: float = 0.0  # What multiple is mathematically justified
    roic_wacc_spread: float = 0.0

    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class DCFResult:
    """Complete DCF output for all three scenarios."""
    ticker: str
    fiscal_year: int
    run_date: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())

    # Market inputs
    current_price: float = 0.0
    shares_diluted: float = 0.0
    market_cap: float = 0.0
    risk_free_rate: float = 0.0
    beta: float = 1.0
    wacc_base: float = 0.0

    # Base financials (from DB)
    revenue: float = 0.0
    ebitda: float = 0.0
    ebit: float = 0.0
    nopat: float = 0.0
    roic: float = 0.0
    fcf: float = 0.0
    net_debt: float = 0.0

    # Scenario results
    bull: Optional[ScenarioResult] = None
    base: Optional[ScenarioResult] = None
    bear: Optional[ScenarioResult] = None

    confidence: str = "high"
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def wacc(self) -> float:
        """
        Returns the baseline WACC (`wacc_base`) for public API consistency 
        with MultipleDecomposition and ReverseDCF. 
        Note: The DCF Engine projects three distinct scenarios simultaneously. 
        For scenario-specific WACCs (e.g. including the 150bps Bear stress), 
        refer to `result.bull.assumptions.wacc` or `result.bear.assumptions.wacc`.
        """
        return self.wacc_base

    def ev_to_equity(self, ev: float, net_debt: float,
                     pension_deficit: float = 0.0,
                     lease_debt: float = 0.0,
                     jva_value: float = 0.0) -> float:
        """Convert EV to equity value. Equity bridge is in equity_bridge.py."""
        equity = ev - net_debt - pension_deficit - lease_debt + jva_value
        return max(0.0, equity) # Non-recourse equity cannot be negative

    def intrinsic_per_share(self, ev: float, net_debt: float) -> Optional[float]:
        if self.shares_diluted and self.shares_diluted > 0:
            equity_val = self.ev_to_equity(ev, net_debt)
            return equity_val / self.shares_diluted
        return None

    def upside(self, intrinsic: float) -> Optional[float]:
        if intrinsic and self.current_price and self.current_price > 0:
            return (intrinsic - self.current_price) / self.current_price
        return None

    def summary(self) -> str:
        lines = [
            f"DCFResult: {self.ticker} FY{self.fiscal_year}",
            f"  Price        : ${self.current_price:,.2f}",
            f"  Market Cap   : ${self.market_cap/1e9:,.1f}B",
            f"  Risk-free    : {self.risk_free_rate:.2%}",
            f"  Beta         : {self.beta:.2f}",
            f"  WACC (base)  : {self.wacc_base:.2%}",
            f"  Revenue      : ${self.revenue/1e9:,.1f}B",
            f"  EBITDA       : ${self.ebitda/1e9:,.1f}B",
            f"  ROIC         : {self.roic:.1%}",
            f"  FCF          : ${self.fcf/1e9:,.1f}B",
            f"  Net Debt     : ${self.net_debt/1e9:,.1f}B",
        ]
        for scenario_name, scenario in [("BULL", self.bull),
                                         ("BASE", self.base),
                                         ("BEAR", self.bear)]:
            if scenario:
                ev = scenario.enterprise_value
                iv = self.intrinsic_per_share(ev, self.net_debt)
                upside = self.upside(iv) if iv else None
                lines.append(
                    f"  {scenario_name:4s} EV={ev/1e9:,.0f}B"
                    f"  IV/share=${iv:,.2f}" if iv else
                    f"  {scenario_name:4s} EV={ev/1e9:,.0f}B  IV/share=N/A"
                )
                if upside is not None:
                    lines[-1] += f"  upside={upside:+.1%}"
                lines.append(
                    f"       WACC={scenario.assumptions.wacc:.2%}"
                    f"  g={scenario.assumptions.terminal_growth:.2%}"
                    f"  EV/EBITDA={scenario.implied_ev_ebitda:.1f}x"
                    f"  (justified={scenario.justified_ev_ebitda:.1f}x)"
                )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Flat dict for database storage."""
        d = {
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_year,
            "run_date": self.run_date,
            "current_price": self.current_price,
            "shares_diluted": self.shares_diluted,
            "market_cap": self.market_cap,
            "risk_free_rate": self.risk_free_rate,
            "beta": self.beta,
            "wacc_base": self.wacc_base,
            "revenue": self.revenue,
            "ebitda": self.ebitda,
            "ebit": self.ebit,
            "nopat": self.nopat,
            "roic": self.roic,
            "fcf": self.fcf,
            "net_debt": self.net_debt,
        }
        for sname, scenario in [("bull", self.bull),
                                  ("base", self.base),
                                  ("bear", self.bear)]:
            if scenario:
                iv = self.intrinsic_per_share(
                    scenario.enterprise_value, self.net_debt
                )
                upside = self.upside(iv) if iv else None
                d[f"{sname}_ev"] = scenario.enterprise_value
                d[f"{sname}_intrinsic_per_share"] = iv
                d[f"{sname}_upside"] = upside
                d[f"{sname}_wacc"] = scenario.assumptions.wacc
                d[f"{sname}_terminal_g"] = scenario.assumptions.terminal_growth
                d[f"{sname}_ev_ebitda"] = scenario.implied_ev_ebitda
                d[f"{sname}_justified_ev_ebitda"] = scenario.justified_ev_ebitda
                d[f"{sname}_roic_wacc_spread"] = scenario.roic_wacc_spread
                tv = scenario.terminal
                if tv:
                    d[f"{sname}_tv_pct_of_ev"] = tv.tv_pct_of_ev
                    d[f"{sname}_implied_tv_ebitda"] = tv.implied_tv_ebitda_multiple
        return d


# ─────────────────────────────────────────────────────────────────────────────
# WACC computation
# ─────────────────────────────────────────────────────────────────────────────

from aletheia.data.market_data import get_risk_free_rate, get_beta

def _fetch_risk_free_rate() -> float:
    """Fetch current 10-year US Treasury yield from market_data."""
    return get_risk_free_rate()

def _compute_beta(ticker: str, period: str = BETA_PERIOD,
                  interval: str = BETA_INTERVAL) -> float:
    """Compute 5-year weekly beta from market_data."""
    return get_beta(ticker, period=period, interval=interval)


def compute_wacc(
    ticker: str,
    total_equity: float,      # Market cap
    total_debt: float,        # Book value of debt (LTD + STD)
    interest_expense: float,  # Annual interest expense
    tax_rate: float,
    risk_free_rate: Optional[float] = None,
    beta: Optional[float] = None,
    mrp: float = MARKET_RISK_PREMIUM,
) -> Tuple[float, float, float, float]:
    """
    Compute WACC from first principles.

    Returns:
        (wacc, ke, kd, beta)
    """
    rf = risk_free_rate or _fetch_risk_free_rate()
    b = beta or _compute_beta(ticker)

    # Cost of equity: CAPM
    ke = rf + b * mrp

    # Cost of debt
    if total_debt and total_debt > 0 and interest_expense and interest_expense > 0:
        kd = abs(interest_expense) / total_debt
        kd = min(kd, 0.15)   # Cap at 15% — higher suggests data error
    else:
        kd = rf + 0.015       # Fallback: Rf + 150bps credit spread

    # Capital structure weights
    total_capital = total_equity + total_debt
    if total_capital <= 0:
        return DEFAULT_WACC, ke, kd, b

    we = total_equity / total_capital
    wd = total_debt / total_capital

    wacc = we * ke + wd * kd * (1 - tax_rate)

    # Bound WACC: floor = max(4%, Rf + 1%) to prevent sub-Rf WACC (CNC, utility-like cos)
    wacc_floor = max(0.04, (risk_free_rate or 0.04) + 0.01)
    wacc = float(np.clip(wacc, wacc_floor, 0.18))

    return wacc, ke, kd, b


# ─────────────────────────────────────────────────────────────────────────────
# Scenario builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_assumptions(
    scenario_name: str,
    revenue: float,
    ebit: float,
    roic: float,
    fcf: float,
    capex_pct: float,
    da_pct: float,
    nwc_pct: float,
    tax_rate: float,
    wacc_base: float,
    hist_revenue_cagr: float,
    profile: "ValuationProfile",
    lifecycle: str = "mature",
    terminal_growth_adj: float = 0.0,
    growth_decay_reduction: float = 0.0,
) -> ScenarioAssumptions:
    """
    Build scenario-specific assumptions from base metrics.

    Bull: above-trend growth, margin expansion, WACC compressed
    Base: consensus + historical discipline
    Bear: margin compression, WACC expansion, mean reversion
    """
    forecast_years = profile.forecast_years
    ebit_margin = ebit / revenue if revenue > 0 else 0.15

    applies_cyclical_haircut = lifecycle == "cyclical_industrial"
    cagr_y1_5_factor = 0.70 if applies_cyclical_haircut else 1.0
    cagr_y6_10_factor = 0.40 if applies_cyclical_haircut else 1.0
    terminal_g_delta = -0.005 if applies_cyclical_haircut else 0.0

    # Margin and decay logic based on profile
    margin_rev_base = 1.0 - profile.terminal_margin_decay
    margin_rev_bull = margin_rev_base + profile.bull_margin_compression
    margin_rev_bear = margin_rev_base + profile.bear_margin_compression
    
    decay_base = profile.decay_base
    decay_bull = profile.decay_bull
    decay_bear = profile.decay_bear

    if scenario_name == "bull":
        # Assume terminal growth is higher in bull scenario, bounded by profile
        bull_terminal_g = profile.terminal_growth + 0.01
        terminal_g = min(bull_terminal_g + terminal_growth_adj + terminal_g_delta, MAX_TERMINAL_G)
        if profile.terminal_growth_cap is not None:
            terminal_g = min(terminal_g, profile.terminal_growth_cap)
            
        y1_5 = min(hist_revenue_cagr * (1.0 + profile.bull_growth_haircut), 0.45) * cagr_y1_5_factor
        y6_10 = min(hist_revenue_cagr * decay_bull + growth_decay_reduction, 0.25) * cagr_y6_10_factor
        
        ebit_margin_term = min(ebit_margin * margin_rev_bull, 0.65)
        capex_pct_rev = capex_pct * 0.90
        wacc = max(wacc_base + profile.bull_wacc_adjustment, 0.06)
        
    elif scenario_name == "base":
        terminal_g = profile.terminal_growth + terminal_growth_adj + terminal_g_delta
        if profile.terminal_growth_cap is not None:
            terminal_g = min(terminal_g, profile.terminal_growth_cap)

        y1_5 = hist_revenue_cagr * cagr_y1_5_factor
        y6_10 = (hist_revenue_cagr * decay_base + growth_decay_reduction) * cagr_y6_10_factor
        
        ebit_margin_term = ebit_margin * margin_rev_base
        capex_pct_rev = capex_pct
        wacc = wacc_base

    else:  # bear
        bear_terminal_g = max(profile.terminal_growth - 0.01, 0.015)
        terminal_g = bear_terminal_g + terminal_growth_adj + terminal_g_delta
        if profile.terminal_growth_cap is not None:
            terminal_g = min(terminal_g, profile.terminal_growth_cap)

        y1_5 = max(hist_revenue_cagr * (1.0 + profile.bear_growth_haircut), 0.01) * cagr_y1_5_factor
        y6_10 = max(hist_revenue_cagr * decay_bear + growth_decay_reduction, 0.005) * cagr_y6_10_factor
        
        ebit_margin_term = ebit_margin * margin_rev_bear
        capex_pct_rev = capex_pct * 1.15
        wacc = min(wacc_base + profile.bear_wacc_adjustment, 0.16)

    # Compute array of explicit growth rates
    growth_rates = []
    for i in range(1, forecast_years + 1):
        if i <= 5:
            growth_rates.append(y1_5)
        elif i <= 10:
            growth_rates.append(y6_10)
        else:
            # Interpolate smoothly from y6_10 to terminal_g
            progress = (i - 10) / (forecast_years - 10)
            interp_g = y6_10 - (y6_10 - terminal_g) * progress
            growth_rates.append(interp_g)

    return ScenarioAssumptions(
        name=scenario_name,
        revenue_growth_rates=growth_rates,
        revenue_cagr_y1_5=y1_5,
        revenue_cagr_y6_10=y6_10,
        ebit_margin_current=ebit_margin,
        ebit_margin_terminal=ebit_margin_term,
        capex_pct_revenue=capex_pct_rev,
        da_pct_revenue=da_pct,
        nwc_pct_revenue=nwc_pct if scenario_name != "bear" else nwc_pct * 1.10,
        wacc=wacc,
        terminal_growth=terminal_g,
        tax_rate=tax_rate if scenario_name != "bear" else min(tax_rate + 0.03, 0.30),
        base_roic=roic,
        justification=f"{scenario_name.capitalize()}: {y1_5:.1%} Y1-5 CAGR, margin {ebit_margin_term/ebit_margin if ebit_margin > 0 else 1.0:.2f}x, g={terminal_g:.1%}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Projection engine
# ─────────────────────────────────────────────────────────────────────────────

def _project_scenario(
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
    Project cash flows for one scenario using the Liberti CFA formula.

    CFA = NOPAT + D&A - CapEx - ΔNWC

    Returns:
        (projections, terminal_value, enterprise_value)
    """
    wacc = assumptions.wacc
    g = assumptions.terminal_growth
    tax = assumptions.tax_rate

    effective_roic = max(base_roic, 0.08)
    terminal_nopat_margin = assumptions.ebit_margin_terminal * (1 - tax)
    
    # Terminal Reinvestment (as % of revenue) = (g / ROIC) * NOPAT Margin
    terminal_reinvest_margin = terminal_nopat_margin * (g / effective_roic)
    
    # Terminal CapEx = D&A + Terminal Reinvestment - Terminal ΔNWC
    # ΔNWC as % of revenue = g * nwc_pct_revenue
    terminal_capex_pct = terminal_reinvest_margin + assumptions.da_pct_revenue - (g * assumptions.nwc_pct_revenue)
    terminal_capex_pct = max(terminal_capex_pct, assumptions.da_pct_revenue * 0.5) # Floor to partial maintenance

    projections = []
    cumulative_pv = 0.0
    prev_nwc = base_nwc

    for yr in range(1, forecast_years + 1):
        # Revenue growth — pulled from pre-calculated array (handles 15yr interpolation)
        # Note: yr is 1-indexed, so we use yr - 1 for array access
        if len(assumptions.revenue_growth_rates) >= yr:
            cagr = assumptions.revenue_growth_rates[yr - 1]
        else:
            # Fallback in case of mismatch
            cagr = assumptions.terminal_growth

        if yr == 1:
            revenue = base_revenue * (1 + cagr)
        else:
            revenue = projections[-1].revenue * (1 + cagr)

        # EBIT margin — linear interpolation from current to terminal
        margin_progression = yr / forecast_years
        ebit_margin = (
            assumptions.ebit_margin_current * (1 - margin_progression) +
            assumptions.ebit_margin_terminal * margin_progression
        )

        ebit = revenue * ebit_margin
        tax_expense = ebit * tax
        nopat = ebit * (1 - tax)

        # Reinvestment (Liberti CFA formula components)
        da = revenue * assumptions.da_pct_revenue
        
        # Gradual convergence of CapEx from current intensity to terminal intensity
        progression = yr / forecast_years
        current_capex_pct = assumptions.capex_pct_revenue * (1 - progression) + terminal_capex_pct * progression
        capex = revenue * current_capex_pct
        
        nwc = revenue * assumptions.nwc_pct_revenue
        delta_nwc = nwc - prev_nwc
        prev_nwc = nwc

        # FCFF = NOPAT + D&A - CapEx - ΔNWC
        fcff = nopat + da - capex - delta_nwc

        # Discount factor
        discount = (1 + wacc) ** yr
        pv_fcff = fcff / discount
        cumulative_pv += pv_fcff

        projections.append(YearProjection(
            year=yr,
            fiscal_year=latest_fy + yr,
            revenue=revenue,
            ebit_margin=ebit_margin,
            ebit=ebit,
            tax_expense=tax_expense,
            nopat=nopat,
            da=da,
            capex=capex,
            delta_nwc=delta_nwc,
            fcff=fcff,
            pv_fcff=pv_fcff,
            cumulative_pv=cumulative_pv,
        ))

    # Terminal value — both methods
    final = projections[-1]
    final_nopat = final.nopat
    final_ebitda = final.ebit + final.da

    # Method 1: Gordon Growth TV = FCF_final × (1+g) / (WACC - g)
    if wacc > g:
        gordon_tv = final.fcff * (1 + g) / (wacc - g)
    else:
        gordon_tv = final.fcff * 20   # Fallback if wacc ≤ g

    # Method 2: Liberti reinvestment-rate TV = NOPAT × (1 - g/ROIC) / (WACC - g)
    effective_roic = max(base_roic, 0.08)   # Floor at 8% if ROIC is zero/negative
    if wacc > g and effective_roic > g:
        reinvest_tv = final_nopat * (1 - g / effective_roic) / (wacc - g)
    else:
        reinvest_tv = gordon_tv

    # Use reinvestment-rate TV (preferred — Liberti) if it is within 50% of Gordon
    # or if Gordon TV goes negative (fallback logic).
    if (gordon_tv > 0 and abs(reinvest_tv - gordon_tv) / gordon_tv < 0.50) or (gordon_tv < 0 and reinvest_tv > 0):
        tv_used = reinvest_tv
    else:
        tv_used = gordon_tv   # Fall back to Gordon if large divergence

    # Present value of terminal value
    pv_tv = tv_used / (1 + wacc) ** forecast_years

    enterprise_value = cumulative_pv + pv_tv

    # Implied terminal multiple for sanity check
    if final_ebitda > 0:
        implied_tv_multiple = tv_used / final_ebitda
    else:
        implied_tv_multiple = 0.0

    tv_pct = pv_tv / enterprise_value if enterprise_value > 0 else 0.0

    # Floor enterprise value at zero — negative EV is economically undefined
    # Can occur in bear case for low-margin companies (CNC, utilities) where
    # high stress WACC overwhelms thin FCF generation
    if enterprise_value < 0:
        enterprise_value = 0.0
        pv_tv = max(pv_tv, 0.0)
        tv_pct = 0.0

    terminal = TerminalValue(
        gordon_tv=gordon_tv,
        reinvestment_tv=reinvest_tv,
        tv_used=tv_used,
        pv_tv=pv_tv,
        implied_tv_ebitda_multiple=implied_tv_multiple,
        tv_pct_of_ev=tv_pct,
    )

    return projections, terminal, enterprise_value


# ─────────────────────────────────────────────────────────────────────────────
# Main engine
# ─────────────────────────────────────────────────────────────────────────────

class DCFEngine:
    """
    Reads from InvestmentDatabase and runs three-scenario DCF.
    No manual inputs required — all assumptions derived from clean data.
    """
    
    # Note: This contract is dynamically consumed by test_structured_ingestion.py
    REQUIRED_CLEAN_FIELDS = [
        "Revenue", "OperatingIncome", "Depreciation", "CapEx", "TotalAssets", "TotalEquity"
    ]

    def __init__(
        self,
        db_path: str = "valuation_data/database/investment.duckdb",
        forecast_years: int = 10,
        mrp: float = MARKET_RISK_PREMIUM,
        verbose: bool = True,
    ):
        self.db_path = db_path
        self.forecast_years = forecast_years
        self.mrp = mrp
        self.verbose = verbose

    def run(self, calc_input: 'CalculationInput', fiscal_year: Optional[int] = None, **kwargs) -> DCFResult:
        max_growth_rate = kwargs.get('max_growth_rate', 0.50)
        """
        Run full three-scenario DCF for a ticker.

        Args:
            calc_input: CalculationInput containing dataframe, classification, valuation_profile, and known_issues
            fiscal_year: specific year to value (defaults to latest)

        Returns:
            DCFResult with bull/base/bear scenarios
        """
        from aletheia.data.exceptions import MissingFieldError

        ticker = calc_input.classification.ticker
        meta = calc_input.classification
        lifecycle = meta.lifecycle
        profile = calc_input.valuation_profile
        
        if meta and meta.business_model in ["ddm_required", "embedded_value_required", "routing_required"]:
            raise NotImplementedError(f"DCFEngine: ticker {ticker} requires specialized model ({meta.business_model}) — see KNOWN_ISSUES")

        result = DCFResult(ticker=ticker, fiscal_year=fiscal_year or 0)

        if self.verbose:
            print(f"\n{'='*60}")
            print(f"  DCF Engine: {ticker}")
            print(f"{'='*60}")

        # ── Step 1: Extract DataFrame ───────────────────────────
        df = calc_input.df

        if df.empty:
            result.errors.append(f"No data in dataframe for {ticker}")
            return result

        # Use requested year or latest
        if fiscal_year:
            year_df = df[df["fiscal_year"] == fiscal_year]
            if year_df.empty:
                fiscal_year = int(df["fiscal_year"].max())
                year_df = df[df["fiscal_year"] == fiscal_year]
        else:
            fiscal_year = int(df["fiscal_year"].max())
            year_df = df[df["fiscal_year"] == fiscal_year]

        latest = year_df.iloc[0]
        result.fiscal_year = fiscal_year

        # ── Step 2: Extract base financials ──────────────────────────────────
        def get(col, fallback=None):
            val = latest.get(col)
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                return float(val)
            return fallback
            
        def get_with_provenance(field: str) -> Tuple[Optional[float], str]:
            raw_val = get(f"raw_{field}")
            if raw_val is not None:
                return raw_val, "raw"
            
            derived_val = get(f"derived_{field}")
            if derived_val is not None:
                return derived_val, "derived"
                
            return None, "missing"

        revenue = get("clean_Revenue")
        if revenue is None:
            raise MissingFieldError(f"Missing required field 'Revenue' for {ticker}")
            
        if "base_revenue_override" in kwargs:
            revenue = kwargs["base_revenue_override"]

        ebit, ebit_prov = get_with_provenance("OperatingIncome")
        if ebit_prov == "missing":
            raise MissingFieldError(f"Missing required field 'OperatingIncome' for {ticker}")

        ebitda, ebitda_prov = get_with_provenance("EBITDA")
        if ebitda_prov == "missing": ebitda = ebit
        
        nopat = get("clean_NOPAT", ebit * 0.79)
        roic = get("derived_ROIC", 0.12)
        fcf = get("derived_FCF", 0.0)
        net_debt = get("derived_NetDebt", 0.0)
        invested_capital = get("derived_InvestedCapital", 0.0)
        tax_rate = get("clean_CashTaxRate") or get("clean_GAAP_TaxRate") or 0.21
        long_term_debt = get("raw_LongTermDebt", 0.0)
        total_equity_book = get("raw_TotalEquity", 0.0)

        da, da_prov = get_with_provenance("Depreciation")
        capex, capex_prov = get_with_provenance("CapEx")

        if da_prov == "missing" or capex_prov == "missing":
            print(f"❌ PIPELINE HALTED: DCFEngine missing critical inputs for {ticker}")
            raise MissingFieldError(
                f"DCFEngine: missing D&A or CapEx for {ticker}. "
                f"da_prov={da_prov}, capex_prov={capex_prov}. "
            )

        nwc = revenue * 0.03   # Structural NWC estimate

        capex_pct = capex / revenue if revenue > 0 else 0.04
        da_pct = da / revenue if revenue > 0 else 0.03
        nwc_pct = 0.03

        # Historical revenue CAGR — Phase 4.5 Exact-Day Math
        # Uses exact period_end_date differences instead of integer fiscal_years.
        # Tracks data integrity (n_years_used vs n_years_attempted) to drop suspect records.
        hist_revenues_df = df[df["fiscal_year"] <= fiscal_year].sort_values("fiscal_year").dropna(subset=["clean_Revenue"])
        
        cagr_candidates = []
        if not hist_revenues_df.empty:
            row_now = hist_revenues_df.iloc[-1]
            rev_now = float(row_now["clean_Revenue"])
            date_now_str = row_now.get("period_end_date")
            
            if pd.notna(date_now_str) and rev_now > 0:
                try:
                    date_now = pd.to_datetime(date_now_str)
                    
                    for lookback in [3, 5, 7, 10]:
                        n_years_attempted = lookback
                        n_years_used = 0
                        rev_past = None
                        date_past = None
                        
                        # Find the actual record N years ago by walking backwards
                        # Note: If history is patchy, this drops records but counts them as attempted.
                        if len(hist_revenues_df) >= lookback:
                            target_row = hist_revenues_df.iloc[-lookback - 1] if len(hist_revenues_df) > lookback else hist_revenues_df.iloc[0]
                            # Count the number of valid intermediate records to check density
                            subset = hist_revenues_df.iloc[-lookback-1:]
                            n_years_used = subset["period_end_date"].notna().sum() - 1 # exclude current year
                            
                            rev_past_val = target_row["clean_Revenue"]
                            date_past_str = target_row.get("period_end_date")
                            
                            if pd.notna(date_past_str) and float(rev_past_val) > 0:
                                rev_past = float(rev_past_val)
                                date_past = pd.to_datetime(date_past_str)
                                
                                days_between = (date_now - date_past).days
                                if days_between > 0:
                                    cagr = (rev_now / rev_past) ** (365.25 / days_between) - 1
                                    cagr_candidates.append((cagr, n_years_used, n_years_attempted, lookback))
                except Exception as e:
                    if self.verbose:
                        print(f"  [WARN] Failed exact-date CAGR parsing: {e}")

        forecast_years_applied = profile.forecast_years

        # Select the longest lookback that meets the 70% data integrity threshold
        valid_cagrs = [c for c in cagr_candidates if c[1] >= c[2] * 0.7]
        
        if valid_cagrs:
            # Sort by lookback period (longest first)
            valid_cagrs.sort(key=lambda x: x[3], reverse=True)
            selected = valid_cagrs[0]
            hist_cagr = selected[0]
            if self.verbose:
                print(f"  Selected {selected[3]}Y Exact-Date CAGR: {hist_cagr:.1%} (Data density: {selected[1]}/{selected[2]})")
        else:
            print(f"  [WARN] Missing or sparse SEC history for {ticker}. Using lifecycle default ({lifecycle}).")
            hist_cagr = profile.growth_rate

        hist_cagr = float(np.clip(hist_cagr, 0.01, max_growth_rate))

        if self.verbose:
            cagr_str = ", ".join(
                f"{c[0]:.1%}" for c in cagr_candidates
            )
            print(f"  CAGR candidates ({len(cagr_candidates)} periods): [{cagr_str}]")
            print(f"  Robust CAGR (trimmed median): {hist_cagr:.1%}")

        result.revenue = revenue
        result.ebitda = ebitda
        result.ebit = ebit
        result.nopat = nopat
        result.roic = roic
        result.fcf = fcf
        result.net_debt = net_debt

        if self.verbose:
            print(f"  Revenue: ${revenue/1e9:.1f}B")
            print(f"  EBIT:    ${ebit/1e9:.1f}B  ({ebit/revenue:.1%} margin)")
            print(f"  ROIC:    {roic:.1%}")
            print(f"  FCF:     ${fcf/1e9:.1f}B")
            print(f"  5Y CAGR: {hist_cagr:.1%}")

        # ── Step 3: Fetch live market data ────────────────────────────────────
        from aletheia.data.market_data import get_current_price, get_market_cap, get_shares_outstanding
        try:
            current_price = get_current_price(ticker)
            market_cap = get_market_cap(ticker)
            shares_diluted = get("clean_SharesDiluted")
            if not shares_diluted or shares_diluted <= 0:
                shares_diluted = get_shares_outstanding(ticker)
        except Exception as e:
            result.errors.append(f"market_data fetch failed: {e}")
            current_price = 0.0
            market_cap = 0.0
            shares_diluted = 0.0

        result.current_price = current_price
        result.market_cap = market_cap
        result.shares_diluted = shares_diluted

        if self.verbose:
            print(f"  Price:   ${current_price:,.2f}")
            print(f"  Mkt Cap: ${market_cap/1e9:.1f}B")

        # ── Step 4: Compute WACC ──────────────────────────────────────────────
        rf = _fetch_risk_free_rate()
        beta = _compute_beta(ticker)

        # Interest expense proxy — kd from net debt and a spread
        interest_expense = long_term_debt * 0.04   # Rough proxy

        wacc_base, ke, kd, beta = compute_wacc(
            ticker=ticker,
            total_equity=market_cap,
            total_debt=max(net_debt + get("raw_Cash", 0.0), long_term_debt),
            interest_expense=interest_expense,
            tax_rate=tax_rate,
            risk_free_rate=rf,
            beta=beta,
            mrp=self.mrp,
        )
        
        if "wacc_override" in kwargs:
            wacc_base = kwargs["wacc_override"]
        elif "wacc_penalty" in kwargs:
            wacc_base += kwargs["wacc_penalty"]
            
        result.risk_free_rate = rf
        result.beta = beta
        result.wacc_base = wacc_base

        if self.verbose:
            print(f"  Rf:      {rf:.2%}")
            print(f"  Beta:    {beta:.2f}")
            print(f"  Ke:      {ke:.2%}")
            print(f"  Kd:      {kd:.2%}")
            print(f"  WACC:    {wacc_base:.2%}")

        # ── Step 5: Build scenarios and project ───────────────────────────────
        for scenario_name in ["bull", "base", "bear"]:
            assumptions = _build_assumptions(
                scenario_name=scenario_name,
                revenue=revenue,
                ebit=ebit,
                roic=roic,
                fcf=fcf,
                capex_pct=capex_pct,
                da_pct=da_pct,
                nwc_pct=nwc_pct,
                tax_rate=tax_rate,
                wacc_base=wacc_base,
                hist_revenue_cagr=hist_cagr,
                profile=profile,
                lifecycle=lifecycle,
                terminal_growth_adj=kwargs.get("terminal_growth_adj", 0.0),
                growth_decay_reduction=kwargs.get("growth_decay_reduction", 0.0),
            )

            projections, terminal, ev = _project_scenario(
                assumptions=assumptions,
                base_revenue=revenue,
                base_roic=roic,
                base_da=da,
                base_capex=capex,
                base_nwc=nwc,
                latest_fy=fiscal_year,
                forecast_years=forecast_years_applied,
            )

            # Multiple decomposition (Liberti formula)
            # EV/EBITDA = NOPATn*(1-g/ROIC)/EBITDA / (WACC-g)
            final_nopat = projections[-1].nopat
            final_ebitda = projections[-1].ebit + projections[-1].da
            g = assumptions.terminal_growth
            w = assumptions.wacc
            effective_roic = max(roic, 0.08)

            if final_ebitda > 0 and w > g:
                justified_ev_ebitda = (
                    final_nopat * (1 - g / effective_roic) / final_ebitda
                ) / (w - g)
            else:
                justified_ev_ebitda = 0.0

            implied_ev_ebitda = ev / ebitda if ebitda > 0 else 0.0
            implied_ev_ebit = ev / ebit if ebit > 0 else 0.0
            roic_wacc_spread = roic - w

            metadata = {
                "lifecycle_category": lifecycle,
                "growth_default": profile.growth_rate,
                "hist_cagr": hist_cagr,
                "forecast_years": forecast_years_applied,
                "terminal_growth_cap": profile.terminal_growth_cap
            }
            
            ticker_issues = calc_input.known_issues
            if ticker_issues:
                metadata["data_quality_warnings"] = " | ".join([issue.description for issue in ticker_issues])
            if lifecycle == "cyclical_industrial":
                metadata["cyclical_haircut_methodology"] = "projection_growth_penalty (peak-cycle distortion expected)"
                
            scenario_result = ScenarioResult(
                assumptions=assumptions,
                projections=projections,
                terminal=terminal,
                enterprise_value=ev,
                pv_explicit_period=sum(p.pv_fcff for p in projections),
                implied_ev_ebitda=implied_ev_ebitda,
                implied_ev_ebit=implied_ev_ebit,
                justified_ev_ebitda=justified_ev_ebitda,
                roic_wacc_spread=roic_wacc_spread,
                metadata=metadata
            )

            # Sanity checks
            if terminal.tv_pct_of_ev > 0.85:
                scenario_result.warnings.append(
                    f"Terminal value is {terminal.tv_pct_of_ev:.0%} of EV. "
                    f"Check terminal growth assumption ({g:.1%}) and WACC ({w:.1%})."
                )
            if terminal.implied_tv_ebitda_multiple > implied_ev_ebitda * 1.2:
                scenario_result.warnings.append(
                    f"Implied TV multiple ({terminal.implied_tv_ebitda_multiple:.1f}x) "
                    f"exceeds current EV/EBITDA ({implied_ev_ebitda:.1f}x). "
                    f"Terminal assumptions may be too optimistic."
                )

            setattr(result, scenario_name, scenario_result)

            if self.verbose:
                iv = result.intrinsic_per_share(ev, net_debt)
                upside = result.upside(iv) if iv else None
                print(f"  {scenario_name.upper():4s}: EV=${ev/1e9:.0f}B"
                      f"  IV/share=${iv:,.0f}" if iv else
                      f"  {scenario_name.upper():4s}: EV=${ev/1e9:.0f}B  IV=N/A",
                      end="")
                if upside is not None:
                    print(f"  ({upside:+.1%} vs ${current_price:.0f})", end="")
                print()

        # ── Step 6: Apply Bear Case Structural Floor ────────────────────────────
        # Ensure bear scenario doesn't produce penny-stock values if company
        # has massive net cash reserves. Floor EV at 20% of net cash.
        if result.bear and net_debt < 0:
            net_cash = abs(net_debt)
            floor_ev = net_cash * 0.20
            if result.bear.enterprise_value < floor_ev:
                result.bear.enterprise_value = floor_ev
                result.bear.warnings.append(
                    f"Bear EV floored at 20% of Net Cash (${floor_ev/1e9:.1f}B)"
                )

        # ── Step 7: Apply Earnings Quality Metadata ────────────────────────────
        issues = calc_input.known_issues
        quality_issues = [i for i in issues if i.issue_type == "earnings_quality"]
        if quality_issues:
            result.confidence = "low"
            result.warnings.extend(i.description for i in quality_issues)

        return result


# CLI entrypoint removed to avoid architectural violations (no config imports allowed here).
# Use scratch/run_valuation_phase.py instead.
