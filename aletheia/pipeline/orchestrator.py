"""Pipeline orchestrator — chains Stage 1 → 2 → 3 → optional Stage 4.

Walks one ticker through the four stage modules, threading lineage
fingerprints between them, and reporting per-stage state through the
``PipelineStatusStore``. The orchestrator IS the integration point
that makes the typed contracts actually useful — without it, each
stage is callable in isolation but no cache/lineage chain wires them.

Cache-hit semantics:
  - The orchestrator computes the "would-be" fingerprint for each
    downstream stage from the upstream fingerprint + override state
    + pipeline_version. If the stored fingerprint matches, the stage
    is marked ``skipped_cached`` and not re-run.
  - For Stage 1, fingerprint is content-addressed (sha256 of raw
    payload bytes). The fetchers themselves cache disk-level, so a
    Stage 1 "skip" means "every source's payload_sha256 matches
    what we have on disk".
  - ``--bust-cache <stage>`` forces a re-run of that stage and every
    downstream stage. Implements the explicit per-stage invalidation
    contract from docs/pipeline_contracts.md.

LLM cost gating:
  - The chain stops at Stage 3 by default. Stage 4 only runs when
    ``auto_agents=True`` (the orchestrator equivalent of
    ``aletheia pipeline run --auto-agents``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from aletheia.contracts.pipeline import (
    AgentBundle,
    CalculationBundle,
    IngestedRawBundle,
    StageStatus,
    ValidatedCleanedRecord,
    cascade_invalidation_targets,
)
from aletheia.pipeline.stage1_ingest import (
    Stage1IngestError,
    run_stage1,
)
from aletheia.pipeline.stage2_validate import (
    Stage2ValidateError,
    run_stage2,
)
from aletheia.pipeline.stage3_calculate import (
    Stage3InputError,
    run_stage3,
)
from aletheia.pipeline.stage4_agents import (
    AgentRunner,
    Stage4AgentError,
    run_stage4,
)
from aletheia.pipeline.status_store import PipelineStatusStore


# ─────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────

@dataclass
class StageOutcome:
    """Per-stage result captured by the orchestrator."""
    stage: str
    status: StageStatus
    fingerprint: Optional[str] = None
    duration_seconds: float = 0.0
    error_message: Optional[str] = None
    payload: Any = None  # Stage's typed output, when produced


@dataclass
class OrchestratorResult:
    """Full output of a single pipeline run for one ticker."""
    ticker: str
    pipeline_version: str
    started_at: datetime
    finished_at: datetime
    stages: Dict[str, StageOutcome] = field(default_factory=dict)
    auto_agents: bool = False

    @property
    def all_ok(self) -> bool:
        return all(
            o.status in (StageStatus.OK, StageStatus.SKIPPED_CACHED)
            for o in self.stages.values()
        )

    @property
    def summary(self) -> Dict[str, str]:
        return {s: o.status.value for s, o in self.stages.items()}


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────

_VALID_STAGES = ("stage1_ingest", "stage2_validate",
                 "stage3_calculate", "stage4_agents")


class Orchestrator:
    """Runs the four-stage pipeline for one ticker per call.

    Holds a single ``PipelineStatusStore`` connection for the
    duration of its lifetime. For batch runs (universe sweep), create
    one orchestrator and call ``.run()`` per ticker — the store
    handles the upserts.
    """

    def __init__(
        self,
        *,
        status_store: Optional[PipelineStatusStore] = None,
        agent_runner: Optional[AgentRunner] = None,
        runner_id: Optional[str] = None,
    ) -> None:
        self._status_store = status_store or PipelineStatusStore()
        self._owns_store = status_store is None
        self._agent_runner = agent_runner
        self._runner_id = runner_id

    def close(self) -> None:
        if self._owns_store and self._status_store is not None:
            self._status_store.close()

    def __enter__(self) -> "Orchestrator":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ── main entry point ────────────────────────────────────────────

    def run(
        self,
        ticker: str,
        *,
        pipeline_version: str,
        auto_agents: bool = False,
        bust_cache: Optional[Sequence[str]] = None,
        force_refresh: bool = False,
        include_market_snapshot: bool = True,
    ) -> OrchestratorResult:
        """Execute the pipeline for one ticker.

        Args:
            ticker: Symbol.
            pipeline_version: Code SHA / version stamp. Folded into
                every stage's fingerprint.
            auto_agents: When True, runs Stage 4 after Stage 3.
                Default False so the orchestrator doesn't burn LLM
                budget on every call.
            bust_cache: Stage ids to force re-run. Downstream stages
                of any busted stage also re-run (cascade-invalidation).
            force_refresh: Equivalent to ``bust_cache`` containing
                every stage. Also propagates to Stage 1's fetcher TTL
                bypass.
            include_market_snapshot: Forwarded to Stage 1. Set False
                for offline / CI runs.
        """
        ticker = ticker.upper()
        started = datetime.now(timezone.utc)
        result = OrchestratorResult(
            ticker=ticker,
            pipeline_version=pipeline_version,
            started_at=started,
            finished_at=started,
            auto_agents=auto_agents,
        )

        bust_set: Set[str] = set(bust_cache or [])
        # Cascade: if a stage is busted, every downstream stage is
        # also implicitly busted. The cascade helper from the
        # contracts module encodes that policy.
        if force_refresh:
            bust_set.update(_VALID_STAGES)
        for stage in list(bust_set):
            bust_set.update(cascade_invalidation_targets(stage))

        # ── Stage 1 ────────────────────────────────────────────────
        stage1_outcome = self._run_stage1(
            ticker, pipeline_version=pipeline_version,
            force_refresh=force_refresh,
            include_market_snapshot=include_market_snapshot,
            bust=("stage1_ingest" in bust_set),
        )
        result.stages["stage1_ingest"] = stage1_outcome
        if stage1_outcome.status == StageStatus.FAILED:
            for downstream in cascade_invalidation_targets("stage1_ingest"):
                self._status_store.mark_skipped_dependency(
                    ticker, downstream, dependency_stage="stage1_ingest",
                )
                result.stages[downstream] = StageOutcome(
                    stage=downstream,
                    status=StageStatus.SKIPPED_DEPENDENCY,
                    error_message=f"upstream stage1_ingest failed",
                )
            result.finished_at = datetime.now(timezone.utc)
            return result

        # ── Stage 2 ────────────────────────────────────────────────
        stage2_outcome = self._run_stage2(
            ticker,
            pipeline_version=pipeline_version,
            input_bundle_fingerprint=stage1_outcome.fingerprint,
            bust=("stage2_validate" in bust_set),
        )
        result.stages["stage2_validate"] = stage2_outcome
        if stage2_outcome.status == StageStatus.FAILED:
            for downstream in cascade_invalidation_targets("stage2_validate"):
                self._status_store.mark_skipped_dependency(
                    ticker, downstream, dependency_stage="stage2_validate",
                )
                result.stages[downstream] = StageOutcome(
                    stage=downstream,
                    status=StageStatus.SKIPPED_DEPENDENCY,
                    error_message=f"upstream stage2_validate failed",
                )
            result.finished_at = datetime.now(timezone.utc)
            return result

        # ── Stage 3 ────────────────────────────────────────────────
        stage3_outcome = self._run_stage3(
            ticker,
            pipeline_version=pipeline_version,
            records=stage2_outcome.payload,
            bust=("stage3_calculate" in bust_set),
        )
        result.stages["stage3_calculate"] = stage3_outcome
        if stage3_outcome.status == StageStatus.FAILED:
            for downstream in cascade_invalidation_targets("stage3_calculate"):
                self._status_store.mark_skipped_dependency(
                    ticker, downstream, dependency_stage="stage3_calculate",
                )
            result.finished_at = datetime.now(timezone.utc)
            return result

        # ── Stage 4 (opt-in) ───────────────────────────────────────
        if auto_agents:
            stage4_outcome = self._run_stage4(
                ticker,
                pipeline_version=pipeline_version,
                calc_bundle=stage3_outcome.payload,
                bust=("stage4_agents" in bust_set),
            )
            result.stages["stage4_agents"] = stage4_outcome

        result.finished_at = datetime.now(timezone.utc)
        return result

    # ── per-stage runners ───────────────────────────────────────────

    def _run_stage1(
        self,
        ticker: str,
        *,
        pipeline_version: str,
        force_refresh: bool,
        include_market_snapshot: bool,
        bust: bool,
    ) -> StageOutcome:
        stage = "stage1_ingest"
        # Capture prior status BEFORE mark_running overwrites it.
        # Cache-hit detection compares against the prior successful
        # state, not the in-flight RUNNING marker.
        prior = self._status_store.get(ticker, stage)
        self._status_store.mark_running(ticker, stage)
        t0 = time.perf_counter()
        try:
            bundle: IngestedRawBundle = run_stage1(
                ticker,
                pipeline_version=pipeline_version,
                force_refresh=force_refresh,
                include_market_snapshot=include_market_snapshot,
            )
        except Stage1IngestError as exc:
            duration = time.perf_counter() - t0
            self._status_store.mark_failed(
                ticker, stage,
                error_message=str(exc), duration_seconds=duration,
            )
            return StageOutcome(
                stage=stage, status=StageStatus.FAILED,
                duration_seconds=duration, error_message=str(exc),
            )

        duration = time.perf_counter() - t0
        # Cache-hit detection: if not bust and fingerprint matches
        # the last successful run, mark as cached. Stage 1's
        # fingerprint is content-addressed, so a match means every
        # source's payload bytes are unchanged.
        cached = self._is_cache_hit(
            prior, bundle.bundle_fingerprint, bust=bust,
        )
        if cached:
            self._status_store.mark_skipped_cached(
                ticker, stage, fingerprint=bundle.bundle_fingerprint,
            )
            return StageOutcome(
                stage=stage, status=StageStatus.SKIPPED_CACHED,
                fingerprint=bundle.bundle_fingerprint,
                duration_seconds=duration, payload=bundle,
            )

        self._status_store.mark_ok(
            ticker, stage,
            fingerprint=bundle.bundle_fingerprint,
            duration_seconds=duration,
            rows_processed=len(bundle.sources),
        )
        return StageOutcome(
            stage=stage, status=StageStatus.OK,
            fingerprint=bundle.bundle_fingerprint,
            duration_seconds=duration, payload=bundle,
        )

    def _run_stage2(
        self,
        ticker: str,
        *,
        pipeline_version: str,
        input_bundle_fingerprint: Optional[str],
        bust: bool,
    ) -> StageOutcome:
        stage = "stage2_validate"
        prior = self._status_store.get(ticker, stage)
        self._status_store.mark_running(ticker, stage)
        t0 = time.perf_counter()
        try:
            records: List[ValidatedCleanedRecord] = run_stage2(
                ticker=ticker,
                pipeline_version=pipeline_version,
                input_bundle_fingerprint=input_bundle_fingerprint,
            )
        except Stage2ValidateError as exc:
            duration = time.perf_counter() - t0
            self._status_store.mark_failed(
                ticker, stage,
                error_message=str(exc), duration_seconds=duration,
            )
            return StageOutcome(
                stage=stage, status=StageStatus.FAILED,
                duration_seconds=duration, error_message=str(exc),
            )

        duration = time.perf_counter() - t0
        # Stage-collection fingerprint: hash the anchor (latest FY)
        # record's fingerprint. That's what Stage 3 will read.
        if not records:
            self._status_store.mark_failed(
                ticker, stage,
                error_message="stage 2 produced zero records",
                duration_seconds=duration,
            )
            return StageOutcome(
                stage=stage, status=StageStatus.FAILED,
                duration_seconds=duration,
                error_message="stage 2 produced zero records",
            )
        anchor = max(records, key=lambda r: r.fiscal_year)
        stage_fp = anchor.record_fingerprint

        cached = self._is_cache_hit(prior, stage_fp, bust=bust)
        if cached:
            self._status_store.mark_skipped_cached(
                ticker, stage, fingerprint=stage_fp,
            )
            return StageOutcome(
                stage=stage, status=StageStatus.SKIPPED_CACHED,
                fingerprint=stage_fp,
                duration_seconds=duration, payload=records,
            )

        self._status_store.mark_ok(
            ticker, stage,
            fingerprint=stage_fp,
            duration_seconds=duration,
            rows_processed=len(records),
        )
        return StageOutcome(
            stage=stage, status=StageStatus.OK,
            fingerprint=stage_fp,
            duration_seconds=duration, payload=records,
        )

    def _run_stage3(
        self,
        ticker: str,
        *,
        pipeline_version: str,
        records: List[ValidatedCleanedRecord],
        bust: bool,
    ) -> StageOutcome:
        stage = "stage3_calculate"
        prior = self._status_store.get(ticker, stage)
        self._status_store.mark_running(ticker, stage)
        t0 = time.perf_counter()
        try:
            bundle: CalculationBundle = run_stage3(
                records, pipeline_version=pipeline_version,
            )
        except Stage3InputError as exc:
            duration = time.perf_counter() - t0
            self._status_store.mark_failed(
                ticker, stage,
                error_message=str(exc), duration_seconds=duration,
            )
            return StageOutcome(
                stage=stage, status=StageStatus.FAILED,
                duration_seconds=duration, error_message=str(exc),
            )

        duration = time.perf_counter() - t0
        cached = self._is_cache_hit(
            prior, bundle.bundle_fingerprint, bust=bust,
        )
        if cached:
            self._status_store.mark_skipped_cached(
                ticker, stage, fingerprint=bundle.bundle_fingerprint,
            )
            return StageOutcome(
                stage=stage, status=StageStatus.SKIPPED_CACHED,
                fingerprint=bundle.bundle_fingerprint,
                duration_seconds=duration, payload=bundle,
            )

        self._status_store.mark_ok(
            ticker, stage,
            fingerprint=bundle.bundle_fingerprint,
            duration_seconds=duration,
        )
        return StageOutcome(
            stage=stage, status=StageStatus.OK,
            fingerprint=bundle.bundle_fingerprint,
            duration_seconds=duration, payload=bundle,
        )

    def _run_stage4(
        self,
        ticker: str,
        *,
        pipeline_version: str,
        calc_bundle: CalculationBundle,
        bust: bool,
    ) -> StageOutcome:
        stage = "stage4_agents"
        prior = self._status_store.get(ticker, stage)
        self._status_store.mark_running(ticker, stage)
        t0 = time.perf_counter()
        try:
            bundle: AgentBundle = run_stage4(
                calc_bundle,
                pipeline_version=pipeline_version,
                agent_runner=self._agent_runner,
                runner_id=self._runner_id,
            )
        except Stage4AgentError as exc:
            duration = time.perf_counter() - t0
            self._status_store.mark_failed(
                ticker, stage,
                error_message=str(exc), duration_seconds=duration,
            )
            return StageOutcome(
                stage=stage, status=StageStatus.FAILED,
                duration_seconds=duration, error_message=str(exc),
            )

        duration = time.perf_counter() - t0
        cached = self._is_cache_hit(
            prior, bundle.bundle_fingerprint, bust=bust,
        )
        if cached:
            self._status_store.mark_skipped_cached(
                ticker, stage, fingerprint=bundle.bundle_fingerprint,
            )
            return StageOutcome(
                stage=stage, status=StageStatus.SKIPPED_CACHED,
                fingerprint=bundle.bundle_fingerprint,
                duration_seconds=duration, payload=bundle,
            )

        self._status_store.mark_ok(
            ticker, stage,
            fingerprint=bundle.bundle_fingerprint,
            duration_seconds=duration,
        )
        return StageOutcome(
            stage=stage, status=StageStatus.OK,
            fingerprint=bundle.bundle_fingerprint,
            duration_seconds=duration, payload=bundle,
        )

    # ── cache-hit detection ─────────────────────────────────────────

    @staticmethod
    def _is_cache_hit(
        prior, fingerprint: str, *, bust: bool,
    ) -> bool:
        """A cache hit means: not busted AND the prior status row
        records an identical fingerprint from a prior successful run.

        ``prior`` is the pre-mark_running snapshot — we must NOT
        re-read the store after mark_running, because RUNNING isn't
        a success state."""
        if bust:
            return False
        if prior is None:
            return False
        if prior.fingerprint != fingerprint:
            return False
        # Only ``ok`` / ``skipped_cached`` count as a prior success.
        return prior.status in (
            StageStatus.OK, StageStatus.SKIPPED_CACHED,
        )


__all__ = [
    "Orchestrator",
    "OrchestratorResult",
    "StageOutcome",
]
