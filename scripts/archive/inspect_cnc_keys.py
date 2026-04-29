
import json
from pathlib import Path

path = Path("valuation_data/raw/sec/companyfacts/CIK0001071739.json")
with open(path, "r") as f:
    data = json.load(f)

us_gaap = data["facts"]["us-gaap"]
keys = sorted(us_gaap.keys())

print(f"Total Keys: {len(keys)}")
print("--- PAYMENT / CAPITAL / PROPERTY KEYS ---")
for k in keys:
    if "Payment" in k or "Capital" in k or "Property" in k or "Equip" in k:
        print(k)
