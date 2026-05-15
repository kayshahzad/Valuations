"""Per-ticker exception registry for legitimate validation overrides.

The registry is a plain Python dict (not YAML) — appropriate for a
single-user / single-workstation system. Each entry MUST carry:

  - reason: a documented rationale (rejected at load time if absent)
  - created_date: ISO date the entry was added
  - review_by_date: ISO date by which the entry must be re-justified

Entries past their ``review_by_date`` trigger a structured warning at
process startup (``log_past_due_overrides``). The registry should
stay small: if it grows past ~20 entries, that's a signal the
validation rules are too strict, not that the universe has many edge
cases. Recalibrate the rules.

Lookup contract:
  ``is_override_active(ticker, override_key)`` → bool
  returns True only if the entry exists AND has not expired (review
  date is informational; the entry remains active until removed).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# Threshold beyond which the registry size itself is a signal that
# validation rules need recalibration.
_REGISTRY_SIZE_WARNING_THRESHOLD = 20


# ─────────────────────────────────────────────────────────────────────
# The registry.
#
# Format:
#   OVERRIDES[ticker][override_key] = {
#       "reason":          "human-readable rationale",
#       "created_date":    "YYYY-MM-DD",
#       "review_by_date":  "YYYY-MM-DD",
#       # Optional fields:
#       "fields":          ["field_name", ...],   # specific fields scoped
#   }
#
# Phase 0 seed entries (from docs/calculation_anomaly_catalog.md):
# ─────────────────────────────────────────────────────────────────────
OVERRIDES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "MDT": {
        "tax_rate_normal_range": {
            "reason": "Irish-domiciled; effective rate runs 14-16%, "
                      "below the 21-35% U.S. normal band. Use the loose "
                      "[-1.0, 1.0] check only; do not warn on the normal-band.",
            "created_date":    "2026-05-11",
            "review_by_date":  "2026-11-11",
            "fields":          ["tax_rate", "effective_tax_rate"],
        },
    },
    "ASML": {
        "tax_rate_normal_range": {
            "reason": "Dutch-domiciled; non-U.S. statutory rate.",
            "created_date":    "2026-05-11",
            "review_by_date":  "2026-11-11",
            "fields":          ["tax_rate", "effective_tax_rate"],
        },
    },
    "TSM": {
        "tax_rate_normal_range": {
            "reason": "Taiwan-domiciled; non-U.S. statutory rate.",
            "created_date":    "2026-05-11",
            "review_by_date":  "2026-11-11",
            "fields":          ["tax_rate", "effective_tax_rate"],
        },
        "accounting_equation_foreign_filer_20f": {
            "reason": "Foreign filer (Taiwan); files SEC Form 20-F with "
                      "IFRS-derived XBRL taxonomy. TotalEquity tag includes "
                      "minority-interest components our US-GAAP-tuned "
                      "TotalLiabilities resolver doesn't see, producing a "
                      "0.5-1% A=L+E gap. Real cleaning gap (utility-taxonomy "
                      "family); waiver preserves Stage 3 access until the "
                      "foreign-filer taxonomy work lands.",
            "created_date":    "2026-05-14",
            "review_by_date":  "2026-08-14",
            "fields":          ["accounting_equation_a_eq_l_plus_e"],
        },
    },
    # A3: known negative-equity tickers (buyback-heavy mature companies).
    # These are legitimate; ROE returns "n_a" instead of trying to compute.
    "LOW": {
        "negative_total_equity": {
            "reason": "Buyback-heavy mature retailer; accumulated treasury "
                      "stock exceeds paid-in capital. Multi-year negative "
                      "equity (verified). ROE refuses to compute; report "
                      "as n_a with reason=negative_equity.",
            "created_date":    "2026-05-11",
            "review_by_date":  "2027-05-11",
            "fields":          ["total_equity", "roe"],
        },
        "accounting_equation_negative_equity_era": {
            "reason": "Multi-year negative-equity period (FY2014, FY2015, "
                      "FY2023): heavy buyback program produces RE swings "
                      "where TotalEquity tag resolution diverges from L+E "
                      "by ~3-5%. Related to negative_total_equity above; "
                      "the A=L+E identity is unreliable when equity is "
                      "near-zero or negative.",
            "created_date":    "2026-05-14",
            "review_by_date":  "2027-05-14",
            "fields":          ["accounting_equation_a_eq_l_plus_e"],
        },
    },
    "HD": {
        "negative_total_equity": {
            "reason": "Historical negative equity in FY2018-2021. Same "
                      "pattern as LOW (aggressive buybacks).",
            "created_date":    "2026-05-11",
            "review_by_date":  "2027-05-11",
            "fields":          ["total_equity", "roe"],
        },
    },
    # A14 (from known_issues.md): UNH / CNC float-based business models
    # require DDM, not FCFF DCF. Already bypassed in ingestion_validator;
    # registered here so the calc-layer guards know to skip.
    "UNH": {
        "skip_fcff_dcf": {
            "reason": "Managed-care float-based business; FCFF DCF "
                      "materially mis-prices. DDM required (Phase 3 utility "
                      "taxonomy work).",
            "created_date":    "2026-05-11",
            "review_by_date":  "2027-05-11",
            "fields":          ["dcf_engine", "reverse_dcf"],
        },
    },
    "CNC": {
        "skip_fcff_dcf": {
            "reason": "Managed-care float-based; same pattern as UNH.",
            "created_date":    "2026-05-11",
            "review_by_date":  "2027-05-11",
            "fields":          ["dcf_engine", "reverse_dcf"],
        },
    },
    # A15: NEE uses non-standard XBRL tags for utility CapEx. Bypassed
    # in ingestion_validator; flag here so the calc layer doesn't
    # double-fail on the same condition.
    "NEE": {
        "utility_capex_xbrl": {
            "reason": "Utility filer uses ConstructionInProgressGross / "
                      "PublicUtilitiesAllowanceForFundsUsedDuringConstruction"
                      "Additions instead of standard PP&E additions. Phase 3 "
                      "utility taxonomy mapping pending.",
            "created_date":    "2026-05-11",
            "review_by_date":  "2026-11-11",
            "fields":          ["capex"],
        },
        "utility_total_liabilities_aggregation": {
            "reason": "Utility-filer XBRL: NEE's TotalLiabilities tag "
                      "excludes regulatory liabilities, deferred income tax "
                      "credits, and subsidiary debt aggregations that are "
                      "filed under utility-specific tags (RegulatoryLiability"
                      "Noncurrent, DeferredTaxLiabilitiesNoncurrent, etc.). "
                      "Same root cause family as A15 (utility CapEx XBRL). "
                      "Affects A=L+E identity by 30-50% gap on historical "
                      "FY2009-FY2018; recent years (FY2019+) less affected as "
                      "newer filings use more standardized aggregation. Full "
                      "fix requires utility-sector tag-priority extension.",
            "created_date":    "2026-05-12",
            "review_by_date":  "2027-05-12",
            "fields":          ["accounting_equation_a_eq_l_plus_e"],
        },
    },
    # B1 — TSLA pre-2015 shares_diluted historical coverage gap.
    # Tesla's early-public-company XBRL filings (FY2011-2014) don't
    # populate the diluted-share tag our resolver expects. Current FYs
    # work. Historical data only — current DCF unaffected.
    "TSLA": {
        "shares_diluted_historical_gap_pre_2015": {
            "reason": "Early-public-company XBRL coverage gap (FY2011-2014); "
                      "Tesla pre-2015 filings predate the diluted-share tag "
                      "the resolver expects. Current FYs populate correctly. "
                      "Affects historical analysis only, not current DCF.",
            "created_date":    "2026-05-12",
            "review_by_date":  "2027-05-12",
            "fields":          ["shares_diluted"],
        },
        "accounting_equation_pre_ipo_era": {
            "reason": "Pre-IPO-era reporting (FY2014-2015): TotalEquity tag "
                      "diverges from underlying XBRL components by ~$1B. "
                      "Pattern consistent across Tesla's early public years. "
                      "Identity check waived for these historical years; "
                      "current FYs (2024/2025) reconcile cleanly.",
            "created_date":    "2026-05-14",
            "review_by_date":  "2027-05-14",
            "fields":          ["accounting_equation_a_eq_l_plus_e"],
        },
    },
    # Layer 1 enforcement (Phase 2026-05-14): per-ticker waivers for
    # the accounting-equation identity violations identified in the
    # universe sweep. All historical or foreign-filer edge cases —
    # see docs/layer1_enforcement_predictions_2026-05-14.md.
    "CAT": {
        "accounting_equation_pre_2012_taxonomy": {
            "reason": "FY2009-FY2011: pre-modernization XBRL TotalEquity "
                      "tag aggregation; cleaning resolves to a value that "
                      "diverges from L+E reported sum by 0.5-1.5%. Modern "
                      "filings (FY2012+) reconcile cleanly. Historical "
                      "analysis only.",
            "created_date":    "2026-05-14",
            "review_by_date":  "2027-05-14",
            "fields":          ["accounting_equation_a_eq_l_plus_e"],
        },
    },
    "NVDA": {
        "accounting_equation_fy2016_cumulative_effect": {
            "reason": "FY2016 only: ASC accounting-standard cumulative-effect "
                      "adjustment landed in equity-statement layout not "
                      "captured by current TotalEquity resolver. All other "
                      "FYs reconcile cleanly. Single-year edge case.",
            "created_date":    "2026-05-14",
            "review_by_date":  "2027-05-14",
            "fields":          ["accounting_equation_a_eq_l_plus_e"],
        },
    },
    # A14 — V (Visa) shares_diluted universe-wide ingest bug.
    # tag_resolver.py fails to extract shares_diluted from Visa's XBRL
    # filings (every FY 2009-2025 affected). Real ingest bug; this
    # override prevents the schema contract from refusing to persist
    # while the actual resolver fix is pending. SHORT review date so
    # the ingest fix is forced to be scheduled.
    "V": {
        "shares_diluted_ingest_bug": {
            "reason": "tag_resolver.py fails on Visa's XBRL diluted-share "
                      "tag (universe-wide, every FY). Real ingest bug "
                      "tracked as anomaly A14. Override prevents schema-"
                      "contract refusal while resolver fix is in flight. "
                      "MUST be fixed before this override expires.",
            "created_date":    "2026-05-12",
            "review_by_date":  "2026-08-12",
            "fields":          ["shares_diluted"],
        },
    },
}


def is_override_active(ticker: str, override_key: str) -> bool:
    """True if ticker has the named override registered (any status)."""
    return override_key in OVERRIDES.get(ticker, {})


def get_override(ticker: str, override_key: str) -> Optional[Dict[str, Any]]:
    """Return the override record dict if active, else None."""
    return OVERRIDES.get(ticker, {}).get(override_key)


def log_past_due_overrides() -> int:
    """Walk the registry; log a warning for any entry whose
    review_by_date is past. Returns the count of past-due entries.

    Call at process startup. If the count grows, validation rules are
    probably too strict — analyst should recalibrate rather than
    extend review dates.
    """
    today = date.today()
    n_past_due = 0
    for ticker, entries in OVERRIDES.items():
        for override_key, record in entries.items():
            review_str = record.get("review_by_date")
            if not review_str:
                logger.warning(
                    "override_missing_review_date ticker=%s key=%s",
                    ticker, override_key,
                )
                continue
            try:
                review_date = date.fromisoformat(review_str)
            except ValueError:
                logger.warning(
                    "override_bad_review_date ticker=%s key=%s review_by=%r",
                    ticker, override_key, review_str,
                )
                continue
            if review_date < today:
                n_past_due += 1
                logger.warning(
                    "override_past_due ticker=%s key=%s review_by=%s days_past=%d",
                    ticker, override_key, review_str,
                    (today - review_date).days,
                )

    # Size check — orthogonal signal that rules might be too strict
    total = sum(len(v) for v in OVERRIDES.values())
    if total >= _REGISTRY_SIZE_WARNING_THRESHOLD:
        logger.warning(
            "override_registry_size=%d >= threshold=%d — validation rules "
            "may be too strict; consider recalibration rather than adding "
            "more exceptions.",
            total, _REGISTRY_SIZE_WARNING_THRESHOLD,
        )

    return n_past_due


def _validate_registry_at_import() -> None:
    """Enforce schema on the in-memory registry. Every entry must have
    reason + created_date + review_by_date. Raises at import if any
    entry is malformed — fail loud, not silent."""
    required = {"reason", "created_date", "review_by_date"}
    for ticker, entries in OVERRIDES.items():
        for key, record in entries.items():
            missing = required - record.keys()
            if missing:
                raise RuntimeError(
                    f"OVERRIDES[{ticker!r}][{key!r}] is missing required "
                    f"fields: {sorted(missing)}. Every entry must carry "
                    f"reason + created_date + review_by_date."
                )
            for fld in ("created_date", "review_by_date"):
                try:
                    date.fromisoformat(record[fld])
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"OVERRIDES[{ticker!r}][{key!r}].{fld} = "
                        f"{record[fld]!r} is not a valid ISO date"
                    ) from exc


_validate_registry_at_import()
