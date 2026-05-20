# Methodology change — FCFF & NetDebt centralization

**Effective date:** 2026-05 (Phase 2 of formula-centralization refactor)
**Affected metrics:** `derived_FCFF`, `derived_NetDebt`
**Registry entries:** `fcf`, `net_debt` in [aletheia/calculations/derivation_registry.py](../../aletheia/calculations/derivation_registry.py)
**Companion memo:** [2026-05-roic-invested-capital.md](2026-05-roic-invested-capital.md) (Phase 1 — ROIC/IC)

## What changed

Two formulas that previously diverged between the FMP and XBRL paths are now computed by a single central function — [aletheia/calculations/formulas/cash_flow.py](../../aletheia/calculations/formulas/cash_flow.py) and [aletheia/calculations/formulas/balance_sheet.py](../../aletheia/calculations/formulas/balance_sheet.py). Both adapters call the same code, eliminating the cross-provider drift surface for these metrics.

### FCFF — from "alias FCF" to the full CFA formula

| Path | Pre-Phase 2 | Post-Phase 2 |
|---|---|---|
| FMP | `FCFF := FCF = OperatingCF − CapEx` | `NOPAT + D&A − CapEx − ΔNWC` |
| XBRL/cleaning_engine | `NOPAT + D&A − CapEx − ΔNWC` | Same — unchanged |

The FMP adapter previously aliased FCFF to FCF because it didn't have access to the `ChangeInWorkingCapital` field. FMP actually does expose this field in `changeInWorkingCapital`; Phase 2 wires it through so the FMP path can compute the proper firm-level cash flow.

### NetDebt — EV-aligned definition canonicalized

| Component | Pre-Phase 2 FMP | Pre-Phase 2 XBRL | Post-Phase 2 (both) |
|---|---|---|---|
| Long-term debt | ✓ | ✓ | ✓ |
| Short-term debt | ✓ | ✓ | ✓ |
| Current portion of LT debt | ✗ | ✓ | ✓ |
| Finance leases | ✗ | ✓ (with full XBRL fallback ladder) | ✓ |
| Less: Cash | ✓ | ✓ | ✓ |
| Less: Short-term investments | ✓ | ✓ | ✓ |
| Less: Long-term investments | ✗ | ✓ | ✓ |

The FMP path previously missed three components — current LT debt, finance leases, and long-term marketable securities. For cash-rich filers with large marketable-securities portfolios (AAPL ~$77B in LT investments, GOOGL/MSFT/META similar) this materially understated the cash position and inflated EV / EBITDA multiples that depend on NetDebt.

The finance-lease fallback ladder (curr+nc → consolidated total → PV from undiscounted maturity schedule) is XBRL-specific and stays in the cleaning_engine; the FMP path uses the consolidated `FinanceLeaseLiability_Total` field when available.

## Why this convention

For NetDebt: the EV-aligned definition is the convention every multiple-decomposition formula in this codebase assumes (EV/EBITDA, EV/FCF, ND/EBITDA). The FMP path's narrower definition was producing inconsistent inputs to those ratios. Canonicalization brings the inputs into alignment.

For FCFF: the CFA-textbook formula is what every DCF reinvestment-rate calculation in this codebase assumes (terminal value formula uses NOPAT × (1 - g/ROIC), which presumes the firm-level cash flow includes the full NOPAT-not-just-FCF basis). The previous FMP alias to FCF was masking the working-capital component, which matters most for high-growth filers (ΔNWC large positive) and inventory-cycle businesses (ΔNWC volatile).

## Expected impact per ticker class

### NetDebt

| Filer profile | Magnitude of shift |
|---|---|
| Asset-heavy, low-cash (utilities, REITs) | Small (~0-5%) — they don't have LT investments and finance leases are already in the LT debt aggregate FMP exposes |
| Cash-rich tech (AAPL, GOOGL, META, MSFT) | Large — LT marketable-securities portfolios were not being netted; NetDebt becomes substantially more negative (net cash) |
| Insurance/financial holdings (BRK-B, JPM) | Largest — these filers carry hundreds of billions in non-cash liquid assets that move them deep into net-cash territory |

Universe-diff sample (FY2025):
- **JPM FY2025**: NetDebt went from -$537B to **-$2,275B** (was missing $1.7T of LT investments)
- **AXP FY2025**: NetDebt went from +$9.2B to **-$211.8B** (sign flip — the asset side dominates once LT investments are included)
- **BRK-B FY2025**: NetDebt went from -$234B to **-$576B** (extra $342B of liquid assets recognized)
- **TSM FY2025**: NetDebt went from -$2.06T to **-$2.27T** (modest 10% shift; TSM doesn't carry much LT-investment portfolio)

### FCFF

| Filer profile | Magnitude of shift |
|---|---|
| Steady-state filer with small ΔNWC | Small (a few % of FCF) — D&A ~= maintenance CapEx, NOPAT close to OCF |
| High-growth filer (NWC inflating with revenue) | Larger downward shift — ΔNWC subtraction now captured |
| Inventory-cycle businesses | Most volatile — ΔNWC swings drive FCFF away from FCF |

Universe-diff sample (FY2024-2025):
- **JPM FY2024**: FCFF went from -$42B to **+$181B** (the FCF alias was capturing the bank's massive working-capital-style swings as if they were investing-cash; FCFF now uses NOPAT basis which strips that out)
- **TSM FY2025**: FCFF went from $1,098B to **$1,236B** (~13% improvement; ΔNWC was negative — cash freed)

## Downstream effects audited

- ✅ **DCF engine** — reads NetDebt for EV→equity bridge. New NetDebt is more accurate; intrinsic-per-share calculations will reflect the corrected cash position.
- ✅ **Multiple decomposition** — `EV/EBITDA = (MarketCap + NetDebt) / EBITDA`. EV will drop for cash-rich filers, which lowers the implied multiple. This is the *correct* number.
- ✅ **Reverse DCF** — uses NetDebt the same way DCF does. Implied growth becomes lower for cash-rich filers.
- ✅ **Screening ratios** — `Net Debt / EBITDA` ratio shifts. Cash-rich filers now appear more conservative on leverage.
- ⚠️ **Historical EV comparisons** — if you've recorded EV / EV-multiples in external spreadsheets and want to compare to the new values, the canonicalization gap is meaningful for cash-rich filers. Re-pull from the registry as your reference.
- ⚠️ **Schwab cross-check** — Schwab uses a narrower NetDebt definition (no LT investments). Divergence vs Schwab on cash-rich filers is now larger but **expected and persistent**.

## Phase 2 diff snapshot

Baseline: [audits/centralization_snapshots/phase2_before_2026-05-17.parquet](../../audits/centralization_snapshots/phase2_before_2026-05-17.parquet)
Post-migration: [audits/centralization_snapshots/phase2_after_2026-05-17.parquet](../../audits/centralization_snapshots/phase2_after_2026-05-17.parquet)
Diff report: [diff_phase2_before_vs_phase2_after_2026-05-17.md](../../audits/centralization_snapshots/diff_phase2_before_vs_phase2_after_2026-05-17.md)

| Bucket | Rows | % |
|---|---|---|
| Clean (no change) | 3,928 | 70% |
| Expected (convention shift) | 1,657 | 30% |
| Unexpected | **0** | 0% |

**Regression check**: Phase 1 metrics (IC/NOPAT/ROIC) showed **zero** drift — confirming Phase 2 didn't accidentally perturb the Phase 1 canonicalization. The centralized formula module is composable.

## Linked artifacts

- Phase 2 formulas: [aletheia/calculations/formulas/cash_flow.py](../../aletheia/calculations/formulas/cash_flow.py), [aletheia/calculations/formulas/balance_sheet.py](../../aletheia/calculations/formulas/balance_sheet.py)
- Phase 2 tests: [tests/calculations/test_formulas_phase2.py](../../tests/calculations/test_formulas_phase2.py)
- Architecture lock test (Phase 5): `tests/architecture/test_single_formula_source.py` (pending)
