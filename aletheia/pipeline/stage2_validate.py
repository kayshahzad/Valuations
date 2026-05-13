"""Stage 2 — Validation and Cleaning.

Consumes Stage 1's raw bundle (or a ticker directly during the
adapter phase) and produces ``List[ValidatedCleanedRecord]`` — one
per (ticker, period) pair. The typed records carry the cleaning
engine's raw/clean/derived dicts plus a ``ValidationReceipt`` with
schema-contract violations, the FMP Gate A.TTM outcome, and the
override-registry entries active during the run.

Boundary discipline (enforced by ``tests/architecture/
test_pipeline_layering.py``):
  - No imports from ``aletheia.tools.*``, ``aletheia.agents``, or
    ``aletheia.workflow``.
  - Stage 2 owns the cleaning_engine, ingestion_validator,
    fmp_validation, schema_contract, override registry, and FX
    conversion — these belong on this side of the boundary.

For Week 5 the module is a thin orchestrator over the existing
``cleaning_engine.clean_all_years``. The cleaning engine still
fetches its own data from edgar_client and fmp_client caches; once
the Week 6 orchestrator wires Stage 1 → Stage 2, the cleaning engine
will read from the bundle's payload paths instead.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aletheia.calculations import (
    OVERRIDES,
    validate_cleaned_record_schema_contract,
)
from aletheia.contracts.pipeline import (
    ValidatedCleanedRecord,
    ValidationReceipt,
)
from aletheia.data.cleaning_engine import CleaningEngine


# ─────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────

class Stage2ValidateError(Exception):
    """Stage 2 input contract violation or unrecoverable cleaning failure."""


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _overrides_active_for(ticker: str) -> List[str]:
    """Return the override-registry keys currently active for
    ``ticker``. Used in the validation receipt + record fingerprint
    so downstream cache invalidation triggers when overrides change.
    """
    ticker_overrides = OVERRIDES.get(ticker, {})
    return sorted(ticker_overrides.keys())


def _overrides_state_hash(ticker: str) -> str:
    """A stable hash of the override entries active for ``ticker``,
    folded into the record fingerprint. Bumping any override entry
    (reason, review date, fields) flips the hash and busts the cache.
    """
    ticker_overrides = OVERRIDES.get(ticker, {})
    payload = json.dumps(ticker_overrides, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _compute_record_fingerprint(
    *,
    ticker: str,
    fiscal_year: int,
    period: str,
    period_end_date: str,
    input_bundle_fingerprint: str,
    overrides_hash: str,
    pipeline_version: str,
) -> str:
    """SHA-256 over (input_bundle_fingerprint + cleaning_engine
    version-equivalent + override registry state + per-record
    identity). Stable when nothing material has changed; busts when
    ingest data changes, cleaning logic changes, or overrides change.
    """
    parts = [
        ticker,
        str(fiscal_year),
        period,
        period_end_date,
        input_bundle_fingerprint,
        overrides_hash,
        pipeline_version,
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _cleaned_record_to_validated(
    cleaned_record,
    *,
    schema_violations: List[Dict[str, Any]],
    overrides_applied: List[str],
    input_bundle_fingerprint: str,
    overrides_hash: str,
    pipeline_version: str,
) -> ValidatedCleanedRecord:
    """Adapt one ``CleanedRecord`` dataclass instance to the typed
    Pydantic contract.

    Preserves the raw/clean/derived dict shape exactly; the contract
    formalises the existing structure without restructuring data.
    """
    period = getattr(cleaned_record, "period", "FY") or "FY"
    if period not in ("FY", "TTM", "Q1", "Q2", "Q3", "Q4"):
        period = "FY"

    period_end_date = getattr(cleaned_record, "period_end_date", None)
    if not period_end_date:
        # Fall back to fiscal-year end stub when the cleaner couldn't
        # anchor a date (rare; sec_quarterly handles most of these).
        period_end_date = f"{cleaned_record.fiscal_year}-12-31"
    else:
        period_end_date = str(period_end_date)[:10]

    record_fp = _compute_record_fingerprint(
        ticker=cleaned_record.ticker,
        fiscal_year=cleaned_record.fiscal_year,
        period=period,
        period_end_date=period_end_date,
        input_bundle_fingerprint=input_bundle_fingerprint,
        overrides_hash=overrides_hash,
        pipeline_version=pipeline_version,
    )

    # Quality score is computed by the cleaning engine's domain
    # scoring; default to a neutral 1.0 when absent (e.g., a partial
    # cleaning result that didn't run every domain).
    quality = getattr(cleaned_record, "overall_quality_score", None)
    if quality is None:
        quality = 1.0
    quality = max(0.0, min(1.0, float(quality)))

    # Cleaning audit trail. ``flags`` on the cleaning record carries
    # structured CleaningFlag entries; ``warnings`` / ``blocking_errors``
    # carry the human-readable messages downstream surfaces.
    warnings = list(getattr(cleaned_record, "warnings", []) or [])
    errors = list(getattr(cleaned_record, "errors", []) or [])

    # Serialise the raw/clean dicts as the opaque blob fields so
    # Stage 3's ScreeningEngine + MultipleDecomposition can read
    # balance-sheet sub-fields via _get_json (the same path the
    # existing DB roundtrip uses).
    raw_blob = json.dumps(
        {k: v for k, v in (cleaned_record.raw or {}).items() if v is not None},
        default=str,
    )
    clean_blob = json.dumps(
        {k: v for k, v in (cleaned_record.clean or {}).items() if v is not None},
        default=str,
    )

    return ValidatedCleanedRecord(
        ticker=cleaned_record.ticker,
        fiscal_year=cleaned_record.fiscal_year,
        period=period,
        period_end_date=period_end_date,
        raw=_coerce_dict_to_optional_floats(cleaned_record.raw or {}),
        clean=_coerce_dict_to_optional_floats(cleaned_record.clean or {}),
        derived=_coerce_dict_to_optional_floats(
            getattr(cleaned_record, "derived", {}) or {}
        ),
        raw_blob_json=raw_blob,
        clean_blob_json=clean_blob,
        overall_quality_score=quality,
        cleaning_warnings=warnings,
        blocking_errors=errors,
        validation=ValidationReceipt(
            schema_violations=schema_violations,
            fmp_validation={},  # populated when fmp_validation Gate A receipt is wired in
            cross_source_agreement={},
            overrides_applied=overrides_applied,
        ),
        record_fingerprint=record_fp,
        input_bundle_fingerprint=input_bundle_fingerprint,
        cleaned_at=datetime.now(timezone.utc),
        pipeline_version=pipeline_version,
    )


def _coerce_dict_to_optional_floats(d: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Coerce a dict of mixed values to float|None. Non-coercible
    entries are dropped — the typed contract is float-only at the
    dict level; blob fields carry richer payloads when needed."""
    out: Dict[str, Optional[float]] = {}
    for k, v in d.items():
        if v is None:
            out[k] = None
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        out[k] = f
    return out


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────

def run_stage2(
    *,
    ticker: str,
    pipeline_version: str,
    input_bundle_fingerprint: Optional[str] = None,
    fiscal_years: Optional[List[int]] = None,
) -> List[ValidatedCleanedRecord]:
    """Run cleaning + validation for a ticker, producing typed records.

    Args:
        ticker: Symbol (case-insensitive).
        pipeline_version: Git SHA / version string stamped on each
            record; folded into the per-record fingerprint.
        input_bundle_fingerprint: Pointer back to the
            ``IngestedRawBundle`` that produced these records. When
            None, a sentinel string marks the pre-orchestrator
            adapter path (Week 6 wires this in fully).
        fiscal_years: Optional whitelist. When None, every fiscal
            year the cleaning engine can produce is included.

    Raises:
        Stage2ValidateError: when the cleaning engine returns zero
            records (no canonical data, ingestion failure upstream).
    """
    ticker = ticker.upper()
    bundle_fp = input_bundle_fingerprint or "<pre-orchestrator-adapter>"

    # Snapshot override state ONCE so every record gets the same
    # hash — important for cascade invalidation semantics.
    overrides_hash = _overrides_state_hash(ticker)
    overrides_applied = _overrides_active_for(ticker)

    # Run the cleaning engine. It owns its own data access (parquet +
    # raw XBRL + FMP cache) until the Week 6 orchestrator wires the
    # Stage 1 bundle in as the input.
    engine = CleaningEngine(verbose=False)
    cleaned_records = engine.clean_all_years(ticker)
    if not cleaned_records:
        raise Stage2ValidateError(
            f"Cleaning engine produced zero records for {ticker!r}. "
            "Check Stage 1 ingest output and canonical parquet "
            f"presence at valuation_data/canonical/financials/{ticker}.parquet."
        )

    if fiscal_years is not None:
        wanted = set(fiscal_years)
        cleaned_records = [r for r in cleaned_records if r.fiscal_year in wanted]

    validated: List[ValidatedCleanedRecord] = []
    for rec in cleaned_records:
        # Run the schema-contract framework on the cleaned record —
        # this is the deterministic gate that surfaces upstream-data
        # violations (missing Tier-1 fields, identity drifts, etc.)
        # without raising. The validator returns a list of dicts;
        # mode resolution is global (ALETHEIA_GUARD_MODE env var).
        _passed, violations = validate_cleaned_record_schema_contract(rec)

        validated.append(_cleaned_record_to_validated(
            rec,
            schema_violations=violations,
            overrides_applied=overrides_applied,
            input_bundle_fingerprint=bundle_fp,
            overrides_hash=overrides_hash,
            pipeline_version=pipeline_version,
        ))

    return validated


__all__ = [
    "Stage2ValidateError",
    "run_stage2",
]
