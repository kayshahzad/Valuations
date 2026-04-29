"""
aletheia/data/data_quality_validator.py

Executes the Data Validation Framework (Layers 1-3) against a parsed record.
Reads rules from config/validation_rules.py.
"""

from config.industry_routing import get_industry
from config.validation_rules import (
    check_layer1_presence,
    check_layer2_field_level,
    check_layer3_identities
)

class DataQualityValidator:
    def __init__(self):
        pass
        
    def validate(self, ticker: str, fiscal_year: int, record: dict) -> dict:
        """
        Validates the record (which is the enriched wide_dict from TagResolver).
        Returns a structured validation result.
        """
        industry = get_industry(ticker)
        
        missing_fields = check_layer1_presence(record, industry)
        field_violations = check_layer2_field_level(record, industry)
        identity_violations = check_layer3_identities(record, industry)
        
        all_failures = missing_fields + field_violations + identity_violations
        
        return {
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "industry": industry,
            "passed": len(all_failures) == 0,
            "layer1_missing": missing_fields,
            "layer2_violations": field_violations,
            "layer3_violations": identity_violations,
            "all_failures": all_failures
        }
