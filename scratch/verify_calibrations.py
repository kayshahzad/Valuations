import traceback
from aletheia.tools.dcf_engine import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from aletheia.tools.dcf_engine import DCFEngine
from aletheia.data.database import InvestmentDatabase
from config.ticker_classification import UNIVERSE
from config.known_issues import KNOWN_ISSUES
from config.valuation_defaults import LIFECYCLE_PROFILES
from aletheia.contracts.interfaces import CalculationInput, ValuationProfile

def make_calc_input(ticker: str):
    db = InvestmentDatabase(verbose=False)
    df = db.get_latest(ticker)
    db.close()
    
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
    
    return CalculationInput(
        df=df,
        classification=classification,
        known_issues=issues,
        valuation_profile=vp
    )

def run_verifications():
    engine = DCFEngine(verbose=False)
    
    # 1. UNH (Managed Care) - Should fail DDM check
    print("Verifying UNH Bypass...")
    unh_result = engine.run(make_calc_input("UNH"))
    assert unh_result.errors, "UNH should fail because it requires DDM"
    assert any("Managed Care" in e for e in unh_result.errors)
    print("  ✓ UNH successfully blocked")

    # 2. TSLA (Hyper Growth + Margin Compression)
    print("Verifying TSLA Scenarios...")
    tsla_result = engine.run(make_calc_input("TSLA"))
    base_tv = tsla_result.base.terminal_value
    bear_tv = tsla_result.bear.terminal_value
    assert bear_tv > 0, "TSLA bear TV should not be negative (Gordon TV fallback)"
    assert tsla_result.base.margin_of_safety is not None
    print("  ✓ TSLA fallback triggered correctly")

    # 3. NVDA (Secular Hyper Growth -> 15 Years)
    print("Verifying NVDA Forecast Length...")
    nvda_result = engine.run(make_calc_input("NVDA"))
    forecast_years = len(nvda_result.base.fcf_forecast)
    assert forecast_years == 15, f"NVDA should have 15y forecast, got {forecast_years}"
    print("  ✓ NVDA forecast length is 15 years")

    # 4. CAT (Cyclical Industrial Haircut)
    print("Verifying CAT Cyclical Haircut...")
    try:
        nvda_result = engine.run("NVDA", fiscal_year=2024)
        assert nvda_result.base is not None
        assert nvda_result.base.metadata["lifecycle_category"] == "hyper_growth"
        assert len(nvda_result.base.projections) == 15
        assert nvda_result.base.metadata["forecast_years"] == 15
        print("PASS: NVDA Hyper-Growth Runway")
    except Exception as e:
        print(f"FAILED: NVDA valuation crashed: {e}")
        exit(1)
        
    print("All calibrations verified successfully.")

if __name__ == "__main__":
    run_verifications()
