import json

with open("valuation_data/raw/sec/companyfacts/CIK0000002488.json") as f:
    raw = json.load(f)

us_gaap = raw.get("facts", {}).get("us-gaap", {})

def print_tag_years(tag_name):
    print(f"\n--- {tag_name} ---")
    if tag_name not in us_gaap:
        print("Not found")
        return
    units = us_gaap[tag_name].get("units", {}).get("USD", [])
    annual = [u for u in units if u.get("form") == "10-K"]
    
    # group by year and take the latest filing date for each year
    by_year = {}
    for u in annual:
        fy = u.get("fy")
        if fy:
            if fy not in by_year or u.get("end") > by_year[fy].get("end"):
                by_year[fy] = u
                
    for fy in sorted(by_year.keys()):
        print(f"  FY{fy}: ${by_year[fy]['val']/1e9:.2f}B")

print_tag_years("RevenueFromContractWithCustomerExcludingAssessedTax")
print_tag_years("SalesRevenueNet")
print_tag_years("SalesRevenueGoodsNet")
print_tag_years("Revenues")
