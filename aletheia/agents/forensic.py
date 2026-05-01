"""
aletheia/agents/forensic.py

Data sources (in priority order):
  1. DuckDB (InvestmentDatabase) — all quantitative inputs
  2. state["raw_10k_text"]      — 10-K text for qualitative narrative
  3. Web search (2 queries max) — only for competitor margins not in DB

Rule: LLM receives structured facts from DuckDB.
      LLM writes narrative and boolean judgments only.
      Python computes operating_leverage_score from DuckDB margins.
      No LLM-generated floats feed DCF.
"""

import os
import json
import numpy as np
import pandas as pd
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from config import MODEL_NAME, TEMPERATURE
from aletheia.utils.tracing import tracer
from aletheia.tools.search import search_sentiment


# ─────────────────────────────────────────────────────────────────────────────
# Python calculation — operating leverage from DuckDB margins
# ─────────────────────────────────────────────────────────────────────────────

from aletheia.tools.forensic_metrics import compute_operating_leverage_score


# ─────────────────────────────────────────────────────────────────────────────
# Schema — no LLM-generated floats for DCF inputs
# ─────────────────────────────────────────────────────────────────────────────

class RevenueSegment(BaseModel):
    segment: str
    pct_revenue: Optional[float] = None
    growth_trend: str = "unknown"

class ForensicReport(BaseModel):
    # Narrative only — score computed in Python
    operating_leverage_analysis: str = Field(
        description="Explain cost structure: fixed vs variable drivers, "
                    "how revenue growth flows to EBIT. Concrete examples.")

    # Moat — LLM judgment
    moat_score: float = Field(description="Overall moat score 1-10.")
    moat_attributes: Dict[str, float] = Field(
        description="Scores 1-10 for: switching_costs, network_effects, "
                    "cost_advantage, intangibles.")
    moat_evidence: str = Field(
        description="Specific evidence from 10-K: retention rates, price hike "
                    "history, switching cost examples, patent counts.")

    # Pricing power — boolean judgment only
    has_pricing_power: bool = Field(
        description="True if company raised prices without material churn in last 3 years.")
    pricing_power_evidence: str = Field(
        description="Specific evidence: price hike dates, amounts, NRR, contract escalators.")

    # Concentration risk — boolean judgment only
    concentration_risk: bool = Field(
        description="True if any single supplier or customer > 10% of revenue.")
    concentration_details: str = Field(
        description="Name the customer/supplier, % of revenue, risk if lost.")

    # Business profile — from 10-K text
    business_description: str = Field(
        description="2-3 specific sentences: what company does, who customers are, "
                    "how revenue is generated. No generic language.")
    revenue_segments: List[RevenueSegment] = Field(
        description="All business segments with approximate revenue % and growth direction.")
    key_customers: List[str] = Field(
        description="Top 3-5 customers or customer categories with concentration if known.")
    competitive_landscape: str = Field(
        description="Top 2-3 competitors and how this company differentiates.")
    regulatory_risk: str = Field(
        description="Meaningful regulatory exposure in one sentence. "
                    "'No material regulatory risk identified.' if none.")


# ─────────────────────────────────────────────────────────────────────────────
# Data loading from DuckDB
# ─────────────────────────────────────────────────────────────────────────────

def load_db_context(ticker: str) -> dict:
    """Load all needed quantitative context from DuckDB."""
    try:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        df = db.get_latest(ticker)
        db.close()
        if df.empty:
            return {}

        # Latest year row
        row = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0]

        def _g(col):
            v = row.get(col)
            return float(v) if v is not None and not (
                isinstance(v, float) and np.isnan(v)) else None

        # Multi-year revenue for CAGR context
        rev_series = df.sort_values("fiscal_year")["clean_Revenue"].dropna()
        rev_cagr_3y = None
        if len(rev_series) >= 3:
            r0, r1 = float(rev_series.iloc[-3]), float(rev_series.iloc[-1])
            if r0 > 0:
                rev_cagr_3y = (r1 / r0) ** (1/3) - 1

        # Deferred revenue from clean_json
        deferred_rev = None
        deferred_rev_growth = None
        try:
            clean_data = json.loads(row.get("clean_json") or "{}")
            deferred_rev = clean_data.get("DeferredRevenue")
            deferred_rev_growth = clean_data.get("DeferredRevenue_Growth")
        except Exception:
            pass

        return {
            "gross_margin_pct":    _g("derived_GrossMargin_Pct"),
            "ebit_margin_pct":     _g("derived_EBIT_Margin_Pct"),
            "ebitda_margin_pct":   _g("derived_EBITDA_Margin_Pct"),
            "fcf_margin_pct":      _g("derived_FCF_Margin_Pct"),
            "roic":                _g("derived_ROIC"),
            "roe":                 _g("derived_ROE"),
            "net_debt":            _g("derived_NetDebt"),
            "revenue":             _g("clean_Revenue"),
            "ebitda":              _g("derived_EBITDA"),
            "fcf":                 _g("derived_FCF"),
            "sbc_pct_fcf":         _g("clean_SBC_PctFCF"),
            "sbc":                 _g("clean_SBC"),
            "share_dilution_pct":  _g("clean_ShareDilution_Pct"),
            "data_quality":        _g("overall_quality_score"),
            "fiscal_year":         int(df["fiscal_year"].max()),
            "rev_cagr_3y":         rev_cagr_3y,
            "deferred_revenue":    deferred_rev,
            "deferred_rev_growth": deferred_rev_growth,
            "d8_revenue_score":    _g("domain_score_D8_Revenue"),
        }
    except Exception as e:
        print(f"  ✗ DB load failed: {e}")
        return {}


def build_db_context_str(db: dict) -> str:
    """Format DuckDB data as structured facts for LLM prompt."""
    if not db:
        return "DuckDB data unavailable."

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

    lines = [
        f"FINANCIAL FACTS FROM INTAKE PIPELINE (FY{db.get('fiscal_year','?')}):",
        f"  Revenue:            {fmt(db.get('revenue'), bn=True)}",
        f"  EBITDA:             {fmt(db.get('ebitda'), bn=True)}",
        f"  FCF:                {fmt(db.get('fcf'), bn=True)}",
        f"  Gross Margin:       {fmt(db.get('gross_margin_pct'), pre_pct=True)}",
        f"  EBIT Margin:        {fmt(db.get('ebit_margin_pct'), pre_pct=True)}",
        f"  FCF Margin:         {fmt(db.get('fcf_margin_pct'), pre_pct=True)}",
        f"  ROIC:               {fmt(db.get('roic'), pct=True)}",
        f"  ROE:                {fmt(db.get('roe'), pct=True)}",
        f"  Net Debt:           {fmt(db.get('net_debt'), bn=True)}",
        f"  DSO (Days Sales):   {fmt(db.get('dso'))}",
        f"  SBC % of FCF:       {fmt(db.get('sbc_pct_fcf'), pre_pct=True)}",
        f"  Share Dilution:     {fmt(db.get('share_dilution_pct'), pre_pct=True)}",
        f"  3Y Revenue CAGR:    {fmt(db.get('rev_cagr_3y'), pct=True)}",
        f"  Deferred Revenue:   {fmt(db.get('deferred_revenue'), bn=True)}",
        f"  Deferred Rev Growth:{fmt(db.get('deferred_rev_growth'), pct=True)}",
        f"  D8 Revenue Score:   {fmt(db.get('d8_revenue_score'))} (1.0=clean, 0=risk)",
        f"  Data Quality:       {fmt(db.get('data_quality'))}",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────

def forensic_agent(state):
    """
    Economic Forensic Agent.

    Data sources:
      1. DuckDB — all quantitative facts (margins, ROIC, FCF, SBC, etc.)
      2. raw_10k_text — qualitative narrative (moat, business profile)
      3. Web search — 1 query only for competitor margins (unavailable in DB)

    Rule compliance:
      - operating_leverage_score: Python computation from DuckDB margins
      - wacc_penalty: NOT returned — valuation_node applies rule from boolean
      - growth_sustainability_boost: NOT returned — valuation_node applies rule
      - LLM returns: narrative, scores 1-10, booleans only
    """
    print("---FORENSIC AGENT---")
    ticker = state["ticker"]

    # ── 1. Load from DuckDB ───────────────────────────────────────────────────
    db = load_db_context(ticker)

    # ── 2. Python calculation: operating leverage score ───────────────────────
    op_leverage_score = compute_operating_leverage_score(
        db.get("gross_margin_pct") or 0.0,
        db.get("ebit_margin_pct") or 0.0,
    )
    print(f"  ✓ Op leverage (Python): gross={db.get('gross_margin_pct', 0):.1%} "
          f"ebit={db.get('ebit_margin_pct', 0):.1%} → score={op_leverage_score}")

    # ── 3. Primary source: 10-K text from state ───────────────────────────────
    raw_10k = state.get("raw_10k_text", "")
    ten_k_available = bool(raw_10k and "unavailable" not in raw_10k.lower()
                           and "failed" not in raw_10k.lower())

    # ── 4. Supplementary: one search for competitor data not in DB ───────────
    competitor_context = ""
    try:
        competitor_context = search_sentiment(
            f"{ticker} competitors gross margin comparison industry"
        )
    except Exception:
        pass

    db_context_str = build_db_context_str(db)

    # ── Mock ──────────────────────────────────────────────────────────────────
    if not os.environ.get("GOOGLE_API_KEY"):
        result = {
            "operating_leverage_score":    op_leverage_score,
            "operating_leverage_analysis": "Mock: balanced fixed/variable.",
            "moat_score":           5.0,
            "moat_attributes":      {"switching_costs": 5, "network_effects": 5,
                                     "cost_advantage": 5, "intangibles": 5},
            "moat_evidence":        "Mock evidence.",
            "has_pricing_power":    False,
            "pricing_power_evidence": "None.",
            "concentration_risk":   False,
            "concentration_details": "None.",
            "business_description": f"{ticker} mock business.",
            "revenue_segments":     [],
            "key_customers":        ["Mock customer"],
            "competitive_landscape": "Mock competitors.",
            "regulatory_risk":      "No material regulatory risk identified.",
        }
        return {
            "forensic_report": result,
            "messages": [HumanMessage(content="Forensic: Mock complete.")]
        }

    llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=TEMPERATURE)
    structured_llm = llm.with_structured_output(ForensicReport)

    prompt = ChatPromptTemplate.from_template("""
You are the Economic Forensic Investigator for {ticker}.

══════════════════════════════════════════════════════════════
PRIMARY DATA — INTAKE PIPELINE (treat as verified facts)
══════════════════════════════════════════════════════════════
{db_context}

Operating Leverage Score: {op_leverage_score}/10 (Python-computed from margins above)

══════════════════════════════════════════════════════════════
PRIMARY SOURCE — 10-K FILING TEXT
══════════════════════════════════════════════════════════════
{ten_k_text}

══════════════════════════════════════════════════════════════
SUPPLEMENTARY — COMPETITOR DATA (web search)
══════════════════════════════════════════════════════════════
{competitor_context}

══════════════════════════════════════════════════════════════
YOUR TASKS — narrative and boolean judgments only
══════════════════════════════════════════════════════════════

1. OPERATING LEVERAGE NARRATIVE
   The score ({op_leverage_score}/10) is already computed from verified margins.
   Your task: explain WHY. What are the major fixed cost drivers?
   Use the pipeline data — R&D spend, SBC, lease obligations, headcount costs.

2. MOAT ANALYSIS
   Score each attribute 1-10: switching_costs, network_effects,
   cost_advantage, intangibles. Use 10-K text for specific evidence.
   Cite: retention rates, price hike history, patent counts, NRR.
   moat_score = weighted average.

3. PRICING POWER (boolean only)
   has_pricing_power = True/False based on 10-K evidence.
   Cite specific price increases and churn outcomes.

4. CONCENTRATION RISK (boolean only)
   concentration_risk = True if any supplier or customer > 10% of revenue.
   Source: 10-K Item 1A Risk Factors. Name them.

5. BUSINESS PROFILE (from 10-K Item 1)
   a) business_description: 2-3 specific sentences
   b) revenue_segments: all segments with approx % and growth direction
   c) key_customers: top 3-5 with concentration if disclosed
   d) competitive_landscape: top 2-3 competitors, differentiation
   e) regulatory_risk: one sentence

DO NOT return wacc_penalty or growth_sustainability_boost floats.
Those are computed by Python rules in valuation_node.

Return ForensicReport JSON.
""")

    chain = prompt | structured_llm

    try:
        report: ForensicReport = chain.invoke({
            "ticker":             ticker,
            "db_context":         db_context_str,
            "op_leverage_score":  op_leverage_score,
            "ten_k_text":         raw_10k[:60000] if ten_k_available else
                                  "10-K text not available — use pipeline data and knowledge.",
            "competitor_context": competitor_context or "Search unavailable.",
        })

        result = report.dict()
        result["operating_leverage_score"] = op_leverage_score  # Python value

        output = {
            "forensic_report": result,
            "messages": [HumanMessage(
                content=f"Forensic: Moat {report.moat_score}/10. "
                        f"OpLev {op_leverage_score}/10 (Python). "
                        f"Pricing power: {report.has_pricing_power}. "
                        f"Concentration: {report.concentration_risk}. "
                        f"Segments: {len(report.revenue_segments)}."
            )]
        }
        tracer.log_step("Forensic", state, output)
        return output

    except Exception as e:
        print(f"  ✗ Forensic agent error: {e}")
        return {"messages": [HumanMessage(content=f"Forensic: Error {e}")]}
