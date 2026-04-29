PYTHONPATH=. python3 main.py --ticker AAPL 2>&1 | tail -5
PYTHONPATH=. python3 main.py --ticker TSLA 2>&1 | tail -5
PYTHONPATH=. python3 main.py --ticker CNC  2>&1 | tail -5

python3 -c "
import json
from pathlib import Path

for ticker in ['AAPL','TSLA','CNC']:
    r  = json.loads(Path(f'valuation_data/serving/latest/{ticker}_report.json').read_text())
    it = r['4_valuation_synthesis']['investment_thesis']
    ps = it.get('pillar_scores', {})
    stage = ps.get('lifecycle_stage', 'unknown')
    print(f'{ticker}: stage={stage} | P2={ps.get(\"p2_health\")} P3={ps.get(\"p3_tailwind\")} P4={ps.get(\"p4_mos\")} | total={ps.get(\"capped_total\")}')
    for p in ps.get('p2_reasons', []) + ps.get('p3_reasons', []):
        print(f'  → {p}')
" 2>&1
