import json

with open("audits/trace_NVDA_1770089178.json", "r") as f:
    data = json.load(f)

# Find Lead step
for step in data:
    if step["agent"] == "Lead":
        fin_data = step["inputs"]["financial_data"]
        stmt = fin_data["statements"]["income_statement"]
        parsed = json.loads(stmt)
        # Get latest year
        latest = parsed[sorted(parsed.keys())[-1]]
        print("Income Statement Keys:")
        for k, v in latest.items():
            print(f"  {k}: {v}")
