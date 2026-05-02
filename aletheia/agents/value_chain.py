"""
aletheia/agents/value_chain.py

Data sources (in priority order):
  1. DuckDB — gross_margin, ebit_margin, roic, fcf_margin, sbc_pct
  2. state["raw_10k_text"] — supplier/customer concentration from Item 1A
  3. Web search (1 query) — supplier gross margins (genuinely unavailable in DB)

Rule: LLM receives structured facts from DuckDB.
      recommended_terminal_growth_adj REMOVED — valuation_node computes from
      strategic_leverage_score via deterministic lookup table.
"""

import os
import numpy as np
import json
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from typing import Dict, List, Literal, Optional
from config import MODEL_NAME, TEMPERATURE
from aletheia.utils.tracing import tracer
from aletheia.tools.search import search_sentiment


# ─────────────────────────────────────────────────────────────────────────────
# Schema — no recommended_terminal_growth_adj (computed by Python rule)
# ─────────────────────────────────────────────────────────────────────────────

class ValueChainReport(BaseModel):
    """
    Value-chain narrative output.

    ARCHITECTURE: pure-narrative agent. The numeric `strategic_leverage_score`
    (1-10) is a LLM-generated qualitative SIGNAL for narrative color only — it
    is NOT consumed by ConvictionScorer or any calc-layer tool. Conviction P5
    leadership pillar reads the deterministic operating-leverage score from
    calc_node state instead.

    The frozen Pydantic config + the test_no_agent_emitted_overrides lock test
    prevent the addition of fields that mutate calc inputs.
    """
    model_config = {"frozen": True}

    # ── Typed categorical assessments (preferred for downstream consumption) ──
    strategic_position: Literal["dominant", "strong", "moderate", "weak", "uncertain"] = "uncertain"
    upstream_power: Literal["high_supplier_power", "balanced", "high_buyer_power", "uncertain"] = "uncertain"
    substitution_pressure: Literal["high", "medium", "low", "uncertain"] = "uncertain"

    strategic_leverage_score: float = Field(
        description="Score 1-10 Porter Five Forces. "
                    "10=Dominant platform. 1=Commodity price taker. "
                    "Narrative color only — does NOT feed conviction or calc.")

    # Upstream
    power_ratio: float = Field(
        description="ESTIMATE: Supplier Gross Margin / Target Gross Margin. "
                    ">1.5 = upstream value leak. LLM estimate — supplier margins "
                    "not in DuckDB.")
    upstream_value_leak: bool = Field(
        description="True if power_ratio > 1.5 or critical supplier bottleneck.")
    bottleneck_analysis: str = Field(
        description="Who is the critical supplier, what leverage do they have, "
                    "what happens if they raise prices?")

    # Substitution
    substitution_risk_score: float = Field(
        description="1-10. 10=easily substituted. 1=deeply locked-in.")
    top_substitutes: str = Field(
        description="Top 3 functional substitutes and realistic switching scenario.")

    # Downstream
    pricing_power_assessment: str = Field(
        description="Evidence of price leadership or price-taking. "
                    "Specific contract terms, price hike history.")
    pass_through_capability: bool = Field(
        description="True if company can raise prices 10% without material churn.")

    analysis_summary: str = Field(
        description="Porter Five Forces executive summary: supplier power, "
                    "buyer power, substitution threat, rivalry, overall position.")


# ─────────────────────────────────────────────────────────────────────────────
# Data loading from DuckDB
# ─────────────────────────────────────────────────────────────────────────────

def load_db_context(ticker: str) -> dict:
    """Load quantitative context from DuckDB for value chain analysis."""
    try:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        df = db.get_latest(ticker)
        db.close()
        if df.empty:
            return {}

        row = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0]

        def _g(col):
            v = row.get(col)
            return float(v) if v is not None and not (
                isinstance(v, float) and np.isnan(v)) else None

        # Multi-year gross margin trend
        gm_series = df.sort_values("fiscal_year")["derived_GrossMargin_Pct"].dropna()
        gm_trend = None
        if len(gm_series) >= 3:
            delta = float(gm_series.iloc[-1]) - float(gm_series.iloc[-3])
            gm_trend = "expanding" if delta > 0.01 else (
                "contracting" if delta < -0.01 else "stable")

        return {
            "gross_margin_pct":  _g("derived_GrossMargin_Pct"),
            "ebit_margin_pct":   _g("derived_EBIT_Margin_Pct"),
            "fcf_margin_pct":    _g("derived_FCF_Margin_Pct"),
            "roic":              _g("derived_ROIC"),
            "sbc_pct_fcf":       _g("clean_SBC_PctFCF"),
            "revenue":           _g("clean_Revenue"),
            "net_debt":          _g("derived_NetDebt"),
            "data_quality":      _g("overall_quality_score"),
            "fiscal_year":       int(df["fiscal_year"].max()),
            "gross_margin_trend": gm_trend,
        }
    except Exception as e:
        print(f"  ✗ DB load failed in value_chain: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────

def value_chain_agent(state):
    """
    Value Chain Strategist — Porter's Five Forces.

    Data sources:
      1. DuckDB — margins, ROIC, FCF (quantitative foundation)
      2. raw_10k_text — Item 1A names suppliers and customer concentration
      3. Web search — 1 query for supplier margins (not in any DB)

    Rule compliance:
      - recommended_terminal_growth_adj REMOVED from schema
      - strategic_leverage_score is LLM judgment (1-10 score)
      - valuation_node converts score to terminal growth adj via lookup table
      - power_ratio is LLM estimate (supplier margins unavoidable)
    """
    print("---VALUE CHAIN STRATEGIST---")
    ticker = state["ticker"]

    # ── 1. Load from DuckDB ───────────────────────────────────────────────────
    db = load_db_context(ticker)

    def fmt(v, pct=False, bn=False, pre_pct=False):
        if v is None:
            return "N/A"
        if pre_pct:
            return f"{v:.1f}%"
        if pct:
            return f"{v:.1%}"
        if bn:
            return f"${v/1e9:.1f}B"
        return str(v)

    db_context = f"""
FINANCIAL FACTS FROM INTAKE PIPELINE (FY{db.get('fiscal_year', '?')}):
  Revenue:           {fmt(db.get('revenue'), bn=True)}
  Gross Margin:      {fmt(db.get('gross_margin_pct'), pre_pct=True)} ({db.get('gross_margin_trend', 'N/A')} 3Y trend)
  EBIT Margin:       {fmt(db.get('ebit_margin_pct'), pre_pct=True)}
  FCF Margin:        {fmt(db.get('fcf_margin_pct'), pre_pct=True)}
  ROIC:              {fmt(db.get('roic'), pct=True)}
  SBC % of FCF:      {fmt(db.get('sbc_pct_fcf'), pre_pct=True)}
  Net Debt:          {fmt(db.get('net_debt'), bn=True)}
"""

    # ── 2. Primary source: 10-K text ─────────────────────────────────────────
    raw_10k = state.get("raw_10k_text", "")
    ten_k_available = bool(raw_10k and "unavailable" not in raw_10k.lower()
                           and "failed" not in raw_10k.lower())

    # ── 3. Supplementary: supplier margin data (genuinely not in DB) ──────────
    supplier_context = ""
    try:
        supplier_context = search_sentiment(
            f"{ticker} key supplier gross margin upstream bottleneck"
        )
    except Exception:
        pass

    # ── Mock ──────────────────────────────────────────────────────────────────
    if not os.environ.get("GOOGLE_API_KEY"):
        result = ValueChainReport(
            strategic_leverage_score=5.0,
            power_ratio=1.0,
            upstream_value_leak=False,
            bottleneck_analysis="Mock: balanced supply chain.",
            substitution_risk_score=5.0,
            top_substitutes="Mock substitutes A, B, C.",
            pricing_power_assessment="Mock: moderate pricing power.",
            pass_through_capability=True,
            analysis_summary="Mock: neutral value chain.",
        )
        return {
            "value_chain_report": result.dict(),
            "messages": [HumanMessage(content="Value Chain: Mock complete.")]
        }

    llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=TEMPERATURE)
    structured_llm = llm.with_structured_output(ValueChainReport)

    prompt = ChatPromptTemplate.from_template("""
You are the Value Chain Strategist for {ticker}.

══════════════════════════════════════════════════════════════
PRIMARY DATA — INTAKE PIPELINE (verified facts)
══════════════════════════════════════════════════════════════
{db_context}

══════════════════════════════════════════════════════════════
PRIMARY SOURCE — 10-K FILING TEXT (Item 1 Business, Item 1A Risk Factors)
══════════════════════════════════════════════════════════════
{ten_k_text}

══════════════════════════════════════════════════════════════
SUPPLEMENTARY — SUPPLIER DATA (web search)
══════════════════════════════════════════════════════════════
{supplier_context}

══════════════════════════════════════════════════════════════
YOUR TASKS
══════════════════════════════════════════════════════════════

1. UPSTREAM POWER
   Identify the critical supplier bottleneck (named in 10-K Item 1A).
   power_ratio = (estimated supplier gross margin) / (target gross margin above).
   Use pipeline GROSS MARGIN as the denominator — it is verified.
   Estimate supplier margin from search data or industry knowledge.
   upstream_value_leak = True if power_ratio > 1.5.

2. SUBSTITUTION THREAT
   Top 3 functional substitutes. Score 1-10 where 10=easily replaced.
   Evidence from 10-K competitive section.

3. BUYER POWER & PRICING
   Can this company raise prices without losing customers?
   Evidence: contract terms, NRR > 100%, price hike history.
   pass_through_capability = True/False.

4. STRATEGIC LEVERAGE SCORE (1-10 overall)
   This score feeds a Python lookup table:
     8-10 → +0.5% terminal growth premium
     6-7  → no adjustment
     4-5  → -0.5% reduction
     1-3  → -1.0% reduction
   Score carefully — it has real valuation impact.

5. ANALYSIS SUMMARY
   One paragraph synthesising the overall value chain position.

DO NOT return recommended_terminal_growth_adj — that float is computed
by Python in valuation_node from your strategic_leverage_score.

Return ValueChainReport JSON.
""")

    chain = prompt | structured_llm

    try:
        report: ValueChainReport = chain.invoke({
            "ticker":           ticker,
            "db_context":       db_context,
            "ten_k_text":       raw_10k[:50000] if ten_k_available else
                                "10-K text not available — use pipeline data and knowledge.",
            "supplier_context": supplier_context or "Search unavailable.",
        })

        output = {
            "value_chain_report": report.dict(),
            "messages": [HumanMessage(
                content=f"Value Chain: Leverage {report.strategic_leverage_score}/10. "
                        f"Upstream leak: {report.upstream_value_leak}. "
                        f"Pass-through: {report.pass_through_capability}. "
                        f"Sub risk: {report.substitution_risk_score}/10."
            )]
        }
        tracer.log_step("ValueChain", state, output)
        return output

    except Exception as e:
        print(f"  ✗ Value Chain error: {e}")
        return {"messages": [HumanMessage(content=f"Value Chain: Error {e}")]}
