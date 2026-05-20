"""_load_llm_payload — DB precedence + JSON fallback semantics.

Step 3 of the JSON→DB-as-truth migration introduces a unified read for
LLM-authored blocks. The contract:

  1. If `agent_runs_latest` has a row, use that (source="db").
  2. Else if `valuation_data/serving/latest/{T}_report.json` exists, use
     that (source="json"). This is the migration bridge — pre-Step-2
     reports remain readable until each ticker is re-run.
  3. Else return None.

The DB precedence rule matters because once a ticker has been re-run
post-Step-2, both sources exist. Reading from the (potentially stale)
JSON would re-introduce the snapshot drift that motivated the entire
refactor.
"""

from __future__ import annotations

import json
import pytest


@pytest.fixture
def tmp_paths(tmp_path, monkeypatch):
    """Redirect REPORT_DIR + DB to a temp location so tests don't touch
    the production state. Yields (report_dir, db_path)."""
    import api_main
    from aletheia.data.database import InvestmentDatabase

    report_dir = tmp_path / "serving"
    report_dir.mkdir()
    db_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(api_main, "REPORT_DIR", report_dir)
    # Patch the in-process DB factory so InvestmentDatabase() in
    # _load_llm_payload uses the temp file. We do this by clobbering
    # DEFAULT_DB_PATH at the class level.
    monkeypatch.setattr(InvestmentDatabase, "DEFAULT_DB_PATH", str(db_path))

    yield report_dir, db_path


def _legacy_report(ticker: str, narrative: str) -> dict:
    """Minimal legacy report.json shape — only the fields _load_llm_payload
    cares about."""
    return {
        "ticker": ticker,
        "generated_at": "2026-01-01T00:00:00",
        "1_economic_reality": {"moat": {"score": 7.0, "marker": "from-json"}},
        "4_valuation_synthesis": {
            "contrarian_analysis": {"sentiment_score": -1, "marker": "from-json"},
            "investment_thesis":   {"narrative": narrative, "marker": "from-json"},
            "agent_scenarios":     [{"name": "json-scenario"}],
        },
    }


def _db_payload(narrative: str) -> dict:
    return {
        "economic_reality":    {"moat": {"score": 8.5, "marker": "from-db"}},
        "contrarian_analysis": {"sentiment_score": -3, "marker": "from-db"},
        "investment_thesis":   {"narrative": narrative, "marker": "from-db"},
        "agent_scenarios":     [{"name": "db-scenario"}],
    }


class TestPrecedence:

    def test_returns_none_when_neither_source_has_data(self, tmp_paths):
        from api_main import _load_llm_payload
        assert _load_llm_payload("NEVER_INGESTED") is None

    def test_json_only_returns_json_marker(self, tmp_paths):
        report_dir, _ = tmp_paths
        from api_main import _load_llm_payload
        (report_dir / "AAPL_report.json").write_text(
            json.dumps(_legacy_report("AAPL", "json narrative")))
        payload = _load_llm_payload("AAPL")
        assert payload is not None
        assert payload["source"] == "json"
        assert payload["investment_thesis"]["marker"] == "from-json"
        assert payload["investment_thesis"]["narrative"] == "json narrative"

    def test_db_only_returns_db_marker(self, tmp_paths):
        from aletheia.data.database import InvestmentDatabase
        from api_main import _load_llm_payload
        db = InvestmentDatabase(verbose=False)
        db.upsert_agent_run("NVDA", 2025, _db_payload("db narrative"))
        db.close()

        payload = _load_llm_payload("NVDA")
        assert payload is not None
        assert payload["source"] == "db"
        assert payload["investment_thesis"]["marker"] == "from-db"
        assert payload["investment_thesis"]["narrative"] == "db narrative"

    def test_db_takes_precedence_over_json(self, tmp_paths):
        """When both sources exist, DB wins. This is the rule that
        prevents stale-JSON drift after a ticker has been re-run."""
        report_dir, _ = tmp_paths
        from aletheia.data.database import InvestmentDatabase
        from api_main import _load_llm_payload

        # Legacy JSON: pre-Step-2 thesis
        (report_dir / "MSFT_report.json").write_text(
            json.dumps(_legacy_report("MSFT", "OLD pre-Step-2 thesis")))

        # DB: post-Step-2 re-run
        db = InvestmentDatabase(verbose=False)
        db.upsert_agent_run("MSFT", 2025, _db_payload("NEW post-Step-2 thesis"))
        db.close()

        payload = _load_llm_payload("MSFT")
        assert payload["source"] == "db"
        assert payload["investment_thesis"]["marker"] == "from-db"
        assert payload["investment_thesis"]["narrative"] == "NEW post-Step-2 thesis"
        assert "pre-Step-2" not in str(payload)

    def test_db_failure_falls_through_to_json(self, tmp_paths, monkeypatch):
        """If the DB read raises (corruption, locked, etc.), the JSON
        fallback should still serve a response — we don't lose data
        because of a transient DB issue."""
        report_dir, _ = tmp_paths
        from api_main import _load_llm_payload
        (report_dir / "GOOGL_report.json").write_text(
            json.dumps(_legacy_report("GOOGL", "fallback narrative")))

        # Force DB read to error
        from aletheia.data.database import InvestmentDatabase
        original = InvestmentDatabase.get_latest_agent_run

        def boom(self, ticker):
            raise RuntimeError("simulated DB failure")
        monkeypatch.setattr(InvestmentDatabase, "get_latest_agent_run", boom)

        try:
            payload = _load_llm_payload("GOOGL")
            assert payload is not None
            assert payload["source"] == "json"
            assert payload["investment_thesis"]["narrative"] == "fallback narrative"
        finally:
            monkeypatch.setattr(InvestmentDatabase, "get_latest_agent_run", original)

    def test_db_payload_carries_metadata(self, tmp_paths):
        """`source`, `version`, `git_sha`, `generated_at` are all
        propagated to the payload — the API uses these for the
        X-Aletheia-Source header and audit trails."""
        from aletheia.data.database import InvestmentDatabase
        from api_main import _load_llm_payload
        db = InvestmentDatabase(verbose=False)
        db.upsert_agent_run("TSLA", 2025, _db_payload("v1"), git_sha="abc123")
        db.upsert_agent_run("TSLA", 2025, _db_payload("v2"), git_sha="def456")
        db.close()

        payload = _load_llm_payload("TSLA")
        assert payload["source"] == "db"
        assert payload["version"] == 2
        assert payload["git_sha"] == "def456"
        assert payload["generated_at"] is not None
