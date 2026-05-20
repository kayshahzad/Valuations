"""
aletheia/agents/contrarian_v2.py

Contrarian Agent — adversarial bear-case synthesis from structured inputs.

Inputs:
  1. phase2_valuation — reverse DCF signal, multiple decomposition,
                         bear scenario IPS (deterministic math)
  2. scenario_results — agent-proposed scenarios with named overrides
                         and DCF outputs (each scenario carries its own
                         IPS / upside)
  3. (removed in week-1.5 A/B) — DuckDuckGo web search

Web search was removed after the controlled A/B (`scripts/contrarian_ab.py`,
`scripts/compare_contrarian_ab.py`) on 9 tickers showed:
  - DuckDuckGo dependency was architecture-broken on the test machine,
    so every `with-search` run was effectively `no-search` + a 735-char
    error string the LLM ignored
  - Bias-category match across with/without was 6/9 with the 3 mismatches
    being label variants of the same diagnosis
  - Sentiment scores matched 8/9 (1 ticker within 2 points)
  - Recency markers (Q3 2024 / "recently" / "earnings call") appeared
    ZERO times in either version's bear case — the LLM never used live
    search content to anchor recency claims even when the search succeeded
  - The contrarian's adversarial value comes from the structured
    quant_challenge (reverse-DCF math + multiple decomposition) and the
    scenario_results IPSs, not from web text

The ContrarianOutput schema is UNCHANGED so lead_agent reads it identically.
"""

import os
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from config import MODEL_NAME, TEMPERATURE
from aletheia.utils.tracing import tracer


class ContrarianOutput(BaseModel):
    bias_detected: str = Field(
        description="Type of market bias (e.g., 'Herding', 'Overconfidence', "
                    "'Narrative Fallacy', 'Growth Extrapolation')"
    )
    bear_case_summary: str = Field(
        description="Detailed summary of the negative sentiment, risks, and "
                    "mathematical challenges to the bull case assumptions"
    )
    sentiment_score: int = Field(
        description="Score from -10 (Extremely Negative) to +10 (Extremely Positive)"
    )


def contrarian_agent(state: dict) -> dict:
    """
    Enhanced Contrarian Agent — uses Phase 2 reverse DCF as adversarial input.
    Challenges the growth assumption embedded in the market price mathematically,
    then supplements with live web search for qualitative bear case evidence.
    """
    print("---CONTRARIAN AGENT (Phase 3 Enhanced)---")

    ticker = state["ticker"]

    # ── Pull Phase 2 valuation context ───────────────────────────────────────
    phase2 = state.get("phase2_valuation", {})
    implied_cagr = phase2.get("implied_cagr")
    historical_cagr = phase2.get("historical_cagr")
    reverse_dcf_signal = phase2.get("reverse_dcf_signal", "unknown")
    reverse_dcf_reasons = phase2.get("reverse_dcf_reasons", [])
    ev_ebitda_market = phase2.get("ev_ebitda_market", 0)
    ev_ebitda_justified = phase2.get("ev_ebitda_justified", 0)
    ev_ebitda_premium_pct = phase2.get("ev_ebitda_premium_pct", 0)
    multiple_signal = phase2.get("multiple_signal", "unknown")
    bear_iv = phase2.get("intrinsic_per_share", {}).get("bear", 0)
    base_iv = phase2.get("intrinsic_per_share", {}).get("base", 0)
    current_price = phase2.get("dcf", {}).get("current_price", 0)

    # Build the quantitative challenge context
    quant_challenge = ""
    if implied_cagr is not None and historical_cagr is not None:
        ratio = implied_cagr / historical_cagr if historical_cagr > 0 else 0
        quant_challenge = f"""
QUANTITATIVE ADVERSARIAL CHALLENGE (Phase 2 Reverse DCF):
  The market price of ${current_price:,.2f} mathematically requires:
  • Implied 10-year revenue CAGR: {implied_cagr:.1%}
  • Company's robust historical CAGR: {historical_cagr:.1%}
  • Ratio (implied / historical): {ratio:.1f}x
  • Reverse DCF signal: {reverse_dcf_signal.upper()}

  Reverse DCF assessment:
  {chr(10).join(f'  - {r}' for r in reverse_dcf_reasons)}

MULTIPLE DECOMPOSITION CHALLENGE (Liberti Formula):
  • Market EV/EBITDA: {ev_ebitda_market:.1f}x
  • Mathematically justified EV/EBITDA (given ROIC, WACC, growth): {ev_ebitda_justified:.1f}x
  • Premium to justified multiple: {ev_ebitda_premium_pct:+.0%}
  • Multiple signal: {multiple_signal.upper()}

BEAR CASE INTRINSIC VALUE:
  • Bear case DCF: ${bear_iv:,.0f}/share vs current price ${current_price:,.2f}
  • Downside in bear case: {(((bear_iv - current_price) / current_price) if current_price else 0.0):+.1%}
"""

    # ── Web search REMOVED in week-1.5 A/B (see module docstring) ────────────
    # The contrarian's adversarial bite comes from quant_challenge +
    # scenario_results, not from web text. The `raw_results` field is
    # retained on the output dict for backward compatibility with any
    # consumer that still reads it; populated with a stable explanatory
    # marker rather than a search response.
    raw_web_results = "(web search removed; bear case derives from structured inputs)"

    # ── LLM Analysis ──────────────────────────────────────────────────────────
    if not os.environ.get("GOOGLE_API_KEY"):
        return {
            "contrarian_report": {
                "raw_results": raw_web_results,
                "quant_challenge": quant_challenge,
                "structured_analysis": {
                    "bias_detected": (
                        f"Growth Extrapolation — market prices {implied_cagr:.1%} CAGR"
                        f" vs {historical_cagr:.1%} historical"
                        if implied_cagr else "Mock Bias (No API Key)"
                    ),
                    "bear_case_summary": (
                        f"Quantitative: Market requires {implied_cagr:.1%} CAGR "
                        f"({(implied_cagr/historical_cagr):.1f}x historical) to justify price. "
                        f"Multiple at {ev_ebitda_premium_pct:+.0%} premium to justified. "
                        f"Bear DCF: ${bear_iv:,.0f}/share."
                        if implied_cagr else "Mock summary."
                    ),
                    "sentiment_score": (
                        -5 if reverse_dcf_signal in ("flag", "caution")
                        else -2 if reverse_dcf_signal == "priced_for_growth"
                        else 0
                    )
                }
            },
            "messages": [HumanMessage(content=(
                f"Contrarian (Phase 3): Quantitative challenge — "
                f"market prices {implied_cagr:.1%} CAGR vs "
                f"{historical_cagr:.1%} historical [{reverse_dcf_signal}]"
                if implied_cagr else
                "Contrarian: Mock analysis (no API key)."
            ))]
        }

    llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=TEMPERATURE)
    structured_llm = llm.with_structured_output(ContrarianOutput)

    prompt = ChatPromptTemplate.from_template("""
You are The Contrarian — the adversarial voice of the investment committee.
Your goal is to construct the strongest possible bear case for {ticker}.

You have TWO sources of evidence:

1. MATHEMATICAL CHALLENGES (from Reverse DCF and Multiple Decomposition):
{quant_challenge}

2. AGENT-PROPOSED ALTERNATE SCENARIOS (already evaluated by DCFEngine —
   these are typed, bounded hypotheses with concrete IPS implications):
{scenario_summary}

YOUR TASKS:
1. **Bias Detection**: What cognitive bias is driving the current valuation?
   - If implied CAGR >> historical CAGR: "Growth Extrapolation Bias"
   - If multiple premium >> 100%: "Narrative Premium / FOMO"
   - If high cyclical Z-score driving the multiple: "Anchoring on Peak Earnings"
   - If structurally weak ROIC trend with growth premium: "Hope-Driven Mean Reversion"

2. **Bear Case Synthesis**: Combine the mathematical and scenario-based
   challenges:
   - Start with the quantitative impossibility from Reverse DCF
   - REFERENCE ANY BEAR/BASE_ALTERNATIVE SCENARIOS BY NAME and cite the
     resulting IPS — e.g. "In the 'AI capex peak' scenario proposed by
     forensic_agent, IV drops to $X (-Y% vs current price)"
   - What is the most likely path to the bear case intrinsic value?

3. **Sentiment Score**: Rate -10 to +10 based on ALL evidence combined,
   including the worst-case agent scenario IPS.

CRITICAL: Be specific and mathematical. Cite numbers from scenarios where
they exist. "The 'Patent cliff drag' scenario (proposed_by=context) lowers
IPS to $X" is a stronger challenge than vague concern.

Return structured JSON.
""")

    chain = prompt | structured_llm

    # Build a compact scenario summary string for the prompt. We want the
    # LLM to be able to reference scenarios by name with their IPS impact.
    scenarios = state.get("scenario_results") or []
    if scenarios:
        scenario_lines = []
        for s in scenarios:
            ips = s.get("intrinsic_per_share_base")
            ups = s.get("upside_pct_base")
            ips_str = f"IPS=${ips:,.2f}" if ips is not None else "IPS=N/A"
            ups_str = f"upside={ups:+.1f}%" if ups is not None else "upside=N/A"
            err = s.get("error")
            line = (
                f"  - '{s['name']}' ({s['scenario_type']}, by {s['proposed_by']}): "
                f"{ips_str} {ups_str}. Rationale: {s['rationale']}"
            )
            if err:
                line += f" [eval error: {err}]"
            scenario_lines.append(line)
        scenario_summary = "\n".join(scenario_lines)
    else:
        scenario_summary = "(no agent-proposed scenarios)"

    try:
        report: ContrarianOutput = chain.invoke({
            "ticker": ticker,
            "quant_challenge": quant_challenge if quant_challenge else "Phase 2 data unavailable.",
            "scenario_summary": scenario_summary,
        })

        output = {
            "contrarian_report": {
                "raw_results": raw_web_results,
                "quant_challenge": quant_challenge,
                "structured_analysis": report.dict()
            },
            "messages": [HumanMessage(content=(
                f"Contrarian (Phase 3): [{report.bias_detected}] "
                f"Sentiment: {report.sentiment_score}/10. "
                f"Implied CAGR {implied_cagr:.1%} vs {historical_cagr:.1%} historical."
                if implied_cagr else
                f"Contrarian: {report.bias_detected}. Score: {report.sentiment_score}/10."
            ))]
        }
        tracer.log_step("ContrarianV2", state, output)
        return output

    except Exception as e:
        return {
            "contrarian_report": {
                "raw_results": raw_web_results,
                "quant_challenge": quant_challenge,
                "error": str(e)
            },
            "messages": [HumanMessage(content=f"Contrarian: Failed. Error: {e}")]
        }
