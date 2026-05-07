from aletheia.data.tag_resolver import TagResolver
import json

r = TagResolver()

def check_keys(ticker, terms):
    print(f"\n--- {ticker} ---")
    facts = r._load_raw_facts(ticker)
    us_gaap = facts.get('facts', {}).get('us-gaap', {})
    found = {term: [] for term in terms}
    
    for k in us_gaap.keys():
        kl = k.lower()
        for term in terms:
            if term in kl:
                found[term].append(k)
                
    for term, keys in found.items():
        print(f"[{term}]:")
        for k in keys:
            print(f"  {k}")
            
tickers = ["MSFT", "NEE", "ABT", "BRK-B", "TSM"]
terms = ["depreciation", "amortization", "capex", "capital", "property", "plant", "equipment", "cash", "liabilities", "operatingincome"]
for t in tickers:
    check_keys(t, terms)
