"""Market-context builders (memo §8): earnings surprises, ratings, ESG, news.

FMP is stubbed so the tests are pure and deterministic.

Run: python -m pytest tests/test_market_context.py -v
"""

import datetime
import unittest

from aletheia.tools import market_context as mc
from aletheia.data import fmp_client


class TestEarningsSurprises(unittest.TestCase):

    def setUp(self):
        self._orig = fmp_client.fetch_earnings
        fmp_client.fetch_earnings = lambda t, **k: [
            {"date": "2026-07-16", "epsActual": None, "epsEstimated": 1.28},  # not reported
            {"date": "2026-04-16", "epsActual": 1.20, "epsEstimated": 1.10},  # beat
            {"date": "2026-01-22", "epsActual": 1.05, "epsEstimated": 1.00},  # beat
            {"date": "2025-10-15", "epsActual": 0.90, "epsEstimated": 1.00},  # miss
            {"date": "2025-07-17", "epsActual": 1.00, "epsEstimated": 1.00},  # in-line
        ]

    def tearDown(self):
        fmp_client.fetch_earnings = self._orig

    def test_surprise_history(self):
        es = mc.build_earnings_surprises("TST")
        self.assertTrue(es["available"])
        self.assertEqual(es["n_reported"], 4)        # excludes the unreported quarter
        self.assertEqual(es["n_beat"], 2)
        self.assertEqual(es["n_miss"], 1)
        self.assertEqual(es["beat_streak"], 2)       # two most-recent are beats
        q = es["quarters"][0]
        self.assertEqual(q["date"], "2026-04-16")    # most-recent first
        self.assertAlmostEqual(q["surprise_pct"], (1.20 - 1.10) / 1.10, places=4)
        self.assertEqual(q["label"], "beat")


class TestRatings(unittest.TestCase):

    def setUp(self):
        self._cons = fmp_client.fetch_grades_consensus
        self._grades = fmp_client.fetch_grades
        self._pt = fmp_client.fetch_price_target_summary
        self._ptc = fmp_client.fetch_price_target_consensus
        fmp_client.fetch_grades_consensus = lambda t, **k: {
            "consensus": "Buy", "strongBuy": 0, "buy": 31, "hold": 10, "sell": 0, "strongSell": 0}
        fmp_client.fetch_grades = lambda t, **k: [
            {"gradingCompany": "BTIG", "action": "maintain", "newGrade": "Buy", "previousGrade": "Buy", "date": "2026-04-27"},
            {"gradingCompany": "Barclays", "action": "upgrade", "newGrade": "Overweight", "previousGrade": "Equal Weight", "date": "2026-04-20"},
            {"gradingCompany": "BTIG", "action": "maintain", "newGrade": "Buy", "date": "2026-01-01"},  # dup firm → skipped
        ]
        fmp_client.fetch_price_target_summary = lambda t, **k: {
            "lastQuarterAvgPriceTarget": 126.86, "lastQuarterCount": 7}
        fmp_client.fetch_price_target_consensus = lambda t, **k: {"targetHigh": 152.0, "targetLow": 92.0}

    def tearDown(self):
        fmp_client.fetch_grades_consensus = self._cons
        fmp_client.fetch_grades = self._grades
        fmp_client.fetch_price_target_summary = self._pt
        fmp_client.fetch_price_target_consensus = self._ptc

    def test_consolidation(self):
        r = mc.build_ratings_consolidation("TST")
        self.assertTrue(r["available"])
        self.assertEqual(r["consensus"], "Buy")
        self.assertEqual(r["distribution"]["buy"], 31)
        self.assertEqual(r["recent_upgrades_30"], 1)
        self.assertEqual(len(r["recent_actions"]), 2)            # BTIG de-duplicated
        self.assertEqual(r["price_target"]["avg"], 126.86)
        self.assertIsNone(r["independent_research"])            # slot left for licensed feed


class TestNewsAndEsg(unittest.TestCase):

    def test_news_window_filter(self):
        orig = fmp_client.fetch_stock_news
        fmp_client.fetch_stock_news = lambda t, limit=40, **k: [
            {"publishedDate": "2026-06-08 09:00:00", "title": "Recent A", "publisher": "Reuters", "url": "u1"},
            {"publishedDate": "2026-05-01 09:00:00", "title": "Recent B", "publisher": "PRNewsWire", "url": "u2"},
            {"publishedDate": "2025-01-01 09:00:00", "title": "Old C", "publisher": "X", "url": "u3"},  # >90d
        ]
        try:
            news = mc.build_recent_news("TST", days=90, top=5,
                                        as_of=datetime.date(2026, 6, 8))
        finally:
            fmp_client.fetch_stock_news = orig
        self.assertTrue(news["available"])
        self.assertEqual(len(news["items"]), 2)                 # the >90d item is dropped
        self.assertEqual(news["items"][0]["title"], "Recent A")

    def test_esg_placeholder(self):
        esg = mc.build_esg("TST")
        self.assertFalse(esg["available"])           # no feed connected
        self.assertIn("MSCI", esg["providers"])
        self.assertTrue(esg["note"])


if __name__ == "__main__":
    unittest.main()
