# Fallback confirmation gate — Phase-1 sign-off (task 0.2.2)

Every HOT substitution site traced and bucketed into the three-state taxonomy.
This is the completion criterion for the confirmation gate: each site lands in
exactly one bucket with a fix action and the golden ticker that will surface an
oracle diff. **36 unique sites** (the AST double-counts multi-operand `or`
chains, so the earlier "40" collapses to 36).

```
FABRICATION  — substitutes a value that CONTRADICTS the missing signal
               (tax 0%→21%, equity $0→$1, cash-taxes missing→0% rate).
               Fix: get_strict() + propagate None.
GENUINE_MISS — substitutes the CORRECT default when the field is truly absent
               (deferred-rev=0, prior-year=0, warning-only denominators).
               Fix: leave as-is; add an invariant comment.
LATENT       — defensible for a complete current filing, but MASKS a problem for
               levered / asset-heavy / multi-year rows (missing debt, interest,
               D&A, capex tags). Fix: per-site None-guard + document the condition.
```

**Counts:** FABRICATION 4 · LATENT 13 · GENUINE_MISS 19.

---

## FABRICATION (4) — the entire Phase-1 value-changing surface

| # | site (current line) | fabrication | feeds | fix | oracle-diff ticker |
|---|---|---|---|---|---|
| F1 | `cleaning_engine.py:927` `_domain3` | `CashTaxRate or 0.21` | NOPAT → ROIC/IV | `get_strict()` + None | 0%/NOL low-tax filer |
| F2 | `cleaning_engine.py:1522` `_domain10` | cash-taxes tags missing → `0.0` → **0% CashTaxRate** | persisted `CashTaxRate` → NOPAT overwrite | leave None when all 3 tags absent | full-rate payer missing `IncomeTaxesPaid` |
| F3 | `cleaning_engine.py:1762` `_compute_derived` | `TotalEquity or 1.0` | ROE + InvestedCapital → ROIC | `get_strict()` + None (mirror neg-equity suppression) | thin/zero-book buyback filer (HD/LOW-type) |
| F4 | `quantitative_screens.py:518` EPV | `CashTaxRate or 0.21` | EPV NOPAT → EPV/price signal | strict + explicit flag | 0/low-cash-tax filer |

**Critical interaction (for 1b):** F2 fabricates a **0%** cash rate (missing tags → 0.0 → `_safe_div` → 0%), which the vestigial `_fb` at `:1759` then flips to **0.21**. Two tax defects collide — F1/F2 must be fixed together, and the `:1759` `_fb` site is currently **vestigial** (assigned, not consumed; NOPAT is read from `clean["NOPAT"]`). So the *consumed* fabrication is F1 (D3) + F2 (D10), not the instrumented `_compute_derived` tax site.

**Instrumentation gap surfaced:** the Phase-0 probe instrumented the `_compute_derived` block (`:1759` tax, `:1762` equity) — but the *consumed* tax fabrications live at F1 (`:927`) and F2 (`:1522`), which weren't instrumented. → Phase-1 should extend `_fb` to F1/F2 (or rely on the golden re-clean diff) to measure their true firing.

---

## LATENT (13) — per-site None-guard + document; batched after the FABRICATION fixes

| site | mask | maps to |
|---|---|---|
| `balance_sheet.py:83` net_debt | missing debt tag → fake net-cash | 1d-adjacent (net-debt) |
| `cash_flow.py:39` fcf | missing capex → FCF=OCF | **1d** |
| `income_statement.py:28` ebitda | missing D&A → EBITDA=EBIT | **1e** |
| `cleaning_engine.py:895` D3 bridge | missing interest → EBIT understated | 1e-adjacent |
| `cleaning_engine.py:1337` AR | missing AR → masks multi-year spread | defer |
| `cleaning_engine.py:1430` D9 struct-NWC | missing rev → sham 3%-of-rev NWC into DCF | 1c-adjacent |
| `cleaning_engine.py:1511` D10 GAAP rate | missing tax tag → 0% GAAP rate | 1b-adjacent |
| `cleaning_engine.py:1744` `_compute_derived` ebit | missing interest → EBIT understated | 1e-adjacent |
| `cleaning_engine.py:1759` `_compute_derived` tax | vestigial 0→0.21 (not consumed) | 1b (delete/guard) |
| `cleaning_engine.py:1932` lease excess | missing → overstates debt (COST-type) | defer |
| `sec_quarterly.py:512` net_debt | missing LTD → fake net-cash (quarterly) | 1d-adjacent |
| `ttm_derivation.py:235` net_debt | missing debt component → net-cash bias | 1d-adjacent |
| `utility_taxonomy.py:98` CIP capex | missing completed-additions → capex understated | defer (utility) |

## GENUINE_MISS (19) — leave + invariant comment
`cost_of_capital.py:115` (Rf floor default) · `derived_inputs.py:76,77` (IC, guarded) · `cleaning_engine.py` 910/911/993/1222/1336/1339/1345/1346/1347/1403 (warning-only denominators & first-year priors) · `1338` deferred-rev (canonical) · `1516` D10 pretax (guarded by `!=0`) · `edgar_client.py:517` (display print) · `sec_quarterly.py:527` + `ttm_derivation.py:274,130` (already `is not None`-guarded / placeholder filter).

---

## What this does to the Phase-1 sub-PR scope

- **1a** `get_strict()` primitive — enables F1/F3. (Empty oracle diff expected; rehearses the gate.)
- **1b** tax unification — fixes **F1 + F2** together (+ delete the vestigial `:1759`). First PR with real movers.
- **1c** equity denominator — fixes **F3**; also None-guard the D9 struct-NWC (`:1430`).
- **1d** CapEx sign + net-debt LATENT masks (`cash_flow.py:39`, `balance_sheet.py:83`, `sec_quarterly.py:512`, `ttm_derivation.py:235`).
- **1e** EBITDA/EBIT D&A + interest masks (`income_statement.py:28`, `:895`, `:1744`).
- **F4** (EPV screen tax) — fold into 1b (same fix, screen output).
- GENUINE_MISS (19) — one cleanup PR adding invariant comments (no value change).

**Bottom line:** the value-changing surface is 4 fabrication sites, cleanly covered by 1b (tax ×3) + 1c (equity ×1). The 13 LATENT sites are real but lower-frequency masks that slot into 1d/1e or a deferred batch.
