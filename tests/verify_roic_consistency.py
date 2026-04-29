
import unittest
from aletheia.tools.pro_forma import ProFormaEngine

class TestROICConsistency(unittest.TestCase):
    
    def test_optimism_tax_trigger(self):
        """
        Test that Optimism Tax triggers when implied ROIC > Historical Max * 1.2.
        Scenario:
        - Hist Max ROIC: 10% (0.10) => Threshold 12%
        - Projected Margins imply ROIC ~20%
        - Expect: 'Optimism Tax' logic reduces FCFF
        """
        print("\n--- Testing ROIC Consistency (Optimism Tax) ---")
        base = {
            "revenue": 1000, "ebit": 100, "invested_capital": 1000, # Base ROIC 10%
            "historical_roic_max": 0.10,
            "da": 50, "capex": 50
        }
        
        assumptions = {
            "wacc": 0.10, "tax_rate": 0.0, # Simple math
            "terminal_growth_rate": 0.03,
            "revenue_growth_initial": 0.05,
            # Force high margin -> High NOPAT -> High ROIC
            "ebit_margin_target": 0.25, # 25% margin -> 250 EBIT -> 250 NOPAT. 
            # Implied ROIC next year: 250 / 1000 = 25%.
            # Threshold 12%. Gap 13%. 
            # Tax ~ 13% * 1000 = 130.
            "capex_percent_sales": 0.05,
            "da_percent_sales": 0.05,
        }
        
        engine = ProFormaEngine(base, assumptions)
        result = engine.generate_forecast()
        
        warnings = result["diagnostics"]["warnings"]
        print(f"Warnings: {warnings}")
        
        # Check for Optimism Tax Application
        self.assertTrue(any("Optimism Tax" in w for w in warnings), "Should trigger Optimism Tax warning")
        
        # Check that FCFF is reduced? 
        # NOPAT ~262 (Y1 Rev 1050 * 25% = 262.5). 
        # Reinvestment (Capex=DA) = 0.
        # FCF (raw) = 262.5.
        # Tax ~ (26.25% - 12%) * 1000 = 142.5.
        # FCF (taxed) ~ 120.
        fcff_y1 = result["projections"][0]["fcff"]
        print(f"FCFF Year 1: {fcff_y1}")
        self.assertLess(fcff_y1, 200, "FCFF should be materially reduced by Optimism Tax")

    def test_ic_growth_engine(self):
        """Test that IC updates correctly: IC_t = IC_{t-1} + Reinvestment."""
        print("\n--- Testing IC Growth Engine ---")
        # No Optimism Tax here, just mechanics
        base = {
            "revenue": 100, "ebit": 10, "invested_capital": 500, "historical_roic_max": 1.0, # High cap to avoid tax
            "da": 10, "capex": 20 # Net Capex 10
        }
        assumptions = {
            "wacc": 0.10, "tax_rate": 0.20, "terminal_growth_rate": 0.0,
            "capex_percent_sales": 0.20,
            "da_percent_sales": 0.10,
            "wc_change_percent_sales": 0.0
        }
        engine = ProFormaEngine(base, assumptions)
        result = engine.generate_forecast()
        
        path = result["audit_metrics"]["invested_capital_path"]
        print(f"IC Path: {path}")
        
        # Y0: 500
        # Y1: Reinvest = (Rev * 0.2) - (Rev * 0.1) = 0.1 * Rev. 
        # Rev ~ 105. Reinvest ~ 10.5.
        # IC1 should be ~510.5.
        
        self.assertAlmostEqual(path[1], path[0] + (result["projections"][0]["capex"] - result["projections"][0]["depreciation"]), delta=1.0)

if __name__ == "__main__":
    unittest.main()
