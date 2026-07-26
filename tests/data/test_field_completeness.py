"""Guard: representative tickers must have no unexplained blank core fields.

Catches provider regressions (e.g. an accidental xbrl-only run) that reopen the
2026-07 operating-income / COGS holes. Skips when the DuckDB isn't present
(CI without the data volume).
"""
import os

import pytest

DB = "valuation_data/database/investment.duckdb"
SAMPLE = ["LLY", "AAPL", "WMT", "KO", "V"]  # all had ~17 blank OI years pre-fix


pytestmark = pytest.mark.skipif(
    not os.path.exists(DB), reason="investment.duckdb not present (no data volume)"
)


def _rows(ticker):
    import duckdb
    con = duckdb.connect(DB, read_only=True)
    return con.execute(
        "SELECT fiscal_year, raw_Revenue, derived_OperatingIncome, clean_NormalizedEBIT "
        "FROM company_records_latest WHERE ticker=? AND period='FY' ORDER BY fiscal_year",
        [ticker],
    ).fetchall()


@pytest.mark.parametrize("ticker", SAMPLE)
def test_no_blank_operating_income_when_revenue_present(ticker):
    """For every FY the company had revenue, operating income must be populated
    (reported OR normalized — some filers never tag reported operating income)."""
    blanks = []
    for fy, rev, oi, norm in _rows(ticker):
        if rev is None:
            continue  # pre-existence / pre-IPO year — legitimately absent
        if (oi is None) and (norm is None):
            blanks.append(fy)
    assert not blanks, f"{ticker}: blank operating income for years with revenue: {blanks}"


def test_audit_script_clean():
    """The universe-wide completeness audit reports no unexplained blanks."""
    from scripts.audit_field_completeness import audit
    res = audit(DB)
    assert res["clean"], (
        f"{res['hard_blank_count']} unexplained blank core-field rows; "
        f"first few: {res['hard_blanks'][:5]}"
    )
