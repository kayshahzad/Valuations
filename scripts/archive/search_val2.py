import json

with open("valuation_data/raw/sec/companyfacts/CIK0000002488.json") as f:
    raw = json.load(f)

us_gaap = raw.get("facts", {}).get("us-gaap", {})
tag = "RevenueFromContractWithCustomerExcludingAssessedTax"
units = us_gaap[tag].get("units", {}).get("USD", [])
for u in units:
    if u.get("fy") == 2018 and u.get("form") == "10-K":
        print(u)
