"""Prompt templates for the qualitative-extraction bundle.

Phase B.1. The consolidated three-dim prompt produces a
``QualitativeExtractionBundle`` from the combined Item 1 + Item 1A
text of the latest 10-K. Following the existing qualitative_synthesis
prompt convention: explicit rubrics, scoring direction reminders,
concrete examples of acceptable output depth.

When per-dim prompts are needed in future phases (DEF 14A, etc.) they
go in this module too, named ``<dim_id>_PROMPT``.
"""

from __future__ import annotations


# Combined Item 1 + Item 1A → all three LLM_AUGMENTED dims in one
# structured response.
#
# Notes on style choices:
#   - Score direction stated explicitly per dim because two of the
#     three dims use the "lower = worse risk" convention which is
#     counterintuitive.
#   - Specific examples for `concentration_disclosed=True/False` so
#     the model handles the three common disclosure patterns
#     correctly (named-with-share, named-without-share, affirmed-no-
#     concentration).
#   - Empty-list-as-flag warning for competitors — filers who claim
#     no competition should get a LOW score, not a None or a score of 7.
#   - 2-4 sentence depth requirement (less than qualitative_synthesis
#     since these are structured-field extractions, not narrative).
BUNDLE_PROMPT = """
You are the Qualitative Extraction Agent for {ticker}. From the 10-K
text below, produce STRUCTURED extractions for THREE dimensions in a
single response. The 10-K text covers Item 1 (Business / Competition)
AND Item 1A (Risk Factors) — read both, populate all three dims.

══════════════════════════════════════════════════════════════
SCORING CONVENTION (read carefully — direction is non-obvious for two dims)
══════════════════════════════════════════════════════════════
All scores are 1-7 integers. Higher = better outcome for the company.

  competitor_identification.score:
    1 — Brutal commodity competition; many fungible competitors; or
        the filer claims "no competition" (which is a red flag, not
        a strength — score it LOW)
    4 — Recognizable competitor set with differentiation
    7 — De-facto monopoly / dominant network position

  regulatory_exposure.score:
    1 — Multiple HIGH-severity exposures (active litigation,
        antitrust review, material near-term financial risk)
    4 — Active regulatory regime requiring compliance spend
    7 — Minimal exposure; routine compliance only

  customer_concentration.score:
    1 — One customer >50% of revenue (existential dependency)
    4 — Top customer 10-25% of revenue
    7 — Well diversified; affirmed "no single customer >10%"

══════════════════════════════════════════════════════════════
COMPETITOR_IDENTIFICATION — extract from Item 1 "Competition" subsection
══════════════════════════════════════════════════════════════
Required fields:
  - score (1-7, per rubric above)
  - narrative (2-4 sentences citing specific competitors and the
    company's positioning vs them — 100-500 chars)
  - named_competitors (list of competitor names AS DISCLOSED in Item 1;
    aggregated across business segments; preserve filer's exact names)
  - competitive_intensity ("low" / "medium" / "high")

CRITICAL: if the filer claims "we have no competition" or similar,
score LOW (1-3), NOT high. The absence of named competitors is a
disclosure red flag, not a competitive advantage. Set
named_competitors=[] AND explain in the narrative.

══════════════════════════════════════════════════════════════
REGULATORY_EXPOSURE — extract from Item 1A "Risk Factors"
══════════════════════════════════════════════════════════════
Required fields:
  - score (1-7, per rubric above — HIGHER = LOWER RISK)
  - narrative (2-4 sentences summarizing the most material regulatory
    + legal exposures and their potential impact — 100-500 chars)
  - material_exposures: list of {{regulator, area, severity}} where:
      regulator: name of the agency/court/body (e.g. "FTC", "DOJ",
        "European Commission", "SEC", "FDA", "state AGs")
      area: domain (antitrust, ESG/environmental, privacy/data, tax,
        labor, sector-specific, geopolitical/sanctions)
      severity: "low" / "medium" / "high"

Most large filers will have AT LEAST one material exposure. Empty
list is rare and only legitimate when Item 1A truly contains no
regulatory/legal risk language.

══════════════════════════════════════════════════════════════
CUSTOMER_CONCENTRATION — search Item 1 + Item 1A for customer disclosure
══════════════════════════════════════════════════════════════
Required fields:
  - score (1-7, per rubric above — HIGHER = BETTER DIVERSIFICATION)
  - narrative (2-4 sentences quoting or paraphrasing the filer's
    specific concentration language — 100-500 chars)
  - concentration_disclosed (boolean: see patterns below)
  - named_customers: list of {{name, revenue_share_pct}}

Three common disclosure patterns and how to handle them:

  Pattern 1: "No single customer accounted for more than 10% of
  revenue" (or similar affirmation):
      concentration_disclosed = False
      named_customers = []
      score: 6-7 (well diversified)

  Pattern 2: Named customer WITH share (e.g. "Apple Inc. accounted
  for 22% of revenue"):
      concentration_disclosed = True
      named_customers = [{{name: "Apple Inc.", revenue_share_pct: 22.0}}]
      score: 1-4 depending on magnitude

  Pattern 3: Named customer WITHOUT share (e.g. "our largest
  customer is the U.S. Government"):
      concentration_disclosed = True
      named_customers = [{{name: "U.S. Government", revenue_share_pct: null}}]
      score: 2-5 (riskier than diversified, less precise than Pattern 2)

══════════════════════════════════════════════════════════════
10-K SOURCE TEXT
══════════════════════════════════════════════════════════════
{source_text}

══════════════════════════════════════════════════════════════
OUTPUT
══════════════════════════════════════════════════════════════
Return the complete QualitativeExtractionBundle as structured output.
All three dimensions are required — partial output will be rejected.
"""


DEF14A_BUNDLE_PROMPT = """
You are the Proxy Statement Extraction Agent for {ticker}. From the
DEF 14A excerpt below, produce STRUCTURED extractions for TWO
dimensions in a single response. The proxy text is truncated to its
first ~80K chars where the board structure, ownership tables, and
Compensation Discussion & Analysis (CD&A) summary live.

══════════════════════════════════════════════════════════════
SCORING CONVENTION
══════════════════════════════════════════════════════════════
All scores are 1-7 integers. Higher = better outcome for shareholders.

  management_tenure_continuity.score (higher = more continuity):
    1 — Very recent CEO replacement; multiple executive departures
        in last 2 years; activist-driven board turnover; ambiguous
        succession plan
    4 — Tenured CEO (3-7 years); normal director rotation;
        succession plan disclosed but not battle-tested
    7 — Long-tenured CEO (>10 years) OR strong founder/family-led
        continuity; very low director turnover; documented +
        recently-rehearsed succession

  management_alignment.score (higher = better alignment):
    1 — Minimal insider ownership (<0.5%); cash-heavy comp (>60%
        cash); no performance metrics tied to long-term value
    4 — Modest insider ownership (~1-2%); standard 40/60 cash/equity
        comp; short-term performance metrics (1-yr EPS, revenue)
    7 — High insider ownership (>5% OR founder >10%); equity-heavy
        comp (>70% equity, mostly performance shares); rigorous
        multi-year TSR/ROIC/FCF metrics tied to vesting

══════════════════════════════════════════════════════════════
TENURE_CONTINUITY — extract from board/governance sections
══════════════════════════════════════════════════════════════
Required fields:
  - score (1-7, per rubric above)
  - narrative (2-4 sentences summarizing the picture)
  - ceo_name (current CEO full name as disclosed)
  - ceo_years_tenure (years as CEO; None if not disclosed)
  - median_director_tenure_years (median tenure across non-management
    directors; estimate from individual tenures + histograms)
  - recent_turnover_events (notable departures in last 2 years)
  - notable_directors (up to 5 — long-tenured founders, lead
    independent director, audit chair, recent additions)

Tenure data is usually in:
  - "Directors and Director Nominees" or "Board of Directors" section
  - Director biographical paragraphs
  - Tenure histograms / board composition tables
  - "Executive Officers" section (for CEO + named officers)

══════════════════════════════════════════════════════════════
ALIGNMENT — extract from ownership tables + CD&A
══════════════════════════════════════════════════════════════
Required fields:
  - score (1-7, per rubric above)
  - narrative (2-4 sentences summarizing ownership + comp)
  - ceo_ownership_pct (CEO beneficial ownership % from ownership table)
  - insider_ownership_pct (directors + NEOs aggregate % — usually a
    "all directors and officers as a group" row in the table)
  - comp_structure (CEO comp components with approximate weights;
    map proxy language to standardized categories: base_salary,
    annual_bonus, stock_options, restricted_stock,
    performance_shares, other_long_term, other)
  - performance_metrics (metrics that determine equity-comp vesting:
    e.g. "Relative TSR vs S&P 500", "3-year cumulative FCF",
    "ROIC target". Include measurement period when stated.)

Ownership data is usually in:
  - "Security Ownership of Certain Beneficial Owners and Management"
    table (most filers' Item 12)
  - CEO comp details are in the CD&A and Summary Compensation Table

══════════════════════════════════════════════════════════════
HANDLING MISSING DATA
══════════════════════════════════════════════════════════════
The truncated proxy excerpt may not contain all the data you need
(some filers put ownership tables late in the document; CD&A can
span 30+ pages). When a specific field is genuinely absent from the
provided excerpt, set it to None / empty list rather than guessing.
The empty-state is more useful than a fabricated value.

══════════════════════════════════════════════════════════════
DEF 14A SOURCE TEXT
══════════════════════════════════════════════════════════════
{source_text}

══════════════════════════════════════════════════════════════
OUTPUT
══════════════════════════════════════════════════════════════
Return the complete Def14aExtractionBundle as structured output.
Both dimensions are required.
"""


__all__ = ["BUNDLE_PROMPT", "DEF14A_BUNDLE_PROMPT"]
