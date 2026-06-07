# Investment-Memo Completeness — Gap Analysis

Status of the full buy-side investment memo against the 12-section completeness
map (v2). Reconciled against the **actual codebase** as of 2026-06-07. Several
sections the map marks red were built in recent sessions — those corrections are
called out with ⬆️.

**Legend:** 🟢 implemented · 🟡 partial · 🔴 gap · ⬆️ changed since the map was drawn

---

## §1 · Executive decision

| Item | Map | Reality | Evidence / gap |
|---|---|---|---|
| Conviction tier + MoS | 🟢 | 🟢 | `conviction_scorer.py` (5 pillars → tier); hero strip |
| Position size (% of portfolio) | 🔴 | 🟡 ⬆️ | A numeric sizing band now exists in `downside_protection.position_sizing` (MoS × asymmetry × tier). Still a *band*, not a single % feeding a portfolio model. |
| Entry **price** triggers | 🔴 | 🔴 | Decision conditions are observable thresholds (growth/multiple), not "buy at $X" |

## §2 · Cross-source triangulation

| Item | Map | Reality | Evidence / gap |
|---|---|---|---|
| Wall St consensus (PT range, count, dispersion) | 🔴 | 🟡 ⬆️ | `current_state.analyst_sentiment` has target avg, implied upside, B/H/S counts, recent up/downgrades. **Missing: PT dispersion / std-dev across analysts.** |
| Sector / self multiples | 🔴 | 🟡 ⬆️ | Sector median EV/EBITDA via `sector_valuation` (built). **Missing: own 5-year-average multiple** (only historical CAGR exists, not own historical EV/EBITDA). |
| **WACC triangulation** (framework vs sell-side) | 🔴 | 🔴 | No comparison of our WACC against a sell-side / external WACC. New item; see §7. |

## §3 · Current-state reconciliation

| Item | Map | Reality | Evidence / gap |
|---|---|---|---|
| Y1 growth vs consensus | 🟢 | 🟢 | `_consensus_forward_growth` |
| Event ingestion | 🟢 | 🟢 | `current_state_events` (grounded LLM, cached) |
| HIGH-flag gate | 🟡 | 🟢 ⬆️ | Acknowledgment workflow makes it **enforced** (gate blocks until resolved). Tier calibration still pending. |
| Margin / capex reconciliation | 🔴 | 🔴 | Only Y1 *growth* is reconciled vs consensus; margin & capex assumptions aren't checked against consensus/history |
| Pattern detection (cumulative regulatory/margin) | 🔴 | 🔴 | No multi-event trend detection |
| Event → assumption link | 🔴 | 🔴 | Events raise flags but don't map to *which DCF lever* to move |

## §4 · Business fundamentals — 🟢 complete

Multi-year history (rev/EBIT/FCF/ROIC), segment + customer concentration, and
the **Norm. EBIT** column (⬆️ built) are all present.

## §5 · Quality pillars

| Item | Map | Reality | Evidence / gap |
|---|---|---|---|
| Moat / Health / Tailwind / MoS | 🟢 | 🟢 | `conviction_scorer` P1–P4 |
| Leadership (SBC) | 🟡 | 🟢 | P5 uses SBC %FCF; surfaced in Financials |
| **Current-state pillar in scored total** | 🔴 | 🔴 | Still an *overlay tag only*. `conviction_scorer` has **zero** references to current_state; total is 5×5=25, not 30. Deferred (display-only decision). |

## §6 · Valuation analysis

| Item | Map | Reality | Evidence / gap |
|---|---|---|---|
| Five scenarios | 🟢 | 🟢 | bull/base/bear + scenario library |
| Reverse DCF + multiples | 🟢 | 🟢 | `reverse_dcf`, `multiple_decomposition` |
| WACC build (basic) | 🟡 | 🟡 | CAPM single calc (see §7 for the depth gap) |
| **Probability weighting (non-equal)** | 🔴 | 🔴 | No `probability`/`weight` field; scenarios independent, no blended IV |
| **Phased assumptions (Y1-3/4-6/7-10)** | 🔴 | 🟡 | 2-stage exists (Y1-5 / Y6-10 / terminal); finer 3-stage phasing not supported |

## §7 · Discount-rate detail — 🔴 all gaps (NEW, highest-leverage)

> WACC drives ~15–25% of IV per 100 bps. Current treatment is a single CAPM
> calc clamped to [6%, 16%], with per-scenario bull/bear bumps. Everything below
> is missing.

| Item | Reality | Gap |
|---|---|---|
| Component justification | 🟡 | Beta (5y weekly vs SPY, sector floor), rf (live), ERP (flat 4.75% Damodaran), Kd (interest/avg debt) are computed — but **not documented/surfaced per-component with sources** in the memo |
| Size + sector premium | 🔴 | `cost_of_equity = rf + β·MRP` only — **no size premium** (by market cap) or **sector premium** |
| Country risk premium | 🔴 | No geographic-revenue-weighted CRP (matters for ASML/TSM/NVO) |
| Idiosyncratic premium | 🔴 | No add-on for concentration / litigation / going-concern / restructuring risk |
| WACC sensitivity table | 🔴 | No "IV at WACC ±50/100/200 bps" — the single most useful WACC artifact |
| Implied WACC | 🔴 | Reverse-DCF solves for *growth*; there is **no solve-for-WACC** (what discount rate the market price implies) |
| Risk-adjusted scenario WACC | 🟡 | Bear bumps WACC +Xbps / bull compresses — mechanical, not a full risk-profile-driven rate |
| Capital-structure target | 🔴 | Uses *current* E/V, D/V weights; no target weights or recent-M&A adjustment |
| Discount-rate quality score | 🔴 | No beta R², ERP-method, component-completeness quality flag |

## §8 · Sector & market context — 🟢 row built ⬆️

| Item | Map | Reality |
|---|---|---|
| Sector relative valuation | 🔴 | 🟢 ⬆️ (`sector_valuation`) |
| Market signal | 🔴 | 🟢 ⬆️ (`market_signal` — 52w + momentum/MA) |
| Analyst sentiment | 🔴 | 🟢 ⬆️ (`analyst_sentiment`) |
| Policy / regulatory | 🔴 | 🟢 ⬆️ (`policy_regulatory`) |
| **Cross-signal synthesis** | 🔴 | 🔴 — no agent reads all four signals together for a combined read |

## §9 · Risk analysis / downside protection — mostly built ⬆️

| Item | Map | Reality |
|---|---|---|
| Downside scenarios (ladder) | 🔴 | 🟢 ⬆️ (engine bear + sector-median de-rating; probabilities not yet attached) |
| Failure-mode catalog | 🔴 | 🟢 ⬆️ (contrarian agent: 3-5 named modes + monitoring metrics) |
| Asymmetry ratio | 🔴 | 🟢 ⬆️ |
| MoS by risk category | 🔴 | 🟢 ⬆️ (lifecycle-stage required MoS vs actual) |
| Position-sizing engine | 🔴 | 🟢 ⬆️ (band from MoS × asymmetry × tier) |
| Pre-mortem | 🔴 | 🟢 ⬆️ (contrarian agent) |
| Drawdown history | 🔴 | 🔴 — needs historical price series |
| Portfolio correlation | 🔴 | 🔴 — needs a portfolio model (cross-holding) |
| Stop-loss / re-eval triggers | 🔴 | 🔴 — no defined triggers |

## §10 · Decision framework

| Item | Map | Reality |
|---|---|---|
| Decision conditions (G/A/R) | 🟡 | 🟡 — exist (thesis_synthesizer), observable-based, no price levels |
| Scaling plan (ladder) | 🔴 | 🔴 — no initial/add/max/trim ladder |

## §11 · Data quality + audit trail

| Item | Map | Reality |
|---|---|---|
| Schema-contract flags | 🟡 | 🟡 — surfaced in Pipeline Explorer / Quality, **not** prominent on the Deep Dive conviction view |
| Override audit trail | 🔴 | 🟢 ⬆️ — `current_state_acknowledgments` table (who/when/why/decision) |

## §12 · Required analyst judgment — 🟢

Gaps the framework explicitly defers to the analyst are already surfaced.

---

## True remaining gaps (consolidated, prioritized)

1. **§7 Discount-rate detail** — the biggest unbuilt block, high IV leverage:
   - WACC **sensitivity table** (IV at ±50/100/200 bps) — quick deterministic win
   - **Implied WACC** (solve discount rate s.t. IV = price) — mirrors reverse-DCF
   - Component **premia**: size, country (geo-revenue-weighted), idiosyncratic
   - Per-component **justification + quality** surfacing (beta R², ERP method)
   - Capital-structure **target** weights
2. **§6 probability-weighted IV** — non-equal scenario weights → one blended IV (deterministic, quick)
3. **§5 score the current-state pillar** — fold into conviction total (25→30), recalibrate tiers
4. **§8 cross-signal synthesis** — combine the four current-state signals into one read
5. **§9 portfolio-level** — drawdown history, correlation, stop-loss (needs price series + portfolio model)
6. **§1/§10 executable plan** — single position %, price-level entry triggers, scaling ladder
7. **§2/§3 smaller adds** — own 5Y multiple, analyst PT dispersion, margin/capex reconciliation, WACC triangulation vs sell-side
8. **§11 prominence** — surface schema-contract flags on the conviction view

## Suggested build sequence (from the map)

`§2 cross-source → §7 WACC depth → §8/§9 four-dim signals + downside synthesis → §10 decision → §11 → calibrate gates`

Cheap-and-high-value first (cross-source completions + WACC sensitivity/implied),
then the WACC component premia, then synthesis and the executable-plan layer,
then recalibrate the conviction gate now that current-state + downside feed it.

## Note on report freshness

The HTML/serving report only regenerates on a **Stage 4 run** (that's when
`lead.py` attaches `current_state` + `downside_protection` and re-renders). A
lightweight "regenerate report from current data" path (compose + `generate_html`
without a paid agent run) would let existing reports pick up new sections
immediately — currently an open TODO.
