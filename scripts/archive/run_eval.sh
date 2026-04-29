for ticker in MSFT AAPL NVDA GOOGL META AMZN TSLA CNC; do
    echo "Running $ticker..."
    PYTHONPATH=. python3 main.py --ticker $ticker 2>&1 | grep -E "conviction|pillar|PASS|FAIL|Lead:|✅|❌"
done

python3 -c "
import json
from pathlib import Path

print(f'{'Ticker':>6} | {'Conv':>5} | {'P1':>3} | {'P2':>3} | {'P3':>3} | {'P4':>3} | {'P5':>3} | {'Total':>6} | {'Tier':>12} | Cap')
print('-' * 72)

for ticker in ['MSFT','AAPL','NVDA','GOOGL','META','AMZN','TSLA','CNC']:
    path = Path(f'valuation_data/serving/latest/{ticker}_report.json')
    if not path.exists():
        print(f'{ticker}: no report')
        continue
    r = json.loads(path.read_text())
    thesis = r.get('4_valuation_synthesis', {}).get('investment_thesis', {})
    ps = thesis.get('pillar_scores', {})
    conv = thesis.get('conviction_score', '?')
    
    if isinstance(ps, dict):
        p1 = ps.get('p1_moat', {}).get('score', '?') if isinstance(ps.get('p1_moat'), dict) else '?'
        p2 = ps.get('p2_health', {}).get('score', '?') if isinstance(ps.get('p2_health'), dict) else '?'
        p3 = ps.get('p3_tailwind', {}).get('score', '?') if isinstance(ps.get('p3_tailwind'), dict) else '?'
        p4 = ps.get('p4_mos', {}).get('score', '?') if isinstance(ps.get('p4_mos'), dict) else '?'
        p5 = ps.get('p5_leadership', {}).get('score', '?') if isinstance(ps.get('p5_leadership'), dict) else '?'
        total = ps.get('capped_total', ps.get('raw_total', '?'))
        tier = ps.get('position_tier', '?')
        cap = '⚠ YES' if ps.get('cap_applied') else 'no'
        rfx = '★' if ps.get('reflexivity_cap') else ''
    else:
        p1=p2=p3=p4=p5=total=tier=cap=rfx='?'
        
    print(f'{ticker:>6} | {str(conv):>5} | {str(p1):>3} | {str(p2):>3} | {str(p3):>3} | {str(p4):>3} | {str(p5):>3} | {str(total):>6} | {tier+rfx:>12} | {cap}')
" 2>&1
