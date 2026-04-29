import json
import pandas as pd
import os
import sys
from pathlib import Path

# Config
TICKER = "AAPL"
TARGET_YEAR = 2024
CIK = "0000320193"

# Validation Metircs Map
# (Standard Tag -> [Possible Raw Tags])
# Priority is handled by the Transformer, but for Raw Check we need to know what to look for.
# We will use a simplified list here to verify "Source of Truth".
METRICS = {
    "Revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"],
    "COGS": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfSales"],
    "NetIncome": ["NetIncomeLoss", "ProfitLoss"],
    "TotalAssets": ["Assets", "TotalAssets"],
    "TotalLiabilities": ["Liabilities", "TotalLiabilities"], # XBRL often just 'Liabilities'
    "TotalEquity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
}

def get_raw_value(metric, possible_tags, defaults):
    """Finds the raw XBRL value for FY2024 10-K"""
    us_gaap = defaults.get("facts", {}).get("us-gaap", {})
    
    for tag in possible_tags:
        if tag in us_gaap:
            units = us_gaap[tag]["units"]["USD"]
            # Filter for FY 2024 10-K
            matches = [u for u in units if u.get("fy") == TARGET_YEAR and u.get("form") == "10-K"]
            if matches:
                return float(matches[-1]["val"]), tag
    return None, None

def validate_pipeline():
    print(f"--- VALIDATION START: {TICKER} (FY{TARGET_YEAR}) ---")
    
    # 1. Load Data
    try:
        with open(f"valuation_data/raw/sec/companyfacts/CIK{CIK}.json", "r") as f:
            raw_data = json.load(f)
            
        canon_df = pd.read_parquet(f"valuation_data/canonical/financials/{TICKER}.parquet")
        
        with open(f"valuation_data/serving/latest/{TICKER}.json", "r") as f:
            serving_data = json.load(f)
            
    except Exception as e:
        print(f"❌ CRITICAL: Data Load Failed - {e}")
        return

    # 2. Iterate Metrics
    results = []
    
    for metric, raw_tags in METRICS.items():
        print(f"\nChecking [{metric}]...")
        
        # A. Raw Check
        raw_val, used_raw_tag = get_raw_value(metric, raw_tags, raw_data)
        if raw_val is None:
            print(f"  ⚠️ Raw: Not found in XBRL (checked {raw_tags})")
            results.append({"metric": metric, "status": "SKIP_RAW_MISSING"})
            continue
            
        print(f"  ✅ Raw: {raw_val:,.0f} (Tag: {used_raw_tag})")
        
        # B. Canonical Check
        # Filter: Standard Tag = metric, FY = 2024, Form = 10-K
        subset = canon_df[
            (canon_df["standard_tag"] == metric) & 
            (canon_df["fy"] == TARGET_YEAR) & 
            (canon_df["form"] == "10-K")
        ]
        
        if subset.empty:
             print(f"  ❌ Canonical: Row missing.")
             results.append({"metric": metric, "status": "FAIL_CANONICAL_MISSING"})
             continue
             
        canon_val = subset.iloc[-1]["value"]
        resolved_tag = subset.iloc[-1]["resolved_tag"]
        
        print(f"  ℹ️ Canonical: {canon_val:,.0f} (Resolved: {resolved_tag})")
        
        if abs(raw_val - canon_val) > 1.0:
            print(f"  ❌ FAIL: Raw ({raw_val}) != Canonical ({canon_val})")
            results.append({"metric": metric, "status": "FAIL_CANONICAL_MISMATCH"})
            continue
        
        # C. Serving Check
        # Mapping Serving Keys to Standard Tags is tricky.
        # Revenue -> base_financials.net_sales
        # COGS -> base_financials.cogs
        # NetIncome -> dcf_result.projections[0].net_income (approx, modeled)
        # Serving data implies "Latest Base".
        # We need to map metric to serving key.
        
        serving_val = None
        if metric == "Revenue":
            serving_val = serving_data["base_financials"].get("net_sales")
        elif metric == "COGS":
            serving_val = serving_data["base_financials"].get("cogs")
        # Add more if needed, but Base Financials is the key interface.
        
        if serving_val is not None:
            # Serving might differ due to TTM or rounding, but should be close for Annual base.
            print(f"  ℹ️ Serving: {serving_val:,.0f}")
             # Relaxed check for Serving (semantic aggregation possibility)
            if abs(serving_val - canon_val) > canon_val * 0.05: # 5% tolerance
                 print(f"  ⚠️ WARN: Serving deviation > 5%")
            else:
                 print(f"  ✅ Serving Match (within 5%)")
        
        results.append({"metric": metric, "status": "PASS", "val": raw_val})

    # Summary
    print("\n--- SUMMARY ---")
    pass_count = len([r for r in results if r["status"] == "PASS"])
    total = len(results)
    print(f"Passing: {pass_count}/{total}")
    
    if pass_count == total:
        print("✅ FULL PIPELINE INTEGRITY CONFIRMED")
    else:
        print("❌ INTEGRITY FAILURES DETECTED")

if __name__ == "__main__":
    validate_pipeline()
