# Methodology change — ROIC & Invested Capital canonicalization

**Effective date:** 2026-05 (Phase 1 of formula-centralization refactor)
**Affected metrics:** `derived_InvestedCapital`, `derived_ROIC`
**Registry entries:** `roic`, `invested_capital` in [aletheia/calculations/derivation_registry.py](../../aletheia/calculations/derivation_registry.py)

## What changed

Until this change, ROIC was computed in two places with **different** Invested Capital formulas, depending on which Stage 1 provider fed the pipeline:

| Path | Invested Capital | ROIC behavior |
|---|---|---|
| FMP provider | `Equity + Debt − Cash` (full cash subtracted, no floor) | Inflated on cash-rich tickers |
| XBRL provider | `Equity + Debt − ExcessCash`, floored at 5% × Revenue | Lower, operating-reality-grounded |

A single ticker (GOOGL) produced **21.44% under FMP-mode** and **12.00% under XBRL-mode** — same code path, same formula, two different IC definitions feeding it.

The centralization refactor folds both definitions into a single function and **canonicalizes the XBRL-mode convention**: Invested Capital nets out only *excess* cash, not the full cash balance, and is floored at 5% of revenue to guard against pathological zero-IC cases.

```
ExcessCash      = max(0, Cash − 0.02 × Revenue)
InvestedCapital = max(0.05 × Revenue, TotalDebt + TotalEquity − ExcessCash)
ROIC            = NOPAT / InvestedCapital
NOPAT           = NormalizedEBIT × (1 − effective_tax_rate)
```

## Why this convention

Excess-cash exclusion is the Damodaran-leaning convention. The reasoning: cash held above operating needs is not earning operating returns, so including it in the IC base understates how productively the firm is using its *operating* capital. The platform's other formulas already lean Damodaran (`FCF = OCF − CapEx`, the Liberti EV/EBITDA decomposition), so this canonicalization aligns ROIC with the rest of the framework.

The 2%-of-revenue working-cash threshold and 5%-of-revenue IC floor are conservative defaults — see [derivation_registry.py](../../aletheia/calculations/derivation_registry.py) for the registry entry that documents the precise thresholds and their basis.

## Expected impact per ticker class

| Ticker profile | Cash / Revenue | Expected ROIC delta |
|---|---|---|
| Cash-light, asset-heavy (utilities, REITs, industrials) | < 10% | ≈ 0 (no excess cash) |
| Normal balance sheet | 10-30% | small drop (a few percentage points) |
| Cash-rich tech / financial holdings | 30-100% | meaningful drop (10-50% relative) |
| Cash > Revenue (some financials, conglomerates) | > 100% | large drop (>50% relative) |

ROIC numbers will be **lower across the board** for cash-rich tickers. The new number is not "wrong" — it reflects operating performance excluding idle cash earnings, which is the metric most analysts intend when they say "ROIC."

## If you see a number that surprises you

1. **Check Cash/Revenue.** If > 30%, the drop is expected — that's the convention change.
2. **Check the Validation panel.** Affected metric cells carry a `📐 Convention canonicalized 2026-05` flag for two quarters (retires automatically 2026-Q4 end).
3. **Compare to Schwab.** Schwab uses the full-cash convention. The divergence vs Schwab is **expected and persistent** — it's not a bug, it's a methodology choice. Document any cross-check using Schwab values to flag the expected gap.
4. **Read the registry entry.** `roic` and `invested_capital` in `derivation_registry.py` document the canonical formula. The registry is the source of truth for "what does this number mean."

## Downstream effects audited

- ✅ **Streamlit dashboard / Financials tab / Reports tab** — pulls from `derived_ROIC`. New number propagates automatically.
- ✅ **DCF engine** — reads ROIC for terminal-value reinvestment math. Lower ROIC = lower implied reinvestment rate = slightly lower terminal value for cash-rich tickers. Captured in Phase 1 diff snapshot.
- ⚠️ **LLM agents (`qualitative_synthesis`, `contrarian_v2`, `thesis_synthesizer`)** — these agents reference ROIC vs sector percentiles. Sector-percentile fixtures are recomputed in Phase 1 so the agents reason against the new convention's distribution, not the pre-change distribution.
- ⚠️ **Cross-provider parity tests** — Phase 1 adds tests asserting `roic(provider=fmp) == roic(provider=xbrl)` for a fixture ticker set. Closes the regression gap that allowed the divergence to ship.
- ❌ **External report exports** — historical PDF/HTML reports retain their original numbers; no rewrite. The convention change applies forward only.

## Phase 1 diff snapshot

Baseline captured: [audits/centralization_snapshots/phase1_before_2026-05-17.parquet](../../audits/centralization_snapshots/phase1_before_2026-05-17.parquet)
Post-migration: [audits/centralization_snapshots/phase1_after_2026-05-17.parquet](../../audits/centralization_snapshots/phase1_after_2026-05-17.parquet)
Tickers in scope: 41 (full extended universe)
Metrics in scope: `InvestedCapital`, `NOPAT`, `ROIC`

### Result

| Bucket | Rows | % |
|---|---|---|
| Clean (no change) | 1,445 | 43% |
| Expected (convention shift within band) | 1,893 | 56% |
| Unexpected (surfaced for review) | 13 | 0.4% |

Diff report: [diff_phase1_before_vs_phase1_after_2026-05-17.md](../../audits/centralization_snapshots/diff_phase1_before_vs_phase1_after_2026-05-17.md)

### Historical floor activations — the 13 "unexpected" rows

Phase 1 surfaced 13 rows where the percent-magnitude shift exceeded the expected band (≥30%). On inspection all 13 are early-history small-base records on two tickers — **not bugs**:

- **AMZN 2001-2012**: pre-migration IC of $19M–$3.9B against $3-60B revenue. The old formula's full-cash subtraction combined with negative working capital + cash hoard yielded IC values 1-7% of revenue — mathematically valid but operationally meaningless for ROIC interpretation.
- **MCO 2004, 2006**: same mechanic — pre-migration IC of $11M–$59M against ~$1.4-2B revenue.

The new formula's 5%-of-revenue floor activates on these rows and lifts IC to its sensible lower bound. The new values are *more* operationally meaningful than the old ones — these are the Phase 1 wins, not regressions.

| Row | Pre IC | Post IC | Notes |
|---|---|---|---|
| AMZN FY2007 | $19M | $742M | Floor lifted IC from 0.1% of revenue to 5% |
| AMZN FY2008 | $495M | $958M | Excess-cash netting + floor |
| MCO FY2004 | $11.4M | $71.9M | Floor lifted IC from 0.8% to 5% of revenue |
| MCO FY2006 | $59M | $102M | Floor + excess-cash netting |
| ... | | | 9 more in the same pattern |

These rows are surfaced explicitly so analysts examining historical AMZN/MCO ROIC trajectories know the early years were affected by the canonicalization and that the post-migration numbers are the more reliable view.

Re-running the diff: `python -m scripts.snapshot_universe_metrics --diff phase1_before phase1_after`

## Linked artifacts

- Registry entries: `roic`, `invested_capital` in [derivation_registry.py](../../aletheia/calculations/derivation_registry.py)
- Centralized formula module: `aletheia/calculations/formulas/` (created in Phase 1)
- Snapshot diff script: [scripts/snapshot_universe_metrics.py](../../scripts/snapshot_universe_metrics.py)
- Architecture-lock test: `tests/architecture/test_single_formula_source.py` (created in Phase 5)
