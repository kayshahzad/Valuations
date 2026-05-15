# Layer 2 — Derivation Registry: Predictions

**Date**: 2026-05-14
**Goal**: Build a registry that documents every derived Stage 3 value's
inputs, formula, methodology choice, and alternates. Surface in the UI
so the analyst can read a drift like "FCF +108% vs FMP" and immediately
see whether it's a methodology divergence or a bug.

---

## Observed FMP drifts (from `_STAGE3_CALC_SPEC` panel on META)

These are the drifts the registry MUST explain post-implementation.
If any remains unexplained after registry V0, the diagnostic is
incomplete.

| Calc | Our value | FMP value | Drift | Hypothesised methodology divergence |
|---|---|---|---|---|
| DCF FCF | $46.11B | $22.13B | +108% | Cleaning's `FCF` (Liberti method, post-SBC add-back) vs FMP's `freeCashFlowToFirmTTM` (different formula) |
| DCF ROIC | 31.10% | 19.96% | +56% | Our IC = TotalEquity + LongTermDebt; FMP IC includes more |
| MD ROIC | 31.10% | 19.96% | +56% | Same as above |
| Market P/E | 25.97 | 22.14 | +17% | TTM EPS source — we use diluted * latest price ÷ derived NI |
| Screen P/E | 26.33 | 22.14 | +19% | Slightly different denominator definition |
| Screen P/B | 7.33 | 6.41 | +14% | Book-value definition (incl./excl. minority interest, intangibles) |
| Market P/Sales | 7.81 | 7.28 | +7% | Trailing-period definition |
| EV/EBITDA | 15.12 | 14.36 | +5% | EV cash-netting (gross debt vs net debt) |

**Validation criterion**: every row above must have a registry entry
with `methodology_note` field explaining the divergence direction.

---

## V0 scope

**In scope**:
- Catalog ~30 derived values used across Stage 3 (DCF + RDCF + MD + Screen + Cleaning's main canonical fields)
- Each entry has:
  - `name` (canonical)
  - `inputs` (list of upstream values)
  - `formula` (human-readable expression)
  - `methodology` (citation or in-house policy reference)
  - `alternates` (other valid methodologies with brief contrast)
  - `fmp_equivalent` (FMP field name + brief note on divergence)
- UI: add "📖 Methodology" expander below the Stage 3 calcs panel
  showing per-row derivation details for the visible filter

**Out of scope (V1+)**:
- Runtime derivation traces (intercept actual engine computations, emit
  trace tree per value)
- Auto-comparison engine that flags methodology mismatches between
  engines (DCF FCF vs Screening FCF using different formulas)
- Methodology divergence alerts ("this filer's FCF derived via path X,
  but DCF defaults to path Y for sectors like this")

---

## Predicted UI behavior post-implementation

For the 8 drift rows above, the Stage 3 panel should:
1. Continue showing the drift % (unchanged)
2. Below the table, expander "📖 How each value was derived" lists each
   calc with its methodology
3. Analyst clicks expander → sees that FCF $46B comes from
   Liberti-method post-SBC, FMP's $22B is freeCashFlowToFirmTTM
   computed differently → drift is a "Category D — methodology
   divergence" not a bug

**Specific entries the registry MUST contain** (otherwise diagnostic
gap remains):
- FCF (with at least 3 alternates: Damodaran, Liberti, FMP-FCFF)
- ROIC (with at least 2 alternates: McKinsey IC vs Damodaran IC)
- P/E (TTM-EPS source choice)
- P/B (book value definition)
- EV (cash-netting choice)
- EBITDA (NormalizedEBIT + DA vs raw OperatingIncome + DA)
- NOPAT (which tax-rate fallback per A11)

---

## Predicted code footprint

- `aletheia/calculations/derivation_registry.py`: new file, ~250 LoC
  for ~30 entries + the `DerivationEntry` dataclass + lookup helpers
- `aletheia/ui/pipeline_explorer_view.py`: +50 LoC for the methodology
  expander; modify the calc-spec catalog rows to carry an optional
  `registry_key` field
- Tests: ~80 LoC across 5-6 unit tests verifying registry lookups +
  UI rendering doesn't crash on registry-missing entries

Total: ~380 LoC net.

---

## Validation criteria

V0 is validated if:
1. **Every drift > 5% in the META Stage 3 vs FMP panel has a registry
   entry with non-empty methodology_note**
2. **Registry has ≥ 25 entries** covering DCF/RDCF/MD/Screen outputs
3. **Methodology expander renders** without crashing on the live META
   bundle
4. **No regression**: 67/67 tests pass

V0 is **invalidated** if:
- Any of the 8 drift rows above lacks a methodology explanation
- Registry has fewer than 25 entries
- Methodology expander breaks the Stage 3 panel for any ticker