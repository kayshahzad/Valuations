# Phase 5 — Architecture lock + centralization summary

**Effective date:** 2026-05 (Phase 5 of formula-centralization refactor)
**Type:** Architecture-protection layer (no business-logic change)
**Companion memos:** [Phase 1](2026-05-roic-invested-capital.md), [Phase 2](2026-05-fcff-netdebt-centralization.md), [Phase 3](2026-05-margins-roe-centralization.md), [Phase 4](2026-05-cost-of-capital-multiples-centralization.md)

This memo serves two purposes: (1) document Phase 5's architecture-lock tests, and (2) summarize the completed centralization refactor.

## Phase 5 — what changed

Two AST-walking tests added; no business-logic change. Their job is to **prevent future drift** — without these tests, nothing structurally stops a future engineer from re-defining `roic()` in a tools file or importing from a submodule, recreating the fragmentation that caused the original ROIC bug.

### Test 1 — single-definition lock

**File:** [tests/architecture/test_single_formula_source.py](../../tests/architecture/test_single_formula_source.py)

**Rule 1.1:** No module-level `def <formula_name>` anywhere in the live codebase may shadow a name in `aletheia.calculations.formulas.__all__`. Class methods and `@property` accessors are excluded — they're descriptors, not formula implementations (e.g. `DCFResult.wacc` returning the pre-computed attribute is allowed).

**Rule 1.2:** Submodule imports of the formulas package are forbidden outside the package itself. Callers must import from the package root:

```python
# OK:
from aletheia.calculations.formulas import roic, nopat
# BLOCKED:
from aletheia.calculations.formulas.ratios import roic
import aletheia.calculations.formulas.ratios
```

Forcing the package-root import keeps `__all__` canonical — engineers see the public surface every time they import.

### Test 2 — registry ↔ docstring sync

**File:** [tests/calculations/test_registry_docstring_sync.py](../../tests/calculations/test_registry_docstring_sync.py)

**Rule:** For every function name that appears in BOTH the central `__all__` AND `derivation_registry.py`, the registry's `formula` field must match the function's docstring first line (whitespace-normalized). Mismatches usually mean one side was updated without the other.

The test deliberately does NOT require every centralized function to have a registry entry. Construction helpers (`gross_debt`, `liquid_assets`, `cash_conversion_ratio`) don't need analyst-facing methodology docs. Coverage is intentional, not mandatory.

### Registry entries updated in Phase 5

Three registry entries had drifted from the canonical implementation:

| Entry | Old formula | New formula | Why |
|---|---|---|---|
| `net_debt` | `TotalDebt − CashAndEquivalents − ShortTermInvestments` | `GrossDebt − LiquidAssets` | Phase 2 broadened both sides (added LT debt + leases on debt side; added LT investments on assets side) |
| `fcf` | `OperatingCF − abs(CapEx)` | `OperatingCF − |CapEx|` | Notation alignment with docstring; same math |
| `justified_ev_ebitda` | `(1 − g/ROIC) / (WACC − g) × (1 − tax_rate) × EBIT/EBITDA` | `[NOPAT × (1 − g/ROIC) / EBITDA] / (WACC − g)` | Notation alignment; same math (NOPAT = EBIT × (1 − tax_rate)) |

## Refactor summary — what the 5 phases delivered

### The original bug

**GOOGL ROIC FMP=21.44% vs XBRL=12.00%** — same ticker, same formula, two different implementations of Invested Capital living in two different code paths. The drift was structural, not data-driven.

### The five phases

| Phase | Scope | Functions centralized | Diff (universe rows) | LOC moved |
|---|---|---|---|---|
| 1 | IC + NOPAT + ROIC; convention canonicalization | 3 | 13 unexpected (historical floor activations — wins, not regressions) | ~80 |
| 2 | FCFF + NetDebt formula replacements | 5 | 0 unexpected (1,657 expected shifts) | ~120 |
| 3 | EBITDA synthesis + 4 margins + ROE | 6 | 0 unexpected (27 ROE corrections on negative-equity filers) | ~250 |
| 4 | WACC + CAPM + Kd + 13 valuation multiples | 16 | 0 unexpected (Phase 1-3 metrics clean) | ~200 |
| 5 | Architecture lock + registry sync | — | n/a | ~250 (test code) |

**Cumulative result: 30 functions in the central module, 0 fragmentation surface remaining.**

### The 8 modules

```
aletheia/calculations/formulas/
├── __init__.py            (public __all__ surface)
├── balance_sheet.py       (gross_debt, liquid_assets, net_debt)
├── cash_flow.py           (fcf, fcff)
├── cost_of_capital.py     (cost_of_equity, cost_of_debt, wacc)
├── derived_inputs.py      (invested_capital, nopat)
├── income_statement.py    (ebitda)
├── margins.py             (gross/ebit/ebitda/fcf margin %)
├── ratios.py              (roe, roic)
└── valuation_multiples.py (13 multiples + justified EV/EBITDA + cash conversion)
```

### Convention changes that shipped

Most centralizations were behavior-preserving. Two real convention canonicalizations happened:

1. **Phase 1 — Invested Capital**: switched the FMP path to match cleaning_engine's ExcessCash netting + 5%-of-revenue floor. ROIC drops for cash-rich filers; the new number reflects operating performance excluding idle cash. UI flag `📐 2026-05` on affected metric cells; retires 2026-12-31.
2. **Phase 2 — NetDebt**: broadened FMP path to the EV-aligned definition (added current LT debt + finance leases on debt side; added LT investments on assets side). Cash-rich filers' NetDebt becomes substantially more negative (more accurate cash position).

### Test coverage added

| Test file | Tests | Purpose |
|---|---|---|
| `test_formulas_phase1.py` | 17 | NOPAT, IC, ROIC behavior + cross-provider parity |
| `test_formulas_phase2.py` | 25 | FCF, FCFF, NetDebt + helpers + parity |
| `test_formulas_phase3.py` | 24 | Margins, ROE, EBITDA synthesis + parity |
| `test_formulas_phase4.py` | 32 | WACC, Ke, Kd + 13 valuation multiples |
| `test_single_formula_source.py` | 2 | Architecture lock (Rule 1 + Rule 2) |
| `test_registry_docstring_sync.py` | 1 | Documentation drift guard |
| **Total** | **101** | |

### Why fragmentation can't return

Future re-fragmentation would require either:

1. Defining a top-level `def roic` (or any other central name) outside the package → blocked by `test_no_formula_redefinition_outside_central_package`.
2. Importing from a submodule path → blocked by `test_no_submodule_imports_outside_central_package`.
3. Updating the registry's `formula` field without updating the function (or vice versa) → blocked by `test_registry_formula_matches_docstring_first_line`.

CI runs these tests on every PR. The bug class is closed.

## Audit trail

For external audit / due diligence:

- **Universe diffs (per phase)** — `audits/centralization_snapshots/diff_phaseN_*.csv` + `.md` for each phase, showing every row classified clean / expected / unexpected.
- **Pre-migration baselines** — `phase{1,2,3,4}_before_*.parquet` snapshots of all derived metrics across the 41-ticker universe.
- **Test coverage** — 101 tests covering formula behavior, cross-provider parity, and architecture invariants.
- **Methodology memos** — five `2026-05-*.md` files in `docs/methodology_changes/` documenting each phase's scope, rationale, expected impact, and downstream effects.

## Open work after Phase 5

The centralization is complete; the formulas package is the canonical source of truth for derived financial quantities. Items deferred from the original plan:

- **FMP-path consolidation into cleaning_engine** (originally Q3 in the plan): keeping two adapter paths with shared formulas is the current state. Going to a single path is a separate larger refactor (~2-4 weeks); not blocking and not in this scope.
- **DCF projection logic** (e.g. terminal value, scenario margin decay): intentionally excluded from centralization scope. That's modeling logic, not derived-metric computation; lives in DCFEngine where the projection sequencing matters.
- **Registry coverage** of all 30 centralized formulas: 7 have registry entries today, 23 are construction primitives that don't need analyst-facing methodology docs. Adding more entries is encouraged but the architecture lock doesn't require it.

## Linked artifacts

- Centralized package: [aletheia/calculations/formulas/](../../aletheia/calculations/formulas/)
- Architecture lock test: [tests/architecture/test_single_formula_source.py](../../tests/architecture/test_single_formula_source.py)
- Registry-docstring sync test: [tests/calculations/test_registry_docstring_sync.py](../../tests/calculations/test_registry_docstring_sync.py)
- Universe-diff tooling: [scripts/snapshot_universe_metrics.py](../../scripts/snapshot_universe_metrics.py)
- All phase memos: [docs/methodology_changes/](.)
