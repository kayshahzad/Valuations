import requests, json

def check_ticker(ticker, cik):
    print(f'=== {ticker} (CIK {cik}) ===')
    url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
    r = requests.get(url, headers={'User-Agent': 'Aletheia Research admin@example.com'}, timeout=30)
    data = r.json()
    us_gaap = data.get('facts', {}).get('us-gaap', {})
    
    units_found = set()
    for tag, tag_data in us_gaap.items():
        units_found.update(tag_data.get('units', {}).keys())
        
    print(f'Currencies/Units found: {units_found}')
    
    rev_tags = [k for k in us_gaap if 'revenue' in k.lower()]
    print(f'Revenue tags: {rev_tags}')

check_ticker('ASML', '0000937966')
