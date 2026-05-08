from typing import TypedDict, Annotated, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage
import operator
from pydantic import BaseModel, Field

# --- Task 2: Structured Communication ---

class LeadAgentOutput(BaseModel):
    conviction_score: int = Field(description="Score from -10 (Strong Sell) to +10 (Strong Buy)")
    margin_of_safety: float = Field(description="Percentage difference between Intrinsic Value and Market Price")
    growth_decay_assessment: str = Field(description="Analysis of the company's growth sustainability")
    contrarian_rebuttal: str = Field(description="Specific response to the Contrarian's bear case")

class DCFConfig(TypedDict):
    revenue_growth_initial: float
    growth_decay_rate: float
    target_ebit_margin: float
    reinvestment_rate: float
    wacc_override: Optional[float]
    target_debt_equity: Optional[float]
    
    # Sovereign Overrides
    tax_rate_global: Optional[float]
    wacc_floor: Optional[float]
    terminal_growth_cap: Optional[float]
    liquidity_ratio_safe: Optional[float]
    maturity_amortization_rate: Optional[float]
    stress_test_revenue_impact: Optional[float]
    stress_test_wacc_impact: Optional[float]
    double_leverage_threshold: Optional[float]

class AgentState(TypedDict):
    ticker: str
    messages: Annotated[List[BaseMessage], operator.add]

    valuation_report: Dict[str, Any]
    strategist_report: Dict[str, Any]
    phase2_valuation: dict
    contrarian_report: Dict[str, Any]
    forensic_report: Dict[str, Any]
    value_chain_report: Dict[str, Any]
    strategic_context_report: Dict[str, Any] # New Agent Output
    final_report: Optional[LeadAgentOutput] # Now strongly typed

    # ── Calc node outputs (deterministic, written before any agent runs) ────
    cyclicality: Dict[str, Any]
    operating_leverage: Dict[str, Any]
    moat_fingerprint: Dict[str, Any]
    conviction: Dict[str, Any]
    calc_bypassed: Optional[str]

    # ── Scenario eval (Phase C) ────────────────────────────────────────────
    # List of evaluated agent-proposed scenarios. Empty when no agent
    # proposed any. Each entry is the per-scenario summary written by
    # scenario_eval_node (name, type, proposed_by, rationale, overrides
    # applied, full DCF result dict, IPS / upside summary).
    scenario_results: List[Dict[str, Any]]

    # ── Thesis synthesizer (week-1.5) ──────────────────────────────────────
    # Structured ThesisSynthesis output from thesis_synthesizer_agent.
    # Contains: thesis_statement, bull/bear/base CitedClaim with
    # cited_signals enforcement, decision_conditions, thesis_confidence,
    # time_horizon, position_sizing_implications, required_analyst_judgment,
    # update_conditions. Lead surfaces it into final_report.
    thesis_synthesis: Dict[str, Any]

    # ── Qualitative dashboard projection (week-6 wiring) ────────────────────
    # Populated by dashboard_fetch_node. Shape:
    #   {ticker, dimensions[dim_id]={status, score, narrative, ...},
    #    categories[cat_id]={composite_score, status, contributing[], ...},
    #    coverage={n_assessed, n_assessable, coverage_state, stale_paths, ...},
    #    citable_dim_paths: List[str],
    #    citable_composite_paths: List[str],
    #    available: bool}
    # thesis_synthesizer reads this to ground its cited_signals against the
    # analyst's structured judgment. Per-call schema validator rejects
    # citations to non-citable paths.
    qualitative_dashboard: Dict[str, Any]

    # Intelligence Repository
    sector_context: str # Loaded from knowledge_base
    rules_content: str # Loaded from RULES.md

    raw_10k_text: str # For Long Context Task
    dcf_config: Optional[DCFConfig] # Dynamic inputs
