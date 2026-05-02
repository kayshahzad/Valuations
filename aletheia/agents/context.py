"""
aletheia/agents/context.py

Data sources (in priority order):
  1. DuckDB — multi-year clean_Revenue series for z-score (replaces yfinance)
              deferred revenue from clean_json/domain_score_D8
              intangible amortization from raw_json
  2. state["raw_10k_text"] — patent expiry dates from Item 1 Business section
  3. Web search (1 query) — patent litigation outcomes (live, not in any DB)

KEY FIX: z-score now uses DuckDB clean_Revenue multi-year series instead of
yfinance income statement JSON. get_latest() returns all fiscal years ordered
by year — exactly what z-score calculation needs.

Rule: all numeric calculations in Python. LLM writes narrative/booleans only.
"""

import os
import json
import numpy as np
import pandas as pd
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from typing import Dict, List, Literal, Optional
from config import MODEL_NAME, TEMPERATURE
from aletheia.utils.tracing import tracer
from aletheia.tools.search import search_sentiment


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

class StrategicContextReport(BaseModel):
    """
    Strategic context narrative output.

    ARCHITECTURE: pure-narrative agent. The numeric fields below
    (revenue_z_score, recommended_base_revenue, revenue_at_risk_percent) are
    deterministic values pulled from calc_node state — the LLM does NOT
    compute them. Conviction reads cyclicality from calc_node directly,
    not from this report.

    The frozen Pydantic config + the test_no_agent_emitted_overrides lock test
    prevent the addition of fields that mutate calc inputs.
    """
    model_config = {"frozen": True}

    # ── Typed categorical assessments ──────────────────────────────────────
    cyclicality_classification: Literal["cyclical", "non_cyclical", "ambiguous"] = "ambiguous"
    growth_quality: Literal["high", "medium", "low", "uncertain"] = "uncertain"
    intangible_decay_severity: Literal["high", "medium", "low", "none", "uncertain"] = "uncertain"

    # ── Deterministic values (sourced from calc_node, NOT LLM-generated) ────
    revenue_z_score: float = Field(
        description="Z-Score of latest revenue vs historical mean. "
                    "Sourced from calc_node state, NOT LLM-generated.")
    is_cyclical_peak: bool = Field(
        description="True if Z-Score > 2.0. Sourced from calc_node state.")
    applies_cyclical_haircut: bool = Field(
        default=False,
        description="True if Z-Score > sector threshold. Sourced from calc_node state.")
    recommended_base_revenue: Optional[float] = Field(
        description="3-year average revenue if peak. Sourced from calc_node state.")

    # LLM narrative
    deferred_revenue_trend: str = Field(
        description="Analysis of deferred revenue trend vs revenue growth. "
                    "Use D8_Revenue domain score and DeferredRevenue_Growth from pipeline data.")
    quality_of_growth_risk: bool = Field(
        description="True if deferred revenue declining relative to revenue — "
                    "signals pull-forward risk. Use D8 domain score as primary signal.")

    # Intangible decay — LLM estimate from 10-K
    intangible_risk_assessment: str = Field(
        description="Assessment of patent/contract expirations from 10-K. "
                    "Cite specific products, dates, revenue exposure if disclosed.")
    revenue_at_risk_percent: float = Field(
        description="ESTIMATE: % of revenue tied to assets expiring within 5 years. "
                    "Use 0.0 if no material risk. Flag uncertainty explicitly.")
    terminal_haircut: bool = Field(
        description="True if revenue_at_risk_percent > 20%.")

    summary: str = Field(
        description="One paragraph: cyclicality status, growth quality, intangible risk.")


# ─────────────────────────────────────────────────────────────────────────────
# Python calculations from DuckDB — replaces yfinance statements
# ─────────────────────────────────────────────────────────────────────────────

from aletheia.tools.cyclicality import calculate_z_score


def build_db_context_str(z_score, is_peak, applies_cyclical_haircut, avg_3yr, db: dict) -> str:
    """Format DuckDB data for LLM prompt."""

    def fmt(v, pct=False, bn=False, pre_pct=False):
        if v is None:
            return "N/A"
        if pre_pct:
            return f"{v:.1f}%"
        if pct:
            return f"{v:.1%}"
        if bn and abs(v) > 1e8:
            return f"${v/1e9:.1f}B"
        return f"{v:,.0f}" if isinstance(v, float) else str(v)

    fy_list = db.get("fiscal_years", [])
    rev_list = db.get("revenues", [])

    rev_history = ""
    if fy_list and rev_list:
        pairs = [f"FY{fy}: ${rev/1e9:.1f}B"
                 for fy, rev in zip(fy_list[-5:], rev_list[-5:])]
        rev_history = " | ".join(pairs)

    d8 = db.get("d8_revenue_score")
    d8_str = f"{d8:.2f}" if d8 is not None else "N/A"
    d8_interp = ("(clean — deferred revenue healthy)" if d8 and d8 >= 0.9
                 else "(flag — deferred revenue quality issue)" if d8 and d8 < 0.7
                 else "")

    return f"""
QUANTITATIVE DATA FROM INTAKE PIPELINE:
  Revenue History (5Y):  {rev_history}
  Latest Revenue:        {fmt(db.get('latest_revenue'), bn=True)}
  3Y Average Revenue:    {fmt(avg_3yr, bn=True)}
  3Y Revenue CAGR:       {fmt(db.get('revenue_cagr_3y'), pct=True)}
  ROIC:                  {fmt(db.get('roic'), pct=True)}
  WACC:                  {fmt(db.get('wacc'), pct=True)}
  Revenue Z-Score:       {z_score:.2f}
  Is Cyclical Peak:      {is_peak} (Z > 2.0)
  Applies Haircut:       {applies_cyclical_haircut} (Industry-adjusted)
  Gross Margin:          {fmt(db.get('gross_margin_pct'), pre_pct=True)}
  FCF Margin:            {fmt(db.get('fcf_margin_pct'), pre_pct=True)}
  Reinvestment Rate:     {fmt(db.get('reinvestment_rate'), pct=True)}
  Deferred Revenue:      {fmt(db.get('deferred_revenue'), bn=True)}
  Deferred Rev Growth:   {fmt(db.get('deferred_rev_growth'), pct=True)}
  D8 Revenue Score:      {d8_str} {d8_interp}
  Intangible Amort:      {fmt(db.get('intangible_amort'), bn=True)}
  Data Quality:          {fmt(db.get('data_quality'))}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────

def strategic_context_agent(state):
    """
    Strategic Context Agent.

    Data sources:
      1. DuckDB clean_Revenue series — replaces yfinance for z-score
      2. DuckDB clean_json — deferred revenue and growth rate
      3. DuckDB domain_score_D8_Revenue — revenue quality already computed
      4. DuckDB raw_json — intangible amortization
      5. raw_10k_text — patent expiry dates (specific dates not in XBRL)
      6. Web search (1 query) — patent litigation live outcomes

    Rule compliance:
      - z_score, is_cyclical_peak, recommended_base_revenue: Python (DuckDB)
      - quality_of_growth_risk: guided by D8 domain score
      - revenue_at_risk_percent: LLM estimate from 10-K (unavoidable)
      - After LLM call: Python values overwrite LLM values for computed fields
    """
    print("---STRATEGIC CONTEXT AGENT---")
    ticker = state["ticker"]

    # ── 1. Python calculations from DuckDB ────────────────────────────────────
    from aletheia.data.database import InvestmentDatabase
    # Prefer cyclicality results that calc_node already computed and put in
    # state — agents READ from calc state per the architecture invariant.
    cyc = state.get("cyclicality") or {}
    if cyc and cyc.get("z_score") is not None:
        z_score = cyc["z_score"]
        is_peak = bool(cyc.get("is_peak"))
        applies_cyclical_haircut = bool(cyc.get("applies_cyclical_haircut"))
        avg_3yr = cyc.get("avg_3yr") or 0.0
        db = cyc.get("db_context") or {}
    else:
        # Fallback: agent invoked outside the graph (legacy run_valuation.py
        # or test). Build CalculationInput via the shared helper.
        from aletheia.utils.calc_input_builder import make_calc_input
        calc_input = make_calc_input(ticker)
        z_score, is_peak, applies_cyclical_haircut, avg_3yr, db = calculate_z_score(calc_input)
    print(f"  ✓ Z-score (DuckDB): {z_score:.2f} | peak={is_peak} | "
          f"3yr_avg={'${:.1f}B'.format(avg_3yr/1e9) if avg_3yr else 'N/A'}")

    db_context_str = build_db_context_str(z_score, is_peak, applies_cyclical_haircut, avg_3yr, db)

    # ── 2. Primary source: 10-K text for patent analysis ─────────────────────
    raw_10k = state.get("raw_10k_text", "")
    ten_k_available = bool(raw_10k and "unavailable" not in raw_10k.lower()
                           and "failed" not in raw_10k.lower())

    # ── 3. Supplementary: patent litigation (genuinely live data) ────────────
    patent_context = ""
    try:
        patent_context = search_sentiment(
            f"{ticker} patent expiration loss of exclusivity risk 10-k"
        )
    except Exception:
        pass

    # ── Mock ──────────────────────────────────────────────────────────────────
    if not os.environ.get("GOOGLE_API_KEY"):
        result = {
            "revenue_z_score":           z_score,
            "is_cyclical_peak":          is_peak,
            "applies_cyclical_haircut":  applies_cyclical_haircut,
            "recommended_base_revenue":  avg_3yr if applies_cyclical_haircut else None,
            "deferred_revenue_trend":    "Mock: stable.",
            "quality_of_growth_risk":    False,
            "intangible_risk_assessment": "Mock: no major patent cliffs.",
            "revenue_at_risk_percent":   0.0,
            "terminal_haircut":          False,
            "summary":                   "Mock: stable cyclicality, no intangible risk.",
        }
        return {
            "strategic_context_report": result,
            "messages": [HumanMessage(content="Context: Mock complete.")]
        }

    llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=TEMPERATURE)
    structured_llm = llm.with_structured_output(StrategicContextReport)

    prompt = ChatPromptTemplate.from_template("""
You are the Strategic Context Agent for {ticker}.

══════════════════════════════════════════════════════════════
PRIMARY DATA — INTAKE PIPELINE (verified facts, Python-computed)
══════════════════════════════════════════════════════════════
{db_context}

══════════════════════════════════════════════════════════════
PRIMARY SOURCE — 10-K FILING TEXT
══════════════════════════════════════════════════════════════
{ten_k_text}

══════════════════════════════════════════════════════════════
SUPPLEMENTARY — PATENT DATA (web search)
══════════════════════════════════════════════════════════════
{patent_context}

══════════════════════════════════════════════════════════════
YOUR TASKS — narrative and booleans only
══════════════════════════════════════════════════════════════

1. DEFERRED REVENUE / QUALITY OF GROWTH
   The pipeline provides: Deferred Revenue, DeferredRevenue_Growth, D8 score.
   D8 Revenue Score < 0.7 = deferred revenue quality issue already flagged.
   Your task: interpret what this means for revenue quality narrative.
   quality_of_growth_risk = True if D8 < 0.7 OR deferred growth significantly
   negative relative to revenue growth.

2. INTANGIBLE DECAY (patent/contract expirations)
   Use 10-K text (Item 1 Business — Patents and Proprietary Rights section).
   Identify specific patent cliffs, exclusivity dates, contract renewals.
   revenue_at_risk_percent = your ESTIMATE of % revenue tied to expiring assets
   within 5 years. Use 0.0 if none found. Be explicit about uncertainty.
   terminal_haircut = True if estimate > 20%.

3. CYCLICALITY (confirm Python calculation)
   Z-score and peak flag are already computed from DuckDB — treat as facts.
   Your task: interpret the economic meaning. Is this cyclical (semiconductors,
   industrials, commodities) or structural growth?

4. SUMMARY
   One paragraph synthesising all three dimensions.

CRITICAL: Do NOT change revenue_z_score, is_cyclical_peak, or
recommended_base_revenue — those are Python-computed facts. Return them
exactly as provided.

Return StrategicContextReport JSON.
""")

    chain = prompt | structured_llm

    try:
        report: StrategicContextReport = chain.invoke({
            "ticker":         ticker,
            "db_context":     db_context_str,
            "ten_k_text":     raw_10k[:50000] if ten_k_available else
                              "10-K text not available — use pipeline data and knowledge.",
            "patent_context": patent_context or "Search unavailable.",
        })

        # Overwrite Python-computed fields — LLM cannot alter these.
        # Schema is frozen=True (architecture invariant), so produce a new
        # instance via model_copy(update=...) rather than mutating in place.
        report = report.model_copy(update={
            "revenue_z_score":          z_score,
            "is_cyclical_peak":         is_peak,
            "applies_cyclical_haircut": applies_cyclical_haircut,
            "recommended_base_revenue": avg_3yr if applies_cyclical_haircut else None,
        })

        output = {
            "strategic_context_report": report.dict(),
            "messages": [HumanMessage(
                content=f"Context: Z={z_score:.2f} (DuckDB) peak={is_peak} "
                        f"haircut={report.terminal_haircut} "
                        f"at_risk={report.revenue_at_risk_percent:.0%} (estimate) "
                        f"growth_risk={report.quality_of_growth_risk}"
            )]
        }
        tracer.log_step("StrategicContext", state, output)
        return output

    except Exception as e:
        print(f"  ✗ Context agent error: {e}")
        return {"messages": [HumanMessage(content=f"Context Agent Failed: {e}")]}
