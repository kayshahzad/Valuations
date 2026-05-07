import duckdb
import pandas as pd

con = duckdb.connect("valuation_data/database/investment.duckdb")

columns = [
    "raw_Revenue",
    "raw_NetIncome",
    "raw_TotalAssets",
    "raw_TotalEquity",
    "raw_LongTermDebt",
    "raw_Cash",
    "raw_Depreciation",
    "raw_capex",
    "raw_RnD",
    "raw_COGS",
    "raw_EBIT",
    "raw_OperatingIncome",
    "raw_TotalLiabilities",
    "raw_LiabilitiesCurrent"
]

# Get the latest year for each ticker
query = """
WITH latest_records AS (
    SELECT ticker, MAX(fiscal_year) as latest_fy
    FROM company_records
    GROUP BY ticker
)
SELECT c.*
FROM company_records c
JOIN latest_records l ON c.ticker = l.ticker AND c.fiscal_year = l.latest_fy
"""

try:
    df = con.execute(query).df()
    total_tickers = len(df)
    print(f"Total Tickers in DB: {total_tickers}")
    print("-" * 40)
    print(f"{'Field':<25} | {'Coverage':<10} | {'Nulls':<5}")
    print("-" * 40)

    for col in columns:
        if col in df.columns:
            non_nulls = df[col].notna().sum()
            coverage = (non_nulls / total_tickers) * 100
            print(f"{col:<25} | {coverage:>8.1f}% | {total_tickers - non_nulls:>5}")
        else:
            print(f"{col:<25} | {'MISSING COL':>8} | {'N/A':>5}")
except Exception as e:
    print(e)
