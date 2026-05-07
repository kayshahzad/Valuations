from aletheia.data.database import InvestmentDatabase
db = InvestmentDatabase()
df = db.query("SELECT derived_InvestedCapital, derived_ROIC FROM company_records WHERE ticker='ORCL' ORDER BY fiscal_year DESC LIMIT 1")
print(df)
