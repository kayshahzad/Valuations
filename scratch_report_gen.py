import json

def generate_report():
    lines = open('valuation_data/logs/universe_validation.jsonl').read().strip().split('\n')
    
    # Only take the last 25 lines for the latest run
    lines = lines[-25:]
    
    passed = []
    failed = []
    
    for line in lines:
        if not line: continue
        data = json.loads(line)
        if data['status'] == 'pass':
            passed.append(data)
        else:
            failed.append(data)
            
    report = []
    report.append("# Aletheia Pipeline: Phase 4 Validation Report (Latest Run)\n")
    report.append("## Executive Summary")
    report.append(f"- **Total Tickers Evaluated:** {len(passed) + len(failed)}")
    report.append(f"- **Passed Validation:** {len(passed)}")
    report.append(f"- **Failed Validation:** {len(failed)}")
    report.append(f"- **Overall Pass Rate:** {len(passed) / (len(passed) + len(failed)) * 100:.1f}%\n")
    
    report.append("## Failures")
    if not failed:
        report.append("None! All tickers passed.")
    else:
        for f in failed:
            report.append(f"### {f['ticker']}")
            for fail in f.get('failures', []):
                report.append(f"- ❌ {fail}")
                
    report.append("\n## Passed Tickers Details")
    report.append("| Ticker | Revenue (bn) | EBITDA (bn) | FCF (bn) | Gross Margin | EBIT Margin |")
    report.append("|--------|--------------|-------------|----------|--------------|-------------|")
    
    for p in passed:
        fields = p.get('fields', {})
        rev = fields.get('revenue_bn', {}).get('db_value', 'N/A')
        ebitda = fields.get('ebitda_bn', {}).get('db_value', 'N/A')
        fcf = fields.get('fcf_bn', {}).get('db_value', 'N/A')
        gm = fields.get('gross_margin_pct', {}).get('db_value', 'N/A')
        ebitm = fields.get('ebit_margin_pct', {}).get('db_value', 'N/A')
        
        # format numbers
        def fmt(v):
            if isinstance(v, (int, float)): return f"{v:.1f}"
            return v
            
        report.append(f"| **{p['ticker']}** | {fmt(rev)} | {fmt(ebitda)} | {fmt(fcf)} | {fmt(gm)}% | {fmt(ebitm)}% |")

    with open("validation_results_report.md", "w") as f:
        f.write("\n".join(report))

if __name__ == '__main__':
    generate_report()
