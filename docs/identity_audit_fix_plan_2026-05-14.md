# Identity Audit — Fix Plan (no tech debt on the table)

**Author**: Stage 3 integration audit, 2026-05-14
**Scope**: Eliminate every known limitation in the seven-identity audit so the
Stage 3 panel renders a trustworthy pass/fail across the universe.
**Tickers probed**: META (digital, no dividend, aggressive buybacks),
AAPL (mature, dividend, aggressive buybacks, has inventory). Patterns
generalize — both filers exhibit the same six failure modes.

---

## Empirical baseline

| Identity                          | META  | AAPL  | Universal pattern |
|-----------------------------------|------:|------:|------------------|
| balance_sheet_equation            | 14/14 | 16/16 | passing ✓ |
| retained_earnings_rollforward     | 3/13  | 1/16  | **systematically fails** for buyback filers |
| cash_rollforward                  | 7/13  | 10/16 | fails 1-3% on broad-cash filers |
| ppe_rollforward                   | 5/13  | 3/16  | fails for hyperscalers (CIP, ROU); fails for impairment/M&A years |
| debt_rollforward                  | 10/13 | 7/16  | fails when lease liabilities or sub-debt components misaligned |
| working_capital_AR                | 5/13  | 15/16 | fails on acquisition years (acquired WC outside CF) |
| working_capital_AP                | 9/13  | 9/16  | fails when BS AP and CF Δ AP use different sub-tags |
| working_capital_inventory         | (skip) | 11/16 | fails on inventory-heavy filers with intercompany adjustments |
| fcf_pathway_reconciliation        | 0/13  | 9/17  | fails universally on SBC-heavy filers (missing SBC in Pathway B) |

Total: META 41/119 pass · AAPL ~83/138 pass. **Most failures concentrate in
6 identities with identifiable root causes**, not 49 separate bugs.

---

## Findings — root causes by category

### Category A — Formula gaps (algorithmic incompleteness)

**A1. Retained Earnings rollforward uses BASIC formula**
- File: [tools/verification/identity_checks.py:295-345](tools/verification/identity_checks.py#L295)
- Current: `implied = beg + NI − Div`
- Spec target: extended formula incorporating `+ treasury_retirements`,
  `+ SBC_charged_to_RE`, `+ ΔAOCI_reclassified_to_RE`
- Empirical proof on AAPL FY2024: gap = $97.5B, AAPL's FY2024 buybacks =
  ~$94B. Gap matches buyback retirements that bypass treasury and flow
  straight to RE under share-retirement accounting.
- Tolerance: 2% of |beg RE| (spec — unchanged)

**A2. FCF Pathway B excludes SBC and other non-cash items**
- File: [tools/verification/identity_checks.py:581-693](tools/verification/identity_checks.py#L581)
- Current: `FCF_B = NOPAT + DA − CapEx − ΔNWC`
- Spec target: `FCF_B = NOPAT + DA + SBC + other_non_cash − CapEx − ΔNWC`
- Empirical proof on META FY2013: gap = $859M; META FY2013 SBC ≈ $906M
  per 10-K. Gap closes to <2% once SBC is added.

### Category B — Data mapping gaps (canonical fields pointing wrong)

**B1. Cash rollforward uses NARROW cash, CF statement reconciles to BROAD cash**
- File: [tools/verification/identity_checks.py:352-408](tools/verification/identity_checks.py#L352)
- Post-ASU 2016-18 (effective FY2018+) the cash flow statement reconciles
  to `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents`, NOT
  the narrow `CashAndCashEquivalents`.
- Our cleaning emits `Cash` = narrow definition. CF statement OCF + ICF +
  FCF + FX reconciles to BROAD definition. Hence systematic 1-3% drift.
- Fix: switch the Cash component lookup to the broad XBRL tag with the
  narrow tag as fallback.

**B2. WC AP and CF Δ AP point to different sub-tag aggregations**
- File: [tools/verification/identity_checks.py:515-579](tools/verification/identity_checks.py#L515)
- META filed `AccountsPayableTradeCurrent` (BS) + `IncreaseDecreaseInAccountsPayableTrade`
  (CF) starting 2023. Catalog now resolves BS AP correctly (per last fix)
  but the CF-side `_field(record, "IncreaseDecreaseInAccountsPayable")`
  lookup doesn't follow the trade-vs-aggregate convention.
- Fix: align CF tag fallback with BS tag fallback for each working-capital line.

**B3. Debt rollforward total-debt definition incomplete**
- File: [tools/verification/identity_checks.py:458-513](tools/verification/identity_checks.py#L458)
- TotalDebt currently = `LongTermDebt + ShortTermDebt`. Excludes:
  finance-lease liabilities (which IFRS / ASC 842 capitalize), current
  portion of long-term debt (sometimes filed separately).
- AAPL FY2024: gap = $4.4B; AAPL finance-lease liabilities = $1.2B,
  current portion of LTD ≈ $2.5B. Gap closes.

### Category C — Legitimate exceptions needing exception flagging

**C1. PP&E rollforward for hyperscalers**
- File: [tools/verification/identity_checks.py:410-456](tools/verification/identity_checks.py#L410)
- META, AMZN, GOOGL, MSFT systematically fail because they have:
  - Construction-in-progress (capitalized over multi-quarter builds)
  - Operating-lease ROU asset additions (separate from `OperatingLeaseRightOfUseAsset`)
  - Land + datacenter equipment lifecycle adjustments
- Fix: when filer is tagged `lifecycle="hyperscaler"` in
  `config/ticker_classification.UNIVERSE`, widen tolerance to 15% AND
  add CIP/ROU components to the rollforward where available.

**C2. PP&E rollforward for impairment years**
- When PPE_end_reported < PPE_end_implied by >5%, the gap is likely an
  impairment charge (D&A understates the actual reduction).
- Fix: flag direction in `components`: `"impairment_implied": disc_abs < 0`.

**C3. PP&E rollforward for M&A years**
- When PPE_end_reported > PPE_end_implied by >5%, gap is likely acquired
  PP&E (CapEx excludes acquired assets which appear via Investing CF).
- Fix: flag direction `"acquisition_implied": disc_abs > 0`. Cross-reference
  Goodwill delta from BS to confirm (large Goodwill change in same FY).

**C4. WC reconciliation on acquisition years**
- When Goodwill ΔFY > 10% of prior Goodwill, WC reconciliations will fail
  because acquired AR/Inv/AP appears on BS without CF working-capital line.
- Fix: skip WC reconciliation when Goodwill change is material — emit
  result with `notes="acquisition_year_skipped"` instead of pass/fail.

**C5. WC inventory for digital/services filers**
- META, V, MA, GOOGL, ASML-software-services have zero inventory. The
  check fires noise on a ~0/0 ratio.
- Fix: skip when `|inventory_beg| + |inventory_end| < $10M` (materiality
  floor, same as cash floor).

**C6. Debt rollforward 2019 transition year**
- ASC 842 (effective 2019 for most US filers) moved operating leases to
  the balance sheet but NOT into the conventional debt line. Net effect:
  in 2019 specifically, some filers reclassified portions across debt
  categories.
- Fix: emit `notes="asc842_transition"` for the 2019 FY result; widen
  tolerance to 8% for 2019 only.

### Category D — Tolerance / methodology (DONE)

**D1. Cash rollforward tolerance 0.1% → 0.5%**
- Status: ✓ shipped in last commit
  [tools/verification/identity_checks.py:49](tools/verification/identity_checks.py#L49)

### Category E — Architecture (promotion / layering cleanup)

**E1. Promote checkers to calc layer**
- Current: checker functions live in `tools/verification/identity_checks.py`
  and the Stage 3 adapter at `aletheia/calculations/identity_checks.py`
  cross-layer imports them.
- Target: invert the dependency — move the 6 checkers + tolerance config
  into `aletheia/calculations/identity_checks.py`; convert the
  `tools/verification/` file to a thin CLI wrapper that imports from the
  calc layer.

**E2. Add tolerance config to `valuation_defaults`**
- Currently tolerances are a module-level dict in
  `tools/verification/identity_checks.py:46`. Move to
  `config/valuation_defaults.py` so they're centrally configurable.

---

## Phased fix plan

### Phase 1 — Formula completeness (Category A) · 1-2 days
Highest-leverage fixes: closing A1+A2 will turn ~30 of META's 49 failures
and ~22 of AAPL's failures into passes.

- [ ] **A1**: extend `check_retained_earnings_rollforward` with treasury
  retirement + SBC + ΔAOCI terms. Compute BOTH basic and extended
  values; pass/fail uses extended; both surface in `components` for
  diagnostic clarity.
- [ ] **A2**: extend `check_fcf_pathway_reconciliation` Pathway B to
  add SBC + other non-cash items. Pull SBC from `clean.SBC`; other
  non-cash from `derived` or skip if unavailable.
- [ ] Add 4 unit tests with synthetic data: basic-formula pass-case
  (no buybacks, no SBC) + extended-formula pass-case (META-like).

**Definition of done**: META + AAPL show ≥80% pass rate on RE and FCF
identities; remaining failures have documented Category C exception flags.

### Phase 2 — Data alignment (Category B) · 1 day

- [ ] **B1**: switch cash rollforward to broad cash via
  `_xbrl_fact_for_period` lookup of
  `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents`.
  Narrow-cash fallback preserved for pre-2018 filings.
- [ ] **B2**: extend AP-side CF tag fallback list to mirror the BS-side
  fallback list. Apply same pattern to AR and Inventory.
- [ ] **B3**: extend TotalDebt definition to include finance-lease
  liabilities + current-portion-of-LTD via additional XBRL fallbacks.
- [ ] 3 unit tests pinning each B-fix on real ticker data (META 2024,
  AAPL 2024, ASML 2024).

**Definition of done**: Cash rollforward universal pass rate ≥95%;
WC AR/AP pass rate ≥80% on non-acquisition years; Debt pass rate ≥90%.

### Phase 3 — Exception flagging (Category C) · 1-2 days

- [ ] **C1**: add `lifecycle="hyperscaler"` to
  `config/ticker_classification.UNIVERSE` for META/AMZN/GOOGL/MSFT.
  Wire into PP&E rollforward to widen tolerance + add CIP components.
- [ ] **C2/C3**: add `acquisition_implied` / `impairment_implied`
  direction flags in PP&E components dict.
- [ ] **C4**: read Goodwill delta and skip WC reconciliation when
  ΔGoodwill / prior_Goodwill > 10%.
- [ ] **C5**: inventory near-zero skip (materiality floor $10M).
- [ ] **C6**: ASC 842 transition year flag (FY2019 widened tolerance,
  notes annotation).
- [ ] UI: identity audit panel renders these annotations as colored
  badges (📦 acquisition, 💸 impairment, 🏗 hyperscaler-CIP, etc.).

**Definition of done**: No failure in the audit lacks a category
classification (A/B/C or genuine D unresolved). Analyst sees
exception annotations directly in the Stage 3 panel.

### Phase 4 — Architecture promotion (Category E) · 0.5 day

- [ ] **E1**: move the 6 checker functions + helpers from
  `tools/verification/identity_checks.py` → `aletheia/calculations/identity_checks.py`.
  Update import in the adapter to point at the new home. Convert the
  tools-side file to:
  ```python
  from aletheia.calculations.identity_checks import *  # CLI entry only
  ```
- [ ] **E2**: move `TOLERANCE_THRESHOLDS` dict to
  `config/valuation_defaults.py` as `IDENTITY_TOLERANCES`.
- [ ] Remove the cross-layer dependency annotation from
  [aletheia/calculations/identity_checks.py](aletheia/calculations/identity_checks.py)
  module docstring.

**Definition of done**: `tests/architecture/test_pipeline_layering.py`
passes with the formal layering rules enforced (no upward imports from
`aletheia/` into `tools/`).

### Phase 5 — Universe validation · 0.5 day

- [ ] Run the audit against the full 40-ticker universe via
  `python -m tools.verification.identity_checks`.
- [ ] Diff against the prior audit JSON in
  `audits/identity_audit_2026-05-13.json` to confirm pass-rate uplift.
- [ ] Update `docs/identity_audit_findings_2026-05-14.md` with
  before/after pass-rate table.

**Definition of done**: universe-wide pass rate ≥85% (currently ≈55% on
META, ≈60% on AAPL). Remaining failures all carry Category C exception
annotations.

---

## Out-of-scope (intentionally deferred)

- Extending the checks to TTM rows (currently FY-only roll-forwards).
- Adding the additional identities suggested in some literature:
  - WACC ≈ market_cap × cost_equity_weight + debt × after_tax_cost_debt
  - DPO/DSO/DIO cycle consistency
  - Goodwill never increases without acquisition

These deserve their own roadmap once the seven core identities pass
universe-wide cleanly.

---

## Estimated total scope

| Phase | Duration | Files touched | LoC delta |
|-------|----------|---------------|-----------|
| 1     | 1-2 days | 2 files       | +120 / -10 |
| 2     | 1 day    | 1 file        | +80 / -20  |
| 3     | 1-2 days | 3 files       | +180 / -10 |
| 4     | 0.5 day  | 3 files       | move-only  |
| 5     | 0.5 day  | 1 doc         | +60 lines  |
| **Total** | **~4-6 days** | **~10 files** | **~400 LoC net** |

---

## Risk register

- **R1**: Extended RE formula needs treasury+SBC+AOCI XBRL tags
  reliably available. Verified META, AAPL. Other 38 universe tickers
  TBD — surface as `skipped: missing fields` rather than spurious fail.
- **R2**: Hyperscaler classification requires the
  `TickerClassification.lifecycle` field to be set. Currently only
  some universe entries have it. Adding to others is a one-line config
  edit per ticker.
- **R3**: B1 (broad cash) may *reduce* pass rate on filers that report
  in narrow cash (pre-2018, foreign filers). Fall back to narrow when
  broad isn't available; expect net pass-rate uplift.
