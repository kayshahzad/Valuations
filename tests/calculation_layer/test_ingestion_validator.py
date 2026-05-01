"""
tests/calculation_layer/test_ingestion_validator.py

Unit tests for the Phase 2.2 code-based ingestion validation layer.
Asserts that the IngestionValidator exports its contracts, strictly enforces
them by returning ValidationResult, and properly leverages config-driven
archetypes and zero-value preservation.
"""

import pytest
from aletheia.data.ingestion_validator import IngestionValidator
from aletheia.data.cleaning_engine import CleanedRecord, CleaningEngine

def create_valid_record(ticker="AAPL") -> CleanedRecord:
    """Helper to create a minimally valid record using constructor kwargs."""
    return CleanedRecord(
        ticker=ticker, 
        fiscal_year=2023, 
        period_end_date="2023-12-31",
        raw={
            "Revenue": 1000.0,
            "TotalAssets": 5000.0,
            "TotalEquity": 2000.0,
            "TotalLiabilities": 3000.0,
            "Cash": 1000.0,
            "OperatingIncome": 200.0,
            "Depreciation": 50.0,
            "CapEx": 100.0,
        },
        derived={
            "ROIC": 0.15,
            "EBIT_Margin_Pct": 20.0,
            "EBITDA_Margin_Pct": 25.0,
            "EBITDA": 250.0,
        }
    )

class TestIngestionValidator:

    def test_exports_contracts(self, make_calc_input):
        """Validator must export strongly typed contracts."""
        abs_contracts = IngestionValidator.get_absolute_contracts()
        rel_contracts = IngestionValidator.get_relative_contracts()
        ident_contracts = IngestionValidator.get_identity_contracts()
        
        assert len(abs_contracts) > 0
        assert len(rel_contracts) > 0
        assert len(ident_contracts) > 0
        assert any(c.field == "raw_Revenue" for c in abs_contracts)
        assert any(c.field == "Depreciation" for c in rel_contracts)
        assert any(c.name == "ebitda_ge_ebit" for c in ident_contracts)

    def test_valid_record_passes(self, make_calc_input):
        record = create_valid_record()
        result = IngestionValidator.validate(record)
        assert result.is_valid is True
        assert len(result.failures) == 0

    def test_valid_zero_does_not_fall_through(self, make_calc_input):
        """Ensure get_val preserves 0.0 and doesn't silently fall back."""
        record = create_valid_record()
        record.raw["Depreciation"] = 0.0 # Valid if low D&A, but let's check it doesn't pull from derived
        record.derived["Depreciation"] = 500.0 # This would violate bounds if it fell through
        
        # At 0.0, pct = 0.0%, which is < 0.5% min_pct, so it will actually fail validation.
        # This proves 0.0 was used, instead of 500.0 (which would be 50%, also failing, but let's make 500.0 valid)
        record.derived["Depreciation"] = 100.0 # 10% (Valid)
        
        result = IngestionValidator.validate(record)
        # 0.0 is < 0.5% (the min for standard), so it should fail relative bounds.
        # If it fell through to 100.0, it would pass.
        assert result.is_valid is False
        assert result.failures[0].field == "Depreciation"
        assert result.failures[0].actual == 0.0

    def test_missing_required_raw_field(self, make_calc_input):
        record = create_valid_record()
        del record.raw["Revenue"]
        
        result = IngestionValidator.validate(record)
        assert result.is_valid is False
        assert len(result.failures) == 1
        
        failure = result.failures[0]
        assert failure.field == "raw_Revenue"
        assert failure.reason == "missing_field"

    def test_absolute_bounds_violation_revenue(self, make_calc_input):
        record = create_valid_record()
        record.raw["Revenue"] = 0.0
        
        result = IngestionValidator.validate(record)
        assert result.is_valid is False
        
        failure = result.failures[0]
        assert failure.field == "raw_Revenue"
        assert failure.reason == "bounds_violation"
        assert failure.actual == 0.0
        assert "violates absolute bounds" in failure.message

    def test_relative_bounds_violation_da(self, make_calc_input):
        record = create_valid_record()
        record.raw["Depreciation"] = 250.0
        
        result = IngestionValidator.validate(record)
        assert result.is_valid is False
        
        failure = result.failures[0]
        assert failure.field == "Depreciation"
        assert failure.reason == "relative_bounds_violation"
        assert failure.actual == 25.0

    def test_unknown_ticker_uses_default(self, make_calc_input):
        record = create_valid_record(ticker="UNKNOWN_123")
        
        record.raw["Depreciation"] = 200.0
        result = IngestionValidator.validate(record)
        assert result.is_valid is True

        record.raw["Depreciation"] = 250.0
        result = IngestionValidator.validate(record)
        assert result.is_valid is False
        assert result.failures[0].field == "Depreciation"

    def test_archetype_override_da_for_utilities(self, make_calc_input):
        record = create_valid_record(ticker="DUMMY_UTIL") # High DA ticker (utility in industry_routing)
        # D&A is 250 (25%). For utility, max is 40%, so this should pass!
        record.raw["Depreciation"] = 250.0
        
        result = IngestionValidator.validate(record, sector="utility")
        assert result.is_valid is True

        # Now push it above 40% (450 = 45%)
        record.raw["Depreciation"] = 450.0
        result = IngestionValidator.validate(record, sector="utility")
        assert result.is_valid is False
        assert result.failures[0].field == "Depreciation"

    def test_jpm_financial_sector_routing(self, make_calc_input):
        record = create_valid_record(ticker="JPM") # Bank in industry_routing
        record.raw["CapEx"] = 500.0 # 50% of revenue
        
        result = IngestionValidator.validate(record)
        # Should be valid because CapEx checks are bypassed for 'bank'
        assert result.is_valid is True

    def test_accounting_identity_violation(self, make_calc_input):
        record = create_valid_record()
        record.derived["EBITDA_Margin_Pct"] = 15.0
        
        result = IngestionValidator.validate(record)
        assert result.is_valid is False
        
        failure = result.failures[0]
        assert failure.field == "accounting_identity"
        assert failure.reason == "ebitda_ge_ebit"

    @pytest.mark.skip(reason="Phase 2.5 — cross-source check pending")
    def test_cross_source_validation_deferred(self, make_calc_input):
        pass

    def test_integration_with_cleaning_engine(self, tmp_path):
        import pandas as pd
        
        df = pd.DataFrame([
            {"ticker": "AAPL", "fy": 2023, "standard_tag": "Revenue", "value": 1000.0},
            {"ticker": "AAPL", "fy": 2023, "standard_tag": "TotalAssets", "value": 5000.0},
            {"ticker": "AAPL", "fy": 2023, "standard_tag": "TotalEquity", "value": 2000.0},
            {"ticker": "AAPL", "fy": 2023, "standard_tag": "TotalLiabilities", "value": 3000.0},
            {"ticker": "AAPL", "fy": 2023, "standard_tag": "Cash", "value": 1000.0},
            {"ticker": "AAPL", "fy": 2023, "standard_tag": "NetIncome", "value": 150.0},
            {"ticker": "AAPL", "fy": 2023, "standard_tag": "EBIT", "value": 200.0},
            {"ticker": "AAPL", "fy": 2023, "standard_tag": "OperatingIncome", "value": 200.0},
            {"ticker": "AAPL", "fy": 2023, "standard_tag": "Depreciation", "value": 50.0},
            {"ticker": "AAPL", "fy": 2023, "standard_tag": "CapEx", "value": 100.0},
        ])
        
        d = tmp_path / "valuation_data" / "canonical" / "financials"
        d.mkdir(parents=True)
        df.to_parquet(d / "AAPL.parquet")
        
        engine = CleaningEngine(canonical_dir=str(d), verbose=False)
        record = engine.clean("AAPL", 2023)
        
        result = IngestionValidator.validate(record)
        assert result.is_valid is True, f"Engine produced invalid record: {result.failures}"
