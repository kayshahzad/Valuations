
import json
from pathlib import Path

path = Path("valuation_data/raw/sec/companyfacts/CIK0001071739.json")
with open(path, "r") as f:
    data = json.load(f)

us_gaap = data["facts"]["us-gaap"]
candidates = [
    "CostOfGoodsAndServicesSold",
    "CostOfServices",
    "PolicyholderBenefitsAndClaimsIncurredHealthCare",
    "PolicyholderBenefitsAndClaimsIncurredNet"
]

print("--- LATEST VALUES ---")
for tag in candidates:
    if tag in us_gaap:
        units = us_gaap[tag]["units"].get("USD", [])
        # Get latest 3
        latest = sorted(units, key=lambda x: x["end"], reverse=True)[:3]
        print(f"\nTAG: {tag}")
        for l in latest:
             print(f"  {l['end']} (FY{l.get('fy')}): {l['val']:,.0f}")
    else:
        print(f"\nTAG: {tag} (NOT FOUND)")
