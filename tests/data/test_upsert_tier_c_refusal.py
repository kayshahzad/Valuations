"""Tests for L1 final 5% — Stage 1 → 2 boundary enforcement.

``upsert_record`` is the persistence boundary every cleaned record
crosses on its way to DuckDB. Layer-1 enforcement at this boundary
ensures downstream consumers reading from ``company_records`` can
trust that every row respects the structural identities (A = L + E,
required-Tier-1 fields present).

OVERRIDES waivers strip waived fields from the schema_violations list
before reaching this gate, so already-waived tickers (CAT, LOW, NVDA,
TSLA, TSM) flow through. Records carrying a genuinely-unwaived
tier-C violation are refused with a ``CalculationError`` instructing
the analyst how to add a waiver.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _make_record_with_invalid_balance_sheet():
    """Build a CleanedRecord whose TotalAssets ≠ TotalLiabilities +
    TotalEquity, with no OVERRIDES waiver covering the field. Uses
    'ZZZTESTONLY' so we don't accidentally collide with a real ticker."""
    from aletheia.data.cleaning_engine import CleanedRecord
    return CleanedRecord(
        ticker="ZZZTESTONLY",
        fiscal_year=2099,
        period="FY",
        period_end_date="2099-12-31",
        raw={
            # Materially imbalanced: A=100, L+E=60 → 40% gap >> 0.5% tol
            "Revenue":         50_000_000_000.0,
            "TotalAssets":     100_000_000_000.0,
            "TotalLiabilities": 40_000_000_000.0,
            "TotalEquity":      20_000_000_000.0,
        },
        clean={"Revenue": 50_000_000_000.0, "SharesDiluted": 1_000_000_000.0},
        derived={},
        domain_scores={},
        cleaning_warnings=[],
        blocking_errors=[],
        cleaned_at=datetime.now(timezone.utc),
        overall_quality_score=0.0,
        period_end_date_missing=False,
    )


def _make_clean_record():
    """Build a CleanedRecord that passes all tier-C checks."""
    from aletheia.data.cleaning_engine import CleanedRecord
    return CleanedRecord(
        ticker="ZZZGOODONLY",
        fiscal_year=2099,
        period="FY",
        period_end_date="2099-12-31",
        raw={
            "Revenue":         100_000_000_000.0,
            "TotalAssets":     200_000_000_000.0,
            "TotalLiabilities": 120_000_000_000.0,
            "TotalEquity":      80_000_000_000.0,  # 120 + 80 = 200 ✓
            "SharesDiluted":   1_000_000_000.0,
        },
        clean={
            "Revenue":       100_000_000_000.0,
            "SharesDiluted": 1_000_000_000.0,
        },
        derived={},
        domain_scores={},
        cleaning_warnings=[],
        blocking_errors=[],
        cleaned_at=datetime.now(timezone.utc),
        overall_quality_score=1.0,
        period_end_date_missing=False,
    )


@pytest.fixture
def tmp_db(tmp_path):
    """Spin up a fresh InvestmentDatabase pointed at tmp_path so test
    writes don't pollute the production DB."""
    from aletheia.data.database import InvestmentDatabase
    db_path = tmp_path / "test.duckdb"
    db = InvestmentDatabase(db_path=str(db_path), verbose=False)
    yield db
    db.close()


def test_upsert_refuses_record_with_balance_sheet_violation(tmp_db):
    """A record with A ≠ L + E should be refused at persistence."""
    from aletheia.calculations._errors import CalculationError
    record = _make_record_with_invalid_balance_sheet()
    with pytest.raises(CalculationError, match="tier-C"):
        tmp_db.upsert_record(record)


def test_upsert_refusal_includes_overrides_path_in_message(tmp_db):
    """The refusal message must direct the analyst to OVERRIDES."""
    from aletheia.calculations._errors import CalculationError
    record = _make_record_with_invalid_balance_sheet()
    with pytest.raises(CalculationError) as exc_info:
        tmp_db.upsert_record(record)
    msg = str(exc_info.value)
    assert "OVERRIDES" in msg
    assert "ZZZTESTONLY" in msg


def test_upsert_persists_clean_record_normally(tmp_db):
    """A record passing all tier-C checks must persist without raising.
    Sanity guarantee: enforcement doesn't break the happy path."""
    record = _make_clean_record()
    version = tmp_db.upsert_record(record)
    assert version == 1
