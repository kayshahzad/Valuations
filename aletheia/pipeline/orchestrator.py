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

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

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
        provider: Optional[str] = None,
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
            provider: Data-source provider name (``"fmp"``, ``"xbrl"``,
                or ``"hybrid"``). When None, falls back to
                ``ALETHEIA_PROVIDER`` env var, then config default.
                Routes Stage 1 source allow-list (FMP skips SEC fetch)
                and Stage 2 record construction (FMP/Hybrid bypass
                cleaning_engine in favor of provider.to_validated_records).
        """
        # Resolve provider once at the top of the run so all stages
        # see the same value (env-var resolution can shift between
        # process invocations otherwise). Falls through to
        # ``config.data_source.DEFAULT_PROVIDER`` (hybrid) when the
        # env var isn't set.
        if provider is None:
            import os
            env_provider = os.environ.get("ALETHEIA_PROVIDER", "").strip().lower()
            if env_provider:
                provider = env_provider
            else:
                try:
                    from config.data_source import DEFAULT_PROVIDER
                    provider = str(DEFAULT_PROVIDER).lower()
                except ImportError:
                    provider = "hybrid"
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
            provider=provider,
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
            provider=provider,
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

        # ── Deterministic qualitative recompute ────────────────────
        # Runs after every successful Stage 3 so the 5 deterministic
        # qualitative dimensions (roiic_trend, buyback_discipline,
        # dividend_policy, cyclicality, industry_concentration) stay
        # in sync with the freshly-cleaned DB rows. The runner is
        # idempotent — fingerprint-matched records get skipped so
        # cached Stage 3 runs are near-free. Failures here don't
        # block the pipeline; qualitative is a downstream concern.
        self._run_qualitative_recompute(ticker)

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

    def _run_qualitative_recompute(self, ticker: str) -> None:
        """Run all deterministic qualitative computers and persist their
        results. Called after Stage 3 success. Idempotent; failures
        are logged-not-raised so a broken computer never blocks
        ingestion."""
        try:
            from aletheia.qualitative.runner import recompute_deterministic
            recompute_deterministic(ticker)
        except Exception as exc:
            logger.warning(
                "qualitative_recompute_failed ticker=%s error=%s: %s",
                ticker, type(exc).__name__, exc,
            )

    # ── per-stage runners ───────────────────────────────────────────

    def _run_stage1(
        self,
        ticker: str,
        *,
        pipeline_version: str,
        force_refresh: bool,
        include_market_snapshot: bool,
        bust: bool,
        provider: Optional[str] = None,
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
                provider=provider,
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
        provider: Optional[str] = None,
    ) -> StageOutcome:
        stage = "stage2_validate"
        prior = self._status_store.get(ticker, stage)
        self._status_store.mark_running(ticker, stage)
        t0 = time.perf_counter()
        try:
            # Provider-aware Stage 2 routing:
            #   xbrl / None  → legacy cleaning_engine path via run_stage2
            #   fmp / hybrid → provider.to_validated_records() bypass +
            #                  DB persistence via _persist_provider_records
            if provider in ("fmp", "hybrid"):
                records = self._run_stage2_via_provider(
                    ticker, provider=provider,
                    pipeline_version=pipeline_version,
                )
            else:
                records = run_stage2(
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

    def _run_stage2_via_provider(
        self,
        ticker: str,
        *,
        provider: str,
        pipeline_version: str,
    ) -> List[ValidatedCleanedRecord]:
        """B-2: Stage 2 records sourced from the provider abstraction
        rather than the legacy cleaning_engine path.

        For ``provider in {"fmp", "hybrid"}`` this bypasses the
        cleaning_engine entirely. The provider returns
        ``ValidatedCleanedRecord`` directly (FmpProvider already
        applies sign conventions + derived-field computation; the
        hybrid path additionally surfaces XBRL specialty tags via
        ``get_companyfact``). Records are persisted to DuckDB via
        the ``CleanedRecord`` adapter so all DB-backed UI views
        (Pipeline Explorer, Dashboard, Universe, etc.) see the
        provider-flavoured data — same DB schema, different source.

        Provider records carry a ``validation.fmp_validation['source']``
        tag identifying which provider produced them. The pipeline
        version stamped on each record includes the provider name
        for downstream attribution.
        """
        from aletheia.providers import get_provider
        prov = get_provider(provider)
        records = prov.to_validated_records(ticker)
        if not records:
            raise Stage2ValidateError(
                f"Provider {provider!r} returned no records for {ticker!r}. "
                "Check FMP cache + API availability."
            )

        # Re-stamp each record's ``pipeline_version`` so downstream
        # audit / cache-hit logic ties this run to the orchestrator's
        # version string (provider's default is its own internal tag).
        # ValidatedCleanedRecord is pydantic-frozen, so use
        # ``.model_copy(update=...)`` to produce a new instance.
        records = [
            r.model_copy(update={"pipeline_version": pipeline_version})
            for r in records
        ]

        # Adapt + persist each ValidatedCleanedRecord to DuckDB. The
        # CleanedRecord adapter strips ValidatedCleanedRecord-only
        # metadata (validation receipt, fingerprints) and keeps the
        # data dicts the legacy DB schema understands. Quality
        # scoring + flags default to provider-source-pass; the
        # underlying provider already validated the records on its
        # side before we got them.
        self._persist_provider_records(records, provider=provider)
        return records

    def _persist_provider_records(
        self,
        records: List[ValidatedCleanedRecord],
        *,
        provider: str,
    ) -> None:
        """B-3: write provider-sourced records to the DB.

        Each ``ValidatedCleanedRecord`` becomes a ``CleanedRecord``
        with the same raw/clean/derived blobs and an empty flag set.
        The DB's ``upsert_record`` schema-contract gate still runs;
        records that violate Tier-C identities raise (matching
        legacy behaviour).
        """
        from aletheia.data.cleaning_engine import CleanedRecord
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        try:
            for vr in records:
                cr = CleanedRecord(
                    ticker=vr.ticker,
                    fiscal_year=vr.fiscal_year,
                    period_end_date=vr.period_end_date,
                    period=vr.period,
                    raw=dict(vr.raw),
                    clean=dict(vr.clean),
                    derived=dict(vr.derived),
                    # 3.2: carry the validation receipt through to persist so
                    # upsert_record writes a real fmp_validation_status instead of
                    # defaulting to 'not_run'. Holds the Gate A receipt (legacy
                    # clean path) or the provider's own — whichever produced it.
                    fmp_validation=dict(
                        getattr(getattr(vr, "validation", None), "fmp_validation", {}) or {}),
                )
                # Best-effort persist — schema-contract violations
                # raise CalculationError; we swallow per-record errors
                # so a single bad year doesn't kill the run, but mark
                # the failure on stderr for the operator.
                try:
                    db.upsert_record(cr)
                except Exception as exc:  # noqa: BLE001
                    import sys
                    print(
                        f"  ⚠ persist failed: {vr.ticker} FY{vr.fiscal_year} "
                        f"{vr.period} ({provider}): {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
        finally:
            db.close()

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
