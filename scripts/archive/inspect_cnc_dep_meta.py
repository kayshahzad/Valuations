
import json
from pathlib import Path

path = Path("valuation_data/raw/sec/companyfacts/CIK0001071739.json")
with open(path, "r") as f:
    data = json.load(f)

tags = ["DepreciationDepletionAndAmortization", "Depreciation", "AmortizationOfIntangibleAssets"]
us_gaap = data["facts"]["us-gaap"]

print("--- DEPRECIATION TAGS ---")
for t in tags:
    if t in us_gaap:
        units = us_gaap[t]["units"].get("USD", [])
        # Get latest
        latest = sorted(units, key=lambda x: x["end"], reverse=True)[:3]
        print(f"\nTAG: {t}")
        for l in latest:
             print(f"  {l['end']} (FY{l.get('fy')}) Form:{l.get('form')} Val:{l.get('val'):,.0f}")
    else:
        print(f"\nTAG: {t} NOT FOUND")
