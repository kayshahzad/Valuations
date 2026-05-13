# Pipeline Performance — Methodology + Baseline

How the four-stage pipeline performs end-to-end, the methodology used
to measure it, and the regression-tracking targets that govern future
optimization.

See [docs/pipeline_contracts.md](pipeline_contracts.md) for the
architecture this doc measures. See
[tests/perf/test_pipeline_perf.py](../tests/perf/test_pipeline_perf.py)
for the measurement code, and
[docs/perf_baselines/pipeline_perf.json](perf_baselines/pipeline_perf.json)
for the machine-readable baseline.

## Methodology

Per the locked Week 7 decision in
[pipeline_contracts.md](pipeline_contracts.md):

- **Sample tickers**: `NVDA`, `MDT`, `AAPL`, `COST`, `TSLA` — chosen
  to span lifecycle types (secular hyper-growth, mature, growth
  compounder, consumer staple, cyclical). The full 25-ticker universe
  is exercised by
  [`tests/pipeline/test_universe_run.py`](../tests/pipeline/test_universe_run.py)
  for end-to-end correctness; the perf sample is a deliberately
  smaller working set for tight feedback loops.
- **Cache states measured**:
    - **Cold**: every stage runs from scratch (`force_refresh=True`).
      The Stage 1 fetcher's disk cache is still warm in practice; the
      "cold" path here measures everything the orchestrator does
      *given* fresh-on-disk source bytes.
    - **Warm**: the prior fingerprint matches → every stage short-
      circuits via `SKIPPED_CACHED`. Stage 1's fetch still runs (it
      has to to discover the fingerprint) but Stages 2-3 don't
      re-execute.
    - **Targeted bust (Stage 3 only)**: `--bust-cache stage3` —
      simulates the methodology-change scenario (WACC tweak,
      terminal-growth bound shift). Cascade-invalidation forces
      Stage 4 if active; Stages 1 and 2 stay cached.
- **Hardware**: developer workstation, single-threaded. The numbers
  here are not "production SLA" — they exist to catch regressions
  on the order of 2-5× from baseline.
- **Persistence**: per-test temp DuckDB so runs don't touch the
  production `pipeline_status` table.

## Targets

| Cache state | Target / ticker | Rationale |
|---|---|---|
| Cold | ≤ 110s | The pre-refactor end-to-end run takes ~100s/ticker including LLM agents. The refactor's Stage 1+2+3 (no LLM) budget is the pre-refactor calc-layer slice plus 10% overhead. |
| Warm | < 5s | Sub-5s is the threshold under which a Streamlit ticker switch feels instant. |
| Stage 3 only | ≤ 30s | A methodology re-run (WACC tweak) should finish in under half a minute, sized to be re-runnable across the universe in a coffee break. |

Test enforcement is **soft warn at target, hard fail at 5× target**.
Dev workstations vary in clock speed; the test exists to catch
order-of-magnitude regressions, not micro-variation.

## Baseline (recorded 2026-05-13)

Median across the 5 sample tickers, post-Week-6 orchestrator:

| Metric | Median | Min | Max | vs target |
|---|---|---|---|---|
| Cold | 4.82s | 4.12s | 5.75s | **23× under** the 110s target |
| Warm | 0.71s | 0.56s | 0.77s | **7× under** the 5s target |
| Stage 3 targeted bust | 0.70s | 0.55s | 0.77s | **43× under** the 30s target |

Per-ticker breakdown:

| Ticker | Cold (s) | Warm (s) | Stage 3 bust (s) |
|---|---|---|---|
| NVDA | 4.82 | 0.74 | 0.70 |
| MDT  | 4.12 | 0.56 | 0.55 |
| AAPL | 4.94 | 0.76 | 0.77 |
| COST | 5.75 | 0.71 | 0.68 |
| TSLA | 4.12 | 0.70 | 0.70 |

The headroom is the consequence of three design decisions:

1. **Stage 1's fetcher disk cache stays hot**. The orchestrator's
   "cold" run still benefits from the existing FMP + SEC raw-payload
   caches. Real cold-from-zero (clearing the disk cache) takes
   minutes per ticker (FMP rate-limits at 250 calls/day on the free
   tier; SEC's RATE_LIMIT_S adds 100ms per request). The orchestrator
   contributes ~0.5s of its own overhead on top of cleaning + calc.
2. **Cleaning + calc are CPU-bound and small**. The 10-domain cleaner
   runs in ~1s per FY; the four calc engines together run in ~1s.
3. **Cache-hit short-circuits the typed-record build**. When Stage 2's
   fingerprint matches, we don't even materialise the
   `ValidatedCleanedRecord` list — the orchestrator records
   `SKIPPED_CACHED` and moves on. Stage 1's fetch (cache validate)
   is therefore the bulk of warm-path latency.

## Regression tracking

The perf test writes
[`docs/perf_baselines/pipeline_perf.json`](perf_baselines/pipeline_perf.json)
on every run. To diff against a prior baseline:

```bash
# After making a change, re-run:
python -m pytest tests/perf/test_pipeline_perf.py -q
# Inspect the JSON for shifts:
jq '.summary' docs/perf_baselines/pipeline_perf.json
```

Future versions should:

- Commit the baseline JSON only when targets shift intentionally
  (i.e., methodology changed and slower-is-acceptable).
- Add CI gating: fail the build if any sample ticker exceeds 2× the
  current baseline median.
- Track real-cold-from-zero numbers separately (with cleared FMP +
  SEC caches) once the Week 8 operational runbook documents the
  procedure.

## What this doc does not cover

- **LLM cost / latency**. Stage 4 timing depends on which agent_runner
  is wired; the baseline above measures the Stage-3-stop path only.
  Operators using `--auto-agents` should expect 30-90s additional
  latency and a per-run cost surfaced through `AgentBundle.llm_cost_usd`.
- **Cross-ticker concurrency**. The pipeline runs one ticker at a
  time, stages in series (locked decision #13 in
  [pipeline_contracts.md](pipeline_contracts.md)). Universe-sweep
  performance scales linearly with ticker count;
  [`tests/pipeline/test_universe_run.py`](../tests/pipeline/test_universe_run.py)
  exercises 25 tickers in ~53s sequentially.
- **Memory footprint**. The orchestrator holds one `OrchestratorResult`
  per active call; the underlying calc engines have their own caches
  (yfinance, fmp_client) but no orchestrator-level retention. If
  memory becomes a constraint we'd benchmark separately.
