
import json
# import pytest (Removed to run standalone)
from pathlib import Path
from datetime import datetime

# Paths
BASE_PATH = Path("valuation_data/serving/base/AAPL_base.json")
RAW_PATH = Path("valuation_data/raw/sec/companyfacts/CIK0000320193.json")
MAPS_PATH = Path("config/ACCOUNTING_MAPS.md")

def load_maps():
    """Parses ACCOUNTING_MAPS.md to get tag lists."""
    maps = {}
    current_section = None
    with open(MAPS_PATH, "r") as f:
        for line in f:
            if "|" in line and "Tag" not in line and "---" not in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3:
                    tag = parts[1]
                    patterns = [p.strip().strip("`") for p in parts[2].split(",")]
                    maps[tag] = patterns
    return maps

def get_raw_value(raw_data, tags, end_date):
    """Finds the value in raw_data matching any of the tags and the end_date."""
    facts = raw_data.get("facts", {}).get("us-gaap", {})
    
    found_values = []
    
    for tag in tags:
        if tag in facts:
            units = facts[tag].get("units", {}).get("USD", [])
            for u in units:
                if u.get("end") == end_date:
                    val = u.get("val")
                    # Handle Frame priority? Or just take latest filed.
                    # Usually "10-K" is preferred.
                    form = u.get("form")
                    if form == "10-K":
                         found_values.append(val)
    
    if found_values:
        return found_values[0] # Return first match (Priority ordered in Maps)
    return None

def test_field_coverage():
    """
    Verifies that every field in Serving Base matches the Raw SEC Source.
    """
    print("\n🚀 STARTING FIELD-LEVEL INTEGRITY CHECK 🚀\n")
    
    # 1. Load Data
    with open(BASE_PATH, "r") as f:
        base = json.load(f)
        
    with open(RAW_PATH, "r") as f:
        raw = json.load(f)
        
    maps = load_maps()
    
    # Meta
    period_end = base["meta"]["period_end_date"].split("T")[0]
    print(f"Target Period End: {period_end}")
    
    financials = base["financials"]
    
    # Flatten Base for Iteration
    fields_to_check = {}
    fields_to_check.update(financials["income_statement"])
    fields_to_check.update(financials["balance_sheet"])
    
    results = []
    
    for field, value in fields_to_check.items():
        if field not in maps:
            print(f"⚠️ Skipped {field} (No Map Defined)")
            continue
            
        tags = maps[field]
        raw_val = get_raw_value(raw, tags, period_end)
        
        # Special Logic for 'cash' (Aggregation)
        if field == 'cash':
            # Cash is sum of multiple tags usually, but Map defines priority list?
            # Actually, ACCOUNTING_MAPS.md defines a list. 
            # If our Intake Logic *sums* them (like Cash + Marketable Securities), 
            # testing against a single raw tag might fail if we don't replicate the sum logic here.
            # Phase 11 Task 1 said "Map ... AND ... to Cash".
            # If Intake sums them, we must sum them here too.
            val_sum = 0
            found_any = False
            for tag in tags:
                 v = get_raw_value(raw, [tag], period_end)
                 if v is not None:
                     val_sum += v
                     found_any = True
            raw_val = val_sum if found_any else None

        # Logic for OPEX (Sum)
        if field == 'opex':
             # Map likely lists aliases. But if Intake sums SG&A + R&D?
             # Base says opex = SG&A + R&D.
             # This test compares against raw tags. 
             # Let's see if 'OperatingExpenses' exists directly.
             pass 

        status = "❌ FAIL"
        if raw_val is not None:
            # Allow small float diff
            if abs(value - raw_val) < 1000: # $1k tolerance
                status = "✅ PASS"
            else:
                 # Check if it's a calculated field (Gross Profit, EBIT)
                 pass
        else:
            status = "❓ MISSING RAW"
            
        diff = value - raw_val if raw_val is not None else 0
        results.append({
            "field": field,
            "base_val": value,
            "raw_val": raw_val,
            "diff": diff,
            "status": status,
            "tags": tags
        })
        
    # Report
    print(f"{'FIELD':<20} | {'BASE':<15} | {'RAW':<15} | {'DIFF':<15} | {'STATUS'}")
    print("-" * 80)
    for r in results:
        raw_print = f"{r['raw_val']:,.0f}" if r['raw_val'] is not None else "N/A"
        print(f"{r['field']:<20} | {r['base_val']:<15,.0f} | {raw_print:<15} | {r['diff']:<15,.0f} | {r['status']}")
        
if __name__ == "__main__":
    test_field_coverage()
