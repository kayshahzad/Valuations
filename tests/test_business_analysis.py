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

    def test_revenue_spike_alone_is_not_an_ma_break(self):
        # A +50% revenue jump with NO cash-flow acquisition spend is organic
        # hypergrowth, NOT M&A — the old revenue-spike heuristic wrongly flagged
        # it (and flagged early-stage growth like CRM FY2004-2007). With no
        # ticker, ma_spend is unavailable → no false break.
        years = [2017, 2018, 2019, 2020, 2021, 2022, 2023]
        revs = [100, 105, 110, 116, 174, 183, 192]  # 2021 = +50%
        gd = build_growth_decomposition(_Calc(revs, years))
        self.assertTrue(gd["available"])
        self.assertEqual(gd["break_years"], [])
        self.assertEqual(gd["ma_contribution_pp"], 0.0)

    def test_cashflow_ma_in_window_flags_break_not_separable(self):
        # M&A is detected from cash-flow spend, not revenue spikes. A material
        # acquisition year inside the window flags a break + marks organic as an
        # upper bound (flag-only — acquired revenue isn't disclosed).
        from aletheia.tools import business_analysis as ba_mod
        cls = _Cls(sector="Technology", industry="Software")
        cls.ticker = "TST"
        calc = _Calc([100, 110, 121, 133, 146, 161],
                     [2018, 2019, 2020, 2021, 2022, 2023], classification=cls)
        orig = ba_mod.ma_spend
        ba_mod.ma_spend = lambda t, years=None: {
            "material": True,
            "years": [{"year": 2022, "spend": 5e9, "pct_of_revenue": 0.30}],
        }
        try:
            gd = ba_mod.build_growth_decomposition(calc)
        finally:
            ba_mod.ma_spend = orig
        self.assertIn(2022, gd["break_years"])
        self.assertFalse(gd["ma_separable"])
        self.assertTrue(gd["organic_is_upper_bound"])
        self.assertIsNone(gd["ma_contribution_pp"])

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

    def test_diversified_customer_base_satisfies_dimension(self):
        # A diversified vendor (e.g. CRM) names no customers in its 10-K, but the
        # "Major customers / contracts" dimension is satisfied by verticals /
        # concentration / retention / public references — not just named accounts.
        from aletheia.tools import business_analysis as ba_mod
        import aletheia.agents.business_extraction as bx
        orig = bx.cached_business_ab
        bx.cached_business_ab = lambda t: {
            "major_customers": [],
            "notable_customers": ["AWS", "Toyota"],
            "industry_verticals": ["financial services", "healthcare"],
            "net_retention": "~92% dollar retention",
        }
        try:
            calc = _Calc([100, 106, 112, 119, 126, 134],
                         [2018, 2019, 2020, 2021, 2022, 2023])
            res = ba_mod.build_business_analysis({}, "CRM", calc=calc)
        finally:
            bx.cached_business_ab = orig
        cov = {c["dimension"]: c["status"] for c in res["coverage"]}
        self.assertEqual(cov["Major customers / contracts"], "present")

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


class TestMaDetection(unittest.TestCase):
    """M&A flag-only: cash-flow acquisitionsNet catches bolt-ons that never
    spike YoY revenue (MSFT/Activision blind spot)."""

    def _stub_fmp(self, ba_mod, fmp_client, acq_by_year, rev_by_year):
        ba_mod._MA_SPEND_CACHE.clear()
        self._orig_cf = fmp_client.fetch_cash_flows
        self._orig_inc = fmp_client.fetch_income_statements
        fmp_client.fetch_cash_flows = lambda t, **k: [
            {"calendarYear": str(y), "acquisitionsNet": -a} for y, a in acq_by_year.items()]
        fmp_client.fetch_income_statements = lambda t, **k: [
            {"calendarYear": str(y), "revenue": r} for y, r in rev_by_year.items()]

    def _restore(self, ba_mod, fmp_client):
        fmp_client.fetch_cash_flows = self._orig_cf
        fmp_client.fetch_income_statements = self._orig_inc
        ba_mod._MA_SPEND_CACHE.clear()

    def test_material_ma_flagged(self):
        from aletheia.tools import business_analysis as ba_mod
        from aletheia.data import fmp_client
        # 28% then 11% of revenue spent on acquisitions, no YoY revenue spike.
        self._stub_fmp(ba_mod, fmp_client,
                       acq_by_year={2024: 69e9, 2022: 22e9, 2023: 1e9},
                       rev_by_year={2024: 245e9, 2023: 212e9, 2022: 198e9})
        try:
            m = ba_mod.ma_spend("MSFT", [2022, 2023, 2024])
        finally:
            self._restore(ba_mod, fmp_client)
        self.assertTrue(m["material"])
        flagged = {x["year"] for x in m["years"]}
        self.assertIn(2024, flagged)
        self.assertIn(2022, flagged)
        self.assertNotIn(2023, flagged)  # 1/212 = 0.5% < 3% threshold

    def test_immaterial_ma_not_flagged(self):
        from aletheia.tools import business_analysis as ba_mod
        from aletheia.data import fmp_client
        self._stub_fmp(ba_mod, fmp_client,
                       acq_by_year={2024: 0.5e9, 2023: 0.2e9},
                       rev_by_year={2024: 245e9, 2023: 212e9})
        try:
            m = ba_mod.ma_spend("XYZ", [2023, 2024])
        finally:
            self._restore(ba_mod, fmp_client)
        self.assertFalse(m["material"])

    def test_growth_decomp_drops_all_organic_when_ma_material(self):
        from aletheia.tools import business_analysis as ba_mod
        from aletheia.data import fmp_client
        # Steady ~10% revenue (no break) but heavy acquisition spend.
        cls = _Cls(sector="Technology", industry="Software")
        cls.ticker = "MSFT"
        calc = _Calc([150e9, 168e9, 198e9, 212e9, 245e9, 270e9],
                     [2020, 2021, 2022, 2023, 2024, 2025], classification=cls)
        self._stub_fmp(ba_mod, fmp_client,
                       acq_by_year={2024: 69e9, 2022: 22e9},
                       rev_by_year={2020: 150e9, 2021: 168e9, 2022: 198e9,
                                    2023: 212e9, 2024: 245e9, 2025: 270e9})
        orig_ps = ba_mod.peer_stats
        ba_mod.peer_stats = lambda t, pg=None: {"available": False}
        try:
            gd = ba_mod.build_growth_decomposition(calc)
        finally:
            ba_mod.peer_stats = orig_ps
            self._restore(ba_mod, fmp_client)
        self.assertFalse(gd["ma_separable"])
        self.assertTrue(gd["organic_is_upper_bound"])
        self.assertIsNotNone(gd["ma_spend"])
        self.assertNotIn("all organic", gd["split"])


class TestOperatingLeverage(unittest.TestCase):
    """Deterministic operating leverage: incremental EBIT margin from financials."""

    def test_incremental_margin(self):
        import pandas as pd
        from aletheia.tools.business_analysis import operating_leverage

        class _C:
            df = pd.DataFrame({
                "fiscal_year": [2019, 2020, 2021, 2022, 2023, 2024],
                "clean_Revenue": [100, 120, 140, 160, 180, 200],
                "raw_OperatingIncome": [10, 16, 24, 34, 46, 60],  # EBIT grows faster
                "period": ["FY"] * 6,
            })
        ol = operating_leverage(_C())
        self.assertTrue(ol["available"])
        # ΔEBIT/ΔRev = (60-10)/(200-100) = 50%; current margin 60/200 = 30%.
        self.assertAlmostEqual(ol["incremental_margin"], 0.50, places=3)
        self.assertAlmostEqual(ol["current_margin"], 0.30, places=3)
        self.assertIn("expanding", ol["label"])


class TestPorterAndMarketDims(unittest.TestCase):
    """New §4 dims: Porter forces + market dynamics flip to present on extraction."""

    def test_new_dims_present_when_extracted(self):
        from aletheia.tools import business_analysis as ba_mod
        import aletheia.agents.business_extraction as bx
        orig = bx.cached_business_ab
        bx.cached_business_ab = lambda t: {
            "market_share_trajectory": "gaining ~1pt/yr",
            "category_creation": "defined the cloud CRM category",
            "competitive_intensity": "high, rising on AI",
            "customer_power": "moderate, weakening as switching costs rise",
            "supplier_power": "low (cloud/talent)",
            "regulatory_trajectory": "data-privacy tightening",
        }
        try:
            calc = _Calc([100, 106, 112, 119, 126, 134],
                         [2018, 2019, 2020, 2021, 2022, 2023])
            res = ba_mod.build_business_analysis({}, "TST", calc=calc)
        finally:
            bx.cached_business_ab = orig
        cov = {c["dimension"]: c["status"] for c in res["coverage"]}
        for d in ("Share trajectory", "New market / category creation",
                  "Competitive intensity", "Customer power (trajectory)",
                  "Supplier power (trajectory)", "Regulatory trajectory"):
            self.assertEqual(cov.get(d), "present", d)


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
    """P3/Tier-2 — TAM confidence band + deterministic implied-share range."""

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

    def test_tam_band_implied_share_inverts(self):
        from aletheia.tools.business_analysis import tam_assessment
        tam = tam_assessment("TST", {
            "tam_estimate": "$50 billion", "tam_low": "$40 billion",
            "tam_high": "$80 billion", "tam_approach": "IT-spend share",
        }, latest_revenue=4e9)
        # base 4/50=8%; low-TAM($40B) → higher share 10%; high-TAM($80B) → 5%.
        self.assertAlmostEqual(tam["implied_share"], 0.08, places=3)
        self.assertAlmostEqual(tam["implied_share_high"], 0.10, places=3)
        self.assertAlmostEqual(tam["implied_share_low"], 0.05, places=3)
        self.assertEqual(tam["tam_approach"], "IT-spend share")


class TestGroundingBridge(unittest.TestCase):
    """Tier-2 #3 — bridge rows carry override targets + computation."""

    class _Asm:
        revenue_cagr_y1_5 = 0.10
        revenue_cagr_y6_10 = 0.08
        terminal_growth = 0.025
        ebit_margin_current = 0.20
        ebit_margin_terminal = 0.18
        capex_pct_revenue = 0.06
        base_roic = 0.25
        wacc = 0.09
        @property
        def terminal_roic(self):
            return max(self.base_roic, 0.08)

    class _Res:
        ticker = "TST"
        class base:
            assumptions = None

    def _result(self):
        r = self._Res(); r.base.assumptions = self._Asm(); return r

    def test_new_rows_present_with_override_targets(self):
        calc = _Calc([100, 106, 112, 119, 126, 134], [2018, 2019, 2020, 2021, 2022, 2023])
        gd = build_growth_decomposition(calc)
        ag = build_assumption_grounding(calc, self._result(), growth_decomposition=gd)
        by = {r["assumption"]: r for r in ag["rows"]}
        for name, field in [("Y6-10 revenue CAGR", "revenue_growth_y6_10"),
                            ("CapEx % of revenue", "capex_pct_revenue"),
                            ("Terminal ROIC", "terminal_roic")]:
            self.assertIn(name, by)
            self.assertEqual(by[name]["override_field"], field)
            self.assertTrue(by[name]["computation"])
        # Terminal ROIC grounded = half-fade base(25%)→WACC(9%) = 17%.
        self.assertAlmostEqual(by["Terminal ROIC"]["grounded_value"], 0.17, places=3)
        self.assertIsNotNone(by["Terminal ROIC"]["override_value"])

    def test_reconciliation_verdict(self):
        calc = _Calc([100, 106, 112, 119, 126, 134], [2018, 2019, 2020, 2021, 2022, 2023])
        ag = build_assumption_grounding(calc, self._result())
        rec = ag.get("reconciliation") or {}
        self.assertIn("terminal_margin_verdict", rec)
        self.assertIn("capex_verdict", rec)

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
