"""
tests/validate_monte_carlo.py

Phase 2: Monte Carlo Sensitivity Analysis

This script runs the DCF Engine 1,000 times, randomly varying the WACC and 
Terminal Growth rate around a normal distribution, to output a probability 
density summary of the Intrinsic Value. This proves whether the valuation 
signal is statistically robust or highly vulnerable to tiny margin-of-errors.
"""

import sys
import os
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aletheia.tools.dcf_engine import _project_scenario, ScenarioAssumptions

def run_monte_carlo():
    print("================================================================================")
    print("  Phase 2: Monte Carlo Sensitivity Analysis")
    print("================================================================================\n")
    
    ITERATIONS = 5000
    
    # Base Inputs (Mocking AAPL-like base values)
    base_revenue = 390e9
    base_roic = 0.50
    base_da = 11e9
    base_capex = 10e9
    base_nwc = 0.0 # simplified
    latest_fy = 2023
    
    # Distributions
    # WACC: Mean 10%, StdDev 1%
    wacc_dist = np.random.normal(0.10, 0.01, ITERATIONS)
    # Term Growth: Mean 2.5%, StdDev 0.5% (capped at 4%)
    g_dist = np.random.normal(0.025, 0.005, ITERATIONS)
    g_dist = np.clip(g_dist, 0.0, 0.04)
    # Revenue Growth Y1-5: Mean 10%, StdDev 2%
    rev_dist = np.random.normal(0.10, 0.02, ITERATIONS)
    
    results = []
    
    for i in range(ITERATIONS):
        assumptions = ScenarioAssumptions(
            name="monte_carlo",
            revenue_cagr_y1_5=rev_dist[i],
            revenue_cagr_y6_10=rev_dist[i] * 0.6,
            ebit_margin_current=0.30,
            ebit_margin_terminal=0.30,
            capex_pct_revenue=0.03,
            da_pct_revenue=0.03,
            nwc_pct_revenue=0.0,
            wacc=wacc_dist[i],
            terminal_growth=g_dist[i],
            tax_rate=0.20,
            justification="Monte Carlo"
        )
        
        _, _, ev = _project_scenario(
            assumptions=assumptions,
            base_revenue=base_revenue,
            base_roic=base_roic,
            base_da=base_da,
            base_capex=base_capex,
            base_nwc=base_nwc,
            latest_fy=latest_fy,
            forecast_years=10
        )
        
        results.append(ev)
        
    results = np.array(results) / 1e9 # Convert to Billions
    
    print(f"--- MONTE CARLO RESULTS ({ITERATIONS} Iterations) ---")
    print(f"Mean Enterprise Value   : ${np.mean(results):,.1f} B")
    print(f"Median Enterprise Value : ${np.median(results):,.1f} B")
    print(f"5th Percentile (P05)    : ${np.percentile(results, 5):,.1f} B")
    print(f"95th Percentile (P95)   : ${np.percentile(results, 95):,.1f} B")
    print(f"Standard Deviation      : ${np.std(results):,.1f} B")
    print("\nCONCLUSION: The spread between the 5th and 95th percentile represents")
    print("the valuation confidence interval under macroeconomic stress.\n")

if __name__ == "__main__":
    run_monte_carlo()
