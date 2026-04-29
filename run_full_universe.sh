for ticker in MSFT AAPL NVDA GOOGL META AMZN TSLA SMCI LLY COST NEE CAT JPM BRK-B V WMT UNH ABT AMD ASML TSM QCOM ORCL TXN CNC; do
    echo "Running $ticker..."
    PYTHONPATH=. python3 main.py --ticker $ticker 2>&1 | tail -2
done

python3 -c "
import json
from pathlib import Path

print(f'{'Ticker':>6} | {'Stage':>20} | {'P1':>3} {'P2':>3} {'P3':>3} {'P4':>3} {'P5':>3} | {'Total':>5} | {'Tier':>12}')
print('─' * 80)

for ticker in ['MSFT','AAPL','NVDA','GOOGL','META','AMZN','TSLA','SMCI','LLY','COST','NEE','CAT','JPM','BRK-B','V','WMT','UNH','ABT','AMD','ASML','TSM','QCOM','ORCL','TXN','CNC']:
    path = Path(f'valuation_data/serving/latest/{ticker}_report.json')
    if not path.exists(): continue
    r  = json.loads(path.read_text())
    it = r['4_valuation_synthesis']['investment_thesis']
    ps = it.get('pillar_scores', {})
    stage = ps.get('lifecycle_stage', '?')
    total = ps.get('capped_total', ps.get('raw_total', '?'))
    rfx   = '★' if ps.get('reflexivity_cap') else ''
    print(f'{ticker:>6} | {stage:>20} | '
          f'{str(ps.get(\"p1_moat\",\"?\")):>3} '
          f'{str(ps.get(\"p2_health\",\"?\")):>3} '
          f'{str(ps.get(\"p3_tailwind\",\"?\")):>3} '
          f'{str(ps.get(\"p4_mos\",\"?\")):>3} '
          f'{str(ps.get(\"p5_leadership\",\"?\")):>3} | '
          f'{str(total):>5} | '
          f'{ps.get(\"position_tier\",\"?\")}{rfx}')
print('─' * 80)
" 2>&1
