# Pipeline Operations — Runbook

How to run, troubleshoot, and maintain the four-stage pipeline in
production. Companion to [docs/pipeline_contracts.md](pipeline_contracts.md)
(architecture) and [docs/pipeline_performance.md](pipeline_performance.md)
(benchmarking).

## CLI reference

The pipeline exposes per-stage CLIs and a unified orchestrator CLI.
Pick the granularity that fits the task.

### Per-stage CLIs

Each module also ships as a stand-alone CLI. Useful for debugging
one stage in isolation, surgical re-runs, or when you want to
inspect a typed bundle's JSON before piping it elsewhere.

```bash
# Stage 1 — fetch raw bytes from SEC + FMP + market data
python -m aletheia.cli.ingest NVDA
python -m aletheia.cli.ingest NVDA --force-refresh
python -m aletheia.cli.ingest NVDA --no-market-snapshot      # offline
python -m aletheia.cli.ingest NVDA --source sec_companyfacts --source fmp_income

# Stage 2 — clean + validate
python -m aletheia.cli.validate NVDA
python -m aletheia.cli.validate NVDA --fiscal-year 2024 --fiscal-year 2023

# Stage 3 — run all deterministic calc engines
python -m aletheia.cli.calc NVDA
python -m aletheia.cli.calc NVDA --fiscal-year 2024

# Each command emits the typed bundle (Pydantic) as JSON to stdout.
# Pipe to `jq` for inspection:
python -m aletheia.cli.calc NVDA | jq '.dcf.wacc'
```

### Orchestrator CLI

```bash
# Full pipeline (stages 1-3) for one ticker
python -m aletheia.cli.pipeline run NVDA

# Including Stage 4 (LLM agents — opt-in, incurs $)
python -m aletheia.cli.pipeline run NVDA --auto-agents

# Universe sweep
python -m aletheia.cli.pipeline run --all

# Targeted re-run (methodology change in calc layer)
python -m aletheia.cli.pipeline run NVDA --bust-cache stage3

# Full re-run, all caches busted
python -m aletheia.cli.pipeline run NVDA --force-refresh

# Status queries
python -m aletheia.cli.pipeline status              # universe matrix
python -m aletheia.cli.pipeline status NVDA         # per-ticker breakdown
```

**`--bust-cache` syntax**: comma-separated stage names, no spaces.
Short and long forms both accepted:

| Short  | Long                |
|--------|---------------------|
| stage1 | stage1_ingest       |
| stage2 | stage2_validate     |
| stage3 | stage3_calculate    |
| stage4 | stage4_agents       |

`--bust-cache stage1,stage2` busts both AND cascade-invalidates Stage
3 and (if active) Stage 4 — per the locked invalidation policy in
[pipeline_contracts.md](pipeline_contracts.md) decision #4.

## Failure-mode triage tree

When a ticker fails, walk this tree:

```
aletheia pipeline status <TICKER>
    │
    ├── stage1_ingest: FAILED
    │     ├── error mentions "CIK"        → SEC ticker→CIK map outdated;
    │     │                                  add CIK_OVERRIDES entry in
    │     │                                  aletheia/data/edgar_client.py
    │     ├── error mentions "403"        → SEC blocks the client's IP
    │     │                                  (cloud server?); re-run from
    │     │                                  a local machine
    │     ├── error mentions "FMP"        → FMP quota exhausted or API
    │     │                                  key missing; check FMP_API_KEY
    │     │                                  env var and free-tier limits
    │     └── empty `sources` dict        → likely yfinance flake; re-run
    │                                       with --no-market-snapshot
    │
    ├── stage2_validate: FAILED
    │     ├── "zero records"              → canonical parquet missing for
    │     │                                  the ticker; run Stage 1 first
    │     │                                  with --force-refresh
    │     ├── "Gate A blocked"            → FMP cross-check drift; inspect
    │     │                                  audits/guard_violations_*.jsonl
    │     │                                  with `jq '.ticker == "TICKER"'`
    │     └── identity violations         → tag-resolver gap; check
    │                                       docs/calculation_anomaly_catalog.md
    │                                       for the matching anomaly entry
    │
    ├── stage3_calculate: SKIPPED_DEPENDENCY  → upstream failed; fix that first
    │
    ├── stage3_calculate: OK but schema_violations non-empty
    │     → DCF/MD/ReverseDCF raised inside the engine; check
    │       `bundle.schema_violations` for the engine+category. Common:
    │       NotImplementedError("routing_required") for NEE/JPM/BRK-B
    │       is expected behaviour — DDM/bank models aren't FCFF-compatible.
    │
    └── stage4_agents: FAILED
          → LLM API error or agent_runner exception. Check the
            error_message; if transient, re-run with --auto-agents.
```

## Cache invalidation cookbook

When to bust which stage:

| You changed... | Bust... | Why |
|---|---|---|
| `aletheia/data/cleaning_engine.py` | `stage2,stage3` | Cleaning logic; calc consumes its output |
| `aletheia/calculations/_overrides.py` (added/removed entry) | nothing — automatic | Override-state hash is folded into Stage 2's fingerprint; the cascade fires automatically when registry changes |
| `aletheia/tools/dcf_engine.py` (WACC formula, terminal-growth, etc.) | `stage3` | Calc methodology change; ingest + cleaning unchanged |
| `aletheia/tools/screening_ratios.py` | `stage3` | Same as above |
| `config/ticker_classification.py` (lifecycle / sector tweak) | `stage3` | Classification feeds DCFEngine's lifecycle dispatch |
| SEC filed a new 10-K | `stage1` | Source bytes changed; cascade-busts stage2 + 3 |
| FMP backfilled historical statements | `stage1` | Same |
| LLM prompt template change | `stage4 --auto-agents` | Only Stage 4 affected |
| Pipeline version bump (any code change in calc/agent layer) | nothing — automatic | `pipeline_version` is folded into every stage's fingerprint; same code SHA = same fingerprint, different SHA = automatic cascade |

## `ALETHEIA_GUARD_MODE` kill switch

The calc-validation framework has four modes. Set via env var; takes
effect on the next call (no restart needed).

| Mode | Behaviour | Use when |
|---|---|---|
| `off` | All guards are no-ops; legacy fallback behaviour preserved | Emergency: validation framework is masking a different bug and you need it out of the way |
| `shadow` | Guards log structured warnings to `audits/guard_violations_*.jsonl`; never raise | Default for production; collect signal without blocking pipeline |
| `soft` | Same as shadow + UI Data Quality panel reads the persisted violations | Operator wants UI surfacing |
| `hard` | Guards raise `CalculationError`; caller's try/except decides next step | Per-function override (currently `reverse_dcf.run` is hard by default — see [docs/calculation_safety.md](calculation_safety.md) §"Per-function override") |

```bash
# Disable framework everywhere (emergency only)
ALETHEIA_GUARD_MODE=off python -m aletheia.cli.pipeline run NVDA

# Production default (no env var set)
python -m aletheia.cli.pipeline run NVDA
```

## When to use `--auto-agents`

Stage 4 invokes the LLM agents (qualitative_synthesis, contrarian_v2,
thesis_synthesizer). Each ticker incurs ~$1-3 in LLM costs and ~30-90
seconds of latency. The orchestrator stops at Stage 3 by default
(locked decision #14 in [pipeline_contracts.md](pipeline_contracts.md)).

**Use `--auto-agents` when**:
- An analyst is actively reviewing the ticker and needs the synthesis.
- The thesis is being prepared for an investment-committee meeting.
- A material 10-K update warrants new qualitative analysis.

**Do NOT use `--auto-agents` when**:
- Running a universe sweep for parity testing.
- Iterating on calc-methodology changes (Stage 4 outputs don't depend
  on WACC/terminal-growth tweaks; re-running them burns budget).
- The previous Stage 4 run is still recent and unchanged — check
  `aletheia pipeline status <TICKER>` first.

The cost is tracked per-run in `AgentBundle.llm_cost_usd`. Future
versions will surface cumulative cost in the status registry.

## Behaviour change for direct CLI users

The pre-refactor entry point (`run_valuation.py` / `aletheia/workflow/
graph.py:create_workflow()`) implicitly ran the entire chain including
Stage 4 agents. The new `aletheia pipeline run` **stops at Stage 3**
unless you pass `--auto-agents`. Anyone who scripted around the old
implicit behaviour must add the flag:

```bash
# Old behaviour
python run_valuation.py NVDA          # ran agents implicitly

# Equivalent new behaviour
python -m aletheia.cli.pipeline run NVDA --auto-agents
```

The `create_workflow()` function now emits a `DeprecationWarning` on
each call. Per the locked 6-month deprecation window (decision #3),
the function is removed when the last caller migrates — track
remaining callers via `grep -r "create_workflow" --include="*.py" .`.

## Status registry queries

The orchestrator persists per-(ticker, stage) state in the
`pipeline_status` DuckDB table. Read it programmatically:

```python
from aletheia.contracts.pipeline import StageStatus
from aletheia.pipeline.status_store import PipelineStatusStore

with PipelineStatusStore() as store:
    # "Which tickers failed Stage 3 in the last 24h?"
    failures = store.get_by_stage_status(
        "stage3_calculate", StageStatus.FAILED,
    )
    for r in failures:
        print(f"{r.ticker} {r.last_run_at} {r.error_message}")

    # "Has NVDA's full chain run successfully today?"
    for r in store.get_for_ticker("NVDA"):
        print(f"  {r.stage:<18} {r.status.value:<14} "
              f"fp={r.fingerprint[:12]}... "
              f"last_success={r.last_success_at}")
```

Or via the CLI:

```bash
python -m aletheia.cli.pipeline status                # universe matrix
python -m aletheia.cli.pipeline status NVDA           # per-ticker
```

## Onboarding checklist

A new engineer joining the calc/data layer should:

1. Read [docs/pipeline_contracts.md](pipeline_contracts.md) for the
   architecture.
2. Read [docs/calculation_safety.md](calculation_safety.md) for the
   validation framework + override-registry conventions.
3. Read [docs/sign_conventions.md](sign_conventions.md) for the
   Tier 1/2/3 schema.
4. Run `python -m aletheia.cli.pipeline run NVDA` and inspect the
   typed bundle (`aletheia calc NVDA | jq` shows the JSON shape).
5. Run `python -m pytest tests/pipeline/` to see the contract +
   parity tests pass.
6. Skim [docs/pipeline_performance.md](pipeline_performance.md) for
   the timing envelope.

## Out of scope for this runbook

- **LLM prompts + agent layer internals**: see `aletheia/agents/`
  and the (in-flight) qualitative-synthesis docs.
- **DuckDB schema migrations**: see `aletheia/data/database.py`
  schema-init block.
- **UI** (Streamlit dashboard): see `aletheia/ui/`.
- **Backtesting**: see `aletheia/backtest/`.
