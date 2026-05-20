"""Pydantic schemas for the DEF 14A extraction bundle.

Phase C of the qualitative-tab wiring. One LLM call against the proxy
statement (DEF 14A) produces a ``Def14aExtractionBundle`` covering:

  - ``management_tenure_continuity`` — CEO + board tenure, recent
    turnover, succession risk
  - ``management_alignment`` — insider ownership, equity-vs-cash comp
    structure, performance metrics in comp plans

Both dimensions follow the catalog's 1=worst / 7=best convention.
For these dims, "best" means high continuity / high alignment.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ── management_tenure_continuity ───────────────────────────────────


class DirectorTenureItem(BaseModel):
    """One director or named officer the filer surfaced with a
    tenure datum. ``years_tenure`` is None when the proxy didn't
    state it explicitly."""
    model_config = {"frozen": True}

    name: str = Field(
        ..., max_length=120,
        description=(
            "Person's name as disclosed in the proxy statement "
            "(e.g. 'Timothy D. Cook', 'Arthur D. Levinson'). Use the "
            "exact spelling from the filing."
        ),
    )
    role: str = Field(
        ..., max_length=120,
        description=(
            "Position: 'CEO', 'CFO', 'Chair', 'Lead Independent "
            "Director', 'Director', etc."
        ),
    )
    years_tenure: Optional[int] = Field(
        None, ge=0, le=80,
        description=(
            "Years in current role (or on the board for directors). "
            "None when the proxy doesn't quantify tenure explicitly — "
            "common for newer directors."
        ),
    )


class TenureContinuityExtraction(BaseModel):
    """CEO + board tenure picture from the proxy statement.

    Score rubric (1-7, higher = better continuity):
      1 — Very recent CEO replacement OR multiple executive
          departures in the last 2 years; activist-driven board
          turnover; succession plan ambiguous
      4 — Tenured CEO (3-7 years); normal director rotation;
          succession plan disclosed but not battle-tested
      7 — Long-tenured CEO (>10 years) OR strong founder/family-led
          continuity; very low director turnover; documented +
          recently-rehearsed succession plan
    """
    model_config = {"frozen": True}

    score: int = Field(
        ..., ge=1, le=7,
        description="1-7 score; higher = better continuity",
    )
    narrative: str = Field(
        ..., max_length=500,
        description=(
            "2-4 sentences summarizing the CEO/board tenure picture, "
            "recent turnover events, and succession-plan disclosure."
        ),
    )
    ceo_name: Optional[str] = Field(
        None, max_length=120,
        description="Current CEO's full name as disclosed.",
    )
    ceo_years_tenure: Optional[int] = Field(
        None, ge=0, le=80,
        description=(
            "Years the current CEO has held the role. None when the "
            "proxy doesn't quantify (e.g. recently promoted, biographical "
            "section says 'appointed in 2019' but doesn't compute the "
            "tenure)."
        ),
    )
    median_director_tenure_years: Optional[float] = Field(
        None, ge=0.0, le=80.0,
        description=(
            "Median years on the board across non-management directors. "
            "Most filers disclose individual tenures and a tenure "
            "histogram — estimate the median from those when present."
        ),
    )
    recent_turnover_events: List[str] = Field(
        default_factory=list,
        description=(
            "Notable executive/director departures or activist-driven "
            "board changes in the last 2 years. Each entry: brief "
            "description (e.g. 'CFO departed Q2 2024, replaced by "
            "interim from Big Four firm')."
        ),
    )
    notable_directors: List[DirectorTenureItem] = Field(
        default_factory=list,
        description=(
            "Up to ~5 directors the analyst should know about — "
            "long-tenured founders, lead independent director, audit "
            "chair, recent additions. Not every director — just the "
            "noteworthy ones for tenure analysis."
        ),
    )


# ── management_alignment ───────────────────────────────────────────


class CompPlanComponent(BaseModel):
    """One component of the CEO/named-officer compensation plan."""
    model_config = {"frozen": True}

    component: Literal["base_salary", "annual_bonus", "stock_options",
                       "restricted_stock", "performance_shares",
                       "other_long_term", "other"] = Field(
        ...,
        description=(
            "Standardized comp component category. Map proxy "
            "language to one of these — e.g. 'PSUs' or 'PRSUs' both "
            "map to 'performance_shares'."
        ),
    )
    weight_pct: Optional[float] = Field(
        None, ge=0.0, le=100.0,
        description=(
            "Approximate share of total target compensation (0-100). "
            "None when the proxy reports values but not percentages "
            "(common — convert to % using Summary Compensation Table "
            "totals when feasible, else leave None)."
        ),
    )


class AlignmentExtraction(BaseModel):
    """Insider ownership + compensation alignment picture.

    Score rubric (1-7, higher = better alignment):
      1 — Minimal insider ownership (<0.5% of company); cash-heavy
          compensation (>60% cash); no performance metrics tied to
          long-term shareholder value
      4 — Modest insider ownership (~1-2%); standard comp mix
          (40% cash / 60% equity); performance metrics present but
          short-term focused (1-year EPS, revenue)
      7 — High insider ownership (>5% of company OR founder still
          holds >10%); equity-heavy comp (>70% equity, mostly
          performance shares); rigorous multi-year TSR / ROIC /
          FCF metrics tied to vesting
    """
    model_config = {"frozen": True}

    score: int = Field(
        ..., ge=1, le=7,
        description="1-7 score; higher = better shareholder alignment",
    )
    narrative: str = Field(
        ..., max_length=500,
        description=(
            "2-4 sentences summarizing insider ownership, comp "
            "structure, and the rigor of performance metrics."
        ),
    )
    ceo_ownership_pct: Optional[float] = Field(
        None, ge=0.0, le=100.0,
        description=(
            "CEO's beneficial ownership as % of total shares outstanding. "
            "From the Beneficial Ownership table. Includes options "
            "exercisable within 60 days per SEC convention. None when "
            "the table reports share count but not %."
        ),
    )
    insider_ownership_pct: Optional[float] = Field(
        None, ge=0.0, le=100.0,
        description=(
            "Aggregate beneficial ownership of all directors + named "
            "executive officers as % of total shares outstanding. "
            "Usually disclosed as a 'directors and officers as a "
            "group' row in the ownership table."
        ),
    )
    comp_structure: List[CompPlanComponent] = Field(
        default_factory=list,
        description=(
            "CEO comp plan components with approximate weights. "
            "Sourced from the Compensation Discussion & Analysis "
            "(CD&A) section + Summary Compensation Table."
        ),
    )
    performance_metrics: List[str] = Field(
        default_factory=list,
        description=(
            "Performance metrics that determine vesting of equity "
            "comp (e.g. 'Relative TSR vs S&P 500', '3-year cumulative "
            "FCF', 'ROIC target'). Each entry: the metric name as "
            "disclosed plus measurement period if stated."
        ),
    )


# ── Bundle ─────────────────────────────────────────────────────────


class Def14aExtractionBundle(BaseModel):
    """Top-level Phase C extraction — both management dims from a
    single DEF 14A read.

    Validation failures retry once (2-attempt budget mirrors the
    Phase B bundle); after the second failure, the extraction node
    persists no-data rows with the failure reason for analyst
    review.
    """
    model_config = {"frozen": True}

    management_tenure_continuity: TenureContinuityExtraction = Field(
        ..., description="CEO + board tenure, turnover, succession",
    )
    management_alignment: AlignmentExtraction = Field(
        ..., description="Insider ownership + comp alignment",
    )


__all__ = [
    "DirectorTenureItem",
    "TenureContinuityExtraction",
    "CompPlanComponent",
    "AlignmentExtraction",
    "Def14aExtractionBundle",
]
