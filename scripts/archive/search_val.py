import json

with open("valuation_data/raw/sec/companyfacts/CIK0000002488.json") as f:
    raw = json.load(f)

us_gaap = raw.get("facts", {}).get("us-gaap", {})

target = 1.42 * 1e9
target_2 = 1.48 * 1e9

for tag, concept in us_gaap.items():
    units = concept.get("units", {}).get("USD", [])
    for u in units:
        if u.get("fy") in [2017, 2018] and u.get("form") == "10-K":
            val = float(u.get("val", 0))
            if abs(val - target) < 1e7 or abs(val - target_2) < 1e7:
                print(f"Match: {tag} FY{u.get('fy')} = ${val/1e9:.2f}B")

