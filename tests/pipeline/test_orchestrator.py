"""Orchestrator + status-store integration tests.

Verifies the Week 6 integration contract:
  - Stage 1 → 2 → 3 chain produces the expected typed payloads.
  - Cache-hit detection skips re-run when fingerprints match.
  - --bust-cache forces re-run and cascades to downstream stages.
  - Failures propagate as skipped_dependency to downstream stages.
  - The pipeline_status DuckDB table is read/written correctly.

Uses monkey-patched stage runners so the tests don't hit network
or DuckDB-cleaning state. The orchestrator's status_store is
redirected to a temp DuckDB file per test for isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

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
from aletheia.pipeline import orchestrator as orch_mod
from aletheia.pipeline.orchestrator import Orchestrator
from aletheia.pipeline.status_store import PipelineStatusStore


# ─────────────────────────────────────────────────────────────────────
# Fixtures — synthetic stage payloads + monkey-patched stage runners
# ─────────────────────────────────────────────────────────────────────

def _make_ingested_bundle(ticker="NVDA", fp="i" * 64) -> IngestedRawBundle:
    src = RawSource(
        source="sec_companyfacts",
        url="https://example/test",
        fetched_at=datetime.now(timezone.utc),
        payload_path=Path("/tmp/fake.json"),
        payload_sha256="a" * 64,
        metadata={},
    )
    return IngestedRawBundle(
        ticker=ticker,
        bundle_fingerprint=fp,
        fetched_at=datetime.now(timezone.utc),
        sources={"sec_companyfacts": src},
        classification_snapshot={"ticker": ticker},
        pipeline_version="v",
    )


def _make_validated_record(
    ticker="NVDA", fy=2024, fp="r" * 64,
) -> ValidatedCleanedRecord:
    return ValidatedCleanedRecord(
        ticker=ticker, fiscal_year=fy, period="FY",
        period_end_date=f"{fy}-12-31",
        raw={"Revenue": 100.0}, clean={"Revenue": 100.0}, derived={},
        overall_quality_score=0.95,
        cleaning_warnings=[], blocking_errors=[],
        validation=ValidationReceipt(),
        record_fingerprint=fp,
        input_bundle_fingerprint="i" * 64,
        cleaned_at=datetime.now(timezone.utc),
        pipeline_version="v",
    )


def _make_calc_bundle(ticker="NVDA", fp="c" * 64) -> CalculationBundle:
    return CalculationBundle(
        ticker=ticker, fiscal_year=2024, base_period="FY",
        dcf={"ticker": ticker}, reverse_dcf={}, multiple_decomposition={},
        screening={}, moat_fingerprint={}, cyclicality={},
        scenarios=[], capital_structure={}, reality_checks={},
        schema_violations=[],
        bundle_fingerprint=fp, input_record_fingerprint="r" * 64,
        computed_at=datetime.now(timezone.utc),
        pipeline_version="v",
    )


@pytest.fixture
def patched_orch(tmp_path, monkeypatch):
    """Builds an Orchestrator with stub stage runners and a temp DB.
    Returns (orchestrator, state) where state lets tests control
    stage behavior."""
    state: Dict[str, Any] = {
        "stage1_bundle": _make_ingested_bundle(),
        "stage1_raise": None,
        "stage2_records": [_make_validated_record()],
        "stage2_raise": None,
        "stage3_bundle": _make_calc_bundle(),
        "stage3_raise": None,
    }

    def stub_stage1(ticker, *, pipeline_version, force_refresh=False,
                    sources=None, include_market_snapshot=True):
        if state["stage1_raise"]:
            raise state["stage1_raise"]
        return state["stage1_bundle"]

    def stub_stage2(*, ticker, pipeline_version,
                    input_bundle_fingerprint=None, fiscal_years=None):
        if state["stage2_raise"]:
            raise state["stage2_raise"]
        return state["stage2_records"]

    def stub_stage3(records, *, pipeline_version,
                    classification=None, fiscal_year=None):
        if state["stage3_raise"]:
            raise state["stage3_raise"]
        return state["stage3_bundle"]

    monkeypatch.setattr(orch_mod, "run_stage1", stub_stage1)
    monkeypatch.setattr(orch_mod, "run_stage2", stub_stage2)
    monkeypatch.setattr(orch_mod, "run_stage3", stub_stage3)

    store = PipelineStatusStore(db_path=tmp_path / "test_pipeline.duckdb")
    orch = Orchestrator(status_store=store)
    yield orch, state
    orch.close()


# ─────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────

def test_orchestrator_chains_stages_1_2_3(patched_orch):
    orch, _ = patched_orch
    result = orch.run("NVDA", pipeline_version="v1")
    assert result.all_ok
    assert {"stage1_ingest", "stage2_validate", "stage3_calculate"} <= set(result.stages)
    assert result.stages["stage1_ingest"].status == StageStatus.OK
    assert result.stages["stage2_validate"].status == StageStatus.OK
    assert result.stages["stage3_calculate"].status == StageStatus.OK
    # Lineage chain: each stage's fingerprint is captured.
    assert result.stages["stage1_ingest"].fingerprint == "i" * 64
    assert result.stages["stage2_validate"].fingerprint == "r" * 64
    assert result.stages["stage3_calculate"].fingerprint == "c" * 64


def test_orchestrator_stops_at_stage3_by_default(patched_orch):
    """LLM cost gating: Stage 4 is opt-in via --auto-agents."""
    orch, _ = patched_orch
    result = orch.run("NVDA", pipeline_version="v1")
    assert "stage4_agents" not in result.stages


def test_orchestrator_runs_stage4_when_auto_agents(patched_orch):
    orch, _ = patched_orch
    result = orch.run("NVDA", pipeline_version="v1", auto_agents=True)
    assert "stage4_agents" in result.stages
    assert result.stages["stage4_agents"].status == StageStatus.OK


# ─────────────────────────────────────────────────────────────────────
# Cache-hit detection
# ─────────────────────────────────────────────────────────────────────

def test_second_run_is_cache_hit(patched_orch):
    orch, _ = patched_orch
    r1 = orch.run("NVDA", pipeline_version="v1")
    assert r1.stages["stage1_ingest"].status == StageStatus.OK

    r2 = orch.run("NVDA", pipeline_version="v1")
    # Same fingerprints from stubs → all three stages should be cached.
    assert r2.stages["stage1_ingest"].status == StageStatus.SKIPPED_CACHED
    assert r2.stages["stage2_validate"].status == StageStatus.SKIPPED_CACHED
    assert r2.stages["stage3_calculate"].status == StageStatus.SKIPPED_CACHED


def test_pipeline_version_bump_busts_cache(patched_orch):
    """Same fingerprint but different pipeline_version doesn't matter
    here because the stub returns fixed fingerprints — but the real
    stages would compute a different fingerprint when
    pipeline_version changes. We test the orchestrator's behaviour
    when the upstream fingerprint differs."""
    orch, state = patched_orch
    orch.run("NVDA", pipeline_version="v1")

    # Simulate: Stage 1 now produces a different bundle (e.g., FMP
    # cache refreshed → different payload_sha256 → new fingerprint).
    state["stage1_bundle"] = _make_ingested_bundle(fp="X" * 64)
    state["stage2_records"] = [
        _make_validated_record(fp="Y" * 64)
    ]
    state["stage3_bundle"] = _make_calc_bundle(fp="Z" * 64)

    r2 = orch.run("NVDA", pipeline_version="v1")
    assert r2.stages["stage1_ingest"].status == StageStatus.OK  # cache miss
    assert r2.stages["stage2_validate"].status == StageStatus.OK
    assert r2.stages["stage3_calculate"].status == StageStatus.OK


def test_bust_cache_forces_rerun(patched_orch):
    orch, _ = patched_orch
    orch.run("NVDA", pipeline_version="v1")  # populates cache
    r2 = orch.run("NVDA", pipeline_version="v1",
                  bust_cache=["stage2_validate"])
    # Stage 2 explicitly busted → re-runs as ok, not cached.
    assert r2.stages["stage2_validate"].status == StageStatus.OK
    # Cascade: stage 3 also busts because it's downstream of stage 2.
    assert r2.stages["stage3_calculate"].status == StageStatus.OK
    # Stage 1 is upstream of the bust target → still cached.
    assert r2.stages["stage1_ingest"].status == StageStatus.SKIPPED_CACHED


def test_force_refresh_busts_all_stages(patched_orch):
    orch, _ = patched_orch
    orch.run("NVDA", pipeline_version="v1")
    r2 = orch.run("NVDA", pipeline_version="v1", force_refresh=True)
    for stage in ("stage1_ingest", "stage2_validate", "stage3_calculate"):
        assert r2.stages[stage].status == StageStatus.OK


def test_short_form_bust_cache_via_cli_parser():
    """CLI accepts 'stage1,stage2' shorthand — same lookup the
    orchestrator gets after parsing."""
    from aletheia.cli.pipeline import _parse_bust_cache
    assert _parse_bust_cache("stage1,stage2") == [
        "stage1_ingest", "stage2_validate",
    ]
    assert _parse_bust_cache("stage4") == ["stage4_agents"]
    assert _parse_bust_cache(None) is None
    assert _parse_bust_cache("") is None


# ─────────────────────────────────────────────────────────────────────
# Failure cascade
# ─────────────────────────────────────────────────────────────────────

def test_stage1_failure_marks_downstream_skipped_dependency(patched_orch):
    from aletheia.pipeline.stage1_ingest import Stage1IngestError
    orch, state = patched_orch
    state["stage1_raise"] = Stage1IngestError("simulated SEC 403")

    result = orch.run("NVDA", pipeline_version="v")
    assert result.stages["stage1_ingest"].status == StageStatus.FAILED
    assert result.stages["stage2_validate"].status == StageStatus.SKIPPED_DEPENDENCY
    assert result.stages["stage3_calculate"].status == StageStatus.SKIPPED_DEPENDENCY


def test_stage2_failure_marks_stage3_skipped_dependency(patched_orch):
    from aletheia.pipeline.stage2_validate import Stage2ValidateError
    orch, state = patched_orch
    state["stage2_raise"] = Stage2ValidateError("zero records")

    result = orch.run("NVDA", pipeline_version="v")
    assert result.stages["stage1_ingest"].status == StageStatus.OK
    assert result.stages["stage2_validate"].status == StageStatus.FAILED
    assert result.stages["stage3_calculate"].status == StageStatus.SKIPPED_DEPENDENCY


# ─────────────────────────────────────────────────────────────────────
# Status store behaviour
# ─────────────────────────────────────────────────────────────────────

def test_status_store_roundtrip(tmp_path):
    db_path = tmp_path / "test_status.duckdb"
    with PipelineStatusStore(db_path=db_path) as store:
        store.mark_ok(
            "NVDA", "stage1_ingest",
            fingerprint="x" * 64,
            duration_seconds=0.5,
            rows_processed=10,
        )
        row = store.get("NVDA", "stage1_ingest")
        assert row is not None
        assert row.status == StageStatus.OK
        assert row.fingerprint == "x" * 64
        assert row.rows_processed == 10


def test_status_store_orders_matrix_by_ticker_then_stage(tmp_path):
    db_path = tmp_path / "test_status.duckdb"
    with PipelineStatusStore(db_path=db_path) as store:
        store.mark_ok("NVDA", "stage3_calculate", fingerprint="a",
                       duration_seconds=0.1)
        store.mark_ok("AAPL", "stage1_ingest", fingerprint="b",
                       duration_seconds=0.1)
        store.mark_ok("NVDA", "stage1_ingest", fingerprint="c",
                       duration_seconds=0.1)
        rows = store.matrix()
        order = [(r.ticker, r.stage) for r in rows]
        assert order == [
            ("AAPL", "stage1_ingest"),
            ("NVDA", "stage1_ingest"),
            ("NVDA", "stage3_calculate"),
        ]


def test_status_store_by_stage_status_query(tmp_path):
    db_path = tmp_path / "test_status.duckdb"
    with PipelineStatusStore(db_path=db_path) as store:
        store.mark_failed("AAPL", "stage3_calculate",
                          error_message="DCF failure",
                          duration_seconds=0.5)
        store.mark_ok("NVDA", "stage3_calculate", fingerprint="x",
                       duration_seconds=0.5)
        failures = store.get_by_stage_status(
            "stage3_calculate", StageStatus.FAILED,
        )
        assert [r.ticker for r in failures] == ["AAPL"]


def test_orchestrator_persists_status_row(patched_orch):
    """After a successful run, the status table holds an OK row per
    stage executed."""
    orch, _ = patched_orch
    orch.run("NVDA", pipeline_version="v1")
    rows = orch._status_store.get_for_ticker("NVDA")
    statuses = {r.stage: r.status for r in rows}
    assert statuses["stage1_ingest"] == StageStatus.OK
    assert statuses["stage2_validate"] == StageStatus.OK
    assert statuses["stage3_calculate"] == StageStatus.OK
