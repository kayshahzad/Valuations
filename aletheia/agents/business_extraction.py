"""Bottom-up business extraction (memo §4, Phase 2) — themes A + B.

Structured LLM extraction from the 10-K of the facts the deterministic layer
can't compute: what the company actually sells (product/service portfolio,
pricing & contract structure, named customers/contracts with recompete timing,
distribution channels) and where it sits in its market (TAM, current share,
whitespace runway, adjacent TAMs).

Cost discipline: one structured LLM call, run inside the Stage-4 extraction node
(``qualitative_extraction_node``) where the 10-K text is already loaded — so it
adds a single call per agent run, not a new pipeline. Results cache to disk
(annual TTL, since they're 10-K-derived) and are read by
``business_analysis.build_business_analysis`` on the free read path. Returns
``None`` / ``[]`` (never raises) when no API key, no 10-K text, or parse fails —
the deterministic bottom-up block still works without it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

_CACHE_DIR = Path("valuation_data/macro/business_ab")
_TTL_SECONDS = 370 * 24 * 3600  # ~1 year (10-K cadence)


# ── Structured schema (themes A + B) ────────────────────────────────────────

class ProductLine(BaseModel):
    name: str = Field(description="Specific product/service offering (not just the segment)")
    segment: str = Field(default="", description="Reporting segment it sits in")
    pricing_model: str = Field(default="", description="e.g. fixed-price, cost-plus, T&M, subscription, usage, license")
    notes: str = Field(default="", description="Contract type / revenue model detail")


class CustomerContract(BaseModel):
    name: str = Field(description="Named customer or contract")
    relationship: str = Field(default="", description="What they buy / nature of relationship")
    duration: str = Field(default="", description="Contract duration if disclosed")
    recompete_or_renewal: str = Field(default="", description="Recompete / renewal date or cadence if disclosed")
    pct_revenue: str = Field(default="", description="Approx % of revenue if disclosed")


class NewLaunch(BaseModel):
    name: str = Field(description="Recent product/service launch")
    timing: str = Field(default="", description="When launched")
    traction: str = Field(default="", description="Early traction / contribution to growth")


class SegmentEconomics(BaseModel):
    segment: str = Field(description="Reporting/operating segment name AS IT APPEARS in the 10-K segment note")
    operating_margin: str = Field(default="", description="Segment operating margin if disclosed, e.g. '6.2%' or 'low-single-digit'")
    margin_trend: str = Field(default="", description="improving / stable / declining")
    notes: str = Field(default="", description="Driver of the trend: mix shift, pricing, cost pressure, etc.")


class BusinessAB(BaseModel):
    """Bottom-up extraction covering themes A (what it sells), B (market),
    C (unit economics) and E (innovation/trend) — one structured 10-K call."""
    # Theme A — what the company sells
    product_lines: List[ProductLine] = Field(default_factory=list,
        description="3-8 specific offerings with pricing/contract structure")
    major_customers: List[CustomerContract] = Field(default_factory=list,
        description="Named major customers/contracts with durations & recompete timing")
    distribution_channels: List[str] = Field(default_factory=list,
        description="How customers buy: direct sales, channel/VAR, marketplace, prime/sub, etc.")
    # Theme B — market size & capture
    tam_estimate: str = Field(default="", description="BASE-case total addressable market $ size (e.g. '$50 billion'). Triangulate if the filing doesn't state one. Leave blank only if genuinely not estimable.")
    tam_low: str = Field(default="", description="LOW (conservative) end of the TAM range (e.g. '$40 billion') — narrower market definition / slower adoption")
    tam_high: str = Field(default="", description="HIGH (optimistic) end of the TAM range (e.g. '$70 billion') — broader definition / faster adoption")
    tam_approach: str = Field(default="", description="The sector-appropriate sizing APPROACH used, e.g. 'top-down IT-spend share' (SaaS), 'addressable patients × annual price' (pharma), 'unit volume × ASP' (semis/hardware), 'store/branch count × revenue-per' (retail), 'served population × penetration × ARPU' (consumer)")
    tam_methodology: str = Field(default="", description="How TAM is defined/sized + its growth rate; if split into sub-markets, list each with its size")
    tam_confidence: str = Field(default="", description="REQUIRED when tam_estimate is non-blank: low (rough triangulation) / medium (consensus third-party sizing) / high (filing or citable source) — or 'not_estimable' only if the market genuinely cannot be sized")
    market_share: str = Field(default="", description="Current share of TAM (overall or by segment) and vs key competitors")
    whitespace_runway: str = Field(default="", description="Unaddressed segments/geographies/customers; realistic share ceiling")
    adjacent_tams: List[str] = Field(default_factory=list,
        description="Adjacent markets the company could credibly enter")
    # Theme C — unit economics
    contract_economics: str = Field(default="", description="Typical deal size, length, and margin profile by contract type")
    cac_ltv: str = Field(default="", description="CAC vs LTV, payback, retention/NRR, cohort behavior (often only SaaS discloses)")
    unit_cost: str = Field(default="", description="Cost per unit produced / per service delivered; key labor or input costs")
    segment_margin_trajectory: str = Field(default="", description="How unit/segment margins are evolving (leading indicator)")
    segment_economics: List[SegmentEconomics] = Field(default_factory=list,
        description="Per-reporting-segment operating margin + trajectory. Use the EXACT segment names from the 10-K segment footnote so they can be matched to FMP revenue-mix data.")
    operating_leverage: str = Field(default="", description="Fixed vs variable cost split; incremental margin on growth")
    # Theme E — innovation & trend positioning
    rd_pipeline: str = Field(default="", description="Active R&D projects, timeline to revenue, R&D as % of revenue, productivity")
    trend_positioning: str = Field(default="", description="Leader / follower / laggard on the key trends affecting the industry")
    new_product_launches: List[NewLaunch] = Field(default_factory=list,
        description="Recent launches with early traction / growth contribution")
    disruption_risk: str = Field(default="", description="Technologies/models that could displace the revenue base, and the firm's response")
    acquisition_strategy: str = Field(default="", description="M&A pattern: what gets bought (capability vs scale), integration approach")
    confidence: str = Field(default="", description="low/medium/high — how grounded these are in the filing vs inferred")


def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}.json"


def cached_business_ab(ticker: str) -> Optional[Dict[str, Any]]:
    """Cache-only read (no LLM) — for the report/UI read path."""
    p = _cache_path(ticker)
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text())
        if time.time() - blob.get("fetched_at", 0) > _TTL_SECONDS:
            return None
        return blob.get("data")
    except Exception:
        return None


def _write_cache(ticker: str, data: Dict[str, Any]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(ticker).write_text(json.dumps(
            {"fetched_at": time.time(), "data": data}, indent=2))
    except Exception:
        pass


_PROMPT = """You are an equity research analyst doing BOTTOM-UP business analysis \
of {company} (ticker {ticker}) from its 10-K. For company-specific facts \
(products, customers, contracts, segment margins) extract ONLY what the filing \
supports — do not invent numbers, leave a field blank rather than guess. \
The ONE exception is market SIZING (TAM / sub-market sizes): there you SHOULD \
apply standard equity-research triangulation using widely-reported industry \
figures and your own market knowledge — but you MUST label every such estimate \
with a confidence that reflects its source (see THEME B).

THEME A — what the company sells:
- product_lines: 3-8 SPECIFIC offerings (not just segment names), each with its \
pricing/contract model (fixed-price, cost-plus, time-&-materials, subscription, \
usage, perpetual license, etc.).
- major_customers: named customers/contracts with duration and recompete/renewal \
timing and % of revenue WHERE DISCLOSED (e.g. for govt contractors, the recompete \
cycle is critical).
- distribution_channels: how customers actually buy (direct sales force, channel/\
VAR, marketplace, prime vs subcontractor, OEM, etc.).

THEME B — market size & capture:
- TAM with a CONFIDENCE BAND + sector approach. Give a genuine range:
  tam_estimate (BASE), tam_low (conservative — narrower definition / slower
  adoption), tam_high (optimistic — broader definition / faster adoption). Do NOT
  repeat the same number three times. Pick the sector-appropriate sizing APPROACH
  and name it in tam_approach:
    SaaS / software   → top-down IT-spend share, or bottoms-up customers × ACV;
    pharma / biotech  → addressable patients × annual price × penetration;
    semis / hardware  → unit volume × ASP;
    retail / consumer → store/branch count × revenue-per, or served pop × penetration × ARPU;
    financials        → addressable balances/transactions × take-rate.
  DO give numbers — use the filing if it states them, otherwise TRIANGULATE from
  well-established industry market-sizing and your own knowledge (this is the one
  place external knowledge is expected). When the market splits into sub-markets,
  size each in tam_methodology and roll up to tam_estimate. ALWAYS set
  tam_confidence by SOURCE quality:
    high   = stated in the filing or a precise, citable third-party figure;
    medium = consensus third-party market sizing (Gartner/IDC-style);
    low    = rough triangulation (sum of sub-markets, or revenue ÷ assumed share);
    not_estimable = ONLY when the market genuinely cannot be sized even roughly \
(rare for large public companies). Never present triangulation as precise.
- market_share: current share of that market (overall or by segment) and standing \
vs named competitors — implied share = company revenue ÷ TAM is acceptable when \
not disclosed (label it as implied).
- whitespace_runway: unaddressed segments/geographies/customers and a realistic \
share ceiling.
- adjacent_tams: markets the company could credibly expand into.

THEME C — unit economics (the structure beneath aggregate margins):
- contract_economics: typical deal size, length, and margin by contract type.
- cac_ltv: customer acquisition cost vs lifetime value, payback, retention/NRR, \
cohort behavior (usually only SaaS/consumer disclose; leave blank otherwise).
- unit_cost: cost per unit produced / per service delivered; key labor or input costs.
- segment_margin_trajectory: how unit/segment margins are evolving (leading indicator).
- segment_economics: for EACH reporting segment, its operating margin (if disclosed) \
and whether it is improving/stable/declining. Use the EXACT segment names from the \
10-K segment footnote (these get matched to revenue-mix data).
- operating_leverage: fixed vs variable cost split; incremental margin on growth.

THEME E — innovation & trend positioning:
- rd_pipeline: active R&D projects, timeline to revenue, R&D as % of revenue, productivity.
- trend_positioning: leader / follower / laggard on the key industry trends.
- new_product_launches: recent launches with early traction / growth contribution.
- disruption_risk: technologies/models that could displace the revenue base + the response.
- acquisition_strategy: M&A pattern — capability vs scale acquisition, integration.

Return the structured object. Use the filing's own language and figures."""


def extract_business_ab(
    ticker: str, company: str = "", raw_10k_text: str = "",
    *, force: bool = False,
) -> Optional[Dict[str, Any]]:
    """Run the structured extraction against the 10-K text and cache it.

    Reads the cache unless ``force=True``. Returns the extracted dict (or None
    on any failure). Intended to be called once from the Stage-4 extraction node
    with the 10-K text already in hand."""
    if not force:
        cached = cached_business_ab(ticker)
        if cached is not None:
            return cached
    text = (raw_10k_text or "").strip()
    if not text:
        return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.prompts import ChatPromptTemplate
        from config import MODEL_NAME
    except Exception:
        return None
    try:
        llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=0.1)
        structured = llm.with_structured_output(BusinessAB)
        prompt = ChatPromptTemplate.from_template(
            _PROMPT + "\n\n=== 10-K EXCERPT ===\n{text}")
        # Cap the text to keep the call bounded.
        result: BusinessAB = (prompt | structured).invoke({
            "company": company or ticker, "ticker": ticker,
            "text": text[:120000],
        })
        data = result.model_dump()
        _write_cache(ticker, data)
        return data
    except Exception as exc:
        print(f"  ⚠ business_ab extraction failed for {ticker}: "
              f"{type(exc).__name__}: {exc}")
        return None


__all__ = ["extract_business_ab", "cached_business_ab", "BusinessAB"]
