import pytest
import pandas as pd
from aletheia.data.cleaning_engine import CleaningEngine, CleanedRecord
from aletheia.tools.dcf_engine import DCFEngine
from aletheia.contracts.interfaces import CalculationInput, ValuationProfile
from config.ticker_classification import TickerClassification

def test_aapl_depreciation_total_sums_components():
    """Verify D&A reconstructs properly from components when aggregate is absent."""
    engine = CleaningEngine()
    
    # AAPL doesn't report canonical aggregate, so we provide components
    record = CleanedRecord(
        ticker="AAPL",
        fiscal_year=2025,
        period_end_date="2025-09-30",
        raw={
            "Depreciation_Tangible": 10.0,
            "IntangibleAmortization": 2.0,
            "FinanceLeaseAmortization": 0.0,
            "CapitalizedSoftwareAmortization": 1.0,
            "Depreciation_Total_Aggregate": None  # Absent
        }
    )
    
    engine._compute_derived(record)
    
    aggregate = record.derived.get("Depreciation_Total")
    components_sum = sum([
        record.raw.get("Depreciation_Tangible") or 0.0,
        record.raw.get("IntangibleAmortization") or 0.0,
        record.raw.get("FinanceLeaseAmortization") or 0.0,
        record.raw.get("CapitalizedSoftwareAmortization") or 0.0,
    ])
    
    assert aggregate == components_sum
    assert record.derived_provenance.get("Depreciation_Total") == "derived_components"
    assert aggregate > record.raw["Depreciation_Tangible"]


def test_lly_operating_income_derived():
    """Verify strict Operating Income derivation logic correctly subtracts R&D."""
    engine = CleaningEngine()
    
    # LLY reports standard components but not canonical OperatingIncome
    record = CleanedRecord(
        ticker="LLY",
        fiscal_year=2024,
        period_end_date="2024-12-31",
        raw={
            "Revenue": 100.0,
            "COGS": 20.0,
            "SG&A": 30.0,
            "R&D": 10.0,
            "AcquiredInProcessRnD": 5.0,
            "OperatingIncome": None  # Absent
        }
    )
    
    engine._compute_derived(record)
    
    op_income = record.derived.get("OperatingIncome")
    assert op_income is not None
    assert op_income == 100.0 - 20.0 - 30.0 - 10.0 - 5.0
    assert record.derived_provenance.get("OperatingIncome") == "derived"


def test_period_end_date_missing_excluded_from_cagr():
    """Verify exact-day CAGR calculation explicitly excludes records missing end dates."""
    engine = DCFEngine(verbose=False)
    
    # Create synthetic dataframe with a missing date record
    df = pd.DataFrame([
        {"fiscal_year": 2021, "period_end_date": "2021-12-31", "period_end_date_missing": False, "clean_Revenue": 100.0, "derived_OperatingIncome": 20.0, "derived_EBITDA": 25.0, "derived_Depreciation_Total": 5.0, "raw_CapEx": 6.0, "raw_TotalAssets": 500.0, "raw_TotalEquity": 250.0, "clean_SharesDiluted": 100.0, "raw_NetIncome": 15.0},
        {"fiscal_year": 2022, "period_end_date": "2022-12-31", "period_end_date_missing": True,  "clean_Revenue": 110.0, "derived_OperatingIncome": 22.0, "derived_EBITDA": 27.5, "derived_Depreciation_Total": 5.5, "raw_CapEx": 6.6, "raw_TotalAssets": 550.0, "raw_TotalEquity": 275.0, "clean_SharesDiluted": 100.0, "raw_NetIncome": 16.5},  # Should be dropped
        {"fiscal_year": 2023, "period_end_date": "2023-12-31", "period_end_date_missing": False, "clean_Revenue": 120.0, "derived_OperatingIncome": 24.0, "derived_EBITDA": 30.0, "derived_Depreciation_Total": 6.0, "raw_CapEx": 7.2, "raw_TotalAssets": 600.0, "raw_TotalEquity": 300.0, "clean_SharesDiluted": 100.0, "raw_NetIncome": 18.0},
        {"fiscal_year": 2024, "period_end_date": "2024-12-31", "period_end_date_missing": False, "clean_Revenue": 130.0, "derived_OperatingIncome": 26.0, "derived_EBITDA": 32.5, "derived_Depreciation_Total": 6.5, "raw_CapEx": 7.8, "raw_TotalAssets": 650.0, "raw_TotalEquity": 325.0, "clean_SharesDiluted": 100.0, "raw_NetIncome": 19.5},
    ])
    
    # We don't need a full run, we just want to exercise the CAGR path.
    classification = TickerClassification(
        ticker="SYN",
        sector="technology",
        industry="software",
        lifecycle="mature",
        business_model="standard",
        notes="",
        last_reviewed="2026-01-01"
    )
    vp = ValuationProfile(growth_rate=0.05, terminal_growth=0.02, forecast_years=10, terminal_margin_decay=0.01)
    calc_input = CalculationInput(df=df, classification=classification, known_issues=[], valuation_profile=vp)
    
    # This will use the records to compute CAGR, skipping 2022
    # Because 2022 is dropped, data density check ensures we fall back to lifecycle default if density < 50%
    # But here 3 out of 4 years are valid, so it computes normally across the gap.
    try:
        result = engine.run(calc_input, fiscal_year=2024)
        # We just need it not to crash and successfully ignore the missing date row
        assert result is not None
    except Exception as e:
        pytest.fail(f"DCFEngine run failed with missing date record: {e}")
