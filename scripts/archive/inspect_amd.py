import json
with open("valuation_data/raw/sec/companyfacts/CIK0000002488.json") as f:
    raw = json.load(f)

us_gaap = raw.get("facts", {}).get("us-gaap", {})

for kw in ["OperatingIncome", "EBIT", "IncomeLoss", "Expense", "Cost"]:
    for tag in us_gaap.keys():
        if kw.lower() in tag.lower():
            units = us_gaap[tag].get("units", {}).get("USD", [])
            annual = [u for u in units if u.get("form") == "10-K"]
            if len(annual) > 5:
                print(f"{tag}: {len(annual)} 10-K filings")

