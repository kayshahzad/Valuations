"""End-to-end report assembly for pending tickers.

ACN is the canonical pending-ticker case: classified as fcff_compatible,
ingested into the DB with 16 years of cleaned data, but never had its
LLM agent run. Before Step 3, `/ticker/ACN` returned 404 because
`_load_report` couldn't find the JSON. After Step 3 it returns 200 with
deterministic blocks populated and LLM blocks empty — this is what
unblocks the Deep Dive UI for pending tickers.

This test pins that contract: a pending ticker through `/ticker/{T}`
must yield a report-shaped dict where Sections 2, 3.capital_stack, and
4.phase2_valuation are populated, and Sections 1, 4.contrarian,
4.investment_thesis are empty (UI degrades).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from api_main import app
    return TestClient(app)


@pytest.mark.skipif(
    not __import__("pathlib").Path("valuation_data/database/investment.duckdb").exists(),
    reason="DuckDB not present",
)
class TestFullReportPending:

    def test_acn_returns_200_not_404(self, client):
        """The bug we're fixing — pending tickers must not 404 on the
        full-report endpoint."""
        r = client.get("/ticker/ACN")
        assert r.status_code == 200, f"got {r.status_code}: {r.text[:200]}"

    def test_acn_has_deterministic_blocks_populated(self, client):
        """Sections 2 and 4.phase2_valuation must populate from the DB
        and live DCF compute, even without an agent run."""
        d = client.get("/ticker/ACN").json()

        # Section 2 — financial translation (from DB)
        clean = d["2_financial_translation"]["clean_financials"]
        assert clean["revenue_bn"] is not None and clean["revenue_bn"] > 0
        assert clean["fiscal_year"] == 2025

        # Section 4.phase2_valuation — live DCF
        p2 = d["4_valuation_synthesis"]["phase2_valuation"]
        assert p2["wacc"] is not None
        assert p2["three_scenario_dcf"]["base"]["intrinsic_per_share"] is not None
        # Bull > base > bear must hold (Step 1 invariant carries through)
        bull = p2["three_scenario_dcf"]["bull"]["intrinsic_per_share"]
        base = p2["three_scenario_dcf"]["base"]["intrinsic_per_share"]
        bear = p2["three_scenario_dcf"]["bear"]["intrinsic_per_share"]
        assert bull > base > bear

    def test_acn_has_llm_blocks_empty_and_marked(self, client):
        """LLM blocks should be empty dicts (not missing keys) so the UI's
        `(d or {}).get(...)` cascades behave predictably. `agent_run`
        should be None to signal pending status."""
        d = client.get("/ticker/ACN").json()

        # Empty dicts, not missing keys
        assert d["1_economic_reality"] == {}
        synthesis = d["4_valuation_synthesis"]
        assert synthesis["contrarian_analysis"] == {}
        assert synthesis["investment_thesis"]   == {}
        assert synthesis["agent_scenarios"]     == []

        # Pending marker
        assert d["agent_run"] is None

    def test_aapl_returns_populated_llm_blocks(self, client):
        """AAPL is a 'ready' ticker — Section 1 and Section 4 LLM blocks
        must be populated regardless of which source resolves first.

        The DB-first read priority means AAPL resolves to source='db'
        once an agent_runs row exists; before then, source='json'
        (legacy fallback). Both are acceptable resolved states. The
        invariant under test is that the LLM blocks are NEVER empty
        for a ticker that has been through the agent pipeline."""
        d = client.get("/ticker/AAPL").json()

        # LLM blocks must populate regardless of source
        assert d["1_economic_reality"], "Section 1 should have moat/value_chain/etc"
        assert d["1_economic_reality"].get("moat", {}).get("score") is not None

        # Source marker must be one of the known resolution paths
        agent_run = d["agent_run"] or {}
        assert agent_run.get("source") in ("db", "json"), (
            f"unexpected source: {agent_run.get('source')!r}"
        )

    def test_universe_summary_unifies_ready_and_pending(self, client):
        """After Step 3, both ready and pending tickers go through
        `_calc_only_summary`. Both should appear in the universe with
        non-null DCF fields; only `agents_status` differs."""
        d = client.get("/universe").json()
        by_ticker = {row["ticker"]: row for row in d["ranked"]}

        if "ACN" in by_ticker:
            acn = by_ticker["ACN"]
            assert acn["agents_status"] == "pending"
            assert acn["base_iv"] is not None
            assert acn["conviction"] is None  # no LLM yet

        if "AAPL" in by_ticker:
            aapl = by_ticker["AAPL"]
            assert aapl["agents_status"] == "ready"  # JSON fallback resolved
            assert aapl["base_iv"] is not None
            assert aapl["conviction"] is not None

    def test_economic_reality_endpoint_404s_for_pending(self, client):
        """ACN has no LLM payload; /economic_reality is LLM-only and
        should 404 (not return empty)."""
        r = client.get("/ticker/ACN/economic_reality")
        assert r.status_code == 404

    def test_economic_reality_endpoint_200_for_ready(self, client):
        """AAPL via JSON fallback should resolve to 200."""
        r = client.get("/ticker/AAPL/economic_reality")
        assert r.status_code == 200
        assert r.headers.get("x-aletheia-source") in ("db", "json")

    def test_narrative_endpoint_404s_for_pending(self, client):
        r = client.get("/ticker/ACN/narrative")
        assert r.status_code == 404

    def test_narrative_endpoint_200_for_ready(self, client):
        r = client.get("/ticker/AAPL/narrative")
        assert r.status_code == 200
        body = r.json()
        assert "narrative" in body
        assert "conviction" in body
