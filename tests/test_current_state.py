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


if __name__ == "__main__":
    unittest.main()
