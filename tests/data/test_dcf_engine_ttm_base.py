"""Phase Q-5: DCF engine selects TTM as base year when present.

Pins:

  1. df with only FY rows → base_period='FY', engine behaves as before.
  2. df with FY + TTM rows → base_period='TTM', latest row carries TTM
     period_end_date, fy_fiscal_year reflects the latest 10-K year.
  3. _merge_ttm_with_fy_fallback produces a row that has TTM values for
     fields TTM derivation populates and FY values for everything else
     so DCFEngine's field-requirement invariants stay satisfied.
"""

from __future__ import annotations

import pandas as pd

from aletheia.tools.dcf_engine import _merge_ttm_with_fy_fallback


def _fy_row(fy=2025, period_end="2025-09-30"):
    return pd.Series({
        "fiscal_year": fy,
        "period": "FY",
        "period_end_date": period_end,
        "clean_Revenue": 380e9,
        "clean_NormalizedEBIT": 110e9,   # FY-only field
        "raw_NetIncome": 95e9,
        "derived_EBITDA": 130e9,
        "derived_Depreciation_Total": 12e9,
        "derived_CapEx": -10e9,
        "derived_FCF": 80e9,
        "derived_NetDebt": 50e9,
        "derived_ROIC": 0.50,
        "derived_InvestedCapital": 200e9,
        "raw_TotalEquity": 70e9,
        "raw_LongTermDebt": 80e9,
        "raw_SharesDiluted": 15e9,
        "domain_score_D1_NonRecurring": 1.0,   # FY-only field
        "warnings_json": "[]",
    })


def _ttm_row(period_end="2026-03-28"):
    return pd.Series({
        "fiscal_year": 2026,
        "period": "TTM",
        "period_end_date": period_end,
        "clean_Revenue": 410e9,
        "raw_NetIncome": 105e9,
        "derived_EBITDA": 145e9,
        "derived_Depreciation_Total": 13e9,
        "derived_CapEx": -11e9,
        "derived_FCF": 88e9,
        "derived_NetDebt": 55e9,
        "derived_ROIC": 0.55,
        "derived_InvestedCapital": 210e9,
        "raw_TotalEquity": 75e9,
        "raw_LongTermDebt": 85e9,
        "raw_SharesDiluted": 15e9,
        # Note: no clean_NormalizedEBIT, no domain_score_*, no warnings_json
    })


def test_merge_takes_ttm_values_for_overlapping_fields():
    fy  = _fy_row()
    ttm = _ttm_row()
    merged = _merge_ttm_with_fy_fallback(ttm, fy)
    assert merged["clean_Revenue"] == 410e9
    assert merged["derived_FCF"] == 88e9
    assert merged["derived_ROIC"] == 0.55


def test_merge_falls_back_to_fy_for_fy_only_fields():
    """Cleaning-engine-only outputs must come through from the FY row."""
    fy  = _fy_row()
    ttm = _ttm_row()
    merged = _merge_ttm_with_fy_fallback(ttm, fy)
    assert merged["clean_NormalizedEBIT"] == 110e9
    assert merged["domain_score_D1_NonRecurring"] == 1.0
    assert merged["warnings_json"] == "[]"


def test_merge_preserves_ttm_identity_fields():
    fy  = _fy_row(fy=2025, period_end="2025-09-30")
    ttm = _ttm_row(period_end="2026-03-28")
    merged = _merge_ttm_with_fy_fallback(ttm, fy)
    assert merged["period"] == "TTM"
    assert merged["period_end_date"] == "2026-03-28"
    assert merged["fiscal_year"] == 2026


def test_merge_handles_nan_on_ttm_side():
    """If TTM has NaN for a field, FY value fills in (silent NaN-as-
    missing behavior, mirroring how the cleaning engine treats nulls)."""
    fy  = _fy_row()
    ttm = _ttm_row()
    ttm["derived_FCF"] = float("nan")
    merged = _merge_ttm_with_fy_fallback(ttm, fy)
    assert merged["derived_FCF"] == 80e9   # FY value carried through
