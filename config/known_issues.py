from dataclasses import dataclass
from typing import Literal, Optional
from datetime import date

@dataclass(frozen=True)
class KnownIssue:
    ticker: str
    field: Optional[str]
    issue_type: Literal["data_gap_resolver", "data_gap_filer", "model_limitation", "earnings_quality", "regulatory_uncertainty"]
    description: str
    expires_after: date
    workaround: Literal["bypass", "use_derived", "flag_low_confidence", "quarantine", "routing_required"]
    # Optional fiscal-year scoping. When set, the issue applies only to
    # records whose fiscal_year is within [fiscal_year_min, fiscal_year_max].
    # Both bounds inclusive. Either bound may be None (open-ended).
    fiscal_year_min: Optional[int] = None
    fiscal_year_max: Optional[int] = None

KNOWN_ISSUES: dict[str, list[KnownIssue]] = {
    "NEE": [
        KnownIssue(
            ticker="NEE",
            field="CapEx",
            issue_type="data_gap_filer",
            description="Phase 5 deferred utility CapEx aggregation; revisit when NEE valuation becomes operationally needed.",
            expires_after=date(2026, 6, 30),
            workaround="routing_required"
        )
    ],
    "UNH": [
        KnownIssue(
            ticker="UNH",
            field=None,
            issue_type="model_limitation",
            description="UnitedHealth requires an embedded value or DDM model due to float and insurance-like reserves.",
            expires_after=date(2099, 12, 31),
            workaround="routing_required"
        )
    ],
    "CNC": [
        KnownIssue(
            ticker="CNC",
            field=None,
            issue_type="model_limitation",
            description="Centene requires an embedded value or DDM model due to float and insurance-like reserves.",
            expires_after=date(2099, 12, 31),
            workaround="routing_required"
        )
    ],
    "SMCI": [
        KnownIssue(
            ticker="SMCI",
            field=None,
            issue_type="earnings_quality",
            description="Earnings quality concerns from SEC investigation. Reported financials and hyper-growth explicit projections may be unreliable.",
            expires_after=date(2025, 12, 31),
            workaround="flag_low_confidence"
        )
    ],
    "V": [
        KnownIssue(
            ticker="V",
            field="SharesDiluted",
            issue_type="data_gap_filer",
            description="Visa does not file primary diluted-share or EPS facts in structured XBRL companyfacts (data is in 10-K narrative only). DCFEngine cannot compute IPS until a non-XBRL share-count source is wired.",
            expires_after=date(2026, 12, 31),
            workaround="bypass"
        )
    ],
    "NVDA": [
        KnownIssue(
            ticker="NVDA",
            field="CapEx",
            issue_type="data_gap_filer",
            description="NVDA's CapEx for fiscal years 2013-2021 is not exposed in SEC structured XBRL companyfacts. PaymentsToAcquirePropertyPlantAndEquipment annual data only covers FY2011; PaymentsToAcquireProductiveAssets annual data starts FY2022. Latest year (FY2026) resolves cleanly so DCF is unaffected.",
            expires_after=date(2027, 12, 31),
            workaround="use_derived",
            fiscal_year_max=2021
        )
    ]
}
