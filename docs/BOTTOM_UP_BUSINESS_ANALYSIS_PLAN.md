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
