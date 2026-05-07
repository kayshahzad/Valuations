# Calculation Inventory

This document serves as the master index of every calculation tool within the Aletheia pipeline, detailing what each tool computes and which section of the **16-Section Investment Framework** it implements.

## 1. Valuation & DCF Tools

### `dcf_engine.py`
- **Computes:** Intrinsic Value via Three-Scenario DCF, Cash Flow from Assets (CFA).
- **Formulas:** 
  - `FCFF = NOPAT + D&A - CapEx - ΔNWC`
  - Reinvestment TV: `TV = NOPAT × (1 - g/ROIC) / (WACC - g)`
  - Gordon Growth TV: `TV = FCF × (1 + g) / (WACC - g)`
- **Framework Section:** Section 3.4 (Base-Case DCF Design)

### `multiple_decomposition.py`
- **Computes:** Mathematically justified multiples.
- **Formulas:**
  - Liberti EV/EBITDA: `[NOPAT × (1 - g/ROIC) / EBITDA] / (WACC - g)`
  - P/Sales Margin Driver: `[(1 + g) / (r - g)] × (1 - reinvestment) × Profit Margin`
- **Framework Section:** Section 3.1 (The Liberti Formula & P/Sales)

### `reverse_dcf.py`
- **Computes:** Implied revenue CAGR and implied terminal multiples from the current market price.
- **Formulas:** Reverse-engineered CFA to isolate the implied growth rate (`g`).
- **Framework Section:** Section 3.4 (Implied Multiple Sanity Check)

### `equity_bridge.py`
- **Computes:** The bridge from Enterprise Value (EV) to Implied Equity Value.
- **Formulas:** `Equity Value = EV - Debt - NCI + Adjusted Cash + JVA Value`
- **Framework Section:** Section 3.3 (Equity Bridge Construction)

## 2. Screening & Profiling Tools

### `screening_ratios.py`
- **Computes:** 34 quantitative screening metrics (Graham, Lynch, Malkiel, Liberti).
- **Formulas:** P/E, PEG, EV/EBITDA, Net Debt/EBITDA, FCF Margin, ROIC vs WACC.
- **Framework Section:** Section 4.1 (Unified Screening Framework)

### `lifecycle_classifier.py`
- **Computes:** Company maturity stage based on revenue growth and profitability.
- **Framework Section:** Section 14.1 (Lifecycle-Adjusted Analysis)

## 3. Decision & Conviction Tools

### `conviction_scorer.py`
- **Computes:** The 5-Pillar Conviction Score and multiple-justified conviction.
- **Formulas:** Threshold-based scoring logic mapping fundamental metrics to a 1-5 scale.
- **Framework Section:** Section 5.1 (5-Pillar Scoring), Section 5.2 (Multiple-Justified Conviction), Section 10.6 (Reflexivity)

### `thesis_builder.py`
- **Computes:** Thesis formatting and sizing protocol constraints.
- **Framework Section:** Section 16.2 (Investment Thesis Documentation), Section 16.3 (Position Entry Protocol)

## 4. Agent-Side Analytics

### `forensic.py`
- **Computes:** Operational efficiency and forensic flags.
- **Formulas:** `Operating Leverage = EBIT Margin / Gross Margin`
- **Framework Section:** Section 2.3 (Extended Data Cleaning)

### `strategist.py`
- **Computes:** Earnings Power Value (EPV) floor.
- **Formulas:** `EPV = Normalized NOPAT / WACC`
- **Framework Section:** Section 16.4 (Quantitative Earnings Quality Screens)

### `intake.py`
- **Computes:** Invested Capital fallback estimation.
- **Formulas:** `(Total_Assets - Excess_Cash) - (Total_Liabilities - Total_Debt)`
- **Framework Section:** Section 3.1 (Used for ROIC calculations)

## Summary of Findings
- **Orphan Code:** The operating leverage approximation in `forensic.py` (`EBIT margin / Gross margin`) is used to scale 0-10 but lacks explicit mention in the 16-section framework. Intake's Invested Capital estimation formula is also an ad-hoc fallback.
- **Orphan Requirements:** Framework Section 16.4 mandates the calculation of the Sloan Accrual Ratio and Piotroski F-Score. These are currently missing from the calculation tools. Furthermore, Section 3.4 mandates explicit splitting of Maintenance vs. Growth CapEx, which is only stubbed.
