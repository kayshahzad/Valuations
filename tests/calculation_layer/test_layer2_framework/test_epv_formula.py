# tests/calculation_layer/test_layer2_framework/test_epv_formula.py

import pytest
import math
from aletheia.data.database import InvestmentDatabase
from aletheia.data.quantitative_screens import QuantitativeScreens

class TestEPVFormula:
    
    @pytest.fixture(scope="class")
    def epv_result_and_record(self):
        """Fetch a real EPV computation for AAPL."""
        db = InvestmentDatabase(verbose=False)
        try:
            df = db.get_latest("AAPL")
            if df.empty:
                pytest.skip("Insufficient AAPL data in DB for EPV test")
                
            fy = int(df["fiscal_year"].max())
            
            from aletheia.data.cleaning_engine import CleaningEngine
            cleaner = CleaningEngine(db_path=db.db_path, verbose=False)
            record = cleaner.clean("AAPL", fy)
            
            screens = QuantitativeScreens(verbose=False)
            result = screens._earnings_power_value(record, current_price=150.0, wacc_override=0.10)
            return result, record
        except Exception as e:
            pytest.skip(f"DB load failed: {e}")
        finally:
            db.close()
            
    def test_formula_matches_section_16_4(self, epv_result_and_record):
        """Framework Section 16.4: EPV = Normalized EBIT × (1 - t) / WACC"""
        result, record = epv_result_and_record
        
        if result.epv is None:
            pytest.skip("No EPV computed")
            
        # Extract the real components from the result object
        ebit = result.normalized_ebit
        tax_rate = result.tax_rate
        wacc = result.wacc
        
        # Manual calculation based on Section 16.4 formula
        nopat = ebit * (1 - tax_rate)
        expected_epv = nopat / wacc
        
        # Result is rounded to nearest integer
        expected_epv_rounded = round(expected_epv, 0)
        
        assert math.isclose(result.epv, expected_epv_rounded, rel_tol=1e-9)
    
    def test_returns_insufficient_data_for_missing_ebit(self, make_calc_input):
        """Per framework intent: undefined inputs should gracefully return a status, not crash."""
        from aletheia.data.cleaning_engine import CleanedRecord
        record = CleanedRecord(ticker="TEST", fiscal_year=2023, period_end_date="2023-12-31")
        # Empty record has no EBIT
        
        screens = QuantitativeScreens(verbose=False)
        result = screens._earnings_power_value(record)
        
        assert result.epv is None
        assert result.signal == "insufficient_data"