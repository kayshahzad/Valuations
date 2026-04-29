python3 -c "
import json
from pathlib import Path

path = Path('valuation_data/serving/latest/AMD_report.json')
if not path.exists():
    print('No AMD report found')
else:
    r  = json.loads(path.read_text())
    it = r['4_valuation_synthesis']['investment_thesis']
    ps = it.get('pillar_scores', {})
    ft = r.get('2_financial_translation', {})
    cf = ft.get('clean_financials', {}) or {}
    rat = ft.get('ratios', {}) or {}
    p2  = r['4_valuation_synthesis'].get('phase2_valuation', {})
    dcf3 = p2.get('three_scenario_dcf', {})
    rdcf = p2.get('reverse_dcf', {})
    md   = p2.get('multiple_decomposition', {})

    print('=== AMD PIPELINE OUTPUT ===')
    print(f'Stage:      {ps.get(\"lifecycle_stage\", \"?\")}')
    print(f'Conviction: {it.get(\"conviction_score\")}')
    print(f'Tier:       {ps.get(\"position_tier\")}')
    print()
    print('--- Fundamentals ---')
    print(f'Revenue:    \${(cf.get(\"revenue_bn\") or 0):.1f}B')
    print(f'EBITDA:     \${(cf.get(\"ebitda_bn\") or 0):.1f}B')
    print(f'FCF:        \${(cf.get(\"fcf_bn\") or 0):.1f}B')
    print(f'FCF Margin: {(rat.get(\"fcf_margin_pct\") or 0):.1f}%')
    print(f'Gross Margin:{(rat.get(\"gross_margin_pct\") or 0):.1f}%')
    print(f'ROIC:       {(rat.get(\"roic\") or 0)*100:.1f}%')
    print(f'Data Quality:{(cf.get(\"data_quality\") or 0):.2f}')
    print()
    print('--- Valuation ---')
    base = dcf3.get('base', {})
    print(f'Bear IV:    \${(dcf3.get(\"bear\",{}).get(\"intrinsic_per_share\") or 0):.2f}')
    print(f'Base IV:    \${(base.get(\"intrinsic_per_share\") or 0):.2f}')
    print(f'Bull IV:    \${(dcf3.get(\"bull\",{}).get(\"intrinsic_per_share\") or 0):.2f}')
    print(f'Base MoS:   {(base.get(\"margin_of_safety\") or 0):+.1%}')
    print(f'WACC:       {(p2.get(\"wacc\") or 0):.1%}')
    print(f'Implied CAGR:{(rdcf.get(\"implied_cagr_10y\") or 0):.1%}')
    print(f'Hist CAGR:  {(rdcf.get(\"historical_cagr\") or 0):.1%}')
    print(f'EV/EBITDA:  {(md.get(\"market_ev_ebitda\") or 0):.1f}x')
    print(f'Justified:  {(md.get(\"justified_ev_ebitda\") or 0):.1f}x')
    print()
    print('--- Pillars ---')
    for p in ['p1_moat','p2_health','p3_tailwind','p4_mos','p5_leadership']:
        print(f'{p}: {ps.get(p)}')
    print(f'Total: {ps.get(\"capped_total\")}')
    print()
    print('--- Constitution ---')
    for c in it.get('constitution_checks', []):
        print(f'  {c}')
    print()
    print('--- Narrative (first 300 chars) ---')
    print(str(it.get('narrative',''))[:300])
" 2>&1
