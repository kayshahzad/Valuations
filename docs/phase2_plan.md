# Phase 2 — Identity Completeness: implementation plan

Close the accounting-identity gaps the analysis surfaced (report §04, I1–I5).
Unlike Phase 1 (which *changed* fabricated values), Phase 2 is mostly **additive
checks** — they run in `shadow` first and move no numbers, so verification is
"characterize what they flag → triage → promote", not oracle-diff.

Status: **plan only.** Unblocked now that Gate F (3.1) exists to surface any
checks promoted to blocking.

---

## Context — two enforcement systems (from the Phase-0 inventory)

- **Persistence schema contract** (`_schema_contract.py`) — tight, definitional
  tolerances (0.5%); A=L+E is Tier-C → hard-blocks Stage 3.
- **Stage-3 identity audit** (`identity_checks.py`) — a 7-identity roll-forward
  diagnostic; looser tolerances; never drops; classifies "expected exceptions".

They use *different tolerances and NCI forms for the same identities* — the root
of several gaps below.

## The gaps (with locations)

| # | Gap | Location |
|---|---|---|
| **I1** | TTM rows bypass the 7-identity roll-forward audit (FY-pairs only) | `identity_checks.py:1542-1560` |
| **I2** | NCI attribution asymmetric (positive-only ≤5% FMP; 2% SEC) + source-inconsistent | `ttm_derivation.py:226`, `sec_quarterly.py:494` |
| **I3** | Two A=L+E tolerances/forms disagree (contract tries 4 NCI forms; audit 2 + $10M floor) | `_schema_contract.py:468` vs `identity_checks.py:374` |
| **I4** | Waterfall derivations not tied to reported subtotals; no gross-profit check | `cleaning_engine.py:1688-1707` |
| **I5** | RE roll-forward reads XBRL directly → silently `skipped` without cache; no passed-vs-skipped distinction | `identity_checks.py:491` |
| **I6** | ΔNWC has 3 unreconciled definitions (audit AR+Inv−AP · cleaning (CA−cash)−CL · rev×0.03 proxy) | `identity_checks.py:1335`, `cleaning_engine.py:1402/1409` |

---

## Guiding principle — measure, shadow, then promote

Every new/unified check lands **advisory in shadow** (`ALETHEIA_GUARD_MODE=shadow`),
gets **characterized across the universe** (like Gate F 3.1.0 and the Phase-0
fallback map), triaged for false-positives (legitimate accounting edge cases),
and only THEN promoted per the locked policy:

- **A=L+E → blocks early** (a balance sheet that doesn't balance is always a data error).
- **Gross-profit tie → stays advisory longer** (net-revenue presenters trip it legitimately).
- **Everything else → advisory; promote per shadow triage.**

Gate F is the surface for promoted checks — its ingestion/calc drift aggregation
now catches systematic identity failures at the universe level.

---

## Tasks

### 2.0 — Characterize current identity state *(first; report-only)* — S
- **Deliverable:** run all existing + proposed checks in shadow across the
  universe; dump per-identity: pass / expected-exception / real-failure counts,
  which tickers, which fields. Establishes the false-positive baseline before any
  promotion (mirrors Gate F 3.1.0).
- **Acceptance:** a table of identity → {pass, exception-category, fail} × tickers.

### 2.1 — Unify A=L+E (I3) + symmetric NCI (I2) — M
- **Single tolerance + one shared NCI-form set** used by BOTH the contract and
  the audit (today: contract 4 forms, audit 2 + $10M floor → a record can pass
  one and be flagged by the other).
- **Symmetric, single-threshold NCI attribution** across FMP and SEC paths
  (today: positive-only ≤5% FMP vs 2% SEC → same filer, different treatment by
  source).
- **Files:** `_schema_contract.py`, `identity_checks.py`, `ttm_derivation.py`,
  `sec_quarterly.py`.
- **Verify:** re-clean the universe in shadow → the two systems now agree on
  every record (no record passes one and fails the other). Value-neutral for
  balancing records; use the oracle-diff on `cleaned.*` to confirm NCI-attribution
  changes don't move NOPAT/ROIC unexpectedly.
- **Promotion:** A=L+E stays Tier-C blocking (already is); the unification just
  removes the contradiction.

### 2.2 — Roll-forward audit on TTM rows (I1) — M
- Extend the 7-identity roll-forward from FY-pairs to **TTM rows** (the freshest,
  most-used data point is currently the least-audited).
- **Files:** `identity_checks.py:1542-1560`.
- **Verify:** shadow-run on TTM rows → characterize what fires; TTM roll-forwards
  legitimately differ (partial-period), so expect a distinct exception category.
- **Promotion:** advisory (TTM roll-forward is inherently noisier).

### 2.3 — Waterfall subtotal ties (I4) — M
- Add a **gross-profit identity** (Revenue − COGS) — currently unchecked anywhere.
- Add a **derived-EBIT-vs-reported tie**: when EBIT is derived from components AND
  a reported `OperatingIncomeLoss` exists, assert they agree (today the raw tag is
  preferred and the derived path only fires when raw is None — a wrong SG&A sum
  flows silently to EBITDA/NOPAT).
- **Files:** `cleaning_engine.py:1688-1707`, new checks in `identity_checks.py`.
- **Verify:** shadow-run → triage. Net-revenue presenters (some financials/retail)
  legitimately trip gross-profit.
- **Promotion:** **gross-profit stays advisory** (per policy); derived-EBIT tie can
  promote once triaged.

### 2.4 — Equity-bridge reads the persisted record (I5) — S
- Make the RE roll-forward read the **persisted record**, not raw XBRL directly
  (today it silently `skipped`s whenever companyfacts isn't cached, with no signal
  distinguishing "passed" from "never ran").
- Emit **distinct `skipped` vs `passed`** statuses.
- **Files:** `identity_checks.py:491`.
- **Verify:** the equity bridge now runs for every persisted ticker (not just
  cached ones); skipped-count drops; passed/skipped are distinguishable in Gate F.
- **Promotion:** advisory.

### 2.5 — Reconcile the ΔNWC definitions (I6) — S (optional/stretch)
- Three unreconciled ΔNWC definitions exist. Pick one canonical definition (or a
  documented mapping) and add a cross-check that they agree in direction/magnitude.
- **Files:** `identity_checks.py:1335`, `cleaning_engine.py:1402/1409`.
- **Promotion:** advisory (diagnostic).

---

## Sequencing
`2.0 characterize → 2.1 unify A=L+E/NCI (the contradiction fix) → 2.4 equity-bridge
(small) → 2.2 TTM roll-forward → 2.3 waterfall ties → 2.5 ΔNWC (optional)`.
Each lands shadow-advisory; promote per 2.0's false-positive baseline + triage.

## Verification protocol (per task)
1. Land the check **advisory in shadow** — no blocking, no value change.
2. **Characterize** across the universe (2.0 harness): pass / exception / fail counts.
3. **Triage** — classify real failures vs legitimate accounting edge cases
   (net-revenue presenters, regulated-utility NCI, first-year priors).
4. For any value-touching change (NCI attribution in 2.1): `diff_oracle.py
   --fail-on material` on `cleaned.*` + `validate_goldens.py`.
5. **Promote** to blocking only where triage shows real-error-only firing, per the
   locked policy. Gate F surfaces promoted checks at the universe level.

## Risks
- **False-positive promotion** → mitigated by 2.0-first + shadow + triage.
- **NCI unification (2.1) is the one value-touching change** — a different NCI form
  can shift equity/liabilities; guard with the oracle-diff + goldens.
- **Legitimate edge cases** (net-revenue presenters, utility NCI, partial-period
  TTM) must stay as *exception categories*, not failures — the audit already has
  this pattern; extend it, don't fight it.

## Decisions (locked from earlier)
- Promotion default **advisory**; A=L+E blocks early; gross-profit stays advisory.
- Measure-before-promote (2.0 characterize first).
- Gate F is the universe-level surface for promoted checks.
