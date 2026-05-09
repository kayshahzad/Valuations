"""Deterministic-computer tests.

Three layers:

  1. **Unit tests** for each computer's bucket logic on synthetic inputs.
     These are fast and don't touch the DB. They lock the formula
     semantics — score 7 means "high quality compounder," score 1 means
     "value-destroying."

  2. **Smoke tests** that each computer can run against real DB rows for
     a representative ticker without crashing.

  3. **Calibration baseline test** that compares current computer output
     against `calibration_baselines.json` across the universe. This is
     the "regression alarm" — any future formula change that affects a
     ticker's score will fail this test, forcing the engineer to either
     accept the change (regenerate baselines + bump `code_version`) or
     fix the bug.

The baseline approach is preferred over hardcoded expected scores
because it captures the *current state* of the formula precisely. When
the catalog is updated, regenerating the baselines is a deliberate
checkpoint: you re-run calibration, eyeball the new column for
surprises, and pin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from aletheia.qualitative.computers import COMPUTERS
from aletheia.qualitative.computers.roiic_trend       import _bucket as roiic_bucket
from aletheia.qualitative.computers.buyback_discipline import _bucket as buyback_bucket
from aletheia.qualitative.computers.cyclicality        import _bucket as cyclicality_bucket


_BASELINES_PATH = Path(__file__).parent / "calibration_baselines.json"


# ─────────────────────────────────────────────────────────────────────────────
# Bucket-logic unit tests (no DB, fast)
# ─────────────────────────────────────────────────────────────────────────────

class TestROIICBuckets:

    def test_high_level_stable_scores_seven(self):
        """Sustained 30% ROIIC with no decay = 7."""
        assert roiic_bucket(median=0.30, slope=0.0) == 7

    def test_decaying_high_level_scores_six(self):
        """20% ROIIC with mild decay = 6."""
        assert roiic_bucket(median=0.22, slope=-0.015) == 6

    def test_moderate_level_scores_five(self):
        assert roiic_bucket(median=0.16, slope=0.0) == 5

    def test_average_scores_four(self):
        assert roiic_bucket(median=0.11, slope=0.01) == 4

    def test_low_positive_scores_three(self):
        assert roiic_bucket(median=0.07, slope=0.0) == 3

    def test_near_zero_scores_two(self):
        assert roiic_bucket(median=0.02, slope=0.0) == 2

    def test_negative_scores_one(self):
        assert roiic_bucket(median=-0.05, slope=0.0) == 1


class TestBuybackBuckets:

    def test_high_yield_consistent_scores_seven(self):
        assert buyback_bucket(yield_pct=0.05, consistency=0.9, has_dilution=False) == 7

    def test_moderate_yield_scores_five_or_six(self):
        s = buyback_bucket(yield_pct=0.025, consistency=0.7, has_dilution=False)
        assert s in (5, 6)

    def test_low_yield_scores_three_or_four(self):
        s = buyback_bucket(yield_pct=0.005, consistency=0.4, has_dilution=False)
        assert s in (3, 4)

    def test_dilution_scores_one_to_three(self):
        s = buyback_bucket(yield_pct=-0.02, consistency=0.0, has_dilution=True)
        assert s <= 3


class TestCyclicalityBuckets:

    def test_consumer_staple_volatility_scores_high(self):
        """Log-return stdev of ~3% = consumer-staple-grade stability."""
        assert cyclicality_bucket(0.03) == 7

    def test_stable_compounder_scores_six(self):
        assert cyclicality_bucket(0.07) == 6

    def test_hyper_growth_scores_five(self):
        """Steady 25% growth has log-return stdev ~10-13%."""
        assert cyclicality_bucket(0.12) == 5

    def test_industrial_cyclical_scores_three(self):
        assert cyclicality_bucket(0.30) == 3

    def test_deep_cyclical_scores_one(self):
        assert cyclicality_bucket(0.50) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic-input integration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRoiicTrendComputer:

    def _df(self, years, nopat, ic):
        return pd.DataFrame({
            "fiscal_year": years,
            "clean_NOPAT": nopat,
            "derived_InvestedCapital": ic,
        })

    def test_capital_releaser_caps_at_05(self):
        """If ΔIC ≤ 0 every year but NOPAT grows, each year contributes
        0.50 to the series. Score should be high (level dominates)."""
        df = self._df(
            years=[2021, 2022, 2023, 2024, 2025],
            nopat=[10, 12, 14, 16, 18],   # NOPAT grows
            ic=[100, 95, 90, 85, 80],     # IC shrinks
        )
        result = COMPUTERS["roiic_trend"](df)
        assert result is not None
        assert result.score is not None
        # Every year hits the capital-release-with-growth rule → series of 0.50s
        assert result.source_payload["roiic_series"] == [0.50, 0.50, 0.50, 0.50]
        # 0.50 sustained → median 0.50 → bucket 7
        assert result.score == 7

    def test_insufficient_history_returns_none_score(self):
        df = self._df(
            years=[2024, 2025],
            nopat=[10, 12],
            ic=[100, 110],
        )
        result = COMPUTERS["roiic_trend"](df)
        assert result is not None
        assert result.score is None
        assert result.source_payload.get("reason") == "insufficient_history"

    def test_value_destroying_returns_low_score(self):
        """NOPAT shrinking while IC grows = negative ROIIC = score 1."""
        df = self._df(
            years=[2021, 2022, 2023, 2024, 2025],
            nopat=[20, 18, 15, 13, 10],
            ic=[100, 110, 130, 150, 180],
        )
        result = COMPUTERS["roiic_trend"](df)
        assert result is not None
        assert result.score == 1


class TestBuybackComputer:

    def _df(self, years, buybacks, revenues):
        return pd.DataFrame({
            "fiscal_year": years,
            "clean_NetBuyback_AfterSBC": buybacks,
            "clean_Revenue": revenues,
        })

    def test_consistent_buybacks_score_high(self):
        df = self._df(
            years=[2021, 2022, 2023, 2024, 2025],
            buybacks=[5e9, 6e9, 7e9, 7e9, 8e9],
            revenues=[100e9] * 5,
        )
        result = COMPUTERS["buyback_discipline"](df)
        assert result is not None
        assert result.score == 7

    def test_all_nan_buybacks_returns_none(self):
        """BRK-B / TSLA case — cleaning pipeline didn't capture buyback
        data. Must surface as data gap, not score 2."""
        import numpy as np
        df = self._df(
            years=[2021, 2022, 2023, 2024, 2025],
            buybacks=[np.nan] * 5,
            revenues=[100e9] * 5,
        )
        result = COMPUTERS["buyback_discipline"](df)
        assert result is not None
        assert result.score is None
        assert "buyback_data_unavailable" in result.source_payload.get("reason", "")

    def test_dilution_scores_low(self):
        df = self._df(
            years=[2021, 2022, 2023, 2024, 2025],
            buybacks=[-2e9, -3e9, -2.5e9, -3e9, -3e9],
            revenues=[100e9] * 5,
        )
        result = COMPUTERS["buyback_discipline"](df)
        assert result is not None
        assert result.score is not None
        assert result.score <= 3


class TestDividendComputer:

    def _df(self, years, dividends, fcf, revenue=None):
        if revenue is None:
            revenue = [100e9] * len(years)
        clean_jsons = [json.dumps({"DividendsPaid": d}) for d in dividends]
        return pd.DataFrame({
            "fiscal_year": years,
            "clean_json":  clean_jsons,
            "derived_FCF": fcf,
            "clean_Revenue": revenue,
        })

    def test_no_dividend_program_returns_none(self):
        df = self._df(
            years=[2021, 2022, 2023, 2024, 2025],
            dividends=[0, 0, 0, 0, 0],
            fcf=[10e9] * 5,
        )
        result = COMPUTERS["dividend_policy"](df)
        assert result is not None
        assert result.score is None
        assert result.source_payload["reason"] == "no_dividend_program"

    def test_growing_sustainable_dividend_scores_high(self):
        # 12% CAGR cleanly clears the 10% cutoff (rounding-resistant)
        df = self._df(
            years=[2021, 2022, 2023, 2024, 2025],
            dividends=[1.0e9, 1.12e9, 1.254e9, 1.405e9, 1.574e9],
            fcf=[5e9] * 5,                                       # ~31% coverage
        )
        result = COMPUTERS["dividend_policy"](df)
        assert result is not None
        assert result.score == 7

    def test_token_dividend_caps_at_four(self):
        """NVDA case — high CAGR on tiny base shouldn't score 7.
        Token dividend < 0.5% of revenue caps at 4."""
        df = self._df(
            years=[2021, 2022, 2023, 2024, 2025],
            dividends=[100e6, 125e6, 156e6, 195e6, 244e6],     # 25% CAGR
            fcf=[20e9] * 5,
            revenue=[100e9] * 5,                               # ratio ~0.2%
        )
        result = COMPUTERS["dividend_policy"](df)
        assert result is not None
        assert result.score is not None
        assert result.score <= 4

    def test_full_continuity_floor_lifts_low_cagr_to_five(self):
        """AAPL case — paid every year with low growth should still
        get 5 minimum."""
        df = self._df(
            years=[2021, 2022, 2023, 2024, 2025],
            dividends=[14e9, 14.5e9, 14.8e9, 15.0e9, 15.5e9],  # ~2% CAGR
            fcf=[100e9] * 5,                                     # 15% coverage
        )
        result = COMPUTERS["dividend_policy"](df)
        assert result is not None
        assert result.score is not None
        assert result.score >= 5


class TestCyclicalityComputer:

    def _df(self, years, revenues):
        return pd.DataFrame({
            "fiscal_year": years,
            "clean_Revenue": revenues,
        })

    def test_consumer_staple_smooth_revenue_scores_seven(self):
        """KO-style: revenue ~3% growth, very low log-return volatility."""
        revenues = [100e9 * (1.03 ** i) for i in range(7)]
        df = self._df(years=list(range(2019, 2026)), revenues=revenues)
        result = COMPUTERS["cyclicality"](df)
        assert result is not None
        assert result.score == 7

    def test_high_growth_with_steady_pace_scores_high(self):
        """Steady 25% growth should NOT register as cyclical — log-return
        stdev is low because growth rate is constant."""
        revenues = [10e9 * (1.25 ** i) for i in range(7)]
        df = self._df(years=list(range(2019, 2026)), revenues=revenues)
        result = COMPUTERS["cyclicality"](df)
        assert result is not None
        # Log returns are constant (= ln(1.25)), so volatility should be
        # near zero → score 7. This is the detrending fix.
        assert result.score == 7

    def test_genuine_cyclical_scores_low(self):
        """Cyclical pattern: revenue swings around a trend."""
        revenues = [100e9, 130e9, 90e9, 140e9, 80e9, 150e9, 75e9]
        df = self._df(years=list(range(2019, 2026)), revenues=revenues)
        result = COMPUTERS["cyclicality"](df)
        assert result is not None
        assert result.score is not None
        assert result.score <= 3


# ─────────────────────────────────────────────────────────────────────────────
# Calibration baseline regression test
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not Path("valuation_data/database/investment.duckdb").exists(),
    reason="DuckDB not present",
)
class TestCalibrationBaselines:
    """Regression alarm: current computer output across the live universe
    must match the pinned baselines.

    When this fails, you've changed a formula or a bucket cutoff. The
    correct response is one of:

      1. **Bug**: revert the formula change.
      2. **Intentional change**: bump the affected dimension's
         `code_version` in the catalog, regenerate the baseline file
         (`scripts/calibrate_qualitative.py`), and document the rationale
         in the PR description (analyst sign-off required per the PR
         template).

    Silently regenerating the baseline file without a code_version bump
    is the failure mode this test is designed to prevent — it would
    erase the audit trail for "why did AAPL's ROIIC score change."
    """

    def test_universe_scores_match_baselines(self):
        baselines = json.loads(_BASELINES_PATH.read_text())

        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        try:
            current_universe = sorted(
                r[0] for r in db._conn.execute(
                    "SELECT DISTINCT ticker FROM company_records"
                ).fetchall()
            )
            mismatches = []
            for ticker in current_universe:
                if ticker not in baselines:
                    # New ticker added since baselines were pinned —
                    # not a regression, but worth surfacing
                    mismatches.append(f"{ticker}: missing from baselines (newly ingested?)")
                    continue
                df = db.get_latest(ticker)
                # Phase Q-1+: TTM/quarterly rows live alongside FY in
                # company_records. Baselines were calibrated on FY only;
                # filter to match. (Production runner does the same in
                # aletheia/qualitative/runner.py.)
                if df is not None and "period" in df.columns:
                    df = df[df["period"] == "FY"].copy()
                if df.empty:
                    continue
                for dim_id, computer in COMPUTERS.items():
                    expected = baselines[ticker].get(dim_id)
                    try:
                        result = computer(df)
                        actual = result.score if result is not None else None
                    except Exception as e:
                        actual = f"ERR:{type(e).__name__}"
                    if actual != expected:
                        mismatches.append(
                            f"{ticker}/{dim_id}: expected={expected} actual={actual}"
                        )
        finally:
            db.close()

        assert not mismatches, (
            f"{len(mismatches)} score(s) drifted from baseline. If the change "
            f"was intentional, regenerate "
            f"tests/qualitative/calibration_baselines.json and bump the "
            f"affected dimension's code_version. Drift detail:\n" +
            "\n".join(mismatches[:30])
        )
