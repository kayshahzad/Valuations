"""Bottom-up business analysis (memo §4) + assumption grounding (Phase 0/1).

Covers the deterministic pieces: growth decomposition (organic vs M&A from a
synthetic revenue series with a transformative-break year) and the grounding
comparison rows. The DCF result + calc are stubbed so the test is pure.

Run: python -m pytest tests/test_business_analysis.py -v
"""

import unittest
import pandas as pd

from aletheia.tools.business_analysis import build_growth_decomposition
from aletheia.tools.assumption_grounding import build_assumption_grounding


class _Cls:
    def __init__(self, sector="", industry="", lifecycle="mature",
                 business_model="fcff_compatible"):
        self.sector = sector
        self.industry = industry
        self.lifecycle = lifecycle
        self.business_model = business_model


class _Calc:
    def __init__(self, revs, years, classification=None):
        self.df = pd.DataFrame({
            "fiscal_year": years, "clean_Revenue": revs,
            "period": ["FY"] * len(revs),
        })
        self.classification = classification or _Cls(lifecycle="mature")


class _Assump:
    revenue_cagr_y1_5 = 0.10
    terminal_growth = 0.025


class _Base:
    assumptions = _Assump()


class _Result:
    ticker = "TST"
    base = _Base()


class TestGrowthDecomposition(unittest.TestCase):

    def test_detects_ma_break(self):
        # Steady ~5%/yr then a +50% jump (M&A) in FY2021.
        years = [2017, 2018, 2019, 2020, 2021, 2022, 2023]
        revs = [100, 105, 110, 116, 174, 183, 192]  # 2021 = +50%
        gd = build_growth_decomposition(_Calc(revs, years))
        self.assertTrue(gd["available"])
        self.assertIn(2021, gd["break_years"])
        # Organic should be well below the raw CAGR (which the M&A inflated).
        self.assertLess(gd["organic_cagr"], gd["raw_cagr"])
        self.assertGreater(gd["ma_contribution_pp"], 0)

    def test_market_vs_share_split(self):
        from aletheia.tools import business_analysis as ba_mod
        # Seed the sector cache so the split is deterministic (no DB).
        ba_mod._SECTOR_GROWTH_CACHE["TestSector"] = 0.04  # market grew 4%
        cls = _Cls(sector="TestSector")
        cls.ticker = "TST"
        calc = _Calc([100, 106, 112, 119, 126, 134],
                     [2018, 2019, 2020, 2021, 2022, 2023], classification=cls)
        gd = ba_mod.build_growth_decomposition(calc)
        self.assertAlmostEqual(gd["market_growth_ref"], 0.04, places=4)
        # Organic ~6% vs market 4% → ~+2pp share gain.
        self.assertGreater(gd["share_gain_pp"], 0)
        self.assertEqual(gd["share_label"], "gaining share")

    def test_all_organic_when_no_break(self):
        years = [2017, 2018, 2019, 2020, 2021, 2022]
        revs = [100, 106, 112, 119, 126, 134]  # steady ~6%
        gd = build_growth_decomposition(_Calc(revs, years))
        self.assertTrue(gd["available"])
        self.assertEqual(gd["break_years"], [])
        self.assertEqual(gd["ma_contribution_pp"], 0.0)
        self.assertAlmostEqual(gd["organic_cagr"], gd["raw_cagr"], places=6)

    def test_too_little_history(self):
        gd = build_growth_decomposition(_Calc([100, 110], [2022, 2023]))
        self.assertFalse(gd["available"])


class TestAssumptionGrounding(unittest.TestCase):

    def test_rows_and_lifecycle_grounding(self):
        calc = _Calc([100, 106, 112, 119, 126, 134], [2018, 2019, 2020, 2021, 2022, 2023])
        gd = build_growth_decomposition(calc)
        cs = {"consensus": {"forward_cagr": 0.06}}
        wa = {"premia": {"idiosyncratic": 0.0}}
        ag = build_assumption_grounding(
            calc, _Result(), growth_decomposition=gd, current_state=cs, wacc_analysis=wa)
        self.assertTrue(ag["available"])
        labels = [r["assumption"] for r in ag["rows"]]
        self.assertIn("Y1-5 revenue CAGR", labels)
        self.assertIn("Terminal growth", labels)
        self.assertIn("Idiosyncratic WACC premium", labels)
        # Terminal growth grounded to the 'mature' lifecycle profile (3%).
        tg = next(r for r in ag["rows"] if r["assumption"] == "Terminal growth")
        self.assertAlmostEqual(tg["grounded_value"], 0.03, places=3)
        # Y1-5 grounded reference blends organic (~6%) + consensus (6%).
        cagr = next(r for r in ag["rows"] if r["assumption"] == "Y1-5 revenue CAGR")
        self.assertIsNotNone(cagr["grounded_value"])

    def test_no_base_unavailable(self):
        class Empty:
            base = None
        ag = build_assumption_grounding(_Calc([1, 2, 3, 4], [1, 2, 3, 4]), Empty())
        self.assertFalse(ag["available"])


class TestPhase2Merge(unittest.TestCase):
    """Phase-2 extracted A+B fields merge into the block + flip coverage."""

    def test_extraction_flips_coverage(self):
        from aletheia.tools import business_analysis as ba_mod
        import aletheia.agents.business_extraction as bx
        orig = bx.cached_business_ab
        bx.cached_business_ab = lambda t: {
            "product_lines": [{"name": "Widget", "pricing_model": "subscription"}],
            "major_customers": [{"name": "US DoD"}],
            "tam_estimate": "$50B",
            "market_share": "8%",
        }
        # Also stub the DB call (no assessments).
        try:
            calc = _Calc([100, 106, 112, 119, 126, 134],
                         [2018, 2019, 2020, 2021, 2022, 2023])
            res = ba_mod.build_business_analysis({}, "TST", calc=calc)
        finally:
            bx.cached_business_ab = orig
        self.assertTrue(res["available"])
        self.assertIsNotNone(res["extracted"])
        # Product/customer/TAM/share dimensions should now read 'present'.
        cov = {c["dimension"]: c["status"] for c in res["coverage"]}
        self.assertEqual(cov["Product / service portfolio"], "present")
        self.assertEqual(cov["Major customers / contracts"], "present")
        self.assertEqual(cov["TAM sizing"], "present")
        self.assertEqual(cov["Market share / position"], "present")

    def test_phase3_ce_coverage(self):
        from aletheia.tools import business_analysis as ba_mod
        import aletheia.agents.business_extraction as bx
        orig = bx.cached_business_ab
        bx.cached_business_ab = lambda t: {
            "cac_ltv": "LTV/CAC 4x, 14-mo payback",
            "segment_margin_trajectory": "Cloud margins rising 200bps/yr",
            "new_product_launches": [{"name": "Model X"}],
        }
        try:
            calc = _Calc([100, 106, 112, 119, 126, 134],
                         [2018, 2019, 2020, 2021, 2022, 2023])
            res = ba_mod.build_business_analysis({}, "TST", calc=calc)
        finally:
            bx.cached_business_ab = orig
        cov = {c["dimension"]: c["status"] for c in res["coverage"]}
        self.assertEqual(cov["CAC / LTV / cohorts"], "present")
        self.assertEqual(cov["Margin trajectory by segment"], "present")
        self.assertEqual(cov["New product launches"], "present")


class TestSectorTemplate(unittest.TestCase):
    """Phase 4 — sector template tags priority dimensions + sorts them first."""

    def test_defense_template(self):
        from aletheia.tools import business_analysis as ba_mod
        import aletheia.agents.business_extraction as bx
        orig = bx.cached_business_ab
        bx.cached_business_ab = lambda t: None
        try:
            calc = _Calc([100, 106, 112, 119, 126, 134],
                         [2018, 2019, 2020, 2021, 2022, 2023],
                         classification=_Cls(sector="Industrials",
                                             industry="Aerospace & Defense"))
            res = ba_mod.build_business_analysis({}, "TST", calc=calc)
        finally:
            bx.cached_business_ab = orig
        tpl = res["sector_template"]
        self.assertEqual(tpl["key"], "defense_govt")
        self.assertTrue(tpl["emphasis"])
        # Priority dimensions sorted to the top of the coverage list.
        self.assertTrue(res["coverage"][0]["priority"])
        prio = {c["dimension"] for c in res["coverage"] if c["priority"]}
        self.assertIn("Major customers / contracts", prio)


if __name__ == "__main__":
    unittest.main()
