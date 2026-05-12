# Phase 0 — Calculation Anomaly Catalog

Empirical inventory of known data inconsistencies in DuckDB as of 2026-05-10.
Each entry becomes a test case for the calculation-layer validation framework
(`aletheia/calculations/_guards.py`, scheduled). The framework's success metric:
**every entry on this list must be caught by an automated validation rule**
once Phase 1+ ships. If a rule misses any entry, the rule set is insufficient.

## How to read

- **ID**: anomaly identifier (A1, A2, ...) — referenced in test names
- **Pattern**: the class of bug (sign, identity violation, fallback, etc.)
- **Affected**: tickers / row count where observed
- **Verified**: empirically confirmed via DB query (yes/no)
- **Status**: `fixed-going-forward` (code change shipped, stale data may remain)
  / `open` (active bug, no fix yet) / `known-edge-case` (legitimate; framework
  must accommodate, not reject)
- **Severity**: P0 (silent failure produces wrong number cited in thesis) / P1
  (visible degradation but not silently wrong) / P2 (cosmetic / display only)
- **Detection rule**: the validation primitive that should catch this case in
  the new framework. If `none defined yet`, this anomaly drives a new rule.

---

## A1 — Stale FY2025 TTM rows in DuckDB

| | |
|---|---|
| **Pattern** | TTM rolling-window obsolescence; ingest creates new (fiscal_year, period_end_date) rows but doesn't garbage-collect older windows |
| **Affected** | 10 tickers: ABT, AMD, AMZN, CAT, GOOGL, HD, ITW, LLY, LOW, META |
| **Verified** | Yes — `SELECT ticker FROM company_records_latest WHERE period='TTM' AND fiscal_year=2025` returns 10 rows |
| **Status** | open — stale rows have `quality_score=0` and pre-fix negative CapEx; coexist with fresh FY2026 TTM rows that are correct |
| **Severity** | P1 — the UI picks the latest period_end_date so users see correct data, but the rows pollute audits and any consumer that picks by fiscal_year (not period_end_date) gets stale data |
| **Detection rule** | Identity check: for any (ticker, period='TTM') pair with multiple fiscal_years, only one should exist OR the older one should be marked superseded. Add a `tombstone_at` column at ingest time and filter from `company_records_latest` view. |
| **Test fixture** | Insert a stale FY2025 TTM row for a test ticker; assert it does not appear in the dashboard's identity reads. |

## A2 — SMCI share-count inconsistency across split boundary

| | |
|---|---|
| **Pattern** | Pre/post split-adjustment inconsistency; FY2024 shares appear retroactively split-adjusted but FY2025 + TTM appear non-split-adjusted |
| **Affected** | SMCI specifically; the 10:1 split was Oct 1, 2024 (FY2025 was filed post-split) |
| **Verified** | Yes — FY2023: 559.7M, FY2024: 6.02B (10× FY2023, post-split), FY2025: 628.4M (≈FY2023, pre-split scale), TTM: 673.6M (matches FY2025 scale) |
| **Status** | open — neither the cleaning engine nor the TTM derivation detects the inconsistency |
| **Severity** | P0 — per-share metrics (EPS, BVPS) across fiscal years are non-comparable; YoY EPS growth math produces garbage; Graham number computation uses inconsistent BVPS |
| **Detection rule** | Identity check on shares-outstanding YoY change: if `shares_diluted[fy] / shares_diluted[fy-1]` is outside `[0.7, 1.3]` AND no documented split event explains it, flag as `consistency_split_adjustment_mismatch`. Cross-reference against FMP `/historical-stock-split` endpoint. |
| **Test fixture** | A2-style row where FY2024 shares are 10× FY2023 with no split event explanation. |

## A3 — Negative-equity tickers in universe

| | |
|---|---|
| **Pattern** | `raw_TotalEquity` legitimately negative for buyback-heavy mature companies (accumulated treasury stock exceeds paid-in capital) |
| **Affected** | 10 rows: LOW (5 years running, peak –$14.25B), HD (4 historical years), AMD (FY2015) |
| **Verified** | Yes — `SELECT WHERE raw_TotalEquity < 0` returns these 10 rows |
| **Status** | known-edge-case — these are correctly reported; the framework must accommodate, not reject |
| **Severity** | P1 if framework hard-fails on this (would reject LOW entirely from the universe); P0 for downstream consumers that compute ROE = NetIncome / TotalEquity and produce nonsense |
| **Detection rule** | Tier 2 soft-flag: `raw_TotalEquity < 0` triggers a structured warning + per-ticker exception registry entry. ROE specifically should refuse to compute on negative-equity inputs (returns "n_a" with reason). Documented as an override case for the listed tickers. |
| **Test fixture** | Pass LOW FY2024 record to ROE function; assert returns "n_a" with reason="negative_equity_denominator", not a nonsense large negative ROE. |

## A4 — NaN `clean_NormalizedEBIT` on every TTM row (universe-wide)

| | |
|---|---|
| **Pattern** | TTM derivation populates `derived_EBITDA`, `clean_FCF`, but skips `clean_NormalizedEBIT` because EBIT normalization is FY-only logic in the cleaning engine |
| **Affected** | 34 of 34 TTM tickers (100%) |
| **Verified** | Yes |
| **Status** | open — addressed at calc-consumer side by filtering reverse_dcf to FY rows (today's commit), but the data layer still emits NaN |
| **Severity** | P0 — this is the MDT root cause. With NaN EBIT, reverse-DCF + DCF + ratio engine all coerced to 0 silently and produced fake-plausible outputs. |
| **Detection rule** | Schema-contract assertion at end of TTM derivation: if `record.period == 'TTM'` and any of {clean_NormalizedEBIT, clean_NOPAT, clean_CashTaxRate} is None, refuse to persist. OR populate them via quarterly normalization (proper Option 2 fix). |
| **Test fixture** | TTM record missing clean_NormalizedEBIT; ingest must refuse with `schema_contract_violation:clean_NormalizedEBIT`. |

## A5 — NaN `clean_NOPAT` on TTM rows (universe-wide)

| | |
|---|---|
| **Pattern** | Same root cause as A4 — TTM derivation doesn't compute NOPAT |
| **Affected** | 42 rows across 34 tickers |
| **Verified** | Yes |
| **Status** | open (paired with A4) |
| **Severity** | P0 — same propagation path as A4 |
| **Detection rule** | Same as A4: schema contract refuses persistence when None on TTM |
| **Test fixture** | Pair with A4 |

## A6 — Pre-fix CapEx sign on TTM rows (historical)

| | |
|---|---|
| **Pattern** | TTM derivation negated CapEx to "match FMP convention"; FY cleaning engine stores positive via abs() |
| **Affected** | Pre-fix: every TTM row in DB. Post-fix: only stale FY2025 TTM rows (overlapping A1) |
| **Verified** | Yes — current FY2026 TTM rows all positive (AAPL +$11.05B, MSFT +$97.2B, etc.); stale FY2025 still negative |
| **Status** | fixed-going-forward — code fix shipped today, data already re-ingested for current rows |
| **Severity** | P0 historically — silently flipped sign propagated through reverse_dcf, FCFF projection, ratio engine. Caught by MDT case study. |
| **Detection rule** | Tier 2 range check: `abs(capex) / revenue ∈ [-0.30, 0.50]` (Tier 2 because legitimate net-divestiture years can have negative). The Tier 2 framing is critical — Tier 1 strict-nonneg would false-positive on GE-class breakups. |
| **Test fixture** | TTM record with sign-flipped CapEx (capex=-1B, revenue=10B); range check on capex/revenue should pass (-10% is in range) but FCF identity should fail because OpCF + |CapEx| won't match clean_FCF. |

## A7 — Pre-fix `overall_quality_score=0` on SEC-derived TTM rows

| | |
|---|---|
| **Pattern** | `derive_ttm_from_sec` forgot to stamp the provisional `overall_quality_score=1.0` that the FMP path stamps |
| **Affected** | Pre-fix: every SEC-derived TTM. Post-fix: only stale rows (overlap with A1) |
| **Verified** | Yes — current FY2026 TTM all show 1.0; stale FY2025 TTM still show 0.0 |
| **Status** | fixed-going-forward |
| **Severity** | P2 — display-only (UI showed "Data quality 0.00"); didn't affect calc outputs |
| **Detection rule** | Schema-contract assertion at end of TTM derivation: `record.overall_quality_score` must be in `[0, 1]` AND non-zero (zero indicates the field was never set). |

## A8 — NEE accounting equation (fixed previously)

| | |
|---|---|
| **Pattern** | TotalEquity mapped from the wrong XBRL tag; should prioritize `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| **Affected** | NEE specifically; other utilities may share pattern |
| **Verified** | Fixed in commit 8508f92 |
| **Status** | fixed-going-forward; framework should catch any recurrence |
| **Severity** | P0 historically — TotalEquity off by minority-interest amount, broke A=L+E identity |
| **Detection rule** | **Arithmetic identity check** (the most powerful kind, per prompt): assert `TotalAssets ≈ TotalLiabilities + TotalEquity` within 0.5% tolerance. NEE's pre-fix data violated this identity. |
| **Test fixture** | A row where `A ≠ L + E` by >0.5%; ingest must refuse. |

## A9 — NSC SEC-fetch hang

| | |
|---|---|
| **Pattern** | unknown — `librarian_agent` hangs on NSC's SEC EDGAR fetch indefinitely. No timeout, no error. |
| **Affected** | NSC specifically; possibly other rail/transport filers |
| **Verified** | Yes — Universe re-run hung at NSC step for 9+ hours |
| **Status** | open — diagnosed as hang but root cause not yet determined |
| **Severity** | P1 — affects ingest reliability, not calculation correctness |
| **Detection rule** | Out of scope for calc validation; this is an ingest infrastructure issue. Add a per-step timeout to librarian_agent (separate fix). |

## A10 — Foreign-filer FX conversion (working correctly)

| | |
|---|---|
| **Pattern** | ASML/TSM report in EUR/TWD; TTM derivation FX-converts to USD using FY-avg rate |
| **Affected** | ASML, TSM (universe foreign filers) |
| **Verified** | Yes — TTM records carry `fx_converted: true` in `fmp_validation` receipt |
| **Status** | known-edge-case — working as designed |
| **Severity** | n/a |
| **Detection rule** | Receipt-stamp assertion: when ticker `is_ifrs_filer=True`, the persisted TTM record must have `fmp_validation.fx_converted == True` and `reported_currency != "USD"`. |

## A11 — Implicit U.S. statutory tax-rate fallback (`or 0.21`)

| | |
|---|---|
| **Pattern** | Multiple callers use `tax_rate or 0.21` when DB returns None — silently applies U.S. statutory rate to international filers |
| **Affected** | Logic-wide: at least reverse_dcf.py and dcf_engine.py. Tickers most affected: MDT (Irish, ~14-16% effective), ASML (Dutch), TSM (Taiwan) |
| **Verified** | Code search confirms `or 0.21` pattern present |
| **Status** | open — flagged as a Phase 2 design decision (use company's most recent FY effective rate, not statutory) |
| **Severity** | P0 for international filers — silently substitutes wrong tax rate, affects every DCF and reverse-DCF output |
| **Detection rule** | Per-function: replace `or 0.21` with explicit `tax_rate_fallback` param. Default fallback: company's most recent FY effective tax rate from DB. Add range check `tax_rate ∈ [-1.0, 1.0]` (Tier 3 — can be negative in tax-benefit years). |

## A12 — Negative `derived_OperatingIncome` and `OperatingCF`

| | |
|---|---|
| **Pattern** | Legitimate negative values for distressed / loss-making / growth-investment years |
| **Affected** | SMCI FY2017 OperatingCF, AMD FY2015 EBIT, etc. — historical loss years |
| **Verified** | Yes — exists in historical FY data |
| **Status** | known-edge-case |
| **Severity** | P1 if framework hard-fails on these |
| **Detection rule** | Tier 2 (not Tier 1) — soft-flag negative, validate via ratio range `ebit/revenue ∈ [-2.0, 0.80]` |

## A13 — Goodwill-level vs goodwill-change confusion

| | |
|---|---|
| **Pattern** | `raw_Goodwill` (level) is non-negative; `ΔGoodwill` (change) can be negative (impairments) |
| **Affected** | Potentially every ticker with M&A activity |
| **Verified** | Not specifically queried, but flagged in prompt |
| **Status** | not-yet-tested — schema doesn't currently track goodwill change explicitly |
| **Severity** | P2 — not in our current calc layer |
| **Detection rule** | When goodwill ingest is added: Tier 1 strict-nonneg on level, Tier 2 soft-flag on change |

---

## Summary

| Severity | Count | Status |
|---|---|---|
| **P0** | 6 (A2, A4, A5, A6, A8, A11) | 2 fixed today, 4 still open |
| **P1** | 5 (A1, A3, A9, A10, A12) | 2 known-edge-case (OK), 3 still open |
| **P2** | 2 (A7, A13) | A7 fixed today |

**Coverage check before Phase 1 inventory**: the validation framework must catch:
- 4 **arithmetic-identity** patterns (A6 FCF identity, A8 A=L+E, A4/A5 NOPAT-NormalizedEBIT identity, A11 tax-rate range)
- 2 **schema-contract assertions** (A4/A5 required-field-on-TTM, A7 score-not-zero)
- 3 **range checks** (A6 capex/revenue, A11 tax_rate, A12 ebit/revenue)
- 2 **identity checks across periods** (A1 supersession, A2 split-adjustment continuity)

If the Phase 2 `_guards.py` module ships without primitives for every category in that list, it's incomplete.

## Tickers most affected (concentration of anomalies)

| Ticker | Anomaly count | Notes |
|---|---|---|
| LOW | 2 (A1, A3) | negative-equity multi-year; stale TTM |
| HD | 2 (A1, A3) | historical negative equity; stale TTM |
| MDT | 1 (A4 → triggered the whole investigation) | also affected by A11 (Irish tax rate) |
| SMCI | 1 (A2) | the only ticker with a split-adjustment pattern |
| NSC | 1 (A9) | ingest-layer issue, not calc |
| All TTM tickers | A4, A5 | universe-wide |

This catalog is the framework's success criteria. Phase 1 inventory proceeds against this baseline.

---

## Phase 2 dry-run addenda (added 2026-05-11 after first hard-mode universe run)

## A14 — Visa (V) missing `shares_diluted` on every FY row

| | |
|---|---|
| **Pattern** | V's cleaning-engine output has neither `raw_SharesDiluted` nor `clean_SharesDiluted` populated. Investigation confirms no share-related keys appear in `raw_json` or `clean_json`. The XBRL filings DO contain dilutive-share disclosures; the cleaning engine's tag-resolution is failing to extract them. |
| **Affected** | V (Visa), every FY row 2009–2025 |
| **Verified** | Yes — direct DB query on V's `raw_json` returns empty share-key list |
| **Status** | open — ingest bug, surfaces during Phase 2 hard-mode dry-run |
| **Severity** | P0 — breaks every per-share metric (EPS, BVPS, market_cap reconciliation) on V |
| **Detection rule** | Schema-contract assertion already catches it (missing required Tier-1 field). Root cause needs an ingest-layer fix in `tag_resolver` or `cleaning_engine`. |
| **Test fixture** | After fix, V should populate `raw_SharesDiluted` for every FY row; schema contract passes. |

## A15 — AXP historical Revenue gap (FY2011–FY2015)

| | |
|---|---|
| **Pattern** | AXP's recent FYs (FY2016+) populate `raw.Revenue` correctly ($41.3B for FY2024). Older years (FY2011–FY2015) have None for Revenue. AXP is `routing_required` business_model (bypasses FCFF DCF anyway), but the schema-contract assertion still expects revenue for any FY row. |
| **Affected** | AXP FY2011–FY2015 (5 rows). Other `routing_required` tickers (JPM, BRK-B) may have similar coverage gaps. |
| **Verified** | Yes |
| **Status** | mitigated — schema contract now uses `_NON_FCFF_REQUIRED_TIER1` for routing_required/ddm_required/embedded_value_required which accepts Revenue OR InterestIncome OR NetInterestIncome as the income measure. AXP latest year passes. Historical FY2011–FY2015 may still fail if NONE of those tags are populated; that's a separate backfill question. |
| **Severity** | P1 — only affects historical data; current FYs work correctly |
| **Detection rule** | Schema-contract path-walk now accepts multiple income tag locations for non-FCFF business models. |
| **Test fixture** | A non-FCFF ticker with only `NetInterestIncome` populated (no `Revenue`) should pass the schema contract. |

## Phase 2 dry-run results (after refinement)

After two refinements (FCF identity auto-detects pre/post ASC 842 transition; non-FCFF business models use relaxed required-field set), hard-mode dry-run on 681 DB records:

- **599 pass** (up from 564, +35)
- **82 violate** (down from 117, −35)

Remaining violations by category:
- 49 A=L+E accounting-equation drifts (NEE utility tags, TSLA, GOOGL, CAT — real Phase 6 triage)
- 21 shares_diluted missing (V universe-wide per A14; UNH some years)
- 9 depreciation missing (financial-services may legitimately not have this)
- 3 capex_to_revenue out of range (TSLA growth-investment, range-calibration question)

These are the Phase 6 triage categories the user's migration plan anticipated:
- True data bugs → A14 (V shares), some A=L+E cases
- Legitimate edge cases → financial-services depreciation
- Validation-rule-too-strict → capex/revenue range may need recalibration for hyper-growth
- Fallback-was-load-bearing → likely no cases here (the FCF auto-detect handled the only such case)

