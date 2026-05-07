import pandas as pd
import duckdb

conn = duckdb.connect("investment.duckdb")
df = conn.execute("SELECT ticker, fiscal_year, raw_data FROM company_records WHERE ticker='ORCL' ORDER BY fiscal_year DESC LIMIT 1").fetchdf()

import json
raw = json.loads(df.iloc[0]['raw_data'])
print(f"ORCL TotalEquity: {raw.get('TotalEquity')}")
print(f"ORCL EquityAttributableToOwnersOfParent: {raw.get('EquityAttributableToOwnersOfParent')}")
