
import json
import os
import pandas as pd
from dataclasses import asdict
from aletheia.tools.three_stage_dcf import ThreeStageDCF, DCFConfig

def trace_aapl_valuation():
    print("--- Tracing Three-Stage DCF for AAPL ---")
    
    # 1. AAPL Mock Finals (Approximate TTM)
    base_data = {
        "revenue": 391_000_000_000, # $391B
        "tax_rate": 0.15,           # Effective ~15-16%
        "cash": 62_000_000_000,     # Cash + Marketable Securities
    }
    
    # 2. Config: Mature Tech Giant
    config = DCFConfig(
        # Growth
        rates_cycle_1=[0.06, 0.05], len_cycle_1=5,
        rates_cycle_2=[0.05, 0.03], len_cycle_2=5,
        rates_cycle_3=[0.03, 0.03], len_cycle_3=0,
        
        # Margins: Very high, stable
        current_operating_margin=0.30, 
        terminal_operating_margin=0.25, # Conservative compression
        year_margin_converge=10,
        
        # Risk: Low Beta
        risk_free_rate=0.042, # 10Y Treasury
        equity_risk_premium=0.05,
        unlevered_beta=1.1,
        terminal_unlevered_beta=1.0,
        
        # Efficiency
        current_sales_to_capital=3.0,
        terminal_sales_to_capital=2.5
    )
    
    # 3. Inputs
    equity_market_val = 3_400_000_000_000
    debt_book_val = 100_000_000_000
    
    engine = ThreeStageDCF(base_data, config)
    result = engine.compute_valuation(equity_market_val, debt_book_val)
    
    # 4. Serialize to JSON
    output_dir = "tests/output"
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "aapl_three_stage_trace.json")
    
    # Convert DataFrames/Dataclasses to Dicts
    payload = {
        "meta": {
            "ticker": "AAPL",
            "model": "ThreeStageDCF",
            "description": "High Growth -> Transition -> Stable"
        },
        "valuation_summary": {
            "enterprise_value": result.enterprise_value,
            "equity_value": result.equity_value,
            "market_cap_input": equity_market_val,
            "upside": (result.equity_value / equity_market_val) - 1
        },
        "inputs": {
            "base_year": base_data,
            "config": asdict(config)
        },
        "audit_trail": result.audit_trail,
        "projections": result.projections.to_dict(orient="records")
    }
    
    with open(report_path, "w") as f:
        json.dump(payload, f, indent=2)
        
    print(f"✅ Trace saved to: {report_path}")
    print(f"Equity Value: ${result.equity_value/1e9:,.1f}B")

if __name__ == "__main__":
    trace_aapl_valuation()
