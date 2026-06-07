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
        # Stub both references so the split is deterministic & hermetic (no FMP).
        orig = ba_mod._sector_market_growth
        orig_ps = ba_mod.peer_stats
        ba_mod._sector_market_growth = lambda pg, ex: 0.04  # market grew 4%
        ba_mod.peer_stats = lambda t, pg=None: {"available": False}  # force fallback
        cls = _Cls(sector="Energy")
        cls.ticker = "TST"
        calc = _Calc([100, 106, 112, 119, 126, 134],
                     [2018, 2019, 2020, 2021, 2022, 2023], classification=cls)
        try:
            gd = ba_mod.build_growth_decomposition(calc)
        finally:
            ba_mod._sector_market_growth = orig
            ba_mod.peer_stats = orig_ps
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


class TestPeerGroupResolver(unittest.TestCase):
    """Peer-group resolver fixes misclassified names + drives template uniformly."""

    def test_override_fixes_misclassification(self):
        from config.business_analysis_templates import peer_group_for, template_for
        # LDOS is filed under Technology / IT Services but is govt-IT.
        self.assertEqual(
            peer_group_for("LDOS", "Technology", "Information Technology Services",
                           "growth_compounder", "fcff_compatible"),
            "defense_govt")
        tpl = template_for("LDOS", "Technology", "Information Technology Services",
                           "growth_compounder", "fcff_compatible")
        self.assertEqual(tpl["key"], "defense_govt")
        self.assertEqual(tpl["peer_group"], "defense_govt")

    def test_keyword_inference(self):
        from config.business_analysis_templates import peer_group_for
        self.assertEqual(peer_group_for("X", "Technology", "Software - Infrastructure"), "tech_saas")
        self.assertEqual(peer_group_for("X", "Semiconductors", "Semiconductors"), "semiconductors")
        self.assertEqual(peer_group_for("X", "Financial Services", "Banks"), "banks")
        self.assertEqual(peer_group_for("X", "Healthcare", "Drug Manufacturers"), "pharma")
        self.assertEqual(peer_group_for("X", "Energy", "Oil & Gas E&P"), "energy")


class TestPeerStats(unittest.TestCase):
    """Curated peer lists + FMP-backed peer stats (refinement P1)."""

    def test_curated_peers_first(self):
        from config.peer_lists import curated_peers
        self.assertEqual(curated_peers("LDOS"), ["BAH", "SAIC", "CACI", "LHX", "GD"])
        self.assertEqual(curated_peers("ldos"), ["BAH", "SAIC", "CACI", "LHX", "GD"])
        self.assertEqual(curated_peers("ZZZ"), [])

    def test_peers_for_uses_curated(self):
        from aletheia.tools import business_analysis as ba_mod
        # Curated list wins regardless of peer_group (true peers, even if not ingested).
        self.assertEqual(ba_mod._peers_for("LDOS", "some_group"),
                         ["BAH", "SAIC", "CACI", "LHX", "GD"])

    def test_peer_stats_medians(self):
        from aletheia.tools import business_analysis as ba_mod
        from aletheia.data import fmp_client
        ba_mod._PEER_STATS_CACHE.clear()
        # 5 fake peers: revenue grows ~10%/yr, op margin 10%, EV/EBITDA 12x.
        revs = [{"revenue": r, "operatingIncome": r * 0.10}
                for r in [161, 146, 133, 121, 110, 100]]  # most-recent first
        orig_inc, orig_km = fmp_client.fetch_income_statements, fmp_client.fetch_key_metrics
        fmp_client.fetch_income_statements = lambda p: list(revs)
        fmp_client.fetch_key_metrics = lambda p: [{"evToEBITDA": 12.0}]
        try:
            ps = ba_mod.peer_stats("LDOS")
        finally:
            fmp_client.fetch_income_statements = orig_inc
            fmp_client.fetch_key_metrics = orig_km
            ba_mod._PEER_STATS_CACHE.clear()
        self.assertTrue(ps["available"])
        self.assertEqual(ps["peers"], ["BAH", "SAIC", "CACI", "LHX", "GD"])
        self.assertAlmostEqual(ps["ev_ebitda_median"], 12.0, places=2)
        self.assertAlmostEqual(ps["op_margin_median"], 0.10, places=3)
        self.assertGreater(ps["market_growth_median"], 0.08)

    def test_peer_stats_unavailable_below_three(self):
        from aletheia.tools import business_analysis as ba_mod
        from aletheia.data import fmp_client
        ba_mod._PEER_STATS_CACHE.clear()
        orig_inc, orig_km = fmp_client.fetch_income_statements, fmp_client.fetch_key_metrics
        fmp_client.fetch_income_statements = lambda p: []
        fmp_client.fetch_key_metrics = lambda p: []
        try:
            ps = ba_mod.peer_stats("ZZZ")  # no curated, no group → no peers
        finally:
            fmp_client.fetch_income_statements = orig_inc
            fmp_client.fetch_key_metrics = orig_km
            ba_mod._PEER_STATS_CACHE.clear()
        self.assertFalse(ps["available"])


class TestSegmentEconomics(unittest.TestCase):
    """P2 — FMP revenue mix overlaid with extracted segment margins."""

    def _stub_seg(self):
        return [
            {"fiscalYear": 2025, "data": {"Big": 600, "Small": 400}},
            {"fiscalYear": 2024, "data": {"Big": 500, "Small": 380}},
        ]

    def test_mix_and_growth(self):
        from aletheia.tools import business_analysis as ba_mod
        from aletheia.data import fmp_client
        ba_mod._SEGMENT_CACHE.clear()
        orig = fmp_client.fetch_revenue_product_segmentation
        fmp_client.fetch_revenue_product_segmentation = lambda t, **k: self._stub_seg()
        try:
            seg = ba_mod.segment_economics("TST")
        finally:
            fmp_client.fetch_revenue_product_segmentation = orig
            ba_mod._SEGMENT_CACHE.clear()
        self.assertTrue(seg["available"])
        self.assertEqual(seg["n_segments"], 2)
        big = seg["segments"][0]  # sorted by revenue desc
        self.assertEqual(big["segment"], "Big")
        self.assertAlmostEqual(big["rev_pct"], 0.6, places=3)
        self.assertAlmostEqual(big["yoy_growth"], 0.2, places=3)  # 600/500-1
        self.assertFalse(seg["has_margins"])

    def test_margin_overlay(self):
        from aletheia.tools import business_analysis as ba_mod
        from aletheia.data import fmp_client
        ba_mod._SEGMENT_CACHE.clear()
        orig = fmp_client.fetch_revenue_product_segmentation
        fmp_client.fetch_revenue_product_segmentation = lambda t, **k: self._stub_seg()
        extracted = [{"segment": "Big", "operating_margin": "15%", "margin_trend": "improving"}]
        try:
            seg = ba_mod.segment_economics("TST", extracted)
        finally:
            fmp_client.fetch_revenue_product_segmentation = orig
            ba_mod._SEGMENT_CACHE.clear()
        self.assertTrue(seg["has_margins"])
        self.assertEqual(seg["segments"][0]["margin"], "15%")
        self.assertEqual(seg["segments"][0]["margin_trend"], "improving")


class TestTamAssessment(unittest.TestCase):
    """P3 — TAM confidence + deterministic implied-share."""

    def test_implied_share_parsed(self):
        from aletheia.tools.business_analysis import tam_assessment, _parse_dollar
        self.assertAlmostEqual(_parse_dollar("$50 billion market"), 50e9)
        self.assertAlmostEqual(_parse_dollar("about $2.5 trillion"), 2.5e12)
        self.assertIsNone(_parse_dollar("a large market"))
        tam = tam_assessment("TST",
                             {"tam_estimate": "$50 billion", "tam_confidence": "Medium"},
                             latest_revenue=5e9)
        self.assertTrue(tam["available"])
        self.assertEqual(tam["tam_confidence"], "medium")
        self.assertAlmostEqual(tam["implied_share"], 0.10, places=3)

    def test_no_tam(self):
        from aletheia.tools.business_analysis import tam_assessment
        tam = tam_assessment("TST", {}, latest_revenue=5e9)
        self.assertFalse(tam["available"])
        self.assertIsNone(tam["implied_share"])


class TestCoverageNA(unittest.TestCase):
    """P5 — N/A labeling distinguishes structural non-applicability from pending."""

    def test_cac_na_for_defense(self):
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
        cov = {c["dimension"]: c for c in res["coverage"]}
        self.assertEqual(cov["CAC / LTV / cohorts"]["status"], "n_a")
        self.assertTrue(cov["CAC / LTV / cohorts"]["reason"])
        self.assertGreaterEqual(res.get("n_na", 0), 1)

    def test_cac_present_when_extracted(self):
        from aletheia.tools import business_analysis as ba_mod
        import aletheia.agents.business_extraction as bx
        orig = bx.cached_business_ab
        bx.cached_business_ab = lambda t: {"cac_ltv": "LTV/CAC 4x"}
        try:
            calc = _Calc([100, 106, 112, 119, 126, 134],
                         [2018, 2019, 2020, 2021, 2022, 2023],
                         classification=_Cls(sector="Industrials",
                                             industry="Aerospace & Defense"))
            res = ba_mod.build_business_analysis({}, "TST", calc=calc)
        finally:
            bx.cached_business_ab = orig
        cov = {c["dimension"]: c["status"] for c in res["coverage"]}
        self.assertEqual(cov["CAC / LTV / cohorts"], "present")  # data beats N/A


class TestAssumptionGroundingP4(unittest.TestCase):
    """P4 — Y1-5 build-up band + terminal margin from segment mix."""

    class _Asm:
        revenue_cagr_y1_5 = 0.05
        terminal_growth = 0.025
        ebit_margin_current = 0.20
        ebit_margin_terminal = 0.15

    class _Res:
        ticker = "TST"
        class base:
            assumptions = None

    def _result(self):
        r = self._Res()
        r.base.assumptions = self._Asm()
        return r

    def test_build_up_band(self):
        calc = _Calc([100, 110, 130, 160, 175, 190], [2018, 2019, 2020, 2021, 2022, 2023])
        gd = build_growth_decomposition(calc)
        ag = build_assumption_grounding(calc, self._result(), growth_decomposition=gd)
        cagr = next(r for r in ag["rows"] if r["assumption"] == "Y1-5 revenue CAGR")
        b = cagr["build_up"]
        self.assertIsNotNone(b)
        self.assertLessEqual(b["band_low"], b["band_high"])

    def test_terminal_margin_from_segments(self):
        calc = _Calc([100, 106, 112, 119, 126, 134], [2018, 2019, 2020, 2021, 2022, 2023])
        seg = {"available": True, "has_margins": True, "segments": [
            {"segment": "A", "rev_pct": 0.5, "margin": "10%", "margin_trend": "declining"},
            {"segment": "B", "rev_pct": 0.5, "margin": "20%", "margin_trend": "improving"},
        ]}
        ag = build_assumption_grounding(calc, self._result(), segment_economics=seg)
        tm = next(r for r in ag["rows"] if r["assumption"] == "Terminal EBIT margin")
        self.assertAlmostEqual(tm["grounded_value"], 0.15, places=3)  # 0.5*10%+0.5*20%
        self.assertEqual(tm["engine_value"], 0.15)


if __name__ == "__main__":
    unittest.main()
