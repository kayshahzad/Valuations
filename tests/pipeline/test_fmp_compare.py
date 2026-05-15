"""Tests for the XBRL-vs-FMP per-FY comparison module.

Covers:
  - drift tier classification (ok / minor / notable / material / incomplete)
  - CapEx sign-convention reconciliation (FMP reports negative, our
    cleaning convention is positive magnitude — must compare abs)
  - fiscal_year indexing of FMP statements (calendarYear vs
    fiscalYear, string vs int)
  - graceful behaviour when one side is empty
"""

from __future__ import annotations

import json

import pytest

from aletheia.pipeline._fmp_compare import (
    FieldSpec,
    _classify_drift,
    _fmp_by_fy,
    _load_fmp_cache,
    _resolve_xbrl_values,
    _xbrl_fact_for_period,
    compare_xbrl_to_fmp,
)


# ─────────────────────────────────────────────────────────────────────
# Drift classification
# ─────────────────────────────────────────────────────────────────────

def test_classify_drift_tiers():
    assert _classify_drift(0.0) == "ok"
    assert _classify_drift(0.001) == "ok"          # 0.1%
    assert _classify_drift(0.005) == "ok"          # 0.5% — on the boundary
    assert _classify_drift(0.01) == "minor"        # 1%
    assert _classify_drift(-0.015) == "minor"      # negative side
    assert _classify_drift(0.03) == "notable"      # 3%
    assert _classify_drift(0.05) == "notable"      # 5% — boundary
    assert _classify_drift(0.10) == "material"     # 10%
    assert _classify_drift(-0.20) == "material"    # large negative
    assert _classify_drift(None) == "incomplete"


# ─────────────────────────────────────────────────────────────────────
# Period-aware XBRL fact lookup
# ─────────────────────────────────────────────────────────────────────

def test_resolve_xbrl_values_falls_back_to_raw_when_cleaning_has_no_coverage():
    """Coverage-gap fallback: when cleaning_engine emits no canonical
    value for a field but companyfacts has the raw fact, the resolver
    surfaces the raw value into the Cleaned slot so the diagnostic
    column isn't blank. This applies to fields like Inventory,
    AccountsPayable, RetainedEarnings, Treasury, AOCI, and the CF
    working-capital deltas where canonical_transformer has no rule yet.
    """
    spec = FieldSpec(
        label="Retained Earnings", category="Balance Sheet", tier="critical",
        xbrl_clean_keys=["RetainedEarnings"], xbrl_raw_keys=["RetainedEarnings"],
        xbrl_fallback_tags=["RetainedEarningsAccumulatedDeficit"],
        fmp_source="balance", fmp_keys=["retainedEarnings"],
    )
    # clean + raw record dicts are empty — cleaning_engine didn't emit anything.
    # us_gaap has the raw fact.
    us_gaap = {
        "RetainedEarningsAccumulatedDeficit": {
            "units": {"USD": [{
                "val": 100_000_000_000,
                "fy": 2024, "fp": "FY", "form": "10-K",
                "end": "2024-12-31", "filed": "2025-01-30",
            }]},
        },
    }
    raw, cleaned, best, source = _resolve_xbrl_values(
        spec, {}, {}, us_gaap, fiscal_year=2024, period="FY",
    )
    assert raw == 100_000_000_000
    # Coverage-gap fallback: cleaned mirrors raw so the column isn't blank.
    assert cleaned == 100_000_000_000
    assert best == 100_000_000_000


def test_xbrl_fact_for_period_reads_shares_unit():
    """Regression for the silent bug where _xbrl_fact_for_period
    only scanned units["USD"]. Share-count tags (e.g.
    ``WeightedAverageNumberOfDilutedSharesOutstanding``) are filed
    under units["shares"] and were dropped, leaving the Raw XBRL
    column blank in the UI even though companyfacts had the value.
    """
    us_gaap = {
        "WeightedAverageNumberOfDilutedSharesOutstanding": {
            "units": {
                "shares": [
                    {
                        "val": 2_614_000_000,
                        "fy": 2024, "fp": "FY", "form": "10-K",
                        "end": "2024-12-31", "filed": "2025-01-30",
                    },
                ],
            },
        },
    }
    val = _xbrl_fact_for_period(
        us_gaap, "WeightedAverageNumberOfDilutedSharesOutstanding",
        2024, "FY",
    )
    assert val == 2_614_000_000


def test_xbrl_fact_for_period_returns_none_for_missing_tag():
    assert _xbrl_fact_for_period({}, "AnyTag", 2024, "FY") is None


def test_xbrl_fact_for_period_matches_on_end_year_not_fy():
    """Regression: SEC companyfacts ``fy`` is the FILING year, not the
    data-period year. When a 10-K reports comparative columns for
    prior FYs, those rows carry ``fy = filing year`` and
    ``end = prior-period year-end``. Matching on ``fy`` mis-attributes
    the period; we must match on year(end).

    META filed FY2021 Interest Expense ($23M) inside the 2023 10-K
    (``fy=2023``, ``end=2021-12-31``). The resolver must surface
    that as FY2021's raw value.
    """
    us_gaap = {
        "InterestExpense": {
            "units": {"USD": [
                {  # FY2021 data restated in 2023's 10-K
                    "val": 23_000_000,
                    "fy": 2023, "fp": "FY", "form": "10-K",
                    "start": "2021-01-01", "end": "2021-12-31",
                    "filed": "2024-02-02",
                },
                {  # FY2023 data in 2023's 10-K
                    "val": 446_000_000,
                    "fy": 2023, "fp": "FY", "form": "10-K",
                    "start": "2023-01-01", "end": "2023-12-31",
                    "filed": "2024-02-02",
                },
            ]},
        },
    }
    # Asking for FY2021 must return the $23M comparative value, not None.
    assert _xbrl_fact_for_period(us_gaap, "InterestExpense", 2021, "FY") == 23_000_000
    assert _xbrl_fact_for_period(us_gaap, "InterestExpense", 2023, "FY") == 446_000_000


def test_xbrl_fact_for_period_prefers_latest_filing_for_restated_period():
    """When the same fiscal period appears in multiple subsequent 10-Ks
    (comparative columns), prefer the most-recently-filed value. This
    surfaces restatements automatically and keeps the diagnostic in
    sync with the latest disclosure.
    """
    us_gaap = {
        "Revenues": {
            "units": {"USD": [
                {  # Original filing
                    "val": 100_000_000_000,
                    "fy": 2022, "fp": "FY", "form": "10-K",
                    "end": "2022-12-31", "filed": "2023-02-01",
                },
                {  # Restated in 2023 10-K
                    "val": 101_500_000_000,
                    "fy": 2023, "fp": "FY", "form": "10-K",
                    "end": "2022-12-31", "filed": "2024-02-01",
                },
            ]},
        },
    }
    assert _xbrl_fact_for_period(us_gaap, "Revenues", 2022, "FY") == 101_500_000_000


# ─────────────────────────────────────────────────────────────────────
# FMP statement indexing
# ─────────────────────────────────────────────────────────────────────

def test_fmp_by_fy_tolerates_string_and_int_year():
    data = [
        {"calendarYear": 2024, "revenue": 100},
        {"fiscalYear": "2023", "revenue": 90},
        {"calendarYear": None, "fiscalYear": "2022", "revenue": 80},
    ]
    indexed = _fmp_by_fy(data)
    assert indexed[2024]["revenue"] == 100
    assert indexed[2023]["revenue"] == 90
    assert indexed[2022]["revenue"] == 80


def test_fmp_by_fy_handles_unparseable_year():
    data = [
        {"calendarYear": "not-a-year", "revenue": 1},
        {"calendarYear": 2024, "revenue": 100},
    ]
    indexed = _fmp_by_fy(data)
    assert 2024 in indexed
    assert "not-a-year" not in indexed
    # Unparseable rows are skipped silently
    assert len(indexed) == 1


def test_fmp_by_fy_returns_first_entry_per_year():
    """Defensive: when FMP duplicates an FY (rare amended statements),
    keep the first occurrence so behaviour is deterministic."""
    data = [
        {"calendarYear": 2024, "revenue": 100},
        {"calendarYear": 2024, "revenue": 999},  # later duplicate
    ]
    indexed = _fmp_by_fy(data)
    assert indexed[2024]["revenue"] == 100


# ─────────────────────────────────────────────────────────────────────
# Cache loading
# ─────────────────────────────────────────────────────────────────────

def test_load_fmp_cache_returns_empty_for_missing_file(tmp_path, monkeypatch):
    import aletheia.pipeline._fmp_compare as mod
    monkeypatch.setattr(mod, "_FMP_CACHE_DIR", tmp_path)
    assert _load_fmp_cache("DOESNOTEXIST", "income") == []


def test_load_fmp_cache_unwraps_data_wrapper(tmp_path, monkeypatch):
    """fmp_client saves payloads in a wrapper dict; the loader must
    unwrap the inner ``data`` list."""
    import aletheia.pipeline._fmp_compare as mod
    monkeypatch.setattr(mod, "_FMP_CACHE_DIR", tmp_path)
    payload = {
        "_cached_at": "2026-05-13T00:00:00",
        "ticker": "META",
        "endpoint": "income_annual",
        "data": [{"calendarYear": 2024, "revenue": 100}],
    }
    (tmp_path / "META__income_annual.json").write_text(json.dumps(payload))
    out = _load_fmp_cache("META", "income")
    assert out == [{"calendarYear": 2024, "revenue": 100}]


def test_load_fmp_cache_returns_empty_on_malformed_json(tmp_path, monkeypatch):
    import aletheia.pipeline._fmp_compare as mod
    monkeypatch.setattr(mod, "_FMP_CACHE_DIR", tmp_path)
    (tmp_path / "META__income_annual.json").write_text("{not json")
    assert _load_fmp_cache("META", "income") == []


# ─────────────────────────────────────────────────────────────────────
# End-to-end comparison
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def synthetic_universe(tmp_path, monkeypatch):
    """Plant synthetic FMP cache files + mock the DB get_latest()
    return to bypass the full DuckDB schema. Lets the test focus on
    the comparison logic without replicating production schema."""
    import pandas as pd
    fmp_dir = tmp_path / "fmp"
    fmp_dir.mkdir()

    # Stand-in for what InvestmentDatabase.get_latest() returns — a
    # DataFrame with the columns the comparison module reads.
    fake_df = pd.DataFrame([{
        "ticker": "TEST",
        "fiscal_year": 2024,
        "period": "FY",
        "clean_json": json.dumps({
            "Revenue": 100_000_000_000.0,
            "NetIncome": 20_000_000_000.0,
            "CapEx_Total": 5_000_000_000.0,
            "TotalAssets": 200_000_000_000.0,
        }),
    }])

    # FMP cache files. Revenue + NI match exactly (drift ok).
    # CapEx differs by sign convention. TotalAssets has 2% drift
    # (minor tier).
    (fmp_dir / "TEST__income_annual.json").write_text(json.dumps({
        "data": [{
            "calendarYear": 2024,
            "revenue": 100_000_000_000.0,
            "netIncome": 20_000_000_000.0,
            "weightedAverageShsOutDil": 1_000_000_000.0,
        }]
    }))
    (fmp_dir / "TEST__balance_annual.json").write_text(json.dumps({
        "data": [{
            "calendarYear": 2024,
            "totalAssets": 204_000_000_000.0,   # 2% high
            "totalStockholdersEquity": 50_000_000_000.0,
            "totalLiabilities": 150_000_000_000.0,
            "cashAndCashEquivalents": 10_000_000_000.0,
            "longTermDebt": 30_000_000_000.0,
        }]
    }))
    (fmp_dir / "TEST__cashflow_annual.json").write_text(json.dumps({
        "data": [{
            "calendarYear": 2024,
            "capitalExpenditure": -5_000_000_000.0,  # FMP negative
            "operatingCashFlow": 30_000_000_000.0,
            "netCashProvidedByInvestingActivities": -10_000_000_000.0,
            "netCashProvidedByFinancingActivities": -5_000_000_000.0,
        }]
    }))

    # Redirect FMP cache dir.
    import aletheia.pipeline._fmp_compare as mod
    monkeypatch.setattr(mod, "_FMP_CACHE_DIR", fmp_dir)

    # Mock InvestmentDatabase to return our synthetic DataFrame on
    # ``get_latest``. Bypasses the production DuckDB schema entirely
    # so the test exercises only the comparison logic.
    from aletheia.data import database as db_mod

    class _FakeDB:
        def __init__(self, *args, **kwargs): pass
        def get_latest(self, ticker): return fake_df
        def close(self): pass

    monkeypatch.setattr(db_mod, "InvestmentDatabase", _FakeDB)
    return tmp_path


def test_compare_xbrl_to_fmp_matches_clean_values(synthetic_universe):
    result = compare_xbrl_to_fmp("TEST")
    assert result.ticker == "TEST"
    assert result.fiscal_years == [2024]
    by_field = {(c.fiscal_year, c.field_label): c for c in result.cells}
    rev = by_field[(2024, "Revenue")]
    assert rev.xbrl_value == 100_000_000_000.0
    assert rev.fmp_value == 100_000_000_000.0
    assert rev.tier == "ok"


def test_compare_capex_sign_convention_reconciles(synthetic_universe):
    """XBRL CapEx positive magnitude, FMP CapEx negative cash flow —
    the comparison must reconcile to drift = 0 (or near it), not
    report a 200% drift just because of sign."""
    result = compare_xbrl_to_fmp("TEST")
    by_field = {(c.fiscal_year, c.field_label): c for c in result.cells}
    capex = by_field[(2024, "CapEx")]
    assert capex.xbrl_value == 5_000_000_000.0
    assert capex.fmp_value == -5_000_000_000.0
    assert capex.tier == "ok"  # post-abs reconciliation


def test_compare_flags_minor_drift_on_totals(synthetic_universe):
    """TotalAssets in the fixture has 2% drift (FMP higher than XBRL)
    — that should land in the ``minor`` tier."""
    result = compare_xbrl_to_fmp("TEST")
    by_field = {(c.fiscal_year, c.field_label): c for c in result.cells}
    assets = by_field[(2024, "Total Assets")]
    assert assets.xbrl_value == 200_000_000_000.0
    assert assets.fmp_value == 204_000_000_000.0
    assert assets.drift_pct is not None
    assert abs(assets.drift_pct + 0.02) < 0.001  # ≈ -2%
    assert assets.tier == "minor"


def test_compare_flags_incomplete_when_one_side_missing(synthetic_universe):
    """The synthetic fixture's clean_json omits Shares Diluted but
    the FMP cash-flow file supplies ``weightedAverageShsOutDil``.
    The comparison must surface as ``incomplete`` rather than
    silently zero-filling the XBRL side."""
    result = compare_xbrl_to_fmp("TEST")
    by_field = {(c.fiscal_year, c.field_label): c for c in result.cells}
    shares = by_field[(2024, "Shares Diluted")]
    assert shares.xbrl_value is None
    assert shares.fmp_value == 1_000_000_000.0
    assert shares.tier == "incomplete"
    assert shares.drift_pct is None
