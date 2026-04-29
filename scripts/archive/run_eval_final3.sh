PYTHONPATH=. python3 main.py --ticker TSLA 2>&1 | tail -2
PYTHONPATH=. python3 main.py --ticker MSFT 2>&1 | tail -2

python3 -c "
import json
from pathlib import Path
for ticker in ['MSFT', 'TSLA']:
    r  = json.loads(Path(f'valuation_data/serving/latest/{ticker}_report.json').read_text())
    ps = r['4_valuation_synthesis']['investment_thesis'].get('pillar_scores', {})
    p3_reasons = ps.get('p3_reasons', [])
    print(f'{ticker}: P3={ps.get(\"p3_tailwind\")}  total={ps.get(\"capped_total\")}  tier={ps.get(\"position_tier\")}')
    for r_ in p3_reasons:
        print(f'  → {r_}')
" 2>&1
