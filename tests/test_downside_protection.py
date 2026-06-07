"""Downside-protection layer (memo §8).

Covers the deterministic math: upside/downside vs price, the downside ladder
(engine bear + sector-median de-rating), asymmetry ratio + verdict,
required-MoS-by-lifecycle, and the position-sizing band. The engine/multiples
are stubbed so the test is pure and fast.

Run: python -m pytest tests/test_downside_protection.py -v
"""

import unittest

from aletheia.tools.downside_protection import build_downside_protection


class _Scenario:
    def __init__(self, ev):
        self.enterprise_value = ev


class _Result:
    """Minimal DCFResult stand-in. intrinsic_per_share maps EV→per-share
    linearly via a fixed share count so the test controls the IVs directly."""
    def __init__(self, price, bull, base, bear):
        self.current_price = price
        self.net_debt = 0.0
        self._shares = 1.0
        self.bull = _Scenario(bull)
        self.base = _Scenario(base)
        self.bear = _Scenario(bear)

    def intrinsic_per_share(self, ev, net_debt):
        return (ev - net_debt) / self._shares


class _Calc:
    class classification:
        lifecycle = "mature"


class TestDownsideProtection(unittest.TestCase):

    def test_unfavorable_richly_valued(self):
        # price 100, bull 110 (+10%), base 102 (+2%), bear 44 (-56%).
        r = _Result(100.0, 110.0, 102.0, 44.0)
        dp = build_downside_protection(_Calc(), r)
        self.assertTrue(dp["available"])
        self.assertAlmostEqual(dp["base_upside_pct"], 0.02, places=4)
        self.assertAlmostEqual(dp["downside_pct"], -0.56, places=4)
        self.assertLess(dp["asymmetry_ratio"], 1.0)
        self.assertEqual(dp["asymmetry_verdict"], "unfavorable")
        self.assertEqual(dp["mos_verdict"], "insufficient")
        # Insufficient MoS + unfavorable → minimal / no size.
        self.assertEqual(dp["position_sizing"]["band_pct"][1], 0.5)

    def test_favorable_cheap(self):
        # price 100, bull 200 (+100%), base 150 (+50%), bear 90 (-10%).
        r = _Result(100.0, 200.0, 150.0, 90.0)
        dp = build_downside_protection(_Calc(), r, conviction_tier="high_conviction")
        self.assertEqual(dp["asymmetry_verdict"], "favorable")   # 0.5/0.1 = 5x
        self.assertEqual(dp["mos_verdict"], "meets_strong")      # 50% >= 30%
        self.assertGreaterEqual(dp["position_sizing"]["band_pct"][1], 6.0)

    def test_sector_derating_added_to_ladder(self):
        r = _Result(100.0, 130.0, 110.0, 80.0)
        md = {"market_ev_ebitda": 20.0, "sector_median_ev_ebitda": 12.0}
        dp = build_downside_protection(_Calc(), r, multiple_decomposition=md)
        names = [e["name"] for e in dp["downside_scenarios"]]
        self.assertIn("Sector-median de-rating", names)
        # de-rate price = 100 * 12/20 = 60 → -40% worst case beats bear -20%.
        self.assertAlmostEqual(dp["worst_case_pct"], -0.40, places=4)

    def test_no_price_unavailable(self):
        r = _Result(0.0, 1, 1, 1)
        self.assertFalse(build_downside_protection(_Calc(), r)["available"])

    def test_required_mos_from_lifecycle(self):
        r = _Result(100.0, 120.0, 110.0, 85.0)
        dp = build_downside_protection(_Calc(), r)
        self.assertEqual(dp["required_mos"]["stage"], "mature")
        self.assertIn("mos_good", dp["required_mos"])


if __name__ == "__main__":
    unittest.main()
