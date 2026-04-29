"""
Regression test suite for the Canonical Data Layer.

Ensures that the 25-ticker universe maintains >= 23 passing calculation validations,
locking in the behavior fixed in Phase 3/4.
"""
import pytest
import json
from aletheia.data.validate_universe import validate_ticker

def test_universe_regression():
    """
    Validates that at least 23 out of 25 tickers pass the calculation validation
    against universe_reference.json. This acts as a regression safety net against
    tag mapping drift or validation rule breakage.
    """
    tickers = [
        "MSFT", "AAPL", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "SMCI", 
        "LLY", "COST", "NEE", "CAT", "JPM", "BRK-B", "V", "WMT", "UNH", 
        "ABT", "AMD", "ASML", "TSM", "QCOM", "ORCL", "TXN", "CNC"
    ]
    
    with open("config/universe_reference.json", "r") as f:
        reference = json.load(f)
    
    passed_count = 0
    failures = {}
    
    for ticker in tickers:
        result = validate_ticker(ticker, reference)
        if result["status"] == "pass":
            passed_count += 1
        else:
            failures[ticker] = result.get("failures", [])
            
    print(f"\nPassed: {passed_count}/25")
    for t, fails in failures.items():
        print(f"{t}: {fails}")
        
    assert passed_count >= 23, f"Regression failure! Only {passed_count} tickers passed. Expected >= 23. Failures: {failures}"
