# Currency Allowlist Centralization — Plan

**Status:** proposed (plan only, no implementation)
**Trigger:** the NVO/DKK bug — DKK was missing from `tag_resolver.ACCEPTED_UNITS`, so Novo Nordisk's IFRS/DKK facts were silently skipped and it cleaned to empty records. The fix touched one of *five* independent, drifting allowlists.

---

## 1. Problem

"Which currencies/units the pipeline accepts" is hard-coded as literal tuples in **5 extraction sites** and duplicated again in **2 FX-rate modules**. There is no single source of truth and no invariant that an *accepted* currency is *convertible*. The lists have silently drifted apart — which is exactly how DKK ended up half-supported (readable nowhere, convertible only at read-time).

## 2. Current fragmentation (audit)

### Extraction-side allowlists (which XBRL unit types are read)

| # | Site | Contents | Drift vs canonical |
|---|------|----------|--------------------|
| 1 | `tag_resolver.py:79` `ACCEPTED_UNITS` | USD, shares, pure, EUR, TWD, CAD, GBP, JPY, CHF, **DKK** | canonical (DKK just added) |
| 2 | `tag_resolver.py:81` `PER_SHARE_UNITS` | USD/…, TWD/…, EUR/…, GBP/…, JPY/…, CAD/…, CHF/…, **DKK/shares** | canonical |
| 3 | `database.py:1639` | USD, shares, pure, EUR, TWD, CAD, GBP, JPY, CHF | **missing DKK** |
| 4 | `cleaning_engine.py:587` (year-discovery) | USD, shares, pure, EUR, TWD, CAD, GBP, JPY, CHF | **missing DKK** |
| 5 | `edgar_client.py:561` (`_report_tag_coverage`, diagnostic) | USD, EUR, TWD, CAD, GBP, JPY, CHF | **missing DKK, shares, pure** |

### FX-rate modules (how a currency converts to USD)

| Module | Role | Currencies | Drift |
|--------|------|-----------|-------|
| `fx_converter.py` `ANNUAL_AVG_FX_TO_USD` | storage-time (`tag_resolver` calls `convert_to_usd`) | EUR, TWD, GBP, JPY, CAD, CHF | **no DKK** |
| `fx.py` `convert_financials_to_usd` | read-time (`calc_input_builder`) | includes DKK (NVO reads correctly) | — |

**Key architectural fact:** financial values are **stored in native currency** (ASML → 30.6B EUR, NVO → 290.4B DKK). Conversion to USD happens at **read** via `fx.py`. `fx_converter.py`'s storage-time conversion is effectively dormant for the raw facts — a currency with no `fx_converter` rate simply stores native (which is what we want). This makes the `fx_converter` table a latent trap: it *looks* authoritative but isn't the operative one.

## 3. Goal

One registry — `config/currencies.py` — as the single source of truth for the accepted unit set. Every allowlist imports from it. A completeness invariant guarantees **accepted ⟹ convertible**, so a currency can never again be half-supported.

## 4. Design — `config/currencies.py`

```python
# Non-currency XBRL unit types always accepted (dimensionless).
NON_CURRENCY_UNITS = frozenset({"shares", "pure"})

# ISO codes the pipeline extracts + can express in USD.
# Adding a code here is the ONLY edit needed to support a new filer currency.
SUPPORTED_CURRENCIES = frozenset({
    "USD", "EUR", "GBP", "JPY", "CAD", "CHF", "TWD", "DKK",
})

# Derived — consumers import these, never a literal tuple.
ACCEPTED_UNITS  = NON_CURRENCY_UNITS | SUPPORTED_CURRENCIES
PER_SHARE_UNITS = frozenset(f"{c}/shares" for c in SUPPORTED_CURRENCIES)

def is_accepted_unit(unit: str) -> bool: ...
def is_per_share_unit(unit: str) -> bool: ...
def numerator_currency(per_share_unit: str) -> str:   # "TWD/shares" -> "TWD"
    ...
```

Membership is set-based (all 5 consumers do `unit not in <list>` or iterate-and-collect — none depend on order; see D2). Lives in `config/` alongside the other registries (`tag_mappings.py`, `sign_conventions.py`).

## 5. Phases

**Phase 0 — Lock the contract (no code).** Confirm the canonical currency set + native units; get D1–D5 signed off.

**Phase 1 — Build the registry.** Create `config/currencies.py` + `tests/…/test_currencies.py`. Add the **FX-completeness invariant** test: every non-USD code in `SUPPORTED_CURRENCIES` has a rate entry in the read-path FX table (`fx.py`). *This is the test that would have caught DKK.*

**Phase 2 — Migrate extraction sites (behavior-preserving, one at a time).** Replace each literal tuple (sites 1–5) with an import. Sites 3, 4, 5 gain DKK; site 5 additionally gains shares/pure — verify site 5 is diagnostic-only (`_report_tag_coverage`, prints coverage, does not affect persisted data → safe). After each site: goldens + foreign-filer sanity.

**Phase 3 — Reconcile the FX tables.** Decide the canonical rate source (D3). Add DKK to `fx_converter.py` **or** collapse the two modules so both derive from one table keyed by `SUPPORTED_CURRENCIES`. Wire the Phase-1 invariant to whichever table is canonical.

**Phase 4 — Regression + validation matrix.** Goldens 11/11; foreign-filer read-back matrix (ASML/EUR, TSM/TWD, NVO/DKK + any GBP/JPY/CAD/CHF filer); `grep` proves zero remaining literal currency tuples outside `config/currencies.py`.

## 6. Decisions needing sign-off

- **D1 — Canonical currency set.** Ship the 8 above (USD, EUR, GBP, JPY, CAD, CHF, TWD, DKK)? Any imminent filers (KRW/Samsung, HKD, AUD, INR) to add now vs later?
- **D2 — Order dependence.** Confirmed none: sites 3/4 are `in`-membership; site 5 collects *all* matches then takes `max(end_date)`. Safe to use a `frozenset`. (Sign off that no future consumer will assume priority order.)
- **D3 — Storage-native vs storage-converted.** Recommend **storage stays native, read converts** (current de-facto behavior — matches how ASML/NVO already work). This makes `fx_converter.py` (storage-time) redundant; either delete it or repoint its callers. Confirm nothing else relies on storage-time conversion.
- **D4 — Location.** `config/currencies.py`. (Alt: `aletheia/data/currencies.py` — but `config/` matches the existing registry convention.)
- **D5 — Invariant enforcement.** Test-time only (recommended — no import-time cost/crash risk) vs an import-time `assert`.

## 7. Risks & mitigations

- **Site 5 behavior change** (gains shares/pure/DKK) — it is a diagnostic printout only; verify `all_found` never gates persistence. *Mitigation:* read `ingest()` — `all_found` only prints a warning.
- **FX table reconciliation shifts values** where `fx_converter` and `fx.py` disagree on a shared currency (EUR/TWD/…). *Mitigation:* diff the two tables before collapsing; freeze on the read-path (`fx.py`) values since those are what production already uses.
- **A currency in the registry with no FX rate** silently stores native and reads native (un-converted) — the exact DKK failure mode. *Mitigation:* the Phase-1 completeness invariant makes this a failing test, not a silent bug.

## 8. Definition of done

1. `config/currencies.py` is the only place currency/unit membership is defined.
2. All 5 extraction sites import from it; `grep` finds no other `"USD", … "CHF"`-style literal.
3. FX-completeness invariant test passes and is wired to CI-runnable tests.
4. Goldens 11/11; foreign-filer read-back matrix green.
5. The two FX modules are reconciled to a single canonical rate source (or one is retired).

## 9. Estimated scope

Small–medium. New file + tests (~1–2h). Five 1-line-ish migrations + per-site verification (~1h). FX reconciliation is the only judgement-heavy part (D3) — bounded once decided. No data re-ingest required (registry is read at extraction time; existing DB rows unaffected). NVO already re-ingested separately.
