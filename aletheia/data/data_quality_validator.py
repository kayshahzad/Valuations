"""
aletheia/data/data_quality_validator.py

Executes the Data Validation Framework (Layers 1-3) against a parsed record.
Reads rules from config/validation_rules.py.
"""

from datetime import date as _date
from config.industry_routing import get_industry
from config.known_issues import KNOWN_ISSUES
from config.validation_rules import (
    check_layer1_presence,
    check_layer2_field_level,
    check_layer3_identities
)


def _is_exempt(ticker: str, fiscal_year: int, field_name: str) -> bool:
    """
    True if `field_name` is exempted for `ticker` at `fiscal_year` per
    KNOWN_ISSUES. Honors:
      - workaround in {bypass, routing_required, use_derived}
      - field scoping (None = ticker-wide, else exact field match)
      - fiscal_year_min / fiscal_year_max bounds (inclusive, None = open)
      - expires_after (issues past expiry no longer apply)
    """
    issues = KNOWN_ISSUES.get(ticker, [])
    today = _date.today()
    for issue in issues:
        if today > issue.expires_after:
            continue
        if issue.workaround not in ("bypass", "routing_required", "use_derived"):
            continue
        if issue.fiscal_year_min is not None and fiscal_year < issue.fiscal_year_min:
            continue
        if issue.fiscal_year_max is not None and fiscal_year > issue.fiscal_year_max:
            continue
        if issue.field is not None and issue.field != field_name:
            continue
        return True
    return False


class DataQualityValidator:
    def __init__(self):
        pass

    def validate(self, ticker: str, fiscal_year: int, record: dict) -> dict:
        """
        Validates the record (enriched wide_dict from TagResolver).
        Failures matching an active KNOWN_ISSUE (with field/year scoping) are
        suppressed before the result is returned.
        """
        industry = get_industry(ticker)

        missing_fields = [f for f in check_layer1_presence(record, industry)
                          if not _is_exempt(ticker, fiscal_year, f)]
        field_violations = [v for v in check_layer2_field_level(record, industry)
                            if not _is_exempt(ticker, fiscal_year, v.split()[0])]
        identity_violations = [v for v in check_layer3_identities(record, industry)
                               if not _is_exempt(ticker, fiscal_year, v.split()[0])]

        all_failures = missing_fields + field_violations + identity_violations

        return {
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "industry": industry,
            "passed": len(all_failures) == 0,
            "layer1_missing": missing_fields,
            "layer2_violations": field_violations,
            "layer3_violations": identity_violations,
            "all_failures": all_failures
        }
