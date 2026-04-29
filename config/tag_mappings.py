"""
tag_mappings.py

Maps canonical target fields (e.g., Revenue, CapEx) to a priority-ordered list of raw XBRL tags.
The extraction logic will evaluate the list in order and select the first valid tag.
This acts as an executable specification.
"""

FIELD_MAPPINGS = {
    "Revenue": {
        "default": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractsWithCustomers",
            "Revenues",
            "Revenue",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax"
        ]
    },
    
    "OperatingIncome": {
        "default": [
            "OperatingIncomeLoss",
            "ProfitLossFromOperatingActivities",
            "OperatingProfit",
            "ProfitLossBeforeTax"
        ]
    },
    
    "NetIncome": {
        "default": [
            "NetIncomeLoss",
            "NetIncomeLossAttributableToParentNetOfTax",
            "ProfitLoss",
            "ProfitLossAttributableToOwnersOfParent",
            "IncomeLossFromContinuingOperations"
        ]
    },

    "COGS": {
        "default": [
            "CostOfGoodsAndServicesSold",
            "CostOfRevenue",
            "CostOfSales",
            "CostOfGoodsSold",
            "CostOfProductsSold",
            "CostOfServices",
            "FuelAndPurchasedPower",
            "UtilitiesOperatingExpenseFuelUsed"
        ],
        "healthcare": [
            "PolicyholderBenefitsAndClaimsIncurredNet",
            "HealthCareOrganizationMedicalClaimsExpense",
            "BenefitsLossesAndExpenses",
            "HealthCareCostsMedical",
            "CostOfGoodsAndServicesSold"
        ]
    },

    "SG&A": {
        "default": [
            "SellingGeneralAndAdministrativeExpense"
        ]
    },

    "R&D": {
        "default": [
            "ResearchAndDevelopmentExpense"
        ]
    },

    "Depreciation_Tangible": {
        "default": [
            "Depreciation"
        ]
    },

    "IntangibleAmortization": {
        "default": [
            "AmortizationOfIntangibleAssets"
        ]
    },

    "Depreciation": {
        "default": [
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "DepreciationExpense",
            "AmortisationExpense"
        ]
    },

    "SBC": {
        "default": [
            "AllocatedShareBasedCompensationExpense",
            "ShareBasedCompensation"
        ]
    },

    "CapEx": {
        "default": [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "PropertyPlantAndEquipmentAdditions",
            "PaymentsForCapitalImprovements",
            "PaymentsToAcquireOtherPropertyPlantAndEquipment",
            "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
        ]
    },

    "OperatingCF": {
        "default": [
            "NetCashProvidedByUsedInOperatingActivities",
            "CashFlowsFromUsedInOperatingActivities"
        ]
    },

    "InvestingCF": {
        "default": [
            "NetCashProvidedByUsedInInvestingActivities"
        ]
    },

    "FinancingCF": {
        "default": [
            "NetCashProvidedByUsedInFinancingActivities"
        ]
    },

    "TaxExpense": {
        "default": [
            "IncomeTaxExpenseBenefit"
        ]
    },
    
    "CashTaxesPaid": {
        "default": [
            "IncomeTaxesPaid",
            "IncomeTaxesPaidNet"
        ]
    },

    "Buybacks": {
        "default": [
            "PaymentsForRepurchaseOfCommonStock"
        ]
    },
    
    # Balance Sheet Fields
    "Cash": {
        "default": [
            "CashAndCashEquivalentsAtCarryingValue"
        ]
    },
    "TotalAssets": {
        "default": [
            "Assets"
        ]
    },
    "TotalLiabilities": {
        "default": [
            "Liabilities"
        ]
    },
    "TotalEquity": {
        "default": [
            "StockholdersEquity",
            "EquityAttributableToOwnersOfParent",
            "Equity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
        ]
    },
    "RedeemableNoncontrollingInterest": {
        "default": [
            "RedeemableNoncontrollingInterestEquityCarryingAmount",
            "TemporaryEquityCarryingAmountAttributableToNoncontrollingInterests"
        ]
    },
    "LongTermDebt": {
        "default": [
            "LongTermDebtNoncurrent",
            "LongTermDebt"
        ]
    },
    "CurrentAssets": {
        "default": [
            "AssetsCurrent"
        ]
    },
    "CurrentLiabilities": {
        "default": [
            "LiabilitiesCurrent"
        ]
    },
    "AccountsReceivable": {
        "default": [
            "AccountsReceivableNetCurrent"
        ]
    },
    "Inventory": {
        "default": [
            "InventoryNet"
        ]
    },
    "Goodwill": {
        "default": [
            "Goodwill"
        ]
    },
    "PPE": {
        "default": [
            "PropertyPlantAndEquipmentNet"
        ]
    },

    # Shares
    "SharesDiluted": {
        "default": [
            "WeightedAverageNumberOfDilutedSharesOutstanding"
        ]
    },
    "SharesBasic": {
        "default": [
            "WeightedAverageNumberOfSharesOutstandingBasic"
        ]
    },
    "SharesOutstanding": {
        "default": [
            "CommonStockSharesOutstanding"
        ]
    },

    # Misc supplemental tags
    "RestructuringCharges": {"default": ["RestructuringCharges"]},
    "ImpairmentLoss": {"default": ["AssetImpairmentCharges"]},
    "GoodwillImpairment": {"default": ["GoodwillImpairmentLoss"]},
    "PretaxIncome": {"default": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"]},
    "NOL_Carryforward": {"default": ["OperatingLossCarryforwards", "DeferredTaxAssetsOperatingLossCarryforwards"]},
    "LIFO_Reserve": {"default": ["InventoryLIFOReserve"]},
    "CapitalizedSoftware": {"default": ["CapitalizedComputerSoftwareNet"]},
    "DeferredRevenue": {"default": ["DeferredRevenueCurrent", "ContractWithCustomerLiabilityCurrent"]},
    "NonOperatingIncome": {"default": ["NonoperatingIncomeExpense"]},
    "OtherNonOperatingIncome": {"default": ["OtherNonoperatingIncomeExpense"]},
    "InterestExpense": {"default": ["InterestExpense"]},
    "FinanceLeasePrincipalPayments": {"default": ["FinanceLeasePrincipalPayments"]},
    "RepaymentsOfLongTermCapitalLeaseObligations": {"default": ["RepaymentsOfLongTermCapitalLeaseObligations"]},

    # Lease and Pension
    "ROU_Asset": {"default": ["OperatingLeaseRightOfUseAsset"]},
    "LeaseLiabilityCurrent": {"default": ["OperatingLeaseLiabilityCurrent"]},
    "LeaseLiabilityNoncurrent": {"default": ["OperatingLeaseLiabilityNoncurrent"]},
    "LeaseCost": {"default": ["OperatingLeaseCost"]},
    "PensionObligation": {"default": ["DefinedBenefitPlanBenefitObligation"]},
    "PensionPlanAssets": {"default": ["DefinedBenefitPlanFairValueOfPlanAssets"]},
    "PensionServiceCost": {"default": ["DefinedBenefitPlanServiceCost"]},
    "PensionInterestCost": {"default": ["DefinedBenefitPlanInterestCost"]},
    "PensionFundedStatus": {"default": ["DefinedBenefitPlanFundedStatusOfPlan"]},
    "JVA_Income": {"default": ["IncomeLossFromEquityMethodInvestments"]}
}

RESOLUTION_STRATEGY = {
    "Revenue": "max",
    "COGS": "max"
}

# The canonical transformer outputs lowercase variants of some tags (e.g. 'revenue', 'ebit').
# This mapping normalizes those canonical names to the PascalCase keys in FIELD_MAPPINGS.
CANONICAL_ALIASES = {
    "revenue":          "Revenue",
    "cogs":             "COGS",
    "sga":              "SG&A",
    "rd":               "R&D",
    "opex":             "OperatingExpenses",
    "ebit":             "OperatingIncome",
    "operating_income": "OperatingIncome",
    "net_income":       "NetIncome",
    "tax_rate":         "TaxExpense",
    "depreciation":     "Depreciation",
    "medical_claims":   "MedicalClaims",
    "cash":             "Cash",
    "assets":           "TotalAssets",
    "liabilities":      "TotalLiabilities",
    "equity":           "TotalEquity",
    "debt_long":        "LongTermDebt",
    "debt_current":     "ShortTermDebt",
    "total_debt":       "TotalDebt",
    "capex":            "CapEx",
    "operating_cf":     "OperatingCF"
}
