# Identity Audit Findings — 2026-05-13

Phase-1 baseline run of the seven foundational accounting identities across the production universe. This is the findings report only — no fixes have been applied. See [tools/verification/identity_checks.py](../tools/verification/identity_checks.py) for the verification logic.

## Executive summary

- Total checks: **5505**
- Passed: **2184** (39.7%)
- Failed: **2109** (38.3%)
- Skipped (no data): **1212** (22.0%)
- Universe size: **40** tickers
- Git SHA: `ed7009f0128b`

Pass-rate excludes skipped checks (skipped is a tooling/coverage gap, not a data-quality finding).

| Identity | Total | Pass | Fail | Skip | Pass-rate (ex-skip) |
|---|---|---|---|---|---|
| `balance_sheet_equation` | 673 | 615 | 48 | 10 | 92.8% |
| `cash_rollforward` | 599 | 336 | 207 | 56 | 61.9% |
| `debt_rollforward` | 599 | 175 | 372 | 52 | 32.0% |
| `fcf_pathway_reconciliation` | 639 | 171 | 413 | 55 | 29.3% |
| `ppe_rollforward` | 599 | 206 | 316 | 77 | 39.5% |
| `retained_earnings_rollforward` | 599 | 332 | 242 | 25 | 57.8% |
| `working_capital_AP` | 599 | 74 | 119 | 406 | 38.3% |
| `working_capital_AR` | 599 | 146 | 177 | 276 | 45.2% |
| `working_capital_inventory` | 599 | 129 | 215 | 255 | 37.5% |

## Findings by identity

### `balance_sheet_equation` — 48 failure(s), 10 skipped

Top 10 by absolute discrepancy:

| Ticker | FY | Period | Discrepancy ($M) | % | Suggested category |
|---|---|---|---|---|---|
| NEE | 2017 | FY | +31463.0 | +32.16% | C (utility taxonomy — see A19/A15) |
| NEE | 2016 | FY | +27818.0 | +30.91% | C (utility taxonomy — see A19/A15) |
| NEE | 2018 | FY | +27250.0 | +26.28% | C (utility taxonomy — see A19/A15) |
| NEE | 2015 | FY | +26681.0 | +32.35% | C (utility taxonomy — see A19/A15) |
| NEE | 2014 | FY | +24367.0 | +32.52% | C (utility taxonomy — see A19/A15) |
| NEE | 2013 | FY | +23969.0 | +34.58% | C (utility taxonomy — see A19/A15) |
| NEE | 2012 | FY | +23177.0 | +35.97% | C (utility taxonomy — see A19/A15) |
| NEE | 2011 | FY | +20810.0 | +36.39% | C (utility taxonomy — see A19/A15) |
| NEE | 2010 | FY | +18013.0 | +33.99% | C (utility taxonomy — see A19/A15) |
| NEE | 2009 | FY | +16300.0 | +33.64% | C (utility taxonomy — see A19/A15) |

### `cash_rollforward` — 207 failure(s), 56 skipped

Top 10 by absolute discrepancy:

| Ticker | FY | Period | Discrepancy ($M) | % | Suggested category |
|---|---|---|---|---|---|
| JPM | 2017 | FY | +38196.0 | +8.86% | ? |
| JPM | 2016 | FY | +26088.0 | +6.67% | ? |
| AXP | 2010 | FY | -14998.0 | -2293.27% | ? |
| BRK-B | 2016 | FY | -4569.0 | -16.29% | ? |
| NEE | 2018 | FY | -4353.0 | -682.29% | ? |
| AMZN | 2024 | FY | -4331.0 | -5.50% | ? |
| NEE | 2019 | FY | +4111.0 | +685.17% | ? |
| ASML | 2021 | FY | +3144.0 | +38.24% | B (FX effect not captured by cleaner) |
| V | 2022 | FY | -2663.0 | -16.97% | ? |
| HD | 2011 | FY | +2352.0 | +118.37% | ? |

### `debt_rollforward` — 372 failure(s), 52 skipped

Top 10 by absolute discrepancy:

| Ticker | FY | Period | Discrepancy ($M) | % | Suggested category |
|---|---|---|---|---|---|
| JPM | 2014 | FY | -259393.0 | -79.63% | ? |
| JPM | 2015 | FY | -50782.0 | -76.54% | ? |
| JPM | 2017 | FY | +40064.0 | +341.32% | ? |
| KO | 2013 | FY | -38297.0 | -123.78% | ? |
| KO | 2012 | FY | -36392.0 | -141.10% | ? |
| GOOGL | 2025 | FY | +33364.0 | +253.08% | ? |
| MSFT | 2017 | FY | +31490.0 | +58.66% | ? |
| JPM | 2019 | FY | -28356.0 | -40.93% | C (ASC 842 transition) |
| AXP | 2010 | FY | +26900.0 | +49.19% | ? |
| JPM | 2011 | FY | +25374.0 | +8.97% | ? |

### `fcf_pathway_reconciliation` — 413 failure(s), 55 skipped

Top 10 by absolute discrepancy:

| Ticker | FY | Period | Discrepancy ($M) | % | Suggested category |
|---|---|---|---|---|---|
| GOOGL | 2025 | FY | +47416.2 | +64.72% | B/C (SBC + deferred-tax non-cash items) |
| META | 2025 | FY | +34171.0 | +74.11% | B/C (SBC + deferred-tax non-cash items) |
| LOW | 2013 | FY | -28129.1 | -996.78% | B/C (SBC + deferred-tax non-cash items) |
| GOOGL | 2021 | FY | +27562.9 | +41.13% | B/C (SBC + deferred-tax non-cash items) |
| LOW | 2012 | FY | -26622.5 | -1043.61% | B/C (SBC + deferred-tax non-cash items) |
| BRK-B | 2024 | FY | -26445.4 | -227.66% | B/C (SBC + deferred-tax non-cash items) |
| LOW | 2011 | FY | -25450.6 | -1009.94% | B/C (SBC + deferred-tax non-cash items) |
| LOW | 2010 | FY | -25395.9 | -1006.58% | B/C (SBC + deferred-tax non-cash items) |
| GOOGL | 2024 | FY | +24668.9 | +33.90% | B/C (SBC + deferred-tax non-cash items) |
| META | 2023 | FY | +23783.4 | +54.24% | B/C (SBC + deferred-tax non-cash items) |

### `ppe_rollforward` — 316 failure(s), 77 skipped

Top 10 by absolute discrepancy:

| Ticker | FY | Period | Discrepancy ($M) | % | Suggested category |
|---|---|---|---|---|---|
| AMZN | 2025 | FY | +38297.0 | +15.16% | ? |
| MSFT | 2025 | FY | +36232.0 | +26.72% | ? |
| AMZN | 2020 | FY | +25520.0 | +35.10% | ? |
| AMZN | 2021 | FY | +20410.0 | +18.04% | ? |
| AMZN | 2017 | FY | +19275.0 | +66.21% | ? |
| AMZN | 2024 | FY | +18284.0 | +8.95% | ? |
| MSFT | 2024 | FY | +17273.0 | +18.06% | ? |
| AMZN | 2019 | FY | +15836.0 | +25.63% | ? |
| AMZN | 2018 | FY | +14845.0 | +30.38% | ? |
| AMZN | 2023 | FY | +13396.0 | +7.17% | ? |

### `retained_earnings_rollforward` — 242 failure(s), 25 skipped

Top 10 by absolute discrepancy:

| Ticker | FY | Period | Discrepancy ($M) | % | Suggested category |
|---|---|---|---|---|---|
| AAPL | 2024 | FY | -97442.0 | -45533.64% | ? |
| AAPL | 2022 | FY | -93592.0 | -1682.70% | ? |
| AAPL | 2025 | FY | -91699.0 | -478.75% | ? |
| AAPL | 2021 | FY | -89617.0 | -598.80% | ? |
| AAPL | 2023 | FY | -79116.0 | -2578.75% | ? |
| AAPL | 2020 | FY | -74262.0 | -161.80% | ? |
| AAPL | 2018 | FY | -73749.0 | -75.00% | ? |
| AAPL | 2019 | FY | -65639.0 | -93.24% | ? |
| BRK-B | 2018 | FY | +61305.0 | +23.97% | ? |
| GOOGL | 2024 | FY | -58918.0 | -27.89% | ? |

### `working_capital_AP` — 119 failure(s), 406 skipped

Top 10 by absolute discrepancy:

| Ticker | FY | Period | Discrepancy ($M) | % | Suggested category |
|---|---|---|---|---|---|
| AMZN | 2025 | FY | -16315.0 | -59.23% | C (likely M&A / WC reclassification) |
| AMZN | 2020 | FY | -7876.0 | -31.06% | C (likely M&A / WC reclassification) |
| AAPL | 2019 | FY | +7729.0 | -80.08% | C (likely M&A / WC reclassification) |
| AMZN | 2024 | FY | -6410.0 | -68.32% | C (likely M&A / WC reclassification) |
| MSFT | 2025 | FY | -5159.0 | -90.07% | C (likely M&A / WC reclassification) |
| WMT | 2021 | FY | +4798.0 | +221.31% | C (likely M&A / WC reclassification) |
| GOOGL | 2025 | FY | -3306.0 | -78.47% | C (likely M&A / WC reclassification) |
| WMT | 2026 | FY | -2784.0 | -63.34% | C (likely M&A / WC reclassification) |
| AMZN | 2021 | FY | -2523.0 | -41.19% | C (likely M&A / WC reclassification) |
| AAPL | 2018 | FY | +2336.0 | +34.16% | C (likely M&A / WC reclassification) |

### `working_capital_AR` — 177 failure(s), 276 skipped

Top 10 by absolute discrepancy:

| Ticker | FY | Period | Discrepancy ($M) | % | Suggested category |
|---|---|---|---|---|---|
| ABT | 2013 | FY | +3739.9 | -103.12% | C (likely M&A / WC reclassification) |
| MSFT | 2018 | FY | -2827.0 | -42.26% | C (likely M&A / WC reclassification) |
| MSFT | 2025 | FY | -2400.0 | -18.49% | C (likely M&A / WC reclassification) |
| ACN | 2019 | FY | -1944.6 | -78.70% | C (likely M&A / WC reclassification) |
| JNJ | 2023 | FY | +1911.0 | -148.48% | C (likely M&A / WC reclassification) |
| ABT | 2017 | FY | -1794.0 | -89.66% | C (likely M&A / WC reclassification) |
| GOOGL | 2025 | FY | -1767.0 | -16.76% | C (likely M&A / WC reclassification) |
| UNH | 2015 | FY | -1680.0 | -73.98% | C (likely M&A / WC reclassification) |
| GOOGL | 2024 | FY | +1515.0 | +34.62% | C (likely M&A / WC reclassification) |
| GOOGL | 2022 | FY | +1363.0 | +142.87% | C (likely M&A / WC reclassification) |

### `working_capital_inventory` — 215 failure(s), 255 skipped

Top 10 by absolute discrepancy:

| Ticker | FY | Period | Discrepancy ($M) | % | Suggested category |
|---|---|---|---|---|---|
| TSLA | 2021 | FY | -3365.0 | -203.20% | C (likely M&A / WC reclassification) |
| JNJ | 2023 | FY | +2625.0 | -201.61% | C (likely M&A / WC reclassification) |
| CAT | 2011 | FY | -2030.0 | -40.95% | C (likely M&A / WC reclassification) |
| LOW | 2022 | FY | +1667.0 | +179.83% | C (likely M&A / WC reclassification) |
| LLY | 2025 | FY | -1483.8 | -24.11% | C (likely M&A / WC reclassification) |
| ABT | 2017 | FY | -1416.0 | -121.34% | C (likely M&A / WC reclassification) |
| ABT | 2013 | FY | +1253.3 | -114.01% | C (likely M&A / WC reclassification) |
| JNJ | 2012 | FY | -1209.0 | -99.92% | C (likely M&A / WC reclassification) |
| JNJ | 2017 | FY | -1202.0 | -193.56% | C (likely M&A / WC reclassification) |
| LLY | 2019 | FY | +1179.8 | -128.09% | C (likely M&A / WC reclassification) |

## Findings by ticker

Tickers with at least one failure, ordered by failure count.

| Ticker | Failures | Total checks | Failing identities |
|---|---|---|---|
| MSFT | 85 | 138 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP, working_capital_AR, working_capital_inventory |
| UNH | 82 | 146 | balance_sheet_equation, cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AR |
| JNJ | 81 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AR, working_capital_inventory |
| ABT | 79 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AR, working_capital_inventory |
| CAT | 74 | 147 | balance_sheet_equation, cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP, working_capital_inventory |
| ACN | 72 | 138 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP, working_capital_AR |
| AMZN | 72 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP, working_capital_AR, working_capital_inventory |
| ORCL | 71 | 138 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AR, working_capital_inventory |
| LLY | 69 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_inventory |
| KO | 68 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AR, working_capital_inventory |
| WMT | 68 | 147 | balance_sheet_equation, cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP |
| EMR | 67 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP, working_capital_inventory |
| TSLA | 65 | 129 | balance_sheet_equation, cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP, working_capital_AR, working_capital_inventory |
| MRK | 64 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, working_capital_AR, working_capital_inventory |
| AAPL | 60 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP, working_capital_AR, working_capital_inventory |
| V | 60 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP, working_capital_AR |
| COST | 59 | 138 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP, working_capital_inventory |
| QCOM | 59 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_inventory |
| ITW | 55 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_inventory |
| META | 52 | 120 | cash_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP, working_capital_AR |
| LOW | 51 | 138 | balance_sheet_equation, cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_inventory |
| PG | 51 | 138 | debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, working_capital_AP, working_capital_AR, working_capital_inventory |
| AMD | 50 | 138 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, working_capital_AP, working_capital_AR, working_capital_inventory |
| NSC | 50 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, retained_earnings_rollforward, working_capital_AR |
| MCO | 48 | 147 | balance_sheet_equation, cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AR |
| ASML | 47 | 129 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward |
| PEP | 44 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, working_capital_inventory |
| NVDA | 42 | 138 | balance_sheet_equation, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP, working_capital_AR, working_capital_inventory |
| GOOGL | 41 | 93 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, retained_earnings_rollforward, working_capital_AP, working_capital_AR |
| CNC | 40 | 137 | balance_sheet_equation, cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward |
| NEE | 40 | 146 | balance_sheet_equation, cash_rollforward, debt_rollforward, working_capital_AR, working_capital_inventory |
| MDT | 38 | 93 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_inventory |
| AXP | 36 | 146 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward |
| TXN | 34 | 147 | debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, working_capital_AR, working_capital_inventory |
| SMCI | 31 | 111 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward, retained_earnings_rollforward, working_capital_AP, working_capital_AR, working_capital_inventory |
| HD | 30 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, ppe_rollforward |
| UNP | 29 | 147 | cash_rollforward, debt_rollforward, fcf_pathway_reconciliation, retained_earnings_rollforward |
| BRK-B | 17 | 146 | cash_rollforward, fcf_pathway_reconciliation, retained_earnings_rollforward, working_capital_inventory |
| JPM | 17 | 146 | cash_rollforward, debt_rollforward, retained_earnings_rollforward |
| TSM | 11 | 66 | balance_sheet_equation, cash_rollforward, fcf_pathway_reconciliation |

## Recommended actions

Each finding above carries a *suggested* category — these are heuristic, not authoritative. The next step is analyst classification per the Category A/B/C/D scheme:

- **A — Source data quality**: SEC/FMP source is wrong. Fix at source or via override registry.
- **B — Cleaning engine gap**: Source is correct, cleaner doesn't handle this case. Extend cleaner.
- **C — Legitimate complexity**: M&A / FX / accounting change. Document as expected exception.
- **D — Methodology divergence**: Team decision required on correct approach.

Next steps:

1. Walk each failing identity's top-10 list and assign a category to each finding.
2. For each Category B finding, open a follow-up to extend the cleaning engine.
3. For each Category C finding, add an entry to `docs/data_quality_exceptions.md` (also new).
4. For Category A findings, evaluate whether the override registry or source-correction is the right path.
5. Re-run this audit after each batch of fixes; expect skip-rate to drop as Category B coverage extends.