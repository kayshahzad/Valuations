# Bottom-Up Business Analysis — Implementation Plan

A deeper Section 4 for the memo: 12 dimensions in 6 themes (A–F) that describe the
*business reality* beneath the financial lines, and — crucially — **ground the
top-down DCF assumptions** that are currently extrapolated only from history.

The diagram's thesis: *"valuation precision sits on assumptions that are
historically extrapolated rather than business-grounded."* So this layer is built
**connection-first** — every bottom-up dimension exists to ground a specific
top-down assumption, shown side-by-side with the historical anchor (the same
"shown, not auto-applied" pattern used for the WACC premia).

## What already exists (~40%, reuse)

| Theme | Reusable today | Where |
|---|---|---|
| A. What it sells | `revenue_segments`, `key_customers`, `concentration_details`, `pricing_power_evidence` | `qualitative_sections.py` (ForensicReport) |
| B. Market size | TAM justification (HITL), `market_position` dim (rank, share trajectory) | `thesis_builder.py`, `qualitative_dimensions.py` |
| C. Unit economics | `operating_leverage_analysis` (narrative), pricing power | `qualitative_sections.py` |
| D. Growth source | **organic vs M&A** (`_organic_cagr_ex_breaks`, computed but console-only) | `dcf_engine.py:899` |
| E. Innovation | `technology_disruption_risk`, `competitive_trajectory`, `capital_allocation` dims; R&D % (FMP) | `qualitative_dimensions.py` |
| F. Industry | `industry_concentration` (HHI proxy), lifecycle stage, `regulatory_exposure` | config + dims |

## What's genuinely new (~60%, mostly LLM extraction)

Market-share quantification, TAM $/methodology, named contracts + recompete dates,
distribution channels, CAC/LTV/cohorts, cost-per-unit, segment-level margin
trajectory, R&D pipeline projects, new-product launches, market-vs-share growth
split. **These require structured LLM extraction from 10-Ks/filings → Stage-4 cost.**

## Architecture

1. **`report["business_analysis"]`** — a NEW nested block (themes A–F). Do NOT
   mutate the frozen `ForensicReport`/`ValueChainReport` schemas (~50 downstream
   refs). Composes from: (a) existing extracted fields, (b) qualitative dims,
   (c) deterministic growth decomposition, (d) new extraction (later phases).
2. **`report["assumption_grounding"]`** — the **keystone**. Compares each DCF
   assumption (engine value) against a business-grounded reference, shown not
   applied:

   | Bottom-up dimension | Grounds |
   |---|---|
   | organic CAGR + consensus | Y1-5 / Y6-10 CAGR |
   | unit-economics trajectory | terminal EBIT margin |
   | industry lifecycle stage | terminal growth |
   | disruption + concentration | idiosyncratic WACC premium (feeds §7) |
   | specific failure mechanics | failure-mode catalog (§9) |
   | new-product / trend riding | bull-case scenario |

3. **Sector templates** (`config/business_analysis_templates.py`) — weight which
   themes matter per business type (defense→contracts/recompete; pharma→pipeline;
   SaaS→NRR/TAM; …). Reuses the existing lifecycle/sector classification.

## How existing implementation changes

| File | Change |
|---|---|
| `dcf_engine.py` | Expose `_organic_cagr_ex_breaks` output (don't recompute) |
| `qualitative_sections.py` | ADD a `BusinessAnalysis` schema (new block) |
| `qualitative_extraction.py` | Extend Stage-4 prompt for new structured fields (Phase 2+) |
| `lead.py` | Attach `report["business_analysis"]` + `report["assumption_grounding"]` |
| new `aletheia/tools/business_analysis.py` | growth decomposition + theme scaffold |
| new `aletheia/tools/assumption_grounding.py` | the keystone comparison |
| new `config/business_analysis_templates.py` | sector dimension weights |
| `report_generator.py` | expand §4 with theme renderers; grounding card in §6/§7 |
| `api_main.py` | add deterministic pieces to the no-LLM rebuild + /dcf payload |

## Build order (reuse-first, inverts the diagram to front-load value)

- **Phase 0 (no LLM):** expose growth decomposition (organic/M&A) + scaffold the
  `business_analysis` block from existing fields. Surface in §4.
- **Phase 1 (no LLM) — keystone:** `assumption_grounding` on data we already have
  (organic-CAGR + consensus → Y1-5; lifecycle → terminal growth; disruption +
  concentration → idiosyncratic WACC premium). The architectural payoff.
- **Phase 2 (LLM):** A + B extraction (product/contracts, TAM/share).
- **Phase 3 (LLM):** C + E (unit economics, R&D pipeline).
- **Phase 4:** sector templates + per-sector weighting.
- **Phase 5:** market-vs-share growth split (needs a market-growth reference).

## Cost note

Phases 0–1 are deterministic / reuse (no new LLM, work in the no-LLM rebuild).
Phases 2–3 add Stage-4 extraction cost and populate only on an agent re-run.

---

# Refinement plan v2 — post-LDOS-output review

The first LDOS Stage-4 run populated 11/17 dimensions. Review verdict: strong on
growth decomposition (raw 6.9% = organic 2.3% + M&A 4.7%, FY2017 break), R&D
intensity, customer sub-decomposition. Weak on TAM/share (blank), unit economics
(qualitative only), industry context. **Root cause of several gaps = peer set.**

## Confirmed root cause: peer set

defense_govt in the 56-ticker universe = {LDOS} alone — BAH/SAIC/CACI/LHX are NOT
ingested. So the universe-grouping market-growth reference can never compute for
LDOS, AND the sector multiple still uses the raw FMP "Technology" median. Both
need a **curated peer list + FMP-backed peer stats** (fetch each named peer's
revenue/multiple from FMP, independent of our universe).

## Prioritized fixes

**P1 — Curated peer lists + FMP peer stats (keystone; deterministic, cached).**
- `config/peer_lists.py`: `PEER_LISTS = {"LDOS": ["BAH","SAIC","CACI","LHX","GD"], …}`
  (curated for key holdings; fall back to universe peer_group when absent).
- `peer_stats(ticker)`: from the named peers' FMP data (cached) compute
  **median revenue CAGR** (→ market-growth reference for market-vs-share) and
  **median EV/EBITDA** (→ replaces the hardcoded SECTOR_MEDIAN for these names,
  fixing the §7 cascade) + peer margin band (context).
- Unlocks: market-vs-share (D), sector-relative multiple (§2/§7), peer margin
  context (C/F). One change, three cascades fixed.

**P2 — Segment-level economics (extraction + FMP).**
- FMP `revenue-product-segmentation` gives segment revenue deterministically;
  extend the 10-K extraction for **segment operating margin + trajectory**
  (the failure-modes already reference "Defense Systems margin < 6.5%", so the
  data is in the filing). New `segment_economics: [{segment, rev_pct, margin,
  trend}]` on BusinessAB. Feeds terminal-margin grounding.

**P3 — TAM sizing with explicit confidence (LLM; hallucination-guarded).**
- Extend extraction: when the 10-K doesn't state a $ TAM, produce a
  *triangulated estimate* with REQUIRED `tam_confidence` (low/med/high) +
  `tam_methodology`, or explicitly "not estimable". Compute implied share =
  revenue ÷ TAM. Always label confidence so a rough number never reads as fact.

**P4 — Richer assumption grounding.**
- Y1-5 CAGR grounded as a **build-up**: organic (2.3%) + share gain (peer-relative)
  + M&A run-rate → a defensible forward band, vs the engine's single number.
- Terminal EBIT margin grounded on **current margin + segment mix** (from P2),
  not just history.

**P5 — Coverage labeling (small UX).** Distinguish "not applicable / not disclosed"
(TAM for a non-disclosing filer, CAC/LTV for a non-subscription model) from
"pending extraction", so genuine N/A doesn't read as un-run.

## Build order
P1 (cascades) → P2 (segment margins) → P4 (grounding uses P1+P2) → P3 (TAM) → P5.
P1/P2/P4 are mostly deterministic/FMP; P3 is the LLM-risk item.
