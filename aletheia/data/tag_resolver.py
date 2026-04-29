"""
aletheia/data/tag_resolver.py

Tag Resolution Layer
====================
Bridges the gap between canonical_transformer.py output (lowercase tags like
`revenue`, `ebit`, `cash`) and the cleaning engine expectations (PascalCase
like `Revenue`, `OperatingIncome`, `Cash`).

Also handles the XBRL tag → standard tag mapping for tags that the
canonical transformer did not resolve (SBC, lease, pension, etc.) by
reading directly from the raw XBRL facts.

This is the translation layer — it runs BEFORE the cleaning engine
and enriches the canonical record with the full set of metrics.

Usage (called internally by CleaningEngine._load_and_pivot):
    resolver = TagResolver()
    wide_dict = resolver.enrich(wide_dict, ticker, fiscal_year)
"""

import json
from pathlib import Path
from typing import Dict, Optional


# Tags that canonically represent cash outflows but may be reported as negative numbers.
# We normalize these to positive magnitudes at ingest so max() resolution logic works.
SIGNED_OUTFLOW_TAGS = {
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsForCapitalImprovements",
    "PaymentsToAcquireProductiveAssets",
    "PropertyPlantAndEquipmentAdditions",
    "PaymentsToAcquireOtherPropertyPlantAndEquipment",
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
}

# ─────────────────────────────────────────────────────────────────────────────
# Canonical tag name normalization
# Maps what canonical_transformer.py actually writes → standard names
# the cleaning engine expects
# ─────────────────────────────────────────────────────────────────────────────

# These are the actual standard_tag values written by RosettaStoneTransformer
# mapped to the PascalCase names the cleaning engine uses internally.
# Extend this as new tag names appear in the canonical parquet.

CANONICAL_TO_CLEAN = {
    # Income statement
    "revenue":          "Revenue",
    "Revenue":          "Revenue",
    "cogs":             "COGS",
    "COGS":             "COGS",
    "sga":              "SG&A",
    "SG&A":             "SG&A",
    "rd":               "R&D",
    "R&D":              "R&D",
    "opex":             "OperatingExpenses",
    "ebit":             "OperatingIncome",   # transformer writes 'ebit', maps to OperatingIncome
    "EBIT":             "OperatingIncome",
    "OperatingIncome":  "OperatingIncome",
    "operating_income": "OperatingIncome",
    "net_income":       "NetIncome",
    "NetIncome":        "NetIncome",
    "tax_rate":         "TaxExpense",        # transformer writes raw tax expense as 'tax_rate'
    "depreciation":     "Depreciation",
    "Depreciation":     "Depreciation",
    "medical_claims":   "MedicalClaims",

    # Balance sheet
    "cash":             "Cash",
    "Cash":             "Cash",
    "assets":           "TotalAssets",
    "TotalAssets":      "TotalAssets",
    "liabilities":      "TotalLiabilities",
    "TotalLiabilities": "TotalLiabilities",
    "equity":           "TotalEquity",
    "TotalEquity":      "TotalEquity",
    "debt_long":        "LongTermDebt",
    "LongTermDebt":     "LongTermDebt",
    "debt_current":     "ShortTermDebt",
    "total_debt":       "TotalDebt",
    "Assets":           "TotalAssets",
    "Liabilities":      "TotalLiabilities",
    "LiabilitiesCurrent": "CurrentLiabilities",

    # Cash flow
    "capex":            "CapEx",
    "CapEx":            "CapEx",
    "operating_cf":     "OperatingCF",
    "OperatingCF":      "OperatingCF",
}

# Direct XBRL tag → clean name mapping for tags the transformer
# may not have captured but exist in raw XBRL facts.
# These are pulled directly from raw JSON when needed.
XBRL_TO_CLEAN = {
    # Revenue (priority ordered — first match wins)
    "Revenues":                     "Revenue",
    "Revenue":                      "Revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
    "RevenueFromContractsWithCustomers": "Revenue",
    "SalesRevenueNet":              "Revenue",
    "SalesRevenueGoodsNet":         "Revenue",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "Revenue",

    # Operating income
    "OperatingIncomeLoss":          "OperatingIncome",
    "ProfitLossFromOperatingActivities": "OperatingIncome",
    "OperatingProfit":              "OperatingIncome",
    "ProfitLossBeforeTax":          "OperatingIncome",

    # Net income
    "NetIncomeLoss":                "NetIncome",
    "ProfitLoss":                   "NetIncome",
    "ProfitLossAttributableToOwnersOfParent": "NetIncome",

    # Tax
    "IncomeTaxExpenseBenefit":      "TaxExpense",
    "IncomeTaxesPaid":              "CashTaxesPaid",
    "IncomeTaxesPaidNet":           "CashTaxesPaid",

    # COGS / Cost of Revenue
    "CostOfGoodsAndServicesSold":   "COGS",
    "CostOfRevenue":                "COGS",
    "CostOfGoodsSold":              "COGS",
    "CostOfSales":                  "COGS",

    # Pre-tax income
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest":
                                    "PretaxIncome",

    # SBC
    "ShareBasedCompensation":       "SBC",
    "AllocatedShareBasedCompensationExpense": "SBC",

    # Depreciation
    "DepreciationDepletionAndAmortization": "Depreciation",
    "DepreciationAndAmortization":  "Depreciation",
    "DepreciationAmortizationAndAccretionNet": "Depreciation",
    "DepreciationExpense": "Depreciation",
    "AmortisationExpense": "Depreciation",
    "Depreciation": "Depreciation_Tangible",

    # CapEx
    "PaymentsToAcquirePropertyPlantAndEquipment": "CapEx",
    "PaymentsToAcquireProductiveAssets": "CapEx",
    "PropertyPlantAndEquipmentAdditions": "CapEx",
    "PaymentsForCapitalImprovements": "CapEx",
    "PaymentsToAcquireOtherPropertyPlantAndEquipment": "CapEx",
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": "CapEx",

    # Operating Expenses (for fallback EBIT)
    "OperatingExpenses": "OperatingExpenses",

    # Buybacks
    "PaymentsForRepurchaseOfCommonStock": "Buybacks",

    # Cash flows
    "NetCashProvidedByUsedInOperatingActivities": "OperatingCF",
    "CashFlowsFromUsedInOperatingActivities": "OperatingCF",
    "NetCashProvidedByUsedInInvestingActivities": "InvestingCF",
    "NetCashProvidedByUsedInFinancingActivities": "FinancingCF",

    # Balance sheet
    "CashAndCashEquivalentsAtCarryingValue": "Cash",
    "Assets":                       "TotalAssets",
    "Liabilities":                  "TotalLiabilities",
    "StockholdersEquity":           "TotalEquity",
    "EquityAttributableToOwnersOfParent": "TotalEquity",
    "Equity":                       "TotalEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "TotalEquity",
    "LongTermDebtNoncurrent":       "LongTermDebt",
    "LongTermDebt":                 "LongTermDebt",
    "AssetsCurrent":                "CurrentAssets",
    "LiabilitiesCurrent":           "CurrentLiabilities",
    "AccountsReceivableNetCurrent": "AccountsReceivable",
    "InventoryNet":                 "Inventory",
    "GoodwillAndIntangibleAssetsDisclosureAbstract": None,  # skip
    "Goodwill":                     "Goodwill",
    "PropertyPlantAndEquipmentNet": "PPE",

    # Shares
    "WeightedAverageNumberOfDilutedSharesOutstanding": "SharesDiluted",
    "WeightedAverageNumberOfSharesOutstandingBasic":   "SharesBasic",
    "CommonStockSharesOutstanding": "SharesOutstanding",

    # SBC
    "ShareBasedCompensation":       "SBC",

    # Lease (ASC 842)
    "OperatingLeaseRightOfUseAsset":       "ROU_Asset",
    "OperatingLeaseLiabilityCurrent":      "LeaseLiabilityCurrent",
    "OperatingLeaseLiabilityNoncurrent":   "LeaseLiabilityNoncurrent",
    "OperatingLeaseCost":                  "LeaseCost",

    # Pension
    "DefinedBenefitPlanBenefitObligation": "PensionObligation",
    "DefinedBenefitPlanFairValueOfPlanAssets": "PensionPlanAssets",
    "DefinedBenefitPlanServiceCost":       "PensionServiceCost",
    "DefinedBenefitPlanInterestCost":      "PensionInterestCost",
    "DefinedBenefitPlanFundedStatusOfPlan": "PensionFundedStatus",

    # Restructuring / non-recurring
    "RestructuringCharges":                "RestructuringCharges",
    "AssetImpairmentCharges":              "ImpairmentLoss",
    "GoodwillImpairmentLoss":             "GoodwillImpairment",

    # JVA
    "IncomeLossFromEquityMethodInvestments": "JVA_Income",

    # Tax sustainability
    "OperatingLossCarryforwards":         "NOL_Carryforward",
    "DeferredTaxAssetsOperatingLossCarryforwards": "NOL_Carryforward",
    "InventoryLIFOReserve":               "LIFO_Reserve",
    "CapitalizedComputerSoftwareNet":     "CapitalizedSoftware",
    "AmortizationOfIntangibleAssets":     "IntangibleAmortization",

    # Deferred revenue
    "DeferredRevenueCurrent":             "DeferredRevenue",
    "ContractWithCustomerLiabilityCurrent": "DeferredRevenue",

    # Cost of revenue
    "CostOfGoodsAndServicesSold":         "COGS",
    "CostOfRevenue":                      "COGS",
    "CostOfGoodsSold":                    "COGS",
    "CostOfProductsSold":                 "COGS",
    "CostOfServices":                     "COGS",
    "FuelAndPurchasedPower":              "COGS",
    "UtilitiesOperatingExpenseFuelUsed":  "COGS",
    "GrossProfit":                        "GrossProfit",
    "SellingGeneralAndAdministrativeExpense": "SG&A",
    "ResearchAndDevelopmentExpense":      "R&D",
    # Healthcare / Managed Care specific tags
    "PolicyholderBenefitsAndClaimsIncurredNet": "COGS",
    "HealthCareOrganizationMedicalClaimsExpense": "MedicalClaims",
    "BenefitsLossesAndExpenses":          "MedicalClaims",
    "HealthCareCostsMedical":             "MedicalClaims",
    # Net income variants
    "NetIncomeLossAttributableToParentNetOfTax": "NetIncome",
    "IncomeLossFromContinuingOperations": "NetIncome",

    # Non-operating
    "NonoperatingIncomeExpense":          "NonOperatingIncome",
    "OtherNonoperatingIncomeExpense":     "OtherNonOperatingIncome",
    "InterestExpense":                    "InterestExpense",
    
    # Financing / Cash Flow tags
    "FinanceLeasePrincipalPayments": "FinanceLeasePrincipalPayments",
    "RepaymentsOfLongTermCapitalLeaseObligations": "RepaymentsOfLongTermCapitalLeaseObligations",
}


# ─────────────────────────────────────────────────────────────────────────────
# TagResolver class
# ─────────────────────────────────────────────────────────────────────────────

class TagResolver:
    """
    Enriches the canonical wide_dict with:
    1. Normalized PascalCase keys (maps 'revenue' → 'Revenue', 'ebit' → 'OperatingIncome')
    2. Supplemental metrics from raw XBRL that the transformer did not capture
       (SBC, lease liabilities, pension, operating cash flows, etc.)
    """

    CLEAN_NAME_RESOLUTION = {
        "COGS":        "max",   # sub-components shadow total — take largest
        "Depreciation": "sum",  # tangible + intangible = total D&A
        "Revenue":     "max",   # same tag reported under multiple names
        # Note: SBC is another candidate for 'sum', but AllocatedShareBasedCompensationExpense
        # is already the comprehensive total. This dict serves as an extension point for future split-tag patterns.
    }

    def __init__(self, raw_dir: str = "valuation_data/raw/sec"):
        self.raw_dir = Path(raw_dir)
        self._cik_cache: Dict[str, Optional[str]] = {}

    def normalize_keys(self, wide_dict: Dict[str, float]) -> Dict[str, float]:
        """
        Step 1: Normalize all canonical tag names to PascalCase standard names.
        Handles both lowercase (transformer output) and PascalCase (direct XBRL).
        """
        normalized = {}
        for raw_key, value in wide_dict.items():
            clean_key = CANONICAL_TO_CLEAN.get(raw_key, raw_key)
            if clean_key and value is not None:
                # If we already have this key (e.g. both 'revenue' and 'Revenue'),
                # prefer non-zero value
                if clean_key not in normalized or (normalized[clean_key] == 0 and value != 0):
                    normalized[clean_key] = value
        return normalized

    def enrich_from_xbrl(
        self,
        wide_dict: Dict[str, float],
        ticker: str,
        fiscal_year: int,
    ) -> Dict[str, float]:
        """
        Step 2: Pull supplemental metrics directly from raw XBRL facts for
        tags the transformer did not capture.

        Only fills gaps — never overwrites values already in wide_dict.
        """
        raw_facts = self._load_raw_facts(ticker)
        if raw_facts is None:
            return wide_dict

        facts = raw_facts.get("facts", {})
        us_gaap = facts.get("us-gaap", {})
        ifrs = facts.get("ifrs-full", {})
        
        combined_facts = {}
        combined_facts.update(ifrs)
        combined_facts.update(us_gaap)

        enriched = dict(wide_dict)

        # Collect all candidates for each clean_name
        candidates: Dict[str, list[float]] = {}

        for xbrl_tag, clean_name in XBRL_TO_CLEAN.items():
            if clean_name is None:
                continue
            if xbrl_tag not in combined_facts:
                continue
            val = self._extract_value(combined_facts[xbrl_tag], fiscal_year, tag_name=xbrl_tag)
            
            # Normalize sign for cash outflows
            if val is not None and xbrl_tag in SIGNED_OUTFLOW_TAGS:
                val = abs(val)
                
            if val is not None and val > 0:
                candidates.setdefault(clean_name, []).append(val)

        # Resolve: apply strategy (sum or max) for each clean_name
        # Never overwrite a value already in enriched from the canonical transformer
        for clean_name, values in candidates.items():
            strategy = self.CLEAN_NAME_RESOLUTION.get(clean_name, "max")
            resolved = sum(values) if strategy == "sum" else max(values)
            if clean_name not in enriched or enriched[clean_name] in (None, 0.0):
                enriched[clean_name] = resolved

        return enriched

    def enrich(
        self,
        wide_dict: Dict[str, float],
        ticker: str,
        fiscal_year: int,
    ) -> Dict[str, float]:
        """
        Full enrichment pipeline: normalize keys then supplement from XBRL.
        This is the main entry point called by CleaningEngine._load_and_pivot.
        """
        normalized = self.normalize_keys(wide_dict)
        enriched = self.enrich_from_xbrl(normalized, ticker, fiscal_year)
        self._log_missing_tags(enriched, ticker, fiscal_year)
        return enriched

    def _log_missing_tags(self, enriched: Dict[str, float], ticker: str, fiscal_year: int):
        """Audit log for missing critical XBRL tags."""
        required_tags = [
            "Revenue", "OperatingIncome", "NetIncome", 
            "OperatingCF", "Depreciation", "CapEx", 
            "TotalAssets", "TotalLiabilities", "Cash"
        ]
        missing = [tag for tag in required_tags if tag not in enriched or enriched[tag] is None]
        
        if missing:
            log_path = Path("valuation_data/logs/tag_misses.jsonl")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:
                for tag in missing:
                    entry = {"ticker": ticker, "fiscal_year": fiscal_year, "missing_tag": tag}
                    f.write(json.dumps(entry) + "\n")

    def _extract_value(self, concept: dict, fiscal_year: int, tag_name: str = "") -> Optional[float]:
        """
        Extract the value for a given concept and fiscal year from XBRL units.
        Prefers 10-K filings and explicitly filters for full-year durations (~365 days)
        to avoid accidentally picking up Q4 standalone values sometimes filed in 10-Ks.
        It also selects the data point with the latest end date to ensure it grabs the 
        current year's data rather than a prior year's restated data.
        """
        from datetime import datetime

        best_val_exact = None
        best_end_date_exact = ""
        best_val_offset = None
        best_end_date_offset = ""
        best_unit = "USD"
        
        DA_TAGS = {
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "DepreciationAndAmortisationExpense",
        }
        min_days = 300 if tag_name in DA_TAGS else 330
        max_days = 400
        
        for unit_type, units in concept.get("units", {}).items():
            if unit_type not in ("USD", "shares", "pure", "EUR", "TWD", "CAD", "GBP", "JPY", "CHF"):
                continue
            
            for u in units:
                # Accept fiscal_year OR fiscal_year+1
                # Some foreign filers (e.g. ASML 20-F) have SEC fy label
                # offset by +1 from the actual fiscal year end date.
                filing_fy = u.get("fy")
                if u.get("form") not in ("10-K", "20-F", "40-F"):
                    continue
                if filing_fy not in (fiscal_year, fiscal_year + 1):
                    continue
                # Prefer exact match — track whether this is an offset match
                is_offset = (filing_fy == fiscal_year + 1)
                
                # Prevent offset leakage for domestic filers
                if is_offset and u.get("form") == "10-K":
                    continue
                
                # Log when the fallback fires to audit offset filers vs ghost records
                if is_offset:
                    print(f"[AUDIT] Using FY+1 fallback for {tag_name} (found in {filing_fy} {u.get('form')})")
                    
                start = u.get("start")
                end = u.get("end")
                val = u.get("val")
                
                if val is None or not end:
                    continue
                    
                # If it's a point-in-time metric (no start date), just take the latest end date
                if not start:
                    if not is_offset:
                        if end > best_end_date_exact:
                            best_end_date_exact = end
                            best_val_exact = float(val)
                            best_unit = unit_type
                    else:
                        if end > best_end_date_offset:
                            best_end_date_offset = end
                            best_val_offset = float(val)
                            best_unit = unit_type
                    continue
                    
                # If it's a duration metric, ensure it's roughly a year
                try:
                    s_date = datetime.strptime(start, "%Y-%m-%d")
                    e_date = datetime.strptime(end, "%Y-%m-%d")
                    duration = (e_date - s_date).days
                    
                    if min_days <= duration <= max_days:
                        if not is_offset:
                            if end > best_end_date_exact:
                                best_end_date_exact = end
                                best_val_exact = float(val)
                                best_unit = unit_type
                        else:
                            if end > best_end_date_offset:
                                best_end_date_offset = end
                                best_val_offset = float(val)
                                best_unit = unit_type
                except Exception:
                    # Fallback if date parsing fails, just use end date
                    if not is_offset:
                        if end > best_end_date_exact:
                            best_end_date_exact = end
                            best_val_exact = float(val)
                            best_unit = unit_type
                    else:
                        if end > best_end_date_offset:
                            best_end_date_offset = end
                            best_val_offset = float(val)
                            best_unit = unit_type
                        
        best_val = best_val_exact if best_val_exact is not None else best_val_offset
        if best_val is not None and best_unit != "USD" and best_unit not in ("shares", "pure"):
            from aletheia.data.fx_converter import convert_to_usd
            best_val = convert_to_usd(best_val, best_unit, fiscal_year)
            
        return best_val

    def _get_cik(self, ticker: str) -> Optional[str]:
        """Resolve ticker → CIK with caching."""
        if ticker in self._cik_cache:
            return self._cik_cache[ticker]

        cik_path = self.raw_dir / "company_tickers" / "company_tickers.json"
        if not cik_path.exists():
            self._cik_cache[ticker] = None
            return None

        try:
            with open(cik_path) as f:
                data = json.load(f)
            for _, v in data.items():
                if v["ticker"].upper() == ticker.upper():
                    cik = str(v["cik_str"]).zfill(10)
                    self._cik_cache[ticker] = cik
                    return cik
        except Exception:
            pass

        self._cik_cache[ticker] = None
        return None

    def _load_raw_facts(self, ticker: str) -> Optional[dict]:
        """Load raw SEC companyfacts JSON for a ticker."""
        cik = self._get_cik(ticker)
        if not cik:
            return None
        facts_path = self.raw_dir / "companyfacts" / f"CIK{cik}.json"
        if not facts_path.exists():
            return None
        try:
            with open(facts_path) as f:
                return json.load(f)
        except Exception:
            return None
