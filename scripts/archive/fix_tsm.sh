echo "Ingesting TSM..."
python3 -m aletheia.data.edgar_client --ticker TSM 2>&1

echo "Running full pipeline for TSM..."
PYTHONPATH=. python3 main.py --ticker TSM 2>&1 | tail -5

echo "Validating outputs..."
python3 -c "
import json
from pathlib import Path

ticker = 'TSM'
path = Path(f'valuation_data/serving/latest/{ticker}_report.json')
if not path.exists():
    print(f'{ticker}: no report')
else:
    r  = json.loads(path.read_text())
    it = r['4_valuation_synthesis']['investment_thesis']
    ps = it.get('pillar_scores', {})
    ft = r.get('2_financial_translation', {})
    cf = ft.get('clean_financials', {}) or {}
    rat = ft.get('ratios', {}) or {}
    p2 = r['4_valuation_synthesis'].get('phase2_valuation', {})
    dcf3 = p2.get('three_scenario_dcf', {})
    rdcf = p2.get('reverse_dcf', {})

    print(f'=== {ticker} ===')
    print(f'Stage:        {ps.get(\"lifecycle_stage\")}')
    print(f'Revenue:      \${cf.get(\"revenue_bn\", 0):.1f}B USD')
    print(f'Gross Margin: {rat.get(\"gross_margin_pct\", 0):.1f}%')
    print(f'ROIC:         {(rat.get(\"roic\") or 0)*100:.1f}%')
    print(f'Base IV:      \${dcf3.get(\"base\",{}).get(\"intrinsic_per_share\",0):.2f}')
    print(f'Base MoS:     {dcf3.get(\"base\",{}).get(\"margin_of_safety\",0):+.1%}')
    print(f'Hist CAGR:    {(rdcf.get(\"historical_cagr\") or 0):.1%}')
    print(f'Impl CAGR:    {(rdcf.get(\"implied_cagr_10y\") or 0):.1%}')
    print(f'Pillars:      P1={ps.get(\"p1_moat\")} P2={ps.get(\"p2_health\")} P3={ps.get(\"p3_tailwind\")} P4={ps.get(\"p4_mos\")} P5={ps.get(\"p5_leadership\")}')
    print(f'Total:        {ps.get(\"capped_total\")}/25  Tier: {ps.get(\"position_tier\")}')
    print()
    for c in it.get('constitution_checks', []):
        print(f'  {c}')
    print()
" 2>&1
