from aletheia.agents.fundamentalist import fundamentalist_agent
from langchain_core.messages import HumanMessage
import json
import os

# Setup Mock State
state = {
    "ticker": "AAPL",
    "financial_data": {}, # Should be ignored now
    "dcf_config": {"revenue_growth_initial": 0.15},
    "strategist_report": {"wacc": 0.08}
}

print("Running Fundamentalist with Canonical Data...")
try:
    result = fundamentalist_agent(state)
    
    print("\n--- Result Summary ---")
    val_report = result.get("valuation_report", {})
    dcf = val_report.get("dcf_result", {})
    
    print(f"Upside: {dcf.get('calculated_upside', 0):.1f}%")
    
    base_fin = val_report.get("base_financials", {})
    print(f"Base Revenue: ${base_fin.get('net_sales', 0)/1e9:.1f}B")
    print(f"Base Interest: ${base_fin.get('interest_expense', 0)/1e9:.1f}B (Dynamic)")
    
    # Check Serving DB
    serving_path = f"valuation_data/serving/latest/AAPL.json"
    if os.path.exists(serving_path):
        print(f"✅ Serving File Created: {serving_path}")
    else:
        print(f"❌ Serving File Missng")

except Exception as e:
    print(f"FAILED: {e}")
    import traceback
    traceback.print_exc()

finally:
    from aletheia.utils.tracing import tracer
    print("\n--- Trace Logs ---")
    # tracer.get_traces() might be empty if saved to disk and cleared? 
    # Tracer singleton usually keeps history unless cleared.
    print(json.dumps(tracer.get_traces(), indent=2))
