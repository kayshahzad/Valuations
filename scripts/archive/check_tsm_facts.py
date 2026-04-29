import json

with open("valuation_data/raw/sec/companyfacts/CIK0001046179.json") as f:
    raw = json.load(f)

facts = raw.get("facts", {})
print("Namespaces found in TSM:")
for ns, ns_data in facts.items():
    print(f"  {ns}: {len(ns_data)} tags")

