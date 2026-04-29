for ticker in AAPL MSFT NVDA GOOGL AMZN TSLA CNC; do
    PYTHONPATH=. python3 main.py --ticker $ticker 2>&1 | tail -2
done

python3 -c "
import json
from pathlib import Path

print(f\"{'Ticker':>6} | {'Conv':>5} | {'P1':>3} | {'P2':>3} | {'P3':>3} | {'P4':>3} | {'P5':>3} | {'Total':>5} | {'Tier':>10} | {'Rfx':>5} | {'Cap':>5}\")
print('-' * 78)
for ticker in ['MSFT','NVDA','META','AAPL','AMZN','GOOGL','CNC','TSLA']:
    path = Path(f'valuation_data/serving/latest/{ticker}_report.json')
    if not path.exists(): continue
    r  = json.loads(path.read_text())
    it = r['4_valuation_synthesis']['investment_thesis']
    ps = it.get('pillar_scores', {})
    rfx = '★' if ps.get('reflexivity_cap') else '-'
    cap = '⚠' if ps.get('cap_applied') else '-'
    print(f\"{ticker:>6} | {str(it.get('conviction_score','')):>5} | \"
          f\"{str(ps.get('p1_moat','')):>3} | {str(ps.get('p2_health','')):>3} | \"
          f\"{str(ps.get('p3_tailwind','')):>3} | {str(ps.get('p4_mos','')):>3} | \"
          f\"{str(ps.get('p5_leadership','')):>3} | \"
          f\"{str(ps.get('capped_total','')):>5} | \"
          f\"{ps.get('position_tier',''):>10} | {rfx:>5} | {cap:>5}\")
" 2>&1
