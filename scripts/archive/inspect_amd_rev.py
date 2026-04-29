import json

with open("valuation_data/raw/sec/companyfacts/CIK0000002488.json") as f:
    raw = json.load(f)

us_gaap = raw.get("facts", {}).get("us-gaap", {})

revenue_keywords = ["Revenue", "Sales"]
for tag, concept in us_gaap.items():
    if any(kw.lower() in tag.lower() for kw in revenue_keywords):
        units = concept.get("units", {}).get("USD", [])
        annual = [u for u in units if u.get("form") == "10-K"]
        if len(annual) > 5:
            # Let's print the most recent 3 years for this tag
            annual_sorted = sorted(annual, key=lambda x: x.get("fy", 0))
            recent = annual_sorted[-3:]
            vals = ", ".join([f"FY{u.get('fy')}: ${u.get('val',0)/1e9:.2f}B" for u in recent])
            print(f"{tag} ({len(annual)}): {vals}")
