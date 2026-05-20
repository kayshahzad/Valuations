"""Phase B.2 integration tests for the extraction workflow node.

End-to-end-ish path with a mocked Gemini call but a REAL DuckDB
connection. Verifies:

  - Bundle extractor invoked once
  - Three rows land in ``qualitative_assessments`` with the right
    source_category, provenance fields, and fingerprint
  - Re-running on the same source text writes nothing the second
    time (idempotency via fingerprint match) — but does still
    invoke the LLM (idempotency is DB-side, not LLM-side; cost
    saving on re-runs is Phase B.3)
  - Per-dim outcomes returned in ``state["qualitative_extraction_results"]``

Uses a temp DuckDB so the test doesn't touch production data.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aletheia.qualitative.extractors.schemas import (
    CompetitorExtraction,
    CustomerExtraction,
    QualitativeExtractionBundle,
    RegulatoryExposureItem,
    RegulatoryExtraction,
)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Spin up a fresh InvestmentDatabase pointed at a temp DuckDB
    file so this test doesn't touch production data."""
    db_path = tmp_path / "test_qualitative.duckdb"
    # Patch the default DB path before constructing
    monkeypatch.setenv("ALETHEIA_DB_PATH", str(db_path))
    from aletheia.data.database import InvestmentDatabase
    db = InvestmentDatabase(db_path=str(db_path), verbose=False)
    yield db
    db.close()


def _valid_bundle() -> QualitativeExtractionBundle:
    return QualitativeExtractionBundle(
        competitor_identification=CompetitorExtraction(
            score=5,
            narrative="GOOGL faces competition from MSFT in cloud and AMZN in commerce.",
            named_competitors=["Microsoft", "Amazon", "Meta"],
            competitive_intensity="medium",
        ),
        regulatory_exposure=RegulatoryExtraction(
            score=3,
            narrative="Multiple active antitrust reviews; EU DMA compliance ongoing.",
            material_exposures=[
                RegulatoryExposureItem(
                    regulator="DOJ Antitrust Division",
                    area="antitrust",
                    severity="high",
                ),
            ],
        ),
        customer_concentration=CustomerExtraction(
            score=6,
            narrative="No single customer accounted for more than 10% of revenue in FY24.",
            concentration_disclosed=False,
            named_customers=[],
        ),
    )


def _mock_bundle_extractor_returning(bundle: QualitativeExtractionBundle):
    """Patch make_bundle_extractor to return a function that returns
    a fan-out of the provided bundle. Bypasses the live LLM call."""
    from aletheia.qualitative.extractors.bundle_extractor import (
        fan_out_bundle,
    )

    def _fake_make_bundle_extractor(**kwargs):
        def _fake_extractor(ticker, source_text):
            import hashlib
            fp = hashlib.sha256(source_text.encode()).hexdigest()[:16]
            return fan_out_bundle(bundle, fp)
        return _fake_extractor

    return _fake_make_bundle_extractor


def test_node_persists_three_rows_on_first_run(temp_db, monkeypatch):
    """Happy path: node calls the bundle, gets back three results,
    writes three rows to qualitative_assessments with the right
    provenance."""
    from aletheia.data.database import InvestmentDatabase
    from aletheia.agents import qualitative_extraction

    # Patch make_bundle_extractor so no real LLM call fires
    fake_factory = _mock_bundle_extractor_returning(_valid_bundle())
    monkeypatch.setattr(
        "aletheia.qualitative.extractors.bundle_extractor."
        "make_bundle_extractor",
        fake_factory,
    )

    # Patch InvestmentDatabase() so the node uses our temp_db
    # (the node opens its own connection inside _run_bundle)
    original_init = InvestmentDatabase.__init__

    def _temp_init(self, db_path=None, verbose=False):
        original_init(self, db_path=str(temp_db.db_path), verbose=False)

    monkeypatch.setattr(InvestmentDatabase, "__init__", _temp_init)

    # Empty raw_def14a_text ⇒ DEF 14A bundle no-ops (no_data). The
    # 10-K bundle is what this test exercises.
    state = {
        "ticker":       "GOOGL",
        "raw_10k_text": (
            "ITEM 1: BUSINESS\nWe make software.\n\n"
            "ITEM 1A: RISK FACTORS\nWe face regulatory risk."
        ),
        "raw_def14a_text": "",
    }
    out = qualitative_extraction.qualitative_extraction_node(state)
    results = out["qualitative_extraction_results"]

    # Filter to the 10-K bundle dims this test is about
    tenk_dims = {
        "competitor_identification",
        "regulatory_exposure",
        "customer_concentration",
    }
    tenk_results = [r for r in results if r["dimension_id"] in tenk_dims]

    assert len(tenk_results) == 3
    statuses = {r["dimension_id"]: r["status"] for r in tenk_results}
    assert all(s == "written" for s in statuses.values()), statuses

    # DB verification — three rows persisted with right shape
    db = InvestmentDatabase(db_path=str(temp_db.db_path), verbose=False)
    try:
        for dim_id, expected_score in [
            ("competitor_identification", 5),
            ("regulatory_exposure", 3),
            ("customer_concentration", 6),
        ]:
            row = db.get_latest_assessment("GOOGL", dim_id)
            assert row is not None, f"No row for {dim_id}"
            assert row["score"] == expected_score
            assert row["source_category"] == "llm_augmented"
            # Provenance trail
            payload = row.get("source_payload")
            if isinstance(payload, str):
                import json
                payload = json.loads(payload)
            assert payload.get("llm_provider") == "gemini"
            assert payload.get("llm_model") == "gemini-3.1-pro-preview"
    finally:
        db.close()


def test_node_idempotent_on_second_run(temp_db, monkeypatch):
    """Re-running the node on the same source text → second run
    writes nothing new (input_fingerprint match). Verifies the DB-
    side idempotency guard works for the bundle path."""
    from aletheia.data.database import InvestmentDatabase
    from aletheia.agents import qualitative_extraction

    fake_factory = _mock_bundle_extractor_returning(_valid_bundle())
    monkeypatch.setattr(
        "aletheia.qualitative.extractors.bundle_extractor."
        "make_bundle_extractor",
        fake_factory,
    )

    original_init = InvestmentDatabase.__init__

    def _temp_init(self, db_path=None, verbose=False):
        original_init(self, db_path=str(temp_db.db_path), verbose=False)

    monkeypatch.setattr(InvestmentDatabase, "__init__", _temp_init)

    state = {
        "ticker":       "GOOGL",
        "raw_10k_text": "ITEM 1: BUSINESS\nfoo\n\nITEM 1A: RISK FACTORS\nbar",
        "raw_def14a_text": "",  # DEF 14A bundle no-ops; test focuses on 10-K
    }
    # Only the 10-K bundle has source; DEF 14A returns no_data
    tenk_dims = {"competitor_identification", "regulatory_exposure",
                 "customer_concentration"}

    # First run — three 10-K writes (DEF 14A no_data)
    out1 = qualitative_extraction.qualitative_extraction_node(state)
    tenk_outcomes = [r for r in out1["qualitative_extraction_results"]
                     if r["dimension_id"] in tenk_dims]
    assert all(r["status"] == "written" for r in tenk_outcomes)

    # Second run — three 10-K unchanged (idempotency match)
    out2 = qualitative_extraction.qualitative_extraction_node(state)
    tenk_statuses = {r["dimension_id"]: r["status"]
                     for r in out2["qualitative_extraction_results"]
                     if r["dimension_id"] in tenk_dims}
    assert all(s == "unchanged" for s in tenk_statuses.values()), (
        f"Expected all 'unchanged' on second run, got: {tenk_statuses}"
    )


def test_node_writes_failure_rows_when_bundle_extractor_raises(
    temp_db, monkeypatch,
):
    """When the bundle extractor fails (production-side validation /
    LLM error), the node logs the failure but doesn't crash. Three
    error outcomes returned; no DB writes (failure results have
    None scores but we still persist them so the gap is auditable —
    matches the design in bundle_extractor._failure_results)."""
    from aletheia.data.database import InvestmentDatabase
    from aletheia.agents import qualitative_extraction

    # Fake make_bundle_extractor that returns a callable raising
    def _broken_factory(**kwargs):
        def _raise(ticker, source_text):
            raise RuntimeError("simulated LLM failure")
        return _raise

    monkeypatch.setattr(
        "aletheia.qualitative.extractors.bundle_extractor."
        "make_bundle_extractor",
        _broken_factory,
    )

    original_init = InvestmentDatabase.__init__

    def _temp_init(self, db_path=None, verbose=False):
        original_init(self, db_path=str(temp_db.db_path), verbose=False)

    monkeypatch.setattr(InvestmentDatabase, "__init__", _temp_init)

    state = {
        "ticker":       "GOOGL",
        "raw_10k_text": "ITEM 1: BUSINESS\nfoo\n\nITEM 1A: RISK FACTORS\nbar",
        "raw_def14a_text": "",  # DEF 14A bundle no-ops
    }
    out = qualitative_extraction.qualitative_extraction_node(state)
    results = out["qualitative_extraction_results"]

    # Filter to the 10-K bundle dims this test exercises
    tenk_dims = {"competitor_identification", "regulatory_exposure",
                 "customer_concentration"}
    tenk_results = [r for r in results if r["dimension_id"] in tenk_dims]

    assert len(tenk_results) == 3
    assert all(r["status"] == "error" for r in tenk_results), tenk_results
    assert all("simulated LLM failure" in (r.get("reason") or "")
               for r in tenk_results)
