# Aletheia Accounting Maps

**Spec version:** 2.0
**Last updated:** 2026-04-29
**Status:** Authoritative source of truth for canonical field resolution and data validation
**Owner:** Aletheia data team
**Compiles to:** `tag_resolver.py`, `data_quality_validator.py`, `coverage_check.py`

---

## How to read this document

This file defines three things, in order of precedence:

1. **What canonical fields exist** and what they mean
2. **How XBRL tags map to canonical fields** (with priority, period rules, and sign conventions)
3. **What invariants must hold** for the data to be trustworthy

Every entry below is consumed by the pipeline. Changes to this file are PRs reviewed by the team.
Changes propagate automatically to `tag_resolver.py` and `data_quality_validator.py` via compilation.

### Conventions used in this doc

- `priority: 1` means "try this first; only fall through if not present"
- `sign_convention: positive_outflow_magnitude` means values stored as `abs(raw)` representing outflow size
- `applies_to:` filters by industry classification
- `since_fy:` indicates the fiscal year an XBRL tag became standard (helps with legacy filings)
- `period_filter:` constrains which contexts are acceptable

---

## Section 0: Industry Classification

The resolver and validator behave differently per industry. Industry is determined from SIC code at ingestion and stored on the ticker record.

### SIC code → Industry classification

| Industry tag       | SIC ranges                | Examples              | Notes |
|--------------------|---------------------------|-----------------------|-------|
| `software`         | 7370-7379                 | MSFT, ORCL, CRM       | High GM, low COGS, often no inventory |
| `hardware`         | 3570-3579, 3670-3679      | AAPL, NVDA, AMD       | Inventory-heavy, traditional COGS |
| `semiconductor`    | 3674                      | NVDA, AMD, TSM, QCOM  | Subset of hardware |
| `internet_media`   | 7370, 7372, 7375          | GOOGL, META           | Service revenue, ad-driven |
| `retail_general`   | 5200-5999                 | WMT, COST, AMZN       | Inventory + COGS standard |
| `pharma`           | 2834, 2836                | LLY, ABBV             | High GM, R&D heavy |
| `medical_device`   | 3841, 3845                | ABT                   | Mixed product/service |
| `managed_care`     | 6321, 6324                | UNH, CNC              | Premium revenue, medical claims as COGS |
| `bank`             | 6020-6029                 | JPM                   | No EBITDA/FCF/GM concept |
| `insurance`        | 6311, 6321, 6331          | (overlap with managed_care) | Premium revenue |
| `conglomerate`     | 6770                      | BRK-B                 | Aggregated financials |
| `industrial`       | 3500-3599, 3700-3799      | CAT                   | Standard COGS |
| `utility_electric` | 4911, 4931                | NEE                   | Fuel + purchased power as COGS |
| `payment_network`  | 6099, 7389                | V, MA                 | No traditional COGS; service-based |
| `auto`             | 3711, 3713                | TSLA                  | Standard COGS, capital-heavy |

### Industry-driven behavior switches

| Behavior                | Configuration                                        |
|-------------------------|------------------------------------------------------|
| Banks: skip GM/EBITDA/FCF | `applies_to: non_financial` excludes `bank`        |
| Managed care: use medical claims as COGS-equivalent | Industry routing in COGS resolution |
| Utility: fuel costs as COGS-equivalent | Industry routing in COGS resolution |
| Payment networks: no GM concept | Set `gross_margin_pct` to null in reference |
| Conglomerates: revenue/NI only | `applies_to: applicable_metrics_only` |

---

## Section 1: Canonical Field Definitions

For each canonical field, this section specifies:
- **What it represents** (semantic meaning)
- **Sign convention** (how values are stored)
- **Required for which industries** (presence expectation)
- **Tag resolution** (which XBRL tags map, in priority order)
- **Period rules** (which contexts are valid)
- **Validation invariants** (what must hold)

---

### 1.1 Income Statement Fields

#### `Revenue`
- **Semantic:** Total revenues for the period as reported on the consolidated income statement
- **Unit:** USD
- **Sign convention:** `positive` (always ≥ 0)
- **Required for:** all industries
- **Period:** duration; full fiscal year for FY records, single quarter for Q records
- **Aggregation strategy:** `max` after sign normalization (handles multiple period contexts)

**Tag resolution (industry-aware):**

```yaml
default:
  priority_order:
    - tag: RevenueFromContractWithCustomerExcludingAssessedTax
      since_fy: 2018
      rationale: "ASC 606 standard for US filers"
    - tag: RevenueFromContractWithCustomerIncludingAssessedTax
      since_fy: 2018
      rationale: "ASC 606 alternative, includes tax"
    - tag: SalesRevenueNet
      until_fy: 2019
      rationale: "Pre-ASC 606 standard"
    - tag: Revenues
      rationale: "Generic fallback, used by some filers throughout"
    - tag: SalesRevenueGoodsNet
      rationale: "Goods-specific revenue, may need to combine with services"
    - tag: SalesRevenueServicesNet
      rationale: "Services-specific revenue, may need to combine with goods"

managed_care:
  priority_order:
    - tag_aggregation: ["PremiumsEarnedNet", "SalesRevenueServicesNet", "InvestmentIncomeOperating"]
      rationale: "UNH, CNC: total revenue = premiums + services + investment income"
    - tag: Revenues
      rationale: "Fallback to total if components missing"

bank:
  priority_order:
    - tag_formula: "InterestAndDividendIncomeOperating + NoninterestIncome - InterestExpense"
      rationale: "JPM: total net revenue (NII + non-interest income, net of interest expense)"
    - tag: Revenues
      rationale: "Fallback"

insurance:
  priority_order:
    - tag_aggregation: ["PremiumsEarnedNet", "InvestmentIncomeOperating"]

conglomerate:
  priority_order:
    - tag: Revenues
      rationale: "BRK-B reports consolidated revenues at top level"
```

**Validation invariants:**
- `Revenue > 0` for any non-distressed company
- YoY change: warn if >50%, alert if >100% (possible restatement or unit error)
- Must equal sum of segment revenues if segment data available (within 1%)

---

#### `COGS` (Cost of Revenue)
- **Semantic:** Direct costs attributable to producing the goods or services sold
- **Unit:** USD
- **Sign convention:** `positive`
- **Required for:** all `non_financial` industries except `payment_network` and `software` (where it may be structurally absent)
- **Period:** duration matching Revenue period

**Tag resolution (industry-aware):**

```yaml
default:
  priority_order:
    - tag: CostOfGoodsAndServicesSold
      rationale: "Most common modern tag for combined goods+services"
    - tag: CostOfRevenue
      rationale: "Generic, widely used"
    - tag: CostOfGoodsSold
      rationale: "Goods-only; verify whether services are reported separately"
    - tag: CostOfServices
      rationale: "Services-only"
    - tag: CostOfProductsSold
      rationale: "ABT and pharma alternative"

managed_care:
  priority_order:
    - tag: PolicyholderBenefitsAndClaimsIncurredNet
      rationale: "Medical claims = COGS-equivalent for managed care"
    - tag: HealthCareOrganizationMedicalClaimsExpense
      rationale: "Alternative tag for medical claims"
    - tag: BenefitsLossesAndExpenses
      rationale: "Aggregate fallback"

utility_electric:
  priority_order:
    - tag_aggregation: ["FuelCosts", "PurchasedPower"]
      rationale: "NEE: fuel + purchased power = utility COGS-equivalent"
    - tag: UtilitiesOperatingExpenseFuelUsed
      rationale: "Combined fuel/purchased-power tag (less common)"
    - tag: CostOfRevenue
      rationale: "Fallback"

software:
  priority_order:
    - tag: CostOfGoodsAndServicesSold
    - tag: CostOfRevenue
  # If absent, gross_margin_pct will be null. ORCL falls into this case.
  may_be_absent: true

payment_network:
  may_be_absent: true
  rationale: "V, MA: no traditional COGS concept; gross margin not meaningful"

bank:
  not_applicable: true
```

**Forbidden tags (do NOT include in COGS resolution):**
- `OperatingExpenses` — this is total opex, includes SG&A
- `OperatingCostsAndExpenses` — total operating costs, includes opex
- `CostsAndExpenses` — total of all costs and expenses
- These will inflate COGS and break gross margin.

**Validation invariants:**
- `0 ≤ COGS ≤ Revenue` (cannot exceed revenue except in distressed situations)
- `COGS / Revenue` should be <100% (gross margin ≥ 0% except in genuine loss situations)

---

#### `GrossProfit`
- **Semantic:** Revenue minus COGS
- **Unit:** USD
- **Sign convention:** `signed`
- **Required for:** all `non_financial` industries with COGS
- **Computation:** `Revenue - COGS` (preferred); use reported tag only as cross-check

**Tag resolution:**
```yaml
default:
  priority_order:
    - tag: GrossProfit
      rationale: "Used as cross-check against computed Revenue - COGS"
```

**Validation invariants (Layer 3 identity):**
- `abs(Revenue - COGS - GrossProfit_reported) / Revenue < 0.01` (if all three present)
- Failure of this identity indicates wrong tag selected for one of the inputs

---

#### `OperatingExpenses`
- **Semantic:** Operating expenses (SG&A + R&D, excluding COGS)
- **Unit:** USD
- **Sign convention:** `positive`
- **Required for:** all `non_financial` industries
- **Computation:** Prefer summed components over aggregate tag

**Tag resolution:**
```yaml
default:
  priority_order:
    - tag_aggregation: ["SellingGeneralAndAdministrativeExpense", "ResearchAndDevelopmentExpense"]
      rationale: "Sum of explicit components is more reliable than aggregate"
    - tag_aggregation: ["SellingGeneralAndAdministrativeExpense", "ResearchAndDevelopmentExpense", "SellingAndMarketingExpense"]
      rationale: "Some filers split S&M from G&A"
    - tag: OperatingExpenses
      rationale: "Aggregate; only use if components unavailable"

bank:
  priority_order:
    - tag: NoninterestExpense
      rationale: "Bank-specific operating expense aggregate"
```

**Forbidden tags:**
- `OperatingCostsAndExpenses` (this includes COGS — will double-count)
- `CostsAndExpenses` (total of everything)

---

#### `SGA` (Selling, General & Administrative)
- **Semantic:** SG&A expense
- **Unit:** USD
- **Sign convention:** `positive`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: SellingGeneralAndAdministrativeExpense
    - tag_aggregation: ["GeneralAndAdministrativeExpense", "SellingAndMarketingExpense"]
    - tag: GeneralAndAdministrativeExpense
```

---

#### `RD` (Research & Development)
- **Semantic:** R&D expense
- **Unit:** USD
- **Sign convention:** `positive`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: ResearchAndDevelopmentExpense
    - tag: ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost
```

---

#### `OperatingIncome` / `EBIT`
- **Semantic:** Earnings from operations before interest and taxes
- **Unit:** USD
- **Sign convention:** `signed` (can be loss)
- **Required for:** all `non_financial` industries

**Tag resolution:**
```yaml
default:
  priority_order:
    - tag: OperatingIncomeLoss
      rationale: "Standard XBRL tag, signed"
    - tag: OperatingIncome
      rationale: "Less common variant"

bank:
  priority_order:
    - tag: IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest
      rationale: "Banks report 'pretax income' rather than operating income"
```

**Validation invariants:**
- `EBIT = GrossProfit - OperatingExpenses` (within 1% tolerance)
- `EBIT_margin = EBIT / Revenue` should be in `[-200%, 100%]`
- `EBIT ≠ GrossProfit` for any company with non-zero opex (catches the column-swap bug)

---

#### `Depreciation` (Depreciation & Amortization, combined)
- **Semantic:** Total D&A for the period (preferred for EBITDA computation)
- **Unit:** USD
- **Sign convention:** `positive`
- **Source preference:** Cash flow statement (more reliable than income statement for combined D&A)

**Tag resolution:**
```yaml
default:
  priority_order:
    - tag: DepreciationDepletionAndAmortization
      rationale: "Broadest combined tag; preferred for EBITDA"
      source_preference: cash_flow_statement
    - tag: DepreciationAndAmortization
      rationale: "Combined depreciation + intangible amortization"
    - tag_aggregation: ["Depreciation", "AmortizationOfIntangibleAssets"]
      rationale: "If reported separately, sum them"
    - tag: Depreciation
      rationale: "Tangible only; use only if amortization is zero or absent"
```

**Aggregation strategy:** `max` (handles multiple contexts)

**Validation invariants:**
- D&A on cash flow statement should equal or approximate D&A on income statement (within 5%)
- `D&A / Revenue` typically `[0.01, 0.15]`; flag if outside `[0, 0.30]`
- D&A < OperatingIncome + D&A (i.e., EBITDA must be greater than D&A alone)

---

#### `SBC` (Stock-Based Compensation)
- **Semantic:** Total stock-based compensation expense for the period
- **Unit:** USD
- **Sign convention:** `positive`
- **Source preference:** Cash flow statement (reported as addback)

**Tag resolution:**
```yaml
default:
  priority_order:
    - tag: ShareBasedCompensation
      source_preference: cash_flow_statement
    - tag: AllocatedShareBasedCompensationExpense
    - tag: EmployeeBenefitsAndShareBasedCompensation
      rationale: "May include other employee benefits; use with caution"
```

**Validation invariants:**
- SBC on income statement = SBC on cash flow statement (within 5%)
- For tech companies, `SBC / Revenue` typically 5-25%; flag if >40%

**IMPORTANT — EBITDA computation:**
SBC is **NOT added back to GAAP EBITDA**. The standard formula is `EBIT + D&A`.
"Adjusted EBITDA" that adds back SBC is a non-GAAP metric and is not used in the canonical layer.

---

#### `NetIncome`
- **Semantic:** Net income attributable to the parent company
- **Unit:** USD
- **Sign convention:** `signed`

**Tag resolution:**
```yaml
default:
  priority_order:
    - tag: NetIncomeLoss
    - tag: NetIncome
    - tag: ProfitLoss
    - tag: NetIncomeLossAvailableToCommonStockholdersBasic
      rationale: "Excludes preferred dividends; less standard"
```

**Validation invariants:**
- NetIncome on income statement = NetIncome at top of cash flow statement (exact match)
- `NetIncome / Revenue` (net margin) within `[-200%, 100%]`

---

### 1.2 Cash Flow Statement Fields

#### `OperatingCF`
- **Semantic:** Net cash provided by (used in) operating activities
- **Unit:** USD
- **Sign convention:** `signed` (usually positive, can be negative for distressed/early-stage companies)

**Tag resolution:**
```yaml
default:
  priority_order:
    - tag: NetCashProvidedByUsedInOperatingActivities
    - tag: NetCashProvidedByUsedInOperatingActivitiesContinuingOperations
      rationale: "Continuing operations only; preferred when discontinued ops exist"
```

---

#### `CapEx` (Capital Expenditures)
- **Semantic:** Cash payments for acquisition of property, plant, and equipment during the period
- **Unit:** USD
- **Sign convention:** `positive_outflow_magnitude` — **stored as abs(raw value)** to represent outflow size
- **Source:** Investing section of cash flow statement
- **Aggregation strategy:** `max` (after sign normalization to positive)

**Tag resolution:**
```yaml
default:
  priority_order:
    - tag: PaymentsToAcquirePropertyPlantAndEquipment
      rationale: "Standard CapEx tag; reported by SEC as positive outflow"
      sign_normalization: abs
    - tag: PaymentsForCapitalImprovements
      sign_normalization: abs
    - tag: PaymentsToAcquireProductiveAssets
      sign_normalization: abs
    - tag: PropertyPlantAndEquipmentAdditions
      sign_normalization: abs
    - tag: PaymentsToAcquireOtherPropertyPlantAndEquipment
      sign_normalization: abs
```

**Forbidden tags (do NOT include in CapEx resolution):**
- `CapitalExpendituresIncurredButNotYetPaid` — non-cash accrual, not actual outflow
- `PurchaseObligation` — future commitment, not current period
- `LongTermPurchaseCommitment` — future
- `PropertyPlantAndEquipmentNet` — balance sheet stock, not flow
- `ProceedsFromSaleOfPropertyPlantAndEquipment` — this is an inflow, NOT CapEx

**Validation invariants:**
- `CapEx ≥ 0` after sign normalization
- `CapEx ≤ |OperatingCF| * 5` (sanity: capex rarely exceeds 5x OCF)
- YoY change: warn if >100% increase (possible major investment cycle or data error)

---

#### `Buybacks` (Common Stock Repurchases)
- **Sign convention:** `positive_outflow_magnitude`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: PaymentsForRepurchaseOfCommonStock
      sign_normalization: abs
    - tag: PaymentsForRepurchaseOfEquity
      sign_normalization: abs
```

---

#### `Dividends` (Cash Dividends Paid)
- **Sign convention:** `positive_outflow_magnitude`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: PaymentsOfDividends
      sign_normalization: abs
    - tag: PaymentsOfDividendsCommonStock
      sign_normalization: abs
```

---

#### `DebtRepayment`
- **Sign convention:** `positive_outflow_magnitude`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: RepaymentsOfLongTermDebt
      sign_normalization: abs
    - tag: RepaymentsOfDebt
      sign_normalization: abs
```

---

#### `DebtIssuance`
- **Sign convention:** `positive` (inflow magnitude; usually reported positive in XBRL)
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: ProceedsFromIssuanceOfLongTermDebt
    - tag: ProceedsFromDebtNetOfIssuanceCosts
```

---

#### `FinanceLeasePrincipalPayments`
- **Semantic:** Principal repayments on finance/capital lease obligations
- **Sign convention:** `positive_outflow_magnitude`
- **Used in:** AMZN FCF override (Amazon defines FCF net of these payments)
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: FinanceLeasePrincipalPayments
      sign_normalization: abs
    - tag: RepaymentsOfFinanceLeaseObligations
      sign_normalization: abs
    - tag: PrincipalPaymentsOnFinanceLeases
      sign_normalization: abs
```

---

#### `DA_CashFlow`
- **Semantic:** D&A as reported on the cash flow statement (addback to net income)
- **Sign convention:** `positive`
- **Used for:** Cross-statement consistency check vs. `Depreciation` from income statement
- **Tag resolution:** Same as `Depreciation`, sourced from cash flow statement context

---

#### `SBC_CashFlow`
- **Semantic:** SBC as reported on cash flow statement (addback)
- **Sign convention:** `positive`
- **Used for:** Cross-statement consistency check
- **Tag resolution:** Same as `SBC`, sourced from cash flow statement

---

#### `CashTaxesPaid`
- **Sign convention:** `positive`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: IncomeTaxesPaidNet
    - tag: IncomeTaxesPaid
```

---

#### `CashInterestPaid`
- **Sign convention:** `positive`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: InterestPaidNet
    - tag: InterestPaid
```

---

### 1.3 Balance Sheet Fields

#### `Cash`
- **Semantic:** Cash and equivalents at carrying value
- **Sign convention:** `positive`
- **Period:** instant
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: CashAndCashEquivalentsAtCarryingValue
    - tag: CashAndCashEquivalents
    - tag: CashCashEquivalentsAndShortTermInvestments
      rationale: "Includes STI; use only if separated tags unavailable"
```

---

#### `ShortTermInvestments`
- **Sign convention:** `positive`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: AvailableForSaleSecuritiesCurrent
    - tag: MarketableSecuritiesCurrent
    - tag: ShortTermInvestments
```

---

#### `LongTermInvestments`
- **Sign convention:** `positive`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: AvailableForSaleSecuritiesNoncurrent
    - tag: MarketableSecuritiesNoncurrent
    - tag: LongTermInvestments
```

---

#### `AccountsReceivable`
- **Sign convention:** `positive`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: AccountsReceivableNetCurrent
    - tag: ReceivablesNetCurrent
```

**Forbidden:** `AccountsReceivableMember` (this is a dimension axis, not a value)

---

#### `Inventory`
- **Sign convention:** `positive`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: InventoryNet
    - tag: InventoryFinishedGoodsNetOfReserves

software:
  may_be_absent: true
  rationale: "Software companies typically have no physical inventory"
```

---

#### `CurrentAssets`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: AssetsCurrent
```

---

#### `TotalAssets`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: Assets
    - tag: AssetsNet
      rationale: "Less common; verify if both present"
```

**Validation invariants:**
- `TotalAssets ≥ CurrentAssets`
- `TotalAssets = TotalLiabilities + TotalEquity` (within 0.1% — this is the fundamental accounting equation)

---

#### `CurrentLiabilities` / `TotalLiabilities`
- **Tag resolution:**
```yaml
CurrentLiabilities:
  default:
    priority_order:
      - tag: LiabilitiesCurrent

TotalLiabilities:
  default:
    priority_order:
      - tag: Liabilities
```

---

#### `LongTermDebt` / `ShortTermDebt` / `TotalDebt`
- **Sign convention:** `positive`
- **Tag resolution:**
```yaml
LongTermDebt:
  default:
    priority_order:
      - tag: LongTermDebtNoncurrent
      - tag: LongTermDebt
      - tag: LongTermNotesPayable
      - tag: SeniorNotes
        rationale: "Specific debt type; verify completeness"

ShortTermDebt:
  default:
    priority_order:
      - tag: ShortTermBorrowings
      - tag: LongTermDebtCurrent
        rationale: "Current portion of long-term debt"
      - tag: CommercialPaper

TotalDebt:
  default:
    computation: "LongTermDebt + ShortTermDebt"
    fallback_tags:
      - tag: LongTermDebtAndCapitalLeaseObligations
      - tag: DebtAndCapitalLeaseObligations
```

---

#### `TotalEquity`
- **Sign convention:** `signed` (can be negative for highly leveraged or distressed companies)
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest
      rationale: "Total equity including NCI; use for accounting equation check"
    - tag: StockholdersEquity
      rationale: "Parent-only equity; preferred for ROE computation"
```

---

#### `MinorityInterest` / Non-controlling interest
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: MinorityInterest
```

**Forbidden:** `NoncontrollingInterestMember` (axis, not value)

---

#### `Goodwill` / `IntangibleAssets`
- **Sign convention:** `positive`
- **Tag resolution:**
```yaml
Goodwill:
  default:
    priority_order:
      - tag: Goodwill

IntangibleAssets:
  default:
    priority_order:
      - tag: FiniteLivedIntangibleAssetsNet
      - tag: IntangibleAssetsNetExcludingGoodwill
```

---

#### `PPE` (Property, Plant, & Equipment)
- **Sign convention:** `positive`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: PropertyPlantAndEquipmentNet
```

---

#### `RetainedEarnings`
- **Sign convention:** `signed`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: RetainedEarningsAccumulatedDeficit
```

---

### 1.4 Per-Share Data

#### `SharesOutstanding`
- **Period:** instant (typically end of fiscal year)
- **Sign convention:** `positive`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: CommonStockSharesOutstanding
```

#### `SharesDiluted` / `SharesBasic`
- **Period:** duration (weighted average over the period)
- **Tag resolution:**
```yaml
SharesDiluted:
  default:
    priority_order:
      - tag: WeightedAverageNumberOfDilutedSharesOutstanding

SharesBasic:
  default:
    priority_order:
      - tag: WeightedAverageNumberOfSharesOutstandingBasic
```

#### `EPS_Diluted`
- **Tag resolution:**
```yaml
default:
  priority_order:
    - tag: EarningsPerShareDiluted
```

**Validation invariants:**
- `EPS_Diluted ≈ NetIncome / SharesDiluted` (within 1%)

---

## Section 2: Period Selection Rules

How to choose the right XBRL context for each canonical field.

### 2.1 Form-type filtering

```yaml
fiscal_year_records:
  source_form: ["10-K", "10-K/A", "20-F", "20-F/A"]
  reject_form: ["10-Q", "10-Q/A", "8-K"]
  rationale: "FY records must come from annual filings to avoid ghost-FY bug"

quarterly_records:
  source_form: ["10-Q", "10-Q/A", "10-K"]
  rationale: "Q4 is implied from 10-K minus first three quarters"
```

### 2.2 Duration filtering

```yaml
fiscal_year_duration:
  required_days: [350, 380]
  rationale: "Full fiscal year is ~365 days; 350-380 allows for fiscal calendar variation"

quarterly_duration:
  required_days: [80, 100]
  rationale: "Quarters are ~91 days"

instant_period:
  rationale: "Balance sheet items have a single date; period_start_date should be null"
```

### 2.3 Period-end date sanity

```yaml
period_end_constraints:
  must_not_be_in_future: true
  rationale: "Catches ORCL ghost FY bug"

  must_match_filing_fiscal_period: true
  rationale: "If filing reports FY2024, period_end must be FY2024 close, not later"

  fiscal_year_end_consistency:
    description: "period_end_date should align with company's stated fiscal year end"
    examples:
      AAPL: month=9 day_range=[24,29]
      ORCL: month=5 day_range=[28,31]
      MSFT: month=6 day_range=[28,30]
      WMT:  month=1 day_range=[28,31]
      COST: month=8 day_range=[28,31]
```

### 2.4 Context dimension filtering

```yaml
default_context_filter:
  require_no_dimensions: true
  rationale: "Want consolidated totals, not segment-level breakdowns"

segment_context_filter:
  used_for: ["segment-level analysis only"]
  axis_filter: "us-gaap:StatementBusinessSegmentsAxis"
```

---

## Section 3: Aggregation Strategies

When multiple raw values exist for a canonical field, how to choose.

### 3.1 Resolution strategies

```yaml
strategies:
  max:
    description: "Take maximum value (after sign normalization). Suitable for fields where larger value is correct."
    used_for: ["Revenue", "OperatingCF", "CapEx (after abs)", "TotalAssets"]

  sum:
    description: "Sum all values. Suitable for component aggregation (D&A from cash flow)."
    used_for: ["Depreciation (when components reported separately)"]

  first:
    description: "Take first value found by priority order. Suitable when priority is definitive."
    used_for: ["Tag-priority-driven resolution"]

  identity:
    description: "All values must agree exactly; fail if disagreement exceeds tolerance."
    used_for: ["NetIncome (must match across statements)"]
```

### 3.2 Sign normalization

```yaml
sign_normalization:
  abs:
    description: "Apply abs() to value before storage. For outflow magnitudes."
    applied_to_tag_groups:
      - CapEx tags
      - Buyback tags
      - Dividend tags
      - DebtRepayment tags
      - FinanceLeasePrincipalPayments tags

  signed_no_change:
    description: "Preserve sign as filed."
    applied_to_tag_groups:
      - OperatingCF, InvestingCF, FinancingCF
      - NetIncome, OperatingIncome
      - TotalEquity (can be negative for distressed)
      - RetainedEarnings (can be deficit)
```

---

## Section 4: Validation Invariants

Hard checks that must hold for data to be considered valid. Ordered by validation layer.

### 4.1 Layer 1: Presence Checks

```yaml
required_fields_by_industry:
  all:
    - Revenue
    - period_end_date
    - accession_number
    - source_form

  non_financial:  # excludes bank, insurance
    additional:
      - OperatingCF
      - CapEx
      - OperatingIncome
      - Depreciation
      - TotalAssets

  industrial:
    additional: [Inventory, COGS]

  managed_care:
    additional: [PolicyholderBenefitsAndClaimsIncurredNet, PremiumsEarnedNet]

  bank:
    additional: [InterestAndDividendIncomeOperating, NoninterestIncome, NetIncome, TotalEquity]

  utility_electric:
    additional: [FuelCosts, OperatingCF, CapEx]
```

### 4.2 Layer 2: Field-Level Validation

```yaml
sign_checks:
  - field: Revenue
    rule: ">= 0"
    severity: ERROR

  - field: CapEx
    rule: ">= 0"  # after sign normalization
    severity: ERROR

  - field: Depreciation
    rule: ">= 0"
    severity: ERROR

  - field: COGS
    rule: ">= 0"
    severity: ERROR

bounded_checks:
  - field: gross_margin_pct
    range: [-100, 100]
    severity: ERROR

  - field: ebit_margin_pct
    range: [-200, 100]
    severity: WARN

  - field: net_margin_pct
    range: [-200, 100]
    severity: WARN

  - field: roe
    range: [-200, 200]
    severity: WARN

magnitude_checks:
  - field: depreciation_to_revenue
    formula: "Depreciation / Revenue"
    range: [0.001, 0.30]
    severity: WARN
    rationale: "Outside this range suggests wrong tag (e.g., picked up Amortization stock instead)"

  - field: capex_to_ocf
    formula: "CapEx / abs(OperatingCF)"
    range: [0, 5.0]
    severity: WARN
    rationale: "CapEx rarely exceeds 5x OCF; outside suggests data error"

yoy_change_flags:
  - field: Revenue
    warn_threshold: 0.50
    alert_threshold: 1.00

  - field: CapEx
    warn_threshold: 1.00
    alert_threshold: 3.00

  - field: TotalAssets
    warn_threshold: 0.50
    alert_threshold: 1.50
```

### 4.3 Layer 3: Within-Statement Identities

```yaml
income_statement_identities:
  - name: gross_profit_identity
    formula: "abs(Revenue - COGS - GrossProfit) / Revenue < 0.01"
    severity: ERROR
    fires_when: "all of Revenue, COGS, GrossProfit are present"
    catches: "Wrong tag selected for one of the inputs (Bucket 1 column swap detection)"

  - name: ebit_identity
    formula: "abs(GrossProfit - OperatingExpenses - EBIT) / Revenue < 0.01"
    severity: ERROR
    fires_when: "all components present"

  - name: gross_margin_not_equal_ebit_margin
    formula: "abs(gross_margin_pct - ebit_margin_pct) > 0.5"
    severity: ERROR
    fires_when: "OperatingExpenses > 0"
    catches: "Bucket 1 column swap bug; if these are equal, COGS resolution failed"

  - name: pretax_identity
    formula: "abs(EBIT + NonOperatingIncome - InterestExpense - PretaxIncome) / Revenue < 0.02"
    severity: WARN

  - name: net_income_identity
    formula: "abs(PretaxIncome - TaxExpense - NetIncome) / abs(NetIncome) < 0.05"
    severity: WARN

balance_sheet_identities:
  - name: accounting_equation
    formula: "abs(TotalAssets - TotalLiabilities - TotalEquity) / TotalAssets < 0.001"
    severity: ERROR
    catches: "Wrong tag selected for Assets, Liabilities, or Equity"

  - name: current_subtotal
    formula: "abs(CurrentAssets + (TotalAssets - CurrentAssets) - TotalAssets) / TotalAssets < 0.001"
    severity: WARN

  - name: cash_subset_of_current
    formula: "(Cash + ShortTermInvestments) <= CurrentAssets * 1.001"
    severity: ERROR

  - name: goodwill_subset_of_assets
    formula: "(Goodwill + IntangibleAssets) <= TotalAssets"
    severity: ERROR

cash_flow_identities:
  - name: cf_total_change
    formula: "abs(OperatingCF + InvestingCF + FinancingCF - NetChangeInCash) / abs(OperatingCF) < 0.05"
    severity: WARN
```

### 4.4 Layer 4: Cross-Statement Consistency

```yaml
cross_statement_checks:
  - name: net_income_consistency
    formula: "NetIncome (income statement) == NetIncome (top of cash flow statement)"
    tolerance: "exact match"
    severity: ERROR

  - name: depreciation_consistency
    formula: "abs(Depreciation_IS - DA_CashFlow) / Depreciation_IS < 0.05"
    severity: WARN
    catches: "Different tags selected on IS vs CF"

  - name: sbc_consistency
    formula: "abs(SBC_IS - SBC_CashFlow) / SBC_IS < 0.10"
    severity: WARN
    fires_when: "both statements report SBC"
```

### 4.5 Layer 5: Cross-Filing Consistency

```yaml
cross_filing_checks:
  - name: cash_continuity
    formula: "Cash_end[FY-1] == Cash_begin[FY] within $1M"
    severity: WARN
    rationale: "Catches restatements and period misalignment"

  - name: equity_continuity
    formula: "TotalEquity_end[FY-1] + ComprehensiveIncome[FY] - Buybacks[FY] - Dividends[FY] ≈ TotalEquity_end[FY]"
    tolerance: 0.05
    severity: INFO
    rationale: "Approximation; exact reconciliation requires more components"

  - name: ppe_continuity
    formula: "PPE_end ≈ PPE_begin + CapEx - Depreciation - Disposals"
    tolerance: 0.10
    severity: INFO

  - name: 10k_vs_10q_reconciliation
    formula: "Sum(Q1, Q2, Q3, Q4_implied) == FY (within 2%)"
    severity: ERROR
    rationale: "Period assignment integrity check"

  - name: restatement_detection
    description: "If FY value from a later filing differs from FY value from the original filing, flag for restatement handling"
    severity: WARN
```

---

## Section 5: Issuer-Specific Overrides

Documented exceptions where a company's stated methodology differs from the canonical formula.

### Override registry schema

```yaml
ISSUER_OVERRIDES:
  AMZN:
    metric: FCF
    adjustment_logic:
      operation: subtract_from_default_formula
      additional_deduction: FinanceLeasePrincipalPayments
    effective_from_fy: 2019
    fallback_behavior: default_formula
    rationale: "AMZN stated FCF methodology subtracts finance lease principal repayments"
    source: "AMZN 10-K, 'Free Cash Flow' section, page reference TBD"
    approver: "[name]"
    approved_date: "2026-04-29"

  WMT:
    metric: FCF
    adjustment_logic:
      operation: subtract_from_default_formula
      additional_deduction: FinanceLeasePrincipalPayments
    effective_from_fy: 2020
    fallback_behavior: default_formula
    rationale: "Per WMT 10-K FY2025 stated FCF definition [VERIFY against actual filing]"
    source: "WMT 10-K FY2025, page reference TBD"
    approver: "[pending verification]"
    approved_date: "[pending]"
```

### Override rules

1. **Every override must cite a primary source** (10-K page, footnote)
2. **Every override must have an approver and approval date**
3. **Overrides apply only when their override conditions are met** (e.g., AMZN FCF override only fires for AMZN)
4. **Override firing is logged** to the `data_quality_report` table for audit trail

---

## Section 6: Failure Modes

What to do when resolution or validation fails.

### 6.1 Tag resolution failures

```yaml
when_no_tag_matches:
  if_field_required:
    action: store_null
    log_severity: ERROR
    coverage_status: MISSING

  if_field_optional:
    action: store_null
    log_severity: INFO
    coverage_status: ABSENT_BY_INDUSTRY  # e.g., software with no Inventory

  if_field_industry_inapplicable:
    action: store_null
    log_severity: NONE
    coverage_status: NOT_APPLICABLE  # e.g., bank with no GrossMargin
```

### 6.2 Validation failures

```yaml
on_layer_1_failure:
  action: flag_ticker_for_no_calculation_validation
  reason: "Cannot validate calculations on incomplete inputs"

on_layer_2_failure:
  severity_ERROR: block_ingestion
  severity_WARN: log_and_continue

on_layer_3_failure:
  severity_ERROR: block_calculation_validation
  rationale: "Failed identity means data is structurally inconsistent"

on_layer_4_5_failure:
  severity_WARN: flag_for_human_review
  severity_INFO: log_only
```

---

## Section 7: Compilation Notes

### 7.1 What this .md compiles to

```
accounting_maps.md (this file)
        │
        ├──compile──▶ tag_resolver.py
        │     - Industry classification lookup
        │     - Per-industry tag priority lists
        │     - Sign normalization rules
        │     - Aggregation strategies
        │     - Period filter logic
        │
        ├──compile──▶ data_quality_validator.py
        │     - Layer 1-5 check definitions
        │     - Severity classifications
        │     - Failure handling
        │
        ├──compile──▶ coverage_check.py
        │     - Required-fields-per-industry matrices
        │     - Coverage status determination
        │
        └──compile──▶ issuer_overrides.py
              - Override registry as Python dict
              - Override application logic
```

### 7.2 Compilation verification

The compilation step must verify:
- Every canonical field has at least one tag mapping (default industry)
- No tag appears in conflicting buckets (e.g., not in both `OperatingExpenses` and `COGS`)
- All forbidden tags are properly excluded
- Every validation invariant references only fields defined in Section 1
- Every override references only fields and overrides that exist

### 7.3 Change protocol

1. Open PR with proposed change to this file
2. Compilation must succeed (catches broken references)
3. Regression test suite must pass (current passing tickers must continue to pass)
4. At least one team member reviews and approves
5. Merge triggers re-compilation of downstream artifacts

---

## Appendix A: Known Issues and TODOs

- [ ] WMT FCF override needs primary source verification
- [ ] ORCL gross_margin_pct: confirmed structurally absent; reference set to null
- [ ] NEE: utility-specific COGS tags need verification against actual NEE 10-K filings
- [ ] BRK-B: conglomerate handling — only revenue and net_income validated
- [ ] V, MA: payment network gross margin handling needs review
- [ ] Restatement handling: when does pipeline pick restated vs. original values?
- [ ] IFRS-specific tags for ASML, TSM not yet enumerated
- [ ] Segment-level fact handling deferred to Phase 5

## Appendix B: Glossary

- **Canonical field:** A standardized field name used throughout Aletheia (e.g., `Revenue`)
- **Raw tag:** A specific XBRL concept identifier as filed (e.g., `RevenueFromContractWithCustomerExcludingAssessedTax`)
- **Resolution:** The process of mapping one or more raw tags to a canonical field
- **Aggregation:** When multiple raw values are present, how to combine them into one canonical value
- **Sign normalization:** Converting raw values to a consistent sign convention before storage
- **Industry routing:** Choosing different resolution rules based on the company's industry classification
- **Identity check:** A mathematical equality that must hold (e.g., Assets = Liabilities + Equity)
- **Coverage:** Whether the data needed to compute a metric is present in the database
- **Override:** A documented exception to the default canonical formula for a specific issuer

