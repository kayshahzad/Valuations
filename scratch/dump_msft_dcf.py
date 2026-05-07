import pandas as pd
from aletheia.tools.dcf_engine import DCFEngine

engine = DCFEngine(verbose=False)
res = engine.run("MSFT")

base = res.base

print("\n--- MSFT BASE SCENARIO ASSUMPTIONS ---")
print(f"Revenue CAGR Y1-5:  {base.assumptions.revenue_cagr_y1_5:.2%}")
print(f"Revenue CAGR Y6-10: {base.assumptions.revenue_cagr_y6_10:.2%}")
print(f"EBIT Margin Cur:    {base.assumptions.ebit_margin_current:.2%}")
print(f"EBIT Margin Term:   {base.assumptions.ebit_margin_terminal:.2%}")
print(f"WACC:               {base.assumptions.wacc:.2%}")
print(f"Terminal Growth:    {base.assumptions.terminal_growth:.2%}")

print("\n--- PROJECTIONS ---")
print("Year | Rev ($B) | Margin | EBIT ($B) | NOPAT ($B) | FCFF ($B) | PV FCFF ($B)")
for p in base.projections:
    print(f"{p.year:4d} | {p.revenue/1e9:8.1f} | {p.ebit_margin:5.1%} | {p.ebit/1e9:9.1f} | {p.nopat/1e9:10.1f} | {p.fcff/1e9:9.1f} | {p.pv_fcff/1e9:12.1f}")

print("\n--- VALUATION SUMMARY ---")
print(f"Terminal Value: ${base.terminal.tv_used:,.1f}")
print(f"Enterprise Val: ${base.enterprise_value:,.1f}")
print(f"Net Debt:       ${res.net_debt:,.1f}")
shares = res.shares_diluted or 1.0
print(f"Shares:         {shares:,.1f}")
iv = (base.enterprise_value - res.net_debt) / shares
print(f"Implied IV:     ${iv:,.2f}")

