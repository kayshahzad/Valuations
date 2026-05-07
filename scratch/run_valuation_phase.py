"""
Phase 4.5 Workflow Note:
This script no longer ingests data on the fly. It relies entirely on the DuckDB `company_records`
table populated by `aletheia/data/ingest_universe.py`. 
If you add a new ticker to `UNIVERSE`, you MUST run `python3 aletheia/data/ingest_universe.py` 
first before running this valuation script.
"""

import pandas as pd
from aletheia.data.database import InvestmentDatabase
from aletheia.tools.dcf_engine import DCFEngine
import traceback

from config.ticker_classification import UNIVERSE
TICKERS = sorted(list(UNIVERSE.keys()))

db = InvestmentDatabase(verbose=False)
dcf_engine = DCFEngine(verbose=False)

results = []

for ticker in TICKERS:
    try:
        # Check if we have data for the ticker in DB
        latest_df = db.get_latest(ticker)
        if latest_df.empty:
            results.append({"Ticker": ticker, "Status": "Failed", "Error": "No data in DB. Run ingest_universe.py first."})
            continue
            
        latest_year = int(latest_df["fiscal_year"].max())
        
        # 1. Prepare CalculationInput
        classification = UNIVERSE[ticker]
        from config.known_issues import KNOWN_ISSUES
        from config.valuation_defaults import LIFECYCLE_PROFILES
        from aletheia.contracts.interfaces import CalculationInput, ValuationProfile
        
        known_issues = KNOWN_ISSUES.get(ticker, [])
        lifecycle = classification.lifecycle if classification else "mature"
        profile_cfg = LIFECYCLE_PROFILES.get(lifecycle, LIFECYCLE_PROFILES["mature"])
        
        vp = ValuationProfile(
            growth_rate=profile_cfg.growth_rate,
            terminal_growth=profile_cfg.terminal_growth,
            forecast_years=profile_cfg.forecast_years,
            terminal_margin_decay=profile_cfg.terminal_margin_decay,
        )
        
        calc_input = CalculationInput(df=latest_df, classification=classification, known_issues=known_issues, valuation_profile=vp)
        
        # 2. Run DCF calculation
        dcf_result = dcf_engine.run(calc_input)
        
        if dcf_result.errors:
            results.append({"Ticker": ticker, "Status": "Failed", "Error": str(dcf_result.errors[0])})
            continue
            
        base_iv = dcf_result.intrinsic_per_share(dcf_result.base.enterprise_value, dcf_result.net_debt) if dcf_result.base else None
        bull_iv = dcf_result.intrinsic_per_share(dcf_result.bull.enterprise_value, dcf_result.net_debt) if dcf_result.bull else None
        bear_iv = dcf_result.intrinsic_per_share(dcf_result.bear.enterprise_value, dcf_result.net_debt) if dcf_result.bear else None
        
        upside = (base_iv - dcf_result.current_price) / dcf_result.current_price if (base_iv and dcf_result.current_price) else None
        
        lifecycle = dcf_result.base.metadata.get("lifecycle_category", "N/A") if dcf_result.base else "N/A"
        growth_def = f"{dcf_result.base.metadata.get('growth_default', 0):.1%}" if dcf_result.base and "growth_default" in dcf_result.base.metadata else "N/A"
        hist_cagr = f"{dcf_result.base.metadata.get('hist_cagr', 0):.1%}" if dcf_result.base and "hist_cagr" in dcf_result.base.metadata else "N/A"
        forecast_yrs = str(dcf_result.base.metadata.get("forecast_years", "N/A")) if dcf_result.base else "N/A"
        
        results.append({
            "Ticker": ticker,
            "Status": "Success",
            "Price": f"${dcf_result.current_price:.2f}" if dcf_result.current_price else "N/A",
            "Base IV": f"${base_iv:.2f}" if base_iv else "N/A",
            "Upside": f"{upside:+.1%}" if upside is not None else "N/A",
            "WACC": f"{dcf_result.wacc:.1%}" if dcf_result.wacc else "N/A",
            "Lifecycle": lifecycle,
            "Hist CAGR": hist_cagr,
            "Growth Def": growth_def,
            "Forecast Yrs": forecast_yrs
        })
        
    except Exception as e:
        results.append({"Ticker": ticker, "Status": "Error", "Error": str(e)})

# Output table
df = pd.DataFrame(results)

# Fill missing columns for cleanly printing
for col in ["Price", "Base IV", "Bull IV", "Bear IV", "Upside", "WACC", "ROIC", "EV/EBITDA", "Lifecycle", "Growth Def", "Hist CAGR", "Forecast Yrs", "Error"]:
    if col not in df.columns:
        df[col] = "N/A"

# Reorder columns
cols = ["Ticker", "Status", "Lifecycle", "Growth Def", "Hist CAGR", "Forecast Yrs", "Price", "Base IV", "Upside", "WACC", "Error"]
df = df[[c for c in cols if c in df.columns]]

markdown = df.to_markdown(index=False)

with open("/Users/kashifshahzad/.gemini/antigravity/brain/426b6bda-c00f-4f62-bd7a-2230751ceedc/valuation_results.md", "w") as f:
    f.write("# Valuation Results (Internal Calculations Only)\n\n")
    f.write(markdown)

print("Report generated successfully.")
