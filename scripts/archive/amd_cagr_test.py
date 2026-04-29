from aletheia.data.database import InvestmentDatabase
import numpy as np

db = InvestmentDatabase(verbose=False)
df = db.get_latest('AMD').sort_values('fiscal_year')
db.close()

print('AMD revenue history:')
for _, row in df.iterrows():
    rev = (row.get('clean_Revenue') or 0) / 1e9
    fy  = int(row.get('fiscal_year', 0))
    print(f'  FY{fy}: \${rev:.2f}B')

print()
rev_series = df['clean_Revenue'].dropna()
rev_now = float(rev_series.iloc[-1])
print(f'CAGR lookbacks from \${rev_now/1e9:.1f}B:')
for y in [3, 5, 7, 10]:
    if len(rev_series) >= y:
        r0 = float(rev_series.iloc[-y])
        if r0 > 0:
            cagr = (rev_now / r0) ** (1/y) - 1
            print(f'  {y}Y CAGR: {cagr:.1%}  (from \${r0/1e9:.1f}B)')

# Show what the robust median produces
candidates = []
for y in [3, 5, 7, 10]:
    if len(rev_series) >= y:
        r0 = float(rev_series.iloc[-y])
        if r0 > 0:
            candidates.append((rev_now / r0) ** (1/y) - 1)
if len(candidates) >= 3:
    s = sorted(candidates)
    trimmed = s[1:-1]
    print(f'Robust median (trimmed): {np.median(trimmed):.1%}')
    print(f'All candidates: {[f"{c:.1%}" for c in candidates]}')

