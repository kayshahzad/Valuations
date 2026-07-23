# Phase 3.1 — Gate F (`aggregate_universe`) implementation plan

Fills in the **documented-but-missing** universe-level validation gate. Right now
Gate B (calc drift) and Gate A.TTM drift are stamped into each report's
`_validation` block and then **never acted on** — the catcher (`aggregate_universe`)
was specified in the docstrings but never written.

Status: **plan only** (calibrated by the 3.1.0 characterization run, 2026-07-23).
Not yet implemented.

---

## 1. The contract (from `aletheia/data/fmp_validation.py:23-25`)

```
Gate F   aggregate_universe(serving_dir)
         → called from scripts/regen_universe.sh step 6
         → reads every report's `_validation` block
         → applies threshold matrix; exits non-zero on FAIL
```
Referenced as the safety net in `state.py:54`, `calc_node.py:622`,
`fmp_validation.py:15/1084/1225`. **`def aggregate_universe` exists nowhere.**

## 2. Data it reads (verified)

Each `valuation_data/serving/latest/{T}_report.json` has a `_validation` block:
- `ingestion.status` / `calc.status` ∈ {validated, drift, blocking_drift, skipped}
- `calc.fields.{field}` = `{ours, fmp, drift_pct, tier, blocking, status, source_endpoint}`
  - field `status` ∈ {byte_perfect, acceptable, structural_drift, n_a}
  - `tier` ∈ {strict, standard, definitional, sanity_only, byte_perfect_required}
- `schema_version` (currently uniform = 2)

---

## 3. Characterization (task 3.1.0 — DONE, read-only)

57 reports, all `schema_version 2`, **0 missing** `_validation` blocks → Gate F has
real calc-side signal. **3.2 (ingestion receipt) is NOT a prerequisite.**

**Calc status:** 12 validated · 39 drift · **6 blocking_drift**.

**Two distinct drift patterns — this is the core calibration:**

| Pattern | Fields (tickers affected) | Nature | Gate F treatment |
|---|---|---|---|
| Systematic **definitional** drift | `market_p_e` (40, worst 216%), `market_ev_ebitda` (38, worst 251%) | our methodology vs FMP; `sanity_only`/`definitional` tier | **EXCLUDE** — drifts by design |
| The **6 blocking_drift** | 5× `current_price`/`market_cap` (2.7–7.6%), 1× `beta` NSC (−34%) | **staleness + beta-noise**, not bugs | see below |

**The 6 blocking_drift are all false positives:**
- AVGO/JPM/LLY/NEE/SOFI — price/market_cap on reports stamped **May–Jun 2026
  (1–2 months stale)**; prices moved. Fresh regen re-fetches → gone.
- NSC — `beta` −34% (known FMP-beta unreliability on defensive names). `beta` is
  `sanity_only` and should not be `blocking` at all (see §7 side-findings).

**Ingestion side:** 60% `skipped: not_loaded_from_db` — the C4 wiring gap. Gate F
gates on the **calc** side, so this doesn't block it, but the ingestion half of the
safety net stays empty until 3.2.

### Calibration conclusions
1. **Filter by tier** — gate only on `strict`/`standard` blocking fields; exclude
   `sanity_only`/`definitional`. (Without this, `market_p_e`/`market_ev_ebitda`
   would fail every regen for a non-bug.)
2. **Run on FRESH post-regen reports** — on stale reports every "failure" is price
   staleness. Confirms the step-6-after-regen placement.
3. **WARN-only rollout is essential** — abort-on-FAIL against today's stale reports
   would false-fail universally.

---

## 4. Threshold matrix (calibrated)

Evaluate **only** fields with `tier ∈ {strict, standard}` and `blocking == true`.
Everything `sanity_only`/`definitional` is reported for context but never fails.

| Condition | Verdict |
|---|---|
| any `calc.status == blocking_drift` on a **strict/standard blocking** field | **FAIL** |
| one **strict/standard** field `structural_drift` on ≥ **N%** of universe (systematic bug) | **FAIL** |
| calc `skipped` rate > **ceiling** (validation silently not running) | **FAIL** |
| isolated non-systematic `structural_drift` on a strict/standard field (< N%) | **WARN** |
| definitional/sanity_only drift (any magnitude) | PASS (reported only) |
| all validated/acceptable | PASS |

- **N%** and **ceiling** to be set from the first *fresh* post-regen run (the
  current stale numbers can't calibrate them — staleness dominates). Provisional:
  N = 25% (systematic if ≥¼ of universe drifts the same strict field); skip-ceiling
  = 40% calc-skipped. Lock these after one fresh regen.
- Constants live in one named block beside `_TIER_BANDS`, each with a rationale
  comment tied to the fresh-run numbers.

---

## 5. Tasks

| # | Task | Deliverable | Size |
|---|---|---|---|
| 3.1.0 | **Characterize** (DONE) | drift distribution above | S ✓ |
| 3.1.1 | `aggregate_universe(serving_dir, *, thresholds, report_only=False)` in `fmp_validation.py` | reads every `_validation` block; version-guards `schema_version`; tier-filters; returns `GateFResult{verdict, universe_n, status_hist, systematic_fields, skip_rate, offenders, thresholds_applied}` | M |
| 3.1.2 | **Threshold matrix** constants + rationale (from a fresh run) | named constant block | S |
| 3.1.3 | Output: human summary + `audits/gate_f_<date>.json` + exit code | diff-able artifact; exit 0 (PASS/WARN) / non-zero (FAIL) | S |
| 3.1.4 | CLI `scripts/gate_f.py [--serving-dir] [--report-only]` | wrapper for step 6 | S |
| 3.1.5 | Wire into `regen_universe.sh` as **step 6**, **WARN-only** first | `echo_step "6. Gate F"` running the CLI; flip to abort-on-FAIL after 1–2 fresh regens prove thresholds | S |
| 3.1.6 | Tests: synthetic serving dirs → verdict + exit code | fixtures: all-clean→PASS · one-off strict drift→WARN · systematic strict drift→FAIL · high-skip→FAIL · blocking_drift on strict→FAIL · definitional-only drift→PASS · malformed/old-schema→skipped-no-crash | M |

**Sequencing:** `3.1.1 + tier-filter → 3.1.3/3.1.4 → 3.1.6 → 3.1.5 (WARN-only) →
[first fresh regen] → 3.1.2 lock thresholds → flip to abort-on-FAIL`.

Note the ordering wrinkle: thresholds (3.1.2) can only be locked after a **fresh**
regen produces non-stale reports, so 3.1.1 ships with provisional constants in
report-only/WARN mode, and 3.1.2 finalizes them once real signal exists.

---

## 6. Definition of Done
- `aggregate_universe` deterministic + `schema_version`-guarded; report-less/old-schema
  reports counted `skipped`, never crash.
- Tier filter excludes `sanity_only`/`definitional`; only strict/standard blocking
  fields can FAIL.
- Test fixtures pin every branch of the matrix.
- Step 6 runs in `regen_universe.sh` (WARN-only initially); `audits/gate_f_<date>.json`
  written and diff-able across regens.
- Thresholds documented with rationale tied to a fresh-run distribution.

## 7. Side-findings to fix in passing
- **`beta` marked `blocking` on NSC but is `sanity_only`** — a mis-configuration;
  `beta` should never block (FMP beta is known-unreliable on defensive names). One-line
  fix in the Gate-B field table.
- **Ingestion validation 60% `skipped: not_loaded_from_db`** — the C4 gap. Gate F gates
  the calc side, so this is non-blocking for 3.1, but the ingestion half of the safety
  net stays empty until **3.2** (wire the Gate A receipt into records).

## 8. Risks
- **Threshold miscalibration** → mitigated by report-only/WARN-first + locking on a
  fresh regen, never on stale reports.
- **Staleness false-positives** → structurally avoided by running at step 6 (post-regen,
  fresh prices) and WARN-only until proven.
- **Definitional-drift false-positives** → the tier filter is the guard; it must be the
  first thing implemented and tested.
- **Gate F only detects** — it aggregates and fails the regen; it does not fix the
  underlying drift (that's per-ticker work, out of scope).

## 9. Decisions locked (from 3.1.0 evidence)
- Rollout: **WARN-only first**, flip to abort-on-FAIL after a fresh regen.
- Scope: **Gate F standalone** (calc-side has signal); 3.2 follows.
- Gate on: **strict/standard blocking fields only**; definitional/sanity_only reported
  but never fail.
