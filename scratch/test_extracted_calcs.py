import pandas as pd
import numpy as np

# 1. Cyclicality Test
from aletheia.tools.cyclicality import calculate_z_score

def test_cyclicality():
    assert INDUSTRY_CLASSIFICATION["MSFT"] == "non_cyclical"
    assert INDUSTRY_CLASSIFICATION["CAT"] == "cyclical"
    print("✓ Cyclicality constants test passed.")

# 2. Forensic Metrics Test
from aletheia.tools.forensic_metrics import compute_operating_leverage_score

def test_forensic_metrics():
    # gross=0.5, ebit=0.25 -> ratio=0.5 -> score=5.0
    assert compute_operating_leverage_score(0.5, 0.25) == 5.0
    # gross=0.5, ebit=0.6 -> ratio=1.2 -> score=10.0 (max)
    assert compute_operating_leverage_score(0.5, 0.6) == 10.0
    # gross=0 -> score=5.0
    assert compute_operating_leverage_score(0.0, 0.1) == 5.0
    print("✓ Forensic metrics test passed.")

# 3. Capital Structure Test
from aletheia.tools.capital_structure import CapitalStructureRiskEngine

class MockState:
    def get(self, key, default):
        return default

def test_capital_structure():
    # Mock data where liquidity ratio > safe threshold
    row_data = {
        "raw_LongTermDebt": 100.0,
        "raw_CurrentLiabilities": 50.0,
        "raw_Cash": 5.0
    }
    
    # Needs a config object, let's mock it
    class MockConfig:
        maturity_amortization_rate = 0.2
        liquidity_ratio_safe = 2.0
    
    engine = CapitalStructureRiskEngine(MockState(), row_data, 1000.0)
    engine.config = MockConfig()
    
    # short_term_debt = 50 * 0.2 = 10
    # ltd amort = 100 * 0.2 = 20
    # total maturities = 30
    # cash = 5
    # ratio = 6.0
    result = engine.analyze_maturity_wall()
    assert result["maturities_next_2y"] == 30.0
    assert result["liquidity_ratio"] == 6.0
    assert result["liquidity_alert"] is True
    print("✓ Capital structure test passed.")

# 4. DCF Assumptions Test
from aletheia.tools.dcf_assumptions import build_base_assumptions

def test_dcf_assumptions():
    row = pd.Series({
        "clean_Revenue": 100.0,
        "clean_CashTaxRate": np.nan, # Should fallback to 0.21
        "sector": "Technology",
        "derived_GrossMargin_Pct": 60.0 # Will trigger Knowledge_Heavy
    })
    clean_data = {
        "SG&A": 20.0,
        "Depreciation": 5.0,
        "CapEx_Total": 10.0
    }
    dcf_config = {"revenue_growth_initial": 0.15}
    archetypes = {
        "Knowledge_Heavy": {"wc_change_percent_sales": 0.05}
    }
    
    assumptions, net_debt = build_base_assumptions(row, clean_data, dcf_config, 0.10, archetypes)
    
    assert assumptions["sga_percent_sales"] == 0.20
    assert assumptions["da_percent_sales"] == 0.05
    assert assumptions["capex_percent_sales"] == 0.10
    assert assumptions["tax_rate"] == 0.21 # fallback
    assert assumptions["revenue_growth_initial"] == 0.15
    assert assumptions["wc_change_percent_sales"] == 0.05 # from archetype override
    assert net_debt == 0.0
    
    print("✓ DCF assumptions test passed.")

if __name__ == "__main__":
    test_cyclicality()
    test_forensic_metrics()
    test_capital_structure()
    test_dcf_assumptions()
    print("All extracted calculations tests passed deterministically.")
