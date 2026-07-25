import json

cik = "0000753308"
with open(f"valuation_data/raw/sec/companyfacts/CIK{cik}.json") as f:
    data = json.load(f)

facts = data['facts']['us-gaap']

def get_val(tag):
    if tag not in facts: return None
    units = facts[tag]['units'].get('USD', [])
    for u in reversed(units):
        if u.get('fy') == 2024 and u.get('fp') == 'FY':
            return u['val']
    return None

results = []
for tag in ['Assets', 'AssetsNet']:
    v = get_val(tag)
    if v is not None: results.append(('TotalAssets', tag, v))

for tag in ['Liabilities']:
    v = get_val(tag)
    if v is not None: results.append(('TotalLiabilities', tag, v))

for tag in ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest', 'MinorityInterest']:
    v = get_val(tag)
    if v is not None: results.append(('StockholdersEquity/MinorityInterest', tag, v))

print(f"{'Field':<40} {'Tag':<70} {'Value'}")
print("-" * 130)
for r in results:
    print(f"{r[0]:<40} {r[1]:<70} {r[2]:,}")
