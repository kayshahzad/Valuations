PYTHONPATH=. python3 main.py --ticker AAPL 2>&1 | tail -5
PYTHONPATH=. python3 main.py --ticker TSLA 2>&1 | tail -5
PYTHONPATH=. python3 main.py --ticker CNC  2>&1 | tail -5

python3 -c "
import json
from pathlib import Path

# Check full P2 and P3 reasons for all three problem tickers
for ticker in ['AAPL', 'TSLA', 'CNC']:
    path = Path(f'valuation_data/serving/latest/{ticker}_report.json')
    r  = json.loads(path.read_text())
    ps = r['4_valuation_synthesis']['investment_thesis'].get('pillar_scores', {})
    print(f'=== {ticker} (stage={ps.get(\"lifecycle_stage\")}) ===')
    print(f'P2={ps.get(\"p2_health\")} reasons:')
    for x in ps.get('p2_reasons', []): print(f'  {x}')
    print(f'P3={ps.get(\"p3_tailwind\")} reasons:')
    for x in ps.get('p3_reasons', []): print(f'  {x}')
    print(f'Total: {ps.get(\"capped_total\")}')
    print()
" 2>&1
