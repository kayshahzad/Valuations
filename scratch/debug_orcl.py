import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from scratch.verify_phase6 import make_calc_input

calc_input = make_calc_input("ORCL")
df = calc_input.df

latest = df[df["fiscal_year"] == 2024].iloc[0]
print(f"Columns: {list(df.columns)}")
print(f"ORCL 2024 ROIC: {latest.get('ROIC')}")
print(f"ORCL 2024 InvestedCapital: {latest.get('InvestedCapital')}")
print(f"ORCL 2024 TotalDebt: {latest.get('LongTermDebt')}")
print(f"ORCL 2024 Cash: {latest.get('Cash')}")
print(f"ORCL 2024 TotalEquity: {latest.get('TotalEquity')}")

