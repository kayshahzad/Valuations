# Sign Conventions — Calculation-Layer Schema

Canonical reference for which fields are Tier 1 / Tier 2 / Tier 3 in the
validation framework. **When adding a new field to the schema, update
this document first** before writing validation code that consumes it.

The three-tier design is the framework's defense against the false-positive
trap: blanket "must be positive" rules produce false positives on legitimate
divestiture / loss / negative-equity cases, which trains the team to ignore
the validator. The right design encodes what is **mathematically true**
(Tier 1) versus what is **typically true** (Tier 2/3).

## Tier 1 — STRICT non-negative

These fields cannot legitimately be negative under any normal accounting
circumstance. A negative value is a sign error, unit error, or wrong-field-
mapping bug. Hard-fail in `_require_strict_nonneg`.

| Field | Rationale |
|---|---|
| `revenue` | Never negative for an operating company |
| `total_assets` | Accounting identity: A = L + E with A ≥ 0 |
| `depreciation` | U.S. GAAP: always positive (IFRS upward revaluation is an override-registry case) |
| `amortization` | Same as depreciation |
| `cogs` | Cost of goods sold — non-negative by definition |
| `operating_expenses` | Non-negative |
| `total_debt` | Gross debt; NetDebt is Tier 2 |
| `cash` | Non-negative |
| `interest_expense` | Cost of debt, non-negative |
| `tax_paid` | Cash taxes (outflow positive convention) |
| `shares_outstanding` | Strictly positive |
| `shares_diluted` | Strictly positive |
| `market_cap` | Strictly positive for any listed company |
| `enterprise_value` | market_cap + net_debt; should be positive except in extreme distress |
| `goodwill` | Balance-sheet level; non-negative (goodwill_change is Tier 2) |
| `intangible_assets` | Level non-negative |

## Tier 2 — SOFT-FLAG, negative is legitimate

These fields CAN legitimately be negative but usually aren't. Validation is
via **range checks on ratios** and **arithmetic identities**, NOT sign rules.
A negative value warrants a structured warning, not a refusal.

| Field | Why negatives are legitimate |
|---|---|
| `capex` | Net-divestiture years (GE breakup, Kraft portfolio rationalization) |
| `net_income` | Company can lose money |
| `ebit` | Cyclical troughs / structurally unprofitable companies |
| `ebitda` | Rare but possible in distressed cases |
| `fcf` | Growth-investment years (AMZN ran negative FCF for ~a decade) |
| `operating_cash_flow` | Distressed / fast-growth with working-capital headwinds |
| `working_capital_change` | Can be either direction |
| `net_debt` | **Negative means net cash position** (AAPL, NVDA, MSFT) |
| `goodwill_change` | Impairments + divestitures produce negative changes |
| `total_equity` | Buyback-heavy mature companies (LOW, HD) show negative |
| `retained_earnings` | Accumulated deficit possible |
| `invested_capital` | Rare but can be negative |

## Tier 3 — No sign rule, range only

These fields legitimately swing either sign frequently. Only check
finiteness and reasonable ranges (`_require_range`), never sign.

| Field | Why both signs are common |
|---|---|
| `tax_rate` | Negative in tax-benefit years (DTA reversals, NOL release) |
| `effective_tax_rate` | Same |
| `roic`, `roe`, `roa` | Negative when company destroys value |
| `gross_margin`, `operating_margin`, `ebit_margin`, `ebitda_margin`, `net_margin`, `fcf_margin` | Negative for unprofitable companies |
| `revenue_growth_rate` | Declining businesses exist |
| `implied_cagr` | Reverse-DCF can imply decline for distressed names |
| `historical_cagr` | Same |
| `wacc`, `cost_of_equity`, `cost_of_debt` | Positive but fluctuates widely |
| `beta` | Can be near-zero or negative in rare cases |
| `risk_free_rate` | Bounded positive in normal markets |
| `terminal_growth` | Bounded near-zero |

## Range bounds reference

Tier-2 and Tier-3 fields are validated by ratio ranges. Bounds live in
`aletheia/calculations/_sign_conventions.RANGE_BOUNDS`. Highlights:

| Bound | Range | Rationale |
|---|---|---|
| `capex_to_revenue` | `[-0.30, 0.75]` | Negative covers divestiture years; +0.75 accommodates semiconductor-fab norms (TSMC 40-55%, ORCL AI buildout edge case) |
| `da_to_revenue` | `[0.0, 0.40]` | D&A is positive; 40% is the cap before suggesting unit error |
| `fcf_to_revenue` | `[-1.0, 0.60]` | Growth-investment can go very negative |
| `ebit_to_revenue` | `[-2.0, 0.80]` | Loss-making at -200% margin is the floor before suggesting data error |
| `tax_rate` | `[-1.0, 1.0]` | Hard bound; values outside are almost always upstream errors |
| `wacc` | `[0.02, 0.30]` | Cost of capital range |
| `implied_cagr` | `[-0.50, 1.0]` | Outside this band = model degradation, not real signal |
| `shares_diluted_yoy_ratio` | `[0.70, 1.30]` | Outside = stock split or M&A event |

## Identity tolerances

Arithmetic identities are the most reliable bug-catcher because they
encode what MUST be true. Tolerances in
`_sign_conventions.IDENTITY_TOLERANCES`:

| Identity | Tolerance | Type |
|---|---|---|
| `ebitda_equals_ebit_plus_da` | 0.5% | Definitional |
| `fcf_equals_opcf_minus_capex` | 0.5% | Definitional (auto-detects pre/post-ASC-842 lease form) |
| `accounting_equation_a_eq_l_plus_e` | 0.5% | Accounting fundamental (auto-detects RedeemableNCI form) |
| `net_debt_equals_debt_minus_cash` | 1.0% | Derived; looser tolerance |
| `roic_equals_nopat_over_ic` | 1.0% | Derived |

## Adding a new field

Process:

1. **Update this document first.** Decide which tier the field belongs in.
2. **Add to the appropriate `frozenset` in `_sign_conventions.py`.**
   The disjoint-set assertion at module load will fail if you accidentally
   add the field to two tiers.
3. **Add a range bound to `RANGE_BOUNDS`** if Tier 2 or Tier 3.
4. **Add a test in `tests/calculations/test_sign_conventions.py`**
   anchoring the membership decision so future refactors don't drift.

## When in doubt

If a field could conceivably be negative in some legitimate accounting case
(loss-making, divestiture, accumulated deficit, NCI variant, etc.), it goes
in Tier 2. The default should be soft-flag, not hard-fail. The framework
catches Tier 2 issues via range checks and identities — that's more reliable
than sign rules anyway.
