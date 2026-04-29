PYTHONPATH=. python3 main.py --ticker AMD 2>&1 | tail -5

python3 -c "
import json
from pathlib import Path

r  = json.loads(Path('valuation_data/serving/latest/AMD_report.json').read_text())
it = r['4_valuation_synthesis']['investment_thesis']
ps = it.get('pillar_scores', {})
p2 = r['4_valuation_synthesis']['phase2_valuation']
dcf3 = p2.get('three_scenario_dcf', {})

print(f'AMD — Final Validated Output')
print(f'Stage:   {ps.get(\"lifecycle_stage\")}')
print(f'Pillars: P1={ps.get(\"p1_moat\")} P2={ps.get(\"p2_health\")} P3={ps.get(\"p3_tailwind\")} P4={ps.get(\"p4_mos\")} P5={ps.get(\"p5_leadership\")}')
print(f'Total:   {ps.get(\"capped_total\")}/25')
print(f'Tier:    {ps.get(\"position_tier\")}')
print(f'Base IV: \${dcf3.get(\"base\",{}).get(\"intrinsic_per_share\",0):.2f}')
" 2>&1
