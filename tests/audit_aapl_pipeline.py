import json
import pandas as pd
import os
from pathlib import Path

# Config
TICKER = "AAPL"
CIK = "0000320193" # AAPL CIK
TARGET_YEAR = 2024
TARGET_TAG = "Revenue" # Standard Tag
RAW_CONCEPT = "RevenueFromContractWithCustomerExcludingAssessedTax" # Likely XBRL tag for AAPL
# AAPL uses 'RevenueFromContractWithCustomerExcludingAssessedTax' or 'SalesRevenueNet' usually.
# Let's check 'Revenues' or similar if that fails. 
# Actually, let's use the ACCOUNTING_MAPS knowledge or just check standard tag in Canonical.

def check_raw():
    print("\n--- 1. Raw Data Check (XBRL) ---")
    path = f"valuation_data/raw/sec/companyfacts/CIK{CIK}.json"
    try:
        with open(path, "r") as f:
            data = json.load(f)
        
        # AAPL specific raw tag for Revenue is often 'RevenueFromContractWithCustomerExcludingAssessedTax' or 'SalesRevenueNet'
        # Let's look for 'RevenueFromContractWithCustomerExcludingAssessedTax' which is common for recent AAPL 10-Ks
        gaap = data["facts"]["us-gaap"]
        
        # Try finding the value for FY2024 10-K
        # We need a robust search because tag names vary.
        # Known AAPL Revenue 2024 is approx $391B.
        
        possible_tags = ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"]
        found_val = None
        
        for tag in possible_tags:
            if tag in gaap:
                units = gaap[tag]["units"]["USD"]
                matches = []
                for u in units:
                    if u.get("fy") == TARGET_YEAR and u.get("form") == "10-K":
                        matches.append(u)
                
                if matches:
                    found_val = matches[-1]["val"]
                    print(f"✅ Found Raw {tag} (FY{TARGET_YEAR} 10-K): ${found_val:,.0f}")
                    return found_val
        
        if found_val is None:
            print(f"❌ Could not find Raw Revenue for FY{TARGET_YEAR}")
            return None
            
    except Exception as e:
        print(f"❌ Raw Check Failed: {e}")
        return None

def check_canonical(raw_val):
    print("\n--- 2. Canonical Data Check (Parquet) ---")
    path = f"valuation_data/canonical/financials/{TICKER}.parquet"
    try:
        df = pd.read_parquet(path)
        # Filter for Revenue, 2024, 10-K
        # Note: Canonical stores 'period_end_date'. AAPL 2024 FY end is 2024-09-28.
        
        mask = (df["standard_tag"] == "Revenue") & (df["fy"] == TARGET_YEAR) & (df["form"] == "10-K")
        row = df[mask]
        
        if row.empty:
            print(f"❌ Canonical row for Revenue FY{TARGET_YEAR} 10-K not found")
            return False
            
        canon_val = row.iloc[0]["value"]
        print(f"Canonical Value: ${canon_val:,.0f}")
        
        if raw_val is not None:
            if abs(canon_val - raw_val) < 1.0: # Floating point tolerance
                print(f"✅ Integrity Verified: Canonical matches Raw exactly.")
                return True
            else:
                print(f"❌ MISMATCH: Raw ({raw_val}) != Canonical ({canon_val})")
                return False
        return True # validation passed vs itself
        
    except Exception as e:
        print(f"❌ Canonical Check Failed: {e}")
        return False

def check_serving():
    print("\n--- 3. Serving Data Check (JSON) ---")
    path = f"valuation_data/serving/latest/{TICKER}.json"
    try:
        with open(path, "r") as f:
            data = json.load(f)
            
        base_rev = data.get("base_financials", {}).get("net_sales")
        print(f"Serving Base Revenue: ${base_rev:,.0f}")
        
        if base_rev > 0:
             print("✅ Serving Data is populated.")
        else:
             print("⚠️ Serving Revenue is 0 or missing.")
             
    except Exception as e:
        print(f"❌ Serving Check Failed: {e}")

def check_traceability():
    print("\n--- 4. Traceability Check ---")
    log_dir = "valuation_data/logs"
    if os.path.exists(log_dir):
        files = os.listdir(log_dir)
        json_logs = [f for f in files if f.endswith(".json")]
        if json_logs:
            print(f"✅ Found {len(json_logs)} trace logs.")
            print(f"Latest: {sorted(json_logs)[-1]}")
        else:
            print("❌ No trace logs found.")
    else:
        print("❌ Log directory missing.")

if __name__ == "__main__":
    print(f"AUDIT START: {TICKER} Pipeline Integrity\n")
    
    raw_val = check_raw()
    
    metric_ok = check_canonical(raw_val)
    
    check_serving()
    
    check_traceability()
    
    print("\nAUDIT COMPLETE.")
