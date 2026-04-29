import requests, json

def check_ticker(ticker, cik):
    print(f'=== {ticker} (CIK {cik}) ===')
    url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
    r = requests.get(url, headers={'User-Agent': 'Aletheia Research admin@example.com'}, timeout=30)
    data = r.json()
    us_gaap = data.get('facts', {}).get('us-gaap', {})
    
    rev_tag = us_gaap.get("RevenueFromContractWithCustomerExcludingAssessedTax")
    if rev_tag:
        usd_units = rev_tag.get("units", {}).get("USD", [])
        eur_units = rev_tag.get("units", {}).get("EUR", [])
        print(f"USD Rev points: {len(usd_units)}")
        print(f"EUR Rev points: {len(eur_units)}")

check_ticker('ASML', '0000937966')
