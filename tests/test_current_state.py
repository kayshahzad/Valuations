"""Current-State Awareness Layer (Phase 1.5).

Covers the deterministic pieces: consensus-vs-engine reconciliation severity
(asymmetric — engine optimism is the dangerous direction), event-driven flags,
the 6th pillar score, and the events-agent JSON parser. The grounded LLM fetch
itself is network/LLM and not unit-tested here.

Run: python -m pytest tests/test_current_state.py -v
"""

import unittest

from aletheia.agents.current_state import build_current_state, HIGH, MEDIUM, LOW
from aletheia.agents.current_state_events import _parse_events


def _cs(engine_y1, consensus_y1, events=None):
    """build_current_state with a stubbed consensus (monkeypatch the fetch)."""
    import aletheia.agents.current_state as m
    orig = m._consensus_forward_growth
    m._consensus_forward_growth = lambda *a, **k: {
        "available": True, "y1_growth": consensus_y1, "anchor_year": 2025,
        "source": "test",
    }
    m._microstructure = lambda *a, **k: {}
    try:
        return build_current_state("TST", engine_y1_growth=engine_y1,
                                   latest_fy=2025, events=events)
    finally:
        m._consensus_forward_growth = orig


class TestConsensusReconciliation(unittest.TestCase):

    def test_nvo_case_engine_optimistic_is_high(self):
        # Engine +11.1% vs consensus -4.1% (the NVO disconnect).
        r = _cs(0.111, -0.041)
        self.assertEqual(r.max_severity, HIGH)
        self.assertTrue(any(f.category == "growth_vs_consensus" and f.severity == HIGH
                            for f in r.flags))

    def test_engine_conservative_is_capped_at_medium(self):
        # Engine 8% below consensus 15% — informative, not alarming.
        r = _cs(0.08, 0.15)
        sev = next(f.severity for f in r.flags if f.category == "growth_vs_consensus")
        self.assertEqual(sev, MEDIUM)

    def test_small_conservative_gap_is_low(self):
        r = _cs(0.13, 0.178)   # 4.8pp below
        sev = next(f.severity for f in r.flags if f.category == "growth_vs_consensus")
        self.assertEqual(sev, LOW)

    def test_aligned_no_flag(self):
        r = _cs(0.10, 0.099)   # ~aligned
        self.assertFalse(any(f.category == "growth_vs_consensus" for f in r.flags))
        self.assertEqual(r.pillar_score, 5)


class TestEventFlags(unittest.TestCase):

    def test_material_adverse_events_raise_flags(self):
        events = [
            {"date": "2026-02-23", "category": "clinical_failure",
             "headline": "CagriSema misses", "materiality": 5, "source": "FT"},
            {"date": "2026-02-04", "category": "guidance_cut",
             "headline": "-13% 2026 sales", "materiality": 5, "source": "FT"},
        ]
        r = _cs(0.05, 0.05, events=events)   # growth aligned; events drive it
        self.assertEqual(r.max_severity, HIGH)
        self.assertGreaterEqual(sum(1 for f in r.flags if f.severity == HIGH), 2)
        self.assertEqual(r.pillar_score, 1)   # multiple HIGH → severe

    def test_low_materiality_event_ignored(self):
        events = [{"date": "2026-01-01", "category": "management",
                   "headline": "minor", "materiality": 2, "source": "x"}]
        r = _cs(0.10, 0.10, events=events)
        self.assertEqual(r.max_severity, "NONE")


class TestPillarScore(unittest.TestCase):

    def test_two_high_is_one(self):
        events = [{"date": "d", "category": "guidance_cut", "headline": "h",
                   "materiality": 5, "source": "s"}]
        self.assertEqual(_cs(0.30, -0.04, events=events).pillar_score, 1)  # consensus HIGH + event HIGH

    def test_single_medium_is_four(self):
        self.assertEqual(_cs(0.08, 0.15).pillar_score, 4)


class TestEventParser(unittest.TestCase):

    def test_parses_and_filters(self):
        text = '''Here are events:
        [{"date":"2026-02-23","category":"clinical_failure","headline":"CagriSema misses","materiality":5,"source":"FT","impact":"growth"},
         {"date":"2026-01-01","category":"routine","headline":"earnings call","materiality":4,"source":"X"},
         {"date":"2026-02-04","category":"guidance_cut","headline":"cut","materiality":2,"source":"FT"}]'''
        evs = _parse_events(text)
        # only the clinical_failure survives (routine = unknown cat; guidance_cut mat<3)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["category"], "clinical_failure")

    def test_empty_and_garbage(self):
        self.assertEqual(_parse_events(""), [])
        self.assertEqual(_parse_events("no json here"), [])
        self.assertEqual(_parse_events("[]"), [])


class TestFlagKeys(unittest.TestCase):
    """Flag keys must be stable so an acknowledgment persists across reruns."""

    def test_consensus_flag_key_is_stable(self):
        r1 = _cs(0.111, -0.041)
        r2 = _cs(0.111, -0.041)
        k1 = next(f.key for f in r1.flags if f.category == "growth_vs_consensus")
        k2 = next(f.key for f in r2.flags if f.category == "growth_vs_consensus")
        self.assertEqual(k1, "growth_vs_consensus")
        self.assertEqual(k1, k2)

    def test_event_flag_key_derives_from_headline(self):
        events = [{"date": "2026-02-23", "category": "clinical_failure",
                   "headline": "CagriSema misses", "materiality": 5,
                   "direction": "adverse", "source": "FT"}]
        r = _cs(0.05, 0.05, events=events)
        f = next(f for f in r.flags if f.category == "clinical_failure")
        self.assertEqual(f.key, "clinical_failure:cagrisema-misses")
        # Same event → same key across runs.
        r2 = _cs(0.05, 0.05, events=events)
        f2 = next(f for f in r2.flags if f.category == "clinical_failure")
        self.assertEqual(f.key, f2.key)


def _annotate(cs, acks):
    """Pure re-implementation mirror of the API/financials ack annotation."""
    rank = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    unresolved_rank, unresolved_high = 0, 0
    for f in cs["flags"]:
        ack = acks.get(f.get("key", ""))
        resolved = bool(ack) and ack["decision"] != "needs_analysis"
        f["acknowledged"] = resolved
        if not resolved:
            unresolved_rank = max(unresolved_rank, rank.get(f.get("severity"), 0))
            if f.get("severity") == "HIGH":
                unresolved_high += 1
    cs["unresolved_severity"] = next(s for s, r in rank.items() if r == unresolved_rank)
    cs["unresolved_high"] = unresolved_high
    return cs


class TestAckResolution(unittest.TestCase):
    """Acknowledging every HIGH flag must clear the gate; needs_analysis must not."""

    def test_unresolved_high_until_acked(self):
        cs = _cs(0.111, -0.041).to_dict()
        cs = _annotate(cs, {})
        self.assertEqual(cs["unresolved_severity"], "HIGH")
        self.assertEqual(cs["unresolved_high"], 1)

    def test_clearing_decision_resolves_gate(self):
        cs = _cs(0.111, -0.041).to_dict()
        cs = _annotate(cs, {"growth_vs_consensus": {"decision": "override_applied"}})
        self.assertEqual(cs["unresolved_high"], 0)
        self.assertEqual(cs["unresolved_severity"], "NONE")

    def test_needs_analysis_does_not_clear(self):
        cs = _cs(0.111, -0.041).to_dict()
        cs = _annotate(cs, {"growth_vs_consensus": {"decision": "needs_analysis"}})
        self.assertEqual(cs["unresolved_high"], 1)
        self.assertEqual(cs["unresolved_severity"], "HIGH")


class TestAckPersistence(unittest.TestCase):
    """Round-trip the ack DB methods on an in-memory DuckDB."""

    def _db(self):
        from aletheia.data.database import InvestmentDatabase
        return InvestmentDatabase(db_path=":memory:", verbose=False)

    def test_upsert_get_clear(self):
        db = self._db()
        try:
            self.assertEqual(db.get_flag_acks("TST"), {})
            db.upsert_flag_ack("tst", "growth_vs_consensus",
                               decision="override_applied", rationale="cut to consensus",
                               category="growth_vs_consensus", severity="HIGH")
            acks = db.get_flag_acks("TST")
            self.assertIn("growth_vs_consensus", acks)
            self.assertEqual(acks["growth_vs_consensus"]["decision"], "override_applied")
            self.assertEqual(acks["growth_vs_consensus"]["rationale"], "cut to consensus")
            # Upsert replaces (no duplicate rows).
            db.upsert_flag_ack("TST", "growth_vs_consensus", decision="rejected",
                               rationale="disputed")
            acks = db.get_flag_acks("TST")
            self.assertEqual(len(acks), 1)
            self.assertEqual(acks["growth_vs_consensus"]["decision"], "rejected")
            # Clear one.
            db.clear_flag_ack("TST", "growth_vs_consensus")
            self.assertEqual(db.get_flag_acks("TST"), {})
        finally:
            db.close()


class TestSectorRelativeValuation(unittest.TestCase):
    """Phase B: reuse MultipleDecomposition's market vs sector-median EV/EBITDA."""

    def test_rich_cheap_inline(self):
        from aletheia.agents.current_state import _sector_relative_valuation
        rich = _sector_relative_valuation(
            {"market_ev_ebitda": 18.0, "sector_median_ev_ebitda": 12.0,
             "vs_sector_premium": 6.0, "sector": "Tech"})
        self.assertTrue(rich["available"])
        self.assertEqual(rich["label"], "rich vs sector")
        self.assertAlmostEqual(rich["premium_pct"], 0.5, places=3)
        cheap = _sector_relative_valuation(
            {"market_ev_ebitda": 8.0, "sector_median_ev_ebitda": 12.0})
        self.assertEqual(cheap["label"], "cheap vs sector")
        inline = _sector_relative_valuation(
            {"market_ev_ebitda": 12.5, "sector_median_ev_ebitda": 12.0})
        self.assertEqual(inline["label"], "in line with sector")

    def test_missing_or_bad_data_unavailable(self):
        from aletheia.agents.current_state import _sector_relative_valuation
        self.assertFalse(_sector_relative_valuation(None)["available"])
        self.assertFalse(_sector_relative_valuation({})["available"])
        self.assertFalse(_sector_relative_valuation(
            {"market_ev_ebitda": 0, "sector_median_ev_ebitda": 12})["available"])


class TestPolicyRegulatoryContext(unittest.TestCase):
    """Phase A: reuse cached regulatory events + regulatory_exposure dimension."""

    def test_filters_to_regulatory_events_only(self):
        from aletheia.agents.current_state import _policy_regulatory_context
        events = [
            {"category": "pricing_regulatory", "direction": "adverse",
             "headline": "MFN", "materiality": 5},
            {"category": "regulatory_legal", "direction": "favorable",
             "headline": "case dismissed", "materiality": 4},
            {"category": "competitive", "direction": "adverse",
             "headline": "rival launch", "materiality": 5},  # excluded
        ]
        pr = _policy_regulatory_context(events, None)
        self.assertTrue(pr["available"])
        self.assertEqual(len(pr["recent_actions"]), 2)  # competitive excluded
        self.assertEqual(pr["net_event_direction"], "mixed")  # 1 adverse, 1 favorable

    def test_exposure_dimension_reused(self):
        from aletheia.agents.current_state import _policy_regulatory_context
        reg = {"score": 6.0, "narrative": "Light touch.",
               "source_payload": {"material_exposures": [
                   {"regulator": "FTC", "area": "antitrust", "severity": "low"}]}}
        pr = _policy_regulatory_context([], reg)
        self.assertTrue(pr["available"])
        self.assertEqual(pr["exposure_label"], "low exposure")  # score>=6
        self.assertEqual(len(pr["material_exposures"]), 1)

    def test_nothing_available(self):
        from aletheia.agents.current_state import _policy_regulatory_context
        self.assertFalse(_policy_regulatory_context([], None)["available"])

    def test_display_only_no_new_flags(self):
        # The reused signals must NOT add flags (display-only decision).
        r = _cs(0.10, 0.099)  # aligned → no consensus flag
        before = len(r.flags)
        # build_current_state with md + reg should not change flag count.
        import aletheia.agents.current_state as m
        orig = m._consensus_forward_growth
        m._consensus_forward_growth = lambda *a, **k: {
            "available": True, "y1_growth": 0.099, "source": "t"}
        m._microstructure = lambda *a, **k: {}
        try:
            r2 = m.build_current_state(
                "TST", engine_y1_growth=0.10, latest_fy=2025,
                multiple_decomposition={"market_ev_ebitda": 18.0,
                                        "sector_median_ev_ebitda": 12.0},
                regulatory_exposure={"score": 3.0})
        finally:
            m._consensus_forward_growth = orig
        self.assertEqual(len(r2.flags), before)
        self.assertTrue(r2.sector_valuation["available"])
        self.assertTrue(r2.policy_regulatory["available"])


class TestMarketSignal(unittest.TestCase):
    """Phase C: 52-week position + momentum/MA interpretation."""

    def test_near_high_priced_for_strength(self):
        from aletheia.agents.current_state import _market_signal
        import aletheia.agents.current_state as m
        # No price history needed — 52w position drives the label.
        import aletheia.data.fmp_client as fmp
        orig = fmp.fetch_historical_prices
        fmp.fetch_historical_prices = lambda *a, **k: []
        try:
            ms = _market_signal("TST", {"price": 100, "pct_below_52w_high": 0.02,
                                        "pct_above_52w_low": 0.8})
        finally:
            fmp.fetch_historical_prices = orig
        self.assertTrue(ms["available"])
        self.assertIn("52-week high", ms["label"])

    def test_unavailable_when_no_data(self):
        from aletheia.agents.current_state import _market_signal
        import aletheia.data.fmp_client as fmp
        orig = fmp.fetch_historical_prices
        fmp.fetch_historical_prices = lambda *a, **k: []
        try:
            ms = _market_signal("TST", {})
        finally:
            fmp.fetch_historical_prices = orig
        self.assertFalse(ms["available"])


class TestAnalystSentiment(unittest.TestCase):
    """Phase D: price target + ratings + recent actions → blended label."""

    def _stub(self, pts, gc, hist):
        import aletheia.data.fmp_client as fmp
        return (fmp, fmp.fetch_price_target_summary,
                fmp.fetch_grades_consensus, fmp.fetch_grades_historical,
                pts, gc, hist)

    def test_bullish_blend(self):
        from aletheia.agents.current_state import _analyst_sentiment
        import aletheia.data.fmp_client as fmp
        o1, o2, o3 = (fmp.fetch_price_target_summary,
                      fmp.fetch_grades_consensus, fmp.fetch_grades_historical)
        fmp.fetch_price_target_summary = lambda *a, **k: {"avgPriceTarget": 130}
        fmp.fetch_grades_consensus = lambda *a, **k: {
            "strongBuy": 10, "buy": 8, "hold": 3, "sell": 1, "strongSell": 0}
        fmp.fetch_grades_historical = lambda *a, **k: [
            {"action": "upgrade"}, {"action": "upgrade"}]
        try:
            s = _analyst_sentiment("TST", current_price=100)
        finally:
            (fmp.fetch_price_target_summary, fmp.fetch_grades_consensus,
             fmp.fetch_grades_historical) = o1, o2, o3
        self.assertTrue(s["available"])
        self.assertAlmostEqual(s["implied_upside"], 0.30, places=4)
        self.assertEqual(s["label"], "bullish")

    def test_unavailable_when_no_endpoints(self):
        from aletheia.agents.current_state import _analyst_sentiment
        import aletheia.data.fmp_client as fmp
        o1, o2, o3 = (fmp.fetch_price_target_summary,
                      fmp.fetch_grades_consensus, fmp.fetch_grades_historical)
        fmp.fetch_price_target_summary = lambda *a, **k: None
        fmp.fetch_grades_consensus = lambda *a, **k: None
        fmp.fetch_grades_historical = lambda *a, **k: None
        try:
            s = _analyst_sentiment("TST", current_price=100)
        finally:
            (fmp.fetch_price_target_summary, fmp.fetch_grades_consensus,
             fmp.fetch_grades_historical) = o1, o2, o3
        self.assertFalse(s["available"])


if __name__ == "__main__":
    unittest.main()


class TestBankReconciliation:
    """CF-R29: bank-native §3 reconciliation (the RI engine has no revenue-growth
    assumption, so reconcile ROE / asset growth / Ke-vs-sector-CoC instead)."""

    def _bvm(self, *, roe_n=0.08, roe_t=0.053, ke=0.105, retention=1.0,
             asset_g=0.15, coc=0.105, sector="Financial Svcs. (Non-bank & Insurance)"):
        return {
            "available": True,
            "inputs": {"roe_normalized": roe_n, "roe_latest": roe_t, "ke": ke,
                       "retention": retention},
            "methods": {"fcfe_bank": {"asset_growth": min(asset_g, roe_n)}},
            "reconciliation": {"normalized_asset_growth": asset_g, "capital_deficit": asset_g > roe_n},
            "ke_band": {"sector_cost_of_capital": coc, "sector_name": sector},
        }

    def test_sofi_like_flags_roe_and_capital_deficit(self):
        from aletheia.agents.current_state import _bank_reconciliation
        rows, flags = _bank_reconciliation(self._bvm())
        assert [r["assumption"] for r in rows] == [
            "Normalized ROE", "Asset growth", "Cost of equity (Ke)"]
        # ROE 8% > trailing 5.3% → MEDIUM; asset growth 15% > fundable 8% → HIGH
        cats = {f.category: f.severity for f in flags}
        assert cats.get("roe_vs_trailing") == "MEDIUM"
        assert cats.get("capital_deficit") == "HIGH"
        # asset-growth row uses the RAW normalized growth, not the FCFE clamp
        ag_row = next(r for r in rows if r["assumption"] == "Asset growth")
        assert ag_row["engine"] == 0.15

    def test_aligned_bank_no_flags(self):
        from aletheia.agents.current_state import _bank_reconciliation
        # ROE ≈ trailing, asset growth self-funded, Ke ≥ sector CoC → no flags
        rows, flags = _bank_reconciliation(
            self._bvm(roe_n=0.163, roe_t=0.162, ke=0.105, retention=0.71,
                      asset_g=0.075, coc=0.105))
        assert len(rows) == 3 and not flags

    def test_non_bank_bvm_yields_nothing(self):
        from aletheia.agents.current_state import _bank_reconciliation
        assert _bank_reconciliation(None) == ([], [])
        assert _bank_reconciliation({"available": False}) == ([], [])
