# Verify AMD data in DuckDB
python3 -c "
from aletheia.data.database import InvestmentDatabase
import numpy as np

db = InvestmentDatabase(verbose=False)
df = db.get_latest('AMD')
db.close()

if df.empty:
    print('ERROR: No AMD records in DuckDB')
else:
    df = df.sort_values('fiscal_year')
    print(f'AMD records: {len(df)} fiscal years')
    print()
    for _, row in df.tail(5).iterrows():
        rev   = (row.get('clean_Revenue') or 0) / 1e9
        ebitda= (row.get('derived_EBITDA') or 0) / 1e9
        fcf   = (row.get('derived_FCF') or 0) / 1e9
        roic  = (row.get('derived_ROIC') or 0) * 100
        gm    = row.get('derived_GrossMargin_Pct') or 0
        dq    = row.get('overall_quality_score') or 0
        fy    = int(row.get('fiscal_year', 0))
        print(f'FY{fy}: Rev=\${rev:.1f}B  EBITDA=\${ebitda:.1f}B  FCF=\${fcf:.1f}B  ROIC={roic:.1f}%  GM={gm:.1f}%  Quality={dq:.2f}')
" 2>&1

# Then run the full pipeline
PYTHONPATH=. python3 main.py --ticker AMD 2>&1 | tail -15

# Check the output
python3 -c "
import json
from pathlib import Path

path = Path('valuation_data/serving/latest/AMD_report.json')
if not path.exists():
    print('No AMD report')
else:
    r  = json.loads(path.read_text())
    it = r['4_valuation_synthesis']['investment_thesis']
    ps = it.get('pillar_scores', {})
    ft = r['2_financial_translation']
    cf = ft.get('clean_financials', {}) or {}
    p2 = r['4_valuation_synthesis'].get('phase2_valuation', {})

    print(f'=== AMD VALIDATED OUTPUT ===')
    print(f'Stage:      {ps.get(\"lifecycle_stage\")}')
    print(f'Tier:       {ps.get(\"position_tier\")}')
    print(f'Conviction: {it.get(\"conviction_score\")}')
    print()
    print(f'Revenue:    \${cf.get(\"revenue_bn\", 0):.1f}B')
    print(f'EBITDA:     \${cf.get(\"ebitda_bn\", 0):.1f}B')
    print(f'FCF:        \${cf.get(\"fcf_bn\", 0):.1f}B')
    print(f'Gross Margin:{cf.get(\"gross_margin\", 0):.1f}%')
    print(f'Data Quality:{cf.get(\"data_quality\", 0):.2f}')
    print()
    dcf3 = p2.get('three_scenario_dcf', {})
    print(f'Bear IV:    \${dcf3.get(\"bear\",{}).get(\"intrinsic_per_share\", 0):.2f}')
    print(f'Base IV:    \${dcf3.get(\"base\",{}).get(\"intrinsic_per_share\", 0):.2f}')
    print(f'Bull IV:    \${dcf3.get(\"bull\",{}).get(\"intrinsic_per_share\", 0):.2f}')
    print(f'Base MoS:   {dcf3.get(\"base\",{}).get(\"margin_of_safety\", 0):+.1%}')
    print()
    print(f'P1={ps.get(\"p1_moat\")} P2={ps.get(\"p2_health\")} P3={ps.get(\"p3_tailwind\")} P4={ps.get(\"p4_mos\")} P5={ps.get(\"p5_leadership\")} | Total={ps.get(\"capped_total\")}')
    print()
    for c in it.get('constitution_checks', []):
        print(f'  {c}')
" 2>&1
