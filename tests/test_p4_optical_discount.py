"""P4 (Margin of Safety) ⇄ reverse-DCF reconciliation.

A large raw MoS is not a real margin of safety when the base-DCF IV rests
on growth the market already prices in. P4 must reconcile with the
reverse-DCF signal (the canonical implied-growth verdict) and the
constitution implied/historical bands — it previously printed "fortress
margin of safety" even when the reverse DCF said CAUTION/FLAG, contradicting
the contrarian agent on the same page (the ET bug).

Caps: flag→2, caution→3, priced_for_growth→4, fair/deep_value→no cap.
Fallback (no signal): implied/historical >2.0→2, >1.3→3.

Run: python -m pytest tests/test_p4_optical_discount.py -v
"""

import unittest

from aletheia.tools.conviction_scorer import ConvictionScorer
from aletheia.utils.calc_input_builder import make_calc_input


class TestP4ReverseDcfReconciliation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sc = ConvictionScorer()
        cls.ci = make_calc_input("ET")

    def _p4(self, base_mos, signal=None, implied=None, hist=None):
        r = self.sc._compute(
            ticker="ET", moat_score=5.0, roic=0.09, wacc=0.09, fcf_margin=20.0,
            net_debt_bn=50.0, ebitda_bn=15.0, data_quality=0.95, rev_cagr=0.052,
            hist_cagr=hist, sector="energy", cyclicality_z=None, is_peak=False,
            base_mos=base_mos, sbc_pct_fcf=2.0, op_leverage=1.0, upstream_leak=None,
            strategic_lev=3.0, multiple_premium=None, implied_cagr=implied,
            calc_input=self.ci, reverse_dcf_signal=signal,
        )
        return r.p4_mos.score, r.p4_mos.reasons

    # ── Signal-driven caps (issue 1 & 2) ────────────────────────────────
    def test_caution_caps_at_3_no_fortress(self):
        score, reasons = self._p4(0.528, signal="caution")
        self.assertEqual(score, 3)
        self.assertTrue(any("caution" in r.lower() for r in reasons))
        self.assertFalse(any("fortress" in r.lower() for r in reasons))

    def test_flag_caps_at_2(self):
        self.assertEqual(self._p4(0.528, signal="flag")[0], 2)

    def test_priced_for_growth_caps_at_4(self):
        self.assertEqual(self._p4(0.528, signal="priced_for_growth")[0], 4)

    def test_deep_value_keeps_fortress(self):
        score, reasons = self._p4(0.528, signal="deep_value")
        self.assertEqual(score, 5)
        self.assertTrue(any("fortress" in r.lower() for r in reasons))

    def test_fair_value_no_cap(self):
        self.assertEqual(self._p4(0.528, signal="fair_value")[0], 5)

    # ── Ratio fallback when signal absent (constitution alignment) ──────
    def test_ratio_fail_band_caps_at_2(self):
        # 0.115 / 0.052 = 2.2× (> 2.0 FAIL)
        self.assertEqual(self._p4(0.528, implied=0.115, hist=0.052)[0], 2)

    def test_ratio_caution_band_caps_at_3(self):
        # 0.078 / 0.052 = 1.5× (> 1.3 CAUTION)
        self.assertEqual(self._p4(0.528, implied=0.078, hist=0.052)[0], 3)

    def test_no_signal_no_ratio_keeps_raw(self):
        self.assertEqual(self._p4(0.528)[0], 5)


if __name__ == "__main__":
    unittest.main()
