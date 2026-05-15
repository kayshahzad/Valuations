"""Pipeline stage contracts — Pydantic schemas for the four boundary bundles.

The 8-week pipeline-separation refactor (see docs/pipeline_contracts.md)
decouples ingestion, validation, calculation, and agent execution into
discrete stages. Each stage's input and output is a typed contract
defined in this module:

    Stage 1 (ingest)    → IngestedRawBundle
    Stage 2 (validate)  → ValidatedCleanedRecord
    Stage 3 (calculate) → CalculationBundle
    Stage 4 (agents)    → AgentBundle

These are skeleton definitions delivered in Week 1 for stakeholder
review. The fields capture what each stage produces today (scattered
across CleanedRecord, DCFResult, agent_runs.serving_json, etc.); the
Week 2-6 extractions wire the actual stages to produce these contracts
as their typed outputs.

Design principles (cf. docs/pipeline_contracts.md):

  - Each bundle carries its own fingerprint (sha256 of inputs + code
    version) so the next stage can detect unchanged inputs and skip work.
  - Each bundle carries `input_*_fingerprint` linking it to the previous
    stage's output. Lets cascading invalidation cleanly propagate.
  - Each bundle carries `pipeline_version` (the git SHA of the code
    that produced it) so methodology refinements bust cache correctly.
  - Stage 1 output points to file paths for large raw payloads; the
    bundle itself stays small enough to query in DuckDB.
  - Stage 2-4 outputs are serialisable to JSON and queryable. The
    cleaning_engine's existing CleanedRecord shape (raw/clean/derived
    dicts) is preserved in ValidatedCleanedRecord; the refactor
    formalises the contract without restructuring the underlying data.

Boundary discipline is enforced via tests/architecture/test_pipeline_
layering.py — stage modules can only import from earlier-stage modules.
This is the structural defense against validation logic creeping back
into ingestion, calc logic creeping back into validation, etc.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────
# Stage 1 — Ingestion
# ─────────────────────────────────────────────────────────────────────

class RawSource(BaseModel):
    """A single raw payload from one external source.

    Stage 1's job is to capture what each source returned, byte-faithful,
    with full provenance. No interpretation, no cleaning, no validation.
    Large payloads (XBRL companyfacts, FMP statement responses) live as
    files on disk; this record carries the path + a content hash so the
    next stage can detect changes.
    """
    model_config = {"frozen": True}

    source: str = Field(
        description=(
            "Source identifier. Canonical values: 'sec_companyfacts', "
            "'fmp_key_metrics', 'fmp_income', 'fmp_cashflow', "
            "'fmp_balance_sheet', 'fmp_ratios_ttm', 'fmp_enterprise_values', "
            "'fmp_income_as_reported_quarter', 'fmp_profile', "
            "'market_price', 'market_beta'."
        ),
    )
    url: str = Field(description="Canonical URL the data was fetched from.")
    fetched_at: datetime = Field(description="UTC timestamp of the fetch.")
    payload_path: Path = Field(
        description=(
            "On-disk location of the raw payload. Convention: "
            "valuation_data/raw/{source_root}/{ticker}/{endpoint}_{date}.json"
        ),
    )
    payload_sha256: str = Field(
        description=(
            "SHA-256 of the raw payload bytes. Used by Stage 1 to detect "
            "source-level changes (re-fetch returns same hash → no Stage 2 "
            "re-run needed)."
        ),
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Source-specific metadata. Examples: CIK for SEC; query params "
            "for FMP; bar interval for market data. Kept generic so new "
            "sources don't require contract changes."
        ),
    )


class IngestedRawBundle(BaseModel):
    """Stage 1 output. Authoritative record of what each source returned
    for a ticker at a specific moment in time.

    Idempotency: a re-fetch that returns identical content (every source's
    payload_sha256 unchanged) produces an identical bundle_fingerprint and
    Stage 2 can skip work.

    Source change detection differs per source (cf. docs/pipeline_contracts.md
    "Stage 1 source change detection"):
      - SEC: cheap to poll filing date; refetch when local cache is older
      - FMP: 24h TTL by default (no free 'last updated' endpoint)
      - Market data: always fresh on each ingest
      - `--force` always refetches
    """
    model_config = {"frozen": True}

    ticker: str
    bundle_fingerprint: str = Field(
        description=(
            "SHA-256 of (sorted source identifiers + their payload_sha256s "
            "+ ticker + pipeline_version). Stable across identical re-fetches."
        ),
    )
    fetched_at: datetime = Field(
        description=(
            "Earliest fetch time across all sources. For source-by-source "
            "freshness, see sources[*].fetched_at."
        ),
    )
    sources: Dict[str, RawSource] = Field(
        description=(
            "Keyed by source identifier (see RawSource.source). All sources "
            "needed by Stage 2 for a complete ticker should be present; "
            "missing-required-source is a Stage 1 failure, not Stage 2."
        ),
    )
    classification_snapshot: Dict[str, Any] = Field(
        description=(
            "Snapshot of config/ticker_classification at fetch time: "
            "{ticker, sector, industry, lifecycle, business_model, "
            "is_ifrs_filer, notes}. Persisted so historical bundles record "
            "the classification that was in effect when they were ingested."
        ),
    )
    pipeline_version: str = Field(
        description="Git SHA of the code that produced this bundle.",
    )


# ─────────────────────────────────────────────────────────────────────
# Stage 2 — Validation and Cleaning
# ─────────────────────────────────────────────────────────────────────

class ValidationReceipt(BaseModel):
    """Outcomes from running validation gates on a ticker's data."""
    model_config = {"frozen": True}

    schema_violations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Output of validate_cleaned_record_schema_contract. Each "
            "violation has category / field / value / expected / message. "
            "Empty list = record passed every check."
        ),
    )
    fmp_validation: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Gate A.TTM cross-source receipt: status (validated|drift|"
            "blocking_drift|skipped), per-field drift band, ttm_source."
        ),
    )
    cross_source_agreement: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Field-level SEC-vs-FMP agreement scores. Reserved for future "
            "expansion; today this captures the existing fmp_validation "
            "output only."
        ),
    )
    overrides_applied: List[str] = Field(
        default_factory=list,
        description=(
            "Keys from aletheia.calculations.OVERRIDES that were active "
            "during cleaning/validation for this ticker. Lets downstream "
            "consumers know which checks were relaxed and why."
        ),
    )


class ValidatedCleanedRecord(BaseModel):
    """Stage 2 output. Cleaned, validated data for one (ticker, period)
    pair, ready for Stage 3 (calculation) consumption.

    Preserves the existing CleanedRecord shape (raw/clean/derived dicts)
    so the refactor doesn't restructure the underlying data — only the
    contract becomes explicit. The cleaning_engine remains the authority
    on what goes in each namespace.

    Foreign-filer FX conversion (ASML/TSM): happens in Stage 2 per the
    design decision in docs/pipeline_contracts.md. Raw values in the
    IngestedRawBundle stay in reported currency; this record's raw/clean/
    derived dicts contain USD-converted values, with the FX rate used
    captured in validation.fmp_validation['fx_converted'] /
    ['reported_currency'].
    """
    model_config = {"frozen": True}

    ticker: str
    fiscal_year: int
    period: Literal["FY", "TTM", "Q1", "Q2", "Q3", "Q4"]
    period_end_date: str = Field(description="ISO date string YYYY-MM-DD")

    raw: Dict[str, Optional[float]] = Field(
        description=(
            "Raw XBRL/FMP facts as ingested, with the cleaning engine's "
            "tag-resolution applied (canonical field names) but no "
            "derivation. Mirrors CleanedRecord.raw."
        ),
    )
    clean: Dict[str, Optional[float]] = Field(
        description=(
            "Normalized values: sign conventions applied, units harmonised, "
            "FX-converted if foreign filer. Mirrors CleanedRecord.clean."
        ),
    )
    derived: Dict[str, Optional[float]] = Field(
        description=(
            "Computed values: NOPAT, NormalizedEBIT, EBITDA, NetDebt, ROIC, "
            "ROE, margin %s, depreciation total, etc. Mirrors "
            "CleanedRecord.derived."
        ),
    )

    raw_blob_json: Optional[str] = Field(
        default=None,
        description=(
            "Opaque JSON-string passthrough of the cleaner's full "
            "``record.raw`` dict (including fields not materialised as "
            "DB columns). ScreeningEngine and MultipleDecomposition "
            "read sub-fields out of this blob via ``_get_json``. Kept "
            "separate from the typed ``raw`` dict so Stage 3 can "
            "round-trip without expanding blob entries into additional "
            "DataFrame columns (which would change engine behaviour by "
            "populating fields the direct path leaves unpopulated)."
        ),
    )
    clean_blob_json: Optional[str] = Field(
        default=None,
        description="Opaque JSON-string passthrough of ``record.clean``. See ``raw_blob_json``.",
    )

    overall_quality_score: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Composite quality score [0, 1] from cleaning_engine's domain "
            "scoring. 1.0 = clean, 0.0 = unusable. Mirrors "
            "CleanedRecord.overall_quality_score."
        ),
    )
    cleaning_warnings: List[str] = Field(default_factory=list)
    blocking_errors: List[str] = Field(default_factory=list)

    validation: ValidationReceipt

    record_fingerprint: str = Field(
        description=(
            "SHA-256 of (input_bundle_fingerprint + cleaning_engine version "
            "+ override registry state). Identical when nothing material "
            "has changed; busts when ingest data changes OR when cleaning "
            "logic / overrides change."
        ),
    )
    input_bundle_fingerprint: str = Field(
        description=(
            "Points to the IngestedRawBundle that produced this record. "
            "Lets the orchestrator detect cascade invalidation."
        ),
    )
    cleaned_at: datetime
    pipeline_version: str


# ─────────────────────────────────────────────────────────────────────
# Stage 3 — Calculation
# ─────────────────────────────────────────────────────────────────────

class CalculationBundle(BaseModel):
    """Stage 3 output. Every deterministic analytical output for one ticker.

    The current calc layer (DCFEngine, ReverseDCF, MultipleDecomposition,
    ScreeningEngine, MoatFingerprint, cyclicality, scenario_eval_node)
    produces these as separate artefacts scattered through agent_runs.
    serving_json. This bundle aggregates them into a single typed output.

    Each sub-result remains a typed dataclass internally; this bundle
    stores them as dicts (via .to_dict()) for serialisation. The decision
    to keep them as dicts here, rather than nested Pydantic models, is
    deliberate: it avoids tight coupling between the contract and the
    internal calc-layer dataclass shapes, which churn more frequently
    than the boundary contract should.

    Schema-contract violations from the calc layer (e.g., implied_cagr
    output sanity, IPS plausibility) are captured separately so callers
    can distinguish "input was clean but calc produced garbage" from
    "input was already bad."
    """
    model_config = {"frozen": True}

    ticker: str
    fiscal_year: int = Field(description="The fiscal year used as the base for projection.")
    base_period: Literal["FY", "TTM"] = Field(
        description=(
            "Which period anchored the calc. Most functions use latest FY; "
            "reverse_dcf is hard-coded to FY (see docs/calculation_safety.md "
            "and the MDT incident). Some functions (DCFEngine) prefer TTM "
            "when available."
        ),
    )

    dcf: Dict[str, Any] = Field(description="DCFResult.to_dict() output.")
    reverse_dcf: Dict[str, Any]
    multiple_decomposition: Dict[str, Any]
    screening: Dict[str, Any] = Field(description="ScreeningCard.to_dict() output.")
    moat_fingerprint: Dict[str, Any]
    cyclicality: Dict[str, Any] = Field(
        description="z_score, is_peak, applies_cyclical_haircut, avg_3yr."
    )
    scenarios: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="scenario_eval_node results — bull/bear/base_alternative.",
    )
    capital_structure: Dict[str, Any] = Field(default_factory=dict)
    reality_checks: Dict[str, Any] = Field(default_factory=dict)
    accounting_identities: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Stage 3 output of the seven accounting identity audits "
            "(balance sheet, RE rollforward, cash rollforward, PP&E "
            "rollforward, debt rollforward, working-capital reconciliation, "
            "FCF pathway). Shape: {ticker, results: [...], summary: "
            "{n_checks, n_passed, n_failed, n_skipped, failed_identities}, "
            "known_limitations: [...]}. See "
            "aletheia/calculations/identity_checks.py."
        ),
    )
    upstream_inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "L2 V2 — flattened raw+clean+derived dict from the anchor "
            "FY record. Lets the derivation-registry's runtime trace "
            "(trace_value) resolve cleaning-side input names that don't "
            "appear in the engine outputs (OperatingCF, CapEx_Total, "
            "NetIncome, TotalEquity, etc.). Source-of-truth is still "
            "the underlying ValidatedCleanedRecord; this is a convenience "
            "echo so UI / agents reading the bundle don't have to re-join."
        ),
    )
    prior_year_inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "L2 V3a — flattened raw+clean+derived from the FY-1 record "
            "(prior to the anchor). Lets the trace resolver handle "
            "pair-rollforward inputs: ``Cash_beg`` reads ``Cash`` from "
            "prior_year_inputs; ``Cash_end`` reads ``Cash`` from "
            "upstream_inputs. Same source-of-truth caveat as upstream_inputs."
        ),
    )
    config_inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "L2 V3 — date-aware config constants (equity_risk_premium, "
            "statutory tax rate, etc.) consulted by the trace resolver "
            "as the last tier before falling back to ``<from upstream>``. "
            "Lets WACC and similar derivations show the actual ERP "
            "value that fed the calc."
        ),
    )

    schema_violations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Calc-layer violations: implied_cagr out of band, IPS implausible, "
            "TV multiple out of [3, 50], premium_pct out of [-90%, +1000%]. "
            "Distinct from Stage 2's schema_violations (which are about "
            "input data quality)."
        ),
    )

    bundle_fingerprint: str
    input_record_fingerprint: str = Field(
        description="Points to the ValidatedCleanedRecord that produced this.",
    )
    computed_at: datetime
    pipeline_version: str


# ─────────────────────────────────────────────────────────────────────
# Stage 4 — Agent Execution
# ─────────────────────────────────────────────────────────────────────

class AgentBundle(BaseModel):
    """Stage 4 output. All LLM-derived qualitative analysis for one ticker.

    The current agent path (librarian → qualitative_synthesis →
    scenario_eval_node → contrarian_v2 → thesis_synthesizer) produces
    these as state-dict entries flowing through the LangGraph. After the
    refactor, agents become a stage like any other with fingerprint cache
    and status tracking.

    Stage 4 has a higher unit cost (LLM dollars) than other stages, which
    is why the orchestrator's chain stops at Stage 3 by default. Operators
    opt in to Stage 4 via --auto-agents or by running `aletheia agents
    <ticker>` explicitly.

    llm_cost_usd is captured per-run so operators can see the cumulative
    cost of agent re-runs over time. The orchestrator's status registry
    uses this to surface "stale agents — recompute would cost $X" prompts.
    """
    model_config = {"frozen": True}

    ticker: str

    qualitative_synthesis: Dict[str, Any] = Field(
        description=(
            "Output of qualitative_synthesis agent: forensic_report, "
            "value_chain_report, strategic_context_report."
        ),
    )
    contrarian: Dict[str, Any] = Field(
        description="contrarian_v2 output: bias detection, bear case, sentiment.",
    )
    thesis: Dict[str, Any] = Field(
        description=(
            "thesis_synthesizer output: thesis_statement, bull/base/bear "
            "cases with cited_signals, decision_conditions, conviction tier."
        ),
    )
    raw_10k_excerpt: Optional[str] = Field(
        default=None,
        description=(
            "Librarian agent's 10-K extract (first 60k chars). Captured "
            "here for forensic traceability — lets us inspect what text the "
            "LLMs were actually given. Kept separate from qualitative_"
            "synthesis output so it doesn't bloat that dict."
        ),
    )

    bundle_fingerprint: str
    input_calculation_fingerprint: str
    computed_at: datetime
    pipeline_version: str
    llm_cost_usd: Optional[float] = Field(
        default=None,
        description="Estimated cost of LLM calls for this run.",
    )


# ─────────────────────────────────────────────────────────────────────
# Cross-stage: status registry
# ─────────────────────────────────────────────────────────────────────

class StageStatus(str, Enum):
    """Status values for a (ticker, stage) row in the pipeline_status
    table."""
    PENDING = "pending"           # ticker registered, stage not yet attempted
    RUNNING = "running"           # currently executing
    OK = "ok"                     # successful, output cached
    FAILED = "failed"             # explicit error; details in error_message
    SKIPPED_CACHED = "skipped_cached"           # inputs unchanged, cache hit
    SKIPPED_DEPENDENCY = "skipped_dependency"   # earlier stage failed; cannot run
    STALE_DUE_TO_OVERRIDE = "stale_due_to_override"  # registry changed; recompute needed


class PipelineStatusRow(BaseModel):
    """One row in the pipeline_status table.

    Schema is intentionally narrow: ticker + stage + status + fingerprint
    + timestamps + error_message + duration. Operators query this via the
    `aletheia pipeline status` CLI to see the universe state at a glance.
    """
    model_config = {"frozen": False}  # mutable so the orchestrator can update in place

    ticker: str
    stage: Literal["stage1_ingest", "stage2_validate", "stage3_calculate", "stage4_agents"]
    status: StageStatus
    fingerprint: Optional[str] = None
    last_run_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None
    rows_processed: Optional[int] = Field(
        default=None,
        description="For Stage 2 (per-period records); None for stages that run once per ticker.",
    )


# ─────────────────────────────────────────────────────────────────────
# Cross-stage: cascade invalidation helper (skeleton)
# ─────────────────────────────────────────────────────────────────────

def cascade_invalidation_targets(stage: str) -> List[str]:
    """When ``stage`` busts (input changed, override changed, code SHA
    changed), return the list of downstream stages whose caches must
    also be invalidated.

    Used by the orchestrator after a successful re-run to mark
    downstream PipelineStatusRow entries as STALE_DUE_TO_OVERRIDE.
    """
    DOWNSTREAM = {
        "stage1_ingest":     ["stage2_validate", "stage3_calculate", "stage4_agents"],
        "stage2_validate":   ["stage3_calculate", "stage4_agents"],
        "stage3_calculate":  ["stage4_agents"],
        "stage4_agents":     [],
    }
    return DOWNSTREAM.get(stage, [])


__all__ = [
    # Stage 1
    "RawSource",
    "IngestedRawBundle",
    # Stage 2
    "ValidationReceipt",
    "ValidatedCleanedRecord",
    # Stage 3
    "CalculationBundle",
    # Stage 4
    "AgentBundle",
    # Status
    "StageStatus",
    "PipelineStatusRow",
    # Helpers
    "cascade_invalidation_targets",
]
