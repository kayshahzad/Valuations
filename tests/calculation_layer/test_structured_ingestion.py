import pytest
from aletheia.data.cleaning_engine import CleaningEngine
from aletheia.data.ingestion_validator import IngestionValidator
from aletheia.tools.dcf_engine import DCFEngine

CORE_UNIVERSE = [
    'MSFT', 'AAPL', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'CNC', 'AMD', 'ASML', 
    'TSM', 'UNH', 'LLY', 'ABT', 'V', 'COST', 'WMT', 'NEE', 'CAT', 'JPM', 'BRK-B', 
    'SMCI', 'QCOM', 'ORCL', 'TXN'
]

def get_all_required_fields():
    """Dynamically aggregate REQUIRED_CLEAN_FIELDS from all calc tools."""
    fields = set()
    # DCFEngine
    if hasattr(DCFEngine, "REQUIRED_CLEAN_FIELDS"):
        fields.update(DCFEngine.REQUIRED_CLEAN_FIELDS)
    
    # Can dynamically import other engines here in the future
    return fields

@pytest.mark.parametrize("ticker", CORE_UNIVERSE)
def test_structured_fields_populated(ticker):
    """
    Integration test asserting that the cleaning pipeline properly populates 
    the core structured fields required by the calc engines and passes the ingestion validator.
    """
    engine = CleaningEngine(verbose=False)
    
    # Run the pipeline for FY2024 (a reliable recent year, TSM is 2024 as well now)
    try:
        record = engine.clean(ticker, 2024)
    except Exception as e:
        pytest.fail(f"CleaningEngine failed to process {ticker} FY2024: {str(e)}")

    # 1. Run the IngestionValidator (evaluates ABSOLUTE, RELATIVE, KNOWN_ISSUES)
    val_result = IngestionValidator.validate(record)
    
    # Extract failure messages if any
    if not val_result.is_valid:
        failures_str = "\\n".join([f"{f.field}: {f.message}" for f in val_result.failures])
        pytest.fail(f"Validation failed for {ticker}:\\n{failures_str}")

    # 2. Dynamically check that all REQUIRED_CLEAN_FIELDS are populated
    # (IngestionValidator handles the bypasses for banks/exemptions)
    required_fields = get_all_required_fields()
    
    # We only assert fields that aren't explicitly bypassed for this sector by the validator
    from config.industry_routing import get_industry
    sector = get_industry(ticker).lower()
    
    for field in required_fields:
        # Check if the field is bypassed for this sector
        is_bypassed = False
        for contract in IngestionValidator.RELATIVE_CONTRACTS + IngestionValidator.ABSOLUTE_CONTRACTS:
            if contract.field == field and sector in contract.bypass_sectors:
                is_bypassed = True
                break
                
        # Also check KNOWN_ISSUES
        from aletheia.data.ingestion_validator import KNOWN_ISSUES
        from datetime import datetime, date
        if ticker in KNOWN_ISSUES:
            issues = KNOWN_ISSUES[ticker]
            # Skip if ANY active known issue exists for this ticker
            has_active_issue = any(datetime.now().date() <= date.fromisoformat(str(i.expires_after)) for i in issues if hasattr(i, 'expires_after'))
            if has_active_issue:
                continue

        if not is_bypassed:
            val, prov = record.get_with_provenance(field)
            assert prov != "missing", f"Required calc field '{field}' missing for {ticker}."

