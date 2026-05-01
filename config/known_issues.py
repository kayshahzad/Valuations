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
    ]
}
