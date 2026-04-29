# Aletheia-Intelligence Configuration
| Parameter | Default Value | Description |
| :--- | :--- | :--- |
| **Global Assumptions** | | |
| `tax_rate_global` | 0.21 | Effective Tax Rate (21% US Default). |
| `wacc_floor` | 0.09 | Minimum WACC for non-Mega Caps. |
| `terminal_growth_cap` | 0.03 | Max Terminal Growth Rate (3%). |
| **Risk Thresholds** | | |
| `liquidity_ratio_safe` | 1.5 | Minimum (Maturity / Cash) ratio before alert. |
| `maturity_amortization_rate` | 0.10 | Assumed % of LT Debt maturing annually if schedule unknown. |
| `double_leverage_threshold` | 7.0 | Score threshold for "High Leverage". |
| **Stress Testing** | | |
| `stress_test_revenue_impact` | 0.50 | Revenue haircut in "Break-the-Company" audit. |
| `stress_test_wacc_impact` | 0.02 | WACC increase in "Break-the-Company" audit. |
