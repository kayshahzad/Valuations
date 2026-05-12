# Phase 6 Triage — 52 Schema-Contract Violators

Categorization of the schema-contract violators surfaced by the Phase 2
framework in hard mode, into the four buckets the migration plan
defined:

- **Bucket A — True data bugs**: real ingest issues requiring upstream fix
- **Bucket B — Legitimate edge cases**: real business reality; add to override registry
- **Bucket C — Validation rule too strict**: tolerance too tight or identity incomplete; recalibrate framework
- **Bucket D — Fallback was load-bearing**: caller relied on the silent coerce; needs caller-side opt-in

Source: hard-mode dry-run on `company_records_latest` (681 records, 92.3% pass rate).

---

## Bucket A — True Data Bugs (28 violators)

These need fixes at the ingest layer (cleaning engine or tag resolver). The framework correctly surfaces them as violations.

### A1. V (Visa) shares_diluted missing — 17 rows, every FY 2009-2025

- **Anomaly catalog ref**: A14
- **Magnitude**: complete absence — no `raw_SharesDiluted`, no `clean_SharesDiluted`
- **Root cause**: cleaning engine's `tag_resolver` fails on Visa's XBRL share-count tags. V's 10-K filings DO contain dilutive-share disclosures; the extraction path doesn't recognize them.
- **Action**: investigate `aletheia/data/tag_resolver.py` for Visa-specific XBRL pattern. Likely V uses a non-standard ConcePT label that the resolver's fallback chain doesn't enumerate.
- **Owner**: data-ingest fix
- **Severity**: P0 — V's universe-wide per-share metrics (EPS, BVPS, market_cap reconciliation) all degrade silently
- **Estimated effort**: 1-2 hours once a sample tag pattern is identified

### A2. NEE historical A=L+E drift — 10 rows, FY2009-2018 (35-57%)

- **Anomaly catalog ref**: A8 (extended)
- **Magnitude**: $16-27B unexplained per year
- **Root cause**: commit 8508f92 fixed the TotalEquity mapping for recent NEE years (prioritized `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`). Historical years FY2009-2018 still use the older, broken mapping. The cleaning engine doesn't re-process historical rows when the resolver is updated.
- **Action**: re-ingest NEE FY2009-2018 with the updated tag mapping. OR add per-ticker override marking these specific years as known-legacy.
- **Owner**: data-ingest backfill
- **Severity**: P1 — these are historical years not used in current DCF (which uses latest FY); but they pollute trend analyses and audits
- **Estimated effort**: 30-min re-ingest

### A3. TXN FY2026 TTM A=L+E drift 27.13%

- **Magnitude**: $7.3B unexplained on a $34.4B total assets base
- **Root cause**: TTM derivation almost certainly missing a balance-sheet component (likely a liability tag that's populated in 10-K but not in 10-Q, or vice versa). TXN has significant working-capital + lease items.
- **Action**: investigate `sec_quarterly.py` / `ttm_derivation.py` balance-sheet stock-item path for TXN specifically. Check what TotalLiabilities or TotalEquity component is missing.
- **Owner**: data-ingest fix
- **Severity**: P0 — fresh TTM data, current production
- **Estimated effort**: 1 hour to diagnose

### A4. UNP FY2026 TTM A=L+E drift 8.58%

- **Magnitude**: $5.5B unexplained on $69.6B
- **Root cause**: same pattern as A3 (TXN). UNP is a rail operator with significant operating-lease balances post-ASC-842.
- **Action**: bundle investigation with A3 — likely the same root cause across TTM-only filers
- **Owner**: data-ingest fix
- **Severity**: P1
- **Estimated effort**: included in A3 fix

### A5. WMT TTM A=L+E drift 2.36%

- **Magnitude**: $6.6B on $284.7B
- **Root cause**: same TTM-balance-sheet pattern (likely)
- **Action**: same as A3/A4
- **Severity**: P2 — small relative drift; lower priority

### A6. COST FY2026 TTM A=L+E drift 3.62%

- **Magnitude**: $3B on $83.6B
- **Root cause**: same TTM pattern
- **Action**: same as A3/A4
- **Severity**: P2

### A7. LOW FY2014/2015 A=L+E drift (5.59% / 6.89%)

- **Magnitude**: $2-4B per year
- **Root cause**: LOW has had massive treasury-stock and buyback activity creating negative-equity periods. Pre-2018 may have had additional items (deferred income tax positioning) not captured in standard `TotalLiabilities + TotalEquity`.
- **Action**: investigate LOW's specific balance sheet for these years; likely needs an additional term like deferred tax noncurrent or pension obligation that's outside `TotalLiabilities`.
- **Severity**: P1
- **Estimated effort**: 1 hour

---

## Bucket B — Legitimate Edge Cases (5 violators)

Real business reality; add to override registry.

### B1. TSLA FY2011-2014 shares_diluted (4 rows)

- **Anomaly catalog ref**: A15-style
- **Root cause**: Tesla pre-IPO / early-public-company XBRL coverage gap. The shares data wasn't filed in standard form during this period.
- **Action**: add override `TSLA → shares_diluted_historical_gap` covering FY2011-2014; mark as legitimate-not-data-bug
- **Owner**: override registry
- **Severity**: P2 — historical years; current DCF unaffected
- **Estimated effort**: 5 minutes (registry entry)

### B2. ORCL FY2026 TTM capex/revenue 75.3%

- **Anomaly catalog ref**: A18
- **Root cause**: Oracle's AI data-center buildout for OCI. ORCL has publicly disclosed massive CapEx acceleration in 2025-26.
- **Action**: needs analyst verification first. If confirmed, add override `ORCL → capex_intensity_ai_buildout` with review date Q3 2026 (when next FY should normalize)
- **Owner**: analyst review → override registry
- **Severity**: P1 — needs human-in-the-loop confirmation before accepting as legitimate
- **Estimated effort**: analyst review then 5-min registry entry

---

## Bucket C — Validation Rule Too Strict (19 violators)

Drifts in the 0.5-1.7% range — just above the 0.5% accounting-equation tolerance. Pattern suggests one or more identity-side terms missing (likely TreasuryStock NCI variant, pension OCI, or similar minor adjustments).

### C1. Small A=L+E drifts (CAT, MCO, NVDA, NSC, TSLA, TSM, WMT) — 14 rows in 0.53%-1.68% range

- **CAT FY2009/2010/2011**: 0.58-0.80%
- **WMT FY2013/2016/2017**: 0.72-0.74%
- **TSLA FY2015/2016**: 0.59%-1.56%
- **MCO FY2014/2015**: 1.54-1.68%
- **NVDA FY2016**: 1.19%
- **NSC FY2026 TTM**: 1.06%
- **TSM FY2024**: 0.53%

**Root cause hypothesis**: the current A=L+E identity is `TotalAssets ≈ TotalLiabilities + TotalEquity + RedeemableNoncontrollingInterest`. There's another mezzanine-equity-class adjustment we're missing — likely one or more of:
- `PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesNoncurrent` (CAT, MCO have these)
- `DeferredCompensationLiabilityClassifiedNoncurrent` (financial-services pattern)
- `CommitmentsAndContingencies` (some filers report this as a tiny line item)
- Stock-warrants-issued or similar mezzanine items

**Action**: investigate one ticker per pattern (CAT FY2010 + MCO FY2014 + WMT FY2017) to identify the missing identity term. Once known, extend the schema_contract identity. Likely resolves the entire cluster.

**Alternative quicker fix**: widen the `accounting_equation_a_eq_l_plus_e` tolerance from 0.5% to 1.7% for FY rows. Would resolve all 14 without identifying the underlying term. NOT recommended — losing detection power.

**Severity**: P2 individually; P1 in aggregate (14 violations is meaningful)
**Estimated effort**: 2-3 hours to investigate + 1-line identity extension

### C2. LOW FY2023 A=L+E drift 1.87%

- Same pattern as C1 but more recent year. LOW's negative-equity issue likely interacts with the missing identity term.
- **Action**: included in C1 fix
- **Severity**: P2

### C3. NEE FY2022/2023 A=L+E drift 0.70-0.71%

- Recent NEE years (FY2022 and FY2023). Not part of the historical pre-fix A2 set; small drifts.
- **Root cause**: same missing identity term (likely a pension or deferred-compensation item for utilities)
- **Action**: included in C1 fix
- **Severity**: P2

---

## Bucket D — Fallback Was Load-Bearing (0 violators)

No violators in this category. The framework's design (Tier-2 soft-flags on legitimate-negative fields; explicit fallback parameters required on calc functions) appears to have caught the load-bearing cases via design rather than triage. The MDT-incident tax_rate fallback is a Phase 3 design item separately, not a Phase 6 violator.

---

## Summary

| Bucket | Count | Action |
|---|---|---|
| **A — True data bugs** | 28 | Fix at ingest: V shares (1-2hr), NEE historical re-ingest (30min), TXN/UNP/WMT/COST TTM balance-sheet (~2hr total), LOW historical (1hr) |
| **B — Legitimate edge cases** | 5 | Override registry entries: TSLA pre-2015 shares, ORCL AI buildout (pending analyst confirm) |
| **C — Rule too strict** | 19 | Investigate missing identity term (pension/deferred-comp/treasury variant); 2-3hr identification + 1-line extension |
| **D — Fallback load-bearing** | 0 | n/a |

**Total estimated effort to resolve all 52: ~10-12 working hours.** Most of the time is investigation (A3 + C1); the actual fixes are small.

## Recommended order

1. **C1 investigation** (highest leverage — one identity term likely resolves 14-19 violators at once)
2. **A1 V shares** (universe-wide P0; quick win)
3. **A3-A6 TTM A=L+E pattern** (P0/P1; likely common root cause)
4. **A2 NEE historical re-ingest** (30 min)
5. **A7 LOW historical** (probably resolved by C1 fix)
6. **B1-B2 override registry entries** (5 min each)

After all six steps: expected pass rate **98-99%** (residual is ORCL pending analyst confirmation + 1-2 corner cases).

## Then — Phase 6 migration

With pass rate above 98%, the shadow → soft → hard migration becomes safe:
- **Week 1**: flip `ALETHEIA_GUARD_MODE=shadow` in production; collect warning logs for a week
- **Week 2**: triage any new warnings; flip to `soft` (UI surfaces "data quality warning" affordance)
- **Week 3**: build UI affordance for `unavailable` field rendering
- **Week 4**: flip to `hard` on a per-function basis (`mode_override="hard"` on the most critical, leave others in `soft`)
- **Week 5+**: gradually promote remaining functions from `soft` to `hard` as confidence builds

The three-state migration with per-function override means we never have to flip everything at once. The kill switch (`ALETHEIA_GUARD_MODE` env var) provides instant rollback to any earlier state.
