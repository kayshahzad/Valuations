
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from aletheia.tools.math_utils import dynamic_converger, dynamic_converger_multiple_phase
from aletheia.utils.tracing import tracer

@dataclass
class DCFConfig:
    # Valuation Basics
    risk_free_rate: float = 0.04
    equity_risk_premium: float = 0.055
    marginal_tax_rate: float = 0.21
    
    # Capital Structure
    unlevered_beta: float = 1.0
    terminal_unlevered_beta: float = 1.0
    year_beta_converge: int = 5
    
    current_pretax_cost_of_debt: float = 0.05
    terminal_pretax_cost_of_debt: float = 0.05
    year_cod_converge: int = 5

    # Growth Cycles (3 Stages)
    # [Start, End] rates for each cycle
    rates_cycle_1: List[float] = field(default_factory=lambda: [0.10, 0.10])
    rates_cycle_2: List[float] = field(default_factory=lambda: [0.10, 0.05])
    rates_cycle_3: List[float] = field(default_factory=lambda: [0.03, 0.03]) # Terminal
    
    # Length of cycles
    len_cycle_1: int = 5
    len_cycle_2: int = 5
    len_cycle_3: int = 0 # Usually 0 logic in legacy? Or used for explicit terminal fade? 
                        # Legacy had len1, len2, len3. Usually 3rd is terminal phase? 
                        # Actually legacy used len3 for explicit projection BEFORE terminal value calculation?
                        # "valuation_interval = len1 + len2 + len3". 
                        # If len3 is terminal growth period, it just projects specific growth.
                        
    # Convergence within cycles (Period to start converging)
    conv_cycle_1: int = 1
    conv_cycle_2: int = 1
    conv_cycle_3: int = 1
    
    # Margins
    current_operating_margin: float = 0.10
    terminal_operating_margin: float = 0.15
    year_margin_converge: int = 5
    
    # Efficiency (Sales/Capital) -> Links Growth to Reinvestment
    current_sales_to_capital: float = 1.5
    terminal_sales_to_capital: float = 1.5
    year_sales_cap_converge: int = 5
    
    # Other
    additional_return_on_coc_perpetuity: float = 0.0 # Excess return in perpetuity (Rare)
    
@dataclass
class ValuationResult:
    equity_value: float
    enterprise_value: float
    projections: pd.DataFrame
    audit_trail: Dict[str, Any]

class ThreeStageDCF:
    """
    Advanced 3-Stage DCF Engine (Damodaran Style).
    Projects explicit cash flows over (len1 + len2 + len3) years, then applies Terminal Value.
    Features dynamic interpolation of WACC, Margins, and Tax Rates.
    """
    
    def __init__(self, base_year_data: Dict[str, float], config: DCFConfig):
        self.base = base_year_data
        self.config = config
        self.total_years = config.len_cycle_1 + config.len_cycle_2 + config.len_cycle_3
        
    def _project_revenue(self) -> tuple[pd.Series, pd.Series]:
        """Returns (Revenues, GrowthRates)"""
        c = self.config
        base_rev = self.base.get("revenue", 0.0)
        
        rates = [c.rates_cycle_1, c.rates_cycle_2, c.rates_cycle_3]
        lengths = [c.len_cycle_1, c.len_cycle_2, c.len_cycle_3]
        convs = [c.conv_cycle_1, c.conv_cycle_2, c.conv_cycle_3]
        
        growth_series = dynamic_converger_multiple_phase(rates, lengths, convs)
        
        # Cumulative Growth
        # Note: growth_series indices are 0 to TotalYears-1.
        # Year 1 Revenue = Base * (1+g0).
        cum_growth = (1 + growth_series).cumprod()
        revenues = base_rev * cum_growth
        
        return revenues, growth_series

    def _project_wacc(self, equity_val: float, debt_val: float) -> tuple[pd.Series, pd.Series, pd.Series]:
        """
        Projects WACC over time.
        Note: WACC depends on D/E ratio.
        Legacy code assumed CONSTANT D/E ratio based on current market values?
        Let's check logic:
        `company_beta = unlevered * (1 + (1-t)*D/E)`
        `terminal_beta = term_unlevered * (1 + (1-t)*D/E)`
        It keeps D/E constant (Using `debt_value/equity_value` passed in).
        """
        c = self.config
        total_cap = equity_val + debt_val
        if total_cap == 0: total_cap = 1.0 # Safety
        
        de_ratio = debt_val / equity_val if equity_val > 0 else 0.0
        debt_weight = debt_val / total_cap
        equity_weight = equity_val / total_cap
        
        # 1. Project Tax Rates
        cur_tax = self.base.get("tax_rate", c.marginal_tax_rate) # Use base effective if avail
        # Legacy: tax_rate_projector(current_effective, marginal, total_years, converge_year)
        # Assuming we converge to marginal tax rate.
        # We need a parameter for "year effective tax rate converge". using 'year_margin_converge' as proxy or add new?
        # Legacy had explicit param. We'll use year_margin_converge for simplicity for now or 5.
        tax_series = dynamic_converger(cur_tax, c.marginal_tax_rate, self.total_years, 5)

        # 2. Project Beta
        # Current Levered Beta
        beta_curr = c.unlevered_beta * (1 + (1 - c.marginal_tax_rate) * de_ratio)
        beta_term = c.terminal_unlevered_beta * (1 + (1 - c.marginal_tax_rate) * de_ratio)
        
        beta_series = dynamic_converger(beta_curr, beta_term, self.total_years, c.year_beta_converge)
        
        # 3. Project Cost of Debt (Pre-Tax)
        cod_series = dynamic_converger(c.current_pretax_cost_of_debt, c.terminal_pretax_cost_of_debt,
                                       self.total_years, c.year_cod_converge)
        
        # 4. Assemble WACC
        cost_equity = c.risk_free_rate + (beta_series * c.equity_risk_premium)
        after_tax_cod = cod_series * (1 - c.marginal_tax_rate) # Logic check: Uses marginal or projected tax?
        # Legacy used projected tax rate? No, legacy used `(1-marginal_tax_rate)` for Beta levering, 
        # and for WACC it calculated `after_tax_cost_of_debt` using marginal tax. 
        # But for `projected_operating_income_after_tax` it used `projected_tax_rates`.
        # Standard: WACC usually uses Marginal Tax Rate for the shield.
        
        wacc_series = (cost_equity * equity_weight) + (after_tax_cod * debt_weight)
        
        return wacc_series, cost_equity, tax_series

    def compute_valuation(self, equity_value_input: float, debt_value_input: float) -> ValuationResult:
        tracer.start_trace("ThreeStageDCF")
        c = self.config
        
        # 1. Forecast Revenue
        revenues, growth_rates = self._project_revenue()
        
        # 2. Forecast WACC
        wacc_series, cost_equity, tax_rates = self._project_wacc(equity_value_input, debt_value_input)
        
        # 3. Margins & EBIT
        margins = dynamic_converger(c.current_operating_margin, c.terminal_operating_margin,
                                    self.total_years, c.year_margin_converge)
        ebit = revenues * margins
        
        # 4. NOPAT (EBI)
        # Uses PROJECTED tax rates (effective)
        nopat = ebit * (1 - tax_rates)
        
        # 5. Reinvestment
        # Reinvestment = Change in Rev / SalesToCap
        sales_to_cap = dynamic_converger(c.current_sales_to_capital, c.terminal_sales_to_capital,
                                         self.total_years, c.year_sales_cap_converge)
        
        # Diff Revenue.
        # Need Rev[t] - Rev[t-1].
        # Prepend Base Revenue.
        rev_with_base = pd.concat([pd.Series([self.base.get("revenue", 0.0)]), revenues], ignore_index=True)
        delta_rev = rev_with_base.diff().dropna().reset_index(drop=True)
        
        reinvestment = delta_rev / sales_to_cap
        # Logic: If growth negative, reinvestment can be negative (divestiture).
        # Legacy had `asset_liquidation` logic. We'll simplify: allow negative.
        
        # 6. FCFF
        fcff = nopat - reinvestment
        
        # 7. Discounting
        # Discount Factor = 1 / cumprod(1+WACC)
        cum_wacc = (1 + wacc_series).cumprod()
        pv_fcff = fcff / cum_wacc
        sum_pv = pv_fcff.sum()
        
        # 8. Terminal Value
        # Uses Year N+1 assumption or Stable Growth Identity?
        # Legacy: 
        # term_cost_cap = last projected WACC
        # term_growth = last projected growth (end of cycle 3)
        # term_reinvest_rate = g / (ROIC). 
        # ROIC = WACC + Excess Returns.
        
        g_term = c.rates_cycle_3[1] # End of Cycle 3
        wacc_term = wacc_series.iloc[-1]
        
        # ROIC Perpetuity
        roic_term = wacc_term + c.additional_return_on_coc_perpetuity
        
        term_reinvest_rate = g_term / roic_term if roic_term != 0 else 0
        
        # Terminal NOPAT
        # Last Year NOPAT * (1+g)
        last_nopat = nopat.iloc[-1]
        term_nopat = last_nopat * (1 + g_term)
        
        term_fcff = term_nopat * (1 - term_reinvest_rate)
        
        terminal_val = term_fcff / (wacc_term - g_term)
        
        pv_terminal = terminal_val / cum_wacc.iloc[-1]
        
        enterprise_value = sum_pv + pv_terminal
        
        # 9. Equity Value
        # Net Debt = Debt - Cash
        # Base input might have "net_debt" or separate.
        # Legacy takes `cash_and_non_operating` and `debt_value`.
        # EV + Cash - Debt = Equity.
        cash = self.base.get("cash", 0.0)
        final_equity = enterprise_value + cash - debt_value_input
        
        # 10. Audit / Table
        projections = pd.DataFrame({
            "Growth": growth_rates,
            "Revenue": revenues,
            "Margin": margins,
            "EBIT": ebit,
            "TaxRate": tax_rates,
            "NOPAT": nopat,
            "Sales/Cap": sales_to_cap,
            "Reinvestment": reinvestment,
            "FCFF": fcff,
            "WACC": wacc_series,
            "PV_FCFF": pv_fcff
        })
        
        audit = {
            "sum_pv": sum_pv,
            "terminal_value": terminal_val,
            "pv_terminal": pv_terminal,
            "terminal_params": {
                "g": g_term,
                "wacc": wacc_term,
                "roic": roic_term,
                "rr": term_reinvest_rate
            }
        }
        
        tracer.log_step("ThreeStageDCF_Complete", audit, {"ev": enterprise_value})
        
        return ValuationResult(
            equity_value=final_equity,
            enterprise_value=enterprise_value,
            projections=projections,
            audit_trail=audit
        )
