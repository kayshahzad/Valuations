# Phase 2 — remaining tasks: detailed plan

Grounded in the **2.0 characterization** (universe identity baseline) and the
friction discovered while building **2.1a**. Supersedes the task-level detail in
`docs/phase2_plan.md` for the tasks not yet done.

**Done:** 2.0 characterize · 2.1a A=L+E audit↔contract NCI alignment (`084efeb`,
178→1 balance-sheet exceptions).

**2.0 baseline (for reference):** 10,136 checks · **0 true FAILs** · 3,851
categorized exceptions · **FY 10,094 vs TTM 42** · RE roll-forward **520 skips**.

Ordering by leverage-per-risk: **2.4 → 2.3 → 2.2 → 2.1b → 2.5**. (2.1b moved to
near-last: it's the only value-touching one and wants the most guard-rails.)

---

## 2.4 — Equity-bridge / RE roll-forward from the persisted record (I5)  — **recommended next**

**The gap (grounded):** `check_retained_earnings_rollforward` reads
`RetainedEarningsAccumulatedDeficit` (beg/end) and APIC (`_apic_for_year`) from
**raw XBRL** via `loader.xbrl_fact` (`identity_checks.py:~501`, `:~445`). When
companyfacts isn't cached / the tag is absent it returns `skipped` — and
`IdentityCheckResult.skipped` sets `passed=True`, so **520 skips masquerade as
passes.** The freshest equity bridge is unenforced for most of the universe.

**Prerequisite spike (0.5 day):** confirm whether the persisted cleaned record
carries `RetainedEarnings` and APIC (`AdditionalPaidInCapital` /
`CommonStocksIncludingAdditionalPaidInCapital`). Check `company_records` columns
+ `raw_json`. Two branches:
- **If present** → read RE/APIC from the record; the check runs for every
  persisted ticker (no XBRL dependency). *Expected — the cleaner resolves these.*
- **If absent** → add them to the canonical projection first (a small cleaning
  change), then wire the check.

**Fix:**
1. Read RE (beg = prior record, end = current record) and APIC from the persisted
   records, XBRL only as a fallback.
2. Make `skipped` **distinct from `passed`** — a third state so Gate F / the UI
   can tell "ran & closed" from "never ran". (Today `skipped` → `passed=True`.)

**Value-touching?** No — audit-only. **Verify:** re-run 2.0 → the 520 skips drop
sharply; passed/skipped are distinguishable. **Promotion:** advisory. **Risk:**
low; the only subtlety is the skipped/passed status change rippling to any
consumer that counted skips as passes (grep for it).

---

## 2.3 — Waterfall subtotal ties (I4)  — two parts, one has a plumbing step

**The gap (grounded):** gross profit (Revenue − COGS) has **no identity check**,
and a derived EBIT is never tied back to a reported `OperatingIncomeLoss`. During
2.1a I found `GrossProfit` is **in `raw_json`** (used by the 3.3/3.4 cross-source
check) **but not in the `identity_checks.RecordLoader` view** — so a naive
gross-profit identity would skip everywhere.

**2.3a — gross-profit tie (needs the plumbing step):**
1. Add `GrossProfit` (and confirm `COGS`) to the RecordLoader projection so the
   audit can see it.
2. New `check_gross_profit_reconciliation`: `Revenue − COGS − GrossProfit ≈ 0`
   when all three are reported.
3. **Exception category** for net-revenue presenters (banks / some financials /
   retailers that present revenue net of certain costs) — reuse the
   `is_bank_for_display` gate; they legitimately have no clean gross-profit line.

**2.3b — derived-EBIT-vs-reported tie (cleaning layer):** when EBIT is derived
from components AND a reported `OperatingIncomeLoss` exists, assert they agree
(today the raw tag is preferred and the derived path only fires when raw is None,
so a wrong SG&A sum flows silently to EBITDA/NOPAT — `cleaning_engine.py:1688`).

**Value-touching?** No (both are checks; 2.3b just adds an assertion/flag).
**Verify:** shadow-run → characterize what fires → triage. **Promotion:**
**gross-profit stays advisory** (net-revenue presenters), derived-EBIT tie can
promote once triaged. **Risk:** low; the plumbing step (2.3a.1) is the only real
work.

---

## 2.2 — Roll-forward audit on TTM rows (I1)  — needs the right formulation

**The gap:** roll-forwards (RE/cash/PPE/debt/WC) run **FY-pairs only**
(`run_all_checks_for_ticker` loops `fys_sorted`); balance-sheet + FCF-pathway are
the only identities touching TTM. TTM is the freshest, least-audited row.

**The nuance (discovered):** a naive "call the same roll-forward on (latest-FY,
TTM)" is **wrong** — TTM *flows* are trailing-12-months, but the balance change
from FY-end→TTM-end is a *stub period*. The flows and the balance delta don't
align, so it would false-fail everywhere.

**Correct formulations (pick per identity):**
- **Balance-sheet & FCF-pathway** already run on TTM (single-period, no prior
  needed) — keep.
- **Roll-forwards:** compare the TTM row against the **same-TTM one year prior**
  (TTM_t vs TTM_{t−1}) when a prior TTM exists — then both flows and balance delta
  are trailing-12m and aligned. Where no prior TTM exists, `skipped`
  (distinct state, per 2.4).

**Value-touching?** No. **Verify:** shadow-run on TTM; TTM roll-forwards are
inherently noisier → expect a distinct exception category. **Promotion:**
advisory (never blocking — partial-period). **Risk:** medium — the prior-TTM
alignment must be right or it's noise; only build after 2.4's distinct-skipped
state lands (reused here).

---

## 2.1b — Symmetric, unified NCI attribution (I2)  — the value-touching one

**The gap (grounded):** two ingest paths handle the A=L+E residual differently:
- **FMP TTM** (`ttm_derivation.py:229-231`): `resid = A−(L+E); if 0 < resid ≤
  0.05·|A|: MinorityInterest = resid` — **positive-only, 5% cap**.
- **SEC** (`sec_quarterly.py:498-499`): `gap = |A−(L+E)|; if gap > 0.02·|A|:
  derive TotalLiabilities` — **2% threshold, different mechanism** (derives L, not NCI).

So the same filer gets different treatment by source, and the FMP path ignores
negative residuals (equity already over-stating NCI).

**Fix:** one shared residual-attribution routine used by both paths — symmetric
(both signs), single threshold, single mechanism (attribute to NCI vs derive L
decided consistently). Align the threshold with 2.1a's now-unified A=L+E tolerance.

**Value-touching? YES** — this changes persisted `MinorityInterest` /
`TotalLiabilities` for affected filers → NOPAT/ROIC/EV downstream.

**Verify (full Phase-1 protocol):** re-clean → `diff_oracle.py --fail-on
material` on `cleaned.*` → **only intended movers** → Schwab-validate them →
`validate_goldens.py` → re-lock goldens (D4). One universe re-ingest at the end.
**Promotion:** the A=L+E gate itself stays as-is (Tier-C); this only makes the
attribution consistent. **Risk:** highest in Phase 2 — do it last, with the
oracle + goldens as the guard, exactly like Phase 1's value-changing PRs.

---

## 2.5 — Reconcile the three ΔNWC definitions (I6)  — optional/stretch

Three unreconciled ΔNWC definitions (`identity_checks.py:~1335` AR+Inv−AP · 
`cleaning_engine.py:1402` (CA−cash)−CL · `:1409` rev×0.03 proxy). Pick one
canonical definition (or a documented mapping) + a cross-check they agree in
direction/magnitude. Advisory diagnostic. Low priority.

---

## Cross-cutting: verification & promotion (unchanged)
1. Land each check **advisory in shadow**.
2. **Characterize** across the universe (reuse the 2.0 harness).
3. **Triage** real failures vs legitimate edge cases (net-revenue presenters,
   utility NCI, partial-period TTM, first-year priors).
4. Value-touching changes (only 2.1b) → oracle-diff on `cleaned.*` + goldens +
   Schwab on movers + re-lock.
5. **Promote** only where triage shows real-error-only firing. A=L+E blocks;
   gross-profit stays advisory; everything else advisory→promote per triage.
6. Gate F is the universe-level surface for anything promoted to blocking.

## Sequencing & rough sizing
`2.4 (S–M, spike first) → 2.3a plumbing + 2.3b (M) → 2.2 (M, reuses 2.4's
skipped state) → 2.1b (M, value-touching, full guard) → 2.5 (S, optional)`.

## Shared building block to land in 2.4 and reuse
A **distinct `skipped` result state** (not `passed=True`). 2.4 needs it (mask the
520), 2.2 needs it (no-prior-TTM), and Gate F benefits (tell "ran & closed" from
"never ran"). Build it once in 2.4.
