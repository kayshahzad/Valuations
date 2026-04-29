python3 -m aletheia.data.edgar_client --ticker LLY
PYTHONPATH=. python3 main.py --ticker LLY

python3 -c "
import json
from pathlib import Path
path = Path(f'valuation_data/serving/latest/LLY_report.json')
r   = json.loads(path.read_text())
rat = r['2_financial_translation'].get('ratios', {})
dcf3= r['4_valuation_synthesis']['phase2_valuation'].get('three_scenario_dcf', {})
print(f'=== LLY ===')
print(f'ROIC:         {(rat.get(\"roic\") or 0)*100:.1f}%')
print(f'Base IV:      \${dcf3.get(\"base\",{}).get(\"intrinsic_per_share\",0):.2f}')
"
