"""Shared schemas + DB-context helpers for the qualitative synthesis agent.

Extracted from the now-removed `forensic.py`, `value_chain.py`, and
`context.py` agents during week-1.5 consolidation. The agent functions
were replaced by `qualitative_synthesis_agent` (one LLM call covering
all three lenses); the *schemas* and *DB-context loaders* are still
needed because:

  - `qualitative_synthesis` emits ForensicReport / ValueChainReport /
    StrategicContextReport instances nested under one parent
  - The DB-loader helpers project DuckDB rows into the structured
    facts the consolidated prompt sends to the LLM
  - The `build_db_context_str` formatters render those facts in the
    exact format the legacy 3-agent prompts used (preserved verbatim
    so prompt compatibility is byte-equivalent)

This module is data-only — no LLM calls, no agent functions, no web
search. Architecture lock test (`test_no_agent_emitted_overrides`)
applies; no field names that mutate calc-layer math.
"""

from __future__ import annotations

import json
from typing import Dict, List, Literal, Optional

import numpy as np
from pydantic import BaseModel, Field, field_validator

from aletheia.contracts.interfaces import ScenarioOverride


# ═════════════════════════════════════════════════════════════════════════════
# Forensic — moat / business profile / concentration / op-leverage narrative
# ═════════════════════════════════════════════════════════════════════════════

class RevenueSegment(BaseModel):
    model_config = {"frozen": True}
    segment: str
    pct_revenue: Optional[float] = None
    growth_trend: str = "unknown"


class ForensicReport(BaseModel):
    """Forensic-section narrative output.

    Numeric fields (moat_score, moat_attributes scores) are LLM-generated
    qualitative SIGNALS for narrative color only — they are NOT consumed
    by ConvictionScorer or any calc-layer tool (those use the deterministic
    `moat_fingerprint` computed in `calc_node`).
    """
    model_config = {"frozen": True}

    moat_assessment: Literal["wide", "narrow", "none", "uncertain"] = "uncertain"
    accruals_quality: Literal["high", "medium", "low", "uncertain"] = "uncertain"
    earnings_quality: Literal["high", "medium", "low", "uncertain"] = "uncertain"

    operating_leverage_analysis: str = Field(
        description="Explain cost structure: fixed vs variable drivers, "
                    "how revenue growth flows to EBIT. Concrete examples.")

    moat_score: float = Field(
        ge=0.0, le=10.0,
        description="Overall moat score 1-10.")
    moat_attributes: Dict[str, float] = Field(
        description="Scores 1-10 for: switching_costs, network_effects, "
                    "cost_advantage, intangibles.")

    @field_validator("moat_attributes")
    @classmethod
    def _validate_moat_attributes_bounds(cls, v: Dict[str, float]) -> Dict[str, float]:
        for key, score in v.items():
            try:
                f = float(score)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"moat_attributes[{key!r}]={score!r} is not numeric"
                ) from exc
            if not (0.0 <= f <= 10.0):
                raise ValueError(
                    f"moat_attributes[{key!r}]={f} out of [0, 10]"
                )
        return v
    moat_evidence: str = Field(
        description="Specific evidence from 10-K: retention rates, price hike "
                    "history, switching cost examples, patent counts.")

    has_pricing_power: bool = Field(
        description="True if company raised prices without material churn in last 3 years.")
    pricing_power_evidence: str = Field(
        description="Specific evidence: price hike dates, amounts, NRR, contract escalators.")

    concentration_risk: bool = Field(
        description="True if any single supplier or customer > 10% of revenue.")
    concentration_details: str = Field(
        description="Name the customer/supplier, % of revenue, risk if lost.")

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

    # Consolidated agent caps at max 1 forensic-lens scenario per ticker
    # (max_length=2 retained from the legacy schema for backward
    # compatibility with scenario_eval_node's reading code).
    scenarios: List[ScenarioOverride] = Field(
        default_factory=list,
        description="0-1 alternate DCF scenarios with rationale (forensic lens).",
        max_length=2,
    )


def load_db_context_forensic(ticker: str) -> dict:
    """Load quantitative facts for the forensic-lens prompt section."""
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

        rev_series = df.sort_values("fiscal_year")["clean_Revenue"].dropna()
        rev_cagr_3y = None
        if len(rev_series) >= 3:
            r0, r1 = float(rev_series.iloc[-3]), float(rev_series.iloc[-1])
            if r0 > 0:
                rev_cagr_3y = (r1 / r0) ** (1 / 3) - 1

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
        print(f"  ✗ DB load failed (forensic): {e}")
        return {}


def build_db_context_str_forensic(db: dict) -> str:
    """Format forensic-lens DB facts for the consolidated LLM prompt."""
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
        f"  SBC % of FCF:       {fmt(db.get('sbc_pct_fcf'), pre_pct=True)}",
        f"  Share Dilution:     {fmt(db.get('share_dilution_pct'), pre_pct=True)}",
        f"  3Y Revenue CAGR:    {fmt(db.get('rev_cagr_3y'), pct=True)}",
        f"  Deferred Revenue:   {fmt(db.get('deferred_revenue'), bn=True)}",
        f"  Deferred Rev Growth:{fmt(db.get('deferred_rev_growth'), pct=True)}",
        f"  D8 Revenue Score:   {fmt(db.get('d8_revenue_score'))} (1.0=clean, 0=risk)",
        f"  Data Quality:       {fmt(db.get('data_quality'))}",
    ]
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# Value chain — Porter's five forces / supplier bottlenecks / pricing power
# ═════════════════════════════════════════════════════════════════════════════

class ValueChainReport(BaseModel):
    """Value-chain narrative output.

    The numeric `strategic_leverage_score` is a LLM-generated qualitative
    signal for narrative color only — NOT consumed by ConvictionScorer
    or calc-layer tools.
    """
    model_config = {"frozen": True}

    strategic_position: Literal["dominant", "strong", "moderate", "weak", "uncertain"] = "uncertain"
    upstream_power: Literal["high_supplier_power", "balanced", "high_buyer_power", "uncertain"] = "uncertain"
    substitution_pressure: Literal["high", "medium", "low", "uncertain"] = "uncertain"

    strategic_leverage_score: float = Field(
        ge=0.0, le=10.0,
        description="Score 1-10 Porter Five Forces. "
                    "10=Dominant platform. 1=Commodity price taker. "
                    "Narrative color only — does NOT feed conviction or calc.")

    power_ratio: float = Field(
        description="ESTIMATE: Supplier Gross Margin / Target Gross Margin. "
                    ">1.5 = upstream value leak. LLM estimate — supplier "
                    "margins not in DuckDB.")
    upstream_value_leak: bool = Field(
        description="True if power_ratio > 1.5 or critical supplier bottleneck.")
    bottleneck_analysis: str = Field(
        description="Who is the critical supplier, what leverage do they have, "
                    "what happens if they raise prices?")

    substitution_risk_score: float = Field(
        ge=0.0, le=10.0,
        description="1-10. 10=easily substituted. 1=deeply locked-in.")
    top_substitutes: str = Field(
        description="Top 3 functional substitutes and realistic switching scenario.")

    pricing_power_assessment: str = Field(
        description="Evidence of price leadership or price-taking. "
                    "Specific contract terms, price hike history.")
    pass_through_capability: bool = Field(
        description="True if company can raise prices 10% without material churn.")

    analysis_summary: str = Field(
        description="Porter Five Forces executive summary: supplier power, "
                    "buyer power, substitution threat, rivalry, overall position.")

    scenarios: List[ScenarioOverride] = Field(
        default_factory=list,
        description="0-1 alternate DCF scenarios anchored in value-chain dynamics.",
        max_length=2,
    )


def load_db_context_value_chain(ticker: str) -> dict:
    """Load quantitative facts for the value-chain-lens prompt section."""
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

        gm_series = df.sort_values("fiscal_year")["derived_GrossMargin_Pct"].dropna()
        gm_trend = None
        if len(gm_series) >= 3:
            delta = float(gm_series.iloc[-1]) - float(gm_series.iloc[-3])
            gm_trend = "expanding" if delta > 0.01 else (
                "contracting" if delta < -0.01 else "stable")

        return {
            "gross_margin_pct":   _g("derived_GrossMargin_Pct"),
            "ebit_margin_pct":    _g("derived_EBIT_Margin_Pct"),
            "fcf_margin_pct":     _g("derived_FCF_Margin_Pct"),
            "roic":               _g("derived_ROIC"),
            "sbc_pct_fcf":        _g("clean_SBC_PctFCF"),
            "revenue":            _g("clean_Revenue"),
            "net_debt":           _g("derived_NetDebt"),
            "data_quality":       _g("overall_quality_score"),
            "fiscal_year":        int(df["fiscal_year"].max()),
            "gross_margin_trend": gm_trend,
        }
    except Exception as e:
        print(f"  ✗ DB load failed (value_chain): {e}")
        return {}


# ═════════════════════════════════════════════════════════════════════════════
# Strategic context — cyclicality interpretation / intangible risk / growth quality
# ═════════════════════════════════════════════════════════════════════════════

class StrategicContextReport(BaseModel):
    """Strategic-context narrative output.

    The numeric fields (revenue_z_score, recommended_base_revenue,
    revenue_at_risk_percent) are deterministic values pulled from
    `calc_node` state — the LLM does NOT compute them. The agent
    overwrites these with the calc_node values after the LLM call.
    """
    model_config = {"frozen": True}

    cyclicality_classification: Literal["cyclical", "non_cyclical", "ambiguous"] = "ambiguous"
    growth_quality: Literal["high", "medium", "low", "uncertain"] = "uncertain"
    intangible_decay_severity: Literal["high", "medium", "low", "none", "uncertain"] = "uncertain"

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

    deferred_revenue_trend: str = Field(
        description="Analysis of deferred revenue trend vs revenue growth.")
    quality_of_growth_risk: bool = Field(
        description="True if deferred revenue declining relative to revenue.")

    intangible_risk_assessment: str = Field(
        description="Assessment of patent/contract expirations from 10-K.")
    revenue_at_risk_percent: float = Field(
        description="ESTIMATE: % of revenue tied to assets expiring within 5 years.")
    terminal_haircut: bool = Field(
        description="True if revenue_at_risk_percent > 20%.")

    summary: str = Field(
        description="One paragraph: cyclicality, growth quality, intangible risk.")

    scenarios: List[ScenarioOverride] = Field(
        default_factory=list,
        description="0-1 alternate DCF scenarios anchored in macro/cyclical context.",
        max_length=2,
    )


def build_db_context_str_context(z_score, is_peak, applies_cyclical_haircut, avg_3yr, db: dict) -> str:
    """Format strategic-context-lens DB facts for the consolidated LLM prompt."""

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


__all__ = [
    "RevenueSegment",
    "ForensicReport",
    "load_db_context_forensic",
    "build_db_context_str_forensic",
    "ValueChainReport",
    "load_db_context_value_chain",
    "StrategicContextReport",
    "build_db_context_str_context",
]
