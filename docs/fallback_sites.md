# Fallback substitution sites — Phase-0 enumeration (task 0.2.2)

Complete inventory of falsy-fallback sites where a legitimate `0`/`None`
can be replaced by a fabricated constant. **Gate: no Phase-1 code merges
until every row's `criticality` is filled.**

- **HOT** — feeds NOPAT / ROE / WACC / IV (a wrong value moves valuation)
- **WARM** — feeds a ratio/margin shown to the user but not the IV
- **COLD** — display-only / defensive / genuinely-safe default

**Totals:** 127 numeric-constant fallbacks · 66 `.get()` accessor chains · 193 sites total.

### Numeric-constant fallbacks by file

| file | count |
|---|---|
| `aletheia/data/cleaning_engine.py` | 58 |
| `aletheia/calculations/identity_checks.py` | 35 |
| `aletheia/data/ttm_derivation.py` | 8 |
| `aletheia/calculations/formulas/balance_sheet.py` | 6 |
| `aletheia/data/sec_quarterly.py` | 6 |
| `aletheia/calculations/_schema_contract.py` | 3 |
| `aletheia/calculations/formulas/derived_inputs.py` | 2 |
| `aletheia/data/database.py` | 2 |
| `aletheia/calculations/formulas/cash_flow.py` | 1 |
| `aletheia/calculations/formulas/cost_of_capital.py` | 1 |
| `aletheia/calculations/formulas/income_statement.py` | 1 |
| `aletheia/calculations/formulas/residual_income.py` | 1 |
| `aletheia/data/edgar_client.py` | 1 |
| `aletheia/data/quantitative_screens.py` | 1 |
| `aletheia/data/utility_taxonomy.py` | 1 |

### All numeric-constant fallback sites

`suggested` is a DRAFT heuristic; fill `confirmed` by hand (the gate).

| # | file:line | function | const | suggested | confirmed | source |
|---|---|---|---|---|---|---|
| 1 | `aletheia/calculations/_schema_contract.py:235` | `_record_fy` | `0` | WARM? | _TODO_ | `return int(getattr(record, "fiscal_year", 0) or 0)` |
| 2 | `aletheia/calculations/_schema_contract.py:419` | `validate_cleaned_record_schema_contract` | `0.0` | WARM? | _TODO_ | `_safe_record_field(record, "raw", "RedeemableNoncontrollingInterest")` |
| 3 | `aletheia/calculations/_schema_contract.py:445` | `validate_cleaned_record_schema_contract` | `0.0` | WARM? | _TODO_ | `_safe_record_field(record, "raw", "MinorityInterest")` |
| 4 | `aletheia/calculations/formulas/balance_sheet.py:47` | `gross_debt` | `0.0` | WARM? | _TODO_ | `return sum(c or 0.0 for c in components)` |
| 5 | `aletheia/calculations/formulas/balance_sheet.py:67` | `liquid_assets` | `0.0` | WARM? | _TODO_ | `return (cash or 0.0) + (short_term_investments or 0.0) + (long_term_investments or 0.0)` |
| 6 | `aletheia/calculations/formulas/balance_sheet.py:67` | `liquid_assets` | `0.0` | WARM? | _TODO_ | `return (cash or 0.0) + (short_term_investments or 0.0) + (long_term_investments or 0.0)` |
| 7 | `aletheia/calculations/formulas/balance_sheet.py:67` | `liquid_assets` | `0.0` | WARM? | _TODO_ | `return (cash or 0.0) + (short_term_investments or 0.0) + (long_term_investments or 0.0)` |
| 8 | `aletheia/calculations/formulas/balance_sheet.py:83` | `net_debt` | `0.0` | HOT? | _TODO_ | `return (gross_debt or 0.0) - (liquid_assets or 0.0)` |
| 9 | `aletheia/calculations/formulas/balance_sheet.py:83` | `net_debt` | `0.0` | HOT? | _TODO_ | `return (gross_debt or 0.0) - (liquid_assets or 0.0)` |
| 10 | `aletheia/calculations/formulas/cash_flow.py:39` | `fcf` | `0.0` | HOT? | _TODO_ | `return operating_cf - abs(capex or 0.0)` |
| 11 | `aletheia/calculations/formulas/cost_of_capital.py:115` | `wacc` | `0.04` | HOT? | _TODO_ | `floor = max(WACC_FLOOR_MIN, (risk_free_rate or 0.04) + 0.01)` |
| 12 | `aletheia/calculations/formulas/derived_inputs.py:76` | `invested_capital` | `0.0` | HOT? | _TODO_ | `cash = cash or 0.0` |
| 13 | `aletheia/calculations/formulas/derived_inputs.py:77` | `invested_capital` | `0.0` | HOT? | _TODO_ | `rev = revenue or 0.0` |
| 14 | `aletheia/calculations/formulas/income_statement.py:28` | `ebitda` | `0.0` | HOT? | _TODO_ | `return operating_income + (depreciation_total or 0.0)` |
| 15 | `aletheia/calculations/formulas/residual_income.py:85` | `residual_income_value` | `0.0` | WARM? | _TODO_ | `iv = bvps0 + pv_explicit + (pv_terminal or 0.0)` |
| 16 | `aletheia/calculations/identity_checks.py:364` | `check_balance_sheet_equation` | `0.0` | COLD? | _TODO_ | `_field(record, "RedeemableNoncontrollingInterest")` |
| 17 | `aletheia/calculations/identity_checks.py:502` | `check_retained_earnings_rollforward` | `0.0` | COLD? | _TODO_ | `div = _field(current, "DividendsPaid") or 0.0` |
| 18 | `aletheia/calculations/identity_checks.py:510` | `check_retained_earnings_rollforward` | `0.0` | COLD? | _TODO_ | `buybacks = _field(current, "Buybacks") or 0.0` |
| 19 | `aletheia/calculations/identity_checks.py:511` | `check_retained_earnings_rollforward` | `0.0` | COLD? | _TODO_ | `sbc = _field(current, "SBC") or 0.0` |
| 20 | `aletheia/calculations/identity_checks.py:512` | `check_retained_earnings_rollforward` | `0.0` | COLD? | _TODO_ | `tax_withhold = loader.xbrl_fact(` |
| 21 | `aletheia/calculations/identity_checks.py:841` | `check_ppe_rollforward` | `0.0` | COLD? | _TODO_ | `gw_beg = _field(prior, "Goodwill") or 0.0` |
| 22 | `aletheia/calculations/identity_checks.py:842` | `check_ppe_rollforward` | `0.0` | COLD? | _TODO_ | `gw_end = _field(current, "Goodwill") or 0.0` |
| 23 | `aletheia/calculations/identity_checks.py:852` | `check_ppe_rollforward` | `0.0` | COLD? | _TODO_ | `revenue = _field(current, "Revenue") or 0.0` |
| 24 | `aletheia/calculations/identity_checks.py:926` | `_total_debt_for_year` | `0.0` | COLD? | _TODO_ | `ltd_nc = loader.xbrl_fact(ticker, "LongTermDebtNoncurrent", fiscal_year) or 0.0` |
| 25 | `aletheia/calculations/identity_checks.py:927` | `_total_debt_for_year` | `0.0` | COLD? | _TODO_ | `ltd_c = loader.xbrl_fact(ticker, "LongTermDebtCurrent", fiscal_year) or 0.0` |
| 26 | `aletheia/calculations/identity_checks.py:928` | `_total_debt_for_year` | `0.0` | COLD? | _TODO_ | `cp = loader.xbrl_fact(ticker, "CommercialPaper", fiscal_year) or 0.0` |
| 27 | `aletheia/calculations/identity_checks.py:929` | `_total_debt_for_year` | `0.0` | COLD? | _TODO_ | `fl_c = loader.xbrl_fact(ticker, "FinanceLeaseLiabilityCurrent", fiscal_year) or 0.0` |
| 28 | `aletheia/calculations/identity_checks.py:930` | `_total_debt_for_year` | `0.0` | COLD? | _TODO_ | `fl_nc = loader.xbrl_fact(ticker, "FinanceLeaseLiabilityNoncurrent", fiscal_year) or 0.0` |
| 29 | `aletheia/calculations/identity_checks.py:935` | `_total_debt_for_year` | `0.0` | COLD? | _TODO_ | `total = (fallback_std or 0.0) + (fallback_ltd or 0.0)` |
| 30 | `aletheia/calculations/identity_checks.py:935` | `_total_debt_for_year` | `0.0` | COLD? | _TODO_ | `total = (fallback_std or 0.0) + (fallback_ltd or 0.0)` |
| 31 | `aletheia/calculations/identity_checks.py:965` | `check_debt_rollforward` | `0.0` | COLD? | _TODO_ | `beg_std_fallback = _field(prior, "ShortTermDebt") or 0.0` |
| 32 | `aletheia/calculations/identity_checks.py:966` | `check_debt_rollforward` | `0.0` | COLD? | _TODO_ | `beg_ltd_fallback = _field(prior, "LongTermDebt") or 0.0` |
| 33 | `aletheia/calculations/identity_checks.py:967` | `check_debt_rollforward` | `0.0` | COLD? | _TODO_ | `end_std_fallback = _field(current, "ShortTermDebt") or 0.0` |
| 34 | `aletheia/calculations/identity_checks.py:968` | `check_debt_rollforward` | `0.0` | COLD? | _TODO_ | `end_ltd_fallback = _field(current, "LongTermDebt") or 0.0` |
| 35 | `aletheia/calculations/identity_checks.py:978` | `check_debt_rollforward` | `0.0` | COLD? | _TODO_ | `loader.xbrl_fact(ticker, "ProceedsFromIssuanceOfLongTermDebt", fy)` |
| 36 | `aletheia/calculations/identity_checks.py:983` | `check_debt_rollforward` | `0.0` | COLD? | _TODO_ | `loader.xbrl_fact(ticker, "RepaymentsOfLongTermDebt", fy)` |
| 37 | `aletheia/calculations/identity_checks.py:988` | `check_debt_rollforward` | `0.0` | COLD? | _TODO_ | `cp_net = loader.xbrl_fact(` |
| 38 | `aletheia/calculations/identity_checks.py:1045` | `check_debt_rollforward` | `0.0` | COLD? | _TODO_ | `gw_beg = _field(prior, "Goodwill") or 0.0` |
| 39 | `aletheia/calculations/identity_checks.py:1046` | `check_debt_rollforward` | `0.0` | COLD? | _TODO_ | `gw_end = _field(current, "Goodwill") or 0.0` |
| 40 | `aletheia/calculations/identity_checks.py:1138` | `check_working_capital_reconciliation` | `0.0` | COLD? | _TODO_ | `gw_beg = _field(prior, "Goodwill") or 0.0` |
| 41 | `aletheia/calculations/identity_checks.py:1139` | `check_working_capital_reconciliation` | `0.0` | COLD? | _TODO_ | `gw_end = _field(current, "Goodwill") or 0.0` |
| 42 | `aletheia/calculations/identity_checks.py:1329` | `check_fcf_pathway_reconciliation` | `0.0` | COLD? | _TODO_ | `ar_b = _field(prior, "AccountsReceivable") or 0.0` |
| 43 | `aletheia/calculations/identity_checks.py:1330` | `check_fcf_pathway_reconciliation` | `0.0` | COLD? | _TODO_ | `ar_e = _field(current, "AccountsReceivable") or 0.0` |
| 44 | `aletheia/calculations/identity_checks.py:1331` | `check_fcf_pathway_reconciliation` | `0.0` | COLD? | _TODO_ | `inv_b = _field(prior, "Inventory") or 0.0` |
| 45 | `aletheia/calculations/identity_checks.py:1332` | `check_fcf_pathway_reconciliation` | `0.0` | COLD? | _TODO_ | `inv_e = _field(current, "Inventory") or 0.0` |
| 46 | `aletheia/calculations/identity_checks.py:1333` | `check_fcf_pathway_reconciliation` | `0.0` | COLD? | _TODO_ | `ap_b = _field(prior, "AccountsPayable") or 0.0` |
| 47 | `aletheia/calculations/identity_checks.py:1334` | `check_fcf_pathway_reconciliation` | `0.0` | COLD? | _TODO_ | `ap_e = _field(current, "AccountsPayable") or 0.0` |
| 48 | `aletheia/calculations/identity_checks.py:1344` | `check_fcf_pathway_reconciliation` | `0.0` | COLD? | _TODO_ | `sbc = _field(current, "SBC") or 0.0` |
| 49 | `aletheia/calculations/identity_checks.py:1448` | `check_fcf_pathway_reconciliation` | `0.0` | COLD? | _TODO_ | `gw_beg = _field(prior, "Goodwill") or 0.0 if prior else 0.0` |
| 50 | `aletheia/calculations/identity_checks.py:1449` | `check_fcf_pathway_reconciliation` | `0.0` | COLD? | _TODO_ | `gw_end = _field(current, "Goodwill") or 0.0` |
| 51 | `aletheia/data/cleaning_engine.py:414` | `clean` | `0.0` | WARM? | _TODO_ | `sbc = record.clean.get("SBC") or 0.0` |
| 52 | `aletheia/data/cleaning_engine.py:871` | `_domain3_ebit_normalization` | `0.0` | HOT? | _TODO_ | `normalized_ebit = pretax + (record.raw.get("InterestExpense") or 0.0)` |
| 53 | `aletheia/data/cleaning_engine.py:886` | `_domain3_ebit_normalization` | `0.0` | HOT? | _TODO_ | `other_income = record.raw.get("OtherNonoperatingIncomeExpense") or 0.0` |
| 54 | `aletheia/data/cleaning_engine.py:887` | `_domain3_ebit_normalization` | `0.0` | HOT? | _TODO_ | `revenue = record.clean.get("Revenue") or record.raw.get("Revenue") or 0.0` |
| 55 | `aletheia/data/cleaning_engine.py:903` | `_domain3_ebit_normalization` | `0.21` | HOT? | _TODO_ | `tax_rate = record.clean.get("CashTaxRate") or 0.21` |
| 56 | `aletheia/data/cleaning_engine.py:969` | `_domain4_accounting_policy` | `1.0` | HOT? | _TODO_ | `revenue = record.raw.get("Revenue") or 1.0` |
| 57 | `aletheia/data/cleaning_engine.py:1031` | `_domain5_lease_normalization` | `0.0` | WARM? | _TODO_ | `op_lease_current = record.raw.get("OperatingLeaseLiabilityCurrent") or 0.0` |
| 58 | `aletheia/data/cleaning_engine.py:1032` | `_domain5_lease_normalization` | `0.0` | WARM? | _TODO_ | `op_lease_noncurrent = record.raw.get("OperatingLeaseLiabilityNoncurrent") or 0.0` |
| 59 | `aletheia/data/cleaning_engine.py:1036` | `_domain5_lease_normalization` | `0.0` | WARM? | _TODO_ | `op_lease_cost = record.raw.get("OperatingLeaseCost") or 0.0` |
| 60 | `aletheia/data/cleaning_engine.py:1039` | `_domain5_lease_normalization` | `0.0` | WARM? | _TODO_ | `rou_asset = record.raw.get("OperatingLeaseRightOfUseAsset") or 0.0` |
| 61 | `aletheia/data/cleaning_engine.py:1079` | `_domain5_lease_normalization` | `1.0` | WARM? | _TODO_ | `lease_ratio = total_op_lease / (record.raw.get("TotalAssets") or 1.0)` |
| 62 | `aletheia/data/cleaning_engine.py:1122` | `_domain6_pension_cleaning` | `1.0` | WARM? | _TODO_ | `total_assets = record.raw.get("TotalAssets") or 1.0` |
| 63 | `aletheia/data/cleaning_engine.py:1197` | `_domain7_sbc_adjustment` | `0.0` | WARM? | _TODO_ | `sbc = record.raw.get("SBC") or record.raw.get("ShareBasedCompensation") or 0.0` |
| 64 | `aletheia/data/cleaning_engine.py:1198` | `_domain7_sbc_adjustment` | `1.0` | HOT? | _TODO_ | `revenue = record.raw.get("Revenue") or 1.0` |
| 65 | `aletheia/data/cleaning_engine.py:1279` | `_domain7_sbc_adjustment` | `0.0` | WARM? | _TODO_ | `buybacks = record.raw.get("Buybacks") or record.raw.get("PaymentsForRepurchaseOfCommonStock") or 0.0` |
| 66 | `aletheia/data/cleaning_engine.py:1312` | `_domain8_revenue_recognition` | `0.0` | HOT? | _TODO_ | `revenue = record.raw.get("Revenue") or 0.0` |
| 67 | `aletheia/data/cleaning_engine.py:1313` | `_domain8_revenue_recognition` | `0.0` | HOT? | _TODO_ | `ar = record.raw.get("AccountsReceivable") or record.raw.get("AccountsReceivableNetCurrent") or 0.0` |
| 68 | `aletheia/data/cleaning_engine.py:1314` | `_domain8_revenue_recognition` | `0.0` | HOT? | _TODO_ | `deferred_rev = record.raw.get("DeferredRevenue") or record.raw.get("DeferredRevenueCurrent") or 0.0` |
| 69 | `aletheia/data/cleaning_engine.py:1315` | `_domain8_revenue_recognition` | `0.0` | HOT? | _TODO_ | `cash_ops = record.raw.get("OperatingCF") or record.raw.get("NetCashProvidedByUsedInOperatingActivities") or 0.0` |
| 70 | `aletheia/data/cleaning_engine.py:1321` | `_domain8_revenue_recognition` | `0.0` | HOT? | _TODO_ | `prior_revenue = prior.raw.get("Revenue") or 0.0` |
| 71 | `aletheia/data/cleaning_engine.py:1322` | `_domain8_revenue_recognition` | `0.0` | HOT? | _TODO_ | `prior_ar = prior.raw.get("AccountsReceivable") or prior.raw.get("AccountsReceivableNetCurrent") or 0.0` |
| 72 | `aletheia/data/cleaning_engine.py:1323` | `_domain8_revenue_recognition` | `0.0` | HOT? | _TODO_ | `prior_deferred = prior.raw.get("DeferredRevenue") or prior.raw.get("DeferredRevenueCurrent") or 0.0` |
| 73 | `aletheia/data/cleaning_engine.py:1379` | `_domain8_revenue_recognition` | `0` | HOT? | _TODO_ | `spread = record.clean.get("AR_RevGrowth_Spread", 0) or 0` |
| 74 | `aletheia/data/cleaning_engine.py:1396` | `_domain9_working_capital` | `0.0` | WARM? | _TODO_ | `current_assets = record.raw.get("CurrentAssets") or record.raw.get("AssetsCurrent") or 0.0` |
| 75 | `aletheia/data/cleaning_engine.py:1397` | `_domain9_working_capital` | `0.0` | WARM? | _TODO_ | `current_liab = record.raw.get("LiabilitiesCurrent") or 0.0` |
| 76 | `aletheia/data/cleaning_engine.py:1398` | `_domain9_working_capital` | `0.0` | WARM? | _TODO_ | `cash = record.raw.get("Cash") or record.raw.get("CashAndCashEquivalentsAtCarryingValue") or 0.0` |
| 77 | `aletheia/data/cleaning_engine.py:1406` | `_domain9_working_capital` | `1.0` | HOT? | _TODO_ | `revenue = record.raw.get("Revenue") or 1.0` |
| 78 | `aletheia/data/cleaning_engine.py:1414` | `_domain9_working_capital` | `0.0` | WARM? | _TODO_ | `prior_nwc = prior.clean.get("NWC") or 0.0` |
| 79 | `aletheia/data/cleaning_engine.py:1487` | `_domain10_tax_sustainability` | `0.0` | HOT? | _TODO_ | `record.raw.get("TaxExpense")` |
| 80 | `aletheia/data/cleaning_engine.py:1492` | `_domain10_tax_sustainability` | `0.0` | HOT? | _TODO_ | `record.raw.get("PretaxIncome")` |
| 81 | `aletheia/data/cleaning_engine.py:1498` | `_domain10_tax_sustainability` | `0.0` | HOT? | _TODO_ | `cash_taxes = record.raw.get("CashTaxesPaid") or record.raw.get("IncomeTaxesPaid") or record.raw.get("IncomeTaxesPaidNet") or 0.0` |
| 82 | `aletheia/data/cleaning_engine.py:1600` | `_compute_depreciation_total` | `0.0` | WARM? | _TODO_ | `record.raw.get("Depreciation_Tangible") or 0.0,` |
| 83 | `aletheia/data/cleaning_engine.py:1601` | `_compute_depreciation_total` | `0.0` | WARM? | _TODO_ | `record.raw.get("IntangibleAmortization") or 0.0,` |
| 84 | `aletheia/data/cleaning_engine.py:1602` | `_compute_depreciation_total` | `0.0` | WARM? | _TODO_ | `record.raw.get("FinanceLeaseAmortization") or 0.0,` |
| 85 | `aletheia/data/cleaning_engine.py:1603` | `_compute_depreciation_total` | `0.0` | WARM? | _TODO_ | `record.raw.get("CapitalizedSoftwareAmortization") or 0.0,` |
| 86 | `aletheia/data/cleaning_engine.py:1680` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `r.derived["SGA_Combined"] = (ga or 0.0) + (sm or 0.0)` |
| 87 | `aletheia/data/cleaning_engine.py:1680` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `r.derived["SGA_Combined"] = (ga or 0.0) + (sm or 0.0)` |
| 88 | `aletheia/data/cleaning_engine.py:1695` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `rnd = r.raw.get("R&D") or 0.0` |
| 89 | `aletheia/data/cleaning_engine.py:1696` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `iprd = r.raw.get("AcquiredInProcessRnD") or 0.0` |
| 90 | `aletheia/data/cleaning_engine.py:1720` | `_compute_derived` | `0.0` | HOT? | _TODO_ | `ebit = pretax + (r.raw.get("InterestExpense") or 0.0)` |
| 91 | `aletheia/data/cleaning_engine.py:1734` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `delta_nwc = r.clean.get("DeltaNWC") or 0.0` |
| 92 | `aletheia/data/cleaning_engine.py:1735` | `_compute_derived` | `0.21` | HOT? | _TODO_ | `cash_tax_rate = r.clean.get("CashTaxRate") or 0.21` |
| 93 | `aletheia/data/cleaning_engine.py:1737` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `total_assets = r.raw.get("TotalAssets") or 0.0` |
| 94 | `aletheia/data/cleaning_engine.py:1738` | `_compute_derived` | `1.0` | HOT? | _TODO_ | `total_equity = r.raw.get("TotalEquity") or 1.0` |
| 95 | `aletheia/data/cleaning_engine.py:1739` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `long_term_debt = r.raw.get("LongTermDebt") or 0.0` |
| 96 | `aletheia/data/cleaning_engine.py:1740` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `net_income = r.raw.get("NetIncome") or 0.0` |
| 97 | `aletheia/data/cleaning_engine.py:1741` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `cash = r.raw.get("Cash") or 0.0` |
| 98 | `aletheia/data/cleaning_engine.py:1742` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `cash_ops = r.raw.get("OperatingCF") or r.raw.get("NetCashProvidedByUsedInOperatingActivities") or r.clean.get("OperatingCF") or 0.0` |
| 99 | `aletheia/data/cleaning_engine.py:1758` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `rd_expense = r.raw.get("R&D") or r.raw.get("ResearchAndDevelopmentExpense") or 0.0` |
| 100 | `aletheia/data/cleaning_engine.py:1768` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `sbc = r.raw.get("SBC") or 0.0` |
| 101 | `aletheia/data/cleaning_engine.py:1886` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `st_debt = r.raw.get("ShortTermDebt") or 0.0` |
| 102 | `aletheia/data/cleaning_engine.py:1887` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `current_lt_debt = r.raw.get("CurrentPortionLongTermDebt") or 0.0` |
| 103 | `aletheia/data/cleaning_engine.py:1901` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `finance_lease_total = (fl_curr or 0.0) + (fl_nc or 0.0)` |
| 104 | `aletheia/data/cleaning_engine.py:1901` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `finance_lease_total = (fl_curr or 0.0) + (fl_nc or 0.0)` |
| 105 | `aletheia/data/cleaning_engine.py:1906` | `_compute_derived` | `0.0` | HOT? | _TODO_ | `excess = r.raw.get("FinanceLeaseLiabilityUndiscountedExcessAmount") or 0.0` |
| 106 | `aletheia/data/cleaning_engine.py:1922` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `st_invest = r.raw.get("ShortTermInvestments") or 0.0` |
| 107 | `aletheia/data/cleaning_engine.py:1923` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `lt_invest = r.raw.get("LongTermInvestments") or 0.0` |
| 108 | `aletheia/data/cleaning_engine.py:1947` | `_compute_derived` | `0.0` | WARM? | _TODO_ | `short_term_debt = r.raw.get("ShortTermDebt", 0.0) or 0.0` |
| 109 | `aletheia/data/database.py:785` | `upsert_record` | `0` | WARM? | _TODO_ | `version = (existing or 0) + 1` |
| 110 | `aletheia/data/database.py:1123` | `upsert_agent_run` | `0` | WARM? | _TODO_ | `version = (existing or 0) + 1` |
| 111 | `aletheia/data/edgar_client.py:517` | `ingest` | `0` | HOT? | _TODO_ | `rev    = df[df.fiscal_year == df.fiscal_year.max()].iloc[0].get("clean_Revenue", 0) or 0` |
| 112 | `aletheia/data/quantitative_screens.py:518` | `_earnings_power_value` | `0.21` | HOT? | _TODO_ | `cash_tax_rate = record.clean.get("CashTaxRate") or 0.21` |
| 113 | `aletheia/data/sec_quarterly.py:512` | `derive_ttm_from_sec` | `0.0` | HOT? | _TODO_ | `net_debt = (long_term_debt or 0.0) - (cash or 0.0)` |
| 114 | `aletheia/data/sec_quarterly.py:512` | `derive_ttm_from_sec` | `0.0` | HOT? | _TODO_ | `net_debt = (long_term_debt or 0.0) - (cash or 0.0)` |
| 115 | `aletheia/data/sec_quarterly.py:527` | `derive_ttm_from_sec` | `0.0` | HOT? | _TODO_ | `(total_equity or 0.0)` |
| 116 | `aletheia/data/sec_quarterly.py:528` | `derive_ttm_from_sec` | `0.0` | WARM? | _TODO_ | `+ (long_term_debt or 0.0)` |
| 117 | `aletheia/data/sec_quarterly.py:529` | `derive_ttm_from_sec` | `0.0` | WARM? | _TODO_ | `- (cash or 0.0)` |
| 118 | `aletheia/data/sec_quarterly.py:564` | `derive_ttm_from_sec` | `0` | WARM? | _TODO_ | `fiscal_year=int(target_fy or 0),` |
| 119 | `aletheia/data/ttm_derivation.py:130` | `derive_ttm_from_fmp` | `0.0` | HOT? | _TODO_ | `real_income = [r for r in income_q if (_f(r.get("revenue")) or 0.0) > 0.0]` |
| 120 | `aletheia/data/ttm_derivation.py:235` | `derive_ttm_from_fmp` | `0.0` | HOT? | _TODO_ | `net_debt = (long_term_debt or 0.0) + (short_term_debt or 0.0) - (cash or 0.0)` |
| 121 | `aletheia/data/ttm_derivation.py:235` | `derive_ttm_from_fmp` | `0.0` | HOT? | _TODO_ | `net_debt = (long_term_debt or 0.0) + (short_term_debt or 0.0) - (cash or 0.0)` |
| 122 | `aletheia/data/ttm_derivation.py:235` | `derive_ttm_from_fmp` | `0.0` | HOT? | _TODO_ | `net_debt = (long_term_debt or 0.0) + (short_term_debt or 0.0) - (cash or 0.0)` |
| 123 | `aletheia/data/ttm_derivation.py:274` | `derive_ttm_from_fmp` | `0.0` | HOT? | _TODO_ | `(total_equity or 0.0)` |
| 124 | `aletheia/data/ttm_derivation.py:275` | `derive_ttm_from_fmp` | `0.0` | WARM? | _TODO_ | `+ (long_term_debt or 0.0)` |
| 125 | `aletheia/data/ttm_derivation.py:276` | `derive_ttm_from_fmp` | `0.0` | WARM? | _TODO_ | `+ (short_term_debt or 0.0)` |
| 126 | `aletheia/data/ttm_derivation.py:277` | `derive_ttm_from_fmp` | `0.0` | WARM? | _TODO_ | `- (cash or 0.0)` |
| 127 | `aletheia/data/utility_taxonomy.py:98` | `capex_from_construction_in_progress` | `0.0` | HOT? | _TODO_ | `completed = ppe_additions_complete or 0.0` |

### `.get()` accessor chains (the falsy-zero accessor itself)

| file:line | function | source |
|---|---|---|
| `aletheia/calculations/_schema_contract.py:107` | `_override_covers_field` | `if field in (record.get("fields") or []):` |
| `aletheia/calculations/derivation_registry.py:945` | `_resolve_input` | `prior = bundle.get("prior_year_inputs") or {}` |
| `aletheia/calculations/derivation_registry.py:952` | `_resolve_input` | `upstream = bundle.get("upstream_inputs") or {}` |
| `aletheia/calculations/derivation_registry.py:960` | `_resolve_input` | `sub = bundle.get(sub_key) or {}` |
| `aletheia/calculations/derivation_registry.py:967` | `_resolve_input` | `upstream = bundle.get("upstream_inputs") or {}` |
| `aletheia/calculations/derivation_registry.py:973` | `_resolve_input` | `config = bundle.get("config_inputs") or {}` |
| `aletheia/calculations/identity_checks.py:230` | `records` | `"period": row.get("period") or "FY",` |
| `aletheia/calculations/identity_checks.py:261` | `xbrl_fact` | `key=lambda u: u.get("end") or u.get("filed") or "",` |
| `aletheia/calculations/specialized_inputs.py:104` | `load_specialized_inputs` | `params=dict(entry.get("params") or {}),` |
| `aletheia/calculations/specialized_inputs.py:107` | `load_specialized_inputs` | `source=entry.get("source") or "",` |
| `aletheia/calculations/specialized_inputs.py:108` | `load_specialized_inputs` | `analyst_notes=entry.get("analyst_notes") or "",` |
| `aletheia/data/cleaning_engine.py:141` | `get` | `return self.clean.get(key) or self.raw.get(key) or fallback` |
| `aletheia/data/cleaning_engine.py:415` | `clean` | `fcf_final = record.derived.get("FCF") or record.clean.get("FCF")` |
| `aletheia/data/cleaning_engine.py:806` | `_domain2_jva_separation` | `clean_ebit = record.clean.get(f"clean_{ebit_key}") or record.clean.get(ebit_key)` |
| `aletheia/data/cleaning_engine.py:858` | `_domain3_ebit_normalization` | `record.clean.get(f"clean_{ebit_key}")` |
| `aletheia/data/cleaning_engine.py:863` | `_domain3_ebit_normalization` | `revenue = record.clean.get("Revenue") or record.raw.get("Revenue")` |
| `aletheia/data/cleaning_engine.py:1046` | `_domain5_lease_normalization` | `ebitda = record.clean.get("EBITDA") or record.derived.get("EBITDA")` |
| `aletheia/data/cleaning_engine.py:1199` | `_domain7_sbc_adjustment` | `fcf = record.derived.get("FCF") or record.clean.get("FCF")` |
| `aletheia/data/cleaning_engine.py:1233` | `_domain7_sbc_adjustment` | `shares_basic = record.raw.get("SharesOutstanding") or record.raw.get("CommonStockSharesOutstanding")` |
| `aletheia/data/cleaning_engine.py:1234` | `_domain7_sbc_adjustment` | `shares_diluted = record.raw.get("SharesDiluted") or record.raw.get("WeightedAverageNumberOfDilutedSharesOutstanding")` |
| `aletheia/data/cleaning_engine.py:1242` | `_domain7_sbc_adjustment` | `diluted_eps = (record.raw.get("DilutedEPS")` |
| `aletheia/data/cleaning_engine.py:1424` | `_domain9_working_capital` | `capex = record.raw.get("CapEx") or record.raw.get("capex") or record.raw.get("PaymentsToAcquirePropertyPlantAndEquipment")` |
| `aletheia/data/cleaning_engine.py:1540` | `_domain10_tax_sustainability` | `nol = record.raw.get("OperatingLossCarryforwards") or record.raw.get("DeferredTaxAssetsOperatingLossCarryforwards")` |
| `aletheia/data/cleaning_engine.py:1678` | `_compute_derived` | `sm = r.raw.get("SellingAndMarketing") or r.raw.get("Marketing")` |
| `aletheia/data/cleaning_engine.py:1712` | `_compute_derived` | `ebit = r.clean.get("NormalizedEBIT") or r.derived.get("OperatingIncome") or r.raw.get("OperatingIncome") or r.raw.get("EBIT")` |
| `aletheia/data/cleaning_engine.py:1729` | `_compute_derived` | `capex_raw = r.clean.get("CapEx_Total") or r.raw.get("CapEx") or r.derived.get("CapEx")` |
| `aletheia/data/cleaning_engine.py:1786` | `_compute_derived` | `adj_val = r.raw.get("FinanceLeasePrincipalPayments") or r.raw.get("RepaymentsOfDebtAndFinanceLeaseObligations")` |
| `aletheia/data/cleaning_engine.py:1820` | `_compute_derived` | `cogs = r.raw.get("CostOfServices") or r.raw.get("MedicalClaims")` |
| `aletheia/data/consensus_estimates.py:135` | `_enrich_with_fmp` | `d = str(e.get("date") or "")` |
| `aletheia/data/fmp_client.py:591` | `get_for_fiscal_year` | `d = r.get("date") or ""` |
| `aletheia/data/fmp_validation.py:273` | `_add_ev_derived_checks` | `ev = (fmp_data or {}).get("enterprise_values") or {}` |
| `aletheia/data/fmp_validation.py:277` | `_add_ev_derived_checks` | `fmp_mc = ev.get("marketCapitalization") or ev.get("marketCap")` |
| `aletheia/data/fmp_validation.py:373` | `_fetch_fmp_for_gate_a` | `fmp_ccy = (inc_fy.get("reportedCurrency") or "").upper()` |
| `aletheia/data/fmp_validation.py:873` | `_add_ttm_ev_identity_check` | `mc = fmp_ev.get("marketCapitalization") or fmp_ev.get("marketCap")` |
| `aletheia/data/fmp_validation.py:973` | `_add_ttm_quarterly_consistency_check` | `end = (rec.get("date") or "")[:10]` |
| `aletheia/data/fmp_validation.py:1007` | `_add_ttm_quarterly_consistency_check` | `end = (q.get("end") or "")[:10]` |
| `aletheia/data/fmp_validation.py:1318` | `_check_scenario_monotonicity` | `p2 = (serving_report.get("4_valuation_synthesis") or {}).get("phase2_valuation") or {}` |
| `aletheia/data/fmp_validation.py:1318` | `_check_scenario_monotonicity` | `p2 = (serving_report.get("4_valuation_synthesis") or {}).get("phase2_valuation") or {}` |
| `aletheia/data/fmp_validation.py:1319` | `_check_scenario_monotonicity` | `dcf3 = p2.get("three_scenario_dcf") or {}` |
| `aletheia/data/fmp_validation.py:1320` | `_check_scenario_monotonicity` | `bear_ips = (dcf3.get("bear") or {}).get("intrinsic_per_share")` |
| `aletheia/data/fmp_validation.py:1321` | `_check_scenario_monotonicity` | `base_ips = (dcf3.get("base") or {}).get("intrinsic_per_share")` |
| `aletheia/data/fmp_validation.py:1322` | `_check_scenario_monotonicity` | `bull_ips = (dcf3.get("bull") or {}).get("intrinsic_per_share")` |
| `aletheia/data/fmp_validation.py:1337` | `_check_narrative_fidelity` | `val = serving_report.get("4_valuation_synthesis") or {}` |
| `aletheia/data/fmp_validation.py:1338` | `_check_narrative_fidelity` | `narr = (val.get("investment_thesis") or {}).get("narrative") or ""` |
| `aletheia/data/fmp_validation.py:1338` | `_check_narrative_fidelity` | `narr = (val.get("investment_thesis") or {}).get("narrative") or ""` |
| `aletheia/data/fmp_validation.py:1339` | `_check_narrative_fidelity` | `p2 = val.get("phase2_valuation") or {}` |
| `aletheia/data/fmp_validation.py:1340` | `_check_narrative_fidelity` | `rdcf = p2.get("reverse_dcf") or {}` |
| `aletheia/data/fmp_validation.py:1342` | `_check_narrative_fidelity` | `hist = rdcf.get("historical_cagr") or rdcf.get("historical_cagr_5y")` |
| `aletheia/data/fmp_validation.py:1408` | `build_receipt_block` | `calc = state.get("_calc_validation") or {"status": "skipped", "skip_reason": "not_run"}` |
| `aletheia/data/fmp_validation_core.py:317` | `validate_ticker` | `raw_json = json.loads(our_row.get("raw_json") or "{}")` |
| `aletheia/data/fmp_validation_core.py:344` | `validate_ticker` | `fmp_ccy = (inc.get("reportedCurrency") or "").upper()` |
| `aletheia/data/quantitative_screens.py:239` | `get` | `v = rec.raw.get(k) or rec.clean.get(k)` |
| `aletheia/data/quantitative_screens.py:433` | `_sloan_accrual_ratio` | `net_income = record.raw.get("NetIncome") or record.raw.get("NetIncomeLoss")` |
| `aletheia/data/quantitative_screens.py:434` | `_sloan_accrual_ratio` | `cash_ops = record.raw.get("OperatingCF") or record.raw.get("NetCashProvidedByUsedInOperatingActivities")` |
| `aletheia/data/quantitative_screens.py:435` | `_sloan_accrual_ratio` | `cash_inv = record.raw.get("InvestingCF") or record.raw.get("NetCashProvidedByUsedInInvestingActivities")` |
| `aletheia/data/quantitative_screens.py:514` | `_earnings_power_value` | `record.clean.get("NormalizedEBIT")` |
| `aletheia/data/quantitative_screens.py:520` | `_earnings_power_value` | `record.raw.get("WeightedAverageNumberOfDilutedSharesOutstanding")` |
| `aletheia/data/sec_xbrl_validator.py:167` | `_read_fact` | `usd = ns[tag].get("units", {}).get("USD") or []` |
| `aletheia/data/sec_xbrl_validator.py:177` | `_read_fact` | `fy=int(fact.get("fy") or fiscal_year),` |
| `aletheia/data/sec_xbrl_validator.py:201` | `lookup_xbrl` | `tag_list = tags or CANONICAL_TAGS.get(field) or []` |
| `aletheia/data/ttm_derivation.py:69` | `_q_date` | `return (record.get("date") or "")[:10]` |
| `aletheia/data/ttm_derivation.py:156` | `derive_ttm_from_fmp` | `fmp_ccy = (income_last4[0].get("reportedCurrency") or "").upper()` |
| `aletheia/data/ttm_derivation.py:157` | `derive_ttm_from_fmp` | `fy_str  = str(income_last4[0].get("fiscalYear") or income_last4[0].get("calendarYear") or "")` |
| `aletheia/data/ttm_derivation.py:285` | `derive_ttm_from_fmp` | `period_end_date = (balance_latest.get("date") or income_last4[0].get("date") or "")[:10] or None` |
| `aletheia/data/ttm_derivation.py:286` | `derive_ttm_from_fmp` | `fy_str = str(income_last4[0].get("fiscalYear") or income_last4[0].get("calendarYear") or "")` |
| `aletheia/data/ttm_derivation.py:376` | `derive_ttm_from_fmp` | `(latest_quarter_income or {}).get("date") or ""` |
