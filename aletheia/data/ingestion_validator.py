"""
aletheia/data/ingestion_validator.py

Phase 2.2 — Code-Based Ingestion Validation Gate
================================================
Validates every CleanedRecord after calculation. 
Uses a typed, config-driven contract system that tests can assert against.

Returns a ValidationResult object. Expected validation failures 
(bounds limits, missing required tags) populate the failures list.
Exceptions are reserved for unexpected operational issues.
"""

import logging
from datetime import date
from typing import List, Optional, Any, Callable, Dict, Tuple
from dataclasses import dataclass, field
from aletheia.data.cleaning_engine import CleanedRecord
from config.known_issues import KNOWN_ISSUES

logger = logging.getLogger(__name__)

try:
    from config.industry_routing import get_industry
except ImportError:
    # Fallback if config is not loadable in isolation
    def get_industry(ticker: str) -> str:
        return "default"

@dataclass
class KnownIssue:
    reason: str
    expires_after: str  # e.g. "2026-06-30"

    def __post_init__(self):
        # Fail fast if date is malformed
        date.fromisoformat(self.expires_after)

@dataclass
class FieldContract:
    field: str
    required: bool = False
    min_val: float = float('-inf')
    max_val: float = float('inf')
    description: str = ""
    error_template: str = "Field '{field}' value {actual} violates absolute bounds [{min_val}, {max_val}]. {description}"
    bypass_sectors: List[str] = field(default_factory=list)

@dataclass
class RelativeContract:
    field: str
    relative_to: str
    min_pct: float
    max_pct: float
    archetype_overrides: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    bypass_sectors: List[str] = field(default_factory=list)
    description: str = ""
    error_template: str = "Field '{field}' relative to '{relative_to}' is {actual:.1f}%. Expected between {min_pct}% and {max_pct}%. {description}"

@dataclass
class IdentityContract:
    name: str
    predicate: Callable[[CleanedRecord], bool]
    error_template: str

@dataclass
class ValidationFailure:
    field: str
    reason: str
    expected: Any = None
    actual: Any = None
    message: str = ""

@dataclass
class ValidationResult:
    is_valid: bool
    failures: List[ValidationFailure] = field(default_factory=list)


class IngestionValidator:
    """
    Validates a CleanedRecord against strict, data-driven contracts.
    Returns a ValidationResult containing structured failures.
    """
    
    # Contract: Absolute Value Rules
    ABSOLUTE_CONTRACTS = [
        FieldContract("raw_Revenue", required=True, min_val=0.001, description="Revenue must be strictly positive"),
        FieldContract("raw_TotalAssets", required=True, min_val=0.001, description="TotalAssets must be strictly positive"),
        FieldContract("raw_TotalEquity", required=True, description="TotalEquity is required for ROE/InvestedCapital"),
        FieldContract("CapEx", required=True, description="CapEx is required", bypass_sectors=["bank", "financials", "insurance"]),
        FieldContract("TotalLiabilities", required=True, description="TotalLiabilities is required"),
        FieldContract("Cash", required=True, description="Cash is required", bypass_sectors=["bank", "financials", "insurance"]),
        FieldContract("OperatingIncome", required=True, description="OperatingIncome is required", bypass_sectors=["bank", "financials", "insurance"]),
        FieldContract("derived_ROIC", min_val=-80.0, max_val=10.0, description="ROIC outside bounds is anomalous"),
        FieldContract("derived_EBIT_Margin_Pct", min_val=-50.0, max_val=80.0, description="EBIT margin outside -50% to 80% is anomalous"),
    ]

    # Contract: Relative Check Rules (Field as a percentage of Relative_To)
    RELATIVE_CONTRACTS = [
        RelativeContract(
            field="CapEx", 
            relative_to="raw_Revenue", 
            min_pct=0.1, 
            max_pct=50.0, 
            archetype_overrides={"utility": (0.1, 60.0), "energy": (0.1, 60.0), "real_estate": (0.1, 60.0), "industrials": (0.1, 60.0)},
            bypass_sectors=["bank", "financials", "insurance"],
            description="CapEx typically 0.1% - 30% of Revenue (bypassed for financials)"
        ),
        RelativeContract(
            field="derived_EBITDA",
            relative_to="raw_Revenue",
            min_pct=4.0,
            max_pct=100.0,
            archetype_overrides={"retail": (1.0, 100.0), "healthcare": (1.0, 100.0)},
            bypass_sectors=["bank", "financials", "insurance"],
            description="EBITDA margin typically >= 4%"
        ),
        # Altman-input plausibility (Phase 1a). Purpose is catching gross
        # tag-resolution / unit errors at ingest, NOT flagging real distress.
        # Bounds were widened after a universe sweep showed legitimate values
        # the naive limits would have quarantined:
        #   RE/TA: TXN +2.2 (asset-light, cash-rich), AMD -2.4 (2015 deficit),
        #          TSLA -4.0 (early-stage) are all real → cap at 10x, which
        #          only trips on unit errors (~1000x) or gross mis-resolution.
        #   CurrentAssets ⊆ TotalAssets is an accounting identity → hard 0..TA.
        #   CurrentLiabilities CAN exceed TA when equity is negative (TSLA 2008
        #          CL/TA 1.7 is real) → require positive, cap generously at 3x.
        RelativeContract(
            field="raw_RetainedEarnings",
            relative_to="raw_TotalAssets",
            min_pct=-1000.0, max_pct=1000.0,
            description="|RetainedEarnings| above 10x TotalAssets indicates a unit/tag error"
        ),
        RelativeContract(
            field="raw_CurrentAssets",
            relative_to="raw_TotalAssets",
            min_pct=0.0, max_pct=102.0,   # 2% slack for rounding/snapshot timing on the CA⊆TA identity
            bypass_sectors=["bank", "financials", "insurance"],
            description="CurrentAssets is a subset of TotalAssets: must be ~0..TA"
        ),
        RelativeContract(
            field="raw_LiabilitiesCurrent",
            relative_to="raw_TotalAssets",
            min_pct=0.0, max_pct=300.0,
            bypass_sectors=["bank", "financials", "insurance"],
            description="CurrentLiabilities positive; can exceed TA on negative equity, but >3x is an error"
        ),
        # Pre-expansion field batch. Inventory / PP&E are subsets of TA (0..TA
        # with slack); ShortTermDebt / InterestExpense are positive and modest
        # vs assets. Generous caps -> catch only gross tag/unit errors.
        RelativeContract(field="raw_Inventory", relative_to="raw_TotalAssets",
                         min_pct=0.0, max_pct=102.0, bypass_sectors=["bank", "financials", "insurance"],
                         description="Inventory is a subset of TotalAssets"),
        RelativeContract(field="raw_PPE", relative_to="raw_TotalAssets",
                         min_pct=0.0, max_pct=102.0, bypass_sectors=["bank", "financials", "insurance"],
                         description="Net PP&E is a subset of TotalAssets"),
        RelativeContract(field="raw_ShortTermDebt", relative_to="raw_TotalAssets",
                         min_pct=0.0, max_pct=200.0, bypass_sectors=["bank", "financials", "insurance"],
                         description="ShortTermDebt positive; >2x TA is an error"),
        RelativeContract(field="raw_InterestExpense", relative_to="raw_TotalAssets",
                         min_pct=0.0, max_pct=50.0, bypass_sectors=["bank", "financials", "insurance"],
                         description="InterestExpense positive and small vs assets"),
    ]

    # Contract: Accounting Identities
    ACCOUNTING_IDENTITIES = [
        IdentityContract(
            name="dna_presence",
            predicate=lambda r: any(
                r.raw.get(s) is not None for s in [
                    "Depreciation_Total_Aggregate",
                    "Depreciation_Tangible",
                    "IntangibleAmortization",
                    "FinanceLeaseAmortization",
                    "CapitalizedSoftwareAmortization"
                ]
            ),
            error_template="Validation Failure: D&A_AllSources. No D&A source resolved at raw layer."
        ),
        IdentityContract(
            name="ebitda_ge_ebit",
            predicate=lambda r: (
                (r.derived.get("EBITDA_Margin_Pct") is None) or 
                (r.derived.get("EBIT_Margin_Pct") is None) or 
                (r.derived.get("EBITDA_Margin_Pct") >= r.derived.get("EBIT_Margin_Pct"))
            ),
            error_template="Accounting Identity Violated: EBITDA margin ({ebitda_margin:.1f}%) < EBIT margin ({ebit_margin:.1f}%). D&A must be positive."
        ),
    ]

    @classmethod
    def get_absolute_contracts(cls) -> List[FieldContract]:
        return cls.ABSOLUTE_CONTRACTS
        
    @classmethod
    def get_relative_contracts(cls) -> List[RelativeContract]:
        return cls.RELATIVE_CONTRACTS

    @classmethod
    def get_identity_contracts(cls) -> List[IdentityContract]:
        return cls.ACCOUNTING_IDENTITIES

    @classmethod
    def validate(cls, record: CleanedRecord, sector: str = "") -> ValidationResult:
        """
        Validates the record and returns a ValidationResult.
        """
        ticker = record.ticker
        failures = []
        
        # If sector is not provided, look it up from industry routing
        if not sector:
            sector = get_industry(ticker).lower()
        else:
            sector = sector.lower()

        # Helper to fetch safely from raw, clean, or derived without silent falsy bug
        def get_val(f_name: str) -> Optional[float]:
            if f_name.startswith("raw_"): return record.raw.get(f_name.replace("raw_", ""))
            if f_name.startswith("clean_"): return record.clean.get(f_name.replace("clean_", ""))
            if f_name.startswith("derived_"): return record.derived.get(f_name.replace("derived_", ""))
            
            # Use 'is not None' to preserve 0.0
            val = record.raw.get(f_name)
            if val is not None: return val
            val = record.clean.get(f_name)
            if val is not None: return val
            return record.derived.get(f_name)

        # 1. Evaluate Absolute Contracts
        for contract in cls.ABSOLUTE_CONTRACTS:
            if sector in contract.bypass_sectors:
                logger.info(f"{ticker} bypassed absolute check {contract.field} (sector: {sector})")
                continue
            val = get_val(contract.field)
            if val is None:
                if contract.required:
                    failures.append(ValidationFailure(
                        field=contract.field,
                        reason="missing_field",
                        message=f"Missing required field: '{contract.field}'. {contract.description}"
                    ))
                continue
            
            if not (contract.min_val <= val <= contract.max_val):
                msg = contract.error_template.format(
                    field=contract.field,
                    min_val=contract.min_val,
                    max_val=contract.max_val,
                    actual=val,
                    description=contract.description
                )
                failures.append(ValidationFailure(
                    field=contract.field,
                    reason="bounds_violation",
                    expected=(contract.min_val, contract.max_val),
                    actual=val,
                    message=msg
                ))

        # Base Reference for Relative
        revenue = get_val("raw_Revenue")
        
        # 2. Evaluate Relative Contracts
        if revenue is not None and revenue > 0:
            for contract in cls.RELATIVE_CONTRACTS:
                if sector in contract.bypass_sectors:
                    logger.info(f"{ticker} bypassed {contract.field} check (sector: {sector})")
                    continue

                val = get_val(contract.field)
                if val is None:
                    continue
                    
                pct = (val / revenue) * 100
                
                # Apply archetype overrides
                min_p, max_p = contract.min_pct, contract.max_pct
                if sector in contract.archetype_overrides:
                    min_p, max_p = contract.archetype_overrides[sector]

                if not (min_p <= pct <= max_p):
                    msg = contract.error_template.format(
                        field=contract.field,
                        relative_to=contract.relative_to,
                        min_pct=min_p,
                        max_pct=max_p,
                        actual=pct,
                        description=contract.description
                    )
                    failures.append(ValidationFailure(
                        field=contract.field,
                        reason="relative_bounds_violation",
                        expected=(min_p, max_p),
                        actual=pct,
                        message=msg
                    ))

        # 3. Evaluate Identity Contracts
        for contract in cls.ACCOUNTING_IDENTITIES:
            if not contract.predicate(record):
                # For specific formatting (can be generalized later)
                ebit_m = record.derived.get("EBIT_Margin_Pct", 0)
                ebitda_m = record.derived.get("EBITDA_Margin_Pct", 0)
                
                msg = contract.error_template.format(
                    ebit_margin=ebit_m,
                    ebitda_margin=ebitda_m
                )
                
                failures.append(ValidationFailure(
                    field="accounting_identity",
                    reason=contract.name,
                    expected=True,
                    actual=False,
                    message=msg
                ))

        # Apply KNOWN_ISSUES exemptions. Each issue is scoped by:
        #   - workaround: only "bypass" / "routing_required" / "use_derived" suppress failures
        #   - field:      None = ticker-wide, otherwise only failures on that field
        #   - fiscal_year_min / fiscal_year_max: inclusive bounds; None = open-ended
        if failures and ticker in KNOWN_ISSUES:
            from datetime import date as _date
            fy = record.fiscal_year
            kept: List[ValidationFailure] = []
            for f in failures:
                exempt = False
                for issue in KNOWN_ISSUES[ticker]:
                    if _date.today() > issue.expires_after:
                        logger.error(f"{ticker} KNOWN_ISSUE EXPIRED on {issue.expires_after}. Exemptions no longer applied.")
                        continue
                    if issue.workaround not in ("bypass", "routing_required", "use_derived"):
                        continue
                    if issue.fiscal_year_min is not None and fy < issue.fiscal_year_min:
                        continue
                    if issue.fiscal_year_max is not None and fy > issue.fiscal_year_max:
                        continue
                    if issue.field is not None and f.field != issue.field:
                        continue
                    exempt = True
                    logger.info(f"{ticker} FY{fy} field '{f.field}' exempted by KNOWN_ISSUE: {issue.description[:80]}")
                    break
                if not exempt:
                    kept.append(f)
            failures = kept

        is_valid = len(failures) == 0
        return ValidationResult(is_valid=is_valid, failures=failures)
