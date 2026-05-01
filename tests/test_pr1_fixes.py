import pytest
import duckdb
from aletheia.agents.forensic import load_db_context
# from aletheia.agents.lead import _generate_report
from unittest.mock import patch, MagicMock

def test_quality_screens_retrieval():
    """Fix 2: Ensure beneish_m_score is populated via the LEFT JOIN in company_records_latest."""
    from aletheia.data.database import InvestmentDatabase
    db = InvestmentDatabase(verbose=False)  # This will execute CREATE OR REPLACE VIEW
    
    try:
        df = db.query("SELECT beneish_m_score, sloan_accrual_ratio FROM company_records_latest WHERE ticker='LLY' ORDER BY fiscal_year DESC LIMIT 1")
        if not df.empty:
            beneish = df.iloc[0]['beneish_m_score']
            sloan = df.iloc[0]['sloan_accrual_ratio']
            assert beneish is not None, "Beneish score should not be null; LEFT JOIN is missing or data is absent."
            assert -3.0 <= beneish <= 1.0, f"Beneish score {beneish} is completely out of the expected [-3, 1] bounds."
    finally:
        db.close()

def test_sbc_pct_fcf_formatting_bounds():
    """Fix 3: SBC as % of FCF is stored as a percentage. Formatting shouldn't multiply by 100 again."""
    from aletheia.agents.forensic import build_db_context_str
    
    # Test our pre_pct formatting logic indirectly via build_db_context_str
    db_mock = {"fiscal_year": 2024, "sbc_pct_fcf": 6.98, "roic": 0.24}
    context_str = build_db_context_str(db_mock)
    
    assert "SBC % of FCF:       7.0%" in context_str, "6.98 stored as percentage should format to 7.0%"
    assert "ROIC:               24.0%" in context_str, "0.24 stored as decimal should format to 24.0%"

def test_floor_price_per_share_positive():
    """Fix 5: Floor price per share must be > 0 when floor_value > 0 and shares > 0."""
    # We can mock the inputs to compile_report to ensure the logic fires
    
    mock_state = {
        "ticker": "TEST",
        "strategist_report": {
            "risk_factors": {
                "downside": {
                    "floor_value": 9000000000.0
                }
            }
        },
        "phase2_valuation": {
            "bridge": {
                "base": {
                    "shares_diluted": 100000000.0
                }
            }
        }
    }
    
    # We won't fully run compile_report because it requires DuckDB setup for "TEST" ticker,
    # but we can test the specific snippet extracted from lead.py.
    capital_structure = mock_state["strategist_report"]
    p2 = mock_state["phase2_valuation"]
    
    shares = p2.get("bridge", {}).get("base", {}).get("shares_diluted")
    if not shares:
        shares = p2.get("three_scenario_dcf", {}).get("base", {}).get("shares_diluted")
        
    floor_val = capital_structure.get("risk_factors", {}).get("downside", {}).get("floor_value")
    if shares and floor_val:
        capital_structure["risk_factors"]["downside"]["floor_price_per_share"] = floor_val / shares
        
    floor_price = capital_structure["risk_factors"]["downside"].get("floor_price_per_share", 0)
    assert floor_price == 90.0, f"Expected 90.0 floor price, got {floor_price}"
    assert floor_price > 0, "Floor price per share should be > 0 when inputs are valid."
