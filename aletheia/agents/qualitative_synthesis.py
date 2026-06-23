"""aletheia/agents/qualitative_synthesis.py

Consolidated qualitative-narrative agent — one LLM call replacing three.

Replaces forensic_agent + value_chain_agent + strategic_context_agent with
a single LLM invocation that emits a nested structured output covering all
three topical lenses. Topical structure preserved via the existing Pydantic
schemas (`ForensicReport`, `ValueChainReport`, `StrategicContextReport`)
nested under one `QualitativeSynthesis` parent.

Why consolidate (per the empirical investigation):
  - 90% of token cost in the previous pipeline was the 10-K text being
    ingested THREE times. One read here.
  - Each agent's prompt was independent, so the LLM had no cross-topic
    context. Single prompt sees all three lenses simultaneously.
  - Three separate LLM calls produced overlapping intermediate work
    (each agent re-read the same DB rows). Single call shares context.

What is preserved:
  - Topical structure via three nested sections (no flat "synthesis prompt")
  - Per-section field schemas (moat_evidence, bottleneck_analysis, etc.)
  - Per-section scenarios — capped at 1 per lens, max 3 total per ticker
  - Backward compat: the agent writes the three legacy state keys
    (forensic_report, value_chain_report, strategic_context_report) so
    downstream consumers (lead, conviction_scorer) need no changes
  - Python-computed cyclicality fields (z_score, is_peak) overwrite the
    LLM output — same architecture invariant as `context.py`

What is dropped (per D2 plan):
  - Web search calls (3 → 0 in this agent). The empirical investigation
    flagged web-search marginal value as unclear; we ship without and
    A/B test re-introduction in week 3-4 if needed. Each section's
    prompt instructs the LLM to flag uncertainty when supplier-margin or
    competitor-margin context would be useful.

Failure mode:
  - Per-section topical isolation in the schema means a single bad LLM
    output corrupts only that section. Catches all exceptions and writes
    empty stubs to the three state keys (lead.py's required-keys check
    still passes — same pattern as the legacy three agents).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field, ValidationError, model_validator

from config import MODEL_NAME
from aletheia.utils.tracing import tracer

# Schemas + DB-context helpers extracted from the now-removed legacy
# narrative agents. Architecture-lock test enforces no forbidden field
# names; frozen=True is set per-class.
from aletheia.agents.qualitative_sections import (
    ForensicReport,
    ValueChainReport,
    StrategicContextReport,
    load_db_context_forensic as _forensic_load_db,
    load_db_context_value_chain as _vc_load_db,
    build_db_context_str_forensic as _forensic_db_str,
    build_db_context_str_context as _context_db_str,
)
from aletheia.tools.cyclicality import calculate_z_score
from aletheia.tools.forensic_metrics import compute_operating_leverage_score


# ─────────────────────────────────────────────────────────────────────────────
# Depth thresholds — minimum character counts per narrative field
# ─────────────────────────────────────────────────────────────────────────────
# Set near the 10th-percentile of baseline (3-agent path) prose lengths.
# When an LLM call returns content below these floors, the agent retries
# once with an explicit "produce deeper analysis" addendum. The constraint
# is "we do not want to lose any depth" — these floors are the structural
# enforcement of that requirement.

_DEPTH_FLOOR = {
    "forensic.moat_evidence":            300,   # p10 baseline ~350
    "forensic.business_description":     300,   # p10 ~336
    "value_chain.bottleneck_analysis":   350,   # p10 ~413
    "value_chain.pricing_power_assessment": 220,  # p10 ~261
    "value_chain.analysis_summary":      400,   # p10 ~512
    "strategic_context.summary":         300,   # p10 ~350
    "strategic_context.intangible_risk_assessment": 180,  # p10 ~207
}


# ─────────────────────────────────────────────────────────────────────────────
# Consolidated schema
# ─────────────────────────────────────────────────────────────────────────────

class QualitativeSynthesis(BaseModel):
    """Top-level structured output of the consolidated narrative agent.

    Three nested topical sections. Each section's `scenarios` field is
    capped at 1 entry by the prompt (the consolidated agent emits at most
    3 scenarios total — one per analytical lens — per the locked design
    decision in week-1.5 planning).

    Cross-field validator (`_check_narrative_depth`) enforces minimum
    character counts on the seven narrative fields where depth matters
    most. Failures raise ValidationError — the agent catches it and
    retries with a depth-augmented prompt. This is structural enforcement
    of "we do not want to lose any depth."
    """
    model_config = {"frozen": True}

    forensic:          ForensicReport          = Field(description="Moat, business profile, concentration, op leverage narrative")
    value_chain:       ValueChainReport        = Field(description="Porter's five forces, supplier bottlenecks, pricing power, pass-through")
    strategic_context: StrategicContextReport  = Field(description="Cyclicality interpretation, intangible risk, growth quality")

    @model_validator(mode="after")
    def _check_narrative_depth(self) -> "QualitativeSynthesis":
        too_short: list[str] = []
        for path, floor in _DEPTH_FLOOR.items():
            section, attr = path.split(".", 1)
            obj = getattr(self, section)
            text = getattr(obj, attr, None) or ""
            if len(text) < floor:
                too_short.append(
                    f"{path} is {len(text)} chars (minimum {floor}); "
                    f"current text starts: {text[:80]!r}"
                )
        if too_short:
            raise ValueError(
                "Narrative-depth floor not met:\n  " + "\n  ".join(too_short)
            )
        return self


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT_BODY = """
You are the Qualitative Synthesis Agent for {ticker}. You produce a single
unified narrative covering THREE distinct analytical lenses, each emitted
as a structured section. The 10-K text below is shared across all three
sections — read it once, answer all three.

CRITICAL DEPTH REQUIREMENT
══════════════════════════════════════════════════════════════
This is a CONSOLIDATED agent replacing three separate narrative agents.
It is essential that prose depth match what three independent agents
would produce, NOT a compressed/summarized version.

Each narrative field has a MINIMUM CHARACTER FLOOR enforced by the
output schema. Outputs below the floor are REJECTED and you will be
asked to retry. The floors are calibrated to the 10th-percentile of
3-agent baseline output — they are the bar for acceptable depth.

What "depth" means in practice:
  - 3-5 sentences per narrative field, not 1-2
  - Each sentence carries a SPECIFIC FACT or analytical claim, not
    generic restatement
  - Bidirectional analysis where applicable (e.g., "TSMC has leverage
    BUT NVIDIA's scale offsets it" — both halves)
  - Pricing-power claims include both the evidence AND the mechanism
    (e.g., "46.9% gross margins" PLUS "expanding via Pro-model ASP
    growth despite saturated market")
  - Bottleneck analysis names the supplier, describes their leverage,
    explains the offset (NVIDIA's scale, Apple's prepayments, etc.)
    AND the consequence if the bottleneck stresses
  - Moat evidence cites multiple distinct facts (renewal rates, SKU
    counts, patent counts, ROIC) — not a single fact restated

══════════════════════════════════════════════════════════════
PRIMARY DATA — INTAKE PIPELINE (verified facts, Python-computed)
══════════════════════════════════════════════════════════════
{db_context}

Operating Leverage Score: {op_leverage_score}/10 (Python-computed from margins above)
Revenue Z-score: {z_score:+.2f} (peak={is_peak}, cyclical haircut applies={applies_haircut})

══════════════════════════════════════════════════════════════
PRIMARY SOURCE — 10-K FILING TEXT
══════════════════════════════════════════════════════════════
{ten_k_text}

══════════════════════════════════════════════════════════════
YOUR TASKS — narrative + boolean judgments + 0-3 scenarios
══════════════════════════════════════════════════════════════

═══ SECTION 1: FORENSIC (output as `forensic`) ═══

  1a. OPERATING LEVERAGE NARRATIVE
      The score ({op_leverage_score}/10) is computed. Explain WHY using
      pipeline data — fixed cost drivers (R&D, SBC, leases, headcount).

  1b. MOAT ANALYSIS
      Score 1-10 each: switching_costs, network_effects, cost_advantage,
      intangibles. moat_score = weighted average.

      moat_evidence (MINIMUM 300 CHARS — usually 4-5 sentences):
      Cite MULTIPLE distinct 10-K facts — retention rates, price hike
      history, patent counts, NRR, SKU concentration, customer base
      size, ROIC level, ecosystem depth. Each sentence should land a
      different specific fact. Example of acceptable depth:
      "Costco's moat is evidenced by membership renewal rates
      consistently 90-93% in the US/Canada (FY24 10-K Item 1). The
      company's 4,000-SKU model creates supplier negotiating leverage
      that competitors with 50,000+ SKUs cannot replicate. Kirkland
      Signature private label drives a substantial share of revenue
      while underpricing national brands by 20-30%, locking in
      price-conscious customers. The 90%+ renewal economics combined
      with capped 14-15% merchandise markups create a structural cost
      advantage that requires multi-year capital build-out to
      replicate."

  1c. PRICING POWER (boolean)
      has_pricing_power: did the company raise prices without material
      churn in last 3 years? Cite specific increases + churn outcomes.

  1d. CONCENTRATION RISK (boolean)
      concentration_risk: any supplier or customer >10% of revenue?
      Source: 10-K Item 1A. Name them.

  1e. BUSINESS PROFILE (from 10-K Item 1)
      business_description (2-3 specific sentences), revenue_segments
      (with %), key_customers (top 3-5), competitive_landscape (2-3
      named competitors), regulatory_risk (one sentence).

═══ SECTION 2: VALUE CHAIN (output as `value_chain`) ═══

  2a. UPSTREAM POWER
      power_ratio: estimate of supplier_GM / target_GM. >1.5 = upstream
      value leak. (Supplier margins not in DB — flag uncertainty.)
      upstream_value_leak: True if power_ratio > 1.5 OR critical bottleneck.

      bottleneck_analysis (MINIMUM 350 CHARS — usually 4-5 sentences):
      Name the critical supplier(s) by full name, describe their
      structural leverage, identify NVIDIA-style offsets (scale,
      prepayments, lock-in, alternative sourcing), and spell out the
      consequence if pricing or supply tightens. The bidirectional
      analysis (supplier leverage AND counterbalances) is required —
      single-sided "TSMC controls supply" is NOT sufficient depth.
      Example of acceptable depth:
      "TSMC (Taiwan Semiconductor Manufacturing Company) holds
      effective monopoly leverage on cutting-edge wafer fabrication
      and CoWoS advanced packaging — capabilities that NVIDIA's
      data-center GPU roadmap depends on entirely. However, NVIDIA's
      multi-year prepayment commitments, status as TSMC's most
      profitable customer category, and reciprocal dependence on
      NVIDIA's volume create a balanced dynamic where TSMC cannot
      arbitrarily extract margin without losing the partnership.
      Pricing pressure from TSMC has historically been passed through
      to end customers without margin degradation, given the absence
      of substitutable foundries at the leading node. Geopolitical
      disruption (Taiwan-strait scenario) remains the unhedged tail
      risk — Samsung Foundry and Intel Foundry Services are not
      production-grade alternatives within a 24-month horizon."

  2b. SUBSTITUTION RISK
      substitution_risk_score 1-10. top_substitutes: top 3 functional
      substitutes + realistic switching scenario.

      substitution_pressure: synthesize substitution_risk_score (above)
      with the gross-margin trend from PRIMARY DATA into a categorical
      judgment. Use these anchors — do not infer from filer language
      alone:
        - high      = score ≥ 7  OR  gross margin contracting AND substitutes
                      functionally near-equivalent
        - medium    = score 4-6  OR  ambiguous threat horizon  OR  margin
                      stable but credible substitute on multi-year horizon
        - low       = score ≤ 3  AND  gross margin stable/expanding  AND
                      no near-functional substitute
        - uncertain = data insufficient (use sparingly)

  2c. DOWNSTREAM PRICING
      pricing_power_assessment (MINIMUM 220 CHARS — usually 3 sentences):
      Combine the EVIDENCE of pricing power (specific margins, ASPs,
      contract escalators) WITH the MECHANISM (why customers accept
      higher prices: switching costs, brand, lock-in, mission-critical
      use case). Single-fact mentions like "premium pricing
      maintained" are insufficient. Example of acceptable depth:
      "Apple exhibits exceptional pricing power evidenced by expanding
      gross margins (46.9% in FY25) and an industry-leading ROIC of
      76.8%. The company drives ASP growth via Pro-tier model launches
      and accessory attachment despite a saturated smartphone market
      where competitors discount aggressively. The mechanism is
      ecosystem lock-in: switching costs across iCloud, App Store
      purchases, AppleCare, and Watch/AirPods integration make
      single-product price-comparison economically irrelevant."
      ── BANKS / INSURERS / LENDERS: do NOT cite gross margin, FCF
      margin, EBIT/EBITDA margin or ROIC — those are industrial-frame
      artifacts that are meaningless for a deposit-funded balance sheet
      (and are omitted from this memo's tables). The bank-correct pricing
      evidence is: net interest margin (NIM), low deposit beta / funding
      cost advantage, efficiency ratio, fee pricing power, and ROE/ROA.
      Use those instead. Never write "cash conversion / FCF margins
      exceeding EBIT margins" for a bank.
      pass_through_capability: True if 10% price increase wouldn't
      lose customers.

  2d. STRATEGIC LEVERAGE + POSITION + SUMMARY
      strategic_leverage_score 1-10 (Porter five forces synthesis,
      narrative only — does NOT feed conviction or calc).

      strategic_position: synthesize ROIC level (PRIMARY DATA), gross-
      margin level + trend, upstream_value_leak (above), and
      substitution_pressure (above) into a categorical market-position
      judgment. Anchor on the PRIMARY DATA numbers — do NOT emit
      "dominant" or "strong" because the 10-K language is positive:
        - dominant  = ROIC > 25%  AND  gross margin > 50%  AND
                      upstream_value_leak=False  AND  substitution_pressure=low
        - strong    = ROIC > 15%  AND  gross margin > 35%  AND
                      substitution_pressure ≤ medium
        - moderate  = ROIC > WACC but lacks structural margin/leverage edge
        - weak      = ROIC ≤ WACC  OR  substitution_pressure=high  OR
                      upstream_value_leak=True
        - uncertain = data insufficient (use sparingly)
      ── BANKS / INSURERS / LENDERS: ROIC and gross margin do NOT apply
      (no invested-capital base; "COGS" = interest expense) — ROIC will
      read artificially below WACC and must NOT be used to call the bank
      "weak", in strategic_position OR analysis_summary. Do NOT write
      "ROIC metrics flag as weak" or similar. Judge market position on
      bank-native evidence: ROE/ROA, scale & deposit-franchise moat,
      market share, efficiency ratio, and funding-cost advantage.

      analysis_summary (MINIMUM 400 CHARS — usually 5-6 sentences):
      One paragraph synthesizing supplier power, buyer power,
      substitution, rivalry, AND overall position. Each Porter force
      must get at least one sentence with a specific claim. Generic
      "well-positioned" summaries fail the floor. (Banks: see the ROIC/
      gross-margin caveat above — never cite ROIC as a weakness.)

═══ SECTION 3: STRATEGIC CONTEXT (output as `strategic_context`) ═══

  3a. DEFERRED REVENUE / GROWTH QUALITY
      Use D8 Revenue Score from pipeline. D8 < 0.7 = quality issue
      already flagged. quality_of_growth_risk = True if D8 < 0.7 OR
      deferred-revenue growth significantly negative vs revenue growth.
      deferred_revenue_trend: interpret the data quantitatively.

      growth_quality: synthesize D8 score (PRIMARY DATA) and the FCF /
      gross margin convergence visible in PRIMARY DATA into a categorical
      judgment. Anchor on the numbers:
        - high      = D8 ≥ 0.85  AND  FCF margin within ~200bps of EBIT
                      margin (clean cash conversion, no working-capital leak)
        - medium    = D8 in [0.7, 0.85)  OR  small persistent FCF/EBIT
                      divergence  OR  deferred-revenue trend mildly negative
        - low       = D8 < 0.7  OR  FCF margin chronically below EBIT
                      (working-capital leak)  OR  deferred-revenue declining
                      sharply vs revenue
        - uncertain = revenue history too short or D8 unavailable
      ── BANKS / INSURERS / LENDERS: the FCF-vs-EBIT-margin "cash conversion"
      test does NOT apply (no unlevered FCF; FCF/gross/EBIT margins are
      omitted from this memo as meaningless for a deposit-funded balance
      sheet). Do NOT write "cash conversion remains robust / FCF margins
      exceeding EBIT margins" for a bank. Anchor growth quality on
      bank-native signals instead: deposit & loan growth, net interest
      income trajectory, fee-income mix/durability, provisioning trend,
      and ROE stability through the cycle.

  3b. INTANGIBLE DECAY (patent / contract expirations)
      intangible_risk_assessment (MINIMUM 180 CHARS):
      Use 10-K Item 1 Patents/Proprietary Rights section. Cite
      specific patent cliffs, exclusivity dates, contract renewals,
      products, and revenue exposure where disclosed. If no material
      risk found, explain WHY the company's IP portfolio is durable
      (e.g., "diversified across thousands of patents, no single
      expiration material"). Don't just write "no material risk."
      revenue_at_risk_percent: estimate % revenue tied to assets
      expiring within 5 years (0.0 if none, flag uncertainty).
      terminal_haircut: True if estimate > 20%.

  3c. CYCLICALITY INTERPRETATION
      Z-score and peak flag are pre-computed Python facts ({z_score:+.2f},
      peak={is_peak}). Do NOT override these in your output — return them
      exactly. Your job: cyclicality_classification (cyclical /
      non_cyclical / ambiguous) + economic interpretation.

  3d. SUMMARY
      summary (MINIMUM 300 CHARS — usually 4 sentences):
      Synthesize cyclicality + growth quality + intangible risk,
      explicitly referencing the z-score number, the D8 revenue
      score, and the intangible exposure estimate. A summary that
      doesn't cite the z-score or quality-of-growth signals fails
      the floor.

═══ SCENARIOS — at most 1 per lens, max 3 total ═══

Each section may propose AT MOST 1 alternate DCF scenario via its own
`scenarios` field. Total scenarios across all three sections ≤ 3.

A scenario specifies:
  - name (short label)
  - scenario_type: "bull" / "bear" / "base_alternative"
  - proposed_by: "forensic" / "value_chain" / "context" (must match
    the section emitting the scenario)
  - rationale (1–2 sentence justification)
  - ONLY these bounded override fields (set ONLY ones with a thesis):
      * revenue_growth_y1_5    [0.0, 0.45]
      * revenue_growth_y6_10   [0.0, 0.25]
      * terminal_margin_decay  [0.5, 1.0]
      * terminal_growth        [0.0, 0.04]
      * base_revenue_normalization (USD, > 0; useful when peak=True)

You may NOT use these analyst-authored fields (they have stricter
bounds and are reserved for human-authored scenarios):
  - terminal_ebit_margin
  - capex_pct_revenue
  - discount_rate
  - tax_rate

You may NOT override WACC, ROIC, beta. Empty scenarios list per
section is preferred when no high-conviction alternate hypothesis exists.
Quality over quantity.

══════════════════════════════════════════════════════════════
CRITICAL ARCHITECTURE RULES
══════════════════════════════════════════════════════════════
- Do NOT change Python-computed fields: revenue_z_score, is_cyclical_peak,
  applies_cyclical_haircut, recommended_base_revenue,
  operating_leverage_score. These are sourced from calc_node — your
  output is overwritten with the Python values regardless.
- Do NOT emit wacc_penalty, growth_decay_reduction, or any field that
  could mutate calc-layer math. The architecture lock test rejects
  these names if introduced.

Return QualitativeSynthesis JSON.
"""

# Compiled ChatPromptTemplate built from the body string. The retry path
# in the agent reuses `_PROMPT_BODY` to construct an augmented prompt;
# accessing `.template` on `_PROMPT` directly is unreliable across
# langchain_core versions (older versions exposed it; current does not).
_PROMPT = ChatPromptTemplate.from_template(_PROMPT_BODY)


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────

def qualitative_synthesis_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Single LLM call producing all three topical narrative outputs.

    Writes three legacy state keys (forensic_report, value_chain_report,
    strategic_context_report) for backward compat with downstream
    consumers (lead, conviction_scorer, capital_structure).
    """
    print("---QUALITATIVE SYNTHESIS AGENT (consolidated)---")
    ticker = state["ticker"]

    # ── 1. Load DB context (one fetch, reused across all three sections) ──
    db_forensic = _forensic_load_db(ticker)
    db_vc       = _vc_load_db(ticker)

    # ── 2. Python-computed determinism: op leverage + cyclicality ─────────
    op_leverage_score = compute_operating_leverage_score(
        db_forensic.get("gross_margin_pct") or 0.0,
        db_forensic.get("ebit_margin_pct") or 0.0,
    )

    z_score = 0.0
    is_peak = False
    applies_haircut = False
    avg_3yr = None
    cyc_db_context: Dict[str, Any] = {}
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        calc_input = make_calc_input(ticker)
        # Prefer pre-computed value from calc_node state if present
        cyc_state = state.get("cyclicality") or {}
        if cyc_state.get("z_score") is not None:
            z_score = cyc_state["z_score"]
            is_peak = cyc_state.get("is_peak", False)
            applies_haircut = cyc_state.get("applies_cyclical_haircut", False)
            avg_3yr = cyc_state.get("avg_3yr")
            cyc_db_context = cyc_state.get("db_context", {}) or {}
        else:
            z_score, is_peak, applies_haircut, avg_3yr, cyc_db_context = (
                calculate_z_score(calc_input)
            )
    except Exception as e:
        print(f"  ⚠ Cyclicality fallback (using zeros): {e}")

    # ── 3. Build consolidated DB context string ──────────────────────────
    # Reuse existing formatters — keeps the LLM-facing presentation
    # identical to what the legacy agents emitted.
    db_context_str = _forensic_db_str(db_forensic)
    if db_vc:
        db_context_str += "\n\nVALUE CHAIN INDICATORS:"
        gm_trend = db_vc.get("gross_margin_trend")
        if gm_trend:
            db_context_str += f"\n  Gross Margin 3Y trend: {gm_trend}"
    if cyc_db_context:
        # Overlay the LIVE engine WACC (from phase2_valuation, written by
        # calc_node before Stage 3) so the strategic-context narrative cites the
        # same WACC the DCF uses — not a phantom default. Avoids the flat
        # contradiction where prose said 9% while the engine used ~6%.
        live_wacc = (state.get("phase2_valuation") or {}).get("wacc")
        if isinstance(live_wacc, (int, float)) and live_wacc > 0:
            cyc_db_context = {**cyc_db_context, "wacc": float(live_wacc)}
        db_context_str += "\n\n" + _context_db_str(
            z_score, is_peak, applies_haircut, avg_3yr or 0.0, cyc_db_context,
        )

    # ── 4. 10-K text (one read, full length — replaces 3× ingestion) ──────
    raw_10k = state.get("raw_10k_text", "")
    ten_k_available = bool(raw_10k and "unavailable" not in raw_10k.lower()
                           and "failed" not in raw_10k.lower())
    ten_k_text = (
        raw_10k[:60000] if ten_k_available
        else "10-K text not available — use pipeline data and knowledge."
    )

    # ── 5. Mock fallback if no API key (mirrors legacy agents' behavior) ──
    if not os.environ.get("GOOGLE_API_KEY"):
        return _emit_mock_outputs(ticker, op_leverage_score,
                                  z_score, is_peak, applies_haircut, avg_3yr)

    # ── 6. LLM call with depth-floor enforcement + 1 retry ────────────────
    # The QualitativeSynthesis model_validator enforces minimum char counts
    # on the 7 narrative fields where depth matters. If any field is below
    # its floor, Pydantic raises ValidationError before the model is
    # constructed. We catch that, surface the specific failures back to
    # the LLM, and retry once with stronger guidance. Retrying more than
    # once is rarely useful — if the LLM can't produce depth on the
    # second pass, the prompt is the problem, not the LLM's effort.
    # Temperature pinned to 0 (not the global 0.1) for reproducibility.
    # This agent emits structured numeric scores (moat_score,
    # switching_costs, network_effects, etc.) that downstream consumers
    # — constitution checks, terminal-growth caps, pillar scores — treat
    # as deterministic outputs of stable inputs. Even small stochasticity
    # at temp=0.1 produced visible drift across reruns (moat 7.5 → 7.0,
    # switching_costs 9.0 → 8.0 on identical filings). Other narrative
    # agents retain temp=0.1 where prose variability isn't a problem.
    llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=0.0)
    structured_llm = llm.with_structured_output(QualitativeSynthesis)
    chain = _PROMPT | structured_llm

    invoke_args = {
        "ticker":            ticker,
        "db_context":        db_context_str,
        "op_leverage_score": op_leverage_score,
        "z_score":           z_score,
        "is_peak":           is_peak,
        "applies_haircut":   applies_haircut,
        "ten_k_text":        ten_k_text,
    }

    synthesis: Optional[QualitativeSynthesis] = None
    last_error: Optional[Exception] = None

    def _is_validation_failure(err: Exception) -> bool:
        """LangChain's `with_structured_output` wraps Pydantic
        ValidationError in a generic Exception with a parse-failure
        message that still contains the underlying field-level errors.
        Both true ValidationError instances and these wrapped versions
        should trigger our retry path."""
        if isinstance(err, ValidationError):
            return True
        msg = str(err)
        return (
            "validation error" in msg.lower()
            or "Failed to parse" in msg
            or "value_error" in msg
        )

    def _extract_failures(err: Exception) -> list[str]:
        """Surface specific actionable error lines from either a true
        ValidationError or a langchain-wrapped parse failure."""
        if isinstance(err, ValidationError):
            return [e.get("msg", str(e)) for e in err.errors()]
        # Wrapped parse failure: extract per-field error lines from the
        # message. Lines like "field.path\n  Value error, ..." are what
        # we want to surface back to the LLM.
        msg = str(err)
        out = []
        for line in msg.splitlines():
            stripped = line.strip()
            if stripped.startswith("Value error,") or "validation error" in stripped.lower():
                out.append(stripped)
            elif "must be in" in stripped or "minimum" in stripped:
                out.append(stripped)
        if not out:
            # Fallback: surface a truncated raw message
            out = [msg[:300]]
        return out

    for attempt in (1, 2):
        try:
            synthesis = chain.invoke(invoke_args)
            break  # success
        except Exception as e:
            last_error = e
            if not _is_validation_failure(e):
                print(f"  ✗ Attempt {attempt}: non-retryable LLM error: {e}")
                break

            failures = _extract_failures(e)
            print(f"  ⚠ Attempt {attempt}: validation/depth failure; "
                  f"retrying with augmented guidance")
            for f in failures[:5]:
                print(f"      → {f[:140]}")
            if attempt == 2:
                print(f"  ✗ Second attempt also failed; falling back to mock")
                break
            # Augment the prompt — same body, prepend the specific
            # failures so the LLM addresses them on the second pass.
            retry_chain = (
                ChatPromptTemplate.from_template(
                    "PRIOR ATTEMPT FAILED VALIDATION:\n{depth_errors}\n\n"
                    "Re-do the analysis fixing the issues above. For "
                    "depth-floor failures, produce substantially more "
                    "prose. For range failures, stay within the documented "
                    "bounds (or omit the field).\n\n"
                    "═══════════════════════════════════════════════════\n"
                    + _PROMPT_BODY
                ) | structured_llm
            )
            invoke_args = dict(invoke_args)
            invoke_args["depth_errors"] = "\n".join(
                f"  - {f}" for f in failures
            )
            chain = retry_chain

    if synthesis is None:
        return _emit_mock_outputs(
            ticker, op_leverage_score,
            z_score, is_peak, applies_haircut, avg_3yr,
            error=str(last_error) if last_error else "unknown error",
        )

    # ── 7. Architecture invariants — overwrite Python-computed fields ─────
    forensic_report = synthesis.forensic.dict()
    forensic_report["operating_leverage_score"] = op_leverage_score   # Python value

    # StrategicContextReport's Python-computed fields are written last
    # so the LLM cannot drift them.
    sc_locked = synthesis.strategic_context.model_copy(update={
        "revenue_z_score":          z_score,
        "is_cyclical_peak":         is_peak,
        "applies_cyclical_haircut": applies_haircut,
        "recommended_base_revenue": avg_3yr if applies_haircut else None,
    })

    # ── 8. Cap scenarios at 1 per section (prompt asks; defensive cap) ────
    forensic_scenarios = list(synthesis.forensic.scenarios)[:1]
    vc_scenarios       = list(synthesis.value_chain.scenarios)[:1]
    ctx_scenarios      = list(sc_locked.scenarios)[:1]

    forensic_report["scenarios"]  = [s.dict() for s in forensic_scenarios]
    vc_report                     = synthesis.value_chain.dict()
    vc_report["scenarios"]        = [s.dict() for s in vc_scenarios]
    sc_report                     = sc_locked.dict()
    sc_report["scenarios"]        = [s.dict() for s in ctx_scenarios]

    total_scenarios = len(forensic_scenarios) + len(vc_scenarios) + len(ctx_scenarios)

    output = {
        # Three legacy keys — downstream consumers read these unchanged
        "forensic_report":           forensic_report,
        "value_chain_report":        vc_report,
        "strategic_context_report":  sc_report,
        "messages": [HumanMessage(
            content=(
                f"QualitativeSynthesis: moat={synthesis.forensic.moat_score}/10, "
                f"opLev={op_leverage_score}/10 (Py), "
                f"upstream_leak={synthesis.value_chain.upstream_value_leak}, "
                f"z={z_score:+.2f} peak={is_peak}, "
                f"scenarios={total_scenarios}"
            )
        )],
    }
    tracer.log_step("QualitativeSynthesis", state, output)
    return output


def _emit_mock_outputs(
    ticker: str,
    op_leverage_score: float,
    z_score: float,
    is_peak: bool,
    applies_haircut: bool,
    avg_3yr: Optional[float],
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """No-API-key / failure fallback. Emits empty-but-valid topical
    payloads so lead.py's required-keys check passes."""
    forensic_report = {
        "operating_leverage_score":    op_leverage_score,
        "operating_leverage_analysis": "Mock — no API key or LLM failure",
        "moat_score":                  5.0,
        "moat_attributes":             {"switching_costs": 5, "network_effects": 5,
                                        "cost_advantage": 5, "intangibles": 5},
        "moat_evidence":               "Mock evidence",
        "has_pricing_power":           False,
        "pricing_power_evidence":      "None.",
        "concentration_risk":          False,
        "concentration_details":       "",
        "business_description":        "",
        "revenue_segments":            [],
        "key_customers":               [],
        "competitive_landscape":       "",
        "regulatory_risk":             "",
        "scenarios":                   [],
    }
    vc_report = {
        "strategic_position":          "uncertain",
        "upstream_power":              "uncertain",
        "substitution_pressure":       "uncertain",
        "strategic_leverage_score":    5.0,
        "power_ratio":                 1.0,
        "upstream_value_leak":         False,
        "bottleneck_analysis":         "Mock",
        "substitution_risk_score":     5.0,
        "top_substitutes":             "",
        "pricing_power_assessment":    "",
        "pass_through_capability":     False,
        "analysis_summary":            "",
        "scenarios":                   [],
    }
    sc_report = {
        "cyclicality_classification":  "ambiguous",
        "growth_quality":              "uncertain",
        "intangible_decay_severity":   "uncertain",
        "revenue_z_score":             z_score,
        "is_cyclical_peak":            is_peak,
        "applies_cyclical_haircut":    applies_haircut,
        "recommended_base_revenue":    avg_3yr if applies_haircut else None,
        "deferred_revenue_trend":      "Mock",
        "quality_of_growth_risk":      False,
        "intangible_risk_assessment":  "Mock",
        "revenue_at_risk_percent":     0.0,
        "terminal_haircut":            False,
        "summary":                     "",
        "scenarios":                   [],
    }
    msg = f"QualitativeSynthesis (mock): {ticker}"
    if error:
        msg += f" — error: {error}"
    return {
        "forensic_report":           forensic_report,
        "value_chain_report":        vc_report,
        "strategic_context_report":  sc_report,
        "messages": [HumanMessage(content=msg)],
    }
