"""Discount-rate detail (memo §7).

Covers the deterministic math: IV(w) re-discount reproduces the engine EV at the
base WACC, the sensitivity table moves the right direction, implied WACC solves
IV(w)=price, and the build-up premia + quality score behave. The DCF result is
stubbed so the test is pure.

Run: python -m pytest tests/test_wacc_analysis.py -v
"""

import unittest

from aletheia.tools.wacc_analysis import (
    build_wacc_analysis, _iv_at_wacc, _size_premium, _country_premium,
)


class _Assump:
    def __init__(self, wacc, g):
        self.wacc = wacc
        self.terminal_growth = g


class _Proj:
    def __init__(self, year, fcff):
        self.year = year
        self.fcff = fcff


class _Terminal:
    def __init__(self, tv_used):
        self.tv_used = tv_used


class _Base:
    def __init__(self, wacc, g, fcffs, tv_used):
        self.assumptions = _Assump(wacc, g)
        self.projections = [_Proj(i + 1, f) for i, f in enumerate(fcffs)]
        self.terminal = _Terminal(tv_used)


class _Result:
    def __init__(self, price, wacc, g, fcffs, tv_used, net_debt=0.0,
                 shares=1.0, market_cap=20e9, beta=1.1, rf=0.04, ebitda=1e9, roic=0.15):
        self.current_price = price
        self.wacc_base = wacc
        self.risk_free_rate = rf
        self.beta = beta
        self.net_debt = net_debt
        self.market_cap = market_cap
        self.ebitda = ebitda
        self.roic = roic
        self._shares = shares
        self.base = _Base(wacc, g, fcffs, tv_used)

    def intrinsic_per_share(self, ev, net_debt):
        return (ev - net_debt) / self._shares


def _engine_ev(result):
    """EV the way the tool should reproduce at base WACC."""
    b = result.base
    w, g, n = b.assumptions.wacc, b.assumptions.terminal_growth, b.projections[-1].year
    pv = sum(p.fcff / (1 + w) ** p.year for p in b.projections)
    pv_tv = b.terminal.tv_used / (1 + w) ** n
    return pv + pv_tv


class TestWaccAnalysis(unittest.TestCase):

    def _mk(self):
        # 5-year FCFF ramp + a terminal value, shares=1 so IV == equity value.
        return _Result(price=100.0, wacc=0.09, g=0.025,
                       fcffs=[10, 11, 12, 13, 14], tv_used=250.0)

    def test_iv_at_base_reproduces_engine_ev(self):
        r = self._mk()
        iv = _iv_at_wacc(r, r.wacc_base)
        self.assertAlmostEqual(iv, _engine_ev(r), places=4)

    def test_sensitivity_monotonic(self):
        r = self._mk()
        wa = build_wacc_analysis(r, country="US")
        self.assertTrue(wa["available"])
        ivs = [s["iv"] for s in wa["sensitivity"]]
        # Lower WACC (earlier rows) → higher IV; strictly decreasing.
        self.assertEqual(ivs, sorted(ivs, reverse=True))
        base_row = next(s for s in wa["sensitivity"] if s["is_base"])
        self.assertAlmostEqual(base_row["iv"], _engine_ev(r), places=4)

    def test_implied_wacc_solves(self):
        r = self._mk()
        wa = build_wacc_analysis(r, country="US")
        iw = wa["implied_wacc"]
        self.assertIsNotNone(iw)
        # IV at the implied WACC should equal the price.
        self.assertAlmostEqual(_iv_at_wacc(r, iw), r.current_price, places=1)

    def test_premia_and_adjusted_wacc(self):
        # Small-cap EM, high leverage → non-zero premia.
        r = _Result(price=20.0, wacc=0.10, g=0.02, fcffs=[1, 1.1, 1.2, 1.3, 1.4],
                    tv_used=20.0, market_cap=1.0e9, ebitda=0.2e9, net_debt=1.4e9, roic=0.05)
        wa = build_wacc_analysis(r, country="BR")
        self.assertGreater(wa["premia"]["size"], 0)           # small cap
        self.assertGreater(wa["premia"]["country"], 0)        # Brazil
        self.assertGreater(wa["premia"]["idiosyncratic"], 0)  # lev>6 + ROIC<WACC
        self.assertGreater(wa["adjusted_wacc"], wa["components"]["wacc_base"])
        # Higher adjusted WACC → lower IV than base.
        self.assertLess(wa["iv_at_adjusted_wacc"], wa["iv_base"])

    def test_premium_tables(self):
        self.assertEqual(_size_premium(50e9), 0.0)
        self.assertGreater(_size_premium(0.3e9), 0.0)
        self.assertEqual(_country_premium("US"), 0.0)
        self.assertGreater(_country_premium("BR"), 0.0)

    def test_no_base_unavailable(self):
        class Empty:
            base = None
        self.assertFalse(build_wacc_analysis(Empty())["available"])


if __name__ == "__main__":
    unittest.main()
