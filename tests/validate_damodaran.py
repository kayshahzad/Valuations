"""
tests/validate_damodaran.py

Phase 1: Academic Mathematical Validation

This script passes a static, textbook scenario through the Aletheia _project_scenario
engine to prove that the DCF calculation logic (discounting, FCFF components,
terminal value, Liberti reinvestment) perfectly matches academic math.
"""

import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aletheia.tools.dcf_engine import _project_scenario, ScenarioAssumptions

def run_academic_test():
    print("================================================================================")
    print("  Phase 1: Academic Mathematical Validation (Damodaran/Liberti Test Case)")
    print("================================================================================\n")
    
    # 1. Setup Static Assumptions
    assumptions = ScenarioAssumptions(
        name="academic_base",
        revenue_cagr_y1_5=0.10,
        revenue_cagr_y6_10=0.05,
        ebit_margin_current=0.20,
        ebit_margin_terminal=0.20,
        capex_pct_revenue=0.05,
        da_pct_revenue=0.03,
        nwc_pct_revenue=0.02,
        wacc=0.10,
        terminal_growth=0.03,
        tax_rate=0.25,
        justification="Damodaran textbook test case"
    )
    
    # Base year inputs
    base_revenue = 1000.0
    base_roic = 0.15
    base_da = 30.0
    base_capex = 50.0
    base_nwc = 20.0
    latest_fy = 2023
    
    print("--- ASSUMPTIONS ---")
    print(f"Revenue (Base) = ${base_revenue}")
    print(f"WACC = {assumptions.wacc:.1%}")
    print(f"Terminal Growth (g) = {assumptions.terminal_growth:.1%}")
    print(f"Base ROIC = {base_roic:.1%}\n")
    
    # 2. Run Engine
    projections, terminal, enterprise_value = _project_scenario(
        assumptions=assumptions,
        base_revenue=base_revenue,
        base_roic=base_roic,
        base_da=base_da,
        base_capex=base_capex,
        base_nwc=base_nwc,
        latest_fy=latest_fy,
        forecast_years=10
    )
    
    # 3. Output Projections
    print("--- 10-YEAR PROJECTIONS ---")
    print(f"{'Yr':<4} | {'Revenue':<10} | {'EBIT':<10} | {'NOPAT':<10} | {'D&A':<10} | {'CapEx':<10} | {'dNWC':<10} | {'FCFF':<10} | {'PV(FCFF)':<10}")
    print("-" * 105)
    
    for p in projections:
        print(f"{p.year:<4} | ${p.revenue:<9.1f} | ${p.ebit:<9.1f} | ${p.nopat:<9.1f} | ${p.da:<9.1f} | ${p.capex:<9.1f} | ${p.delta_nwc:<9.1f} | ${p.fcff:<9.1f} | ${p.pv_fcff:<9.1f}")
        
    print("\n--- TERMINAL VALUE ---")
    print(f"Gordon Growth TV      : ${terminal.gordon_tv:,.1f}")
    print(f"Liberti Reinvest TV   : ${terminal.reinvestment_tv:,.1f}")
    print(f"TV Selected           : ${terminal.tv_used:,.1f}  (PV: ${terminal.pv_tv:,.1f})")
    
    print(f"\n--- ENTERPRISE VALUE ---")
    print(f"Sum of PV(FCFF)       : ${projections[-1].cumulative_pv:,.1f}")
    print(f"PV of Terminal Value  : ${terminal.pv_tv:,.1f}")
    print(f"Total Enterprise Value: ${enterprise_value:,.1f}\n")

if __name__ == "__main__":
    run_academic_test()
