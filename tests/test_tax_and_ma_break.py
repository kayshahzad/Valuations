"""Tax-rate normalization + M&A/regime-break CAGR detection.

Bug 2: a one-time / anomalous single-year effective tax rate (AVGO TTM 1.8%,
FY2025 −1.7%) must not become the DCF's perpetual rate — defer to the
multi-year normalized mean. Genuine low foreign rates (MDT ~15%) are kept.

Bug 1: a recent transformative acquisition (AVGO VMware FY2024 +44%) inflates
every lookback window; the organic CAGR must exclude the break year(s).

Run: python -m pytest tests/test_tax_and_ma_break.py -v
"""

import unittest

import pandas as pd

from aletheia.calculations._tax_rate import resolve_tax_rate
from aletheia.tools.dcf_engine import _organic_cagr_ex_breaks


def _df(rates_by_fy):
    return pd.DataFrame([
        {"fiscal_year": fy, "period": "FY",
         "clean_CashTaxRate": r, "clean_GAAP_TaxRate": r}
        for fy, r in rates_by_fy.items()
    ])


class TestTaxNormalization(unittest.TestCase):

    # AVGO-like history: volatile, mean ~10%.
    AVGO = {2020: -0.21, 2021: 0.004, 2022: 0.076, 2023: 0.067,
            2024: 0.378, 2025: -0.017}

    def test_anomalous_low_current_normalizes_to_mean(self):
        rate, src = resolve_tax_rate(
            ticker="AVGO", fn="t", df=_df(self.AVGO), fy=2026,
            cash_tax_rate=0.018, gaap_tax_rate=0.018)
        self.assertEqual(src, "company_fy_normalized")
        self.assertGreater(rate, 0.05)   # not the 1.8% one-timer

    def test_negative_current_normalizes(self):
        rate, src = resolve_tax_rate(
            ticker="AVGO", fn="t", df=_df(self.AVGO), fy=2025,
            cash_tax_rate=-0.017, gaap_tax_rate=-0.017)
        self.assertEqual(src, "company_fy_normalized")

    def test_genuine_foreign_low_rate_is_kept(self):
        # MDT-like: consistently ~15% (above the 10% one-time floor, and the
        # current matches its own mean) → keep the current rate.
        mdt = {2021: 0.15, 2022: 0.15, 2023: 0.16, 2024: 0.14}
        rate, src = resolve_tax_rate(
            ticker="MDT", fn="t", df=_df(mdt), fy=2025,
            cash_tax_rate=0.15, gaap_tax_rate=0.15)
        self.assertEqual(src, "cash")
        self.assertAlmostEqual(rate, 0.15, places=3)

    def test_outlier_high_current_normalizes(self):
        # A 38% spike vs a ~21% norm is an outlier year → normalize.
        norm = {2021: 0.20, 2022: 0.21, 2023: 0.22, 2024: 0.21}
        rate, src = resolve_tax_rate(
            ticker="X", fn="t", df=_df(norm), fy=2025,
            cash_tax_rate=0.38, gaap_tax_rate=0.38)
        self.assertEqual(src, "company_fy_normalized")

    def test_stable_normal_rate_unchanged(self):
        norm = {2021: 0.21, 2022: 0.21, 2023: 0.21, 2024: 0.21}
        rate, src = resolve_tax_rate(
            ticker="X", fn="t", df=_df(norm), fy=2025,
            cash_tax_rate=0.21, gaap_tax_rate=0.21)
        self.assertEqual(src, "cash")

    def test_anomalous_low_no_history_falls_to_statutory(self):
        rate, src = resolve_tax_rate(
            ticker="X", fn="t", df=_df({2024: 0.02}), fy=2025,
            cash_tax_rate=0.02, gaap_tax_rate=0.02)
        self.assertEqual(src, "statutory")
        self.assertAlmostEqual(rate, 0.21, places=3)


class TestMABreakCagr(unittest.TestCase):

    def _rev_df(self, revs_by_fy):
        return pd.DataFrame([
            {"fiscal_year": fy, "clean_Revenue": v}
            for fy, v in revs_by_fy.items()
        ])

    def test_recent_acquisition_excluded(self):
        # Steady ~12% organic with a +44% M&A spike in 2024.
        revs = {2019: 22.6, 2020: 23.9, 2021: 27.4, 2022: 33.2,
                2023: 35.8, 2024: 51.6, 2025: 63.9}
        # scale to dollars
        revs = {k: v * 1e9 for k, v in revs.items()}
        organic, breaks = _organic_cagr_ex_breaks(self._rev_df(revs))
        self.assertIn(2024, breaks)
        self.assertIsNotNone(organic)
        # organic should be well below the raw ~23% CAGR
        self.assertLess(organic, 0.20)

    def test_no_break_returns_none(self):
        # Smooth ~10% grower, no jumps.
        revs = {2019: 10, 2020: 11, 2021: 12.1, 2022: 13.3,
                2023: 14.6, 2024: 16.1, 2025: 17.7}
        revs = {k: v * 1e9 for k, v in revs.items()}
        organic, breaks = _organic_cagr_ex_breaks(self._rev_df(revs))
        self.assertEqual(breaks, [])
        self.assertIsNone(organic)

    def test_sparse_history_returns_none(self):
        organic, breaks = _organic_cagr_ex_breaks(
            self._rev_df({2024: 50e9, 2025: 60e9}))
        self.assertIsNone(organic)


if __name__ == "__main__":
    unittest.main()
