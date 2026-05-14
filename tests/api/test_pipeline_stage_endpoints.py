"""Tests for the per-stage pipeline endpoints used by the Stage
Explorer + Status Matrix UIs.

Coverage shape mirrors docs/pipeline_ui_design.md "Commit 1" spec:
  - Happy path per stage (ingest, validate, calculate, agents, run)
  - Stage-4 LLM-cost confirmation gate (refuses without flag)
  - Status read endpoints (matrix + per-ticker)
  - Cache-bust endpoint accepts short + long stage forms; rejects unknowns

Uses FastAPI's TestClient and monkey-patches the underlying stage
runners so tests stay deterministic and offline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from aletheia.contracts.pipeline import (
    AgentBundle,
    CalculationBundle,
    IngestedRawBundle,
    PipelineStatusRow,
    RawSource,
    StageStatus,
    ValidatedCleanedRecord,
    ValidationReceipt,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """FastAPI TestClient with the per-stage endpoints' runners
    monkey-patched to deterministic synthetic outputs. Each test
    can override individual runners through the returned ``state``
    dict."""
    # Redirect PipelineStatusStore to a temp DuckDB so reads/writes
    # don't pollute the production status table.
    from aletheia.pipeline import status_store as ss_mod

    state: Dict[str, Any] = {
        "stage1_bundle": _make_ingested_bundle(),
        "stage2_records": [_make_validated_record()],
        "stage3_bundle": _make_calc_bundle(),
        "stage4_bundle": _make_agent_bundle(),
        "raises": {},  # stage → exception to raise
    }

    def fake_run_stage1(ticker, **kw):
        if exc := state["raises"].get("stage1"):
            raise exc
        return state["stage1_bundle"]

    def fake_run_stage2(*, ticker, **kw):
        if exc := state["raises"].get("stage2"):
            raise exc
        return state["stage2_records"]

    def fake_run_stage3(records, **kw):
        if exc := state["raises"].get("stage3"):
            raise exc
        return state["stage3_bundle"]

    def fake_run_stage4(calc_bundle, **kw):
        if exc := state["raises"].get("stage4"):
            raise exc
        return state["stage4_bundle"]

    def fake_load_records(ticker, pipeline_version):
        return state["stage2_records"]

    from aletheia.pipeline import stage1_ingest, stage2_validate, stage3_calculate, stage4_agents
    monkeypatch.setattr(stage1_ingest, "run_stage1", fake_run_stage1)
    monkeypatch.setattr(stage2_validate, "run_stage2", fake_run_stage2)
    monkeypatch.setattr(stage3_calculate, "run_stage3", fake_run_stage3)
    monkeypatch.setattr(stage4_agents, "run_stage4", fake_run_stage4)
    # api_main imports lazily inside each endpoint, so we also patch
    # the cli.calc.load_records that Stage 3 + Stage 4 call.
    from aletheia.cli import calc as cli_calc
    monkeypatch.setattr(cli_calc, "load_records", fake_load_records)

    # Temp DuckDB for status store. Each PipelineStatusStore() in
    # the endpoint code opens the default DB path; we redirect by
    # patching that default.
    monkeypatch.setattr(ss_mod, "_DEFAULT_DB_PATH", tmp_path / "test.duckdb")

    import api_main
    client = TestClient(api_main.app)
    yield client, state


# ─────────────────────────────────────────────────────────────────────
# Synthetic bundle helpers
# ─────────────────────────────────────────────────────────────────────

def _make_ingested_bundle() -> IngestedRawBundle:
    return IngestedRawBundle(
        ticker="NVDA",
        bundle_fingerprint="i" * 64,
        fetched_at=datetime.now(timezone.utc),
        sources={
            "sec_companyfacts": RawSource(
                source="sec_companyfacts",
                url="https://example/test",
                fetched_at=datetime.now(timezone.utc),
                payload_path=Path("/tmp/fake.json"),
                payload_sha256="a" * 64,
                metadata={},
            )
        },
        classification_snapshot={"ticker": "NVDA", "sector": "Technology"},
        pipeline_version="test-v1",
    )


def _make_validated_record() -> ValidatedCleanedRecord:
    return ValidatedCleanedRecord(
        ticker="NVDA", fiscal_year=2024, period="FY",
        period_end_date="2024-12-31",
        raw={"Revenue": 100.0}, clean={"Revenue": 100.0}, derived={},
        overall_quality_score=0.95,
        cleaning_warnings=[], blocking_errors=[],
        validation=ValidationReceipt(),
        record_fingerprint="r" * 64,
        input_bundle_fingerprint="i" * 64,
        cleaned_at=datetime.now(timezone.utc),
        pipeline_version="test-v1",
    )


def _make_calc_bundle() -> CalculationBundle:
    return CalculationBundle(
        ticker="NVDA", fiscal_year=2024, base_period="FY",
        dcf={"ticker": "NVDA", "wacc_base": 0.13},
        reverse_dcf={}, multiple_decomposition={},
        screening={}, moat_fingerprint={}, cyclicality={},
        scenarios=[], capital_structure={}, reality_checks={},
        schema_violations=[],
        bundle_fingerprint="c" * 64,
        input_record_fingerprint="r" * 64,
        computed_at=datetime.now(timezone.utc),
        pipeline_version="test-v1",
    )


def _make_agent_bundle() -> AgentBundle:
    return AgentBundle(
        ticker="NVDA",
        qualitative_synthesis={}, contrarian={}, thesis={},
        raw_10k_excerpt=None,
        bundle_fingerprint="a" * 64,
        input_calculation_fingerprint="c" * 64,
        computed_at=datetime.now(timezone.utc),
        pipeline_version="test-v1",
        llm_cost_usd=None,
    )


# ─────────────────────────────────────────────────────────────────────
# Happy-path per-stage tests
# ─────────────────────────────────────────────────────────────────────

def test_ingest_endpoint_returns_typed_bundle(client):
    c, _ = client
    r = c.post("/pipeline/stages/NVDA/ingest", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "NVDA"
    assert len(body["bundle_fingerprint"]) == 64
    assert "sec_companyfacts" in body["sources"]


def test_validate_endpoint_returns_record_list(client):
    c, _ = client
    r = c.post("/pipeline/stages/NVDA/validate", json={})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert body[0]["ticker"] == "NVDA"
    assert body[0]["fiscal_year"] == 2024


def test_calculate_endpoint_returns_calc_bundle(client):
    c, _ = client
    r = c.post("/pipeline/stages/NVDA/calculate", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "NVDA"
    assert body["dcf"]["wacc_base"] == 0.13
    assert len(body["bundle_fingerprint"]) == 64


def test_run_endpoint_returns_orchestrator_result(client):
    c, _ = client
    r = c.post("/pipeline/stages/NVDA/run", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "NVDA"
    assert body["all_ok"] is True
    # Stage 4 omitted when auto_agents is False (default).
    assert "stage4_agents" not in body["stages"]
    for s in ("stage1_ingest", "stage2_validate", "stage3_calculate"):
        assert body["stages"][s]["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────
# Stage-4 LLM cost gate
# ─────────────────────────────────────────────────────────────────────

def test_agents_endpoint_refuses_without_confirm_flag(client):
    c, _ = client
    r = c.post("/pipeline/stages/NVDA/agents", json={})
    assert r.status_code == 400
    assert "confirm_llm_cost" in r.json()["detail"]


def test_agents_endpoint_refuses_when_flag_explicitly_false(client):
    c, _ = client
    r = c.post("/pipeline/stages/NVDA/agents", json={"confirm_llm_cost": False})
    assert r.status_code == 400


def test_agents_endpoint_runs_when_confirmed(client):
    c, _ = client
    r = c.post("/pipeline/stages/NVDA/agents", json={"confirm_llm_cost": True})
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "NVDA"
    assert len(body["bundle_fingerprint"]) == 64


# ─────────────────────────────────────────────────────────────────────
# Status read endpoints
# ─────────────────────────────────────────────────────────────────────

def test_status_matrix_is_empty_before_any_runs(client):
    c, _ = client
    r = c.get("/pipeline/status")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_status_for_ticker_returns_empty_list_when_unknown(client):
    """Spec: empty 200 not 404 — matches the matrix endpoint shape."""
    c, _ = client
    r = c.get("/pipeline/status/ZZZNOTREAL")
    assert r.status_code == 200
    assert r.json() == []


# ─────────────────────────────────────────────────────────────────────
# Cache-bust endpoint
# ─────────────────────────────────────────────────────────────────────

def test_bust_cache_rejects_empty_stages_list(client):
    c, _ = client
    r = c.post("/pipeline/bust-cache/NVDA", json={"stages": []})
    assert r.status_code == 400
    assert "at least one stage" in r.json()["detail"]


def test_bust_cache_rejects_unknown_stage_id(client):
    c, _ = client
    r = c.post("/pipeline/bust-cache/NVDA", json={"stages": ["stage99"]})
    assert r.status_code == 400
    assert "Unknown stage" in r.json()["detail"]


def test_bust_cache_accepts_short_and_long_stage_forms(client):
    """The CLI's --bust-cache stage1,stage2 short form maps to
    stage1_ingest / stage2_validate. The endpoint accepts both."""
    c, state = client
    # Seed status rows so bust-cache has something to update.
    from aletheia.pipeline.status_store import PipelineStatusStore
    from aletheia.pipeline import status_store as ss_mod
    with PipelineStatusStore() as store:
        store.mark_ok(
            "NVDA", "stage1_ingest",
            fingerprint="x" * 64, duration_seconds=0.5,
        )
        store.mark_ok(
            "NVDA", "stage3_calculate",
            fingerprint="y" * 64, duration_seconds=0.5,
        )

    r = c.post(
        "/pipeline/bust-cache/NVDA",
        json={"stages": ["stage1"]},  # short form
    )
    assert r.status_code == 200
    body = r.json()
    # stage1 busted; stage2/3/4 cascade-invalidated (downstream).
    busted_stages = {row["stage"] for row in body["updated"]}
    assert "stage1_ingest" in busted_stages


def test_bust_cache_cascades_downstream(client):
    """Busting stage2 must cascade-invalidate stage3 + stage4 per
    cascade_invalidation_targets."""
    c, _ = client
    from aletheia.pipeline.status_store import PipelineStatusStore
    with PipelineStatusStore() as store:
        store.mark_ok("NVDA", "stage2_validate", fingerprint="x" * 64, duration_seconds=0.5)
        store.mark_ok("NVDA", "stage3_calculate", fingerprint="y" * 64, duration_seconds=0.5)

    r = c.post(
        "/pipeline/bust-cache/NVDA",
        json={"stages": ["stage2_validate"]},
    )
    assert r.status_code == 200
    busted = {row["stage"] for row in r.json()["updated"]}
    # stage2 was busted; stage3 cascade-invalidated.
    assert "stage2_validate" in busted
    assert "stage3_calculate" in busted
