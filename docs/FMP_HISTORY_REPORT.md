# FMP Historical Validation Report

**Generated:** 2026-05-06  
**Scope:** Last 5 fiscal years per ticker, FMP statements + derived ratios (screening section is latest-FY only and not included here)  
**Cells per ticker per year:** 34 (9 income + 14 balance + 5 cash-flow + 6 derived)  


## What this catches that single-FY doesn't

The single-FY validator (`scripts/validate_fmp.py`) tells you whether the
latest cleaned record matches FMP. The historical validator tells you
whether the cleaning pipeline produces *consistent* output across years.
Use it to catch:

- **Tag-mapping regressions.** A filer changes which us-gaap tag they use
  between FY filings. The single-FY check passes for the new year but the
  prior years show drift you didn't see at clean-time.
- **Restatement leakage.** When an issuer restates prior-year numbers, FMP
  picks up the restatement quickly but our cached cleaned record is from
  the original filing. Drift increases for restated FYs.
- **Cleaning fix backports.** A fix shipped for the latest FY may not
  apply cleanly to the way the same field was filed 3-4 years ago — older
  years can drift more than newer ones.

A ticker with a flat ~85% pass rate across 5 years is more trustworthy
than one that's 95% on the latest FY but 60% three years back. The "YoY
spread" column in the rollup highlights tickers where this is happening.

## What's not covered

This validator skips the screening section (`P/E`, `EV/EBITDA`, etc.)
because the screening engine runs only on the latest FY in the cleaned
DataFrame. Historical screening would require running the screening
pipeline per-FY, which the engine wasn't designed for. The drift patterns
on screening multiples are price-timing-driven anyway and don't reveal
filing-level issues.


## Per-ticker stability rollup

| Ticker | Years | Avg pass rate | Worst FY | Best FY | YoY spread |
|---|---:|---:|---:|---:|---:|
| AAPL | 5 | 86.5% | 85.3% | 88.2% | 2.9pp |
| MSFT | 5 | 88.2% | 88.2% | 88.2% | 0.0pp |
| LLY | 5 | 74.7% | 67.6% | 82.4% | 14.7pp |
| COST | 5 | 87.6% | 82.4% | 91.2% | 8.8pp |
| ASML | 0 | _no validation_ |  |  |  |
| SMCI | 5 | 85.9% | 82.4% | 88.2% | 5.9pp |
| GOOGL | 5 | 87.1% | 82.4% | 94.1% | 11.8pp |
| ABT | 5 | 77.1% | 73.5% | 82.4% | 8.8pp |
| AMD | 5 | 81.8% | 79.4% | 85.3% | 5.9pp |
| AMZN | 5 | 76.5% | 73.5% | 79.4% | 5.9pp |
| BRK-B | 5 | 40.6% | 38.2% | 41.2% | 2.9pp |
| CAT | 5 | 77.6% | 76.5% | 79.4% | 2.9pp |
| CNC | 5 | 65.9% | 58.8% | 70.6% | 11.8pp |
| JPM | 5 | 41.8% | 38.2% | 44.1% | 5.9pp |
| META | 5 | 85.9% | 79.4% | 88.2% | 8.8pp |
| NEE | 5 | 60.6% | 55.9% | 67.6% | 11.8pp |
| NVDA | 5 | 90.6% | 82.4% | 94.1% | 11.8pp |
| ORCL | 5 | 71.2% | 67.6% | 76.5% | 8.8pp |
| QCOM | 5 | 81.2% | 73.5% | 88.2% | 14.7pp |
| TSLA | 5 | 84.7% | 79.4% | 88.2% | 8.8pp |
| TSM | 0 | _no validation_ |  |  |  |
| TXN | 5 | 89.4% | 85.3% | 94.1% | 8.8pp |
| UNH | 5 | 70.0% | 61.8% | 79.4% | 17.6pp |
| V | 5 | 70.6% | 64.7% | 73.5% | 8.8pp |
| WMT | 5 | 77.6% | 70.6% | 82.4% | 11.8pp |
| KO | 5 | 72.9% | 70.6% | 73.5% | 2.9pp |
| PEP | 5 | 80.0% | 76.5% | 82.4% | 5.9pp |
| PG | 5 | 86.5% | 82.4% | 88.2% | 5.9pp |
| JNJ | 5 | 71.8% | 55.9% | 82.4% | 26.5pp |
| MRK | 5 | 85.9% | 73.5% | 91.2% | 17.6pp |
| MDT | 5 | 90.6% | 88.2% | 94.1% | 5.9pp |
| HD | 5 | 87.1% | 85.3% | 88.2% | 2.9pp |
| LOW | 5 | 76.5% | 67.6% | 85.3% | 17.6pp |
| UNP | 5 | 75.3% | 73.5% | 79.4% | 5.9pp |
| NSC | 5 | 74.1% | 67.6% | 76.5% | 8.8pp |
| ITW | 5 | 84.7% | 79.4% | 88.2% | 8.8pp |
| EMR | 5 | 61.8% | 44.1% | 70.6% | 26.5pp |
| MCO | 5 | 75.9% | 70.6% | 79.4% | 8.8pp |
| AXP | 5 | 56.5% | 55.9% | 58.8% | 2.9pp |
| ACN | 5 | 76.5% | 70.6% | 82.4% | 11.8pp |

## Per-ticker per-year detail (✓ / total per FY)

| Ticker | FY2026 | FY2025 | FY2024 | FY2023 | FY2022 | FY2021 | FY2020 |
|---|---|---|---|---|---|---|---|
| AAPL | — | 30/34 (88%) | 30/34 (88%) | 27/34 (85%) | 27/34 (85%) | 27/34 (85%) | — |
| MSFT | — | 30/34 (88%) | 28/34 (88%) | 28/34 (88%) | 28/34 (88%) | 28/34 (88%) | — |
| LLY | — | 25/34 (79%) | 23/34 (68%) | 25/34 (74%) | 21/34 (71%) | 20/34 (82%) | — |
| COST | — | 29/34 (91%) | 28/34 (82%) | 28/34 (88%) | 28/34 (88%) | 26/34 (88%) | — |
| ASML | — | err | err | err | err | err | — |
| SMCI | — | 28/34 (88%) | 26/34 (85%) | 28/34 (85%) | 25/34 (88%) | 26/34 (82%) | — |
| GOOGL | — | 29/34 (85%) | 28/34 (82%) | 28/34 (94%) | 29/34 (91%) | 27/34 (82%) | — |
| ABT | — | 22/34 (82%) | 25/34 (76%) | 25/34 (74%) | 23/34 (79%) | 23/34 (74%) | — |
| AMD | — | 28/34 (85%) | 26/34 (79%) | 27/34 (79%) | 28/34 (82%) | 28/34 (82%) | — |
| AMZN | — | 24/34 (74%) | 24/34 (79%) | 24/34 (79%) | 25/34 (74%) | 26/34 (76%) | — |
| BRK-B | — | 13/34 (38%) | 14/34 (41%) | 12/34 (41%) | 12/34 (41%) | 12/34 (41%) | — |
| CAT | — | 23/34 (76%) | 23/34 (79%) | 23/34 (79%) | 23/34 (76%) | 23/34 (76%) | — |
| CNC | — | 16/34 (59%) | 24/34 (71%) | 23/34 (68%) | 23/34 (68%) | 22/34 (65%) | — |
| JPM | — | 13/34 (38%) | 14/34 (41%) | 14/34 (41%) | 14/34 (44%) | 14/34 (44%) | — |
| META | — | 28/34 (88%) | 28/34 (88%) | 27/34 (85%) | 27/34 (79%) | 28/34 (88%) | — |
| NEE | — | 21/34 (62%) | 21/34 (62%) | 21/34 (68%) | 19/34 (56%) | 19/34 (56%) | — |
| NVDA | 27/34 (82%) | 29/34 (94%) | 28/34 (91%) | 27/34 (91%) | 28/34 (94%) | — | — |
| ORCL | — | 21/34 (76%) | 21/34 (71%) | 22/34 (68%) | 22/34 (68%) | 23/34 (74%) | — |
| QCOM | — | 24/34 (74%) | 26/34 (82%) | 26/34 (76%) | 27/34 (85%) | 30/34 (88%) | — |
| TSLA | — | 27/34 (88%) | 28/34 (85%) | 27/34 (85%) | 27/34 (85%) | 25/34 (79%) | — |
| TSM | — | — | err | err | err | err | err |
| TXN | — | 28/34 (94%) | 28/34 (88%) | 28/34 (85%) | 28/34 (91%) | 28/34 (88%) | — |
| UNH | — | 21/34 (62%) | 22/34 (65%) | 23/34 (68%) | 25/34 (79%) | 24/34 (76%) | — |
| V | — | 20/34 (65%) | 23/34 (74%) | 23/34 (74%) | 24/34 (71%) | 22/34 (71%) | — |
| WMT | 26/34 (76%) | 28/34 (82%) | 24/34 (71%) | 26/34 (82%) | 26/34 (76%) | — | — |
| KO | — | 25/34 (74%) | 24/34 (71%) | 25/34 (74%) | 24/34 (74%) | 24/34 (74%) | — |
| PEP | — | 24/34 (76%) | 27/34 (82%) | 25/34 (82%) | 26/34 (82%) | 26/34 (76%) | — |
| PG | — | 26/34 (82%) | 26/34 (88%) | 26/34 (85%) | 26/34 (88%) | 28/34 (88%) | — |
| JNJ | — | 23/34 (74%) | 23/34 (76%) | 27/34 (82%) | 16/34 (56%) | 21/34 (71%) | — |
| MRK | — | 22/34 (74%) | 28/34 (91%) | 29/34 (91%) | 29/34 (91%) | 27/34 (82%) | — |
| MDT | — | 28/34 (91%) | 28/34 (94%) | 31/34 (91%) | 29/34 (88%) | 28/34 (88%) | — |
| HD | — | 29/34 (85%) | 27/34 (85%) | 29/34 (88%) | 29/34 (88%) | 30/34 (88%) | — |
| LOW | — | 26/34 (79%) | 21/34 (76%) | 19/34 (68%) | 20/34 (74%) | 21/34 (85%) | — |
| UNP | — | 23/34 (74%) | 24/34 (79%) | 22/34 (76%) | 22/34 (74%) | 22/34 (74%) | — |
| NSC | — | 23/34 (74%) | 21/34 (68%) | 22/34 (76%) | 24/34 (76%) | 22/34 (76%) | — |
| ITW | — | 27/34 (85%) | 24/34 (79%) | 24/34 (88%) | 25/34 (82%) | 25/34 (88%) | — |
| EMR | — | 23/34 (71%) | 21/34 (65%) | 21/34 (65%) | 13/34 (44%) | 22/34 (65%) | — |
| MCO | — | 22/34 (76%) | 20/34 (79%) | 19/34 (79%) | 21/34 (71%) | 23/34 (74%) | — |
| AXP | — | 19/34 (56%) | 19/34 (56%) | 18/34 (56%) | 20/34 (59%) | 18/34 (56%) | — |
| ACN | — | 24/34 (76%) | 22/34 (76%) | 22/34 (71%) | 24/34 (82%) | 24/34 (76%) | — |

## Tickers with biggest year-over-year drift in pass rate

Large spread suggests either a tag-mapping change between filings or a cleaning-engine fix that improved one year but not others — worth a closer look.

| Ticker | Avg pass rate | YoY spread |
|---|---:|---:|
| EMR | 61.8% | 26.5pp |
| JNJ | 71.8% | 26.5pp |
| UNH | 70.0% | 17.6pp |
| MRK | 85.9% | 17.6pp |
| LOW | 76.5% | 17.6pp |
| LLY | 74.7% | 14.7pp |
| QCOM | 81.2% | 14.7pp |
| GOOGL | 87.1% | 11.8pp |