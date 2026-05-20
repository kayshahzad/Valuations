# Methodology change — Margins, ROE, EBITDA synthesis centralization

**Effective date:** 2026-05 (Phase 3 of formula-centralization refactor)
**Affected metrics:** `derived_GrossMargin_Pct`, `derived_EBIT_Margin_Pct`, `derived_EBITDA_Margin_Pct`, `derived_FCF_Margin_Pct`, `derived_ROE`, `derived_EBITDA`
**Companion memos:** [Phase 1](2026-05-roic-invested-capital.md), [Phase 2](2026-05-fcff-netdebt-centralization.md)

## What changed

Phase 3 was a **mechanical consolidation** — eight formulas that were already IDENTICAL on both adapter paths got routed through new central functions. No numerical convention changed; the goal was code-organization cleanup so future tweaks happen in one place.

| Formula | Central function | Pre-Phase 3 sites | Post-Phase 3 |
|---|---|---|---|
| EBITDA (synthesis) | `formulas.ebitda` | inline in both adapters | central |
| Gross Margin % | `formulas.gross_margin_pct` | inline in both adapters | central |
| EBIT Margin % | `formulas.ebit_margin_pct` | inline in both adapters | central |
| EBITDA Margin % | `formulas.ebitda_margin_pct` | inline in both adapters | central |
| FCF Margin % | `formulas.fcf_margin_pct` | inline in both adapters | central |
| ROE | `formulas.roe` | inline in both adapters | central |

## One real correctness fix — ROE suppression on negative equity

The migration surfaced an existing inconsistency: the cleaning_engine ([line 1821](../../aletheia/data/cleaning_engine.py#L1821)) suppressed ROE when `total_equity ≤ 0` with a documented rationale —

> "Aggressive-buyback companies (LOW, HD, AZO, DRI etc.) drive book equity below zero via treasury-stock subtraction; NI/equity then produces a misleading negative percentage that suggests an operational problem the company doesn't have."

But the FMP adapter checked only `abs(total_equity) > 1e3`, which **allowed** computation on negative equity — yielding the exact misleading values the cleaning_engine code comment warned about. The centralized `roe()` function applies the cleaning_engine's suppression policy uniformly, so 27 ROE rows in the universe (latest snapshot) that previously had misleading negative values now correctly report `None`.

Affected tickers (latest snapshot, 27 rows):
- **LOW** (FY2021-2025, TTM) — aggressive buybacks; negative book equity since 2021
- **HD** (FY2018, 2019, 2021) — same pattern
- **MCO** (FY2000-2003, 2007-2010) — early-history accumulated deficits
- **AMZN** (FY2000-2004) — pre-profitability years with negative equity
- **AMD** (FY2015) — historical loss-year drawdown
- **TSLA** (FY2007-2009) — pre-revenue years with negative equity
- **CNC** (FY2000), **ORCL** (FY2022) — one-off years

For these rows the dashboard will now show `—` for ROIC instead of a misleading negative percentage. The cleaning_engine sets `derived_ROE_suppressed_reason = "negative_or_zero_book_equity"` when this happens; analysts can inspect this field in the FMP Compare view for an explanation.

## Phase 3 diff snapshot

| Bucket | Rows | % |
|---|---|---|
| Clean (no change) | 12,260 | 99.78% |
| Expected (ROE suppression corrections) | 27 | 0.22% |
| Unexpected | **0** | 0% |

The 99.78% clean rate is the headline: Phase 3 was deliberately mechanical and the snapshot proves it didn't move any other metric — margins, EBITDA, IC, NOPAT, ROIC, FCFF, NetDebt all stayed identical.

Diff report: [diff_phase3_before_vs_phase3_after_2026-05-17.md](../../audits/centralization_snapshots/diff_phase3_before_vs_phase3_after_2026-05-17.md)

## Cumulative state after Phase 3

The central formula module now houses **14 functions**, owning the canonical implementation of:

- **Derived inputs**: `nopat`, `invested_capital`
- **Income statement**: `ebitda` (synthesis)
- **Cash flow**: `fcf`, `fcff`
- **Balance sheet**: `gross_debt`, `liquid_assets`, `net_debt`
- **Margins**: `gross_margin_pct`, `ebit_margin_pct`, `ebitda_margin_pct`, `fcf_margin_pct`
- **Ratios**: `roe`, `roic`

Both adapter paths (`fmp_stage3_adapter._compute_derived` and `cleaning_engine`) now call the same functions for all 14. Cross-provider drift on these metrics is impossible by construction.

## What's left

- **Phase 4** — Cost of capital. Migrate WACC, CAPM (Ke), and Kd from [dcf_engine.py:470-680](../../aletheia/tools/dcf_engine.py#L470-L680) into `formulas/cost_of_capital.py`. Plus valuation multiples (`P/E`, `P/B`, `EV/EBITDA`, etc.) currently duplicated across [screening_ratios.py](../../aletheia/tools/screening_ratios.py) and [multiple_decomposition.py](../../aletheia/tools/multiple_decomposition.py).
- **Phase 5** — Architecture lock. AST-walk test ensuring no formula name from the central `__all__` is redefined outside the formulas package; registry-docstring sync test.

## Linked artifacts

- Phase 3 formulas: [margins.py](../../aletheia/calculations/formulas/margins.py), [income_statement.py](../../aletheia/calculations/formulas/income_statement.py), `roe()` in [ratios.py](../../aletheia/calculations/formulas/ratios.py)
- Phase 3 tests: [tests/calculations/test_formulas_phase3.py](../../tests/calculations/test_formulas_phase3.py)
