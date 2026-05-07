# Framework Coverage Matrix

This matrix maps every requirement from the **16-Section Investment Framework** to its specific code implementation status within the Aletheia pipeline.

| Framework Section | Requirement | File | Status |
| :--- | :--- | :--- | :--- |
| **Section 1: Macro & Regime** | | | |
| 1.2 | The 3 Macro Regimes Detection | `universe_portfolio.py` | ✓ Implemented |
| 1.3 | Cyclicality Haircuts | `context.py` / `strategist.py` | ✓ Implemented |
| **Section 2: Data Cleaning** | | | |
| 2.2 | Liberti 3-Step Cleaning (EBITDA) | `forensic.py` | ⚠ Partial (LLM-driven) |
| 2.3 | Domain 7: SBC adjustment | `pro_forma.py` | ✓ Implemented |
| **Section 3: Valuation Engine** | | | |
| 3.1 | Liberti EV/EBITDA `NOPAT×(1-g/ROIC)/(WACC-g)` | `multiple_decomposition.py` | ✓ Implemented |
| 3.1 | P/Sales Margin Driver | `multiple_decomposition.py` | ✓ Implemented |
| 3.2 | Multiple Selection Matrix | `fundamentalist.py` | ✓ Implemented |
| 3.3 | Equity Bridge (8 items) | `equity_bridge.py` | ✓ Implemented |
| 3.4 | Cash Flow from Assets (CFA) Base | `dcf_engine.py` | ✓ Implemented |
| 3.4 | Maintenance/Growth CapEx Split | `screening_ratios.py` | ⚠ Stub only |
| 3.5 | WACC Methodology | `finance.py` | ✓ Implemented |
| 3.6 | Reverse-DCF Implied Growth | `reverse_dcf.py` | ✓ Implemented |
| **Section 4: Moat & Quality** | | | |
| 4.1 | Unified Screening Framework | `screening_ratios.py` | ✓ Implemented |
| 4.2 | 7 Powers Framework | `lead.py` / `strategist.py` | ⚠ Qualitative Only |
| **Section 5: Conviction Scoring** | | | |
| 5.1 | 5 Pillars 1-5 Scoring | `conviction_scorer.py` | ✓ Implemented |
| 5.2 | Multiple-Justified Conviction | `conviction_scorer.py` | ✓ Implemented |
| **Section 6: Portfolio Discipline**| | | |
| 6.2 | Exit Trigger Monitor | `universe_portfolio.py` | ✓ Implemented |
| 6.3 | Comparable Universe Definition | `fundamentalist.py` | ⚠ Manual Override |
| **Section 10: Risk Framework** | | | |
| 10.2 | Systemic Beta Flags | N/A | ❌ Orphan Requirement |
| 10.3 | Concentration Risk Thresholds | `universe_portfolio.py` | ⚠ Stub only |
| 10.6 | Reflexivity (Widen bear case) | `conviction_scorer.py` | ✓ Implemented |
| **Section 11: Management** | | | |
| 11.2 | Capital Allocation Track Record | `fundamentalist.py` | ⚠ Prompt-based only |
| **Section 12: Tax & ESG** | | | |
| 12.3 | Beneish M-Score | N/A | ❌ Orphan Requirement |
| 12.4 | Regulatory Risk Register | N/A | ❌ Orphan Requirement |
| **Section 14: Lifecycle** | | | |
| 14.1 | Lifecycle Classifier | `lifecycle_classifier.py` | ✓ Implemented |
| 14.2 | Porter's 5 Forces Gate | `lead.py` | ⚠ Qualitative Only |
| **Section 15: Unit Economics** | | | |
| 15.1 | LTV/CAC & NRR | `screening_ratios.py` | ⚠ Stub only |
| 15.2 | TAM/SAM/SOM Pen. Rate | `strategist.py` | ⚠ Stub only |
| **Section 16: Idea Gen & Rules**| | | |
| 16.2 | Thesis Document Standard | `thesis_builder.py` | ✓ Implemented |
| 16.3 | Position Entry Protocol | `thesis_builder.py` | ✓ Implemented |
| 16.4 | Sloan Accrual Ratio | N/A | ❌ Orphan Requirement |
| 16.4 | Piotroski F-Score | N/A | ❌ Orphan Requirement |
| 16.4 | Earnings Power Value (EPV) | `strategist.py` | ⚠ Returns 0 (Bug) |

## Gap Analysis (Orphan Requirements)
The following quantitative features specified in the framework are entirely missing from the analytical pipeline:
1. **Sloan Accrual Ratio** (Section 16.4)
2. **Piotroski F-Score** (Section 16.4)
3. **Beneish M-Score** (Section 12.3)
4. **Systemic Beta Flags** (Section 10.2)
5. **Regulatory Risk Register** Probability Matrix (Section 12.4)

## Orphan Code
The following implementations exist in the code but lack direct justification in the text of the integrated framework:
1. `forensic.py` uses an operating leverage approximation (`EBIT margin / Gross margin`) scaled 1-10. This heuristic isn't mentioned in the 16 sections.
2. `intake.py` estimates Invested Capital using `(Total_Assets - Excess_Cash) - (Total_Liabilities - Total_Debt)`. The framework specifies invested capital without providing this exact fallback formula.
