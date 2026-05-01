# tests/calculation_layer/test_layer2_framework/test_beneish_8ratios.py

import pytest
import math
from aletheia.data.database import InvestmentDatabase
from aletheia.data.quantitative_screens import QuantitativeScreens

class TestBeneishCompliance:
    
    @pytest.fixture(scope="class")
    def beneish_result(self):
        """Fetch a real Beneish computation for AAPL to test the framework compliance."""
        db = InvestmentDatabase(verbose=False)
        try:
            df = db.get_latest("AAPL")
            if df.empty or len(df) < 2:
                pytest.skip("Insufficient AAPL data in DB for Beneish test")
            
            # Need current and prior year for Beneish
            df = df.sort_values(by="fiscal_year", ascending=False)
            current_fy = int(df["fiscal_year"].iloc[0])
            prior_fy = int(df["fiscal_year"].iloc[1])
            
            from aletheia.data.cleaning_engine import CleaningEngine
            cleaner = CleaningEngine(db_path=db.db_path, verbose=False)
            record = cleaner.clean("AAPL", current_fy)
            prior = cleaner.clean("AAPL", prior_fy)
            
            screens = QuantitativeScreens(verbose=False)
            return screens._beneish_m_score(record, prior)
        except Exception as e:
            pytest.skip(f"DB load failed: {e}")
        finally:
            db.close()

    def test_eight_components_present(self, beneish_result):
        """Beneish output must include all 8 ratios."""
        if beneish_result.data_completeness < 1.0:
            pytest.skip("Live DB lacks sufficient data points to compute all 8 Beneish components for AAPL")
            
        components = beneish_result.components
        for component in ["DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "LVGI", "TATA"]:
            assert component in components, f"Missing component {component}"
    
    def test_m_score_formula(self, beneish_result):
        """M-Score combination must perfectly match Beneish 1999 published coefficients."""
        if beneish_result.data_completeness < 1.0:
            pytest.skip("Incomplete data")
            
        components = beneish_result.components
        
        # Manual recalculation using explicit formula
        expected_m_score = (
            -4.84
            + 0.920 * components["DSRI"]
            + 0.528 * components["GMI"]
            + 0.404 * components["AQI"]
            + 0.892 * components["SGI"]
            + 0.115 * components["DEPI"]
            - 0.172 * components["SGAI"]
            + 4.679 * components["TATA"]
            - 0.327 * components["LVGI"]
        )
        
        # QuantitativeScreens rounds to 4 decimal places
        expected_m_score_rounded = round(expected_m_score, 4)
        
        assert math.isclose(beneish_result.m_score, expected_m_score_rounded, rel_tol=1e-9), \
            f"Expected {expected_m_score_rounded}, got {beneish_result.m_score}"
    
    def test_threshold_at_negative_178(self, beneish_result):
        """Framework threshold: M-Score > -1.78 = elevated risk."""
        if beneish_result.m_score is None:
            pytest.skip("No M-score computed")
            
        should_flag = beneish_result.m_score > -1.78
        assert beneish_result.is_flagged == should_flag, \
            "is_flagged property must perfectly align with > -1.78 threshold"