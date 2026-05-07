import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from scratch.verify_phase6 import make_calc_input
from aletheia.tools.dcf_engine import DCFEngine

engine = DCFEngine(verbose=False)

def print_result(ticker):
    print(f"\n--- {ticker} ---")
    calc_input = make_calc_input(ticker)
    
    # Just to see what's in the df
    df = calc_input.df
    print(f"Revenue (latest): {df['Revenue'].iloc[-1]:,.0f}")
    if 'InvestedCapital' in df.columns:
        print(f"Invested Capital (latest): {df['InvestedCapital'].iloc[-1]:,.0f}")
    else:
        print("InvestedCapital column missing from df!")
        
    res = engine.run(calc_input)
    if res.errors:
        print(f"Errors: {res.errors}")
        return
        
    base = res.base
    if not base:
        print("No base scenario generated")
        return
        
    print(f"Base Enterprise Value: {base.enterprise_value:,.2f}")
    iv = res.intrinsic_per_share(base.enterprise_value, res.net_debt)
    print(f"Base IV: {iv}")
    
    if hasattr(base, 'terminal'):
        print(f"Terminal Value Used: {base.terminal.tv_used:,.2f}")
        print(f"Terminal Gordon: {base.terminal.gordon_tv:,.2f}")
        print(f"Terminal Reinvestment (Liberti): {base.terminal.reinvestment_tv:,.2f}")

for t in ["ORCL", "TXN", "TSLA"]:
    print_result(t)
