"""
aletheia/agents/contrarian_v2.py

Phase 3 — Enhanced Contrarian Agent
=====================================
Drop-in replacement for contrarian.py that adds the Phase 2 reverse DCF
signal as a structured adversarial input.

The key upgrade: instead of only searching for bear case news, the contrarian
now has the mathematical implied CAGR from the reverse DCF. It challenges:
  1. Whether the implied CAGR is realistic given historical growth
  2. Whether the multiple premium is justified
  3. Whether the bear case DCF scenario is too optimistic

The ContrarianOutput schema is UNCHANGED so lead_agent reads it identically.
The only change is richer context passed to the LLM.

To use: rename this file to contrarian.py and it replaces the existing one.
Or update graph.py to import from contrarian_v2.
"""

import os
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from langchain_community.tools import DuckDuckGoSearchRun
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

    # ── Web search for qualitative bear case ─────────────────────────────────
    search = DuckDuckGoSearchRun()
    query = f"{ticker} stock bear case risks negative analysis concerns"
    try:
        raw_web_results = search.invoke(query)
    except Exception as e:
        raw_web_results = f"Search failed: {e}"

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

You have THREE sources of evidence:

1. MATHEMATICAL CHALLENGES (from Reverse DCF and Multiple Decomposition):
{quant_challenge}

2. QUALITATIVE BEAR CASE (from live search):
{web_results}

3. AGENT-PROPOSED ALTERNATE SCENARIOS (already evaluated by DCFEngine —
   these are typed, bounded hypotheses with concrete IPS implications):
{scenario_summary}

YOUR TASKS:
1. **Bias Detection**: What cognitive bias is driving the current valuation?
   - If implied CAGR >> historical CAGR: "Growth Extrapolation Bias"
   - If multiple premium >> 100%: "Narrative Premium / FOMO"
   - If web results show consensus optimism: "Herding Bias"

2. **Bear Case Synthesis**: Combine the mathematical, qualitative, and
   scenario-based challenges:
   - Start with the quantitative impossibility from Reverse DCF
   - Layer in the qualitative risks from web search
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
            "web_results": raw_web_results,
            "implied_cagr": f"{implied_cagr:.1%}" if implied_cagr else "N/A",
            "historical_cagr": f"{historical_cagr:.1%}" if historical_cagr else "N/A",
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
