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
    # Component-sum fallback. When the catalog's primary ``xbrl_fallback_tags``
    # all miss for a given (ticker, period), the resolver tries to SUM these
    # tags instead of picking the first hit. Needed for filers (e.g. MSFT
    # quarterly) that report D&A as ``Depreciation`` +
    # ``AmortizationOfIntangibleAssets`` + ``FinanceLeaseRightOfUseAssetAmortization``
    # without an aggregate tag. Empty list = no sum-mode fallback.
    xbrl_sum_tags: List[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        # Frozen dataclass — bypass setattr via object.__setattr__ for the
        # default-mutable list field. None means "no sum-mode fallback."
        if self.xbrl_sum_tags is None:
            object.__setattr__(self, "xbrl_sum_tags", [])


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
        fmp_source="income", fmp_keys=["costAndExpenses"],
        note=(
            "We compare to FMP's ``costAndExpenses`` (the TRUE total: "
            "COGS + R&D + S&M + G&A), not FMP's ``operatingExpenses`` "
            "(which is SG&A-only, excluding COGS — a $10B+ apparent "
            "drift on META Q1 2026 that's purely definitional). Our "
            "cleaner resolves to the filer's ``CostsAndExpenses`` tag "
            "which has the same total-OpEx semantics. Verified on "
            "META Q1 2026: COGS $10.22B + opEx $23.22B = "
            "costAndExpenses $33.44B, matches XBRL exactly."
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
        # Component-sum fallback for filers that report D&A as discrete
        # line items rather than the aggregate tag (MSFT 10-Q is the
        # canonical case — Depreciation + AmortizationOfIntangibleAssets +
        # FinanceLeaseRightOfUseAssetAmortization, no aggregate).
        # Order doesn't matter — the resolver sums whichever resolve.
        xbrl_sum_tags=[
            "Depreciation",
            "AmortizationOfIntangibleAssets",
            "FinanceLeaseRightOfUseAssetAmortization",
        ],
        fmp_source="income", fmp_keys=["depreciationAndAmortization"],
    ),
    FieldSpec(
        label="Interest Expense", category="Income Statement", tier="nice_to_have",
        xbrl_clean_keys=["InterestExpense"], xbrl_raw_keys=["InterestExpense"],
        xbrl_fallback_tags=[
            "InterestExpense",
            "InterestExpenseDebt",
            "InterestExpenseLongTermDebt",
            "InterestExpenseNonoperating",
        ],
        fmp_source="income", fmp_keys=["interestExpense"],
        note=(
            "Filers switch tags across years — META used "
            "``InterestExpense`` 2013-2019 and 2023, then moved to "
            "``InterestExpenseDebt`` 2024-2025. Fallback list is "
            "ordered most-aggregate first so the headline tag wins "
            "when filers publish both. "
            "QUARTERLY-FILING GAP: modern META 10-Qs (2024+) do NOT "
            "tag accrual interest expense in standard us-gaap — they "
            "only tag ``InterestPaidNet`` (cash interest paid, a "
            "different concept). FMP's quarterly ``interestExpense`` "
            "comes from the 10-Q text/tables, not structured XBRL, "
            "so quarterly Raw column will be ``incomplete`` for these "
            "filers. Do not substitute ``InterestPaidNet`` — cash vs "
            "accrual diverge by accrued-but-unpaid coupon amounts."
        ),
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
        note=(
            "Structural null for filers with no physical goods "
            "(META, GOOGL, V, MA, MSFT-services-only filings, etc.). "
            "FMP reports 0; XBRL filers omit the tag entirely. The "
            "``incomplete`` tier on these cells reflects the "
            "None-vs-zero mismatch — both sources agree there is no "
            "inventory."
        ),
    ),
    FieldSpec(
        label="PP&E (Net)", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["PPE"], xbrl_raw_keys=["PPE"],
        xbrl_fallback_tags=[
            "PropertyPlantAndEquipmentNet",
            "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
        ],
        fmp_source="balance", fmp_keys=["propertyPlantEquipmentNet"],
        note=(
            "Filers that capitalize finance leases inline (META 2022+, "
            "many tech filers) use the compound "
            "``PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAsset...`` "
            "tag instead of the simple ``PropertyPlantAndEquipmentNet``. "
            "Both forms in fallback list. "
            "DEFINITIONAL DIVERGENCE: FMP includes OPERATING-lease ROU "
            "assets in PP&E; META reports them on a separate line "
            "(``OperatingLeaseRightOfUseAsset``). On FY2025: META "
            "$176.4B (PP&E + Finance ROU) + $20.4B (Operating ROU) = "
            "FMP $196.8B. Material drift here is a methodology choice "
            "(Category C), not a cleaning bug."
        ),
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
        xbrl_fallback_tags=[
            "AccountsPayableCurrent",
            "AccountsPayableTradeCurrent",
            "AccountsPayableAndAccruedLiabilitiesCurrent",
            "AccountsPayableAndOtherAccruedLiabilitiesCurrent",
        ],
        fmp_source="balance", fmp_keys=["accountPayables", "accountsPayable"],
        note=(
            "Filers split AP across multiple tags — META stopped "
            "filing ``AccountsPayableCurrent`` after 2020 and switched "
            "to ``AccountsPayableTradeCurrent`` (+ ``AccountsPayableOtherCurrent`` "
            "which we don't aggregate). Fallback list resolves trade-AP "
            "for META; cross-filer total may understate when filers "
            "split trade vs other and we only pick the first match."
        ),
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
        # Altman WC term (CurrentAssets - CurrentLiabilities). Previously flowed
        # into record.raw as "CurrentAssets" but was never a cataloged field or
        # a materialized column; formalized here for provenance + bounds.
        label="Current Assets", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["CurrentAssets"], xbrl_raw_keys=["CurrentAssets"],
        xbrl_fallback_tags=["AssetsCurrent"],
        fmp_source="balance", fmp_keys=["totalCurrentAssets"],
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
        xbrl_fallback_tags=[
            "IncreaseDecreaseInAccountsPayable",
            "IncreaseDecreaseInAccountsPayableTrade",
            "IncreaseDecreaseInAccountsPayableAndAccruedLiabilities",
            "IncreaseDecreaseInAccountsPayableAndOtherOperatingLiabilities",
        ],
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
            # Pre-ASU-2016-18 (legacy filings, ~2010-2017).
            "EffectOfExchangeRateOnCashAndCashEquivalents",
            "EffectOfExchangeRateOnCash",
            # Post-ASU-2016-18 (restricted-cash inclusion, ~2018+).
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            # Disposal-group variant — MSFT and other filers with
            # ongoing discontinued-operations disclosure use the
            # longest us-gaap form. The disclosure is required even
            # when there are no actual disposal groups (defensive
            # taxonomy); MSFT switched to it in FY2022.
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
        ],
        fmp_source="cashflow", fmp_keys=["effectOfForexChangesOnCash"],
        note=(
            "Filers switched tags around 2018-2020 when restricted-cash "
            "ASU 2016-18 took effect — modern filings use "
            "``EffectOfExchangeRateOnCashCashEquivalentsRestrictedCash...``. "
            "MSFT and similar filers use the ``IncludingDisposalGroupAnd"
            "DiscontinuedOperations`` variant. Fallback list covers all eras."
        ),
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
