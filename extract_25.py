import duckdb
import pandas as pd
import os

tickers = ['MSFT', 'AAPL', 'NVDA', 'GOOGL', 'META', 'AMZN', 'TSLA', 'SMCI', 'LLY', 'COST', 'NEE', 'CAT', 'JPM', 'BRK-B', 'V', 'WMT', 'UNH', 'ABT', 'AMD', 'ASML', 'TSM', 'QCOM', 'ORCL', 'TXN', 'CNC']
tickers_str = ", ".join([f"'{t}'" for t in tickers])

con = duckdb.connect("/Users/kashifshahzad/Documents/Projects/Valuations/valuation_data/database/investment.duckdb")

query = f"""
WITH latest_records AS (
    SELECT ticker, MAX(fiscal_year) as latest_fy
    FROM company_records
    WHERE ticker IN ({tickers_str})
    GROUP BY ticker
)
SELECT 
    c.ticker,
    c.raw_Revenue,
    c.raw_NetIncome,
    c.raw_TotalAssets,
    c.raw_TotalEquity,
    c.raw_LongTermDebt,
    c.raw_Cash,
    c.raw_Depreciation,
    c.raw_RnD,
    c.raw_COGS,
    c.raw_OperatingIncome,
    c.raw_TotalLiabilities
FROM company_records c
JOIN latest_records l ON c.ticker = l.ticker AND c.fiscal_year = l.latest_fy
ORDER BY c.ticker
"""

df = con.execute(query).df()

# Format as markdown table
md = df.to_markdown(index=False)
out_path = "/Users/kashifshahzad/.gemini/antigravity/brain/426b6bda-c00f-4f62-bd7a-2230751ceedc/structured_fields_25.md"

with open(out_path, "w") as f:
    f.write("# Structured Fields for 25 Core Universe Companies\n\n")
    f.write(md)
    f.write("\n\n*Note: raw_capex, raw_EBIT, and raw_LiabilitiesCurrent are omitted as they are uniformly null due to legacy mappings.*")

print("Done")
