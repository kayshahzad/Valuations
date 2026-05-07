# SEC XBRL Validation Report

**Generated:** 2026-05-06  
**Source:** `valuation_data/raw/sec/companyfacts/CIK*.json` (per-filer SEC bulk XBRL)  
**Tolerance:** ✓ <1%, ≈ 1-5%, ✗ >5%  


## What this validates

The validator pulls the canonical us-gaap (or IFRS-full) tagged value
straight from each filer's SEC XBRL companyfacts and compares it byte-for-
byte to our cleaned `raw_<field>`. Because both sides ultimately come from
the same 10-K filing, byte-perfect agreement is expected; any drift means
either:

1. Our tag resolver picked a tag the issuer no longer files, leaving us with
   a stale or null value (look at fields with `ours_missing`).
2. The issuer files under a non-canonical tag name we haven't mapped (for
   example `RegulatedAndUnregulatedOperatingRevenue` for utilities).
3. The issuer files multiple variants of the same concept (e.g., Revenue
   tagged twice with slightly different scopes — common in 20-F filings) and
   we picked a different variant than the filer's primary.

This is structurally different from FMP validation: FMP applies its own
normalization (aggregating receivables, bundling lease ROU into PPE,
adding SBC back into EBITDA), so drift there is almost always a documented
normalization difference. SEC drift, in contrast, is almost always either
a tag-mapping fix or a known multi-variant disclosure.

## Coverage

This source closes the FMP-restricted gap: every SEC filer in our universe
has a companyfacts file, including the 11 tickers FMP's free tier blocks
(LLY, ASML, SMCI, ABT, BRK-B, CAT, CNC, NEE, ORCL, QCOM, TXN). It is also
the first authoritative validation for the bank (JPM) and 20-F filers
(ASML, TSM) where FMP either reclassifies (banks) or returns local-currency
statements (TSM in TWD).


## Summary by ticker

| Ticker | FY | ✓ | ≈ | ✗ | missing | total |
|---|---|---:|---:|---:|---:|---:|
| AAPL | 2025 | 8 | 0 | 0 | 0 | 8 |
| MSFT | 2025 | 8 | 0 | 0 | 0 | 8 |
| LLY | 2025 | 8 | 0 | 0 | 0 | 8 |
| COST | 2025 | 8 | 0 | 0 | 0 | 8 |
| ASML | 2025 | 0 | 0 | 0 | 8 | 8 |
| SMCI | 2025 | 7 | 0 | 0 | 1 | 8 |
| GOOGL | 2025 | 8 | 0 | 0 | 0 | 8 |
| ABT | 2025 | 6 | 2 | 0 | 0 | 8 |
| AMD | 2025 | 8 | 0 | 0 | 0 | 8 |
| AMZN | 2025 | 8 | 0 | 0 | 0 | 8 |
| BRK-B | 2025 | 5 | 0 | 1 | 2 | 8 |
| CAT | 2025 | 8 | 0 | 0 | 0 | 8 |
| CNC | 2025 | 7 | 0 | 1 | 0 | 8 |
| JPM | 2025 | 6 | 0 | 0 | 2 | 8 |
| META | 2025 | 8 | 0 | 0 | 0 | 8 |
| NEE | 2025 | 6 | 0 | 2 | 0 | 8 |
| NVDA | 2026 | 8 | 0 | 0 | 0 | 8 |
| ORCL | 2025 | 6 | 1 | 0 | 1 | 8 |
| QCOM | 2025 | 8 | 0 | 0 | 0 | 8 |
| TSLA | 2025 | 8 | 0 | 0 | 0 | 8 |
| TSM | 2024 | 0 | 4 | 0 | 4 | 8 |
| TXN | 2025 | 8 | 0 | 0 | 0 | 8 |
| UNH | 2025 | 8 | 0 | 0 | 0 | 8 |
| V | 2025 | 8 | 0 | 0 | 0 | 8 |
| WMT | 2026 | 6 | 1 | 1 | 0 | 8 |
| KO | 2025 | 5 | 1 | 1 | 1 | 8 |
| PEP | 2025 | 8 | 0 | 0 | 0 | 8 |
| PG | 2025 | 7 | 0 | 0 | 1 | 8 |
| JNJ | 2025 | 8 | 0 | 0 | 0 | 8 |
| MRK | 2025 | 8 | 0 | 0 | 0 | 8 |
| MDT | 2025 | 7 | 0 | 0 | 1 | 8 |
| HD | 2025 | 8 | 0 | 0 | 0 | 8 |
| LOW | 2025 | 8 | 0 | 0 | 0 | 8 |
| UNP | 2025 | 8 | 0 | 0 | 0 | 8 |
| NSC | 2025 | 7 | 0 | 0 | 1 | 8 |
| ITW | 2025 | 7 | 0 | 0 | 1 | 8 |
| EMR | 2025 | 5 | 0 | 2 | 1 | 8 |
| MCO | 2025 | 7 | 1 | 0 | 0 | 8 |
| AXP | 2025 | 7 | 0 | 0 | 1 | 8 |
| ACN | 2025 | 3 | 2 | 2 | 1 | 8 |

---
## AAPL FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    416.16B | $    416.16B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $    112.01B | $    112.01B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    359.24B | $    359.24B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $    285.51B | $    285.51B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     73.73B | $     73.73B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $     35.93B | $     35.93B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     78.33B | $     78.33B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $    111.48B | $    111.48B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## MSFT FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    281.72B | $    281.72B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $    101.83B | $    101.83B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    619.00B | $    619.00B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $    275.52B | $    275.52B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $    343.48B | $    343.48B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $     30.24B | $     30.24B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     40.15B | $     40.15B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $    136.16B | $    136.16B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## LLY FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     65.18B | $     65.18B | +0.00% | ✓ | `Revenues` |
| Net Income | $     20.64B | $     20.64B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    112.48B | $    112.48B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     85.94B | $     85.94B | +0.00% | ✓ | `(derived: Assets − StockholdersEquity)` |
| Total Equity | $     26.54B | $     26.54B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $      7.27B | $      7.27B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     40.87B | $     40.87B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $     16.81B | $     16.81B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## COST FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    275.24B | $    275.24B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $      8.10B | $      8.10B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     77.10B | $     77.10B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     47.94B | $     47.94B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     29.16B | $     29.16B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $     14.16B | $     14.16B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $      5.71B | $      5.71B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $     13.34B | $     13.34B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## ASML FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | — | $     35.77B | — | sec_missing | `-` |
| Net Income | — | $     10.52B | — | sec_missing | `-` |
| Total Assets | — | $     55.37B | — | sec_missing | `-` |
| Total Liabilities | — | $     33.90B | — | sec_missing | `-` |
| Total Equity | — | $     21.48B | — | sec_missing | `-` |
| Cash | — | $     14.14B | — | sec_missing | `-` |
| Long-Term Debt | — | $      2.97B | — | sec_missing | `-` |
| Operating CF | — | $     13.86B | — | sec_missing | `-` |

---
## SMCI FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     21.97B | $     21.97B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $      1.05B | $      1.05B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     14.02B | $     14.02B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $      7.72B | $      7.72B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $      6.30B | $      6.30B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $      5.17B | $      5.17B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | — | $      4.65B | — | sec_missing | `-` |
| Operating CF | $      1.66B | $      1.66B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## GOOGL FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    402.84B | $    402.84B | +0.00% | ✓ | `Revenues` |
| Net Income | $    132.17B | $    132.17B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    595.28B | $    595.28B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $    180.02B | $    180.02B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $    415.26B | $    415.26B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $     30.71B | $     30.71B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     46.55B | $     46.55B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $    164.71B | $    164.71B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## ABT FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     44.33B | $     44.33B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $      6.52B | $      6.52B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     86.71B | $     86.71B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     34.58B | $     33.94B | -1.85% | ≈ | `(derived: Assets − StockholdersEquity)` |
| Total Equity | $     52.13B | $     52.77B | +1.23% | ≈ | `StockholdersEquity` |
| Cash | $      8.52B | $      8.52B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $      9.90B | $      9.90B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $      9.57B | $      9.57B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## AMD FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     34.64B | $     34.64B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $      4.33B | $      4.33B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     76.93B | $     76.93B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     13.93B | $     13.93B | +0.00% | ✓ | `(derived: Assets − StockholdersEquity)` |
| Total Equity | $     63.00B | $     63.00B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $      5.54B | $      5.54B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $      2.35B | $      2.35B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $      7.71B | $      7.71B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## AMZN FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    716.92B | $    716.92B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $     77.67B | $     77.67B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    818.04B | $    818.04B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $    406.98B | $    406.98B | +0.00% | ✓ | `(derived: Assets − StockholdersEquity)` |
| Total Equity | $    411.06B | $    411.06B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $     86.81B | $     86.81B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     65.65B | $     65.65B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $    139.51B | $    139.51B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## BRK-B FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    247.24B | $    371.44B | +50.23% | ✗ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $     66.97B | $     66.97B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $  1,222.18B | $  1,222.18B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $    502.47B | $    502.47B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $    717.42B | $    719.70B | +0.32% | ✓ | `StockholdersEquity` |
| Cash | $     52.57B | — | — | ours_missing | `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` |
| Long-Term Debt | — | — | — | — | `-` |
| Operating CF | $     45.97B | $     45.97B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## CAT FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     67.59B | $     67.59B | +0.00% | ✓ | `Revenues` |
| Net Income | $      8.88B | $      8.88B | +0.00% | ✓ | `ProfitLoss` |
| Total Assets | $     98.58B | $     98.58B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     77.27B | $     77.27B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     21.32B | $     21.32B | +0.00% | ✓ | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| Cash | $      9.98B | $      9.98B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     30.70B | $     30.70B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $     11.74B | $     11.74B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## CNC FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    174.58B | $    194.78B | +11.57% | ✗ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $     -6.67B | $     -6.67B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     76.75B | $     76.75B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     56.69B | $     56.69B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     19.95B | $     20.03B | +0.40% | ✓ | `StockholdersEquity` |
| Cash | $     17.89B | $     17.89B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     17.35B | $     17.35B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $      5.09B | $      5.09B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## JPM FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    182.45B | $    182.45B | +0.00% | ✓ | `Revenues` |
| Net Income | $     57.05B | $     57.05B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $  4,424.90B | $  4,424.90B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $  4,062.46B | $  4,062.46B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $    362.44B | $    362.44B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $    343.34B | — | — | ours_missing | `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` |
| Long-Term Debt | — | — | — | — | `-` |
| Operating CF | $   -147.78B | $   -147.78B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## META FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    200.97B | $    200.97B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $     60.46B | $     60.46B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    366.02B | $    366.02B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $    148.78B | $    148.78B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $    217.24B | $    217.24B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $     35.87B | $     35.87B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     58.74B | $     58.74B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $    115.80B | $    115.80B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## NEE FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     25.80B | $     27.41B | +6.25% | ✗ | `RevenueFromContractWithCustomerIncludingAssessedTax` |
| Net Income | $      6.83B | $      6.83B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    212.72B | $    212.72B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $    146.24B | $    146.24B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     54.61B | $     66.48B | +21.74% | ✗ | `StockholdersEquity` |
| Cash | $      2.81B | $      2.81B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     89.56B | $     89.56B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $     12.48B | $     12.48B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## NVDA FY2026

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    215.94B | $    215.94B | +0.00% | ✓ | `Revenues` |
| Net Income | $    120.07B | $    120.07B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    206.80B | $    206.80B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     49.51B | $     49.51B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $    157.29B | $    157.29B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $     10.61B | $     10.61B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $      7.47B | $      7.47B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $    102.72B | $    102.72B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## ORCL FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     57.40B | $     57.40B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $     12.44B | $     12.44B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    168.36B | $    168.36B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $    147.91B | $    147.39B | -0.35% | ✓ | `(derived: Assets − StockholdersEquity)` |
| Total Equity | $     20.45B | $     20.97B | +2.53% | ≈ | `StockholdersEquity` |
| Cash | $     10.79B | $     10.79B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | — | — | — | — | `-` |
| Operating CF | $     20.82B | $     20.82B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## QCOM FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     44.28B | $     44.28B | +0.00% | ✓ | `Revenues` |
| Net Income | $      5.54B | $      5.54B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     50.14B | $     50.14B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     28.94B | $     28.94B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     21.21B | $     21.21B | +0.00% | ✓ | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| Cash | $      5.52B | $      5.52B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     14.81B | $     14.81B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $     14.01B | $     14.01B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## TSLA FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     94.83B | $     94.83B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $      3.79B | $      3.79B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    137.81B | $    137.81B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     54.94B | $     54.94B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     82.14B | $     82.81B | +0.82% | ✓ | `StockholdersEquity` |
| Cash | $     16.51B | $     16.51B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $      6.58B | $      6.58B | +0.00% | ✓ | `LongTermDebt` |
| Operating CF | $     14.75B | $     14.75B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## TSM FY2024

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     88.27B | $     90.27B | +2.27% | ≈ | `Revenue` |
| Net Income | $     35.30B | $     36.10B | +2.27% | ≈ | `ProfitLoss` |
| Total Assets | $    204.08B | $    208.72B | +2.27% | ≈ | `Assets` |
| Total Liabilities | $     73.57B | $     75.25B | +2.27% | ≈ | `Liabilities` |
| Total Equity | — | $    132.38B | — | sec_missing | `-` |
| Cash | — | $     66.36B | — | sec_missing | `-` |
| Long-Term Debt | — | — | — | — | `-` |
| Operating CF | — | $     56.96B | — | sec_missing | `-` |

---
## TXN FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     17.68B | $     17.68B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $      5.00B | $      5.00B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     34.59B | $     34.59B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     18.31B | $     18.31B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     16.27B | $     16.27B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $      3.23B | $      3.23B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     13.55B | $     13.55B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $      7.15B | $      7.15B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## UNH FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    447.57B | $    447.57B | +0.00% | ✓ | `Revenues` |
| Net Income | $     12.06B | $     12.06B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    309.58B | $    309.58B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $    207.88B | $    207.88B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $    100.09B | $    100.09B | +0.00% | ✓ | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| Cash | $     24.36B | $     24.36B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     72.32B | $     72.32B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $     19.70B | $     19.70B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## V FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     40.00B | $     40.00B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $     20.06B | $     20.06B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     99.63B | $     99.63B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     61.72B | $     61.72B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     37.91B | $     37.91B | +0.00% | ✓ | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| Cash | $     17.16B | $     17.16B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     19.60B | $     19.60B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $     23.06B | $     23.06B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## WMT FY2026

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    706.41B | $    713.16B | +0.96% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $     21.89B | $     21.89B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    284.67B | $    284.67B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $    185.05B | $    178.78B | -3.39% | ≈ | `(derived: Assets − StockholdersEquity)` |
| Total Equity | $     99.62B | $    105.89B | +6.29% | ✗ | `StockholdersEquity` |
| Cash | $     10.73B | $     10.73B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     34.62B | $     34.62B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $     41.56B | $     41.56B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## KO FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     47.94B | $     47.94B | +0.00% | ✓ | `Revenues` |
| Net Income | $     13.11B | $     13.11B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    104.82B | $    104.82B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     72.65B | $     70.54B | -2.90% | ≈ | `(derived: Assets − StockholdersEquity)` |
| Total Equity | $     32.17B | $     34.27B | +6.55% | ✗ | `StockholdersEquity` |
| Cash | $     10.27B | $     10.27B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | — | $     42.12B | — | sec_missing | `-` |
| Operating CF | $      7.41B | $      7.41B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## PEP FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     93.92B | $     93.92B | +0.00% | ✓ | `Revenues` |
| Net Income | $      8.24B | $      8.24B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    107.40B | $    107.40B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     86.85B | $     86.85B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     20.41B | $     20.55B | +0.69% | ✓ | `StockholdersEquity` |
| Cash | $      9.16B | $      9.16B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     42.32B | $     42.32B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $     12.09B | $     12.09B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## PG FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     84.28B | $     84.28B | +0.00% | ✓ | `Revenues` |
| Net Income | $     15.97B | $     15.97B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    125.23B | $    125.23B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     72.95B | $     72.95B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     52.28B | $     52.28B | +0.00% | ✓ | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| Cash | $      9.56B | — | — | ours_missing | `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` |
| Long-Term Debt | $     25.00B | $     25.00B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $     17.82B | $     17.82B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## JNJ FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     94.19B | $     94.19B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $     26.80B | $     26.80B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    199.21B | $    199.21B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $    117.67B | $    117.67B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     81.54B | $     81.54B | +0.00% | ✓ | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| Cash | $     19.71B | $     19.71B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     39.44B | $     39.44B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $     24.53B | $     24.53B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## MRK FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     65.01B | $     65.01B | +0.00% | ✓ | `Revenues` |
| Net Income | $     18.25B | $     18.25B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    136.87B | $    136.87B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     84.26B | $     84.20B | -0.07% | ✓ | `(derived: Assets − StockholdersEquity)` |
| Total Equity | $     52.61B | $     52.66B | +0.11% | ✓ | `StockholdersEquity` |
| Cash | $     14.56B | $     14.56B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     46.75B | $     46.75B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $     16.47B | $     16.47B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## MDT FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     33.54B | $     33.54B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $      4.66B | $      4.66B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     91.68B | $     91.68B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     43.42B | $     43.42B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     48.02B | $     48.26B | +0.48% | ✓ | `StockholdersEquity` |
| Cash | $      2.22B | $      2.22B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | — | $     25.64B | — | sec_missing | `-` |
| Operating CF | $      7.04B | $      7.04B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## HD FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $    164.68B | $    164.68B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $     14.16B | $     14.16B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    105.09B | $    105.09B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     92.28B | $     92.28B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     12.81B | $     12.81B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $      1.39B | $      1.39B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     49.40B | $     49.40B | +0.00% | ✓ | `LongTermDebt` |
| Operating CF | $     16.32B | $     16.32B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## LOW FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     86.29B | $     86.29B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $      6.65B | $      6.65B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     54.14B | $     54.14B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     64.06B | $     64.06B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     -9.92B | $     -9.92B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $       982M | $       982M | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     39.82B | $     39.82B | +0.00% | ✓ | `LongTermDebt` |
| Operating CF | $      9.86B | $      9.86B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## UNP FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     24.51B | $     24.51B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $      7.14B | $      7.14B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     69.70B | $     69.70B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     51.23B | $     51.23B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     18.47B | $     18.47B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $      1.27B | $      1.27B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $     31.81B | $     31.81B | +0.00% | ✓ | `LongTermDebt` |
| Operating CF | $      9.29B | $      9.29B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## NSC FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     12.18B | $     12.18B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $      2.87B | $      2.87B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     45.24B | $     45.24B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     29.69B | $     29.69B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     15.55B | $     15.55B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $      1.53B | $      1.53B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | — | $     16.48B | — | sec_missing | `-` |
| Operating CF | $      4.36B | $      4.36B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## ITW FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     16.04B | $     16.04B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $      3.07B | $      3.07B | +0.00% | ✓ | `ProfitLoss` |
| Total Assets | $     16.15B | $     16.15B | +0.00% | ✓ | `Assets` |
| Total Liabilities | — | $     12.92B | — | sec_missing | `-` |
| Total Equity | $      3.23B | $      3.23B | +0.00% | ✓ | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| Cash | $       851M | $       851M | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $      6.68B | $      6.68B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $      3.13B | $      3.13B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## EMR FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $      4.43B | $     18.02B | +306.50% | ✗ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $       485M | $      2.29B | +372.78% | ✗ | `NetIncomeLoss` |
| Total Assets | $     41.96B | $     41.96B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     21.68B | $     21.67B | -0.07% | ✓ | `(derived: Assets − StockholdersEquity)` |
| Total Equity | $     20.28B | $     20.30B | +0.08% | ✓ | `StockholdersEquity` |
| Cash | $      1.54B | — | — | ours_missing | `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` |
| Long-Term Debt | $      8.32B | $      8.32B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $      3.10B | $      3.10B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## MCO FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $      7.72B | $      7.72B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $      2.46B | $      2.46B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $     15.83B | $     15.83B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     11.62B | $     11.62B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $      4.05B | $      4.21B | +3.72% | ≈ | `StockholdersEquity` |
| Cash | $      2.38B | $      2.38B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | $      6.99B | $      6.99B | +0.00% | ✓ | `LongTermDebtNoncurrent` |
| Operating CF | $      2.90B | $      2.90B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## AXP FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     41.30B | $     41.30B | +0.00% | ✓ | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | $     10.83B | $     10.83B | +0.00% | ✓ | `NetIncomeLoss` |
| Total Assets | $    300.05B | $    300.05B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $    266.58B | $    266.58B | +0.00% | ✓ | `Liabilities` |
| Total Equity | $     33.47B | $     33.47B | +0.00% | ✓ | `StockholdersEquity` |
| Cash | $     47.79B | — | — | ours_missing | `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` |
| Long-Term Debt | $     56.39B | $     56.39B | +0.00% | ✓ | `LongTermDebt` |
| Operating CF | $     18.43B | $     18.43B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |

---
## ACN FY2025

| Metric | SEC value | Our raw | Drift | Flag | SEC tag |
|---|---:|---:|---:|:---:|---|
| Revenue | $     16.66B | $     69.67B | +318.22% | ✗ | `Revenues` |
| Net Income | $      1.79B | $      7.68B | +329.42% | ✗ | `NetIncomeLoss` |
| Total Assets | $     65.39B | $     65.39B | +0.00% | ✓ | `Assets` |
| Total Liabilities | $     34.20B | $     33.15B | -3.06% | ≈ | `(derived: Assets − StockholdersEquity)` |
| Total Equity | $     31.20B | $     32.24B | +3.35% | ≈ | `StockholdersEquity` |
| Cash | $     11.48B | $     11.48B | +0.00% | ✓ | `CashAndCashEquivalentsAtCarryingValue` |
| Long-Term Debt | — | $      5.03B | — | sec_missing | `-` |
| Operating CF | $     11.47B | $     11.47B | +0.00% | ✓ | `NetCashProvidedByUsedInOperatingActivities` |