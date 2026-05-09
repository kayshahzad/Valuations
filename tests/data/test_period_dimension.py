"""Phase Q-1: period dimension on company_records / cleaning_flags.

Pins the schema-migration contract:

  1. New DBs initialize with `period` columns defaulting to 'FY' on
     legacy rows so unfiltered queries continue to behave identically.
  2. Multiple periods (FY + TTM + Q3) can coexist for the same
     (ticker, fiscal_year) without primary-key collisions.
  3. `company_records_latest` returns one row per (ticker, fy, period)
     — versioning is per-period.
  4. cleaning_flags rows are tagged with the same period as their parent
     record.

The Phase Q-3+ writers can rely on these guarantees when they start
landing quarterly + TTM rows.
"""

from __future__ import annotations

import pytest

from aletheia.data.database import InvestmentDatabase
from aletheia.data.cleaning_engine import CleanedRecord, CleaningFlag


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.duckdb"
    db = InvestmentDatabase(db_path=str(path), verbose=False)
    yield db
    db.close()


def _stub_record(ticker="AAPL", fy=2024, period="FY", revenue=395_000_000_000):
    rec = CleanedRecord(
        ticker=ticker, fiscal_year=fy,
        period_end_date="2024-09-28", period=period,
    )
    rec.raw["Revenue"] = revenue
    rec.clean["Revenue"] = revenue
    rec.derived["EBITDA"] = revenue * 0.34
    rec.flags.append(CleaningFlag(
        domain=1, domain_name="D1", metric="Revenue",
        raw_value=revenue, adjusted_value=revenue,
        action="no_change", reason="byte_perfect", confidence=1.0,
    ))
    return rec


# ── Column presence + default ─────────────────────────────────────────

def test_period_column_exists_on_company_records(db):
    cols = db.query("DESCRIBE company_records")["column_name"].tolist()
    assert "period" in cols


def test_period_column_exists_on_cleaning_flags(db):
    cols = db.query("DESCRIBE cleaning_flags")["column_name"].tolist()
    assert "period" in cols


# ── FY default behavior — legacy code paths unaffected ───────────────

def test_fy_record_writes_with_default_period(db):
    rec = _stub_record(period="FY")
    db.upsert_record(rec)
    rows = db.query("SELECT period FROM company_records")
    assert rows["period"].tolist() == ["FY"]


def test_fy_record_id_format_unchanged_for_backward_compat(db):
    """Existing record_id format `{ticker}_{fy}_v{ver}` is preserved
    for FY rows — downstream consumers parsing the id won't break."""
    rec = _stub_record(period="FY")
    db.upsert_record(rec)
    rows = db.query("SELECT id FROM company_records")
    assert rows["id"].iloc[0] == "AAPL_2024_v1"


# ── Multiple periods coexist for the same (ticker, fy) ───────────────

def test_fy_and_ttm_can_coexist_for_same_ticker_fy(db):
    fy = _stub_record(ticker="AAPL", fy=2024, period="FY",
                      revenue=395_000_000_000)
    ttm = _stub_record(ticker="AAPL", fy=2024, period="TTM",
                       revenue=410_000_000_000)
    db.upsert_record(fy)
    db.upsert_record(ttm)

    rows = db.query(
        "SELECT period, clean_Revenue FROM company_records "
        "WHERE ticker='AAPL' AND fiscal_year=2024 ORDER BY period"
    )
    assert rows["period"].tolist() == ["FY", "TTM"]
    assert rows["clean_Revenue"].tolist() == [395_000_000_000, 410_000_000_000]


def test_quarterly_record_id_includes_period_segment(db):
    rec = _stub_record(period="Q3")
    db.upsert_record(rec)
    rows = db.query("SELECT id FROM company_records WHERE period='Q3'")
    assert rows["id"].iloc[0] == "AAPL_2024_Q3_v1"


def test_versioning_is_per_period(db):
    """Writing FY twice produces FY v1, FY v2.  An interleaved TTM
    write doesn't bump the FY counter — versions are scoped per period."""
    db.upsert_record(_stub_record(period="FY", revenue=100))
    db.upsert_record(_stub_record(period="TTM", revenue=110))
    db.upsert_record(_stub_record(period="FY", revenue=105))

    rows = db.query(
        "SELECT period, version FROM company_records "
        "WHERE ticker='AAPL' AND fiscal_year=2024 "
        "ORDER BY period, version"
    )
    assert list(zip(rows["period"], rows["version"])) == [
        ("FY", 1), ("FY", 2), ("TTM", 1),
    ]


# ── company_records_latest exposes one row per (ticker, fy, period) ──

def test_latest_view_returns_one_row_per_period(db):
    db.upsert_record(_stub_record(period="FY"))
    db.upsert_record(_stub_record(period="TTM"))

    rows = db.query(
        "SELECT period, version FROM company_records_latest "
        "WHERE ticker='AAPL' AND fiscal_year=2024 ORDER BY period"
    )
    assert rows["period"].tolist() == ["FY", "TTM"]


def test_latest_view_picks_max_version_per_period(db):
    db.upsert_record(_stub_record(period="FY", revenue=100))
    db.upsert_record(_stub_record(period="FY", revenue=200))  # v2 overrides

    rows = db.query(
        "SELECT version, clean_Revenue FROM company_records_latest "
        "WHERE ticker='AAPL' AND fiscal_year=2024 AND period='FY'"
    )
    assert len(rows) == 1
    assert rows["version"].iloc[0] == 2
    assert rows["clean_Revenue"].iloc[0] == 200


# ── cleaning_flags inherit the parent record's period ────────────────

def test_flags_carry_period_from_record(db):
    db.upsert_record(_stub_record(period="TTM"))
    rows = db.query(
        "SELECT period, metric FROM cleaning_flags "
        "WHERE ticker='AAPL' AND fiscal_year=2024"
    )
    assert rows["period"].iloc[0] == "TTM"
