"""Tests for editable DCF assumptions: validator, persistence, and the
override→recompute path.

Run: python -m pytest tests/test_dcf_overrides.py -v
"""

import unittest

from aletheia.calculations.dcf_assumption_validation import (
    validate_dcf_assumptions,
    MAX_TERMINAL_G,
)


# A clean, internally-consistent assumptions bundle (mature-compounder-ish).
_OK = {
    "revenue_cagr_y1_5": 0.45, "revenue_cagr_y6_10": 0.25,
    "ebit_margin_current": 0.268, "ebit_margin_terminal": 0.215,
    "capex_pct_revenue": 0.013, "da_pct_revenue": 0.012,
    "nwc_pct_revenue": 0.03, "tax_rate": 0.138,
    "wacc": 0.094, "terminal_growth": 0.02,
}


class TestValidator(unittest.TestCase):

    def test_clean_assumptions_ok(self):
        self.assertEqual(validate_dcf_assumptions(_OK).status, "ok")

    def test_terminal_growth_ge_wacc_is_error(self):
        # g(0.10) >= wacc(0.094); also out of bounds — either way an error.
        r = validate_dcf_assumptions({**_OK, "terminal_growth": 0.10},
                                     {"terminal_growth"})
        self.assertEqual(r.status, "error")

    def test_explosive_spread_is_error(self):
        # wacc 0.094, g 0.088 → spread 0.6% (≤1%) explodes the TV.
        r = validate_dcf_assumptions({**_OK, "terminal_growth": 0.088},
                                     {"terminal_growth"})
        self.assertEqual(r.status, "error")

    def test_thin_spread_is_warn(self):
        # wacc 0.05, g 0.035 → spread 1.5% (between 1% and 2%).
        r = validate_dcf_assumptions(
            {**_OK, "wacc": 0.05, "terminal_growth": 0.035},
            {"wacc", "terminal_growth"})
        self.assertEqual(r.status, "warn")

    def test_terminal_growth_above_hard_cap_is_error(self):
        r = validate_dcf_assumptions({**_OK, "terminal_growth": MAX_TERMINAL_G + 0.005},
                                     {"terminal_growth"})
        self.assertEqual(r.status, "error")

    def test_lifecycle_cap_breach_is_warn(self):
        r = validate_dcf_assumptions(
            {**_OK, "terminal_growth": 0.035}, {"terminal_growth"},
            terminal_growth_cap=0.025)
        self.assertEqual(r.status, "warn")

    def test_margin_expansion_is_warn(self):
        r = validate_dcf_assumptions({**_OK, "ebit_margin_terminal": 0.30},
                                     {"terminal_ebit_margin"})
        self.assertEqual(r.status, "warn")

    def test_out_of_bounds_wacc_is_error(self):
        r = validate_dcf_assumptions({**_OK, "wacc": 0.20}, {"discount_rate"})
        self.assertEqual(r.status, "error")

    def test_layer3_wacc_far_from_baseline_warns(self):
        r = validate_dcf_assumptions(
            {**_OK, "wacc": 0.14}, {"discount_rate"},
            baseline={**_OK, "wacc": 0.094})
        self.assertEqual(r.status, "warn")
        self.assertTrue(any("deviates" in w for w in r.warnings))

    def test_overridden_fields_are_badged(self):
        r = validate_dcf_assumptions(_OK, {"discount_rate", "tax_rate"})
        badged = {f.name for f in r.fields if f.overridden}
        self.assertEqual(badged, {"wacc", "tax_rate"})


class TestPersistence(unittest.TestCase):
    """Round-trips against the real DuckDB using a throwaway ticker."""

    TICKER = "ZZOVRTEST"

    def setUp(self):
        from aletheia.data.database import InvestmentDatabase
        self.db = InvestmentDatabase(verbose=False)
        self.db.clear_dcf_overrides(self.TICKER)

    def tearDown(self):
        self.db.clear_dcf_overrides(self.TICKER)
        self.db.close()

    def test_roundtrip_and_null_handling(self):
        self.db.upsert_dcf_overrides(
            self.TICKER, {"discount_rate": 0.11, "terminal_growth": 0.03,
                          "tax_rate": None}, updated_by="t", note="n")
        got = self.db.get_dcf_overrides(self.TICKER)
        self.assertEqual(got["discount_rate"], 0.11)
        self.assertEqual(got["terminal_growth"], 0.03)
        self.assertNotIn("tax_rate", got)            # None → not stored
        self.assertEqual(got["updated_by"], "t")

    def test_versioning_latest_wins(self):
        self.db.upsert_dcf_overrides(self.TICKER, {"discount_rate": 0.10})
        self.db.upsert_dcf_overrides(self.TICKER, {"discount_rate": 0.12})
        self.assertEqual(self.db.get_dcf_overrides(self.TICKER)["discount_rate"], 0.12)

    def test_clear(self):
        self.db.upsert_dcf_overrides(self.TICKER, {"discount_rate": 0.10})
        self.db.clear_dcf_overrides(self.TICKER)
        self.assertEqual(self.db.get_dcf_overrides(self.TICKER), {})


class TestOverrideRecompute(unittest.TestCase):
    """Integration: a persisted override changes the DCF; no override is a
    no-op. Uses AAPL (FCFF-compatible, always present)."""

    TICKER = "AAPL"

    def _run(self, apply_overrides=True):
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.dcf_engine import DCFEngine
        calc = make_calc_input(self.TICKER, apply_overrides=apply_overrides)
        res = DCFEngine(verbose=False).run(calc)
        iv = res.intrinsic_per_share(res.base.enterprise_value, res.net_debt)
        return res.base.assumptions.wacc, iv

    def setUp(self):
        from aletheia.data.database import InvestmentDatabase
        self.db = InvestmentDatabase(verbose=False)
        self.db.clear_dcf_overrides(self.TICKER)

    def tearDown(self):
        self.db.clear_dcf_overrides(self.TICKER)
        self.db.close()

    def test_higher_wacc_lowers_iv(self):
        base_wacc, base_iv = self._run()
        self.db.upsert_dcf_overrides(self.TICKER, {"discount_rate": 0.13})
        ov_wacc, ov_iv = self._run()
        self.assertAlmostEqual(ov_wacc, 0.13, places=4)
        self.assertLess(ov_iv, base_iv)

    def test_apply_overrides_false_ignores_persisted(self):
        self.db.upsert_dcf_overrides(self.TICKER, {"discount_rate": 0.13})
        wacc_baseline, _ = self._run(apply_overrides=False)
        self.assertNotAlmostEqual(wacc_baseline, 0.13, places=3)

    def test_no_override_is_noop(self):
        w1, iv1 = self._run()
        w2, iv2 = self._run()
        self.assertAlmostEqual(iv1, iv2, places=6)

    def test_terminal_roic_override_changes_iv(self):
        # Lowering terminal ROIC raises the reinvestment rate (g/ROIC), cutting
        # terminal FCF → lower IV. Confirms the Tier-2 bridge reaches the engine.
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.dcf_engine import DCFEngine
        _, base_iv = self._run()
        base_roic = DCFEngine(verbose=False).run(
            make_calc_input(self.TICKER, apply_overrides=False)
        ).base.assumptions.terminal_roic
        self.db.upsert_dcf_overrides(self.TICKER, {"terminal_roic": max(0.05, base_roic * 0.5)})
        ov_roic = DCFEngine(verbose=False).run(
            make_calc_input(self.TICKER)
        ).base.assumptions.terminal_roic
        _, ov_iv = self._run()
        self.assertLess(ov_roic, base_roic)
        self.assertLess(ov_iv, base_iv)

    def test_terminal_roic_roundtrip(self):
        self.db.upsert_dcf_overrides(self.TICKER, {"terminal_roic": 0.17})
        self.assertAlmostEqual(
            self.db.get_dcf_overrides(self.TICKER).get("terminal_roic"), 0.17, places=4)


class TestTerminalRoicValidation(unittest.TestCase):

    def _bundle(self, **over):
        b = dict(_OK)
        b.update(over)
        return b

    def test_roic_le_growth_is_error(self):
        v = validate_dcf_assumptions(
            self._bundle(terminal_roic=0.03, terminal_growth=0.02), {"terminal_roic"})
        self.assertEqual(v.status, "error")  # 3% ROIC < per-field 5% floor + ≤g

    def test_roic_below_wacc_is_warn(self):
        # ROIC 7% > g 2% (ok) but < WACC 9.4% → warn (value-destructive terminal).
        v = validate_dcf_assumptions(
            self._bundle(terminal_roic=0.07), {"terminal_roic"})
        self.assertEqual(v.status, "warn")

    def test_roic_out_of_bounds_is_error(self):
        v = validate_dcf_assumptions(self._bundle(terminal_roic=0.90), {"terminal_roic"})
        self.assertEqual(v.status, "error")

    def test_roic_above_wacc_is_ok(self):
        v = validate_dcf_assumptions(self._bundle(terminal_roic=0.20), {"terminal_roic"})
        self.assertEqual(v.status, "ok")

    def test_scenario_override_enforces_roic_band(self):
        from aletheia.contracts.interfaces import ScenarioOverride
        with self.assertRaises(Exception):
            ScenarioOverride(name="bad roic", scenario_type="base_alternative",
                             proposed_by="analyst", rationale="out of band",
                             terminal_roic=0.90)


if __name__ == "__main__":
    unittest.main()
