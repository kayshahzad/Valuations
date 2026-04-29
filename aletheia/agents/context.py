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
from typing import Dict, List, Optional
from config import MODEL_NAME, TEMPERATURE
from aletheia.utils.tracing import tracer
from aletheia.tools.search import search_sentiment


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

class StrategicContextReport(BaseModel):
    # Python-computed — LLM must not alter
    revenue_z_score: float = Field(
        description="Z-Score of latest revenue vs historical mean. Python-computed from DuckDB.")
    is_cyclical_peak: bool = Field(
        description="True if Z-Score > 2.0. Python rule.")
    recommended_base_revenue: Optional[float] = Field(
        description="3-year average revenue if peak. Python numpy mean from DuckDB.")

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

def calculate_z_score_from_db(ticker: str):
    """
    Calculate revenue Z-score from DuckDB clean_Revenue multi-year series.

    KEY IMPROVEMENT over original:
    - Original used yfinance income_statement JSON (unreliable parsing)
    - This uses clean_Revenue from DuckDB (already cleaned, multi-year)
    - get_latest() returns ALL fiscal years ordered by year

    Returns: (z_score, is_peak, avg_3yr, df_context)
    """
    try:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        df = db.get_latest(ticker)
        db.close()

        if df.empty or "clean_Revenue" not in df.columns:
            return 0.0, False, None, {}

        rev_series = df.sort_values("fiscal_year")["clean_Revenue"].dropna()
        revenues = [float(r) for r in rev_series if r and not np.isnan(r)]

        if len(revenues) < 3:
            return 0.0, False, None, {}

        mean = np.mean(revenues)
        std  = np.std(revenues)
        z_score = float((revenues[-1] - mean) / std if std > 0 else 0.0)
        is_peak = bool(z_score > 2.0)
        avg_3yr = float(np.mean(revenues[-3:]))

        # Additional context from latest row
        latest_row = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0]

        def _g(col):
            v = latest_row.get(col)
            return float(v) if v is not None and not (
                isinstance(v, float) and np.isnan(v)) else None

        # Deferred revenue from clean_json
        deferred_rev = None
        deferred_rev_growth = None
        d8_score = _g("domain_score_D8_Revenue")
        try:
            clean_data = json.loads(latest_row.get("clean_json") or "{}")
            deferred_rev = clean_data.get("DeferredRevenue")
            deferred_rev_growth = clean_data.get("DeferredRevenue_Growth")
        except Exception:
            pass

        # Intangible amortization from raw_json
        intangible_amort = None
        try:
            raw_data = json.loads(latest_row.get("raw_json") or "{}")
            intangible_amort = raw_data.get("AmortizationOfIntangibleAssets")
        except Exception:
            pass

        db_context = {
            "fiscal_years":      sorted(df["fiscal_year"].tolist()),
            "revenues":          revenues,
            "latest_revenue":    revenues[-1],
            "avg_3yr":           avg_3yr,
            "revenue_cagr_3y":   (revenues[-1] / revenues[-3]) ** (1/3) - 1
                                 if len(revenues) >= 3 and revenues[-3] > 0 else None,
            "deferred_revenue":  deferred_rev,
            "deferred_rev_growth": deferred_rev_growth,
            "d8_revenue_score":  d8_score,
            "intangible_amort":  intangible_amort,
            "data_quality":      _g("overall_quality_score"),
            "gross_margin_pct":  _g("derived_GrossMargin_Pct"),
            "fcf_margin_pct":    _g("derived_FCF_Margin_Pct"),
        }

        return z_score, is_peak, avg_3yr, db_context

    except Exception as e:
        print(f"  ✗ DB z-score calculation failed: {e}")
        return 0.0, False, None, {}


def build_db_context_str(z_score, is_peak, avg_3yr, db: dict) -> str:
    """Format DuckDB data for LLM prompt."""

    def fmt(v, pct=False, bn=False):
        if v is None:
            return "N/A"
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
  Revenue Z-Score:       {z_score:.2f}
  Is Cyclical Peak:      {is_peak} (Z > 2.0)
  Gross Margin:          {fmt(db.get('gross_margin_pct'), pct=True)}
  FCF Margin:            {fmt(db.get('fcf_margin_pct'), pct=True)}
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
    z_score, is_peak, avg_3yr, db = calculate_z_score_from_db(ticker)
    print(f"  ✓ Z-score (DuckDB): {z_score:.2f} | peak={is_peak} | "
          f"3yr_avg={'${:.1f}B'.format(avg_3yr/1e9) if avg_3yr else 'N/A'}")

    db_context_str = build_db_context_str(z_score, is_peak, avg_3yr, db)

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
            "recommended_base_revenue":  avg_3yr if is_peak else None,
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

        # Overwrite Python-computed fields — LLM cannot alter these
        report.revenue_z_score        = z_score
        report.is_cyclical_peak       = is_peak
        report.recommended_base_revenue = avg_3yr if is_peak else None

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
