# Verification Program — Five-Phase Plan

**Status**: planning document — design ready for stakeholder review. No
execution has started on Phases A-E; the Phase-1 identity audit and
the A11 / domain-10 fix are the *inputs* that motivate this plan.

## Why this exists

The pipeline refactor (Weeks 1-8) and the calc-validation framework
(Phases 0-8) ship the *infrastructure* — typed stages, schema
contracts, override registry, identity primitives. The Phase-1
identity audit ([docs/identity_audit_findings_2026-05-13.md](identity_audit_findings_2026-05-13.md))
showed where the *data* sits inside that infrastructure: 92.8% pass on
balance-sheet identity, 60-30% on the roll-forward identities, 30%
on FCF pathway. The remaining gap isn't a bug — it's a mix of
documented complexity (SBC, M&A, ASC 842), genuine schema limits
(banks, insurers, utilities), and known-anomaly cases (A1-A19).

Trust across the universe is currently **implicit and uneven**: an
analyst running NVDA gets near-full confidence; an analyst running
JPM gets a thesis that silently omits its bank-specific complexity.
The platform doesn't surface that asymmetry.

This program makes trust **explicit, calibrated, and continuously
monitored**. Five phases, sequenced across roughly a year, ending in
backtested evidence about analytical edge claims.

## The ladder at a glance

```
Phase A (2-3 weeks, immediate) — Trust ladder
        │
        ▼
  Each ticker gets High / Medium / Low classification.
  Output drives UI affordances + analyst caveats.

Phase B (4-6 weeks, parallel) — Deep verification (5 representative tickers)
        │
        ▼
  Per-ticker authoritative-source comparison.
  Output: calibrated confidence framework.

Phase C (1-2 weeks engineering, parallel) — Continuous verification
        │
        ▼
  Identity checks run inside Stage 2 on every refresh.
  Failures persist to pipeline_status; trends visible over time.

Phase D (6-8 weeks, next quarter) — Cross-source comparison
        │
        ▼
  Gate A.TTM extended to systematic FMP / Morningstar / 10-K
  reconciliation per ticker. Methodology confidence document.

Phase E (6-8 weeks infra + years of discipline) — Backtesting
        │
        ▼
  Every signal tracked; subsequent performance recorded.
  Evidence accumulates over years about analytical edge.
```

Phases A/B/C run in parallel and complete by ~Week 6. Phase D starts
after the foundation. Phase E builds on Phase D's per-ticker
methodology calibration.

## Phase A — Trust ladder establishment

**Scope**: classify every ticker in the 40-ticker universe by current
trust level. Document each classification with explicit rationale.

**Timeline**: 2-3 weeks. ~80% analyst time, ~20% engineering.

**Inputs**:
- Identity audit results from [docs/identity_audit_findings_2026-05-13.md](identity_audit_findings_2026-05-13.md).
- Override registry entries in [aletheia/calculations/_overrides.py](../aletheia/calculations/_overrides.py).
- Known-anomaly catalog [docs/calculation_anomaly_catalog.md](calculation_anomaly_catalog.md) (A1-A19).
- Per-ticker classification metadata (sector, lifecycle, business_model)
  in [config/ticker_classification.py](../config/ticker_classification.py).

### Proposed classification rubric (locked candidate)

| Tier | Criteria | Expected count |
|---|---|---|
| **High-trust** | Identity audit pass-rate ≥ 85% AND `business_model = fcff_compatible` AND no override entry of magnitude ≥ 5% of any line-item AND no open Category A finding | 15-20 |
| **Medium-trust** | One or more documented Category C exception (SBC-heavy tech, active acquirer, ASC 842) AND identity audit pass-rate ≥ 60% AND no schema gap that blocks calc | 12-15 |
| **Low-trust** | `business_model in {routing_required, ddm_required}` OR schema-gap override active (NEE utility taxonomy, SMCI splits) OR identity audit pass-rate < 60% | 5-8 |

Pass-rate threshold and "5% magnitude" are stub values — final
calibration is a Phase-A deliverable.

### Per-ticker deliverable

For each ticker, a single row in `docs/trust_ladder.md` (new):

```
| Ticker | Tier | Identity pass-rate | Open exceptions | Caveat for UI |
|---|---|---|---|---|
| NVDA | High | 87% | — | none |
| AAPL | Medium | 78% | SBC, EU State Aid FY2025 | "SBC-heavy: FCF metrics include non-cash adjustment" |
| NEE | Low | 64% | utility_capex_xbrl, utility_total_liabilities_aggregation | "Utility schema — CapEx + TotalLiabilities tooling limited" |
| JPM | Low | 41% | routing_required | "Bank schema — DCF + multiple decomp not applicable" |
```

### Wire-in to the UI

Once classified, the dashboard reads the trust tier and decorates the
header accordingly:

- **High**: no caveat. Standard analyst-confidence affordances.
- **Medium**: subtle banner explaining the documented exception (one
  line, dismissible). The thesis_synthesizer prompt includes the
  caveat in the agent context so it doesn't write past the limit.
- **Low**: prominent banner. Sections that depend on the broken
  schema are visually suppressed or replaced with "limited support
  for this filer."

Engineering effort: ~3 days to plumb `trust_tier` through the API +
dashboard. Listed in the timeline.

### Phase A locked decisions (proposed; need stakeholder confirmation)

1. **Tier is per-ticker, not per-FY**. A ticker is Medium because of a
   persistent pattern (SBC), not because FY2024 has more SBC than
   FY2023. (Reduces churn; FY-specific issues belong in
   `data_quality_exceptions.md`.)
2. **Tier reviewed quarterly**. New 10-Ks can flip a ticker's tier
   (a Medium ticker that resolves its acquisition-year noise reverts
   to High). Owner: analyst lead.
3. **Tier surfaced via API**, not hardcoded in UI. The dashboard
   reads `/ticker/{T}` and gets the tier in the response payload.
4. **No automated tiering**. The classification is analyst judgment
   informed by the rubric, not a script's output. The rubric is the
   floor, not the ceiling.

### Phase A open questions

- Q1. Final pass-rate threshold per tier (rubric uses 85% / 60% as
  candidates — calibration during execution).
- Q2. Does the UI suppress sections or just caveat them for
  Low-trust tickers? (Implementation depends on this.)
- Q3. Should the LLM prompt context include the tier explicitly, or
  should the thesis_synthesizer just see the documented exception
  list? (Affects how Stage 4 reasons about each ticker.)

## Phase B — Deep verification on representative sample

**Scope**: 5 representative tickers, each verified against authoritative
external sources. Output is the calibrated confidence framework that
generalises to the rest of the universe.

**Timeline**: 4-6 weeks. ~95% analyst time, ~5% engineering (light
tooling for source comparison).

### Sample selection (proposed, validated against audit data)

| Slot | Proposed ticker | Why it's representative | Audit pass-rate |
|---|---|---|---|
| Simple consumer | **KO** | Mature, stable, classic consumer-staples shape — minimal cleaning gymnastics | TBD |
| Tech with SBC | **NVDA** | High SBC, no major acquisitions, growth profile — isolates the SBC pattern | 70% |
| Foreign filer | **ASML** | EUR-reporting, IFRS, FX cash-flow gap — tests Stage 2 FX handling | TBD |
| Bank | **JPM** | `business_model=routing_required` — characterises the bank schema gap concretely | 47% |
| Active acquirer | **ABT** | M&A-driven PP&E + WC roll-forward gaps — quantifies legitimate complexity | TBD |

Audit pass-rates from the 2026-05-13 baseline. The TBDs need a per-
ticker pull from the JSON before kickoff.

### Per-ticker verification methodology

For each of the 5 tickers, the analyst:

1. **Pull authoritative source values**:
   - Latest 10-K direct extraction (Revenue, EBIT, NI, OCF, CapEx,
     FCF, total debt, equity, shares diluted).
   - Sell-side consensus for forward estimates where accessible
     (Bloomberg, FactSet — if licensed).
   - Morningstar or similar for historical ratios (ROIC, ROE, P/E
     decomposition).
2. **Run the full pipeline** for the ticker; capture the typed
   `CalculationBundle` + `AgentBundle`.
3. **Compare** every key metric. Tabulate where the platform agrees
   and where it differs.
4. **Classify each difference** using the Category A/B/C/D scheme
   from the identity audit prompt:
   - A — Source data wrong; fix at ingest.
   - B — Cleaning logic gap; extend cleaning_engine.
   - C — Legitimate methodology divergence; document expected.
   - D — Team decision needed.

### Per-ticker deliverable

`docs/verification/<TICKER>_deep_verification.md` (new directory):

```
# <TICKER> Deep Verification — <DATE>

## Authoritative-source baseline
... 10-K values, sell-side consensus, Morningstar ratios

## Platform-produced values
... CalculationBundle + AgentBundle outputs

## Reconciliation table
| Metric | Authoritative | Platform | Δ | Category |
|---|---|---|---|---|
| Revenue FY2024 | $383.3B (10-K) | $383.3B | 0 | — |
| ROIC FY2024 | 45.2% (M*) | 47.1% | +1.9pp | C (calc uses NorthWestern NOPAT — defensible) |
| ... |

## Findings
- N differences total: K Category A, L Category B, M Category C, N Category D
- Detailed write-up per material difference

## Conclusion
- Platform trust assessment for this filer-pattern
- Generalisation note: "Tickers similar to <TICKER> can be assumed to
  inherit these confidence characteristics."
```

### Phase B locked decisions (proposed)

5. **5 tickers, not 10**. The verification is deep, not broad.
   Generalisation to similar filer-patterns is the leverage.
6. **No quantitative confidence score**. Phase B produces narrative
   confidence: "platform reproduces 10-K headline values to within
   0.1%; ROIC differs by 1-3pp due to NOPAT methodology — defensible."
   A single confidence number is misleading.
7. **5-8 analyst-days per ticker**. Spread across 4-6 elapsed weeks.

### Phase B open questions

- Q4. Source licensing — is Bloomberg / FactSet available for the
  sell-side consensus comparison, or do we constrain to FMP + 10-K?
- Q5. Who's the analyst lead? Engineer assists; analyst owns
  classification of differences.
- Q6. Are the 5 sample tickers locked, or do we add a 6th (cyclical
  industrial — CAT) once underway?

## Phase C — Continuous verification infrastructure

**Scope**: integrate the identity-audit script into Stage 2's
validation framework. Every data refresh runs identity checks;
failures persist to `pipeline_status`; trends visible over time.

**Timeline**: 1-2 weeks. ~95% engineering, ~5% analyst (tolerance
calibration).

### Integration spec

The Phase-1 audit script
([tools/verification/identity_checks.py](../tools/verification/identity_checks.py))
is standalone investigative tooling. Phase C makes it a Stage 2
gate:

1. **New module** at `aletheia/calculations/_identity_checks.py`
   re-uses the seven check functions but is callable from inside
   Stage 2's `run_stage2()`.
2. **Each `ValidatedCleanedRecord`** gets new fields:
   - `validation.identity_violations: List[Dict[str, Any]]` —
     per-identity failure record (mirrors the existing
     `schema_violations` shape).
   - `validation.identity_pass_rate: float` — percentage that
     passed for this record's year (informational only; doesn't
     gate persistence).
3. **Aggregated** per-ticker pass rates write to a new column on
   `pipeline_status`:
   - `identity_pass_rate_30d` — rolling 30-day pass rate, useful
     for trend detection.
4. **CLI surface**:
   ```
   aletheia pipeline status NVDA --identity-trend
   ```
   Prints a sparkline of pass-rate over the last 30 days.

### Phase C locked decisions (proposed)

8. **Identity checks are advisory, not blocking** in Stage 2. A
   failed identity check doesn't refuse persistence — the framework
   captures the violation, surfaces it to the UI, and lets the
   analyst decide. Same posture as the existing schema_contract
   primitives.
9. **Tolerances inherited from Phase 1**. The 0.5% / 2% / 0.1% etc.
   thresholds from [tools/verification/identity_checks.py](../tools/verification/identity_checks.py)
   carry over. Recalibration is a Phase-A activity, not a Phase-C
   one.
10. **No new dashboard panel**. Existing Data Quality panel (Phase 8
    soft-mode surfacing) gets an "Identity checks" subsection.

### Phase C deliverables

- `aletheia/calculations/_identity_checks.py` — the reusable module.
- `tests/calculations/test_identity_checks.py` — unit tests using
  synthetic ValidatedCleanedRecord fixtures (mirror the Phase-1 tools
  tests but at the calc-layer level).
- `aletheia/pipeline/stage2_validate.py` extension — wire the checks
  into the validation receipt.
- `aletheia/ui/data_quality_panel.py` extension — identity-check
  subsection.
- Schema migration for `pipeline_status` adding the
  `identity_pass_rate_30d` column.

## Phase D — Cross-source verification expansion

**Scope**: the Gate A.TTM cross-source pattern extended to systematic
FMP / Morningstar / 10-K reconciliation for every ticker. Output is
the per-ticker methodology confidence document.

**Timeline**: 6-8 weeks. ~70% engineering, ~30% analyst.

### Design questions to resolve in Phase A/B (so Phase D can execute cleanly)

- **D-Q1**: source landscape. Which third-party sources are licensed
  and accessible? Bloomberg API? FactSet? Morningstar Direct?
  S&P Capital IQ? FMP is the floor. The licensing answer shapes the
  technical scope.
- **D-Q2**: tolerance methodology. Different sources use different
  methodologies (ROIC including / excluding goodwill, EBITDA pre /
  post-SBC, etc.). Per-metric tolerances need analyst-set values
  that distinguish "methodology divergence" from "calculation bug."
- **D-Q3**: cadence. Does the cross-source check run on every
  pipeline run, daily, or on demand? Free-tier rate limits constrain
  the answer.

### Phase D deliverable shape (placeholder)

For each ticker, a `docs/methodology_confidence/<TICKER>.md` with:

- Per-metric agreement: platform vs FMP vs Morningstar vs 10-K
- Documented methodology choices with rationale
- Confidence band per metric
- Material disagreements escalated to Category D (team decision)

This is intentionally light — the real shape gets locked once D-Q1/2/3
are answered.

## Phase E — Backtesting infrastructure + multi-year discipline

**Scope**: track every signal the platform emits. Document subsequent
performance. Accumulate evidence about analytical edge over years.

**Timeline**: 6-8 weeks engineering for the infrastructure; the
discipline is years of sustained operation.

### Infrastructure shape

A new DuckDB table `signal_history`:
```sql
CREATE TABLE signal_history (
    id                   VARCHAR PRIMARY KEY,
    ticker               VARCHAR NOT NULL,
    signal_type          VARCHAR NOT NULL,     -- 'reverse_dcf', 'multiple_decomp', 'thesis'
    fingerprint          VARCHAR NOT NULL,     -- bundle_fingerprint at emission
    emitted_at           TIMESTAMP NOT NULL,
    signal_value         JSON,                 -- the actual signal payload
    price_at_emission    DOUBLE,
    pipeline_version     VARCHAR
);
```

Plus a periodic job:
```
aletheia backtest update     # adds subsequent prices for every signal
aletheia backtest report     # per-signal-type accuracy / IRR / drawdown
```

### Phase E locked decisions (proposed)

11. **No live trading signals**. The infrastructure tracks emitted
    signals for evidence-building only. No "BUY/SELL" action ever
    gets emitted (consistent with the rest of the platform).
12. **Multi-year horizon**. Phase E's value compounds over years.
    The infrastructure ships in weeks; the evidence needs ≥3 years
    of sustained operation to mean something.

### Phase E open questions

- Q7. Should the backtest include thesis-synthesizer narrative
  outputs, or just calc-layer signals (reverse_dcf, multiple
  decomp)? Different evaluation methodologies.
- Q8. Benchmark? Buy-and-hold S&P 500 is the floor; the harder
  question is what the *analytical edge* looks like specifically.

## Cross-phase coordination

### Dependency graph

```
Phase A trust ladder    Phase B sample        Phase C engineering
       │                      │                      │
       └─→ feeds ←─────┬──────┘                      │
                       ▼                              │
         Phase A finalises tier rubric                │
         (calibrated against Phase B findings)        │
                       │                              │
                       └──────────────┬───────────────┘
                                      ▼
                              Phase D design
                              (depends on A tier
                               for source-allocation
                               decisions per ticker)
                                      │
                                      ▼
                              Phase E infrastructure
                              (depends on D methodology
                               confidence per signal)
```

Phases A, B, C overlap. The first 2 weeks are A (trust ladder draft)
+ C (engineering), running concurrently. B is analyst-time so it
can start any time and runs ~4-6 weeks. Phase A's tier rubric
calibrates against Phase B findings, so A doesn't formally lock
until B produces its first ticker write-up.

### Tension points

- **Trust labels are visible before Phase B verifies them.** The
  Phase-A High-trust label on (say) NVDA is informed by audit pass
  rate but isn't *deeply verified* until Phase B. Mitigation: Phase
  A labels are explicitly preliminary; the label gains weight when
  Phase B finishes the analogous filer-pattern.
- **Tolerance calibration is iterative.** Phase C inherits Phase 1
  thresholds; Phase A recalibrates; Phase D adds source-specific
  methodology tolerances. Each phase may want to revise the
  inheritance.
- **Phase E discipline is the hardest part.** The infrastructure is
  weeks of engineering; the value accrues over years of disciplined
  operation. Many teams ship the infra and never use it. Owner +
  cadence are essential to lock at Phase E kickoff.

## Decision summary

| # | Decision | Phase | Type | Lock state |
|---|---|---|---|---|
| 1 | Tier is per-ticker, not per-FY | A | Architectural | Proposed |
| 2 | Tier reviewed quarterly | A | Operational | Proposed |
| 3 | Tier surfaced via API, not UI hardcoded | A | Architectural | Proposed |
| 4 | No automated tiering — analyst judgment | A | Architectural | Proposed |
| 5 | 5 tickers in Phase B deep-verification | B | Operational | Proposed |
| 6 | No quantitative confidence score | B | Architectural | Proposed |
| 7 | 5-8 analyst-days per Phase B ticker | B | Operational | Proposed |
| 8 | Identity checks advisory, not blocking | C | Architectural | Proposed |
| 9 | Tolerances inherited from Phase 1 | C | Operational | Proposed |
| 10 | No new dashboard panel — extend Data Quality | C | Architectural | Proposed |
| 11 | No live trading signals — evidence only | E | Architectural | Proposed |
| 12 | Phase E multi-year horizon explicit | E | Operational | Proposed |

All decisions are **proposed** pending stakeholder review. This
mirrors the pipeline_contracts.md pattern: draft proposed, reviewer
feedback, then lock.

## Open items for stakeholder review

Numbered for easy reference in review:

- **Q1** — Final pass-rate threshold per tier (rubric uses 85%/60% as candidates).
- **Q2** — UI Low-trust handling: suppress sections or caveat them?
- **Q3** — LLM prompt context: include tier explicitly, or just the exception list?
- **Q4** — Phase B source licensing (Bloomberg / FactSet / Morningstar).
- **Q5** — Analyst lead identity for Phase B.
- **Q6** — Are the 5 Phase B sample tickers locked, or open to a 6th?
- **Q7** — Phase E scope: calc-layer signals only or include thesis narratives?
- **Q8** — Phase E benchmark methodology.
- **D-Q1/2/3** — Phase D source landscape, tolerance methodology, cadence.

## What this plan does not cover

- Specific tolerance values for each cross-source comparison metric
  (Phase D detail).
- Backtest report format / cadence (Phase E detail).
- UI wireframes for trust-tier surfacing (Phase A engineering detail).
- Migration of existing analyses to the trust-tier framework (one-time
  retrofit work; estimate during Phase A execution).

## Sequencing recommendation (proposed)

Concrete next steps if the plan is approved:

1. **Week 0 (this week)**: stakeholder review of this document.
   Lock or revise the 12 proposed decisions. Resolve Q1-Q8 +
   D-Q1/2/3 where possible; defer the rest into the relevant phase.
2. **Weeks 1-3**: Phase A trust-ladder execution; Phase C engineering
   starts in parallel.
3. **Weeks 1-6**: Phase B deep verification (analyst time).
4. **Week 4 onwards**: Phase A finalises after Phase B's first
   ticker write-up calibrates the rubric.
5. **Weeks 8-15**: Phase D execution (next quarter).
6. **Quarter 2+**: Phase E infrastructure builds; multi-year
   discipline begins.

The first week is **review, not execution**. The plan is designed
to fail fast if the foundational decisions don't survive scrutiny.
