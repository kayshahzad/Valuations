# tests/calculation_layer/conftest.py

import pytest

UNIVERSE = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA",
            "LLY", "ABT", "UNH", "V", "JPM", "BRK-B",
            "COST", "WMT", "NEE", "CAT", "SMCI", "QCOM", "ASML", "TSM",
            "ORCL", "TXN", "AMD", "CNC"]

REFERENCE_TICKERS = ["MSFT", "LLY", "JPM", "CAT", "NVDA"]

@pytest.fixture
def synthetic_dcf_inputs():
    """Standard synthetic inputs for DCF identity tests."""
    return {
        "revenue": 1000.0,
        "ebit_margin": 0.20,
        "tax_rate": 0.21,
        "growth_rate": 0.05,
        "terminal_growth": 0.025,
        "wacc": 0.10,
        "capex_pct": 0.05,
        "da_pct": 0.05,
        "nwc_pct": 0.03,
    }

@pytest.fixture
def lly_state_synthetic():
    """Synthetic state dict resembling LLY's phase 2 valuation output."""
    return {
        "phase2_valuation": {
            "three_scenario_dcf": {
                "base": {"margin_of_safety": -0.252, "intrinsic_value_per_share": 638.0}
            },
            "multiple_decomposition": {
                "roic": 0.33, "value_creation": "creating", "premium_pct": 1.85
            },
            "wacc": 0.105,
            "reverse_dcf": {"implied_cagr_10y": 0.18, "historical_cagr": 0.087},
        },
        "forensic_report": {"moat_score": 8.5, "operating_leverage_score": 6.0},
        "value_chain_report": {"strategic_leverage": 7.0},
        "strategic_context_report": {
            "applies_cyclical_haircut": False,
            "is_cyclical_peak": False,
            "revenue_z_score": 0.5,
        },
    }

@pytest.fixture
def jpm_state_synthetic():
    """Synthetic state for JPM (financial sector)."""
    # ...

# ... more fixtures

def _make_calc_input(ticker: str, df=None):
    from aletheia.contracts.interfaces import CalculationInput, ValuationProfile
    from config.ticker_classification import UNIVERSE
    from config.known_issues import KNOWN_ISSUES
    from config.valuation_defaults import LIFECYCLE_PROFILES
    from config.lifecycle_thresholds import STAGE_THRESHOLDS, Stage
    from aletheia.data.database import InvestmentDatabase
    
    classification = UNIVERSE.get(ticker)
    issues = KNOWN_ISSUES.get(ticker, [])
    lifecycle = classification.lifecycle if classification else "mature"
    profile_cfg = LIFECYCLE_PROFILES.get(lifecycle, LIFECYCLE_PROFILES["mature"])
    vp = ValuationProfile(
        growth_rate=profile_cfg.growth_rate,
        terminal_growth=profile_cfg.terminal_growth,
        forecast_years=profile_cfg.forecast_years,
        terminal_margin_decay=profile_cfg.terminal_margin_decay,
    )
    
    try:
        stage = Stage(lifecycle)
        thresholds = STAGE_THRESHOLDS[stage]
    except ValueError:
        thresholds = STAGE_THRESHOLDS[Stage.GROWTH_COMPOUNDER]
    
    if df is None:
        db = InvestmentDatabase(verbose=False)
        df = db.get_latest(ticker)
        db.close()
        
    return CalculationInput(
        df=df,
        classification=classification,
        known_issues=issues,
        valuation_profile=vp,
        lifecycle_thresholds=thresholds
    )

@pytest.fixture
def make_calc_input():
    return _make_calc_input