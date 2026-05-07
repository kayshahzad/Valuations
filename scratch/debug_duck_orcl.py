import duckdb
conn = duckdb.connect("investment.duckdb")
df = conn.execute("SELECT derived_InvestedCapital, derived_ROIC FROM company_records WHERE ticker='ORCL' ORDER BY fiscal_year DESC LIMIT 1").fetchdf()
print(df)
