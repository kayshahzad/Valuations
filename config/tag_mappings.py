"""
config/tag_mappings.py

Canonical XBRL field mappings for the Aletheia data ingestion pipeline.

Each entry in FIELD_MAPPINGS maps a canonical target field (e.g., 'Revenue', 'CapEx')
to a priority-ordered list of raw XBRL tags. The TagResolver evaluates the list
in order and selects the first tag where the filer reports a non-null value.

Resolution semantics:
- "valid" means: tag is present in the filer's payload AND value is not null.
  A reported zero IS valid (filer explicitly reports zero).
- If no tag in the priority list resolves, the field returns None and the
  cleaning engine may attempt derivation (e.g., OperatingIncome fallback).

Sector overrides:
- A field can specify per-sector priority lists keyed by industry classification
  from config.ticker_classification.UNIVERSE.
- The cleaning engine selects the override matching the ticker's classification
  before falling back to "default".

Convention:
- PascalCase keys for direct XBRL/accounting line items
  (e.g., 'CapEx', 'LiabilitiesCurrent', 'OperatingIncome')
- lowercase_with_underscores in CANONICAL_ALIASES for cleaning engine internals
  that need normalization to PascalCase
"""

FIELD_MAPPINGS = {

    # ─────────────────────────────────────────────────────────────────────────
    # Income Statement - Revenue & Costs
    # ─────────────────────────────────────────────────────────────────────────

    "Revenue": {
        # Priority: ASC 606-compliant tag first (post-2018 standard), then legacy.
        "default": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractsWithCustomers",
            "Revenues",
            "Revenue",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ],
        # IFRS filers (TSM, ASML 20-Fs) use these.
        # 20-F filers report under SEC's us-gaap namespace even when reporting
        # under IFRS, so the us-gaap revenue tags must also be searched.
        "ifrs": [
            "Revenue",
            "RevenueFromContractsWithCustomers",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
        ],
        # Utility filers (NEE) primarily use the regulated/unregulated combined
        # tag; the standard ASC 606 tag is only present from 2018 onwards.
        "utilities": [
            "RegulatedAndUnregulatedOperatingRevenue",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    },

    "COGS": {
        # Standard goods/services COGS. Excludes D&A and SG&A.
        "default": [
            "CostOfGoodsAndServicesSold",
            "CostOfRevenue",
            "CostOfSales",
            "CostOfGoodsSold",
            "CostOfProductsSold",
            "CostOfServices",
        ],
        # Healthcare/managed care: cost equivalent is medical claims expense.
        "healthcare": [
            "PolicyholderBenefitsAndClaimsIncurredNet",
            "HealthCareOrganizationMedicalClaimsExpense",
            "BenefitsLossesAndExpenses",
            "HealthCareCostsMedical",
            "CostOfGoodsAndServicesSold",
        ],
        # Utilities: fuel and purchased power is the operating cost equivalent.
        "utilities": [
            "FuelAndPurchasedPower",
            "UtilitiesOperatingExpenseFuelUsed",
            "CostOfGoodsAndServicesSold",
            "CostOfRevenue",
        ],
        # Banks: no traditional COGS; interest expense is the funding cost equivalent.
        # Banks should be routed to financial-sector calc paths, not standard FCFF.
        "financials": [
            "InterestExpense",
        ],
    },

    "SG&A": {
        # SG&A is sometimes broken into Selling vs G&A; the aggregated tag is canonical.
        "default": [
            "SellingGeneralAndAdministrativeExpense",
            "GeneralAndAdministrativeExpense",
        ],
    },

    "R&D": {
        # Standard GAAP R&D first; "Excluding IPR&D" only as fallback for filers
        # that don't report the standard tag (LLY).
        "default": [
            "ResearchAndDevelopmentExpense",
            "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
        ],
    },

    "OperatingExpenses": {
        # Aggregated operating expenses (some filers report only this, not components).
        "default": [
            "OperatingExpenses",
            "CostsAndExpenses",
        ],
    },

    "OperatingIncome": {
        # OperatingIncomeLoss is the canonical US-GAAP tag.
        # Note: ProfitLossBeforeTax intentionally excluded — it includes non-operating items.
        # When all these are absent (LLY case), cleaning engine derives via:
        #   OperatingIncome = Revenue - COGS - SG&A - R&D
        "default": [
            "OperatingIncomeLoss",
            "ProfitLossFromOperatingActivities",
            "OperatingProfit",
        ],
    },

    "NetIncome": {
        # Parent-attributed net income preferred (excludes NCI portion).
        # Filers with discontinued ops (ABT pre-2013, etc.) often only file
        # *IncludingPortionAttributableToNoncontrollingInterest under the
        # IncomeLossFromContinuingOperations* family for older fiscal years.
        "default": [
            "NetIncomeLoss",
            "NetIncomeLossAttributableToParentNetOfTax",
            "ProfitLoss",
            "ProfitLossAttributableToOwnersOfParent",
            "IncomeLossFromContinuingOperations",
            "IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest",
        ],
    },

    "PretaxIncome": {
        # Income before taxes - used for tax rate calculation, NOT as OperatingIncome fallback.
        "default": [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
            "IncomeLossBeforeIncomeTaxes",
            "ProfitLossBeforeTax",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Income Statement - D&A Components (split for semantic clarity)
    # ─────────────────────────────────────────────────────────────────────────
    #
    # CRITICAL: These three fields are SEMANTICALLY DISTINCT.
    # - Depreciation_Tangible: physical asset depreciation only (D)
    # - IntangibleAmortization: intangible asset amortization only (A)
    # - Depreciation_Total: derived in cleaning engine as the sum
    #
    # Calc tools should read Depreciation_Total, not the individual components.
    # The cleaning engine prefers the canonical aggregate tag when reported,
    # and falls back to summing components when not.
    # ─────────────────────────────────────────────────────────────────────────

    "Depreciation_Tangible": {
        # Standalone depreciation of physical PPE. AAPL reports this.
        "default": [
            "Depreciation",
            "DepreciationExpense",
        ],
    },

    "IntangibleAmortization": {
        # Amortization of intangibles, lease ROU assets, capitalized software.
        # These are summed by the cleaning engine to construct full A.
        "default": [
            "AmortizationOfIntangibleAssets",
        ],
    },

    "FinanceLeaseAmortization": {
        # Finance lease ROU asset amortization. Separate component for full D&A reconstruction.
        "default": [
            "FinanceLeaseRightOfUseAssetAmortization",
        ],
    },

    "CapitalizedSoftwareAmortization": {
        # Software development amortization. Component of full D&A.
        "default": [
            "CapitalizedComputerSoftwareAmortization",
        ],
    },

    "Depreciation_Total_Aggregate": {
        # The canonical aggregated D&A tag, when filer reports a single combined value.
        # Cleaning engine prefers this over component summation when present.
        "default": [
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "DepreciationAmortizationAndDepletionNet",
        ],
        "ifrs": [
            "DepreciationAndAmortisationExpense",
            "DepreciationExpense",
            "AmortisationExpense",
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Income Statement - Other
    # ─────────────────────────────────────────────────────────────────────────

    "SBC": {
        # Stock-based compensation. AllocatedShareBasedCompensationExpense is preferred
        # because it captures the period's expense; ShareBasedCompensation may include
        # cumulative or grant-date values for some filers.
        "default": [
            "AllocatedShareBasedCompensationExpense",
            "ShareBasedCompensation",
            "ShareBasedCompensationExpense",
        ],
    },

    "InterestExpense": {
        "default": [
            "InterestExpense",
            "InterestExpenseDebt",
        ],
    },

    "NonOperatingIncome": {
        "default": [
            "NonoperatingIncomeExpense",
        ],
    },

    "OtherNonOperatingIncome": {
        "default": [
            "OtherNonoperatingIncomeExpense",
        ],
    },

    "RestructuringCharges": {
        "default": [
            "RestructuringCharges",
            "RestructuringCosts",
        ],
    },

    "ImpairmentLoss": {
        "default": [
            "AssetImpairmentCharges",
            "ImpairmentOfLongLivedAssetsHeldForUse",
        ],
    },

    "GoodwillImpairment": {
        "default": [
            "GoodwillImpairmentLoss",
        ],
    },

    "AcquiredInProcessRnD": {
        # IPR&D charges - separate from standard R&D for LLY-class derivations.
        "default": [
            "ResearchAndDevelopmentAssetAcquiredOtherThanThroughBusinessCombinationWrittenOff",
            "InProcessResearchAndDevelopmentExpense",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Tax
    # ─────────────────────────────────────────────────────────────────────────

    "TaxExpense": {
        # GAAP tax expense from income statement.
        "default": [
            "IncomeTaxExpenseBenefit",
            "IncomeTaxExpense",
        ],
    },

    "CashTaxesPaid": {
        # Cash taxes from cash flow statement - used for clean_CashTaxRate.
        "default": [
            "IncomeTaxesPaid",
            "IncomeTaxesPaidNet",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Balance Sheet - Assets
    # ─────────────────────────────────────────────────────────────────────────

    "TotalAssets": {
        "default": [
            "Assets",
        ],
        "ifrs": [
            "Assets",
            "TotalAssets",
        ],
    },

    "CurrentAssets": {
        "default": [
            "AssetsCurrent",
            "CurrentAssets",
        ],
    },

    "Cash": {
        # Restricted cash deliberately excluded - it's captured separately and
        # subtracted via the Equity Bridge cash haircut (Section 3.3).
        "default": [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashAndCashEquivalents",
        ],
        # IFRS 20-F filers (TSM, ASML) report under us-gaap
        # CashAndCashEquivalentsAtCarryingValue in addition to the IFRS
        # canonical tag, so we search both.
        "ifrs": [
            "CashAndCashEquivalents",
            "CashAndCashEquivalentsAtCarryingValue",
        ],
    },

    "RestrictedCash": {
        # Captured separately so the Equity Bridge can subtract it explicitly.
        "default": [
            "RestrictedCashAndCashEquivalents",
            "RestrictedCash",
            "RestrictedCashCurrent",
            "RestrictedCashNoncurrent",
        ],
    },

    "ShortTermInvestments": {
        # Marketable securities on the asset side - included in Equity Bridge as
        # accessible cash.
        "default": [
            "ShortTermInvestments",
            "MarketableSecuritiesCurrent",
            "AvailableForSaleSecuritiesCurrent",
        ],
    },

    "AccountsReceivable": {
        "default": [
            "AccountsReceivableNetCurrent",
            "ReceivablesNetCurrent",
        ],
    },

    "Inventory": {
        "default": [
            "InventoryNet",
            "InventoryGross",
        ],
    },

    "PPE": {
        # Property, plant, equipment - net of accumulated depreciation.
        "default": [
            "PropertyPlantAndEquipmentNet",
            "PropertyPlantAndEquipmentNetExcludingProperties",
        ],
    },

    "PPE_Gross": {
        # Gross PPE before accumulated depreciation - used for asset turnover analysis.
        "default": [
            "PropertyPlantAndEquipmentGross",
        ],
    },

    "AccumulatedDepreciation": {
        "default": [
            "AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
        ],
    },

    "Goodwill": {
        "default": [
            "Goodwill",
        ],
    },

    "IntangibleAssetsNet": {
        "default": [
            "IntangibleAssetsNetExcludingGoodwill",
            "FiniteLivedIntangibleAssetsNet",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Balance Sheet - Liabilities
    # ─────────────────────────────────────────────────────────────────────────

    "TotalLiabilities": {
        "default": [
            "Liabilities",
        ],
        "ifrs": [
            "Liabilities",
            "TotalLiabilities",
        ],
    },

    "LiabilitiesCurrent": {
        # Banks/insurers don't classify liabilities as current/noncurrent.
        # Field stays null for those tickers; calc layer routes them differently.
        "default": [
            "LiabilitiesCurrent",
            "CurrentLiabilities",
        ],
    },

    "LongTermDebt": {
        "default": [
            "LongTermDebtNoncurrent",
            "LongTermDebt",
            "LongTermDebtAndCapitalLeaseObligations",
        ],
    },

    "ShortTermDebt": {
        # Genuine short-term obligations only (commercial paper, ST notes).
        # `DebtCurrent` is filer-dependent in semantics — for LLY it equals
        # the current portion of LT debt ($1.5B when commercial paper = $0)
        # — so it's NOT a reliable ShortTermDebt fallback. The "current
        # portion of long-term debt" lives in its own field
        # (CurrentPortionLongTermDebt). NetDebt sums BOTH; the raw fields
        # stay semantically clean.
        "default": [
            "CommercialPaper",
            "ShortTermBorrowings",
        ],
    },

    "LongTermInvestments": {
        # Marketable securities held > 12 months. Treasuries, corporate bonds,
        # equities. Subtracted from gross debt for enterprise-value (NetDebt)
        # purposes — same logic as cash equivalents, just longer-dated.
        # AAPL's $77B portfolio sits here; missing it overstates NetDebt by ~$77B.
        "default": [
            "MarketableSecuritiesNoncurrent",
            "LongTermInvestments",
            "AvailableForSaleSecuritiesNoncurrent",
            "InvestmentsNoncurrent",
        ],
    },

    "CurrentPortionLongTermDebt": {
        # The piece of long-term debt maturing within 12 months. NOT the same
        # as ShortTermDebt (commercial paper / ST notes); both should sit in
        # the gross-debt rollup for NetDebt purposes.
        # Filer variation: AAPL uses LongTermDebtCurrent; LLY uses both
        # the long-form Maturities-Repayments tag AND DebtCurrent (the
        # latter for FY2025 specifically). DebtCurrent is last fallback —
        # safe because filers with a real ShortTermDebt distinction
        # (e.g. AAPL with CommercialPaper) don't file DebtCurrent at all.
        "default": [
            "LongTermDebtCurrent",
            "LongtermDebtCurrent",
            "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
            "LongtermDebtMaturitiesRepaymentsOfPrincipalAndCapitalLeaseObligationsInNextTwelveMonths",
            "DebtCurrent",
        ],
    },

    "LiabilitiesNoncurrent": {
        # Used as an intermediate when filers (e.g. LLY) don't report a
        # rolled-up `Liabilities` total directly. cleaning_engine derives
        # TotalLiabilities = LiabilitiesCurrent + LiabilitiesNoncurrent in
        # that case.
        "default": [
            "LiabilitiesNoncurrent",
        ],
    },

    "TotalDebt": {
        # Aggregated debt - if filer reports a single total. Cleaning engine
        # prefers LongTermDebt + ShortTermDebt summation when components exist.
        "default": [
            "DebtLongtermAndShorttermCombinedAmount",
        ],
    },

    "AccountsPayable": {
        "default": [
            "AccountsPayableCurrent",
        ],
    },

    "DeferredRevenue": {
        # Note: NOT subtracted from invested capital (deferred revenue is operating).
        "default": [
            "DeferredRevenueCurrent",
            "ContractWithCustomerLiabilityCurrent",
            "ContractWithCustomerLiability",
        ],
    },

    "DeferredRevenueNoncurrent": {
        "default": [
            "DeferredRevenueNoncurrent",
            "ContractWithCustomerLiabilityNoncurrent",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Balance Sheet - Equity
    # ─────────────────────────────────────────────────────────────────────────

    "TotalEquity": {
        # Includes NCI portion (the "including" variant) - canonical for invested capital.
        "default": [
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "StockholdersEquity",
            "EquityAttributableToOwnersOfParent",
            "Equity",
        ],
    },

    "EquityParentOnly": {
        # Parent-only equity, excludes NCI - used for ROE.
        "default": [
            "StockholdersEquity",
            "EquityAttributableToOwnersOfParent",
        ],
    },

    "MinorityInterest": {
        # NCI - subtracted in Equity Bridge per Section 3.3.
        "default": [
            "MinorityInterest",
            "StockholdersEquityNoncontrollingInterest",
        ],
    },

    "RedeemableNoncontrollingInterest": {
        "default": [
            "RedeemableNoncontrollingInterestEquityCarryingAmount",
            "TemporaryEquityCarryingAmountAttributableToNoncontrollingInterests",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Cash Flow Statement
    # ─────────────────────────────────────────────────────────────────────────

    "OperatingCF": {
        # The *ContinuingOperations variant is the legacy tag used by filers
        # that previously had discontinued operations on the books (CAT, BRK-B,
        # TXN, etc., for fiscal years pre-2018). Listed last so the modern
        # consolidated tag wins when both are reported.
        "default": [
            "NetCashProvidedByUsedInOperatingActivities",
            "CashFlowsFromUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ],
    },

    "InvestingCF": {
        "default": [
            "NetCashProvidedByUsedInInvestingActivities",
            "CashFlowsFromUsedInInvestingActivities",
            "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
        ],
    },

    "FinancingCF": {
        "default": [
            "NetCashProvidedByUsedInFinancingActivities",
            "CashFlowsFromUsedInFinancingActivities",
            "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations",
        ],
    },

    "CapEx": {
        # Standard PPE acquisition. Not a single tag for utilities (NEE) -
        # utility CapEx aggregation is handled in cleaning_engine via known_issues.
        "default": [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "PropertyPlantAndEquipmentAdditions",
            "PaymentsForCapitalImprovements",
            "PaymentsToAcquireOtherPropertyPlantAndEquipment",
            "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        ],
    },

    "Buybacks": {
        "default": [
            "PaymentsForRepurchaseOfCommonStock",
            "PaymentsForRepurchaseOfEquity",
        ],
    },

    "DividendsPaid": {
        # Used for DDM-required tickers (UNH, CNC) once that calc path lands.
        "default": [
            "PaymentsOfDividends",
            "PaymentsOfDividendsCommonStock",
        ],
    },

    "FinanceLeasePrincipalPayments": {
        "default": [
            "FinanceLeasePrincipalPayments",
            "RepaymentsOfLongTermCapitalLeaseObligations",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Shares
    # ─────────────────────────────────────────────────────────────────────────

    "SharesDiluted": {
        "default": [
            "WeightedAverageNumberOfDilutedSharesOutstanding",
        ],
    },

    # Diluted EPS — used as a derivation fallback in cleaning_engine when the
    # direct share-count tag isn't filed (foreign filers, some dei-only filers).
    "DilutedEPS": {
        "default": [
            "EarningsPerShareDiluted",
            "DilutedEarningsLossPerShare",
        ],
        "ifrs": [
            "DilutedEarningsLossPerShare",
            "EarningsPerShareDiluted",
        ],
    },

    "SharesBasic": {
        "default": [
            "WeightedAverageNumberOfSharesOutstandingBasic",
        ],
    },

    "SharesOutstanding": {
        # Period-end shares, not weighted average. Used for floor scenarios per Bug 1.5.
        # `EntityCommonStockSharesOutstanding` is the dei-namespace fallback that
        # some filers (e.g. V) use instead of the us-gaap canonical tag.
        "default": [
            "CommonStockSharesOutstanding",
            "CommonStockSharesIssued",
            "EntityCommonStockSharesOutstanding",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Leases (Section 3.3 Domain 5)
    # ─────────────────────────────────────────────────────────────────────────

    "ROU_Asset_Operating": {
        "default": [
            "OperatingLeaseRightOfUseAsset",
        ],
    },

    "ROU_Asset_Finance": {
        "default": [
            "FinanceLeaseRightOfUseAsset",
        ],
    },

    "LeaseLiabilityCurrent_Operating": {
        "default": [
            "OperatingLeaseLiabilityCurrent",
        ],
    },

    "LeaseLiabilityNoncurrent_Operating": {
        "default": [
            "OperatingLeaseLiabilityNoncurrent",
        ],
    },

    "LeaseLiabilityCurrent_Finance": {
        "default": [
            "FinanceLeaseLiabilityCurrent",
        ],
    },

    "LeaseLiabilityNoncurrent_Finance": {
        "default": [
            "FinanceLeaseLiabilityNoncurrent",
        ],
    },

    "LeaseCost": {
        "default": [
            "OperatingLeaseCost",
            "LeaseCost",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Pension (Section 3.3 Domain 6)
    # ─────────────────────────────────────────────────────────────────────────

    "PensionObligation": {
        "default": [
            "DefinedBenefitPlanBenefitObligation",
        ],
    },

    "PensionPlanAssets": {
        "default": [
            "DefinedBenefitPlanFairValueOfPlanAssets",
        ],
    },

    "PensionServiceCost": {
        "default": [
            "DefinedBenefitPlanServiceCost",
        ],
    },

    "PensionInterestCost": {
        "default": [
            "DefinedBenefitPlanInterestCost",
        ],
    },

    "PensionFundedStatus": {
        # Negative = deficit, positive = surplus. Subtracted from Equity Bridge if deficit.
        "default": [
            "DefinedBenefitPlanFundedStatusOfPlan",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # JVA / Equity Method Investments (Section 3.3 - separately valued)
    # ─────────────────────────────────────────────────────────────────────────

    "JVA_Income": {
        # Income from equity-method investments - isolated from EBIT per framework.
        "default": [
            "IncomeLossFromEquityMethodInvestments",
        ],
    },

    "JVA_Investment": {
        # Carrying value of equity-method investments - added to Equity Bridge.
        "default": [
            "EquityMethodInvestments",
            "InvestmentsInAffiliatesSubsidiariesAssociatesAndJointVentures",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Specialized
    # ─────────────────────────────────────────────────────────────────────────

    "NOL_Carryforward": {
        "default": [
            "OperatingLossCarryforwards",
            "DeferredTaxAssetsOperatingLossCarryforwards",
        ],
    },

    "LIFO_Reserve": {
        "default": [
            "InventoryLIFOReserve",
        ],
    },

    "CapitalizedSoftware": {
        # Capitalized software on balance sheet (asset).
        "default": [
            "CapitalizedComputerSoftwareNet",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Healthcare-specific (managed care, insurers)
    # ─────────────────────────────────────────────────────────────────────────

    "MedicalClaims": {
        # Used for managed care DDM/sector-specific work once that path lands.
        "default": [
            "PolicyholderBenefitsAndClaimsIncurredNet",
            "HealthCareOrganizationMedicalClaimsExpense",
        ],
    },

    "ClaimsReserve": {
        "default": [
            "LiabilityForClaimsAndClaimsAdjustmentExpense",
            "LiabilityForFuturePolicyBenefit",
        ],
    },

    "PolicyholderFunds": {
        # Insurance float - load-bearing for embedded value calculations.
        "default": [
            "PolicyholderFunds",
            "LiabilityForFuturePolicyBenefit",
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Banking-specific (JPM)
    # ─────────────────────────────────────────────────────────────────────────

    "InterestIncome": {
        "default": [
            "InterestIncomeOperating",
            "InterestAndDividendIncomeOperating",
        ],
    },

    "NetInterestIncome": {
        "default": [
            "InterestIncomeExpenseNet",
            "InterestIncomeExpenseAfterProvisionForLoanLoss",
        ],
    },

    "ProvisionForLoanLosses": {
        "default": [
            "ProvisionForLoanLeaseAndOtherLosses",
            "ProvisionForLoanAndLeaseLosses",
        ],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Resolution strategies for fields that need non-standard merge behavior
# ─────────────────────────────────────────────────────────────────────────────

RESOLUTION_STRATEGY = {
    # "first": Use the first valid tag in priority order (default behavior).
    # "max": Some filers report Revenue under multiple non-mutually-exclusive tags
    #        (e.g., both gross and net of assessed tax). Take the maximum to capture
    #        the broadest revenue measurement, consistent with framework's revenue
    #        normalization (Section 2.8).
    # "sum": Aggregate values across the priority list (used for component summation).

    "Revenue": "max",
    "COGS": "max",

    # All other fields default to "first" - the cleaning engine assumes "first"
    # when a field is not explicitly listed here.
}


# ─────────────────────────────────────────────────────────────────────────────
# Canonical aliases - normalize cleaning-engine internal names to FIELD_MAPPINGS keys
# ─────────────────────────────────────────────────────────────────────────────
#
# The canonical transformer in cleaning_engine outputs lowercase variants of some
# tags. This mapping resolves those internal names to the PascalCase keys in
# FIELD_MAPPINGS. Every alias on the right MUST exist as a key in FIELD_MAPPINGS.
# ─────────────────────────────────────────────────────────────────────────────

CANONICAL_ALIASES = {
    "revenue":          "Revenue",
    "cogs":             "COGS",
    "sga":              "SG&A",
    "rd":               "R&D",
    "opex":             "OperatingExpenses",
    "ebit":             "OperatingIncome",
    "operating_income": "OperatingIncome",
    "net_income":       "NetIncome",
    "pretax_income":    "PretaxIncome",
    "tax_expense":      "TaxExpense",
    "cash_taxes":       "CashTaxesPaid",

    # D&A components - calc tools should read derived "Depreciation_Total"
    "depreciation":           "Depreciation_Tangible",
    "amortization":           "IntangibleAmortization",
    "depreciation_aggregate": "Depreciation_Total_Aggregate",

    # Balance sheet
    "cash":             "Cash",
    "restricted_cash":  "RestrictedCash",
    "assets":           "TotalAssets",
    "current_assets":   "CurrentAssets",
    "ppe":              "PPE",
    "ppe_gross":        "PPE_Gross",
    "goodwill":         "Goodwill",
    "intangibles":      "IntangibleAssetsNet",

    "liabilities":           "TotalLiabilities",
    "current_liabilities":   "LiabilitiesCurrent",
    "equity":                "TotalEquity",
    "equity_parent":         "EquityParentOnly",
    "minority_interest":     "MinorityInterest",

    "debt_long":        "LongTermDebt",
    "debt_current":     "ShortTermDebt",
    "total_debt":       "TotalDebt",

    # Cash flow
    "operating_cf":     "OperatingCF",
    "investing_cf":     "InvestingCF",
    "financing_cf":     "FinancingCF",
    "capex":            "CapEx",
    "buybacks":         "Buybacks",
    "dividends":        "DividendsPaid",

    # Specialized
    "sbc":                  "SBC",
    "interest_expense":     "InterestExpense",
    "non_op_income":        "NonOperatingIncome",
    "restructuring":        "RestructuringCharges",
    "impairment":           "ImpairmentLoss",
    "goodwill_impairment":  "GoodwillImpairment",
    "ipr_d":                "AcquiredInProcessRnD",

    # Healthcare
    "medical_claims":   "MedicalClaims",
    "claims_reserve":   "ClaimsReserve",
    "policyholder_funds": "PolicyholderFunds",

    # Shares
    "shares_diluted":     "SharesDiluted",
    "shares_basic":       "SharesBasic",
    "shares_outstanding": "SharesOutstanding",

    # Leases
    "rou_op":             "ROU_Asset_Operating",
    "rou_finance":        "ROU_Asset_Finance",
    "lease_liab_op_curr": "LeaseLiabilityCurrent_Operating",
    "lease_liab_op_lt":   "LeaseLiabilityNoncurrent_Operating",
    "lease_cost":         "LeaseCost",

    # Pension
    "pension_obligation":    "PensionObligation",
    "pension_assets":        "PensionPlanAssets",
    "pension_service_cost":  "PensionServiceCost",
    "pension_funded_status": "PensionFundedStatus",

    # JVA
    "jva_income":     "JVA_Income",
    "jva_investment": "JVA_Investment",
}


# ─────────────────────────────────────────────────────────────────────────────
# Validation - sanity checks at module load
# ─────────────────────────────────────────────────────────────────────────────

def _validate_aliases():
    """Every alias target must exist as a FIELD_MAPPINGS key. Fails at import time."""
    missing = [
        (k, v) for k, v in CANONICAL_ALIASES.items()
        if v not in FIELD_MAPPINGS
    ]
    if missing:
        raise ValueError(
            f"CANONICAL_ALIASES contains targets not in FIELD_MAPPINGS: {missing}"
        )


def _validate_resolution_strategies():
    """Every RESOLUTION_STRATEGY key must exist in FIELD_MAPPINGS."""
    missing = [
        k for k in RESOLUTION_STRATEGY.keys()
        if k not in FIELD_MAPPINGS
    ]
    if missing:
        raise ValueError(
            f"RESOLUTION_STRATEGY references unknown fields: {missing}"
        )


def _validate_sector_keys():
    """Every sector key in tag_mappings should match a real UNIVERSE.sector."""
    from config.ticker_classification import UNIVERSE
    used_sectors = {c.sector.lower() for c in UNIVERSE.values()}
    for field, rules in FIELD_MAPPINGS.items():
        for key in rules.keys():
            if key in ("default", "ifrs"):
                continue
            assert key in used_sectors, (
                f"FIELD_MAPPINGS['{field}']['{key}'] doesn't match any "
                f"UNIVERSE sector. Sectors: {used_sectors}"
            )


_validate_aliases()
_validate_resolution_strategies()
_validate_sector_keys()