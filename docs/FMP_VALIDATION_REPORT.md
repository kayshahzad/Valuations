# FMP Validation Report

**Generated:** 2026-05-08  
**Tickers:** ABT  
**Tolerance:** ✓ <1% drift, ≈ 1-5%, ✗ >5%  
**Source A:** FMP `income-statement`, `balance-sheet-statement`, `cash-flow-statement` (annual)  
**Source B:** Aletheia cleaned records via `make_calc_input`  


## Findings — TL;DR

The harness compares **45 fields per ticker** (9 income + 14 balance + 5
cash-flow + 6 derived ratios + 11 screening ratios) across all 40 universe
tickers. The screening ratios layer cross-checks the screening-engine
output (P/E, P/B, EV/EBITDA, EV/FCF, Debt-to-Equity, Current Ratio,
Interest Coverage, Net Debt / EBITDA, Dividend Yield, EV, Market Cap)
end-to-end, validating the same numbers the dashboard's screening tab
displays. Two classes of result remain:

1. **Validated** — most tickers. FMP returns USD statements and matches
   our cleaned data on the conventional fields. Bank/insurer/utility/
   conglomerate tickers (JPM, BRK-B, UNH, CNC, NEE) show many ✗ and
   missing flags by design — their schemas don't map to the standard
   income statement / balance sheet layout. ROIC and Invested Capital
   are explicitly suppressed for those filers via
   `business_model != fcff_compatible`.
2. **Currency-mismatched** — ASML files 20-F under EUR; TSM under TWD.
   FMP returns the home-currency statements; comparison is not meaningful.
   Harness skips with a clear flag.

### Documented normalization-difference patterns

Every ✗ flag observed in validated tickers fits one of these patterns. None
are data errors in the cleaned records — values reconcile exactly when you
add back the granular fields we keep separate but FMP aggregates:

| Drift pattern                        | Cause                                                      | Reconciles |
|--------------------------------------|------------------------------------------------------------|---|
| Accounts Receivable (AAPL/UNH/V)     | FMP `netReceivables` aggregates trade AR + other/vendor receivables | ✓ exact |
| Short-Term Debt (AAPL/WMT)           | FMP `shortTermDebt` aggregates commercial paper + current portion of LTD | ✓ exact |
| PPE Net (MSFT/COST/GOOGL/AMZN/NVDA/WMT) | FMP `propertyPlantEquipmentNet` includes Operating Lease ROU assets | ✓ exact |
| EBITDA (most tickers, -5 to -16%)    | FMP `ebitda` adds back stock-based compensation; ours = OpInc + D&A. See `clean_EBITDA_ExcludingSBC` for the FMP-pattern parallel field. | ✓ to within SBC |
| ROIC (most tickers, +30 to +60%)     | FMP's `returnOnInvestedCapital` divides by *operating-side* invested capital (NWC + Net PP&E); ours divides by *financing-side* (Equity + Debt − Cash). Both standard definitions. | definitional |
| Invested Capital (large drifts)      | Same root cause: FMP `investedCapital` = NWC + Net PP&E; ours = Equity + Total Debt − Cash. | definitional |
| Margin %, ROE                        | Byte-perfect or within SBC across every ticker — these are robust to definitional choices. | ✓ |
| Screening multiples (P/E, P/B, EV/EBITDA, EV/FCF, EV, Market Cap, Dividend Yield) | FMP uses period-end price (FY close); our screening uses current market price. ~+10% drift is the price move since fiscal-year-end. | price-timing |
| Net Debt / EBITDA                    | We subtract long-term marketable securities from gross debt (AAPL has $77B securities portfolio); FMP doesn't. Cash-rich tech tickers can flip from net-cash (us) to net-debt (FMP). | definitional |
| Debt-to-Equity (some tickers)        | FMP sometimes folds operating-lease debt into total debt. Ours uses financial debt only. | definitional |
| ROIC / Invested Capital for JPM, BRK-B, NEE, UNH, CNC | Suppressed entirely (`n/a (schema)`) — invested-capital ratios don't apply to bank, insurer, conglomerate, or regulated-utility balance sheets. | schema |

### FMP rate limits

The current FMP plan provides 300 calls/minute and full coverage of the
universe. A full 40-ticker run is ~200 calls (5 endpoints × 40 tickers)
and completes in well under a minute. The harness retains the
quota-exhaustion + stale-cache fallback paths.

## Summary by ticker

| Ticker | FY | ✓ | ≈ | ✗ | missing | total |
|---|---|---:|---:|---:|---:|---:|
| ABT | 2025 | 23 | 6 | 14 | 2 | 45 |

---
## ABT FY2025


### Income Statement

| Metric | FMP | Ours | Drift | Flag |
|---|---:|---:|---:|:---:|
| Revenue | $     44.33B | $     44.33B | +0.00% | ✓ |
| COGS | $     19.72B | $     19.32B | -2.04% | ≈ |
| R&D | $      2.96B | $      2.94B | -0.44% | ✓ |
| SG&A | $     12.38B | $     12.33B | -0.37% | ✓ |
| Operating Income | $      8.05B | $      8.05B | +0.06% | ✓ |
| EBITDA | $     11.55B | $     11.17B | -3.32% | ≈ |
| Net Income | $      6.52B | $      6.52B | +0.00% | ✓ |
| Diluted EPS | 3.72 | 3.72 | +0.00% | ✓ |
| Diluted Shares | $      1.75B | $      1.75B | +0.06% | ✓ |

### Balance Sheet

| Metric | FMP | Ours | Drift | Flag |
|---|---:|---:|---:|:---:|
| Cash | $      8.52B | $      8.52B | +0.00% | ✓ |
| Short-Term Investments | $       417M | $       417M | +0.00% | ✓ |
| Accounts Receivable | $      7.93B | $      7.93B | +0.00% | ✓ |
| Inventory | $      6.49B | $      6.49B | +0.00% | ✓ |
| Current Assets | $     26.00B | $     26.00B | +0.00% | ✓ |
| PPE Net | $     12.94B | $     11.82B | -8.70% | ✗ |
| Goodwill | $     24.04B | $     24.04B | +0.00% | ✓ |
| Total Assets | $     86.71B | $     86.71B | +0.00% | ✓ |
| Accounts Payable | $      4.24B | — | — | ours_missing |
| Short-Term Debt | $      3.31B | — | — | ours_missing |
| Current Liabilities | $     16.50B | $     16.50B | +0.00% | ✓ |
| Long-Term Debt | $     10.83B | $      9.90B | -8.60% | ✗ |
| Total Liabilities | $     33.94B | $     33.94B | +0.00% | ✓ |
| Total Equity | $     52.13B | $     52.77B | +1.23% | ≈ |

### Cash Flow

| Metric | FMP | Ours | Drift | Flag |
|---|---:|---:|---:|:---:|
| Operating CF | $      9.57B | $      9.57B | +0.00% | ✓ |
| CapEx | $      2.17B | $      2.17B | +0.00% | ✓ |
| Free Cash Flow | $      7.39B | $      7.39B | +0.00% | ✓ |
| Dividends Paid | $      4.12B | $      4.12B | +0.00% | ✓ |
| Buybacks | $       893M | $       893M | +0.00% | ✓ |

### Derived metrics + ratios

| Metric | FMP | Ours | Drift | Flag |
|---|---:|---:|---:|:---:|
| Gross Margin % | 55.51 | 56.42 | +1.63% | ≈ |
| EBIT Margin % | 18.16 | 18.17 | +0.06% | ✓ |
| EBITDA Margin % | 26.06 | 25.20 | -3.32% | ≈ |
| ROIC | 0.0843 | 0.1156 | +37.05% | ✗ |
| ROE | 0.1251 | 0.1236 | -1.21% | ≈ |
| Invested Capital | $     52.00B | $     55.03B | +5.82% | ✗ |

### Screening ratios

| Metric | FMP | Ours | Drift | Flag |
|---|---:|---:|---:|:---:|
| P/E Ratio | 33.55 | 22.59 | -32.66% | ✗ |
| P/B Ratio | 4.20 | 2.79 | -33.48% | ✗ |
| EV/EBITDA | 19.51 | 13.42 | -31.20% | ✗ |
| EV/FCF | 30.48 | 20.28 | -33.49% | ✗ |
| Debt-to-Equity | 0.2890 | 0.1875 | -35.12% | ✗ |
| Interest Coverage | 23.60 | 18.08 | -23.38% | ✗ |
| Current Ratio | 1.58 | 1.58 | +0.00% | ✓ |
| Net Debt / EBITDA | 0.5665 | 0.2750 | -51.45% | ✗ |
| Dividend Yield % | 1.88 | 2.80 | +49.03% | ✗ |
| EV ($B) | 225.43 | 149.94 | -33.49% | ✗ |
| Market Cap ($B) | 218.88 | 146.87 | -32.90% | ✗ |