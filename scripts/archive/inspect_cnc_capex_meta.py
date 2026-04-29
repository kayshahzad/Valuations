
import json
from pathlib import Path

path = Path("valuation_data/raw/sec/companyfacts/CIK0001071739.json")
with open(path, "r") as f:
    data = json.load(f)

tag = "PaymentsToAcquirePropertyPlantAndEquipment"
units = data["facts"]["us-gaap"][tag]["units"].get("USD", [])

print(f"--- META FOR {tag} ---")
for u in units:
    print(f"Date: {u.get('end')} FY:{u.get('fy')} Form:{u.get('form')} Val:{u.get('val')}")
