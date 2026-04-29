echo "Running full pipeline for TSM..."
PYTHONPATH=. python3 main.py --ticker TSM 2>&1 | tail -5

echo ""
python3 -c "
import json
from pathlib import Path

tickers = ['MSFT','NVDA','META','AAPL','AMZN','GOOGL','CNC','TSLA','AMD','ASML','TSM']
print(f'{'Ticker':>6} | {'Stage':>20} | {'P1':>3} {'P2':>3} {'P3':>3} {'P4':>3} {'P5':>3} | {'Tot':>3} | {'Tier':>12} | {'MoS':>7} | {'ROIC':>6}')
print('─' * 100)

for ticker in tickers:
    path = Path(f'valuation_data/serving/latest/{ticker}_report.json')
    if not path.exists():
        print(f'  {ticker}: no report')
        continue
    r   = json.loads(path.read_text())
    it  = r['4_valuation_synthesis']['investment_thesis']
    ps  = it.get('pillar_scores', {})
    ft  = r['2_financial_translation']
    rat = (ft.get('ratios') or {})
    p2v = r['4_valuation_synthesis']['phase2_valuation']
    dcf3= p2v.get('three_scenario_dcf', {})
    stage = ps.get('lifecycle_stage', '?')[:20]
    total = ps.get('capped_total', '?')
    tier  = ps.get('position_tier', '?')
    rfx   = '★' if ps.get('reflexivity_cap') else ''
    mos   = dcf3.get('base', {}).get('margin_of_safety', 0) or 0
    roic  = (rat.get('roic') or 0) * 100
    print(f'  {ticker:>4} | {stage:>20} | '
          f'{str(ps.get(\"p1_moat\",\"?\")):>3} '
          f'{str(ps.get(\"p2_health\",\"?\")):>3} '
          f'{str(ps.get(\"p3_tailwind\",\"?\")):>3} '
          f'{str(ps.get(\"p4_mos\",\"?\")):>3} '
          f'{str(ps.get(\"p5_leadership\",\"?\")):>3} | '
          f'{str(total):>3} | '
          f'{tier+rfx:>12} | '
          f'{mos:>+7.1%} | '
          f'{roic:>5.1f}%')
print('─' * 100)
print('★ = reflexivity cap  | MoS = base case margin of safety')
" 2>&1
