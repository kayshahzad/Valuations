
import json
import os
import unittest
from aletheia.tools.pro_forma import ProFormaEngine
from aletheia.utils.tracing import tracer

class TestDCFTrace(unittest.TestCase):
    def setUp(self):
        # Ensure output directory exists
        self.output_dir = "tests/output"
        os.makedirs(self.output_dir, exist_ok=True)
        self.trace_file = os.path.join(self.output_dir, "dcf_trace.json")
        
        # Helper to load CNC data
        self.cnc_json_path = "valuation_data/serving/latest/CNC_report.json"
        
    def test_trace_generation_and_content(self):
        if not os.path.exists(self.cnc_json_path):
            self.skipTest(f"CNC data not found at {self.cnc_json_path}")

        # 1. Load Data
        with open(self.cnc_json_path, 'r') as f:
            data = json.load(f)
            
        financials = data.get("2_financial_translation", {}).get("clean_financials", {})
        inc = financials.get("income_statement", {})
        cf = financials.get("cash_flow", {})
        bs = financials.get("balance_sheet", {})
        risk = data.get("3_capital_structure_risk", {}).get("capital_stack", {})
        ratios = data.get("2_financial_translation", {}).get("ratios", {})
        
        # Simple net debt for pro-forma validation
        net_debt = bs.get("total_debt", 0.0) - bs.get("cash", 0.0)
        
        base = {
            "revenue": inc.get("revenue"),
            "ebit": inc.get("ebit"),
            "da": cf.get("depreciation"),
            "capex": cf.get("capex"),
            "net_debt": net_debt
        }
        
        assumptions = {
            "wacc": risk.get("wacc", 0.10),
            "tax_rate": ratios.get("tax_rate", 0.21),
            "terminal_growth_rate": 0.03,
            "revenue_growth_initial": 0.05,
            "revenue_growth_decay": 0.005,
            "ebit_margin_initial": inc.get("ebit") / inc.get("revenue") if inc.get("revenue") else 0.05,
            "ebit_margin_target": 0.05,
            "ebit_margin_convergence_years": 5,
            "wc_change_percent_sales": 0.0 # Keeping simple for baseline trace
        }

        # 2. Run Engine & Trace
        # Force a fresh trace ID for clarity
        tracer.current_trace_id = None 
        tracer.start_trace("ProForma_Trace_Request")
        
        engine = ProFormaEngine(base, assumptions)
        dcf_result = engine.generate_forecast(projection_years=5)
        
        # 3. Save Trace
        tracer.save_traces(self.trace_file)
        print(f"Trace generated at: {self.trace_file}")

if __name__ == "__main__":
    unittest.main()
