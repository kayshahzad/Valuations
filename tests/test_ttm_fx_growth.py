"""Regression: TTM revenue growth must be currency-consistent for non-USD filers.

NVO (DKK filer) showed a spurious TTM revenue growth of -83.3%. Root cause:
`ttm_snapshot["Revenue"]` was FX-converted to USD (via the history frame) while
`_attach_prior_year_ttm` pulled the prior-year TTM straight from FMP quarterly
statements in NATIVE DKK and never converted it — so the growth ratio divided a
USD numerator by a DKK denominator (USD/DKK ≈ 0.154 → ~-83%).

Fix: `_attach_prior_year_ttm(ticker, snap, fx_rate)` scales the prior-year
figures by the same USD-per-foreign-unit rate the snapshot used, so both sides
of the ratio are in one currency. growth is currency-invariant, so the result
must match the native-vs-native growth.

Run: python -m pytest tests/test_ttm_fx_growth.py -v
"""

import unittest

import aletheia.ui.financials as fin


# NVO-like quarters (DKK), most-recent first. Current TTM = q[0:4],
# prior-year TTM = q[4:8]. Native growth = 327.8 / 303.141 - 1 ≈ +8.13%.
_CUR_TTM_NATIVE = 327.8e9
_PRIOR_TTM_NATIVE = 303.141e9
_FAKE_QUARTERS = (
    [{"revenue": _CUR_TTM_NATIVE / 4, "netIncome": 30e9, "date": "2026-03-31"}] * 4
    + [{"revenue": _PRIOR_TTM_NATIVE / 4, "netIncome": 27e9, "date": "2025-03-31"}] * 4
)
_FX_DKK_USD = 0.15419012308120728


def _attach_with_stub(snapshot, fx_rate):
    """Run _attach_prior_year_ttm with FMP stubbed to the fake quarters."""
    import aletheia.data.fmp_client as fmp
    orig = fmp.fetch_income_statements
    fmp.fetch_income_statements = lambda *a, **k: list(_FAKE_QUARTERS)
    try:
        fin._attach_prior_year_ttm("NVO", snapshot, fx_rate=fx_rate)
    finally:
        fmp.fetch_income_statements = orig


class TestTTMFXGrowth(unittest.TestCase):

    def test_nvo_growth_is_currency_consistent(self):
        # Snapshot Revenue is the USD-converted current TTM (the production path).
        snap = {"Revenue": _CUR_TTM_NATIVE * _FX_DKK_USD}
        _attach_with_stub(snap, _FX_DKK_USD)
        growth = snap["Revenue"] / snap["PriorYearRevenue"] - 1.0
        # Must equal the native-vs-native growth, NOT the -83% currency-mix.
        native_growth = _CUR_TTM_NATIVE / _PRIOR_TTM_NATIVE - 1.0
        self.assertAlmostEqual(growth, native_growth, places=6)
        self.assertGreater(growth, 0.05)            # ~+8%, sane
        self.assertGreater(abs(growth - (-0.833)), 0.5)  # nowhere near the -83.3% bug

    def test_usd_filer_is_unchanged(self):
        # fx_rate == 1.0 (USD filer / no conversion) → prior values untouched.
        snap = {"Revenue": _CUR_TTM_NATIVE}
        _attach_with_stub(snap, 1.0)
        self.assertAlmostEqual(snap["PriorYearRevenue"], _PRIOR_TTM_NATIVE, places=2)
        growth = snap["Revenue"] / snap["PriorYearRevenue"] - 1.0
        self.assertAlmostEqual(growth, _CUR_TTM_NATIVE / _PRIOR_TTM_NATIVE - 1.0, places=6)

    def test_net_income_also_converted(self):
        snap = {"Revenue": _CUR_TTM_NATIVE * _FX_DKK_USD}
        _attach_with_stub(snap, _FX_DKK_USD)
        # Prior NI (27e9 * 4 = 108e9 DKK) scaled into USD.
        self.assertAlmostEqual(
            snap["PriorYearNetIncome"], 108e9 * _FX_DKK_USD, places=2)


if __name__ == "__main__":
    unittest.main()
