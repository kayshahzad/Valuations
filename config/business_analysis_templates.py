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


def template_for(sector: str = "", industry: str = "", lifecycle: str = "",
                 business_model: str = "") -> Dict[str, Any]:
    """Pick the best-fit sector template from classification metadata.
    Returns the template dict with its ``key`` added."""
    s = (sector or "").lower()
    i = (industry or "").lower()
    lc = (lifecycle or "").lower()
    bm = (business_model or "").lower()

    def _t(key):
        return {**SECTOR_TEMPLATES[key], "key": key}

    # Order matters — most specific first.
    if any(k in i for k in ("defense", "aerospace")) or \
       ("government" in i or "govt" in i):
        return _t("defense_govt")
    if "bank" in i or "capital markets" in i or "insurance" in i or \
       bm in ("ddm_required", "embedded_value_required") or s == "financial services":
        return _t("financials")
    if s == "healthcare" and any(k in i for k in ("drug", "pharma", "biotech")):
        return _t("pharma")
    if any(k in i for k in ("drug manufacturers", "biotechnology")):
        return _t("pharma")
    if s == "technology" and "software" in i:
        return _t("tech_saas")
    if s == "energy" or "oil" in i or "gas" in i:
        return _t("energy")
    if s in ("consumer defensive", "consumer cyclical") or "consumer" in s:
        return _t("consumer")
    if s in ("industrials", "basic materials") or lc == "cyclical_industrial":
        return _t("industrial")
    return _t("default")


__all__ = ["SECTOR_TEMPLATES", "template_for"]
