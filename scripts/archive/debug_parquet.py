
import pandas as pd
from pathlib import Path

path = Path("valuation_data/canonical/financials/AAPL.parquet")
if path.exists():
    df = pd.read_parquet(path)
    print(f"Rows: {len(df)}")
    print("\nUnknown Tags:")
    print(df["standard_tag"].unique())
    print("\nFirst 20 Rows:")
    print(df[["period_end_date", "standard_tag", "value", "fy"]].head(20))
    
    # Check specifically for Cash
    print("\nChecking for 'cash':")
    print(df[df["standard_tag"] == "cash"])
else:
    print("File not found.")
