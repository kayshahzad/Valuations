"""agent_runs table — versioning + write guardrails.

Step 2 of the JSON-as-truth → DB-as-truth migration introduces a versioned
DB store for LLM-authored agent payloads. This test pins the contract:

  1. Multiple writes to the same ticker create monotonically increasing
     versions, never overwriting prior rows.
  2. Each ticker has its own version sequence.
  3. The `agent_runs_latest` view returns exactly one row per ticker.
  4. The writer rejects any payload key that isn't on the LLM-authored
     whitelist — this is the structural guardrail that prevents future
     callers from accidentally re-introducing JSON-as-truth duplication
     by caching deterministic fields like `clean_financials` or
     `phase2_valuation` in the DB.
  5. Round-trip: written JSON → read back as dict.

Tests use a tmp-path DuckDB so they don't pollute the production DB.
"""

from __future__ import annotations

import json
import pytest

from aletheia.data.database import InvestmentDatabase


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.duckdb"
    db = InvestmentDatabase(db_path=str(path), verbose=False)
    yield db
    db.close()


# Minimal valid LLM payload — every test starts from this and mutates.
def _payload(narrative: str = "v1 thesis"):
    return {
        "economic_reality":    {"moat": {"score": 8.0}},
        "contrarian_analysis": {"sentiment_score": -2},
        "investment_thesis":   {"narrative": narrative, "conviction_score": 5},
        "agent_scenarios":     [{"name": "base"}],
    }


class TestVersioning:

    def test_first_write_returns_version_1(self, db):
        v = db.upsert_agent_run("AAPL", 2025, _payload(), git_sha="abc123")
        assert v == 1

    def test_second_write_returns_version_2(self, db):
        db.upsert_agent_run("AAPL", 2025, _payload("v1"))
        v = db.upsert_agent_run("AAPL", 2025, _payload("v2"))
        assert v == 2

    def test_versions_are_per_ticker(self, db):
        """Versions count up independently per ticker — writing AAPL twice
        and NVDA once must give NVDA version 1, not 3."""
        db.upsert_agent_run("AAPL", 2025, _payload())
        db.upsert_agent_run("AAPL", 2025, _payload())
        v_nvda = db.upsert_agent_run("NVDA", 2025, _payload())
        assert v_nvda == 1

    def test_history_is_preserved(self, db):
        """Older versions stay in `agent_runs` — never overwritten."""
        db.upsert_agent_run("AAPL", 2025, _payload("v1"))
        db.upsert_agent_run("AAPL", 2025, _payload("v2"))
        db.upsert_agent_run("AAPL", 2025, _payload("v3"))
        rows = db.query("SELECT version FROM agent_runs WHERE ticker='AAPL' ORDER BY version")
        assert list(rows["version"]) == [1, 2, 3]

    def test_agent_runs_latest_view_returns_single_row(self, db):
        """The view should expose exactly the most recent version per
        ticker — that's the contract Step 3 will rely on for read paths."""
        db.upsert_agent_run("AAPL", 2025, _payload("v1"))
        db.upsert_agent_run("AAPL", 2025, _payload("v2"))
        db.upsert_agent_run("AAPL", 2025, _payload("v3"))
        rows = db.query("SELECT * FROM agent_runs_latest WHERE ticker='AAPL'")
        assert len(rows) == 1
        assert int(rows.iloc[0]["version"]) == 3


class TestPayloadGuardrail:
    """The whitelist that prevents future callers from leaking
    deterministic content into agent_runs. Without this guardrail the
    Step 1 architectural fix would slowly erode as developers added
    'just one more field for convenience'."""

    def test_rejects_clean_financials(self, db):
        """The single most likely accidental smuggling — caching the
        already-deterministic financial translation."""
        bad = _payload()
        bad["clean_financials"] = {"revenue_bn": 416.1}
        with pytest.raises(ValueError, match="deterministic-field keys"):
            db.upsert_agent_run("AAPL", 2025, bad)

    def test_rejects_phase2_valuation(self, db):
        """The other obvious one — caching DCF scenarios alongside the
        thesis. Step 1 specifically removed this duplication."""
        bad = _payload()
        bad["phase2_valuation"] = {"three_scenario_dcf": {}}
        with pytest.raises(ValueError, match="deterministic-field keys"):
            db.upsert_agent_run("AAPL", 2025, bad)

    def test_rejects_capital_stack(self, db):
        bad = _payload()
        bad["capital_stack"] = {"wacc": 0.085}
        with pytest.raises(ValueError, match="deterministic-field keys"):
            db.upsert_agent_run("AAPL", 2025, bad)

    def test_rejects_arbitrary_unknown_key(self, db):
        bad = _payload()
        bad["something_made_up"] = "value"
        with pytest.raises(ValueError, match="deterministic-field keys"):
            db.upsert_agent_run("AAPL", 2025, bad)

    def test_partial_payload_is_allowed(self, db):
        """Not every run produces every block (e.g. failure before
        contrarian). Missing keys are stored as NULL, not rejected."""
        v = db.upsert_agent_run("AAPL", 2025, {"investment_thesis": {"narrative": "x"}})
        assert v == 1
        row = db.get_latest_agent_run("AAPL")
        assert row["investment_thesis"] == {"narrative": "x"}
        assert row["economic_reality"] is None


class TestRoundTrip:

    def test_payload_decodes_back_to_original(self, db):
        original = _payload("round trip")
        original["agent_scenarios"] = [
            {"name": "bull", "intrinsic_per_share": 999.0},
            {"name": "bear", "intrinsic_per_share": 100.0},
        ]
        db.upsert_agent_run("AAPL", 2025, original, git_sha="deadbeef")
        row = db.get_latest_agent_run("AAPL")
        assert row["economic_reality"]    == original["economic_reality"]
        assert row["contrarian_analysis"] == original["contrarian_analysis"]
        assert row["investment_thesis"]   == original["investment_thesis"]
        assert row["agent_scenarios"]     == original["agent_scenarios"]
        assert row["git_sha"]             == "deadbeef"
        assert row["fiscal_year"]         == 2025
        assert row["version"]             == 1

    def test_get_latest_returns_none_for_unknown_ticker(self, db):
        assert db.get_latest_agent_run("NEVER_INGESTED") is None

    def test_get_latest_returns_most_recent_version(self, db):
        db.upsert_agent_run("AAPL", 2025, _payload("first"))
        db.upsert_agent_run("AAPL", 2025, _payload("second"))
        row = db.get_latest_agent_run("AAPL")
        assert row["investment_thesis"]["narrative"] == "second"
        assert row["version"] == 2
