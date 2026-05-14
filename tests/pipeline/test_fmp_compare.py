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
    _classify_drift,
    _fmp_by_fy,
    _load_fmp_cache,
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
