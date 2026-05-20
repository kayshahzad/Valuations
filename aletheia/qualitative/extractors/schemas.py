"""Pydantic schemas for the LLM-augmented extraction bundle.

Phase B.1 of the qualitative-tab wiring. One LLM call produces a
``QualitativeExtractionBundle`` containing structured outputs for all
three LLM_AUGMENTED catalog dimensions:

  - ``competitor_identification`` — named competitors + competitive
    intensity, scored 1-7 against the competitive-position rubric
  - ``regulatory_exposure`` — material regulatory/legal exposures,
    scored 1-7 (lower = higher risk, per catalog convention)
  - ``customer_concentration`` — named customers + concentration
    disclosure, scored 1-7 (lower = more concentrated/riskier)

Scoring convention follows the catalog's 1=worst / 7=best framework.
For risk dimensions (regulatory, customer concentration), "worst"
means high risk; "best" means low risk. Same direction as
`technology_disruption_risk` in the catalog (which has the
counterintuitive but documented "lower scores = higher risk"
convention).

All narrative fields cap at 500 chars to match the
``qualitative_assessments.narrative`` DB column constraint enforced
by ``upsert_qualitative_assessment``.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ── competitor_identification ──────────────────────────────────────


class CompetitorExtraction(BaseModel):
    """Named competitors + competitive intensity for the company's
    primary business lines.

    Source text: 10-K Item 1, "Competition" subsection (most filers
    use this exact heading). Some filers (especially financial /
    diversified holdings) distribute competitor discussion across
    business-segment subsections — extractor should aggregate.

    Score rubric (1-7, higher = better competitive position):
      1 — Brutal commodity competition; many fungible competitors;
          no pricing power; vague "we have no competition" claim is
          actually a red flag (suggests the filer can't articulate
          its position)
      4 — Recognizable competitor set with differentiation; modest
          competitive intensity
      7 — De-facto monopoly / dominant network position; competitors
          named but not material threats
    """
    model_config = {"frozen": True}

    score: int = Field(
        ..., ge=1, le=7,
        description="1-7 competitive-position score; higher = stronger position",
    )
    narrative: str = Field(
        ..., max_length=500,
        description=(
            "2-4 sentences describing the competitive landscape, citing "
            "specific competitors and the company's positioning vs them."
        ),
    )
    named_competitors: List[str] = Field(
        default_factory=list,
        description=(
            "Competitor names as disclosed in Item 1. Aggregated across "
            "business segments. Empty list when the filer claims no "
            "material competition (a flag, not absence of competition)."
        ),
    )
    competitive_intensity: Literal["low", "medium", "high"] = Field(
        ...,
        description=(
            "Subjective intensity: low = de-facto monopoly or strong moat; "
            "medium = recognizable competitors with differentiation; "
            "high = commodity / many fungible competitors."
        ),
    )


# ── regulatory_exposure ────────────────────────────────────────────


class RegulatoryExposureItem(BaseModel):
    """One material regulatory or legal exposure surfaced in Item 1A."""
    model_config = {"frozen": True}

    regulator: str = Field(
        ..., max_length=120,
        description=(
            "Regulator, court, or regulatory body name "
            "(e.g. 'FTC', 'DOJ Antitrust Division', 'European Commission', "
            "'SEC', 'FDA', 'state attorneys general')."
        ),
    )
    area: str = Field(
        ..., max_length=200,
        description=(
            "Domain: antitrust, ESG/environmental, privacy/data, "
            "tax, labor, sector-specific (e.g. drug approval, banking "
            "capital), geopolitical (export controls / sanctions)."
        ),
    )
    severity: Literal["low", "medium", "high"] = Field(
        ...,
        description=(
            "Material severity per filer disclosure: high = ongoing "
            "investigation/litigation with material financial exposure; "
            "medium = active regulatory regime requiring compliance "
            "spend; low = routine compliance, no near-term action."
        ),
    )


class RegulatoryExtraction(BaseModel):
    """Material regulatory + legal exposures disclosed in 10-K Item 1A.

    Score rubric (1-7, **higher = lower risk**, per catalog
    convention matching technology_disruption_risk):
      1 — Multiple high-severity exposures (active antitrust + ESG +
          litigation); large near-term financial risk
      4 — Active regulatory regime requiring compliance spend; no
          near-term acute threat
      7 — Minimal regulatory exposure; routine compliance only
    """
    model_config = {"frozen": True}

    score: int = Field(
        ..., ge=1, le=7,
        description="1-7 score; higher = lower regulatory risk",
    )
    narrative: str = Field(
        ..., max_length=500,
        description=(
            "2-4 sentences summarizing the most material regulatory + "
            "legal exposures and their potential financial impact."
        ),
    )
    material_exposures: List[RegulatoryExposureItem] = Field(
        default_factory=list,
        description=(
            "Specific exposures the filer disclosed. Empty list ONLY "
            "when Item 1A truly contains no material regulatory risk "
            "(rare — most large filers have multiple)."
        ),
    )


# ── customer_concentration ─────────────────────────────────────────


class NamedCustomer(BaseModel):
    """One customer the filer named with material revenue share."""
    model_config = {"frozen": True}

    name: str = Field(
        ..., max_length=120,
        description=(
            "Customer name as disclosed (e.g. 'Apple Inc.', "
            "'U.S. Government', 'a major hyperscaler customer')."
        ),
    )
    revenue_share_pct: Optional[float] = Field(
        None, ge=0.0, le=100.0,
        description=(
            "Disclosed share of revenue (0-100). None when the filer "
            "named the customer but didn't quantify the share, OR when "
            "they only said 'no single customer exceeded 10%'."
        ),
    )


class CustomerExtraction(BaseModel):
    """Customer concentration disclosure from the 10-K.

    Most filers state one of three patterns:
      1. "No single customer accounted for more than 10% of revenue"
         → ``concentration_disclosed=False``, ``named_customers=[]``
      2. Named customer(s) with share ("Apple Inc. accounted for 22%")
         → ``concentration_disclosed=True``, named_customers populated
         with shares
      3. Named customer(s) without share ("our largest customer is X")
         → ``concentration_disclosed=True``, ``revenue_share_pct=None``

    Score rubric (1-7, **higher = better diversification**, same
    convention as regulatory_exposure):
      1 — One customer >50% of revenue; existential dependency
      4 — Top customer 10-25%; manageable but watchworthy
      7 — Well diversified; no customer >10%; affirmed via filer
          disclosure language
    """
    model_config = {"frozen": True}

    score: int = Field(
        ..., ge=1, le=7,
        description="1-7 score; higher = better diversification",
    )
    narrative: str = Field(
        ..., max_length=500,
        description=(
            "2-4 sentences summarizing customer-base concentration as "
            "disclosed. Cite the filer's specific language."
        ),
    )
    concentration_disclosed: bool = Field(
        ...,
        description=(
            "True when filer named one or more material customers "
            "(disclosure-positive). False when filer affirmed 'no "
            "single customer >10%' (disclosure-negative). Distinct "
            "from 'no disclosure at all' (extractor returns None score)."
        ),
    )
    named_customers: List[NamedCustomer] = Field(
        default_factory=list,
        description=(
            "Customers named by the filer with their disclosed share "
            "(or None when share isn't quantified)."
        ),
    )


# ── Bundle (top-level output of the extraction agent) ──────────────


class QualitativeExtractionBundle(BaseModel):
    """Top-level structured output — all three LLM_AUGMENTED dims in
    one call.

    Schema enforced via LangChain's ``with_structured_output(bundle)``
    so a single Gemini round-trip populates all three sections.
    Validation failures retry once (2-attempt budget mirrors
    qualitative_synthesis_agent / thesis_synthesizer); after the
    second failure, the extraction node persists whatever it got with
    a `reason` field documenting the failure for analyst review.
    """
    model_config = {"frozen": True}

    competitor_identification: CompetitorExtraction = Field(
        ..., description="Named competitors + competitive intensity",
    )
    regulatory_exposure: RegulatoryExtraction = Field(
        ..., description="Material regulatory + legal exposures",
    )
    customer_concentration: CustomerExtraction = Field(
        ..., description="Customer-base concentration disclosure",
    )


__all__ = [
    "CompetitorExtraction",
    "RegulatoryExtraction",
    "RegulatoryExposureItem",
    "CustomerExtraction",
    "NamedCustomer",
    "QualitativeExtractionBundle",
]
