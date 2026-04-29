import pandas as pd
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)

path = "valuation_data/canonical/financials/AAPL.parquet"
try:
    df = pd.read_parquet(path)
    print("\n--- Schema ---")
    print(df.dtypes)
    
    print("\n--- Sample Data (Recent) ---")
    print(df.sort_values("period_end_date").tail(20)[["period_end_date", "standard_tag", "value", "resolved_tag"]])
    
    print("\n--- Value Checks (Revenue) ---")
    revs = df[df["standard_tag"] == "Revenue"].sort_values("period_end_date")
    print(revs.tail(5)[["period_end_date", "standard_tag", "value", "resolved_tag"]])
    
except Exception as e:
    print(f"FAILED: {e}")
