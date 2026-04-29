
import pandas as pd
df = pd.read_parquet("valuation_data/canonical/financials/CNC.parquet")
print("--- UNIQUE TAGS ---")
print(df["standard_tag"].unique())

print("\n--- CAPEX ROWS ---")
capex = df[df["standard_tag"] == "capex"]
if capex.empty:
    print("NO CAPEX FOUND")
else:
    print(capex[["period_end_date", "value", "resolved_tag"]])
