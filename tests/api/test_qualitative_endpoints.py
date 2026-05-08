"""Qualitative-framework API endpoints — read, write, and category composites.

Five endpoints covered:

  GET  /ticker/{T}/qualitative                 — list all 19 dimensions
  GET  /ticker/{T}/qualitative/{dim}           — one dimension's full state
  POST /ticker/{T}/qualitative/{dim}           — submit HITL assessment
  POST /qualitative/recompute/{T}              — trigger deterministic recompute
  GET  /qualitative/categories/{T}             — category composites

Tests run against a TEMPORARY COPY of the production DuckDB so writes,
inserts, and the per-test `qualitative_assessments` truncate cannot
touch real analyst data. Implementation:

  - module-scope fixture copies the prod DB file → tmp_path
  - patches `InvestmentDatabase.DEFAULT_DB_PATH` for the module's lifetime
  - every endpoint inside `api_main` opens `InvestmentDatabase()` lazily,
    picks up the patched default, and writes only to the tmp copy
  - tmp dir is auto-cleaned by pytest at teardown

This was previously a plain `DELETE FROM qualitative_assessments` against
the live DB, which silently wiped real recompute output (the bug that
caused NVDA's dashboard fingerprint to flip mid-session).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Production DB to clone for the test sandbox. Skip the whole module if
# it doesn't exist (CI without ingested data).
_PROD_DB = Path("valuation_data/database/investment.duckdb")


@pytest.fixture(scope="module", autouse=True)
def _isolate_db_to_tmp_copy(tmp_path_factory):
    """Module-scope: clone the prod DB into a tmp file, repoint
    `InvestmentDatabase.DEFAULT_DB_PATH` at it for the duration, restore
    on teardown. Every endpoint in api_main that does
    `InvestmentDatabase(verbose=False)` will land on the clone.
    """
    if not _PROD_DB.exists():
        pytest.skip("production DuckDB not present")

    from aletheia.data.database import InvestmentDatabase

    tmp_dir = tmp_path_factory.mktemp("qualitative_endpoints_db")
    tmp_db_path = tmp_dir / "investment.duckdb"
    shutil.copy(_PROD_DB, tmp_db_path)

    original = InvestmentDatabase.DEFAULT_DB_PATH
    InvestmentDatabase.DEFAULT_DB_PATH = str(tmp_db_path)
    try:
        yield tmp_db_path
    finally:
        InvestmentDatabase.DEFAULT_DB_PATH = original


@pytest.fixture(scope="module")
def client():
    from api_main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_qualitative_rows():
    """Wipe qualitative_assessments between tests for isolation. Now safe
    because writes land on the tmp DB clone (see `_isolate_db_to_tmp_copy`),
    not the production file."""
    from aletheia.data.database import InvestmentDatabase
    db = InvestmentDatabase(verbose=False)
    db._conn.execute("DELETE FROM qualitative_assessments")
    db.close()
    yield
    db = InvestmentDatabase(verbose=False)
    db._conn.execute("DELETE FROM qualitative_assessments")
    db.close()


@pytest.mark.skipif(
    not __import__("pathlib").Path("valuation_data/database/investment.duckdb").exists(),
    reason="DuckDB not present",
)
class TestList:

    def test_returns_all_19_dimensions(self, client):
        r = client.get("/ticker/AAPL/qualitative")
        assert r.status_code == 200
        data = r.json()
        assert data["ticker"] == "AAPL"
        assert len(data["dimensions"]) == 19

    def test_pending_data_dimensions_have_correct_status(self, client):
        """Management dimensions are PENDING_DATA per the catalog. They
        should always render with status='pending_data', regardless of
        assessment activity, since the data infra isn't wired."""
        r = client.get("/ticker/AAPL/qualitative")
        by_id = {d["dimension_id"]: d for d in r.json()["dimensions"]}
        assert by_id["management_tenure_continuity"]["status"] == "pending_data"
        assert by_id["management_alignment"]["status"] == "pending_data"

    def test_unassessed_dimensions_have_correct_status(self, client):
        """For a fresh DB (no rows), every non-pending dimension reads
        as 'not_assessed'."""
        r = client.get("/ticker/AAPL/qualitative")
        for d in r.json()["dimensions"]:
            if d["source_category"] == "pending_data":
                continue
            assert d["status"] == "not_assessed", f"{d['dimension_id']} expected not_assessed"
            assert d["score"] is None

    def test_list_omits_questions(self, client):
        """Questions are heavy; the list endpoint should not include
        them — that's what the detail endpoint is for."""
        r = client.get("/ticker/AAPL/qualitative")
        for d in r.json()["dimensions"]:
            assert d.get("questions") in (None, [])


@pytest.mark.skipif(
    not __import__("pathlib").Path("valuation_data/database/investment.duckdb").exists(),
    reason="DuckDB not present",
)
class TestDetail:

    def test_hitl_dimension_includes_questions(self, client):
        r = client.get("/ticker/AAPL/qualitative/moat_strength")
        assert r.status_code == 200
        data = r.json()
        assert data["dimension_id"] == "moat_strength"
        assert data["source_category"] == "hitl"
        assert len(data["questions"]) == 5
        # Each question has the required structural fields
        q = data["questions"][0]
        assert "id" in q and "text" in q and "weight" in q

    def test_deterministic_dimension_has_formula_citation_no_questions(self, client):
        r = client.get("/ticker/AAPL/qualitative/roiic_trend")
        data = r.json()
        assert data["source_category"] == "deterministic"
        assert data["formula_citation"]
        assert data["questions"] == []

    def test_unknown_dimension_returns_404(self, client):
        r = client.get("/ticker/AAPL/qualitative/totally_made_up_dimension")
        assert r.status_code == 404

    def test_catalog_hash_present_for_localstorage_keying(self, client):
        """Step 5's draft persistence depends on catalog_hash. Ensure
        it's surfaced from every dimension response."""
        r = client.get("/ticker/AAPL/qualitative/moat_strength")
        data = r.json()
        assert data["catalog_hash"] and len(data["catalog_hash"]) == 16


@pytest.mark.skipif(
    not __import__("pathlib").Path("valuation_data/database/investment.duckdb").exists(),
    reason="DuckDB not present",
)
class TestSubmit:

    def test_valid_submission_returns_composite(self, client):
        body = {
            "sub_scores": {
                "switching_costs": 6,
                "network_effects":  5,
                "cost_advantage":   5,
                "intangibles":      4,
                "efficient_scale":  4,
            },
            "narrative": "Strong moat from ecosystem lock-in.",
        }
        r = client.post("/ticker/AAPL/qualitative/moat_strength", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        # Composite = 6*0.30 + 5*0.25 + 5*0.20 + 4*0.15 + 4*0.10 = 5.05
        assert data["score"] == pytest.approx(5.05, abs=0.01)
        assert data["assessment_id"]
        assert data["assessed_at"]

    def test_submitted_assessment_visible_via_get(self, client):
        body = {"sub_scores": {
            "switching_costs": 6, "network_effects": 5, "cost_advantage": 5,
            "intangibles": 4, "efficient_scale": 4,
        }}
        client.post("/ticker/AAPL/qualitative/moat_strength", json=body)

        r = client.get("/ticker/AAPL/qualitative/moat_strength")
        d = r.json()
        assert d["status"] == "assessed"
        assert d["score"] == pytest.approx(5.05, abs=0.01)
        assert d["sub_scores"]["switching_costs"] == 6

    def test_missing_question_rejected(self, client):
        body = {"sub_scores": {"switching_costs": 6}}   # missing 4 of 5
        r = client.post("/ticker/AAPL/qualitative/moat_strength", json=body)
        assert r.status_code == 422
        assert "missing answers" in r.json()["detail"].lower()

    def test_unknown_question_id_rejected(self, client):
        body = {"sub_scores": {
            "switching_costs": 6, "network_effects": 5, "cost_advantage": 5,
            "intangibles": 4, "efficient_scale": 4,
            "totally_invented_question": 7,
        }}
        r = client.post("/ticker/AAPL/qualitative/moat_strength", json=body)
        assert r.status_code == 422
        assert "unknown question" in r.json()["detail"].lower()

    def test_out_of_range_score_rejected(self, client):
        body = {"sub_scores": {
            "switching_costs": 8,  # out of range
            "network_effects": 5, "cost_advantage": 5,
            "intangibles": 4, "efficient_scale": 4,
        }}
        r = client.post("/ticker/AAPL/qualitative/moat_strength", json=body)
        assert r.status_code == 422
        assert "out of [1, 7]" in r.json()["detail"]

    def test_oversize_narrative_rejected(self, client):
        body = {
            "sub_scores": {
                "switching_costs": 6, "network_effects": 5, "cost_advantage": 5,
                "intangibles": 4, "efficient_scale": 4,
            },
            "narrative": "x" * 501,
        }
        r = client.post("/ticker/AAPL/qualitative/moat_strength", json=body)
        assert r.status_code == 422

    def test_post_to_deterministic_dimension_rejected(self, client):
        """ROIIC trend is computed, not analyst-submitted. POSTing should
        get a 422 telling the analyst to use /recompute instead."""
        body = {"sub_scores": {"q1": 5}}
        r = client.post("/ticker/AAPL/qualitative/roiic_trend", json=body)
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "deterministic" in detail.lower() or "not HITL" in detail

    def test_post_to_pending_data_dimension_rejected(self, client):
        body = {"sub_scores": {"q1": 5}}
        r = client.post("/ticker/AAPL/qualitative/management_tenure_continuity", json=body)
        assert r.status_code == 422

    def test_post_to_unknown_dimension_returns_404(self, client):
        body = {"sub_scores": {"q1": 5}}
        r = client.post("/ticker/AAPL/qualitative/totally_made_up", json=body)
        assert r.status_code == 404


@pytest.mark.skipif(
    not __import__("pathlib").Path("valuation_data/database/investment.duckdb").exists(),
    reason="DuckDB not present",
)
class TestRecompute:

    def test_recompute_aapl_writes_four_deterministic_dimensions(self, client):
        r = client.post("/qualitative/recompute/AAPL")
        assert r.status_code == 200
        data = r.json()
        # 4 deterministic dimensions: roiic_trend, buyback_discipline,
        # dividend_policy, cyclicality
        assert len(data["results"]) == 4
        # First-time recompute: every entry should be "written"
        statuses = [r_["status"] for r_ in data["results"]]
        assert statuses.count("written") == 4
        assert data["written_count"] == 4

    def test_recompute_idempotent(self, client):
        client.post("/qualitative/recompute/AAPL")
        r = client.post("/qualitative/recompute/AAPL")
        data = r.json()
        # Second call: input fingerprint + git_sha unchanged, all "unchanged"
        assert data["unchanged_count"] == 4
        assert data["written_count"] == 0

    def test_recompute_visible_via_get(self, client):
        client.post("/qualitative/recompute/AAPL")
        r = client.get("/ticker/AAPL/qualitative/roiic_trend")
        d = r.json()
        assert d["status"] == "assessed"
        assert d["score"] == 7  # AAPL's calibrated baseline
        assert d["source_payload"]["formula"] == "roiic_trend_v1"


@pytest.mark.skipif(
    not __import__("pathlib").Path("valuation_data/database/investment.duckdb").exists(),
    reason="DuckDB not present",
)
class TestCategories:

    def test_returns_five_categories(self, client):
        r = client.get("/qualitative/categories/AAPL")
        assert r.status_code == 200
        data = r.json()
        cats = {c["category_id"]: c for c in data["categories"]}
        assert set(cats.keys()) == {
            "quality", "capital_allocation", "competitive", "risk", "management"
        }

    def test_unassessed_category_has_null_composite(self, client):
        r = client.get("/qualitative/categories/AAPL")
        cats = {c["category_id"]: c for c in r.json()["categories"]}
        # No assessments yet (autouse fixture wiped). Quality has 5
        # potential members but n_assessed=0 → composite None.
        assert cats["quality"]["composite_score"] is None
        assert cats["quality"]["n_assessed"] == 0
        assert cats["quality"]["n_total"] == 5

    def test_management_n_total_is_zero(self, client):
        """Both management dimensions are PENDING_DATA — excluded from
        composite. Category should report n_total=0."""
        r = client.get("/qualitative/categories/AAPL")
        cats = {c["category_id"]: c for c in r.json()["categories"]}
        assert cats["management"]["n_total"] == 0
        assert cats["management"]["composite_score"] is None

    def test_partial_assessment_renormalizes_weights(self, client):
        """Submit a single quality dimension (moat_strength) and verify
        the category composite is just that score (renormalized weight
        = 1.0)."""
        body = {"sub_scores": {
            "switching_costs": 6, "network_effects": 6, "cost_advantage": 6,
            "intangibles": 6, "efficient_scale": 6,
        }}   # composite = 6.0
        client.post("/ticker/AAPL/qualitative/moat_strength", json=body)

        r = client.get("/qualitative/categories/AAPL")
        cats = {c["category_id"]: c for c in r.json()["categories"]}
        q = cats["quality"]
        assert q["n_assessed"] == 1
        assert q["composite_score"] == pytest.approx(6.0, abs=0.01)
        # Renormalized weight should be 1.0 (only contributor)
        assert q["contributing"][0]["renormalized_weight"] == pytest.approx(1.0, abs=0.001)

    def test_full_quality_category_composite(self, client):
        """Submit moat_strength=5 and recompute deterministic to populate
        roiic_trend. Quality category should average over both."""
        # HITL: moat_strength = 5.0 (all answers = 5)
        client.post(
            "/ticker/AAPL/qualitative/moat_strength",
            json={"sub_scores": {
                "switching_costs": 5, "network_effects": 5, "cost_advantage": 5,
                "intangibles": 5, "efficient_scale": 5,
            }},
        )
        # Deterministic: roiic_trend (AAPL = 7 per calibration)
        client.post("/qualitative/recompute/AAPL")

        r = client.get("/qualitative/categories/AAPL")
        cats = {c["category_id"]: c for c in r.json()["categories"]}
        q = cats["quality"]
        assert q["n_assessed"] == 2          # moat + roiic
        assert q["n_total"] == 5
        # Composite is renormalized average of 5.0 and 7.0 (equal weights)
        assert q["composite_score"] == pytest.approx(6.0, abs=0.01)
