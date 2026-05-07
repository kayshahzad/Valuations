import json

with open('baselines/pre-correctness-pass/LLY_report.json') as f:
    b = json.load(f)
with open('valuation_data/serving/latest/LLY_report.json') as f:
    n = json.load(f)

# The correct paths
iv_b = b['4_valuation_synthesis']['phase2_valuation']['three_scenario_dcf']['base']['ev'] / 1e9
# Updated path for new structure
iv_n = n['4_valuation_synthesis']['phase2_valuation']['three_scenario_dcf']['base']['ev']
if iv_n is not None:
    iv_n = iv_n / 1e9
else:
    iv_n = 0.0

print("--- PR 3: Cyclicality Logic ---")
print(f"Baseline Base EV: ${iv_b:.0f}B")
print(f"New Base EV: ${iv_n:.0f}B")

fp_b = b['3_capital_structure_risk'].get('risk_factors', {}).get('downside', {}).get('floor_price_per_share')
fp_n = n['3_capital_structure_risk'].get('risk_factors', {}).get('downside', {}).get('floor_price_per_share')

print("\n--- PR 1: Floor Price Logic ---")
print(f"Baseline Floor Price: {fp_b}")
print(f"New Floor Price: ${fp_n:.2f}")

qs_b = b['2_financial_translation'].get('quality_screens', {})
qs_n = n['2_financial_translation'].get('quality_screens', {})
print("\n--- PR 1: Quality Screens ---")
print(f"Baseline Beneish: {qs_b.get('beneish_m_score')}")
print(f"New Beneish: {qs_n.get('beneish_m_score')}")
print(f"Baseline Sloan: {qs_b.get('sloan_accrual')}")
print(f"New Sloan: {qs_n.get('sloan_accrual')}")
