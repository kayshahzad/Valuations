
import json
from pathlib import Path

FACT_PATH = Path("valuation_data/raw/sec/companyfacts/CIK0000320193.json")

def find_latest_fact_accession():
    print("Loading Company Facts...")
    with open(FACT_PATH, "r") as f:
        facts = json.load(f)
    
    us_gaap = facts["facts"]["us-gaap"]
    
    # Check both old and new Revenue tags
    tags_to_check = [
        "Revenues", 
        "RevenueFromContractWithCustomerExcludingAssessedTax", 
        "SalesRevenueNet",
        "SalesRevenueServicesNet"
    ]
    
    for tag in tags_to_check:
        if tag in us_gaap:
            units = us_gaap[tag]["units"].get("USD", [])
            if units:
                # Sort by end date
                units.sort(key=lambda x: x.get("end", ""), reverse=True)
                latest = units[0]
                print(f"\nLatest '{tag}' Entry:")
                print(f"Accession: {latest.get('accn')}")
                print(f"End Date: {latest.get('end')}")
                print(f"Value: {latest.get('val'):,.0f}")
                print(f"Filed: {latest.get('filed')}")
            else:
                print(f"\n'{tag}': Found but no USD units")
        else:
            print(f"\n'{tag}': Tag NOT FOUND")

if __name__ == "__main__":
    find_latest_fact_accession()
