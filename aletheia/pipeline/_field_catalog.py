"""Unified XBRL ↔ FMP field catalog.

Single source of truth for the Stage Explorer's "Extracted from XBRL"
table AND the XBRL-vs-FMP side-by-side comparison. Keeping both views
backed by the same catalog guarantees they stay consistent — every
field shown in extraction is comparable to FMP, and every comparison
row corresponds to a known display field.

Each entry maps a *canonical analytical field* to:
  - the cleaning_engine output keys to look at first
    (e.g. ``record.clean["Revenue"]``)
  - the raw SEC XBRL tag name(s) to fall through to when the cleaner
    doesn't materialise the field (e.g. RetainedEarnings is in raw
    XBRL as ``RetainedEarningsAccumulatedDeficit`` but isn't currently
    populated in ``record.clean`` — this is a documented Category-B
    gap)
  - the FMP cache file + key for the second-source comparison
  - whether the two sides need abs-value reconciliation (CapEx is the
    canonical case: FMP reports the cash outflow as negative, our
    cleaning convention is positive magnitude)

Category groupings ("Income Statement", "Balance Sheet", "Cash Flow")
drive the analyst-friendly sectioning of both panels.

Tier (``critical`` / ``important`` / ``nice_to_have``) tracks the
prioritisation from the analyst's expansion request — used today for
ordering within a category; future versions can hide nice-to-have
fields behind a toggle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class FieldSpec:
    """One canonical field's full mapping."""
    label: str                     # display name
    category: str                  # "Income Statement" | "Balance Sheet" | "Cash Flow"
    tier: str                      # "critical" | "important" | "nice_to_have"
    xbrl_clean_keys: List[str]     # try these in record.clean first
    xbrl_raw_keys: List[str]       # ALSO try these in record.raw
    xbrl_fallback_tags: List[str]  # raw SEC XBRL tag names for direct fetch
    fmp_source: Optional[str]      # "income" | "balance" | "cashflow" | None
    fmp_keys: List[str]            # FMP statement keys to try
    abs_compare: bool = False      # True → compare abs() values (CapEx sign)
    note: str = ""                 # optional context shown next to the field


# ─────────────────────────────────────────────────────────────────────
# Income Statement (10 fields)
# ─────────────────────────────────────────────────────────────────────

_INCOME = [
    FieldSpec(
        label="Revenue", category="Income Statement", tier="critical",
        xbrl_clean_keys=["Revenue"], xbrl_raw_keys=["Revenue"],
        xbrl_fallback_tags=[
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ],
        fmp_source="income", fmp_keys=["revenue"],
    ),
    FieldSpec(
        label="Cost of Goods Sold", category="Income Statement", tier="nice_to_have",
        xbrl_clean_keys=["COGS"], xbrl_raw_keys=["COGS"],
        xbrl_fallback_tags=["CostOfGoodsAndServicesSold", "CostOfRevenue"],
        fmp_source="income", fmp_keys=["costOfRevenue"],
    ),
    FieldSpec(
        label="Total Operating Costs", category="Income Statement", tier="nice_to_have",
        xbrl_clean_keys=["OperatingExpenses"], xbrl_raw_keys=["OperatingExpenses"],
        # Mirrors tag_resolver's chain in config/tag_mappings.py — many
        # filers (META, GOOGL, MSFT) file under ``CostsAndExpenses`` and
        # don't have an ``OperatingExpenses`` tag at all.
        xbrl_fallback_tags=[
            "OperatingExpenses",
            "CostsAndExpenses",
            "OperatingCostsAndExpenses",
        ],
        fmp_source="income", fmp_keys=["operatingExpenses"],
        note=(
            "Definitional divergence with FMP: our cleaner resolves to "
            "the filer's ``CostsAndExpenses`` (total OpEx INCLUDING COGS) "
            "while FMP's ``operatingExpenses`` field is SG&A only (R&D + "
            "S&M + G&A, EXCLUDING COGS). Same XBRL filing, different "
            "aggregation. Treat material drift here as Category C "
            "(documented methodology choice), not a cleaning bug."
        ),
    ),
    FieldSpec(
        label="EBIT (Operating Income)", category="Income Statement", tier="critical",
        xbrl_clean_keys=["NormalizedEBIT", "OperatingIncome"],
        xbrl_raw_keys=["OperatingIncome"],
        xbrl_fallback_tags=["OperatingIncomeLoss"],
        fmp_source="income", fmp_keys=["operatingIncome", "ebit"],
    ),
    FieldSpec(
        label="Depreciation & Amortization", category="Income Statement", tier="critical",
        xbrl_clean_keys=["Depreciation_Total", "Depreciation_Total_Aggregate"],
        xbrl_raw_keys=["Depreciation_Total_Aggregate", "Depreciation_Tangible"],
        xbrl_fallback_tags=[
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
        ],
        fmp_source="income", fmp_keys=["depreciationAndAmortization"],
    ),
    FieldSpec(
        label="Interest Expense", category="Income Statement", tier="nice_to_have",
        xbrl_clean_keys=["InterestExpense"], xbrl_raw_keys=["InterestExpense"],
        xbrl_fallback_tags=["InterestExpense"],
        fmp_source="income", fmp_keys=["interestExpense"],
    ),
    FieldSpec(
        label="Income Tax Expense", category="Income Statement", tier="critical",
        xbrl_clean_keys=["TaxExpense"],
        xbrl_raw_keys=["TaxExpense"],
        xbrl_fallback_tags=["IncomeTaxExpenseBenefit"],
        fmp_source="income", fmp_keys=["incomeTaxExpense"],
    ),
    FieldSpec(
        label="Net Income", category="Income Statement", tier="critical",
        xbrl_clean_keys=["NetIncome"], xbrl_raw_keys=["NetIncome"],
        xbrl_fallback_tags=["NetIncomeLoss"],
        fmp_source="income", fmp_keys=["netIncome"],
    ),
    FieldSpec(
        label="Effective Tax Rate", category="Income Statement", tier="critical",
        xbrl_clean_keys=["GAAP_TaxRate"], xbrl_raw_keys=[],
        xbrl_fallback_tags=[],
        # FMP doesn't publish effective tax rate directly; we leave
        # fmp_keys empty so the comparison cell renders as ``incomplete``
        # on the FMP side. The XBRL side is the analyst-facing value.
        fmp_source=None, fmp_keys=[],
        note="Computed by cleaning_engine domain-10; FMP not published",
    ),
    FieldSpec(
        label="Shares Diluted", category="Income Statement", tier="important",
        xbrl_clean_keys=["SharesDiluted"], xbrl_raw_keys=["SharesDiluted"],
        xbrl_fallback_tags=["WeightedAverageNumberOfDilutedSharesOutstanding"],
        fmp_source="income", fmp_keys=["weightedAverageShsOutDil"],
    ),
]


# ─────────────────────────────────────────────────────────────────────
# Balance Sheet (14 fields)
# ─────────────────────────────────────────────────────────────────────

_BALANCE = [
    FieldSpec(
        label="Total Assets", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["TotalAssets"], xbrl_raw_keys=["TotalAssets"],
        xbrl_fallback_tags=["Assets"],
        fmp_source="balance", fmp_keys=["totalAssets"],
    ),
    FieldSpec(
        label="Cash", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["Cash"], xbrl_raw_keys=["Cash"],
        xbrl_fallback_tags=["CashAndCashEquivalentsAtCarryingValue"],
        fmp_source="balance", fmp_keys=["cashAndCashEquivalents", "cashAndShortTermInvestments"],
    ),
    FieldSpec(
        label="Accounts Receivable", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["AccountsReceivable"], xbrl_raw_keys=["AccountsReceivable"],
        xbrl_fallback_tags=["AccountsReceivableNetCurrent"],
        fmp_source="balance", fmp_keys=["accountsReceivables", "netReceivables"],
    ),
    FieldSpec(
        label="Inventory", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["Inventory"], xbrl_raw_keys=["Inventory"],
        xbrl_fallback_tags=["InventoryNet"],
        fmp_source="balance", fmp_keys=["inventory"],
    ),
    FieldSpec(
        label="PP&E (Net)", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["PPE"], xbrl_raw_keys=["PPE"],
        xbrl_fallback_tags=["PropertyPlantAndEquipmentNet"],
        fmp_source="balance", fmp_keys=["propertyPlantEquipmentNet"],
    ),
    FieldSpec(
        label="Goodwill", category="Balance Sheet", tier="important",
        xbrl_clean_keys=["Goodwill"], xbrl_raw_keys=["Goodwill"],
        xbrl_fallback_tags=["Goodwill"],
        fmp_source="balance", fmp_keys=["goodwill"],
    ),
    FieldSpec(
        label="Total Liabilities", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["TotalLiabilities"], xbrl_raw_keys=["TotalLiabilities"],
        xbrl_fallback_tags=["Liabilities"],
        fmp_source="balance", fmp_keys=["totalLiabilities"],
    ),
    FieldSpec(
        label="Accounts Payable", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["AccountsPayable"], xbrl_raw_keys=["AccountsPayable"],
        xbrl_fallback_tags=["AccountsPayableCurrent"],
        fmp_source="balance", fmp_keys=["accountPayables", "accountsPayable"],
    ),
    FieldSpec(
        label="Short-Term Debt", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["ShortTermDebt"], xbrl_raw_keys=["ShortTermDebt"],
        xbrl_fallback_tags=[
            "ShortTermBorrowings",
            "CommercialPaper",
            "LongTermDebtCurrent",
        ],
        fmp_source="balance", fmp_keys=["shortTermDebt"],
    ),
    FieldSpec(
        label="Long-Term Debt", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["LongTermDebt"], xbrl_raw_keys=["LongTermDebt"],
        xbrl_fallback_tags=["LongTermDebtNoncurrent", "LongTermDebt"],
        fmp_source="balance", fmp_keys=["longTermDebt"],
    ),
    FieldSpec(
        label="Retained Earnings", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["RetainedEarnings"], xbrl_raw_keys=["RetainedEarnings"],
        xbrl_fallback_tags=["RetainedEarningsAccumulatedDeficit"],
        fmp_source="balance", fmp_keys=["retainedEarnings"],
    ),
    FieldSpec(
        label="Treasury Stock", category="Balance Sheet", tier="important",
        xbrl_clean_keys=["TreasuryStock"], xbrl_raw_keys=["TreasuryStock"],
        xbrl_fallback_tags=["TreasuryStockValue", "TreasuryStockCommonValue"],
        fmp_source="balance", fmp_keys=["treasuryStock"],
        # Treasury reported as negative on balance sheet by GAAP
        # convention; both XBRL and FMP follow it so no abs needed.
    ),
    FieldSpec(
        label="Accumulated OCI", category="Balance Sheet", tier="important",
        xbrl_clean_keys=["AOCI"], xbrl_raw_keys=["AOCI"],
        xbrl_fallback_tags=["AccumulatedOtherComprehensiveIncomeLossNetOfTax"],
        fmp_source="balance", fmp_keys=["accumulatedOtherComprehensiveIncomeLoss"],
    ),
    FieldSpec(
        label="Total Equity", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["TotalEquity", "EquityParentOnly"],
        xbrl_raw_keys=["TotalEquity", "EquityParentOnly"],
        xbrl_fallback_tags=[
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "StockholdersEquity",
        ],
        fmp_source="balance", fmp_keys=["totalStockholdersEquity", "totalEquity"],
    ),
]


# ─────────────────────────────────────────────────────────────────────
# Cash Flow (14 fields)
# ─────────────────────────────────────────────────────────────────────

_CASHFLOW = [
    FieldSpec(
        label="Operating CF", category="Cash Flow", tier="critical",
        xbrl_clean_keys=["OperatingCF"], xbrl_raw_keys=["OperatingCF"],
        xbrl_fallback_tags=["NetCashProvidedByUsedInOperatingActivities"],
        fmp_source="cashflow", fmp_keys=["operatingCashFlow"],
    ),
    FieldSpec(
        label="Investing CF", category="Cash Flow", tier="critical",
        xbrl_clean_keys=["InvestingCF"], xbrl_raw_keys=["InvestingCF"],
        xbrl_fallback_tags=["NetCashProvidedByUsedInInvestingActivities"],
        fmp_source="cashflow", fmp_keys=[
            "netCashProvidedByInvestingActivities",
            "netCashUsedForInvestingActivites",  # FMP misspelling, kept for older payloads
        ],
    ),
    FieldSpec(
        label="Financing CF", category="Cash Flow", tier="critical",
        xbrl_clean_keys=["FinancingCF"], xbrl_raw_keys=["FinancingCF"],
        xbrl_fallback_tags=["NetCashProvidedByUsedInFinancingActivities"],
        fmp_source="cashflow", fmp_keys=["netCashProvidedByFinancingActivities"],
    ),
    FieldSpec(
        label="CapEx", category="Cash Flow", tier="critical",
        xbrl_clean_keys=["CapEx_Total", "CapEx"], xbrl_raw_keys=["CapEx"],
        xbrl_fallback_tags=["PaymentsToAcquirePropertyPlantAndEquipment"],
        fmp_source="cashflow", fmp_keys=["capitalExpenditure"],
        abs_compare=True,
        note="FMP reports cash outflow (negative); our cleaning is positive magnitude",
    ),
    FieldSpec(
        label="Stock-Based Compensation", category="Cash Flow", tier="important",
        xbrl_clean_keys=["SBC"], xbrl_raw_keys=["SBC"],
        xbrl_fallback_tags=["ShareBasedCompensation"],
        fmp_source="cashflow", fmp_keys=["stockBasedCompensation"],
    ),
    FieldSpec(
        label="Dividends Paid", category="Cash Flow", tier="critical",
        xbrl_clean_keys=["DividendsPaid"], xbrl_raw_keys=["DividendsPaid"],
        xbrl_fallback_tags=["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
        fmp_source="cashflow", fmp_keys=["commonDividendsPaid", "dividendsPaid"],
        abs_compare=True,
    ),
    FieldSpec(
        label="Cash Taxes Paid", category="Cash Flow", tier="nice_to_have",
        xbrl_clean_keys=["CashTaxesPaid"], xbrl_raw_keys=["CashTaxesPaid"],
        xbrl_fallback_tags=["IncomeTaxesPaidNet", "IncomeTaxesPaid"],
        fmp_source="cashflow", fmp_keys=["incomeTaxesPaid"],
    ),
    FieldSpec(
        label="Δ Accounts Receivable (CF)", category="Cash Flow", tier="important",
        xbrl_clean_keys=[], xbrl_raw_keys=[],
        xbrl_fallback_tags=["IncreaseDecreaseInAccountsReceivable"],
        fmp_source="cashflow", fmp_keys=["accountsReceivables", "changeInReceivables"],
        abs_compare=True,
        note="XBRL reports balance-change direction; FMP reports cash-impact direction (opposite sign)",
    ),
    FieldSpec(
        label="Δ Inventory (CF)", category="Cash Flow", tier="important",
        xbrl_clean_keys=[], xbrl_raw_keys=[],
        xbrl_fallback_tags=["IncreaseDecreaseInInventories"],
        fmp_source="cashflow", fmp_keys=["inventory"],
        abs_compare=True,
    ),
    FieldSpec(
        label="Δ Accounts Payable (CF)", category="Cash Flow", tier="important",
        xbrl_clean_keys=[], xbrl_raw_keys=[],
        xbrl_fallback_tags=["IncreaseDecreaseInAccountsPayable"],
        fmp_source="cashflow", fmp_keys=["accountsPayables"],
        abs_compare=True,
    ),
    FieldSpec(
        label="Debt Issued", category="Cash Flow", tier="important",
        xbrl_clean_keys=[], xbrl_raw_keys=[],
        xbrl_fallback_tags=[
            "ProceedsFromIssuanceOfLongTermDebt",
            "ProceedsFromIssuanceOfDebt",
        ],
        fmp_source="cashflow", fmp_keys=["longTermDebtIssuance", "debtIssuance"],
    ),
    FieldSpec(
        label="Debt Repaid", category="Cash Flow", tier="important",
        xbrl_clean_keys=[], xbrl_raw_keys=[],
        xbrl_fallback_tags=[
            "RepaymentsOfLongTermDebt",
            "RepaymentsOfDebt",
        ],
        fmp_source="cashflow", fmp_keys=["longTermDebtRepayment", "debtRepayment"],
        abs_compare=True,
    ),
    FieldSpec(
        label="FX Effect on Cash", category="Cash Flow", tier="important",
        xbrl_clean_keys=[], xbrl_raw_keys=[],
        xbrl_fallback_tags=[
            "EffectOfExchangeRateOnCashAndCashEquivalents",
            "EffectOfExchangeRateOnCash",
        ],
        fmp_source="cashflow", fmp_keys=["effectOfForexChangesOnCash"],
    ),
    FieldSpec(
        label="Acquisitions of Businesses", category="Cash Flow", tier="nice_to_have",
        xbrl_clean_keys=[], xbrl_raw_keys=[],
        xbrl_fallback_tags=[
            "PaymentsToAcquireBusinessesNetOfCashAcquired",
            "PaymentsToAcquireBusinessesGross",
        ],
        fmp_source="cashflow", fmp_keys=["acquisitionsNet"],
        abs_compare=True,
    ),
]


CATALOG: List[FieldSpec] = _INCOME + _BALANCE + _CASHFLOW


CATEGORIES = ["Income Statement", "Balance Sheet", "Cash Flow"]


def fields_in_category(category: str) -> List[FieldSpec]:
    return [f for f in CATALOG if f.category == category]


__all__ = [
    "FieldSpec",
    "CATALOG",
    "CATEGORIES",
    "fields_in_category",
]
