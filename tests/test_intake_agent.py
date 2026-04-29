
import unittest
from aletheia.agents.intake import intake_agent

class TestIntakeAgent(unittest.TestCase):
    def test_run_intake_aapl(self):
        """
        Runs the intake_agent function for AAPL and validates the returned state dictionary.
        """
        print("\n🧪 TESTING INTAKE AGENT (AAPL) 🧪")
        
        # 1. Setup State
        state = {"ticker": "AAPL"}
        
        # 2. Execute Agent
        output = intake_agent(state)
        
        # 3. Assert Output Structure
        self.assertIsNotNone(output, "Agent returned None")
        self.assertIn("serving_base", output, "Output missing 'serving_base' key")
        self.assertIn("messages", output, "Output missing 'messages' key")
        
        base = output["serving_base"]
        
        # 4. Validate Schema (Sections)
        expected_sections = ["financials", "ratios", "economics", "meta"]
        for section in expected_sections:
            self.assertIn(section, base, f"Serving Base missing '{section}'")
            
        # 5. Validate Financials
        fin = base["financials"]
        self.assertIn("income_statement", fin)
        self.assertIn("balance_sheet", fin)
        
        # Check Key Metrics (Sanity Check)
        rev = fin["income_statement"].get("revenue", 0)
        net_income = fin["income_statement"].get("net_income", 0)
        assets = fin["balance_sheet"].get("assets", 0)
        
        self.assertGreater(rev, 0, "Revenue should be positive")
        self.assertGreater(assets, 0, "Assets should be positive")
        
        print(f"✅ Revenue: ${rev:,.0f}")
        print(f"✅ Net Income: ${net_income:,.0f}")
        print(f"✅ Total Assets: ${assets:,.0f}")
        
        # 6. Validate Economics/Ratios
        ratios = base["ratios"]
        self.assertIn("gross_margin", ratios)
        self.assertIn("operating_margin", ratios)
        
        print(f"✅ Gross Margin: {ratios['gross_margin']:.1%}")
        
        # 7. Check Meta
        meta = base["meta"]
        self.assertTrue(meta.get("form") in ["10-K", "10-Q"], f"Unexpected Form: {meta.get('form')}")
        print(f"✅ Source: {meta.get('form')} (FY{meta.get('fy')})")

if __name__ == "__main__":
    unittest.main()
