"""Sector-specific emphasis templates for the bottom-up business analysis (§4).

The same six themes (A–F) weight differently across business types — a defense
contractor lives or dies on its recompete cycle, a pharma on its pipeline, a
SaaS on NRR. Each template names the **priority dimensions** (which of the
bottom-up coverage rows matter most) and the sector-specific **watch items** the
memo should foreground. Deterministic config; no LLM.

`priority_dimensions` reference the exact coverage-row labels used in
``aletheia.tools.business_analysis._COVERAGE`` so the block can flag them.
"""

from __future__ import annotations

from typing import Any, Dict

# key -> {label, emphasis[], priority_dimensions[]}
SECTOR_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "defense_govt": {
        "label": "Defense / government services",
        "emphasis": ["Contract portfolio & recompete cycle", "Government budget exposure",
                     "Prime vs subcontractor mix", "Backlog / book-to-bill"],
        "priority_dimensions": ["Major customers / contracts", "Regulatory trajectory",
                                 "Organic vs M&A", "Acquisition strategy"],
    },
    "pharma": {
        "label": "Pharma / biotech",
        "emphasis": ["Pipeline & trial outcomes", "Patent cliffs",
                     "Mechanism of action / differentiation", "Regulatory (FDA) calendar"],
        "priority_dimensions": ["Product / service portfolio", "Disruption / R&D posture",
                                 "Regulatory trajectory", "New product launches"],
    },
    "tech_saas": {
        "label": "Technology / SaaS",
        "emphasis": ["TAM & penetration", "Net revenue retention",
                     "Sales efficiency (CAC payback)", "Product cadence"],
        "priority_dimensions": ["TAM sizing", "Market share / position",
                                 "CAC / LTV / cohorts", "New product launches"],
    },
    "industrial": {
        "label": "Industrial / cyclical",
        "emphasis": ["Capacity utilization", "Cycle position",
                     "End-market mix", "Input / commodity costs"],
        "priority_dimensions": ["Margin trajectory by segment", "Operating leverage",
                                 "Competitive intensity", "Lifecycle stage"],
    },
    "financials": {
        "label": "Financials / banks",
        "emphasis": ["Loan book & credit quality", "Capital & NIM",
                     "Deposit franchise", "Regulatory capital"],
        "priority_dimensions": ["Market share / position", "Regulatory trajectory",
                                 "Competitive intensity"],
    },
    "consumer": {
        "label": "Consumer / brand",
        "emphasis": ["Brand strength", "Geographic mix",
                     "Channel dynamics", "Pricing power"],
        "priority_dimensions": ["Product / service portfolio", "Distribution channels",
                                 "Market share / position", "Margin trajectory by segment"],
    },
    "energy": {
        "label": "Energy / commodities",
        "emphasis": ["Reserves & production trajectory", "Cost-curve position",
                     "Commodity price exposure", "Capital intensity"],
        "priority_dimensions": ["Operating leverage", "Margin trajectory by segment",
                                 "Organic vs M&A"],
    },
    "default": {
        "label": "General",
        "emphasis": ["Revenue drivers", "Market position",
                     "Growth source (organic vs M&A)", "Competitive dynamics"],
        "priority_dimensions": ["Product / service portfolio", "TAM sizing",
                                 "Organic vs M&A", "Competitive intensity"],
    },
}


# ── Peer-group resolver ─────────────────────────────────────────────────────
# The raw FMP sector/industry is too coarse and sometimes wrong (e.g. LDOS is a
# defense/government-IT contractor but is filed under Technology / "Information
# Technology Services"). A normalized PEER GROUP is used uniformly for the
# sector template AND the market-growth reference so the same — correct — peer
# set drives both. Curated overrides handle the cases keyword inference can't.

PEER_GROUP_OVERRIDES: Dict[str, str] = {
    # Government / defense IT & primes (filed under Technology or Industrials).
    "LDOS": "defense_govt", "SAIC": "defense_govt", "CACI": "defense_govt",
    "BAH": "defense_govt", "LMT": "defense_govt", "RTX": "defense_govt",
    "GD": "defense_govt", "NOC": "defense_govt", "LHX": "defense_govt",
}

# Map a (fine) peer group to one of the emphasis templates above.
_GROUP_TO_TEMPLATE = {
    "defense_govt": "defense_govt", "pharma": "pharma", "biotech": "pharma",
    "tech_saas": "tech_saas", "hardware": "tech_saas",
    "semiconductors": "industrial", "industrials": "industrial",
    "auto": "industrial", "utilities": "industrial", "materials": "energy",
    "financials": "financials", "banks": "financials", "insurance": "financials",
    "consumer": "consumer", "energy": "energy", "healthcare": "default",
    "default": "default",
}


def peer_group_for(ticker: str = "", sector: str = "", industry: str = "",
                   lifecycle: str = "", business_model: str = "") -> str:
    """Normalized peer group from classification metadata. Override table first,
    then keyword inference, then a sector fallback — so every ticker gets a
    consistent group for both the template and the market-growth reference."""
    if ticker and ticker.upper() in PEER_GROUP_OVERRIDES:
        return PEER_GROUP_OVERRIDES[ticker.upper()]
    s = (sector or "").lower()
    i = (industry or "").lower()
    lc = (lifecycle or "").lower()
    bm = (business_model or "").lower()

    if any(k in i for k in ("defense", "aerospace")) or "government" in i or "govt" in i:
        return "defense_govt"
    if "bank" in i or "capital markets" in i:
        return "banks"
    if "insurance" in i:
        return "insurance"
    if bm in ("ddm_required", "embedded_value_required") or s == "financial services":
        return "financials"
    if any(k in i for k in ("drug manufacturers", "pharmaceutical")):
        return "pharma"
    if "biotech" in i:
        return "biotech"
    if "semiconductor" in i or s == "semiconductors":
        return "semiconductors"
    if s == "technology" and "software" in i:
        return "tech_saas"
    if s == "technology":
        return "hardware"
    if s == "energy" or "oil" in i or "gas" in i:
        return "energy"
    if s == "basic materials":
        return "materials"
    if "auto" in s or "auto" in i:
        return "auto"
    if s == "utilities":
        return "utilities"
    if s in ("consumer defensive", "consumer cyclical") or "consumer" in s:
        return "consumer"
    if s == "industrials" or lc == "cyclical_industrial":
        return "industrials"
    if s in ("healthcare", "healthcare plans"):
        return "healthcare"
    return "default"


def template_for(ticker: str = "", sector: str = "", industry: str = "",
                 lifecycle: str = "", business_model: str = "") -> Dict[str, Any]:
    """Best-fit emphasis template via the normalized peer group. Returns the
    template dict with its ``key`` (template) and ``peer_group`` added."""
    group = peer_group_for(ticker, sector, industry, lifecycle, business_model)
    tkey = _GROUP_TO_TEMPLATE.get(group, "default")
    return {**SECTOR_TEMPLATES[tkey], "key": tkey, "peer_group": group}


__all__ = ["SECTOR_TEMPLATES", "template_for", "peer_group_for",
           "PEER_GROUP_OVERRIDES"]
