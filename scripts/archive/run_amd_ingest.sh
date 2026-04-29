PYTHONPATH=. python3 -c "
from aletheia.data.edgar_client import EdgarClient
from aletheia.data.cleaning_engine import CleaningEngine
from aletheia.data.database import InvestmentDatabase

print('Fetching AMD from SEC EDGAR...')
client = EdgarClient()
facts  = client.get_company_facts('AMD')

if not facts:
    print('ERROR: Could not fetch AMD facts from EDGAR')
else:
    us_gaap = facts.get('facts', {}).get('us-gaap', {})
    revenue_tags = [k for k in us_gaap.keys()
                    if any(w in k.lower() for w in ['revenue','sales','netsales'])]
    print(f'Found {len(revenue_tags)} revenue-related tags:')
    for tag in sorted(revenue_tags)[:10]:
        vals = us_gaap[tag].get('units',{}).get('USD',[])
        annual = [v for v in vals if v.get('form')=='10-K']
        if annual:
            print(f'  {tag}: \${annual[-1].get(\"val\",0)/1e9:.1f}B ({annual[-1].get(\"end\")})')
" 2>&1

PYTHONPATH=. python3 main.py --ticker AMD 2>&1 | tail -10
