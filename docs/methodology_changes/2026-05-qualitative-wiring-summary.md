# Qualitative-tab wiring — Phase A-F completion summary

**Effective dates:** 2026-05-17 / 2026-05-18 (six phases shipped over two days)
**Companion architecture-lock test:** [tests/architecture/test_qualitative_wiring_lock.py](../../tests/architecture/test_qualitative_wiring_lock.py)

## What the project was missing before this work

The qualitative-analysis catalog declared 19 analytical dimensions across 5 categories, but only **13 of 19** had a producer wired:

| Source category | Pre-wiring | Producer infrastructure |
|---|---|---|
| `DETERMINISTIC` | 4 | `aletheia/qualitative/computers/` |
| `HITL` | 9 | Analyst form in `qualitative_input.py` |
| `LLM_AUGMENTED` | 0 (3 deferred) | None — comments said "scheduled for week 4-6" |
| `PENDING_DATA` | 3 | None — explicit data-source gap |

The 6 unwired dims:
- Phase B: `competitor_identification`, `regulatory_exposure`, `customer_concentration` (10-K extraction)
- Phase C: `management_tenure_continuity`, `management_alignment` (DEF 14A extraction)
- Phase D: `industry_concentration` (peer-cohort computer)

The thesis synthesizer was already wired to cite qualitative dims by name (`qualitative.{dim_id}`), but 6 of those citation paths returned `not_assessed` for every ticker — gap was structural, not analyst-side.

## What the six phases delivered

### Phase A — Foundation (no behavior change)
- Extractor protocol: `aletheia/qualitative/extractors/base.py` (`ExtractionResult`, `Extractor`)
- Single-dim LLM extractor factory: `aletheia/qualitative/extractors/llm_extractor.py`
- Persistence helper: `aletheia/qualitative/extraction_runner.py` (`persist_extraction`, `run_extractors`)
- Workflow node: `aletheia/agents/qualitative_extraction.py` (no-op until producers register)
- Empty `EXTRACTORS = {}` registry — Phase B populates

11 tests.

### Phase B — 10-K bundle (3 dims)
- Schemas: `aletheia/qualitative/extractors/schemas.py` — `CompetitorExtraction`, `RegulatoryExtraction` (+ `RegulatoryExposureItem`), `CustomerExtraction` (+ `NamedCustomer`), `QualitativeExtractionBundle`
- Shared LLM-with-retry primitive: `aletheia/qualitative/extractors/_llm_invoke.py`
- Factory: `aletheia/qualitative/extractors/bundle_extractor.py` + `fan_out_bundle`
- Prompt: `BUNDLE_PROMPT` in `prompts.py`
- Workflow integration in `aletheia/workflow/graph.py` (between calc_node and qualitative_synthesis)

22 tests. Live GOOGL Stage 4: all 3 dims persisted with `source_category=llm_augmented` + `llm_provider=gemini` provenance.

### Phase C — DEF 14A bundle (2 dims) + filing cache
- Schemas: `aletheia/qualitative/extractors/def14a_schemas.py` — `TenureContinuityExtraction` (+ `DirectorTenureItem`), `AlignmentExtraction` (+ `CompPlanComponent`), `Def14aExtractionBundle`
- Factory: `aletheia/qualitative/extractors/def14a_bundle_extractor.py`
- Prompt: `DEF14A_BUNDLE_PROMPT` in `prompts.py`
- Librarian extension: `aletheia/agents/librarian.py` fetches DEF 14A alongside 10-K
- `raw_def14a_text` declared in `aletheia/state.py` (LangGraph filters undeclared keys)
- Catalog flip: `management_tenure_continuity` + `management_alignment` `PENDING_DATA → LLM_AUGMENTED`
- **Filing persistence cache** (Phase C.1 follow-up): `valuation_data/raw/sec/filings/{TICKER}/10K_{accession}.md` + `DEF14A_{accession}.md`. Idempotent — re-runs skip SEC body downloads. Audit trail for "what did the LLM see"

20 tests. Live AAPL Stage 4: both management dims persisted. Cache write+hit verified end-to-end across two consecutive runs.

### Phase D — industry_concentration (deterministic computer)
- Cross-ticker computer: `aletheia/qualitative/computers/industry_concentration.py`
- Top-3 cohort share within the in-universe sector (MVP — real industry HHI deferred)
- Small-cohort fallback (top-2 for cohorts of 2-3; top-1 for 1)
- 1-7 bucket mapping with documented thresholds
- Catalog flip: `PENDING_DATA → DETERMINISTIC` + `formula_citation`
- Registered in `COMPUTERS`

18 tests. Live AAPL: scored 6/7 (Technology sector, 9 peers, top-3 share 69.5%, AAPL ranks #2 with 18.8%).

### Phase E.1 — UI provenance pills
- New `_provenance_pill()` helper in `aletheia/ui/qualitative_view.py`:
  - DETERMINISTIC → `📊 {formula_version}` (blue)
  - HITL → `👤 Analyst` (violet)
  - LLM_AUGMENTED → `🤖 {Provider}` (orange)
- Status line refactored to show status + provenance side-by-side
- Per-dim renderers in `_render_llm_payload()` — surfaces structured extraction data (named competitors, regulatory exposures, named customers, notable directors, comp structure, performance metrics)
- Outdated "scheduled for week 4-6" copy replaced with current "Run Stage 4 (LLM)" guidance

No new automated tests (UI surface; live verification via running Streamlit).

### Phase F — Architecture lock
**File**: [tests/architecture/test_qualitative_wiring_lock.py](../../tests/architecture/test_qualitative_wiring_lock.py)

Eleven invariants enforced at import time + pytest:

1. **Every DETERMINISTIC catalog dim has a registered computer** — no silent coverage rot
2. **Every entry in COMPUTERS has a matching DETERMINISTIC catalog dim** — no orphan computers writing unread rows
3. **Every LLM_AUGMENTED catalog dim has a registered producer** (BUNDLE_DIMS / DEF14A_BUNDLE_DIMS / EXTRACTORS)
4. **Every bundle/extractor member has a matching LLM_AUGMENTED catalog dim**
5. **BUNDLE_DIMS == QualitativeExtractionBundle's fields** — fan-out can't drift from schema
6. **DEF14A_BUNDLE_DIMS == Def14aExtractionBundle's fields** — same invariant for Phase C
7. **No PENDING_DATA dims** — locks the Phase D promise that all catalog dims are wired
8. **Bundle dims disjoint from each other** — no dim routed through both 10-K and DEF 14A LLM calls
9. **Bundle dims disjoint from per-dim EXTRACTORS** — no double-write race condition
10. **COMPUTERS disjoint from extractors** — no dim claimed by both DETERMINISTIC and LLM_AUGMENTED paths
11. **Coverage stat pinned** — catalog size (19 dims) and per-source counts (5/5/9/0) locked as regression net

CI now fails loudly if any of these invariants regresses.

## Cumulative state

| Metric | Pre-Phase-A | Post-Phase-F |
|---|---|---|
| Catalog dims wired (have a producer) | 13/19 | **19/19** |
| PENDING_DATA dims | 3 | **0** |
| Producer paths | 1 (computers) + 1 (HITL form) | 4 (computers + Phase B bundle + Phase C bundle + HITL form) |
| Stage 4 LLM calls (happy path) | 3 | **5** (+2 for the two extraction bundles) |
| Test files (qualitative) | 3 | **8** |
| Tests covering this surface | 71 | **82** |
| Architecture-lock invariants | 0 | **11** |

## Stage 4 LLM cost trajectory

| Phase | LLM calls per Stage 4 run |
|---|---|
| Pre-Phase-A | 3 (qualitative_synthesis + contrarian + thesis_synthesizer) |
| Post-Phase B | 4 (+1 for 10-K extraction bundle) |
| Post-Phase C | **5** (+1 for DEF 14A extraction bundle) |

Worst case w/ retries: 9 calls (3 narrative + 2 extraction, each with one retry budget). Real-world: typically 5-6 because retries are rare with Gemini 3.1 Pro structured-output mode.

## What's deferred (intentionally)

1. **HITL override of LLM/computer values** — the UI surfaces provenance pills and structured payloads, but the override-write path (analyst submits an HITL row to overwrite an LLM-extracted score) needs a small DB constraint change: `upsert_qualitative_assessment` enforces strict `source_category` matching, blocking the override write. Suggested approach: relax the constraint to allow HITL submissions for dims with any non-PENDING source_category, and stamp `source_payload["analyst_override"] = True` so the dashboard provenance shows the override.

2. **Real industry HHI** (Phase D Path 2/3) — current `industry_concentration` uses the in-universe peer cohort as a proxy. Future enhancement: fetch sector HHI from FMP's industry endpoint or US Census BLS data.

3. **Stage 4 idempotency for LLM cost** — the cache helper in the librarian saves the FETCH cost on re-runs but the LLM CALL still fires regardless. Idempotency at the extraction layer (skip the Gemini call when the source-text fingerprint matches the prior row) is a separate optimization worth ~$0.30-0.50 per universe re-run.

4. **Quality calibration sweep** — Phases B + C shipped with fixture-ticker smoke tests but no universe-wide quality A/B (e.g., comparing extracted competitor lists across 10 tickers to analyst-curated baselines). Worth doing before the universe-wide thesis-synthesizer integration cites these dims at scale.

## Audit trail

For external review / due diligence:

- **Methodology memos** — this file + the per-phase notes in commit messages
- **Test coverage** — 82 tests covering protocols, schemas, factories, persistence, idempotency, and 11 architecture invariants
- **Live verification** — GOOGL Stage 4 (Phase B), AAPL Stage 4 (Phase C), AAPL `recompute_deterministic` (Phase D), Streamlit Qualitative tab (Phase E)
- **Filing cache** — `valuation_data/raw/sec/filings/{TICKER}/` carries every 10-K + DEF 14A excerpt the LLM agents have seen, keyed by SEC accession number

## Linked artifacts

- Extractor protocol: [aletheia/qualitative/extractors/](../../aletheia/qualitative/extractors/)
- Bundle schemas: [schemas.py](../../aletheia/qualitative/extractors/schemas.py), [def14a_schemas.py](../../aletheia/qualitative/extractors/def14a_schemas.py)
- Workflow node: [aletheia/agents/qualitative_extraction.py](../../aletheia/agents/qualitative_extraction.py)
- Industry concentration computer: [aletheia/qualitative/computers/industry_concentration.py](../../aletheia/qualitative/computers/industry_concentration.py)
- UI provenance: [aletheia/ui/qualitative_view.py](../../aletheia/ui/qualitative_view.py)
- Architecture lock: [tests/architecture/test_qualitative_wiring_lock.py](../../tests/architecture/test_qualitative_wiring_lock.py)
