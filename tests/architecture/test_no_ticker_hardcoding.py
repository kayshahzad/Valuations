import pytest
from pathlib import Path
from glob import glob

# This serves as an architectural lock to prevent tickers from entering the calc layer.
def test_no_ticker_hardcoding_in_calc_layer():
    """Calc layer must contain zero ticker-specific logic."""
    from config.ticker_classification import UNIVERSE
    
    # Check all python files in the tools directory
    tool_files = glob("aletheia/tools/**/*.py", recursive=True)
    
    violations = []
    
    for path in tool_files:
        source = Path(path).read_text()
        for ticker in UNIVERSE.keys():
            # Basic string matching for exact ticker strings (e.g. "MSFT" or 'MSFT')
            if f'"{ticker}"' in source or f"'{ticker}'" in source:
                violations.append(f"Ticker {ticker} hardcoded in {path}")
                
    assert not violations, "Architectural violation: Ticker-specific overrides found in Calc layer:\n" + "\n".join(violations)

def test_no_config_imports_in_calc_layer():
    """Calc layer must not import from config at runtime. Configuration must be injected."""
    from glob import glob
    from pathlib import Path
    
    tool_files = glob("aletheia/tools/**/*.py", recursive=True)
    violations = []
    for path in tool_files:
        source = Path(path).read_text()
        for line in source.split("\n"):
            line = line.strip()
            if line.startswith("from config.") or line.startswith("import config."):
                violations.append(f"Calc layer imports config in {path}: {line}")
                
    assert not violations, "Architectural violation: config imports found in Calc layer:\n" + "\n".join(violations)

def test_calc_tools_accept_synthetic_classifications():
    from aletheia.tools.dcf_engine import DCFEngine
    from aletheia.contracts.interfaces import CalculationInput
    from config.ticker_classification import TickerClassification
    import pandas as pd
    
    # Create a synthetic dataframe representing a valid ingested company record.
    # Field names match what DCFEngine reads via get_with_provenance:
    #   - get("clean_X") for X in {Revenue, NOPAT, SharesDiluted}
    #   - get_with_provenance(X) checks raw_X first, then derived_X
    synthetic_df = pd.DataFrame([{
        "fiscal_year": 2024,
        "clean_Revenue": 100e9,
        "raw_OperatingIncome": 20e9,
        "derived_EBITDA": 25e9,
        "clean_NOPAT": 15e9,
        "derived_ROIC": 0.20,
        "derived_FCF": 10e9,
        "derived_NetDebt": 5e9,
        "derived_InvestedCapital": 50e9,
        "raw_LongTermDebt": 10e9,
        "raw_TotalEquity": 40e9,
        "raw_Depreciation_Total_Aggregate": 5e9,
        "derived_Depreciation_Total": 5e9,
        "raw_CapEx": 6e9,
        "clean_SharesDiluted": 1e9,
        "raw_NetIncome": 12e9,
        "period_end_date": "2024-12-31",
    }])
    
    lifecycles = [
        "secular_hyper_growth", "hyper_growth", "high_growth_compounder",
        "growth_compounder", "mature", "cyclical_industrial"
    ]
    
    engine = DCFEngine(verbose=False)
    
    for lc in lifecycles:
        classification = TickerClassification(
            ticker="SYNTH", sector="Synthetic", industry="Synthetic",
            lifecycle=lc, business_model="fcff_compatible", notes="",
            last_reviewed="2026-05-01"
        )
        from aletheia.contracts.interfaces import ValuationProfile
        vp = ValuationProfile(0.1, 0.02, 10, 0.2)
        calc_input = CalculationInput(df=synthetic_df, classification=classification, known_issues=[], valuation_profile=vp)
        
        result = engine.run(calc_input)
        assert not result.errors, f"Synthetic {lc} DCF failed: {result.errors}"
        assert result.base is not None, f"Base scenario missing for {lc}"
