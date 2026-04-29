import requests, json

def check_ticker(ticker, cik):
    print(f'=== {ticker} (CIK {cik}) ===')
    url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
    r = requests.get(url, headers={'User-Agent': 'Aletheia Research admin@example.com'}, timeout=30)
    if r.status_code != 200:
        print(f'  HTTP {r.status_code} — run from local machine')
        return
    data = r.json()
    us_gaap = data.get('facts', {}).get('us-gaap', {})
    ifrs    = data.get('facts', {}).get('ifrs-full', {})
    print(f'  US-GAAP tags: {len(us_gaap)}')
    print(f'  IFRS tags:    {len(ifrs)}')
    print(f'  Forms filed:  ', end='')
    forms = set()
    for tag_data in list(us_gaap.values())[:20]:
        for v in tag_data.get('units', {}).get('USD', []):
            forms.add(v.get('form', ''))
    print(forms)
    # Revenue
    rev_tags = [k for k in us_gaap if any(w in k.lower() for w in ['revenue','sales'])]
    print(f'  Revenue tags: {rev_tags[:5]}')

check_ticker('ASML', '0000937966')
