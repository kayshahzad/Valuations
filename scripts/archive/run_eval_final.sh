for ticker in MSFT AAPL NVDA GOOGL META AMZN TSLA CNC; do
    PYTHONPATH=. python3 main.py --ticker $ticker 2>&1 | tail -3
done

python3 -c "
import json
from pathlib import Path

print(f'{'Ticker':>6} | {'Conv':>5} | {'P1':>3} | {'P2':>3} | {'P3':>3} | {'P4':>3} | {'P5':>3} | {'Total':>6} | Tier')
print('-' * 65)
for ticker in ['MSFT','AAPL','NVDA','GOOGL','META','AMZN','TSLA','CNC']:
    path = Path(f'valuation_data/serving/latest/{ticker}_report.json')
    if not path.exists(): continue
    r    = json.loads(path.read_text())
    it   = r.get('4_valuation_synthesis',{}).get('investment_thesis',{})
    ps   = it.get('pillar_scores', {})
    conv = it.get('conviction_score','?')
    
    # Handle both old format (nested dict) and new format (flat int) for safety
    def get_score(key):
        v = ps.get(key, '?')
        if isinstance(v, dict): return str(v.get('score', '?'))
        return str(v)
        
    print(f'{ticker:>6} | {str(conv):>5} | '
          f'{get_score(\"p1_moat\")  :>3} | '
          f'{get_score(\"p2_health\"):>3} | '
          f'{get_score(\"p3_tailwind\"):>3} | '
          f'{get_score(\"p4_mos\"):>3} | '
          f'{get_score(\"p5_leadership\"):>3} | '
          f'{str(ps.get(\"capped_total\",ps.get(\"raw_total\",\"?\"))):>6} | '
          f'{ps.get(\"position_tier\",\"?\")}')
" 2>&1
