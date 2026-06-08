# Methodology change — Cost of capital + valuation multiples centralization

**Effective date:** 2026-05 (Phase 4 of formula-centralization refactor)
**Affected formulas:** WACC, CAPM (Ke), Kd, justified EV/EBITDA, cash-conversion ratio, P/E, P/B, EV/EBITDA, EV/EBIT, EV/FCF, P/S, ND/EBITDA, D/E, Interest Coverage, Current Ratio, Dividend Yield
**Companion memos:** [Phase 1](2026-05-roic-invested-capital.md), [Phase 2](2026-05-fcff-netdebt-centralization.md), [Phase 3](2026-05-margins-roe-centralization.md)

## What changed

Phase 4 consolidated the **cost-of-capital math** and the **valuation-multiple ratios** into the central formula module. These formulas live at the *consumer* layer of the codebase — DCFEngine, multiple_decomposition, screening_ratios — and were duplicated/divergent across call sites pre-migration.

| Formula | Pre-Phase 4 site(s) | Post-Phase 4 |
|---|---|---|
| WACC, CAPM Ke, Kd | `dcf_engine.compute_wacc` only | central `cost_of_capital.py` |
| Justified EV/EBITDA + cash-conversion | `multiple_decomposition._compute_justified_ev_ebitda` | central `valuation_multiples.py` |
| P/E, P/B, EV/EBITDA, EV/EBIT, EV/FCF | inline in `screening_ratios.py:485-499` | central `valuation_multiples.py` |
| ND/EBITDA, D/E, Interest Coverage, Current Ratio, Dividend Yield | inline in `screening_ratios.py` | central `valuation_multiples.py` |

The bounds and fallbacks that previously lived in DCFEngine (`DEFAULT_WACC = 0.09`, Kd cap at 15%, WACC floor at max(4%, Rf+1%), WACC ceiling at 18%, NorthWestern ROIC floor at 8%) now live in the formula module as documented constants. Any future caller that needs WACC will get the same guarded result without re-implementing the policy.

## Why this convention

For the multiples: the formulas were already IDENTICAL across the two call sites. Centralizing eliminates the *risk* of future drift even though none exists today. The exercise also lets analyst-facing policies (e.g. "P/B is suppressed when book equity is negative, same as ROE") live in one place instead of being re-encoded in each consumer.

For WACC: the math was a single site, so there was no cross-site drift. The benefit is making the bounds policy *explicit and testable* — `WACC_CEILING = 0.18` and `KD_CAP = 0.15` are now named constants with tests pinning their behavior, rather than magic numbers buried mid-function.

## Diff snapshot

Universe diff: **12,287 rows clean / 0 expected / 0 unexpected**. Phase 4 didn't touch any DB-stored derived metric (WACC and valuation multiples are computed live from market data, not persisted). The clean diff confirms that Phase 1-3 metrics didn't accidentally regress when Phase 4 reshuffled the cost-of-capital orchestration.

Diff report: [diff_phase4_before_vs_phase4_after_2026-05-17.md](../../audits/centralization_snapshots/diff_phase4_before_vs_phase4_after_2026-05-17.md)

## Downstream effects audited

- ✅ **DCFEngine WACC computation**: orchestration unchanged, math delegated; sample-ticker spot check confirmed numerical parity with pre-Phase-4 behavior (within rounding).
- ✅ **Multiple decomposition**: legacy `_compute_justified_ev_ebitda` wrapper preserved (returns `(0.0, 0.0)` tuple on degenerate inputs); central function returns `None`, wrapper coerces. Tests pin both contracts.
- ✅ **Screening tab**: P/E, P/B, EV/EBITDA, etc. now flow through central functions. Suppression policy explicit (P/B returns None on negative equity, same as ROE — matches the Phase 3 correctness fix for HD/LOW/MCO).
- ✅ **Reverse DCF**: reads WACC the same way; no change in behavior.
- ✅ **All Phase 1-3 metrics**: zero shift confirmed by universe diff.

## Cumulative state after Phase 4

The central formula module now houses **27 functions**:

| Module | Functions |
|---|---|
| `balance_sheet.py` | gross_debt, liquid_assets, net_debt |
| `cash_flow.py` | fcf, fcff |
| `cost_of_capital.py` | cost_of_equity, cost_of_debt, wacc |
| `derived_inputs.py` | invested_capital, nopat |
| `income_statement.py` | ebitda |
| `margins.py` | gross_margin_pct, ebit_margin_pct, ebitda_margin_pct, fcf_margin_pct |
| `ratios.py` | roe, roic |
| `valuation_multiples.py` | price_to_earnings, price_to_book, ev_to_ebitda, ev_to_ebit, ev_to_fcf, price_to_sales, net_debt_to_ebitda, debt_to_equity, interest_coverage, current_ratio, dividend_yield, justified_ev_ebitda, cash_conversion_ratio |

Both adapter paths (FMP + cleaning_engine) AND the calc consumers (DCFEngine, multiple_decomposition, screening_ratios) now share these 27 functions. The formula fragmentation that allowed the original ROIC drift (FMP=21.44% vs XBRL=12.00%) is structurally closed.

## What's left

**Phase 5** — Architecture lock. Two tests:

1. AST-walk verifying no function name from the central `__all__` list is redefined outside `aletheia/calculations/formulas/`. Modeled on the existing `tests/architecture/test_no_resurrected_agents.py` pattern.
2. Registry-docstring sync test ensuring each function's docstring first line matches the corresponding `derivation_registry.py` entry's `formula` field.

After Phase 5, future drift becomes a build failure rather than a silent regression.

## Linked artifacts

- Phase 4 formulas: [cost_of_capital.py](../../aletheia/calculations/formulas/cost_of_capital.py), [valuation_multiples.py](../../aletheia/calculations/formulas/valuation_multiples.py)
- Phase 4 tests: [tests/calculations/test_formulas_phase4.py](../../tests/calculations/test_formulas_phase4.py)
- Migrated call sites: [dcf_engine.py:470](../../aletheia/tools/dcf_engine.py#L470), [multiple_decomposition.py:49](../../aletheia/tools/multiple_decomposition.py#L49), [screening_ratios.py:479](../../aletheia/tools/screening_ratios.py#L479)
