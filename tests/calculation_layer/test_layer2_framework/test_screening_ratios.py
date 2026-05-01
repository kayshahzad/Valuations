import pytest
import pandas as pd
import numpy as np
import math

from aletheia.tools.screening_ratios import ScreeningEngine, ScreeningCard, PASS, FLAG, FAIL

def create_mock_data() -> tuple[pd.DataFrame, pd.Series]:
    """Generates a perfectly predictable deterministic dataset for testing math logic."""
    df = pd.DataFrame({
        "fiscal_year": [2021, 2022, 2023, 2024],
        "clean_Revenue": [100.0, 110.0, 121.0, 133.1], # Exactly 10% CAGR
        "clean_NormalizedEBIT": [10.0, 11.0, 12.0, 15.0],
        "derived_EBITDA": [15.0, 16.0, 18.0, 20.0],
        "clean_NOPAT": [8.0, 9.0, 10.0, 12.0],
        "derived_FCF": [5.0, 6.0, 7.0, 10.0],
        "derived_ROIC": [0.15, 0.15, 0.15, 0.15],
        "derived_ROE": [0.20, 0.20, 0.20, 0.20],
        "derived_NetDebt": [20.0, 20.0, 20.0, 20.0],
        "derived_InvestedCapital": [80.0, 80.0, 80.0, 80.0],
        "derived_GrossMargin_Pct": [45.0, 45.0, 45.0, 45.0],
        "derived_EBIT_Margin_Pct": [10.0, 10.0, 10.0, 11.27],
        "derived_FCF_Margin_Pct": [5.0, 5.0, 5.0, 7.5],
        "raw_NetIncome": [10.0, 11.0, 12.1, 13.31], # Also exactly 10% CAGR
        "raw_TotalAssets": [100.0, 110.0, 120.0, 130.0],
        "raw_TotalEquity": [50.0, 55.0, 60.0, 65.0],
        "raw_LongTermDebt": [20.0, 20.0, 20.0, 20.0],
        "raw_Cash": [10.0, 10.0, 10.0, 10.0],
        "clean_SBC": [0.0, 0.0, 0.0, 0.0],
        "clean_SBC_PctFCF": [2.0, 2.0, 2.0, 2.0], # <5% passing
        "clean_CashTaxRate": [0.21, 0.21, 0.21, 0.21],
        "raw_json": [None]*4,
        "clean_json": [None]*4
    })
    row = df.iloc[-1]
    return df, row

class TestScreeningRatios:
    def test_pe_and_peg_ratios_math(self, make_calc_input):
        engine = ScreeningEngine(verbose=False)
        df, row = create_mock_data()
        card = ScreeningCard(ticker="TEST", fiscal_year=2024, current_price=20.0, market_cap=200.0)
        shares = 10.0
        
        # Act
        res = engine._compute_metrics(
            card=card, all_years_df=df, fy=2024, row=row,
            price=20.0, mktcap=200.0, shares=shares,
            wacc=0.10, terminal_growth=0.03
        )
        
        # Assert PE Math: Net Income = 13.31, Shares = 10.0 -> EPS = 1.331
        # P/E = 20.0 / 1.331 = 15.026
        pe_metric = res.get("P/E Ratio")
        assert math.isclose(pe_metric.value, 15.026296, rel_tol=1e-5)
        
        # Assert PEG Math: EPS CAGR = 10% (10.0 -> 13.31 over 3 years)
        # PEG = 15.026 / (0.10 * 100) = 1.5026
        peg_metric = res.get("PEG Ratio")
        assert math.isclose(peg_metric.value, 1.5026296, rel_tol=1e-5)

    def test_dynamic_cash_conversion(self, make_calc_input):
        """Verifies Liberti Cash Conversion strictly follows (1 - g/ROIC) dynamics."""
        engine = ScreeningEngine(verbose=False)
        df, row = create_mock_data()
        
        # NOPAT = 12.0, EBITDA = 20.0
        # g = 0.03, ROIC = 0.15
        # cash_conv = 12 * (1 - 0.03/0.15) / 20 = 12 * (0.8) / 20 = 9.6 / 20 = 0.48
        
        card = ScreeningCard(ticker="TEST", fiscal_year=2024, current_price=20.0, market_cap=200.0)
        res1 = engine._compute_metrics(
            card=card, all_years_df=df, fy=2024, row=row,
            price=20.0, mktcap=200.0, shares=10.0,
            wacc=0.10, terminal_growth=0.03
        )
        
        assert math.isclose(res1.get("EBITDA Cash Conversion").value, 0.48, rel_tol=1e-9)

        # Re-run with different injected g
        # g = 0.075, ROIC = 0.15
        # cash_conv = 12 * (1 - 0.075/0.15) / 20 = 12 * 0.5 / 20 = 0.30
        card2 = ScreeningCard(ticker="TEST", fiscal_year=2024, current_price=20.0, market_cap=200.0)
        res2 = engine._compute_metrics(
            card=card2, all_years_df=df, fy=2024, row=row,
            price=20.0, mktcap=200.0, shares=10.0,
            wacc=0.10, terminal_growth=0.075
        )
        
        assert math.isclose(res2.get("EBITDA Cash Conversion").value, 0.30, rel_tol=1e-9)

    def test_leverage_ratios(self, make_calc_input):
        engine = ScreeningEngine(verbose=False)
        df, row = create_mock_data()
        
        card = ScreeningCard(ticker="TEST", fiscal_year=2024, current_price=20.0, market_cap=200.0)
        res = engine._compute_metrics(
            card=card, all_years_df=df, fy=2024, row=row,
            price=20.0, mktcap=200.0, shares=10.0,
            wacc=0.10, terminal_growth=0.03
        )
        
        # Debt-to-Equity: LongTermDebt = 20.0, TotalEquity = 65.0 -> 20.0 / 65.0 = 0.30769
        de_ratio = res.get("Debt-to-Equity").value
        assert math.isclose(de_ratio, 20.0 / 65.0, rel_tol=1e-5)
        
        # Net Debt / EBITDA: Net Debt = 20.0, EBITDA = 20.0 -> 1.0
        nd_ebitda = res.get("Net Debt / EBITDA").value
        assert math.isclose(nd_ebitda, 1.0, rel_tol=1e-9)
        
    def test_signal_thresholds(self, make_calc_input):
        engine = ScreeningEngine(verbose=False)
        df, row = create_mock_data()
        
        # Tweak price to exactly hit the Graham PE threshold
        # PE = 15.0 -> Price = 15.0 * 1.331 = 19.965
        card = ScreeningCard(ticker="TEST", fiscal_year=2024, current_price=19.965, market_cap=199.65)
        res = engine._compute_metrics(
            card=card, all_years_df=df, fy=2024, row=row,
            price=19.965, mktcap=199.65, shares=10.0,
            wacc=0.10, terminal_growth=0.03
        )
        
        pe_metric = res.get("P/E Ratio")
        assert math.isclose(pe_metric.value, 15.0, rel_tol=1e-5)
        assert pe_metric.signal == PASS  # ≤15 is a PASS
        
        # Tweak price to slightly exceed 15 (e.g. 15.5)
        card = ScreeningCard(ticker="TEST", fiscal_year=2024, current_price=20.6305, market_cap=206.305)
        res = engine._compute_metrics(
            card=card, all_years_df=df, fy=2024, row=row,
            price=20.6305, mktcap=206.305, shares=10.0,
            wacc=0.10, terminal_growth=0.03
        )
        
        pe_metric = res.get("P/E Ratio")
        assert math.isclose(pe_metric.value, 15.5, rel_tol=1e-5)
        assert pe_metric.signal == FLAG  # >15 but <=30 is a FLAG

    def test_cagr_robust_logic(self, make_calc_input):
        from aletheia.tools.screening_ratios import _robust_cagr
        # Series with identical 3, 5, 7, 10 yr CAGRs
        series = pd.Series([100.0, 110.0, 121.0, 133.1, 146.41]) # 4 years of 10% growth
        cagr = _robust_cagr(series)
        # Should be exactly 10%
        assert math.isclose(cagr, 0.10, rel_tol=1e-5)
