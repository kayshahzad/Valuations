"""3.2 — Gate A receipt propagation through the typed Stage-2 adapter.

Guards against the receipt being dropped (`fmp_validation={}`) on the way from
the CleanedRecord (where clean() stamps it) to the typed ValidatedCleanedRecord
(which persists it → the DB `fmp_validation_*` columns → Gate D / Gate F).
"""
from __future__ import annotations

from aletheia.data.cleaning_engine import CleanedRecord
from aletheia.pipeline.stage2_validate import _cleaned_record_to_validated


def _adapt(rec):
    return _cleaned_record_to_validated(
        rec,
        schema_violations=[],
        overrides_applied=[],
        input_bundle_fingerprint="fp",
        overrides_hash="h",
        pipeline_version="test",
    )


def test_receipt_propagates_to_typed_record():
    rec = CleanedRecord(ticker="AAA", fiscal_year=2025, period_end_date="2025-12-31")
    rec.raw = {"Revenue": 100.0}
    rec.clean = {"Revenue": 100.0}
    rec.fmp_validation = {"status": "validated", "fields": {"revenue": {"drift_pct": 0.0}}}

    vr = _adapt(rec)
    assert vr.validation.fmp_validation.get("status") == "validated"
    assert "revenue" in vr.validation.fmp_validation.get("fields", {})


def test_absent_receipt_stays_empty_not_crashes():
    rec = CleanedRecord(ticker="BBB", fiscal_year=2025, period_end_date="2025-12-31")
    rec.raw = {"Revenue": 100.0}
    rec.clean = {"Revenue": 100.0}
    # fmp_validation defaults to {} on the dataclass
    vr = _adapt(rec)
    assert vr.validation.fmp_validation == {}
