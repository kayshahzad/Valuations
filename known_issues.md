# Known Issues & Deferrals Tracker

This document tracks formal architectural deferrals, bypassed tickers, and explicitly scoped pending work for the valuation engine.

## 1. Float-Based Financials (Insurers & Managed Care)
**Affected Tickers:** `UNH`, `CNC` (Managed Care)
**Status:** Bypassed in `ingestion_validator.py` (added to `KNOWN_ISSUES` with expiration).

**Reason for Deferral:**
Insurers run float-based business models. Applying a standard industrial Free Cash Flow to Firm (FCFF) DCF mathematically explodes their present value (often producing 250%+ upside) because it fundamentally misprices the cost of insurance float and reserves.

**Future Implementation Requirements:**
To properly value these names, the engine requires a Dividend Discount Model (DDM) or an Embedded-Value Model. Before implementation, the following architectural decisions must be resolved:

1. **Sub-sector Economics:**
   - **Managed Care (UNH, CNC):** Functions more like service revenue with lower float duration.
   - **P&C Insurance:** Higher float dependency, requires distinct reserve tracking.
   - **Life Insurance:** Long-duration float, where Embedded Value (EV) is the standard valuation metric.
   *A one-size-fits-all financial model will not work across these sub-sectors.*

2. **Data Layer Impact:**
   - The current ingestion phase does not extract the necessary XBRL tags for insurers.
   - Required additions: Dividend history, Policy Reserves, Surrender Values, and Float-specific line items.

3. **Calc-Tool Architecture:**
   - Decision required: Should we build a parallel `DDMEngine.py` alongside `DCFEngine.py`? Or should `DCFEngine` become a parameterized wrapper that flips into DDM mode based on the `config/industry_routing.py` sector classification?

## 2. Utility CapEx Aggregation
**Affected Tickers:** `NEE`
**Status:** Bypassed in `ingestion_validator.py`

**Reason for Deferral:**
NEE's CapEx aggregation fails because the filer utilizes non-standard investing cash flow tags (e.g., `ConstructionInProgressGross`, `PublicUtilitiesAllowanceForFundsUsedDuringConstructionAdditions`) instead of standard property additions.
**Resolution:** Await Phase 3 utility taxonomy mapping.
