
import unittest
from aletheia.tools.pro_forma import ProFormaEngine

class TestProFormaDCF(unittest.TestCase):
    def test_pro_forma_basic_dcf(self):
        """
        Test a simplified 5-year forecast to verify DCF logic.
        New Engine uses FCFF = NOPAT + D&A - Capex - Change WC
        """
        base = {
            "revenue": 1000.0,
            "ebit": 100.0,      # Implies 10% margin
            "da": 50.0,
            "capex": 50.0,
            "net_debt": 100.0
        }
        
        assumptions = {
            "wacc": 0.10,
            "tax_rate": 0.20,
            "terminal_growth_rate": 0.02,
            
            "revenue_growth_initial": 0.10,
            "revenue_growth_decay": 0.0,
            
            "ebit_margin_initial": 0.10,
            "ebit_margin_target": 0.10,
            "ebit_margin_convergence_years": 5,
            
            # Simple reinvestment scaling
            "da_percent_sales": 0.05,    # 50/1000
            "capex_percent_sales": 0.05, # 50/1000
            "wc_change_percent_sales": 0.0
        }
        
        engine = ProFormaEngine(base, assumptions)
        result = engine.generate_forecast(projection_years=5)
        
        # Check Year 1
        # Revenue: 1000 * 1.10 = 1100
        # EBIT: 1100 * 0.10 = 110
        # NOPAT: 110 * (1 - 0.20) = 88
        # D&A: 1100 * 0.05 = 55
        # Capex: 1100 * 0.05 = 55
        # WC: 0
        # FCFF = 88 + 55 - 55 - 0 = 88
        
        y1 = result['projections'][0]
        self.assertEqual(y1['year'], 1)
        self.assertAlmostEqual(y1['revenue'], 1100.0)
        self.assertAlmostEqual(y1['ebit'], 110.0)
        self.assertAlmostEqual(y1['nopat'], 88.0)
        self.assertAlmostEqual(y1['fcff'], 88.0)
        
        # Check Terminal Value
        self.assertTrue('enterprise_value' in result)
        self.assertGreater(result['enterprise_value'], 0)
        
        # Equity = EV - Net Debt
        # EV = sum_pv + terminal_pv
        self.assertAlmostEqual(result['equity_value'], result['enterprise_value'] - base['net_debt'])

    def test_pro_forma_terminal_growth(self):
        """
        Verify terminal value calculation.
        """
        # Zero growth, zero capex/da just to isolate TV math
        base = {"revenue": 100.0, "ebit": 10.0, "net_debt": 0.0}
        assumptions = {
            "wacc": 0.10,
            "tax_rate": 0.0, # NOPAT = EBIT
            "terminal_growth_rate": 0.05,
            
            "revenue_growth_initial": 0.0, 
            "ebit_margin_initial": 0.10,
            
            "da_percent_sales": 0.0,
            "capex_percent_sales": 0.0
        } 
        
        engine = ProFormaEngine(base, assumptions)
        result = engine.generate_forecast(projection_years=1)
        
        # Year 1
        # Rev=100, EBIT=10, NOPAT=10, FCFF=10
        y1 = result['projections'][0]
        self.assertAlmostEqual(y1['fcff'], 10.0)
        
        # TV = FCFF_1 * (1+g) / (WACC - g)
        # = 10 * 1.05 / (0.10 - 0.05) = 10.5 / 0.05 = 210
        
        # PV TV = 210 / (1.1)^1 = 190.909...
        expected_pv_tv = (10 * 1.05 / 0.05) / 1.1
        
        self.assertAlmostEqual(result['discounted_terminal_value'], expected_pv_tv, places=2)

if __name__ == '__main__':
    unittest.main()

