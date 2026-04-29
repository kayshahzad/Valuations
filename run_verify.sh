# Quickly regenerate MSFT and TSLA so the JSON has the new tier names
PYTHONPATH=. python3 main.py --ticker MSFT > /dev/null 2>&1
PYTHONPATH=. python3 main.py --ticker TSLA > /dev/null 2>&1

python3 -c "
import json
from pathlib import Path
for t in ['MSFT','TSLA']:
    r = json.loads(Path(f'valuation_data/serving/latest/{t}_report.json').read_text())
    ps = r['4_valuation_synthesis']['investment_thesis']['pillar_scores']
    print(f'{t}: tier={ps.get(\"position_tier\")}')
"

echo ""
echo "Running AMD..."
PYTHONPATH=. python3 main.py --ticker AMD 2>&1 | tail -5
