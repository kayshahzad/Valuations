# Pipeline UI — Design + Build Plan

Two distinct UI surfaces over the post-refactor pipeline. Designed to
ship as one workstream but stay **architecturally separate**: own
files, own API endpoints (mostly shared), own state. Either can be
shipped or deprecated independently of the other.

## The two views and who they're for

### View 1 — Stage Explorer (per-ticker depth)

**File**: `aletheia/ui/pipeline_explorer_view.py`
**Primary user**: analyst doing deep verification
**Workflow**: pick a ticker, step through Stage 1 → 2 → 3 → optional
Stage 4, validate each stage's output before proceeding.
**Information density**: HIGH per stage. Each card expands to the
typed bundle + a validation panel that surfaces "is this output
trustworthy?"

This is the **primary tool for Phase B deep verification** (KO, NVDA,
ASML, JPM, ABT walks). It's also where Category A/B/C/D triage
happens after the identity audit flags something.

### View 2 — Status Matrix (universe glance)

**File**: `aletheia/ui/pipeline_status_view.py`
**Primary user**: operator / engineer monitoring health
**Workflow**: see all 40 tickers × 4 stages at once. Spot failures.
Bulk-trigger re-runs. Drill into one ticker → switches to Stage
Explorer.
**Information density**: LOW per cell, HIGH coverage. Status colours,
timestamps, drift indicators. No bundle JSON at this level.

This is the **operational dashboard** — useful for the engineer
running universe sweeps, the analyst checking which tickers got
stale after an override-registry change, the on-call rotation.

### The hard division

These two views **do not share rendering code**. They share the API
endpoints below and the underlying orchestrator/status_store, but
each `.py` file is self-contained, focused on its own user model.
Mixing the two — putting universe-matrix rows inside the stage
explorer, or stuffing per-stage JSON viewers inside the matrix —
makes both views worse. Keep them separate.

## Shared backend (FastAPI endpoints)

All under tag `Pipeline`. Located in `api_main.py` to match the
existing pattern. None of these supersede `POST /pipeline/run/
{ticker}` (the legacy LangGraph entry that calls `main.py` via
subprocess) — that stays until the workflow/graph compat wrapper
window closes.

```
POST /pipeline/stages/{ticker}/ingest
       body: { force_refresh?: bool, sources?: list[str] }
       → IngestedRawBundle (typed JSON)

POST /pipeline/stages/{ticker}/validate
       body: { input_bundle_fingerprint?: str, fiscal_years?: list[int] }
       → list[ValidatedCleanedRecord]

POST /pipeline/stages/{ticker}/calculate
       body: { fiscal_year?: int }
       → CalculationBundle

POST /pipeline/stages/{ticker}/agents
       body: { confirm_llm_cost: bool }   # required true to actually run
       → AgentBundle

POST /pipeline/stages/{ticker}/run
       body: { auto_agents?: bool, bust_cache?: list[str], force_refresh?: bool }
       → OrchestratorResult (all stage outcomes + fingerprints + timings)

GET  /pipeline/status                     → list[PipelineStatusRow] (universe matrix)
GET  /pipeline/status/{ticker}            → list[PipelineStatusRow] (per-ticker rows)
POST /pipeline/bust-cache/{ticker}
       body: { stages: list[str] }
       → updated PipelineStatusRows
```

**Authorisation note**: Stage 4 incurs LLM cost. The
`confirm_llm_cost: true` body flag is a deliberate friction step —
the UI surfaces an explicit cost estimate before allowing the call.
The endpoint refuses without the flag.

**Persistence**: Every endpoint writes to the `pipeline_status` table
via `PipelineStatusStore` (Week 6 work). Both UIs read from there to
show last-run timestamps + fingerprints without re-running anything.

## View 1 — Stage Explorer (detailed spec)

### Page structure

```
[Sidebar nav: Pipeline → Stage Explorer]

[Ticker dropdown — UNIVERSE]   [▶ Run all (stages 1-3)]   [⏹ Stop at: ▼]

╭─ Stage 1 — Ingest ─────────────────── status ─ fp ──── elapsed ──╮
│ [Run] [Force refresh] [View raw bundle ▾]                        │
│ Validation panel:                                                 │
│   - Sources fetched: 11/11   per-source sha + age   FX detected  │
│   - classification_snapshot vs config (any drift?)                │
╰───────────────────────────────────────────────────────────────────╯

╭─ Stage 2 — Validate ─────────────────────────────────────────────╮
│ [Run] [Bust cache] [View records ▾]                              │
│ Validation panel:                                                 │
│   - 17 cleaned records · quality_score histogram                  │
│   - schema_violations: 0   overrides_applied: [V's shares_…]     │
│   - Identity audit summary: 6/7 passed (FCF pathway Category C)  │
╰───────────────────────────────────────────────────────────────────╯

╭─ Stage 3 — Calculate ────────────────────────────────────────────╮
│ [Run] [Bust cache] [View bundle ▾]                               │
│ Validation panel:                                                 │
│   - WACC 13.43%   DCF base $433.71   RDCF implied 28%            │
│   - Screening 22 ✓ / 5 ⚠ / 7 ✗   Moat fingerprint 7.5/10        │
│   - Schema violations (calc-output sanity): 0                     │
│   - tax_rate_source: cash (15.6% — plausible band [13, 17])      │
╰───────────────────────────────────────────────────────────────────╯

╭─ Stage 4 — Agents ───────────────────────── ⚠ LLM cost gate ────╮
│ ☐ I confirm this will incur ~$1-3 in LLM cost  [Run with agents]│
│ Last run: 2026-05-12 14:30 · cost $1.42 · fp=3c6fa8…             │
│ Validation panel:                                                 │
│   - Thesis present: ✓  cited_signals: 14 from upstream           │
│   - contrarian: bear case present, sentiment "neutral"           │
│   - qualitative_synthesis: 3/3 reports                            │
╰───────────────────────────────────────────────────────────────────╯
```

### Validation panels — what each surfaces

Each panel is the analyst's "is this stage trustworthy?" view. The
content is opinionated per stage:

**Stage 1**: source completeness, fetch timestamps, content hashes,
FX/foreign-filer detection, classification drift.

**Stage 2**: schema_violations (by tier), overrides_applied (with
each override's `reason` displayed), quality_score per FY, validation
receipt (Gate A.TTM outcome, FX conversion details).

**Stage 3**: key numerics (WACC, IV, implied CAGR, screening summary),
calc-layer schema_violations (output sanity), `tax_rate_source` with
plausibility band, identity-check status for the latest FY.

**Stage 4**: thesis structural completeness, cited_signals upstream
match, LLM cost, per-agent outputs (collapsed).

### State management

- Streamlit session state stores the latest bundle per stage per
  ticker so the analyst can navigate away and come back.
- API responses are cached locally for the session (no re-pull on
  expand/collapse of a panel).
- Hitting "Run" on a stage invalidates downstream session state
  for that ticker (matches the cascade-invalidation policy).

## View 2 — Status Matrix (detailed spec)

### Page structure

```
[Sidebar nav: Pipeline → Status]

[Filters: Stage ▼  Status ▼  Last-run > ▼]
[Bulk actions: Re-run selected · Bust cache: stage3 · Export CSV]

┌────────┬──────────┬──────────┬──────────┬──────────┐
│ Ticker │ Stage 1  │ Stage 2  │ Stage 3  │ Stage 4  │
├────────┼──────────┼──────────┼──────────┼──────────┤
│ AAPL   │ ✓ ok     │ ✓ ok     │ ✓ ok     │ ✓ ok     │ ← click row
│ NVDA   │ ✓ ok     │ ✓ ok     │ ✓ ok     │ — n/a    │
│ NEE    │ ✓ ok     │ ✓ ok     │ ⚠ stale… │ — n/a    │ ← amber row
│ JPM    │ ✓ ok     │ ✓ ok     │ ✗ failed │ — skipped│ ← red row
│ ...    │          │          │          │          │
└────────┴──────────┴──────────┴──────────┴──────────┘

Footer:
  Universe: 40 tickers  Last sweep: 2026-05-13 16:40
  Stage 1 ok: 40/40  Stage 2: 39/40  Stage 3: 36/40  Stage 4: 12/12 (opt-in)
```

### Interactions

- **Click a row** → switches to Stage Explorer focused on that
  ticker (via Streamlit `st.switch_page` or query param).
- **Filter** by stage / status / last-run age.
- **Bulk select** rows → "Re-run selected" runs the orchestrator
  for each. Progress shown inline.
- **Export CSV** — same shape as the `aletheia pipeline status`
  CLI output.

### Status colour coding (uses StageStatus enum from contracts)

| StageStatus | Visual |
|---|---|
| `ok` | green ✓ |
| `skipped_cached` | grey ✓ |
| `running` | spinner |
| `failed` | red ✗ |
| `skipped_dependency` | grey — |
| `stale_due_to_override` | amber ⚠ |
| `pending` | white — |

### Refresh policy

- Polling: 5-second refresh while any row is `running`.
- Otherwise: refresh on user action only.
- No websocket; Streamlit's native auto-rerun is enough.

## Build sequence (~8 days)

Each step ends in a commit. Both views depend on the API endpoints,
so those land first. Then the two pages build in parallel.

### Commit 1 — API endpoints (~0.5 day)

- 7 new `Pipeline`-tagged endpoints in `api_main.py`.
- Pydantic request body models.
- Tests at `tests/api/test_pipeline_stage_endpoints.py` covering:
  happy path per stage, `confirm_llm_cost` gate, malformed inputs.

### Commit 2 — Stage Explorer scaffolding (~1.5 days)

- `aletheia/ui/pipeline_explorer_view.py` with the 4-card layout.
- Sidebar nav entry in `streamlit_app.py`.
- Each card calls the corresponding endpoint, displays raw JSON
  bundle in a `st.expander`.
- No validation panels yet — just the "run + show JSON" core flow.

### Commit 3 — Stage Explorer validation panels (~3 days)

- Per-stage validation panel components.
- Source-completeness table (Stage 1).
- schema_violations + overrides + quality-score histogram (Stage 2).
- WACC/DCF/RDCF/MD/screening summary + tax_rate_source + identity
  panel (Stage 3).
- thesis/contrarian/synthesis (Stage 4).
- LLM-cost confirmation gate (Stage 4).

### Commit 4 — Status Matrix (~2 days)

- `aletheia/ui/pipeline_status_view.py` with the matrix grid.
- Status colour coding via the `StageStatus` enum.
- Filters + bulk re-run.
- CSV export.
- Polling for `running` rows.

### Commit 5 — Cross-linking + polish (~0.5 day)

- Row-click in matrix → Stage Explorer with ticker preselected.
- "Back to status" breadcrumb in Stage Explorer.
- Consistent header / styling across both pages.

### Commit 6 — Tests + docs (~0.5 day)

- E2E smoke test: run Stage 1 → 2 → 3 via the API, verify each
  endpoint returns a typed bundle that matches its contract.
- Update `docs/pipeline_operations.md` with the UI workflows.

## Why both can ship independently

The hard division means:

- If the Stage Explorer ships first, analysts can deep-verify
  Phase B tickers immediately. The Status Matrix arrives later;
  in the meantime, the CLI `aletheia pipeline status` covers the
  matrix-level need.
- If the Status Matrix ships first, the operations workflow gets
  the universe view. Per-ticker depth still works via the existing
  per-stage CLIs.
- Deprecating one doesn't touch the other.

## What this doesn't cover

- **Backtest report views** (Phase E deliverable, separate UI).
- **Cross-source comparison views** (Phase D deliverable, separate UI).
- **Identity-audit-findings drilldown** — exists as a Markdown
  report today. A UI for it is a Phase A/C deliverable, not part
  of this build.
- **Mobile / responsive layout** — Streamlit's default rendering
  is sufficient for analyst desktops.
