
import unittest
import json
from aletheia.agents.intake import intake_agent
from aletheia.agents.strategist import strategist_agent
from aletheia.agents.forensic import forensic_agent
from aletheia.agents.value_chain import value_chain_agent
from aletheia.agents.context import strategic_context_agent
from aletheia.agents.contrarian import contrarian_agent
from aletheia.agents.fundamentalist import fundamentalist_agent
from aletheia.agents.lead import lead_agent

class TestIndividualAgents(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        """
        Run Intake ONCE to get the Base State for all other agents.
        This simulates the pipeline flow.
        """
        print("\n🚀 SETTING UP BASE STATE (Running Intake for AAPL)...")
        initial_state = {"ticker": "AAPL", "dcf_config": {"target_debt_equity": 0.5}}
        cls.state = intake_agent(initial_state)
        cls.state.update(initial_state) # Ensure original config is preserved
        
        if "serving_base" not in cls.state:
            raise RuntimeError("Setup Failed: Intake Agent did not return serving_base.")

    def test_01_intake_output(self):
        """Validate Intake output exists (Sanity check)"""
        print("\n🧪 1. Testing Intake Agent")
        self.assertIn("serving_base", self.state)
        print("✅ Intake Agent: Valid")

    def test_02_strategist_agent(self):
        """Test Strategist (Capital Structure)"""
        print("\n🧪 2. Testing Strategist Agent")
        output = strategist_agent(self.state)
        
        self.assertIsNotNone(output)
        self.assertIn("strategist_report", output)
        
        report = output["strategist_report"]
        self.assertIn("wacc", report["capital_stack"])
        wacc = report["capital_stack"]["wacc"]
        
        self.assertGreater(wacc, 0.0)
        print(f"✅ Strategist: WACC = {wacc:.1%}")
        
        # Update class state for downstream dependencies (Fundamentalist needs WACC)
        self.state.update(output)

    def test_03_forensic_agent(self):
        """Test Forensic (Moat/Quality)"""
        print("\n🧪 3. Testing Forensic Agent")
        output = forensic_agent(self.state)
        
        self.assertIsNotNone(output)
        self.assertIn("forensic_report", output)
        
        report = output["forensic_report"]
        score = report.get("moat_score", 0)
        print(f"✅ Forensic: Moat Score = {score}/10")
        
        self.state.update(output)

    def test_04_value_chain_agent(self):
        """Test Value Chain"""
        print("\n🧪 4. Testing Value Chain Agent")
        output = value_chain_agent(self.state)
        
        self.assertIsNotNone(output)
        self.assertIn("value_chain_report", output)
        print("✅ Value Chain: Valid")
        
        self.state.update(output)

    def test_05_context_agent(self):
        """Test Strategic Context"""
        print("\n🧪 5. Testing Strategic Context Agent")
        output = strategic_context_agent(self.state)
        
        self.assertIsNotNone(output)
        self.assertIn("strategic_context_report", output)
        print("✅ Strategic Context: Valid")
        
        self.state.update(output)

    def test_06_contrarian_agent(self):
        """Test Contrarian"""
        print("\n🧪 6. Testing Contrarian Agent")
        output = contrarian_agent(self.state)
        
        self.assertIsNotNone(output)
        self.assertIn("contrarian_report", output)
        print("✅ Contrarian: Valid")
        
        self.state.update(output)

    def test_07_fundamentalist_agent(self):
        """Test Fundamentalist (DCF)"""
        print("\n🧪 7. Testing Fundamentalist Agent")
        # Fundamentalist needs Strategist (WACC) and Intake (Base)
        output = fundamentalist_agent(self.state)
        
        self.assertIsNotNone(output)
        self.assertIn("valuation_report", output)
        
        val = output["valuation_report"]
        upside = val.get("calculated_upside", 0)
        print(f"✅ Fundamentalist: Upside = {upside:.1f}%")
        
        self.state.update(output)

    def test_08_lead_agent(self):
        """Test Lead Agent (Report Generation)"""
        print("\n🧪 8. Testing Lead Agent")
        output = lead_agent(self.state)
        
        self.assertIsNotNone(output)
        self.assertIn("final_report", output)
        
        print("✅ Lead Agent: Report Generated")


if __name__ == "__main__":
    unittest.main()
