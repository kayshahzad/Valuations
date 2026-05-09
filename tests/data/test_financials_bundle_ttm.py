"""Phase Q-7 minimal: Financials bundle exposes TTM + freshness.

Pins the contract that:

  1. The Financials bundle (ticker_detail) splits FY rows from TTM
     rows.  Income/balance/returns continue to use the latest FY row
     (Phase Q-5 will switch the calc engine to TTM-base; this MVP slice
     keeps tab consumers stable).
  2. `ttm_snapshot` is None when no TTM row exists (legacy DBs / not-
     yet-ingested tickers), and a populated dict when one is present.
  3. `freshness` block computes days_since_filing + next_expected_date
     using the freshest period_end_date available (TTM > FY).
"""

from __future__ import annotations

import datetime
import json
from typing import Any, Dict

import pandas as pd
import pytest

from aletheia.ui.financials import (
    _build_freshness, _build_ttm_snapshot, _ttm_source_from_validation,
)


# ── _build_ttm_snapshot ───────────────────────────────────────────────

def test_ttm_snapshot_none_when_no_rows():
    empty = pd.DataFrame()
    assert _build_ttm_snapshot(empty) is None


def test_ttm_snapshot_extracts_latest_period_end():
    df = pd.DataFrame([
        {"fiscal_year": 2024, "period_end_date": "2024-12-31",
         "clean_Revenue": 380e9, "raw_NetIncome": 90e9,
         "derived_EBITDA": 130e9, "derived_FCF": 80e9,
         "derived_ROIC": 0.28, "derived_ROE": 0.55,
         "derived_EBIT_Margin_Pct": 28.0, "derived_FCF_Margin_Pct": 21.0,
         "derived_CapEx": -10e9, "raw_CapEx": -10e9,
         "fmp_validation_status": "validated",
         "fmp_validation_json":   json.dumps({"ttm_source": "fmp_derived_quarters"}),
         "overall_quality_score": None},
        {"fiscal_year": 2025, "period_end_date": "2025-09-30",
         "clean_Revenue": 410e9, "raw_NetIncome": 105e9,
         "derived_EBITDA": 145e9, "derived_FCF": 92e9,
         "derived_ROIC": 0.30, "derived_ROE": 0.58,
         "derived_EBIT_Margin_Pct": 30.0, "derived_FCF_Margin_Pct": 22.5,
         "derived_CapEx": -11e9, "raw_CapEx": -11e9,
         "fmp_validation_status": "validated",
         "fmp_validation_json":   json.dumps({"ttm_source": "fmp_derived_quarters"}),
         "overall_quality_score": None},
    ])
    snap = _build_ttm_snapshot(df)
    assert snap is not None
    assert snap["fiscal_year"] == 2025
    assert snap["period_end_date"] == "2025-09-30"
    assert snap["Revenue"] == 410e9
    assert snap["ttm_source"] == "fmp_derived_quarters"
    assert snap["FMPStatus"] == "validated"


def test_ttm_source_from_validation_handles_dict_and_string():
    """Validation receipt is stored as JSON string in DB, but tests
    sometimes pass it as a dict directly — both shapes resolve."""
    assert _ttm_source_from_validation(json.dumps({"ttm_source": "fmp_derived_quarters"})) == "fmp_derived_quarters"
    assert _ttm_source_from_validation({"ttm_source": "sec_derived"}) == "sec_derived"
    assert _ttm_source_from_validation(None) is None
    assert _ttm_source_from_validation("not-json-{") is None


# ── _build_freshness ──────────────────────────────────────────────────

def _series(period_end: str) -> pd.Series:
    return pd.Series({"period_end_date": period_end})


def test_freshness_uses_ttm_period_end_when_available():
    """TTM is fresher than FY, so the banner should reflect TTM."""
    fy_row  = _series("2024-12-31")
    ttm     = {"period_end_date": "2025-09-30"}
    f = _build_freshness(fy_row, ttm)
    assert f["latest_period"] == "TTM"
    assert f["latest_period_end_date"] == "2025-09-30"


def test_freshness_falls_back_to_fy_when_no_ttm():
    fy_row = _series("2024-12-31")
    f = _build_freshness(fy_row, None)
    assert f["latest_period"] == "FY"
    assert f["latest_period_end_date"] == "2024-12-31"


def test_freshness_computes_days_since_and_next_expected():
    """Mock today via period_end relative math: pick a recent
    period_end and assert the math holds within ±1 day."""
    today = datetime.date.today()
    pe = today - datetime.timedelta(days=47)
    fy_row = _series(pe.isoformat())
    f = _build_freshness(fy_row, None)
    # Days since filing: ~47
    assert f["days_since_filing"] == 47
    # Next expected: pe + 90 + 45 = pe + 135 days
    expected_next = (pe + datetime.timedelta(days=135)).isoformat()
    assert f["next_expected_date"] == expected_next


def test_freshness_handles_missing_period_end():
    """Legacy DBs may have null period_end_date — banner stays safe."""
    fy_row = _series(None)
    f = _build_freshness(fy_row, None)
    assert f["latest_period_end_date"] is None
    assert f["days_since_filing"] is None
    assert f["next_expected_date"] is None


def test_freshness_handles_unparseable_period_end():
    fy_row = _series("not-a-date")
    f = _build_freshness(fy_row, None)
    assert f["latest_period_end_date"] == "not-a-date"
    # Date math suppressed when unparseable
    assert f["days_since_filing"] is None
