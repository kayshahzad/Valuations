# Pipeline Stage Contracts

**Status**: Week 1 deliverable — design ready for stakeholder review. No
code has moved yet; this document defines the target architecture that
Weeks 2-8 will implement.

## Why this exists

The current pipeline couples data ingestion, validation, calculation,
and agent execution into a single LangGraph workflow. This produces
specific testability + debugging problems:

- Data quality issues surface late (during calculation or even agent
  execution) rather than at ingestion.
- Methodology refinements (e.g., WACC clipping, terminal-growth cap)
  require universe-wide re-runs that re-fetch + re-clean unchanged data.
- Failures in one stage cascade to subsequent stages without explicit
  failure boundaries.
- Scaling agents independently of calculation is hard because they're
  executed in the same workflow pass.

The refactor preserves the calc-as-truth discipline while enabling
near-term initiatives (document analysis, methodology experimentation,
identification automation) and longer-term scaling (independent
deployment, point-in-time analysis, backtesting infrastructure).

## The four stages

```
┌──────────────────────┐    ┌──────────────────────┐
│  Stage 1: Ingest     │    │  Stage 2: Validate   │
│  Raw fetch, no       │ →  │  Clean + cross-check │
│  interpretation.     │    │  Apply overrides.    │
│  Output: bytes + URLs│    │  Output: typed       │
│  + provenance.       │    │  validated records.  │
└──────────────────────┘    └──────────────────────┘
            ↓                          ↓
┌──────────────────────┐    ┌──────────────────────┐
│  Stage 3: Calculate  │    │  Stage 4: Agents     │
│  DCF, reverse-DCF,   │ →  │  Qualitative, thesis │
│  multiples, screen,  │    │  synthesizer, etc.   │
│  moat, scenarios.    │    │  (LLM cost gated)    │
│  Output: typed       │    │  Output: typed       │
│  analytical bundle.  │    │  agent bundle.       │
└──────────────────────┘    └──────────────────────┘
```

Each stage's input and output is a **typed contract** defined in
`aletheia/contracts/pipeline.py`:

| Stage | Output contract | Persistence |
|---|---|---|
| 1 — Ingest | `IngestedRawBundle` | Files on disk + DuckDB bundle index |
| 2 — Validate | `ValidatedCleanedRecord` | DuckDB `company_records` (existing) |
| 3 — Calculate | `CalculationBundle` | DuckDB `calculation_outputs` (new) |
| 4 — Agents | `AgentBundle` | DuckDB `agent_runs` (existing) |

## Stage interface contracts in detail

Authoritative source: [aletheia/contracts/pipeline.py](../aletheia/contracts/pipeline.py).
The schemas summarised below mirror what is locked there as of Week 1
delivery. Field descriptions, validators, and `Literal` enumerations
live in the code; this section is a navigational summary.

Every bundle shares four cross-cutting fields:

- `ticker` — string identifier (one bundle per ticker per run).
- `*_fingerprint` — SHA-256 of (inputs + code version), used for cache
  hits and cascade-invalidation detection.
- `input_*_fingerprint` — points at the previous stage's
  `*_fingerprint`, so the orchestrator can trace lineage.
- `pipeline_version` — git SHA of the code that produced the bundle.

### `IngestedRawBundle` (Stage 1 output)

| Field | Type | Notes |
|---|---|---|
| `ticker` | `str` | |
| `bundle_fingerprint` | `str` | SHA-256(sorted source ids + payload_sha256s + ticker + pipeline_version) |
| `fetched_at` | `datetime` (UTC) | Earliest fetch across sources |
| `sources` | `Dict[str, RawSource]` | Keyed by canonical source id (e.g. `sec_companyfacts`, `fmp_income`) |
| `classification_snapshot` | `Dict[str, Any]` | Snapshot of `config/ticker_classification` at fetch time |
| `pipeline_version` | `str` | |

`RawSource` (one per external source within the bundle):

| Field | Type | Notes |
|---|---|---|
| `source` | `str` | Canonical id; see [aletheia/contracts/pipeline.py:65-72](../aletheia/contracts/pipeline.py#L65-L72) for the locked list |
| `url` | `str` | Canonical URL the data was fetched from |
| `fetched_at` | `datetime` | UTC |
| `payload_path` | `Path` | On-disk location of raw payload (see "Persistence layout" below) |
| `payload_sha256` | `str` | Content hash used for source-change detection |
| `metadata` | `Dict[str, Any]` | Source-specific (CIK, query params, bar interval, etc.) |

### `ValidatedCleanedRecord` (Stage 2 output)

| Field | Type | Notes |
|---|---|---|
| `ticker` | `str` | |
| `fiscal_year` | `int` | |
| `period` | `Literal["FY", "TTM", "Q1", "Q2", "Q3", "Q4"]` | |
| `period_end_date` | `str` | ISO date `YYYY-MM-DD` |
| `raw` | `Dict[str, Optional[float]]` | Tag-resolved facts, no derivation (mirrors `CleanedRecord.raw`) |
| `clean` | `Dict[str, Optional[float]]` | Normalised: signs, units, FX-converted for ASML/TSM |
| `derived` | `Dict[str, Optional[float]]` | NOPAT, EBITDA, NetDebt, ROIC, margins, etc. |
| `overall_quality_score` | `float [0.0, 1.0]` | Composite quality score from cleaning_engine |
| `cleaning_warnings` | `List[str]` | |
| `blocking_errors` | `List[str]` | |
| `validation` | `ValidationReceipt` | See sub-schema below |
| `record_fingerprint` | `str` | SHA-256(input_bundle_fingerprint + cleaning_engine version + override state) |
| `input_bundle_fingerprint` | `str` | Lineage pointer |
| `cleaned_at` | `datetime` | |
| `pipeline_version` | `str` | |

`ValidationReceipt`:

| Field | Type | Notes |
|---|---|---|
| `schema_violations` | `List[Dict[str, Any]]` | Output of `validate_cleaned_record_schema_contract` |
| `fmp_validation` | `Dict[str, Any]` | Gate A.TTM receipt; also carries `fx_converted` / `reported_currency` |
| `cross_source_agreement` | `Dict[str, Any]` | SEC-vs-FMP field agreement (currently captures fmp_validation output only) |
| `overrides_applied` | `List[str]` | Override-registry keys active during cleaning |

### `CalculationBundle` (Stage 3 output)

| Field | Type | Notes |
|---|---|---|
| `ticker` | `str` | |
| `fiscal_year` | `int` | Base year for projection |
| `base_period` | `Literal["FY", "TTM"]` | reverse_dcf is hard-coded FY (MDT incident); DCFEngine prefers TTM when available |
| `dcf` | `Dict[str, Any]` | `DCFResult.to_dict()` |
| `reverse_dcf` | `Dict[str, Any]` | |
| `multiple_decomposition` | `Dict[str, Any]` | |
| `screening` | `Dict[str, Any]` | `ScreeningCard.to_dict()` |
| `moat_fingerprint` | `Dict[str, Any]` | |
| `cyclicality` | `Dict[str, Any]` | z_score, is_peak, applies_cyclical_haircut, avg_3yr |
| `scenarios` | `List[Dict[str, Any]]` | `scenario_eval_node` results (bull/bear/base_alternative) |
| `capital_structure` | `Dict[str, Any]` | |
| `reality_checks` | `Dict[str, Any]` | |
| `schema_violations` | `List[Dict[str, Any]]` | Calc-layer violations (output sanity, IPS implausibility, etc.) — distinct from Stage 2 violations |
| `bundle_fingerprint` | `str` | |
| `input_record_fingerprint` | `str` | Lineage pointer |
| `computed_at` | `datetime` | |
| `pipeline_version` | `str` | |

Sub-results are stored as `Dict[str, Any]` (via the internal dataclasses'
`.to_dict()`), not nested Pydantic models. Rationale: the boundary
contract shouldn't churn every time a calc-layer dataclass refactors.

### `AgentBundle` (Stage 4 output)

| Field | Type | Notes |
|---|---|---|
| `ticker` | `str` | |
| `qualitative_synthesis` | `Dict[str, Any]` | forensic_report, value_chain_report, strategic_context_report |
| `contrarian` | `Dict[str, Any]` | contrarian_v2 output: bias detection, bear case, sentiment |
| `thesis` | `Dict[str, Any]` | thesis_synthesizer output: bull/base/bear cases with cited_signals, decision_conditions, conviction |
| `raw_10k_excerpt` | `Optional[str]` | Librarian's 10-K extract (first 60k chars) — kept separate so it doesn't bloat the synthesis dict |
| `bundle_fingerprint` | `str` | |
| `input_calculation_fingerprint` | `str` | Lineage pointer |
| `computed_at` | `datetime` | |
| `pipeline_version` | `str` | |
| `llm_cost_usd` | `Optional[float]` | Estimated cost — surfaced in status registry prompts |

### Status registry contracts

`StageStatus` enum values (see [aletheia/contracts/pipeline.py:400-409](../aletheia/contracts/pipeline.py#L400-L409)):
`pending`, `running`, `ok`, `failed`, `skipped_cached`,
`skipped_dependency`, `stale_due_to_override`.

`PipelineStatusRow` is mutable (the orchestrator updates in place) and
carries: ticker, stage, status, fingerprint, last_run_at,
last_success_at, error_message, duration_seconds, rows_processed.

### Cascade invalidation helper

`cascade_invalidation_targets(stage)` returns the downstream stages
whose caches must be invalidated when `stage` busts. Lookup table:

| Stage | Downstream targets |
|---|---|
| `stage1_ingest` | `stage2_validate`, `stage3_calculate`, `stage4_agents` |
| `stage2_validate` | `stage3_calculate`, `stage4_agents` |
| `stage3_calculate` | `stage4_agents` |
| `stage4_agents` | *(none)* |

## Stage 1 — Ingestion

**Job**: Capture what each source returned for a ticker at a specific
moment in time. Byte-faithful, with full provenance. No interpretation,
no cleaning, no validation.

### Sources

Canonical source identifiers (matched against `RawSource.source`):

- `sec_companyfacts` — SEC EDGAR XBRL companyfacts JSON for one CIK
- `fmp_key_metrics`, `fmp_income`, `fmp_cashflow`, `fmp_balance_sheet`,
  `fmp_ratios_ttm`, `fmp_enterprise_values`, `fmp_income_as_reported_quarter`,
  `fmp_profile` — Financial Modeling Prep responses
- `market_price`, `market_beta` — live market data snapshots

### Persistence layout

`{date}` is the ISO-8601 calendar date (`YYYY-MM-DD`) of the fetch in
UTC, derived from `RawSource.fetched_at`. No timestamps in file names —
multiple fetches on the same UTC day overwrite, with the bundle row in
DuckDB recording the most recent `fetched_at`.

```
valuation_data/raw/
├── sec/companyfacts/CIK{0-padded-10-digit}.json
├── fmp/{ticker}/key_metrics_{YYYY-MM-DD}.json
├── fmp/{ticker}/income_statement_{YYYY-MM-DD}.json
├── fmp/{ticker}/cashflow_statement_{YYYY-MM-DD}.json
├── ...
└── market/{ticker}/price_{YYYY-MM-DD}.json
```

Each file is content-addressed: refetch that returns identical content
yields the same `payload_sha256` and the same `bundle_fingerprint`.

### Source change detection

Differs per source (cost-aware):

| Source | Detection | Rationale |
|---|---|---|
| `sec_companyfacts` | Source-driven (poll filing date) | One cheap HTTP call to compare local cache's last filing date against SEC's most recent |
| `fmp_*` | Time-based (24h TTL) | FMP has no free "last updated" endpoint; quarterly data is FMP's internal aggregation |
| `market_*` | Real-time | Always fresh; lightweight |
| Manual override | `--force` always refetches | For methodology debugging |

### Currency

Stage 1 stores **reported currency**. Foreign-filer (ASML, TSM) values
remain in EUR/TWD here. FX conversion is Stage 2's job (see below).

### Idempotency

`bundle_fingerprint` = SHA-256 of (sorted source identifiers + each
source's `payload_sha256` + ticker + `pipeline_version`). Stable across
identical refetches.

## Stage 2 — Validation and Cleaning

**Job**: Consume Stage 1's raw bundle, produce a cleaned + validated
record ready for Stage 3 (calculation) consumption.

### What Stage 2 owns

- The cleaning_engine (10-domain normalisation)
- The ingestion_validator (FieldContract pattern)
- The fmp_validation Gate A.TTM (cross-source cross-check)
- The schema_contract validation framework (Phase 0-8 work)
- The override registry consultation (anomaly catalog entries)
- **Foreign-filer FX conversion** (per design decision below)

### Output structure

`ValidatedCleanedRecord` preserves the existing `CleanedRecord` shape
(`raw`, `clean`, `derived` dicts) so the refactor doesn't restructure
underlying data — only the contract becomes explicit. The cleaning_engine
remains the authority on what goes in each namespace.

Validation outcomes are captured in a `ValidationReceipt` block on the
record: `schema_violations`, `fmp_validation`, `cross_source_agreement`,
`overrides_applied`.

### FX conversion boundary — decision

**Decision**: Foreign-filer FX conversion happens in Stage 2, not Stage 1.

**Rationale**:
1. Stage 1's contract is "what the source returned." Persisting USD-
   converted values there mixes raw with interpretation.
2. Rate selection is methodology — FY-avg vs spot vs quarter-avg are
   different choices. Future methodology changes should re-run Stage 2
   only, not re-fetch Stage 1.
3. The framework's idempotency model is cleaner: changing the FX rate
   source busts the Stage 2 fingerprint, not Stage 1's.

Stage 2 records the FX rate used in `validation.fmp_validation` so the
conversion is forensically observable. Canonical filers: ASML (EUR),
TSM (TWD). New foreign filers added later inherit the same path.

### Idempotency

`record_fingerprint` = SHA-256 of (`input_bundle_fingerprint` + cleaning_
engine version + override registry state). Identical when nothing
material has changed; busts when:

- Ingest data changes (Stage 1's `bundle_fingerprint` changed)
- Cleaning logic changes (cleaning_engine code SHA changed)
- Override registry changes (override added/removed/modified)

## Stage 3 — Calculation

**Job**: Consume `ValidatedCleanedRecord`, run all deterministic
financial calculations, produce `CalculationBundle`.

### Functions in Stage 3

- `DCFEngine.run` — three-scenario FCFF DCF
- `ReverseDCF.run` — implied-CAGR solver
- `MultipleDecomposition.run` — NorthWestern EV/EBITDA + P/Sales decomp
- `ScreeningEngine.score` — 34-metric scorecard
- `MoatFingerprint.compute_moat_fingerprint` — 5-factor moat score
- `cyclicality.calculate_z_score`
- `capital_structure` analysis
- `scenario_eval_node` — scenario IPS evaluation
- Reality checks

### What Stage 3 does NOT do

- Re-validate inputs (Stage 2 is the authority; the schema_contract
  framework's Stage 2 receipt is the proof)
- Consult the override registry (Stage 2 already applied overrides;
  cleaned data already reflects them)
- Make LLM calls (Stage 4 territory)

### Output structure

`CalculationBundle` aggregates each sub-result via `.to_dict()`. The
decision to store dicts rather than nested Pydantic models is
deliberate: the boundary contract shouldn't churn with every internal
calc-layer dataclass refactor.

Calc-layer schema-contract violations (implied_cagr out of band, IPS
implausible, TV multiple out of [3, 50], premium_pct out of band) are
captured separately in `schema_violations`. These distinguish "input
was clean but calc produced garbage" from Stage 2's "input was already
bad."

### Idempotency

`bundle_fingerprint` = SHA-256 of (`input_record_fingerprint` + calc
code SHA). Bust on input data change OR calc methodology change.

## Stage 4 — Agent Execution

**Job**: Consume `CalculationBundle`, produce LLM-derived qualitative
analysis.

### Functions in Stage 4

- `librarian_agent` — 10-K text fetch
- `qualitative_synthesis_agent` — forensic + value_chain + strategic
  context
- `contrarian_v2` — bias detection + bear case
- `thesis_synthesizer` — bull/base/bear cases with cited_signals

### LLM cost gating

Stage 4 has higher unit cost (LLM dollars). The orchestrator's
**chain stops at Stage 3 by default**. Operators opt into Stage 4
explicitly:

```
aletheia pipeline run NVDA --auto-agents   # full chain including agents
aletheia pipeline run NVDA                  # stops after stage 3
aletheia agents NVDA                        # run only stage 4 on cached calc
```

`AgentBundle.llm_cost_usd` tracks per-run cost. The status registry
surfaces "stale agents — recompute would cost $X" prompts so operators
make informed decisions.

**Behaviour change for current CLI users**: today's `run_valuation` /
LangGraph entry points run the full chain including agents implicitly.
After cutover, `aletheia pipeline run NVDA` stops at Stage 3 — anyone
who relied on the implicit agent run must add `--auto-agents`. This
change is intentional (default-safe re: LLM cost) and is called out in
the Week 8 operational runbook, the deprecation warning emitted by the
compat wrapper, and the cutover changelog entry.

### Formalisation parity — decision

**Decision**: Stage 4 is formalised consistently with stages 1-3.

Same fingerprint/cache pattern. Same `PipelineStatusRow` schema. Same
`--bust-cache` semantics. The existing `agent_runs` table becomes Stage
4's persistence layer with no schema changes.

The current LangGraph wrapping individual agents stays inside Stage 4
as an implementation detail — Stage 4's contract is `(CalculationBundle)
→ AgentBundle`, irrespective of how that's wired internally.

## Cross-stage concerns

### Schema-contract enforcement — decision

**Decision**: Architecture lock test + type-system constraints, not
code review discipline alone.

Implementation:

1. `IngestedRawBundle` is a Pydantic model with no validation methods —
   only `fetched_at`, `source_url`, `payload_path` fields. There's no
   API surface a developer can call to "validate during ingestion."
2. `tests/architecture/test_pipeline_layering.py` (Week 1 scaffold)
   asserts via AST analysis that:
   - `aletheia.pipeline.stage1_ingest` does not import from
     `aletheia.calculations`, `aletheia.data.cleaning_engine`, etc.
   - `aletheia.pipeline.stage2_validate` does not import from
     `aletheia.tools.dcf_engine`, etc.
   - `aletheia.pipeline.stage3_calculate` does not import from
     `aletheia.agents` etc.
3. Tests skip until the corresponding stage module lands; once it
   does, the import boundary is enforced at PR time.

### Cache invalidation semantics — decision

Different stages have different costs and different invalidation
triggers. The CLI requires explicit per-stage flag — there is no
`--bust-all` shortcut.

| Stage | `--bust-cache` cost | Typical trigger |
|---|---|---|
| Stage 1 | High (SEC + FMP rate limits, network) | Source data update; suspected ingest bug |
| Stage 2 | Low (deterministic) | Cleaning engine refactor; override registry change |
| Stage 3 | Low (deterministic) | Methodology change (WACC, terminal growth, etc.) |
| Stage 4 | High (LLM dollars) | Prompt change; thesis architecture refactor |

**CLI convention for multi-stage values**: a single `--bust-cache` flag
with a comma-separated list of stage names (no spaces). Repeated-flag
syntax is not supported. The same convention applies to any future
flag that accepts a list of stages.

```
aletheia pipeline run NVDA --bust-cache stage3                 # single stage
aletheia pipeline run NVDA --bust-cache stage1,stage2          # multiple stages
aletheia pipeline run NVDA --bust-cache stage4 --auto-agents   # rerun thesis only
```

### Override registry cascade

Adding, changing, or removing an override entry in
`aletheia.calculations.OVERRIDES` triggers cascade invalidation. The
exact mechanism is the fingerprint chain — Stage 2's `record_fingerprint`
includes a hash of the override-registry state (specifically: the
serialised dict of override keys + reason hashes for tickers active in
the run). When the registry changes:

1. The next Stage 2 run for each affected ticker computes a different
   `record_fingerprint` than the cached value — Stage 2 cache misses,
   re-runs, and writes a new record.
2. Stage 3 sees `input_record_fingerprint` no longer matches its cached
   `bundle_fingerprint` input, cache-misses, re-runs.
3. Stage 4 sees `input_calculation_fingerprint` mismatch the same way,
   cache-misses, re-runs only if Stage 4 was requested (`--auto-agents`
   or explicit `aletheia agents`).
4. Operators see the cascade via `pipeline_status` — affected tickers
   transition `OK` → `STALE_DUE_TO_OVERRIDE` until a re-run completes.

**Override-registry scope rule**: an override entry's cascade is
limited to its `tickers` list. A new override for a single ticker does
not invalidate the rest of the universe. A "global" override (entry
applies to all tickers) cascades universe-wide — these should be rare
and require explicit operator awareness.

**Cross-reference**: [docs/calculation_safety.md](calculation_safety.md)
documents the per-function side of the override registry (writing/
reviewing entries, size discipline, `review_by_date` policy). It does
**not** yet describe the pipeline-architecture cascade behaviour
documented in this section. Week 8 deliverable: add a "Pipeline cascade
behaviour" subsection to `calculation_safety.md` pointing back here, so
the override registry doc is the single landing page for both authoring
and cache-invalidation semantics.

### Compat wrapper for `aletheia.workflow.graph`

**Decision**: Permanent compat wrapper initially, with explicit 6-month
deprecation timeline.

Timeline:
- Week 6 (orchestration): `create_workflow()` becomes a thin wrapper
  that internally calls the new orchestrator.
- Week 8 (cutover): `DeprecationWarning` is emitted from
  `create_workflow()` on each call.
- 6 months post-cutover: function deleted. No hard deadline beyond
  that — the team monitors deprecation-warning logs and deletes when
  the last caller migrates.

This prevents both ongoing maintenance burden and breaking-change
surprise.

### Mode resolution (calc-validation framework)

The `ALETHEIA_GUARD_MODE` kill switch (default `shadow`) still applies
to Stage 2 + Stage 3. Function-level overrides (e.g., `reverse_dcf` is
hard) are unchanged. The refactor doesn't touch the framework's mode
machinery — it just makes the stage boundaries that consume those
guards explicit.

### Concurrent execution policy

The pipeline executes **one ticker at a time, stages in series**.
`aletheia pipeline run --all` iterates the universe sequentially. No
cross-ticker concurrency, no within-ticker stage parallelism, no
worker pool. Rationale: the current per-ticker latency (~100s cold,
sub-5s warm) is acceptable for the universe size, and serial execution
makes the audit log and `pipeline_status` table trivially consistent —
no row-update races, no partial-bundle ordering questions.

Future versions may add cross-ticker concurrency (e.g., a process pool
over the universe with per-ticker bundles isolated by fingerprint).
That is **out of scope for this refactor** and would require its own
design pass — at minimum: rate-limit coordination for SEC/FMP, atomic
status-registry updates, and a parity story for the LLM-cost ledger.

### Pipeline status registry

A new DuckDB table `pipeline_status` with one row per (ticker, stage):

```sql
CREATE TABLE pipeline_status (
    ticker VARCHAR NOT NULL,
    stage VARCHAR NOT NULL,        -- stage1_ingest | stage2_validate | ...
    status VARCHAR NOT NULL,       -- StageStatus enum
    fingerprint VARCHAR,
    last_run_at TIMESTAMP,
    last_success_at TIMESTAMP,
    error_message VARCHAR,
    duration_seconds DOUBLE,
    rows_processed INTEGER,
    PRIMARY KEY (ticker, stage)
);
CREATE INDEX idx_pipeline_status_stage_status ON pipeline_status(stage, status);
```

Operator queries supported:

- "Which tickers failed Stage 3 in the last 24h?"
- "Which tickers have stale Stage 4 outputs after the recent override change?"
- "What's the success rate for Stage 1 across the universe?"

CLI surface: `aletheia pipeline status` returns a matrix. `aletheia
pipeline status NVDA` returns a per-ticker breakdown.

## Operational concerns (Week 6/8 deliverables)

### Per-stage CLI

```
aletheia ingest NVDA          # stage 1 only
aletheia validate NVDA        # stage 2 only (requires stage 1 fresh)
aletheia calc NVDA            # stage 3 only (requires stage 2 fresh)
aletheia agents NVDA          # stage 4 only (requires stage 3 fresh)

aletheia pipeline run NVDA                     # stages 1-3, no agents
aletheia pipeline run NVDA --auto-agents       # stages 1-4
aletheia pipeline run --all                    # universe, stages 1-3

aletheia pipeline status                       # universe matrix
aletheia pipeline status NVDA                  # per-ticker breakdown
```

### Operational runbook

`docs/pipeline_operations.md` (Week 8 deliverable) includes:

- Per-stage CLI reference with examples
- Failure-mode triage tree ("Stage 3 failed for X tickers → check
  `pipeline_status` → check `audits/guard_violations_*.jsonl` → run
  with `--debug` for stack trace")
- Cache invalidation cookbook for common scenarios
- When to use `--auto-agents` flag (LLM-cost decisions)
- `ALETHEIA_GUARD_MODE` kill switch usage

### Performance benchmarking methodology

`docs/pipeline_performance.md` (Week 7 deliverable) captures:

- Hardware: developer workstation (current dev environment)
- Sample tickers: NVDA, MDT, AAPL, COST, TSLA (5 representative; full
  universe for parity tests)
- Cache states: cold (full re-run), warm (no changes), targeted bust
  (Stage 3 only)
- Targets:
  - Cold cache: ≤ current 100s/ticker + 10% overhead acceptable
  - Warm cache (full chain skipped): <5s/ticker
  - Targeted bust (Stage 3 only): ≤30s/ticker
- Regression tracking: numbers recorded in the doc for future comparison

### Contract testing

Per-stage contract tests land alongside each stage extraction:

- `tests/pipeline/test_stage3_contract.py` — Week 2 (calc first)
- `tests/pipeline/test_stage1_contract.py` — Week 4
- `tests/pipeline/test_stage2_contract.py` — Week 5
- `tests/pipeline/test_stage4_contract.py` — Week 6

Each verifies: (a) stage produces output matching the contract schema,
(b) stage rejects malformed input, (c) stage's output is consumable by
the next stage's input contract.

## Sequencing decision (locked)

**Hybrid**: A11 stabilization (2 days) precedes the refactor; A14 and
A19 fold into Weeks 4-5 as part of Stage 1/Stage 2 extraction.

Rationale:
- A11 is the most systematic stabilization remaining (`or 0.21` pattern
  in 5 calc functions). Doing it pre-refactor avoids carrying the bug
  forward into new code.
- A14 (V shares ingest) is naturally Stage 1 work (FMP fallback resolver).
- A19 (NEE utility taxonomy) is Stage 1 + Stage 2 work.
- Most of what could be called "stabilization" is already done via the
  Phase 0-8 calc-validation framework (CapEx sign, terminal-value guard,
  ratio fallback flagging, schema-contract framework + 114 tests).

## Migration order (locked)

**Stage 3 → Stage 1 → Stage 2** (small reorder from the original
calc → validation → ingestion suggestion).

Rationale:
- Stage 3 (calc) is most-isolated, fastest to validate against existing
  outputs. Starting here proves the pattern works.
- Stage 1 (ingest) is most-tangled (raw fetches happen inside
  cleaning_engine via edgar_client). Separating it second surfaces the
  boundary issues that validation refactor needs to resolve.
- Stage 2 (validation) lands last with both Stage 1 (raw inputs) and
  Stage 3 (validated consumers) formalised — cleaner contracts to fit
  between them.

## Week-by-week deliverables (locked)

| Week | Deliverable |
|---|---|
| 1 | **(this document)** + `aletheia/contracts/pipeline.py` + `tests/architecture/test_pipeline_layering.py` scaffold |
| 1.5 (interlude) | **A11 stabilization** — replace `or 0.21` with company FY effective rate across 5 calc functions; ~2 days |
| 2 | **Stage 3 extraction**: `aletheia/pipeline/stage3_calculate.py` + `aletheia/cli/calc.py`; `tests/pipeline/test_stage3_contract.py` |
| 3 | **Stage 3 verification**: parity tests on 25-ticker regression universe |
| 4 | **Stage 1 extraction**: `aletheia/pipeline/stage1_ingest.py` + `aletheia/cli/ingest.py`; **A14 fix** (V shares FMP fallback); `tests/pipeline/test_stage1_contract.py` |
| 5 | **Stage 2 extraction**: `aletheia/pipeline/stage2_validate.py` + `aletheia/cli/validate.py`; **A19 fix** (NEE utility taxonomy mapping); `tests/pipeline/test_stage2_contract.py` |
| 6 | **Orchestration + status registry** + **Stage 4 formalisation**: `aletheia/pipeline/orchestrator.py`, `aletheia/cli/pipeline.py`, `pipeline_status` table; `tests/pipeline/test_stage4_contract.py` |
| 7 | **Universe migration + parity verification**: run full new pipeline on 25-ticker universe, compare each stage's output against current `agent_runs` payloads, cutover; performance benchmarking |
| 8 | **Cleanup + docs**: thin compat wrapper for `workflow/graph`, `docs/pipeline_operations.md`, `docs/pipeline_performance.md`, onboarding updates |

## Decision summary

**Type** = the load-bearing weight of the decision:
- **Architectural** = changes the shape of the system; reversing requires another refactor.
- **Operational default** = a sensible starting point; can be re-tuned later
  without a refactor (e.g., flip a flag, edit a CLI default, adjust a TTL).

| # | Decision | Type | Lock state |
|---|---|---|---|
| 1 | FX conversion in Stage 2 with documented rationale | Architectural | Locked |
| 2 | Architecture lock test for boundary enforcement | Architectural | Locked + scaffolded |
| 3 | Permanent compat wrapper for `workflow/graph` with 6-month deprecation | Operational default | Locked |
| 4 | Per-stage cache invalidation semantics (explicit, no `--bust-all`) | Architectural | Locked |
| 5 | Override registry changes cascade-invalidate downstream stages | Architectural | Locked |
| 6 | Stage 4 formalised consistently with Stages 1-3 | Architectural | Locked |
| 7 | Contract testing per stage, landing alongside extraction | Architectural | Locked |
| 8 | Operational runbook as explicit Week 8 deliverable | Operational default | Locked |
| 9 | Performance benchmarking methodology in Week 7 | Operational default | Locked |
| 10 | Source change detection per source type (SEC source-driven, FMP 24h time-based, market real-time) | Operational default | Locked |
| 11 | Hybrid stabilization sequencing (A11 first, A14/A19 folded) | Operational default | Locked |
| 12 | Migration order: Stage 3 → Stage 1 → Stage 2 → Stage 4 | Operational default | Locked |
| 13 | Serial execution (one ticker at a time, no concurrency) | Operational default | Locked |
| 14 | Default-off agent execution in `aletheia pipeline run` (behaviour change vs. today) | Architectural | Locked |

## What this document does not lock

- Specific function signatures inside each stage module — those evolve
  during extraction.
- Internal DCFEngine/ReverseDCF restructuring — out of scope per
  "methodology stays identical."
- Document analysis tab, new validator sources, dashboard redesign,
  agent architecture changes — all explicit out-of-scope items.

## Open items for stakeholder review

None at the time of Week 1 delivery (post-review) — all fourteen
decisions above are locked. Subsequent weeks may surface new items that need decisions
(e.g., specific behaviors when Stage 1 detects a source change mid-run,
or how the orchestrator handles a partial-success batch).

Each surfaced item gets added to a "decision log" appendix below as it
arises, with the decision + rationale captured at the time it was
made.

## Decision log appendix

*(Empty at Week 1 delivery. Each subsequent week appends new items.)*
