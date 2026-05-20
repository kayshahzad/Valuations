# Complete Formula Inventory — Aletheia Valuations System

**Generated**: 2026-05-17
**Scope**: Every derived value, ratio, and calculation across the codebase
**Companion**: [docs/methodology_changes/](methodology_changes/) — per-phase canonicalization memos

This document is the definitive reference for what gets computed, where, and how. It maps every formula in the system to its exact location, inputs, centralization status, and edge-case behavior. If a number is shown anywhere in the UI, reports, or exports, its formula must appear here.

---

## Table of contents

1. [Centralized formulas (30 functions)](#1-centralized-formulas)
2. [Identity checks & rollforwards (7 audit primitives)](#2-identity-checks--rollforwards)
3. [Tax rate resolution](#3-tax-rate-resolution)
4. [Data-layer derivations (cleaning_engine Domains)](#4-data-layer-derivations)
5. [Validation-layer derivations (FMP adapter)](#5-validation-layer-derivations)
6. [DCF engine — projection + valuation](#6-dcf-engine--projection--valuation)
7. [Reverse DCF](#7-reverse-dcf)
8. [Multiple decomposition (Liberti / SFM)](#8-multiple-decomposition)
9. [Screening ratios](#9-screening-ratios)
10. [Forensic + quality metrics](#10-forensic--quality-metrics)
11. [Reality checks](#11-reality-checks)
12. [Configuration defaults that affect formulas](#12-configuration-defaults)
13. [Centralization gap report](#13-centralization-gap-report)

---

## 1. Centralized formulas

**Location**: `aletheia/calculations/formulas/`
**Public surface**: 30 functions exported in `__all__`
**Architecture lock**: [tests/architecture/test_single_formula_source.py](../tests/architecture/test_single_formula_source.py) prevents redefinition outside this package.

### 1.1 Cost of capital (Phase 4)

| Function | Formula | File:Line | Inputs | Notes |
|---|---|---|---|---|
| `cost_of_equity` | `Rf + Beta × MRP` | [cost_of_capital.py:44](../aletheia/calculations/formulas/cost_of_capital.py) | risk_free_rate, beta, market_risk_premium | CAPM. Returns None when Rf or β missing |
| `cost_of_debt` | `\|InterestExpense\| / TotalDebt`, capped at 15% | [cost_of_capital.py:61](../aletheia/calculations/formulas/cost_of_capital.py) | interest_expense, total_debt, risk_free_rate (fallback) | Falls back to `Rf + 150bps` when primary inputs missing. `KD_CAP = 0.15` |
| `wacc` | `(E/V × Ke) + (D/V × Kd × (1 − t))` | [cost_of_capital.py:83](../aletheia/calculations/formulas/cost_of_capital.py) | cost_of_equity, cost_of_debt, total_equity, total_debt, tax_rate, risk_free_rate | Floor = max(4%, Rf+1%), Ceiling = 18%. Returns `DEFAULT_WACC = 9%` on degenerate inputs |

### 1.2 Income statement (Phase 3)

| Function | Formula | File:Line | Notes |
|---|---|---|---|
| `ebitda` | `OperatingIncome + Depreciation_Total` | [income_statement.py:14](../aletheia/calculations/formulas/income_statement.py) | Synthesis when not filed. D&A defaults to 0 |

### 1.3 Cash flow (Phase 2)

| Function | Formula | File:Line | Notes |
|---|---|---|---|
| `fcf` | `OperatingCF − \|CapEx\|` | [cash_flow.py:20](../aletheia/calculations/formulas/cash_flow.py) | Sign-invariant on CapEx |
| `fcff` | `NOPAT + D&A − \|CapEx\| − ΔNWC` | [cash_flow.py:42](../aletheia/calculations/formulas/cash_flow.py) | CFA-textbook. ΔNWC defaults to 0 |

### 1.4 Balance sheet (Phase 2)

| Function | Formula | File:Line | Notes |
|---|---|---|---|
| `gross_debt` | `LTD + STD + CurrentLTPortion + FinanceLeases` | [balance_sheet.py:25](../aletheia/calculations/formulas/balance_sheet.py) | EV-aligned. Components default to 0 |
| `liquid_assets` | `Cash + ST_Investments + LT_Investments` | [balance_sheet.py:50](../aletheia/calculations/formulas/balance_sheet.py) | LT marketable securities included |
| `net_debt` | `GrossDebt − LiquidAssets` | [balance_sheet.py:70](../aletheia/calculations/formulas/balance_sheet.py) | Negative = net cash position |

### 1.5 Derived inputs (Phase 1)

| Function | Formula | File:Line | Notes |
|---|---|---|---|
| `nopat` | `NormalizedEBIT × (1 − tax_rate)` | [derived_inputs.py:20](../aletheia/calculations/formulas/derived_inputs.py) | Tax-rate resolution is caller's responsibility |
| `invested_capital` | `max(0.05 × Revenue, Equity + Debt − ExcessCash)` | [derived_inputs.py:52](../aletheia/calculations/formulas/derived_inputs.py) | `ExcessCash = max(0, Cash − 0.02 × Revenue)`. Canonicalized 2026-05 |

### 1.6 Margins (Phase 3)

All four margins: `numerator / Revenue × 100` (percentage units). Return `None` for zero/negative revenue.

| Function | Numerator | File:Line |
|---|---|---|
| `gross_margin_pct` | GrossProfit | [margins.py:28](../aletheia/calculations/formulas/margins.py) |
| `ebit_margin_pct` | EBIT (or NormalizedEBIT) | [margins.py:37](../aletheia/calculations/formulas/margins.py) |
| `ebitda_margin_pct` | EBITDA | [margins.py:51](../aletheia/calculations/formulas/margins.py) |
| `fcf_margin_pct` | FCF | [margins.py:60](../aletheia/calculations/formulas/margins.py) |

### 1.7 Returns (Phase 1 + Phase 3)

| Function | Formula | File:Line | Notes |
|---|---|---|---|
| `roic` | `NOPAT / InvestedCapital` | [ratios.py:13](../aletheia/calculations/formulas/ratios.py) | Returns None when IC ≤ 0 |
| `roe` | `NetIncome / TotalEquity` | [ratios.py:37](../aletheia/calculations/formulas/ratios.py) | Suppressed for negative equity (aggressive-buyback filers) |

### 1.8 Valuation multiples (Phase 4)

All ratios return `None` when denominator is zero, negative, or missing.

| Function | Formula | File:Line | Suppression rule |
|---|---|---|---|
| `price_to_earnings` | `Price / EPS` | [valuation_multiples.py:37](../aletheia/calculations/formulas/valuation_multiples.py) | EPS ≤ 0 → None |
| `price_to_book` | `MarketCap / BookEquity` | [valuation_multiples.py:50](../aletheia/calculations/formulas/valuation_multiples.py) | BookEquity ≤ 0 → None |
| `ev_to_ebitda` | `EV / EBITDA` | [valuation_multiples.py:64](../aletheia/calculations/formulas/valuation_multiples.py) | EBITDA ≤ 0 → None |
| `ev_to_ebit` | `EV / EBIT` | [valuation_multiples.py:73](../aletheia/calculations/formulas/valuation_multiples.py) | EBIT ≤ 0 → None |
| `ev_to_fcf` | `EV / FCF` | [valuation_multiples.py:82](../aletheia/calculations/formulas/valuation_multiples.py) | FCF ≤ 0 → None |
| `price_to_sales` | `MarketCap / Revenue` | [valuation_multiples.py:91](../aletheia/calculations/formulas/valuation_multiples.py) | Revenue ≤ 0 → None |
| `net_debt_to_ebitda` | `NetDebt / EBITDA` | [valuation_multiples.py:100](../aletheia/calculations/formulas/valuation_multiples.py) | EBITDA ≤ 0 → None; negative result allowed |
| `debt_to_equity` | `TotalDebt / TotalEquity` | [valuation_multiples.py:117](../aletheia/calculations/formulas/valuation_multiples.py) | Equity ≤ 0 → None |
| `interest_coverage` | `EBIT / \|InterestExpense\|` | [valuation_multiples.py:130](../aletheia/calculations/formulas/valuation_multiples.py) | Interest = 0 → None (debt-free filer) |
| `current_ratio` | `CurrentAssets / CurrentLiabilities` | [valuation_multiples.py:150](../aletheia/calculations/formulas/valuation_multiples.py) | Std safety |
| `dividend_yield` | `DividendsPaid / MarketCap` | [valuation_multiples.py:159](../aletheia/calculations/formulas/valuation_multiples.py) | Std safety |
| `justified_ev_ebitda` (Liberti) | `[NOPAT × (1 − g/ROIC) / EBITDA] / (WACC − g)` | [valuation_multiples.py:178](../aletheia/calculations/formulas/valuation_multiples.py) | WACC ≤ g → None. `JUSTIFIED_ROIC_FLOOR = 8%` |
| `cash_conversion_ratio` | `NOPAT × (1 − g/ROIC) / EBITDA` | [valuation_multiples.py:202](../aletheia/calculations/formulas/valuation_multiples.py) | EBITDA ≤ 0 → None |

---

## 2. Identity checks & rollforwards

**Location**: `aletheia/calculations/identity_checks.py` + `aletheia/calculations/rollforward.py`

These are **audit primitives** (verify clean numbers reconcile), not derivation formulas. Intentionally NOT in the central formulas package — they live in the calc layer where tolerance logic is also encoded.

### 2.1 Balance Sheet Equation
- **Formula**: `TotalAssets = TotalLiabilities + TotalEquity`
- **Tolerance**: 0.5% of TotalAssets (materiality floor: $10M)
- **Function**: `check_balance_sheet_equation()` at [identity_checks.py:322](../aletheia/calculations/identity_checks.py)
- **Special handling**: Regulated utilities (NEE, others) get 25-35% structural drift exception

### 2.2 Retained Earnings Rollforward
- **Formula**: `RE_end ≈ RE_beg + NI − Div − Buybacks − TaxWithhold − ExciseTax + SBC − ΔAPIC + ΔAOCI`
- **Tolerance**: 2% of beginning RE
- **Function**: `check_retained_earnings_rollforward()` at [identity_checks.py:452](../aletheia/calculations/identity_checks.py)
- **Notes**: IRA 1% excise tax included for FY2023+. SBC credits APIC.

### 2.3 Cash Rollforward
- **Formula**: `Cash_end = Cash_beg + OCF + ICF + FCF + FX_effect`
- **Tolerance**: 0.5%
- **Function**: `check_cash_rollforward()` at [identity_checks.py:641](../aletheia/calculations/identity_checks.py)
- **ASU 2016-18**: Post-FY2018 uses broad cash (includes restricted)

### 2.4 PP&E Rollforward
- **Formula**: `PPE_end ≈ PPE_beg + CapEx − D&A + Acquisitions − Impairments + CIP_additions`
- **Tolerance**: 5% standard, 15% for hyperscalers (META, AMZN, GOOGL, GOOG, MSFT, NVDA, AAPL)
- **Function**: `check_ppe_rollforward()` at [identity_checks.py:759](../aletheia/calculations/identity_checks.py)

### 2.5 Debt Rollforward
- **Formula**: `Debt_end ≈ Debt_beg + Issued − Repaid + CP_net + FX_translation`
- **Tolerance**: 3% standard, 8% for FY2019 (ASC 842 transition year)
- **Function**: `check_debt_rollforward()` at [identity_checks.py:938](../aletheia/calculations/identity_checks.py)

### 2.6 Working Capital Reconciliation
- **Formula**: `CF_ReportedChange − BS_Change = 0` (per-line: AR, Inventory, AP)
- **Tolerance**: 10% per line
- **Function**: `check_working_capital_reconciliation()` at [identity_checks.py:1107](../aletheia/calculations/identity_checks.py)

### 2.7 FCF Pathway B Reconciliation
- **Formula**: `FCF_B = NOPAT + D&A + SBC + Deferred_tax + Other_non_cash − \|CapEx\| − ΔNWC`
- **Tolerance**: 10%
- **Function**: `check_fcf_pathway_reconciliation()` at [identity_checks.py:1250](../aletheia/calculations/identity_checks.py)

---

## 3. Tax rate resolution

**Location**: [aletheia/calculations/_tax_rate.py](../aletheia/calculations/_tax_rate.py)

NOT a formula — a deterministic 4-step fallback ladder.

| Order | Source | Returns |
|---|---|---|
| 1 | `clean_CashTaxRate` (current FY, cash basis) | Preferred |
| 2 | `clean_GAAP_TaxRate` (current FY, accrual) | Fallback |
| 3 | Company FY effective tax rate (5-year history, min 3 years) | Multi-year fallback |
| 4 | `US_STATUTORY = 0.21` | Last resort |

**Function**: `resolve_tax_rate()` at [_tax_rate.py:111](../aletheia/calculations/_tax_rate.py)
**Returns**: `(rate, source)` tuple where `source ∈ {"cash", "gaap", "company_fy", "statutory"}`

---

## 4. Data-layer derivations

**Location**: [aletheia/data/cleaning_engine.py](../aletheia/data/cleaning_engine.py) — `_compute_derived()` method, lines 1587-1945

All formula primitives delegate to the central package (Phase 1-4 migrations). Domain-specific synthesis logic stays here.

### 4.1 Domain 1-10 cleaning derivations

| Derivation | Formula | Line | Centralized? |
|---|---|---|---|
| NormalizedEBIT | OperatingIncome with non-recurring backouts | 820 | NO — domain-specific normalization |
| Depreciation_Total | Fallback ladder over D&A tags | 1564-1586 | NO — XBRL tag synthesis |
| OperatingIncome synthesis | `Revenue − COGS − R&D − SG&A` (or `Pretax + Interest`) | 1665-1684 | NO — multi-tag synthesis |
| SG&A synthesis | `SellingAndMarketing + General&Administrative` | 1639-1658 | NO — multi-tag synthesis |
| SGA_Combined | Adds AMZN's separate MarketingExpense tag | 1639 | NO — ticker-specific |
| TotalLiabilities synthesis | `CurrentLiab + NoncurrentLiab` or `Assets − Equity` | 1599-1611 | NO — fallback ladder |
| EBITDA | (central) | 1722-1730 | YES — Phase 3 |
| EBITDA_Liberti | `EBITDA + R&D` (R&D as capital) | 1734-1738 | NO — Liberti methodology variant |
| EBITDA_ExcludingSBC | `EBITDA + SBC` (FMP convention) | 1740-1748 | NO — convention variant |
| FCF | (central) | — | YES — Phase 2 |
| FCF override (AMZN) | `FCF − FinanceLeasePrincipalRepayments` | 1756-1776 | NO — issuer-specific |
| FCFF | (central) | — | YES — Phase 2 |
| Gross margin, EBIT margin, EBITDA margin, FCF margin | (central) | — | YES — Phase 3 |
| ROE, ROIC | (central) | — | YES — Phase 1 + 3 |
| Invested Capital, NOPAT | (central) | — | YES — Phase 1 |
| Net Debt | (central) | — | YES — Phase 2 |

---

## 5. Validation-layer derivations

**Location**: [aletheia/validation/fmp_stage3_adapter.py](../aletheia/validation/fmp_stage3_adapter.py) — `_compute_derived()` method, lines 139-371

Mirrors cleaning_engine using FMP raw inputs. **All centralized formulas called** (Phase 1-4 consolidation).

### 5.1 FMP-specific derivations

| Derivation | Formula | Line | Notes |
|---|---|---|---|
| GAAP_TaxRate | `TaxExpense / PretaxIncome`, clamped to [-0.5, 0.6] | 206-212 | Drops credit-year outliers |
| NormalizedEBIT | = OperatingIncome (no normalization on FMP path) | 225-229 | FMP doesn't expose non-recurring backouts |
| ChangeInWorkingCapital | Passed through from FMP `changeInWorkingCapital` field | 241-253 | Enables full FCFF on FMP path (Phase 2 canonicalization) |

### 5.2 Cross-namespace mirroring

`derived_*` and `clean_*` columns mirrored at [fmp_stage3_adapter.py:352-369](../aletheia/validation/fmp_stage3_adapter.py) so downstream ReverseDCF + ScreeningEngine reading `clean_*` directly don't see NaN.

---

## 6. DCF engine — projection + valuation

**Location**: [aletheia/tools/dcf_engine.py](../aletheia/tools/dcf_engine.py)

This is **modeling logic**, not pure derivation — intentionally kept whole rather than fragmented into the central module.

### 6.1 Module-level constants

| Constant | Value | Source |
|---|---|---|
| `MARKET_RISK_PREMIUM` | 0.0475 | Damodaran current ERP (was 0.055) |
| `DEFAULT_WACC` | 0.09 | Fallback when WACC computation degenerate |
| `DEFAULT_TERMINAL_G` | 0.025 | 2.5% perpetual growth |
| `MAX_TERMINAL_G` | 0.04 | Hard cap (requires megatrend justification) |
| `BETA_PERIOD` | "5y" | 5-year weekly regression |

Location: [dcf_engine.py:59-64](../aletheia/tools/dcf_engine.py)

### 6.2 Beta computation
- **Formula**: Linear regression of stock 5Y weekly returns vs S&P 500 weekly returns → β = Cov(Stock, Market) / Var(Market)
- **Function**: `_compute_beta()` at [dcf_engine.py:457](../aletheia/tools/dcf_engine.py)
- **Fallback**: `_sector_beta_floor()` (sector median)

### 6.3 CapEx smoothing
- **Formula**: 3-year trailing average of (CapEx / Revenue), IQR-outlier-clipped
- **Function**: `_compute_smoothed_capex_pct()` at [dcf_engine.py:357](../aletheia/tools/dcf_engine.py)

### 6.4 WACC computation (orchestration)
- **Function**: `compute_wacc()` at [dcf_engine.py:470](../aletheia/tools/dcf_engine.py)
- Delegates math to the central `cost_of_equity`, `cost_of_debt`, `wacc` (Phase 4)
- Owns Rf fetch and Beta computation

### 6.5 Scenario projection (`_project_scenario()`)
- **Location**: [dcf_engine.py:723-913](../aletheia/tools/dcf_engine.py)

| Step | Formula |
|---|---|
| Y1 revenue | `Prior_year × (1 + CAGR_y1_5)` |
| Y6-Y10 revenue | `Year_5 × (1 + CAGR_y6_10)^(year-5)` |
| Margin trajectory | Linear fade: current → terminal over 10-year explicit period |
| D&A projection | `D&A% × projected_revenue` (smoothed prior 3-5 years) |
| CapEx projection | `CapEx% × projected_revenue` (smoothed) |
| ΔNWC projection | `NWC% × ΔRevenue` |
| Projected EBIT | `projected_margin × projected_revenue` |
| Projected NOPAT | `EBIT × (1 − tax_rate)` |
| Projected FCFF | `NOPAT + D&A − CapEx − ΔNWC` |
| PV of FCFF | `FCFF_year / (1 + WACC)^year` |

### 6.6 Terminal ROIC
- **Formula**: `terminal_roic = max(base_roic, 0.08)`
- **Location**: [dcf_engine.py:104-114](../aletheia/tools/dcf_engine.py)
- Rejects academic assumption that competitive advantages erode to WACC

### 6.7 Terminal value (two methods)

**Gordon growth**:
- **Formula**: `TV_Gordon = FCF_Y10 × (1 + g) / (WACC − g)`
- **Location**: [dcf_engine.py:845](../aletheia/tools/dcf_engine.py)

**Liberti reinvestment-adjusted** (preferred):
- **Formula**: `TV_Liberti = NOPAT_Y10 × (1 − g/terminal_ROIC) / (WACC − g)`
- **Location**: [dcf_engine.py:851](../aletheia/tools/dcf_engine.py)

### 6.8 EV → equity bridge
- **EV**: `PV(Y1-10 FCFF) + PV(TV)`
- **TV % of EV**: `PV(TV) / EV`
- **Equity Value**: `EV − NetDebt` (or `EV + NetCash` if net-cash)
- **Intrinsic per share**: `Equity_Value / SharesDiluted`
- **MoS**: `(Intrinsic − CurrentPrice) / CurrentPrice`
- **Implied EV/EBITDA**: `TV / EBITDA_Y10`

Location: [dcf_engine.py:222-285](../aletheia/tools/dcf_engine.py)

---

## 7. Reverse DCF

**Location**: [aletheia/tools/reverse_dcf.py](../aletheia/tools/reverse_dcf.py)

Solves for the implied CAGR that equates a model EV to the observed market EV — bisection root-finding, not a closed-form formula.

### 7.1 Implied CAGR solver
- **Function**: `_compute_model_ev()` at [reverse_dcf.py:492](../aletheia/tools/reverse_dcf.py)
- **Algorithm**:
  1. For trial CAGR, project revenue `Rev_0 × (1+CAGR)^10`
  2. Project EBIT margin (linear fade from current to terminal)
  3. Compute NOPAT, FCFF (CFA pathway)
  4. Discount at WACC; add terminal value
  5. Compare model EV to market EV
  6. Bisect on CAGR (scipy `brentq`) to close the gap

### 7.2 Scenario grid
- WACC ∈ [6%, 12%], EBIT margin ∈ [current −50%, current +50%]
- Solver runs for each cell to produce sensitivity table
- **Location**: [reverse_dcf.py:672](../aletheia/tools/reverse_dcf.py)

---

## 8. Multiple decomposition

**Location**: [aletheia/tools/multiple_decomposition.py](../aletheia/tools/multiple_decomposition.py)

### 8.1 Liberti EV/EBITDA decomposition
- **Formula**: `EV/EBITDA = [NOPAT × (1 − g/ROIC) / EBITDA] / (WACC − g)`
- **Function**: `_compute_justified_ev_ebitda()` at [multiple_decomposition.py:49](../aletheia/tools/multiple_decomposition.py)
- **Delegates to central**: `justified_ev_ebitda` + `cash_conversion_ratio` (Phase 4)
- Decomposes into three drivers: cash conversion, risk (WACC), value-added growth

### 8.2 P/Sales decomposition (SFM)
- **Formula**: `P/Sales = [(1+g) / (r−g)] × (1 − reinvestment_rate) × profit_margin`
- **Location**: [multiple_decomposition.py:156-191](../aletheia/tools/multiple_decomposition.py)
- **NOT centralized**: Lives only here (used only by multiple_decomposition)

### 8.3 Sector medians (reference comparison)
- **Location**: [multiple_decomposition.py:71-83](../aletheia/tools/multiple_decomposition.py)
- Damodaran approximations for 10 sectors (Technology, Software, Semiconductors, Healthcare, Healthcare Plans, Consumer Cyclical, Auto Manufacturers, Internet, Financial, Default)

---

## 9. Screening ratios

**Location**: [aletheia/tools/screening_ratios.py](../aletheia/tools/screening_ratios.py)

Computed live for the screening dashboard; reads pre-computed `derived_*` from DB plus does its own market-data math.

### 9.1 Robust CAGR computation
- **Function**: `_robust_cagr()` at [screening_ratios.py:199](../aletheia/tools/screening_ratios.py)
- **Behavior**: Handles sparse data via polynomial fit; fallback to simple N-year CAGR
- **Output**: Annual growth rate as decimal (not percentage)
- **NOT centralized**: Different tools may use different CAGR heuristics

### 9.2 EPS-derived metrics
| Metric | Formula |
|---|---|
| EPS | `NetIncome / Shares` |
| EPS CAGR | `_robust_cagr(EPS_series)` |
| EPS Leverage | `EPS_CAGR − Revenue_CAGR` |
| PEG | `P/E / (EPS_CAGR × 100)` |

Location: [screening_ratios.py:480-540](../aletheia/tools/screening_ratios.py)

### 9.3 Centralized multiples (called from this file)
P/E, P/B, EV/EBITDA, EV/EBIT, EV/FCF, ND/EBITDA, D/E, interest coverage, current ratio, dividend yield — all delegate to centralized formulas (Phase 4). See [screening_ratios.py:485-516](../aletheia/tools/screening_ratios.py).

### 9.4 Interest expense fallback
- **Formula (when InterestExpense missing)**: `EBIT / (LongTermDebt × 0.045)` proxy
- **Location**: [screening_ratios.py:438-447](../aletheia/tools/screening_ratios.py)
- **Reason**: AAPL/MSFT/LLY stopped filing InterestExpense in FY2024+

### 9.5 EPV rough margin of safety
- **Formula**: `(NOPAT / TaxRate / 0.09 − EV) / EV`
- **Location**: [screening_ratios.py:510](../aletheia/tools/screening_ratios.py)
- **Notes**: Simplified Earnings Power Value at 9% perpetuity. Screening-only, not formal valuation.

### 9.6 Cash conversion (Liberti)
- Calls centralized `cash_conversion_ratio()` (Phase 4)

---

## 10. Forensic + quality metrics

**Locations**:
- [aletheia/tools/forensic_metrics.py](../aletheia/tools/forensic_metrics.py)
- [aletheia/tools/conviction_scorer.py](../aletheia/tools/conviction_scorer.py)

### 10.1 Operating leverage score
- **Formula**: `(EBIT_Margin / Gross_Margin) × 10`
- **Function**: `compute_operating_leverage_score()` at [forensic_metrics.py:8](../aletheia/tools/forensic_metrics.py)
- **Output**: 0-10 scale
- **Fallback**: 5.0 (neutral) when inputs missing

### 10.2 Conviction P1-P5 pillar scoring

Composite logic with thresholds and weights — not single formulas. Intentionally local to enable parameterizable scoring.

| Pillar | Inputs | Function |
|---|---|---|
| P1 — Valuation | DCF base IPS vs market price (MoS thresholds) | `_p1_score()` |
| P2 — Financial Quality | ROIC-WACC spread (40%), FCF margin (35%), ND/EBITDA (25%) | `_p2_score()` at [conviction_scorer.py:61](../aletheia/tools/conviction_scorer.py) |
| P3 — Secular Tailwind | Revenue CAGR + sector tailwind/headwind + cyclicality | `_p3_score()` at [conviction_scorer.py:170](../aletheia/tools/conviction_scorer.py) |
| P4 — Margin of Safety | (Same as P1 — emphasizes valuation anchor) | — |
| P5 — Management Quality | SBC %, operating leverage, capital allocation, moat | `_p5_score()` at [conviction_scorer.py:246](../aletheia/tools/conviction_scorer.py) |

**Financial-sector override on P2**: Uses ROE instead of ROIC-WACC spread (banks don't have meaningful invested capital).

**Data-quality haircut**: All pillar scores × 0.85 when data quality < 0.80.

---

## 11. Reality checks

**Location**: [aletheia/tools/reality_checks.py](../aletheia/tools/reality_checks.py)

### 11.1 Year-N revenue projection
- **Formula**: `Revenue_Y10 = Revenue_0 × (1 + g1)^5 × (1 + g2)^(years − 5)`
- **Function**: `project_year_n_revenue()` at [reality_checks.py:108](../aletheia/tools/reality_checks.py)
- Two-stage growth (Y1-5 @ g1, Y6-10 @ g2)

### 11.2 GDP share check
- **Formula**: `ProjectedRevenue_Y10 / ProjectedGDP_Y10`
- **Function**: `gdp_check()` at [reality_checks.py:138](../aletheia/tools/reality_checks.py)
- **Thresholds** (from [config/gdp_projections.py](../config/gdp_projections.py)):
  - Critical: company would be non-trivial fraction of economy
  - Warning: > 3% (historical max — Walmart at peak early-2000s)
  - Caution: ~1.4% AAPL, ~1.3% MSFT today
  - Info: below caution threshold

---

## 12. Configuration defaults

**Location**: [config/valuation_defaults.py](../config/valuation_defaults.py)

### 12.1 Lifecycle profiles

| Profile | growth_rate | terminal_growth | forecast_years | margin_decay |
|---|---|---|---|---|
| secular_hyper_growth | 35% | 4% | 15y | 10% |
| hyper_growth | 25% | 4% | 15y | 10% |
| high_growth_compounder | 18% | 4% | 10y | 10% |
| growth_compounder | 13.5% | 3.5% | 10y | 20% |
| growth_compounder_software | 13.5% | 4% | 10y | 5% |
| growth_compounder_consumer | 10% | 3.5% | 10y | 15% |
| growth_compounder_pharma | 12% | 4% | 10y | 10% |
| mature | 5% | 3% | 10y | 30% |
| cyclical_industrial | 4% | 2.5% | 10y | 50% |

### 12.2 Scenario adjustments
- **Bear**: growth_haircut −50%, margin_compression −10%, WACC +150bps
- **Bull**: growth_haircut +25%, margin_compression +10%, WACC −50bps

---

## 13. Centralization gap report

### 13.1 What's centralized (Phase 1-4 complete)

30 functions in `aletheia/calculations/formulas/`. Both FMP and XBRL adapter paths plus the three calc consumers (DCFEngine, multiple_decomposition, screening_ratios) call these for the canonical computations. Architecture lock prevents drift.

### 13.2 Intentionally local — not centralized by design

| Domain | Location | Reason |
|---|---|---|
| Identity checks (7 rollforwards) | `aletheia/calculations/identity_checks.py` | Audit primitives with tolerance logic; live in calc layer alongside the data they check |
| Tax-rate fallback ladder | `aletheia/calculations/_tax_rate.py` | Not a formula — deterministic resolver with fallback chain |
| DCF scenario projection | `aletheia/tools/dcf_engine.py:_project_scenario` | Multi-year projection with margin decay; modeling logic, not pure derivation |
| Reverse DCF solver | `aletheia/tools/reverse_dcf.py` | Numerical bisection (scipy `brentq`); optimization, not formula |
| Domain 1-10 cleaning synthesis | `aletheia/data/cleaning_engine.py` | XBRL tag fallback ladders, ticker-specific overrides — provider-specific reconciliation logic |
| Beta computation (5Y regression) | `aletheia/tools/dcf_engine.py:_compute_beta` | Statistical estimation, not closed-form |
| CapEx smoothing (IQR + 3Y avg) | `aletheia/tools/dcf_engine.py:_compute_smoothed_capex_pct` | Ad-hoc outlier removal for DCF projection |
| Robust CAGR estimator | `aletheia/tools/screening_ratios.py:_robust_cagr` | Polynomial-fit heuristic for sparse data; screening-specific |
| Conviction pillar scoring (P1-P5) | `aletheia/tools/conviction_scorer.py` | Composite logic with thresholds and weights; not single formulas |
| Operating leverage score | `aletheia/tools/forensic_metrics.py` | Single-site forensic ratio; not duplicated |
| P/Sales SFM decomposition | `aletheia/tools/multiple_decomposition.py:156-191` | Single-site illustrative framework; Liberti EV/EBITDA (centralized) is preferred |
| Interest-expense 4.5% proxy fallback | `aletheia/tools/screening_ratios.py:438-447` | Ticker-specific workaround (AAPL/MSFT/LLY post-FY2024 disclosure changes) |
| EPV rough MoS | `aletheia/tools/screening_ratios.py:510` | Screening-only approximation; not formal valuation |
| Lifecycle profiles | `config/valuation_defaults.py` | Configuration constants, not formulas |

### 13.3 Potential gaps worth tracking

| Item | Risk level | Recommendation |
|---|---|---|
| `_robust_cagr` (screening_ratios.py) | LOW | Could centralize for screening + reverse_dcf reuse; not critical |
| Interest-expense 4.5% proxy | MEDIUM | Pre-central-formula divergence for AAPL/MSFT/LLY post-FY2024; document or formalize |
| P/Sales SFM formula | LOW | Single-site; not duplicated anywhere |
| Domain-specific cleaning synthesis | LOW | Provider-specific; centralizing would require provider-agnostic abstraction |

### 13.4 Architecture-lock protection

Two tests guard against re-fragmentation:

1. **[tests/architecture/test_single_formula_source.py](../tests/architecture/test_single_formula_source.py)** — AST-walks every live `.py` file; fails if any top-level `def <name>` matches a centralized formula name outside the formulas package. Also blocks submodule imports of the formulas package.

2. **[tests/calculations/test_registry_docstring_sync.py](../tests/calculations/test_registry_docstring_sync.py)** — Asserts every centralized function with a `derivation_registry` entry has a docstring first line matching the registry's `formula` field (whitespace-normalized).

Plus 98 unit + parity tests pinning function-level behavior across Phases 1-4.

---

## Summary

**30 centralized functions** own the canonical implementation of all derived numeric quantities the system relies on. Modeling logic (DCF projection, reverse DCF solving, scenario assembly) intentionally stays in the tool layer where its sequencing matters. Audit primitives (identity checks, rollforwards) live in the calc layer alongside their tolerance logic. Configuration defaults live in `config/`.

The architecture lock makes formula fragmentation a build failure rather than a silent regression. The bug class that produced GOOGL ROIC FMP=21.44% vs XBRL=12.00% is structurally closed.

For audit history (universe-level numeric diffs across the 5 centralization phases), see `audits/centralization_snapshots/`.
