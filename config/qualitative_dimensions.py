"""Qualitative-analysis catalog — code-as-truth.

The 19 dimensions covering quality, capital allocation, competitive position,
risk, and management. Sub-question weights, score anchors, formula citations,
and bucket cutoffs all live here, reviewed via PR with mandatory analyst
sign-off on meaning-altering changes (see `.github/pull_request_template.md`).

Why catalog-as-code:
  - Catalog evolution is principled — git log is the audit trail
  - Type checking enforces weight-sums-to-1.0 and required fields at import
  - The localStorage draft key includes the catalog hash so prior drafts
    cleanly invalidate when questions or weights change
  - Score-anchors render directly into the assessment dialog so the analyst
    sees the same definitions every time

Counts:
  - Quality:              5 (moat, ROIIC, pricing, brand, switching)
  - Capital Allocation:   4 (track record, reinvestment, buyback, dividend)
  - Competitive Position: 4 (market pos, competitor ID, industry conc., trajectory)
  - Risk:                 4 (regulatory, tech disruption, customer conc., cyclicality)
  - Management:           2 (tenure, alignment) — both pending DEF 14A ingestion
  Total: 19

Source-category mix (week 1):
  - DETERMINISTIC: 4  (ROIIC trend, buyback discipline, dividend policy, cyclicality)
  - HITL:          9
  - LLM_AUGMENTED: 3  (competitor identification, regulatory exposure, customer concentration)
  - PENDING_DATA:  3  (industry concentration, tenure & continuity, alignment with shareholders)

The PENDING_DATA dimensions are deliberately distinct from "not yet assessed":
they're slots whose data infrastructure is not yet wired (proxy filings or
sector concentration benchmarks). Surfacing them as "data infra pending"
rather than asking the analyst to manually fill them keeps the framework's
categorization honest.
"""

from __future__ import annotations

from typing import Dict

from aletheia.qualitative.types import (
    QualitativeDimension,
    SourceCategory,
    SubQuestion,
)


# ─────────────────────────────────────────────────────────────────────────────
# Score-anchor templates — reused across HITL dimensions for calibration.
# Specific dimensions can override individual anchors but the 1/4/7 frame
# (poor / average / exceptional) holds universally.
# ─────────────────────────────────────────────────────────────────────────────
def _anchor(low: str, mid: str, high: str) -> Dict[int, str]:
    return {1: low, 4: mid, 7: high}


# ─────────────────────────────────────────────────────────────────────────────
# Quality — 5 dimensions
# ─────────────────────────────────────────────────────────────────────────────

MOAT_STRENGTH = QualitativeDimension(
    id="moat_strength",
    category="quality",
    title="Moat Strength",
    source_category=SourceCategory.HITL,
    staleness_days=365,  # moats erode slowly; annual review sufficient
    description=(
        "Composite assessment of structural competitive advantages — "
        "switching costs, network effects, cost advantages, intangibles, "
        "and efficient scale. Per Pat Dorsey's 'Five Sources' framework."
    ),
    questions=(
        SubQuestion(
            id="switching_costs",
            text="How costly — financially, operationally, or psychologically — is it for the company's customers to leave for a competitor?",
            weight=0.30,
            score_anchors=_anchor(
                "Customers churn easily; nothing keeps them but inertia.",
                "Some friction (data migration, retraining), but switchable within a quarter.",
                "Switching is structurally hard or expensive — multi-year integration, regulatory recertification, or sunk-cost data lock-in.",
            ),
        ),
        SubQuestion(
            id="network_effects",
            text="Does the product or platform get more valuable as more customers use it?",
            weight=0.25,
            score_anchors=_anchor(
                "No network effect; product value is independent of user count.",
                "Modest two-sided dynamics or community effects, but not central to the moat.",
                "Strong, durable network effects — users would lose meaningful value by switching to a smaller network.",
            ),
        ),
        SubQuestion(
            id="cost_advantage",
            text="Does the company have a sustainable cost advantage from scale, location, captive resources, or proprietary process?",
            weight=0.20,
            score_anchors=_anchor(
                "Costs in line with peers; no structural edge.",
                "Some scale advantages, but replicable by larger entrants.",
                "Durable structural cost edge — irreplicable scale, captive low-cost inputs, or patented process.",
            ),
        ),
        SubQuestion(
            id="intangibles",
            text="Do brands, patents, regulatory licenses, or other intangibles create meaningful barriers to entry?",
            weight=0.15,
            score_anchors=_anchor(
                "No meaningful intangible barriers; pure execution-driven business.",
                "Some brand or IP value, but commoditizable over a 5-10y horizon.",
                "Intangibles are the primary moat (premium brand, blocking patents, scarce regulatory approvals) and durable for >10y.",
            ),
        ),
        SubQuestion(
            id="efficient_scale",
            text="Is the addressable market small enough that additional rational entrants would destroy returns for everyone?",
            weight=0.10,
            score_anchors=_anchor(
                "Market is large and fragmented; new entry is constant.",
                "Market is moderately concentrated; rational competitors mostly stay disciplined.",
                "Market is structurally constrained (geographic monopoly, single-supplier customer, regulatory limits) and entry is irrational.",
            ),
        ),
    ),
)

ROIIC_TREND = QualitativeDimension(
    id="roiic_trend",
    category="quality",
    title="ROIIC Trend",
    source_category=SourceCategory.DETERMINISTIC,
    staleness_days=180,  # quarterly-ish — refreshed when a new fiscal year is cleaned
    description=(
        "Return on Incremental Invested Capital over a rolling 5-year window. "
        "Score combines level (median ROIIC) and trajectory (linear-regression "
        "slope) into a 1-7 bucket. High-quality compounders show 20%+ ROIIC "
        "sustained without decay."
    ),
    formula_citation=(
        "Damodaran convention: ROIIC_t = (NOPAT_t − NOPAT_t-1) / "
        "(InvestedCapital_t − InvestedCapital_t-1). Buckets in "
        "aletheia.qualitative.computers.roiic_trend (locked at code_version=1)."
    ),
    code_version=1,
)

PRICING_POWER = QualitativeDimension(
    id="pricing_power",
    category="quality",
    title="Pricing Power",
    source_category=SourceCategory.HITL,
    staleness_days=365,
    description=(
        "Ability to raise prices above inflation without losing volume. "
        "The single most reliable signal of moat width per Buffett — "
        "tested across input-cost cycles and competitive responses."
    ),
    questions=(
        SubQuestion(
            id="real_price_increases",
            text="Has the company raised prices above inflation over the past 5 years without meaningful volume loss?",
            weight=0.40,
            score_anchors=_anchor(
                "Prices have declined in real terms; pricing decisions are reactive to competition.",
                "Modest above-inflation increases, but only in line with input-cost pass-through.",
                "Consistent above-inflation price increases beyond input pass-through, with stable or growing volumes.",
            ),
        ),
        SubQuestion(
            id="margin_stability",
            text="Are gross margins stable or expanding through input-cost volatility?",
            weight=0.25,
            score_anchors=_anchor(
                "Margins compress sharply when input costs rise; commodity-like behavior.",
                "Margins recover within 2-3 quarters of input-cost shocks.",
                "Margins are stable or expanding regardless of input-cost regime — pricing flows through fully and quickly.",
            ),
        ),
        SubQuestion(
            id="customer_price_sensitivity",
            text="How price-sensitive are customers? Are they making small purchases relative to total cost-of-ownership, or buying necessities?",
            weight=0.20,
            score_anchors=_anchor(
                "Customers are highly price-sensitive; switching is easy and frequent.",
                "Some price sensitivity, but offset by switching costs or convenience.",
                "Customers are price-takers — purchase is small share of TCO, mission-critical, or non-discretionary.",
            ),
        ),
        SubQuestion(
            id="contractual_escalators",
            text="Does the company's pricing model include built-in escalators (auto-renewal at higher rates, indexation, recurring revenue terms)?",
            weight=0.15,
            score_anchors=_anchor(
                "Pricing is per-transaction with no escalation; renegotiation is per-deal.",
                "Some contractual escalators (CPI-indexation, multi-year terms) but not universal.",
                "Pricing model has structural escalators; price increases happen automatically without renegotiation friction.",
            ),
        ),
    ),
)

BRAND_STRENGTH = QualitativeDimension(
    id="brand_strength",
    category="quality",
    title="Brand Strength",
    source_category=SourceCategory.HITL,  # deterministic ranking deferred until brand-data source acquired
    staleness_days=365,
    description=(
        "Strength of the brand as a moat — recognition, margin premium, "
        "resilience to product missteps, durability across generational cohorts. "
        "Originally proposed as hybrid; deterministic ranking component "
        "deferred until a licensed brand-value data source is acquired."
    ),
    questions=(
        SubQuestion(
            id="recognition",
            text="In the company's primary category, is the brand top-of-mind for typical customers?",
            weight=0.35,
            score_anchors=_anchor(
                "Brand is unknown or generic; customers select on price/spec.",
                "Brand is recognized but not preferred over alternatives.",
                "Brand is the default choice — synonymous with the category.",
            ),
        ),
        SubQuestion(
            id="margin_premium",
            text="Do customers pay a meaningful premium for this brand vs commodity-equivalent products?",
            weight=0.25,
            score_anchors=_anchor(
                "No discernible premium; sells at or below industry average price.",
                "Modest premium (~5-15%) attributable to brand.",
                "Substantial premium (>20%) sustained over time even as functional alternatives exist.",
            ),
        ),
        SubQuestion(
            id="resilience",
            text="Has the brand survived prior product missteps, controversies, or competitive incursions without lasting damage?",
            weight=0.25,
            score_anchors=_anchor(
                "Brand is fragile — single product failure or controversy could permanently damage it.",
                "Brand has survived past missteps but visible scars remain.",
                "Brand has weathered major missteps with full recovery; resilience is part of the moat.",
            ),
        ),
        SubQuestion(
            id="generational_durability",
            text="Is the brand strengthening or weakening with younger demographic cohorts?",
            weight=0.15,
            score_anchors=_anchor(
                "Brand is fading — younger cohorts prefer alternatives.",
                "Brand is stable across generations but not gaining mindshare.",
                "Brand is gaining strength with younger cohorts; multi-generational durability is intact.",
            ),
        ),
    ),
)

SWITCHING_COSTS = QualitativeDimension(
    id="switching_costs",
    category="quality",
    title="Switching Costs",
    source_category=SourceCategory.HITL,
    staleness_days=365,
    description=(
        "Friction faced by customers to leave for an alternative. "
        "Distinct from moat_strength.switching_costs sub-question: "
        "this dimension goes deeper into the structural mechanics."
    ),
    questions=(
        SubQuestion(
            id="process_integration",
            text="How deeply embedded is the product in customer workflows or operations?",
            weight=0.30,
            score_anchors=_anchor(
                "Product sits at the periphery; switching is a one-day project.",
                "Product is integrated into core workflows; switching is a multi-month migration.",
                "Product is the operational nervous system; switching is a multi-year, board-approved transformation.",
            ),
        ),
        SubQuestion(
            id="data_lock_in",
            text="Does switching require migrating accumulated data, configurations, or learned models?",
            weight=0.25,
            score_anchors=_anchor(
                "Data is portable and well-documented; export tools exist and work.",
                "Data is partially exportable but loses meaningful context in transit.",
                "Data accumulation is the product — years of customer-specific state cannot be meaningfully recreated elsewhere.",
            ),
        ),
        SubQuestion(
            id="peer_lock_in",
            text="Do customers' peers, partners, or counterparties also use this product, increasing the cost of unilateral switching?",
            weight=0.20,
            score_anchors=_anchor(
                "Customer can switch without affecting external relationships.",
                "Some peer-network effects but switching does not break collaboration.",
                "Customer's ecosystem (suppliers, partners, regulators) is also on the platform; unilateral switching is operationally untenable.",
            ),
        ),
        SubQuestion(
            id="retraining_cost",
            text="How significant is the human-capital retraining required to switch products?",
            weight=0.15,
            score_anchors=_anchor(
                "Minimal retraining; alternatives have similar UX.",
                "Several weeks of retraining for power users.",
                "Specialized expertise built over years; retraining is a multi-quarter productivity hit.",
            ),
        ),
        SubQuestion(
            id="contract_length",
            text="Are multi-year commitments standard in the customer base?",
            weight=0.10,
            score_anchors=_anchor(
                "Pay-as-you-go or annual contracts; customers can leave any time.",
                "Multi-year contracts common but not universal.",
                "Long-term commitments (3+ years) are standard with auto-renewal default.",
            ),
        ),
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Capital Allocation — 4 dimensions
# ─────────────────────────────────────────────────────────────────────────────

CAPITAL_ALLOCATION_TRACK_RECORD = QualitativeDimension(
    id="capital_allocation_track_record",
    category="capital_allocation",
    title="Capital Allocation Track Record",
    source_category=SourceCategory.HITL,
    staleness_days=180,
    description=(
        "Composite assessment of management's historical capital decisions: "
        "reinvestment ROIIC, M&A discipline, buyback timing, dividend "
        "sustainability, leverage management. Per Mauboussin's 'CFO Capital "
        "Allocation' framework."
    ),
    questions=(
        SubQuestion(
            id="reinvestment_roiic",
            text="Has reinvested capital (capex, R&D, working capital growth) earned returns above the cost of capital over rolling 5-year periods?",
            weight=0.25,
            score_anchors=_anchor(
                "Reinvestment ROIIC consistently below cost of capital — value-destroying growth.",
                "Reinvestment ROIIC roughly matches cost of capital — growth is value-neutral.",
                "Reinvestment ROIIC sustained at 2x+ cost of capital across cycles.",
            ),
        ),
        SubQuestion(
            id="acquisition_discipline",
            text="Have past M&A deals created or destroyed shareholder value, accounting for goodwill impairments and integration costs?",
            weight=0.20,
            score_anchors=_anchor(
                "Track record of overpaying — multiple goodwill impairments or destroyed-value deals.",
                "Mixed — some good deals, some bad; net roughly neutral.",
                "Disciplined — most deals priced below intrinsic value; few or no impairments.",
            ),
        ),
        SubQuestion(
            id="buyback_timing",
            text="Are buybacks weighted toward periods of relative undervaluation, or do they peak at peak multiples?",
            weight=0.20,
            score_anchors=_anchor(
                "Buybacks peak at peak multiples — destroys value via mistimed repurchases.",
                "Buybacks roughly average across the cycle — neither smart nor stupid.",
                "Buybacks accelerate at low multiples and slow at peaks — disciplined countercyclical timing.",
            ),
        ),
        SubQuestion(
            id="dividend_sustainability",
            text="Has the dividend grown sustainably without straining the business through downturns?",
            weight=0.20,
            score_anchors=_anchor(
                "Dividend cuts during downturns; payout ratio routinely exceeds free cash flow.",
                "Dividend has been maintained but not consistently grown.",
                "Dividend has grown for 10+ years without straining the business in any single year.",
            ),
        ),
        SubQuestion(
            id="leverage_management",
            text="Is leverage managed conservatively through cycles, with capacity to invest at the bottom?",
            weight=0.15,
            score_anchors=_anchor(
                "Leverage is consistently high; refinancing risk and pro-cyclical balance sheet.",
                "Moderate leverage; some headroom but not optimized for downturns.",
                "Conservative leverage with explicit dry-powder for counter-cyclical investment.",
            ),
        ),
    ),
)

REINVESTMENT_OPPORTUNITY = QualitativeDimension(
    id="reinvestment_opportunity",
    category="capital_allocation",
    title="Reinvestment Opportunity",
    source_category=SourceCategory.HITL,
    staleness_days=180,
    description=(
        "Quality of the reinvestment runway — TAM headroom, incremental "
        "ROIIC potential, and pace at which capital can be deployed at "
        "attractive returns. The 'optionality' lens on the cap-allocation "
        "stack — distinct from track record (backward-looking)."
    ),
    questions=(
        SubQuestion(
            id="tam_headroom",
            text="Is the addressable market large enough to support 5+ years of above-GDP growth without saturation?",
            weight=0.40,
            score_anchors=_anchor(
                "Saturated or near-saturated market; future growth must come from share gains.",
                "TAM supports moderate growth for 3-5 years before saturation pressures.",
                "TAM is multiples of current revenue; runway extends a decade or more.",
            ),
        ),
        SubQuestion(
            id="incremental_roiic",
            text="Can incremental capital plausibly earn returns above the cost of capital, given the company's competitive position?",
            weight=0.30,
            score_anchors=_anchor(
                "Marginal investments earn below cost of capital — diminishing returns evident.",
                "Marginal investments earn roughly cost of capital — growth-for-growth's-sake risk.",
                "Marginal investments retain healthy spreads to cost of capital — moat scales with size.",
            ),
        ),
        SubQuestion(
            id="deployment_pace",
            text="Is the company able to deploy capital fast enough to fund stated growth, without forcing low-quality investments?",
            weight=0.30,
            score_anchors=_anchor(
                "Either too-slow (cash piles up) or too-fast (forced into low-ROIIC deals) — pacing is misaligned.",
                "Reasonable pace, but occasional pressure to deploy creates marginal-quality decisions.",
                "Capital deployment is well-paced — investments scaled to genuine opportunity, not artificial growth targets.",
            ),
        ),
    ),
)

BUYBACK_DISCIPLINE = QualitativeDimension(
    id="buyback_discipline",
    category="capital_allocation",
    title="Buyback Discipline",
    source_category=SourceCategory.DETERMINISTIC,
    staleness_days=180,
    description=(
        "Were buybacks weighted toward periods of relative undervaluation, "
        "or did they accelerate at peak multiples? Score combines the "
        "EV/EBITDA percentile at which net buybacks were executed across "
        "the past 5 fiscal years."
    ),
    formula_citation=(
        "buyback_discipline_v1: weighted_avg(EV_EBITDA_at_buyback_year) / "
        "median_5y_EV_EBITDA. <0.85 → top quartile; >1.15 → bottom quartile. "
        "See aletheia.qualitative.computers.buyback_discipline."
    ),
    code_version=1,
)

DIVIDEND_POLICY = QualitativeDimension(
    id="dividend_policy",
    category="capital_allocation",
    title="Dividend Policy",
    source_category=SourceCategory.DETERMINISTIC,
    staleness_days=180,
    description=(
        "Sustainability and growth quality of the dividend — payout ratio, "
        "growth rate, coverage by free cash flow, and continuity through "
        "downturns. Score blends consistency and headroom."
    ),
    formula_citation=(
        "dividend_policy_v1: combines (a) 5y dividend CAGR, (b) median "
        "FCF coverage ratio, (c) consecutive growth years. See "
        "aletheia.qualitative.computers.dividend_policy."
    ),
    code_version=1,
)


# ─────────────────────────────────────────────────────────────────────────────
# Competitive Position — 4 dimensions
# ─────────────────────────────────────────────────────────────────────────────

MARKET_POSITION = QualitativeDimension(
    id="market_position",
    category="competitive",
    title="Market Position",
    source_category=SourceCategory.HITL,
    staleness_days=180,
    description=(
        "Where the company sits in its primary market — share, share "
        "trajectory, competitive intensity, and geographic dispersion."
    ),
    questions=(
        SubQuestion(
            id="market_share",
            text="What is the company's share rank in its primary product category?",
            weight=0.30,
            score_anchors=_anchor(
                "Outside top 10; subscale player.",
                "Top 5 but not market leader; structural #2 or #3 with disadvantages.",
                "Clear market leader (#1 or co-leader) with structural scale advantages over followers.",
            ),
        ),
        SubQuestion(
            id="share_trajectory",
            text="Is the company gaining, holding, or losing share over the past 3-5 years?",
            weight=0.25,
            score_anchors=_anchor(
                "Losing share to multiple competitors over 3+ years.",
                "Holding share roughly steady.",
                "Gaining share consistently from credible competitors over 3+ years.",
            ),
        ),
        SubQuestion(
            id="competitive_intensity",
            text="Is the competitive structure rational (oligopoly with stable shares and pricing discipline) or zero-sum?",
            weight=0.25,
            score_anchors=_anchor(
                "Hyper-competitive — pricing wars, share volatility, low or negative industry returns.",
                "Mixed — some rational segments, some battlegrounds.",
                "Stable rational oligopoly — pricing discipline holds, shares are durable, industry returns are healthy.",
            ),
        ),
        SubQuestion(
            id="geographic_dispersion",
            text="Is the business geographically diversified, or concentrated in markets with correlated risk?",
            weight=0.20,
            score_anchors=_anchor(
                "Single-country or single-region exposure with no realistic geographic offset.",
                "Diversified across 2-3 major regions but with significant concentration in one.",
                "Genuinely global, with no single region exceeding 40% of revenue or earnings.",
            ),
        ),
    ),
)

COMPETITOR_IDENTIFICATION = QualitativeDimension(
    id="competitor_identification",
    category="competitive",
    title="Competitor Identification",
    source_category=SourceCategory.LLM_AUGMENTED,
    staleness_days=90,  # competitor landscape shifts fast — quarterly review
    description=(
        "Structured list of named competitors per business line, extracted "
        "from 10-K Item 1 'Competition' sections. Deferred to week 4-6 "
        "when LLM extraction pipeline is built."
    ),
)

INDUSTRY_CONCENTRATION = QualitativeDimension(
    id="industry_concentration",
    category="competitive",
    title="Industry Concentration",
    source_category=SourceCategory.DETERMINISTIC,
    staleness_days=180,
    description=(
        "Top-3 cohort share across the in-universe sector peers (MVP "
        "proxy for real industry HHI). Higher score = more concentrated "
        "= structural moats / pricing power for the leader. Caveat: "
        "the universe-cohort proxy degrades for tickers in small "
        "single-ticker sectors; the narrative documents the limitation."
    ),
    formula_citation=(
        "industry_concentration_v1: top-N share of in-universe sector "
        "cohort (N=3 for cohorts ≥4; N=2 for cohorts of 2-3; N=1 "
        "trivially for cohort of 1). See "
        "aletheia.qualitative.computers.industry_concentration."
    ),
    code_version=1,
)

COMPETITIVE_TRAJECTORY = QualitativeDimension(
    id="competitive_trajectory",
    category="competitive",
    title="Competitive Trajectory",
    source_category=SourceCategory.HITL,
    staleness_days=180,
    description=(
        "Whether the company is structurally gaining or losing competitive "
        "ground — innovation pace, customer migration, disruption posture, "
        "and competitor responses."
    ),
    questions=(
        SubQuestion(
            id="innovation_pace",
            text="Is the company's product/feature innovation pace ahead of, in line with, or behind its main competitors?",
            weight=0.35,
            score_anchors=_anchor(
                "Following — competitors set the agenda; the company plays catch-up.",
                "Parity — innovation pace matches competitors but does not lead.",
                "Leading — competitors react to this company's moves rather than the reverse.",
            ),
        ),
        SubQuestion(
            id="customer_migration",
            text="Net customer wins from competitors over the past 3 years — is the direction-of-flow clearly favorable?",
            weight=0.25,
            score_anchors=_anchor(
                "Net losing customers to competitors; churn analysis shows structural disadvantage.",
                "Roughly balanced — wins and losses cancel.",
                "Net winning meaningful share from competitors year-over-year.",
            ),
        ),
        SubQuestion(
            id="disruption_posture",
            text="Is the company defending an existing margin pool, or actively building the next one?",
            weight=0.25,
            score_anchors=_anchor(
                "Defending only — significant resources go to protecting legacy revenue while disruption emerges.",
                "Doing both at modest scale — defending while seeding new bets.",
                "Actively building the next margin pool while defending the current one — strategic optionality is well-funded.",
            ),
        ),
        SubQuestion(
            id="competitor_responses",
            text="Are competitors copying this company's strategy (validation) or diverging (it may be wrong)?",
            weight=0.15,
            score_anchors=_anchor(
                "Competitors are diverging in directions that look more promising — strategic uncertainty.",
                "Mixed — some copy, some diverge.",
                "Competitors are clearly imitating this company's strategy — strong validation signal.",
            ),
        ),
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Risk — 4 dimensions
# ─────────────────────────────────────────────────────────────────────────────

REGULATORY_EXPOSURE = QualitativeDimension(
    id="regulatory_exposure",
    category="risk",
    title="Regulatory Exposure",
    source_category=SourceCategory.LLM_AUGMENTED,
    staleness_days=90,
    description=(
        "Material regulatory and legal exposure — antitrust, sector-specific "
        "agencies, ESG/environmental, tax, geopolitical. Extracted from 10-K "
        "Item 1A risk factors. Deferred to week 4-6."
    ),
)

TECHNOLOGY_DISRUPTION_RISK = QualitativeDimension(
    id="technology_disruption_risk",
    category="risk",
    title="Technology Disruption Risk",
    source_category=SourceCategory.HITL,
    staleness_days=180,
    description=(
        "Vulnerability of the business model to AI, platform shifts, "
        "or step-function technology changes. Lower scores indicate higher "
        "disruption risk — counterintuitive but consistent with the "
        "1=worst / 7=best framework."
    ),
    questions=(
        SubQuestion(
            id="tech_vulnerability",
            text="How exposed is the business model to AI, platform shifts, or step-function technology changes?",
            weight=0.35,
            score_anchors=_anchor(
                "Existential — the core value proposition can be eliminated by current AI/platform shifts.",
                "Material — material parts of the business will need to be rebuilt; outcome uncertain.",
                "Minimal — physical/regulatory/scale moats insulate the business from foreseeable tech disruption.",
            ),
        ),
        SubQuestion(
            id="adaptation_track_record",
            text="Has the company successfully adapted to prior major tech transitions (e.g., mobile, cloud, mainframe-to-PC)?",
            weight=0.25,
            score_anchors=_anchor(
                "Failed prior transitions — was disrupted from a stronger position.",
                "Adapted partially — survived but lost ground.",
                "Adapted successfully across multiple prior transitions; institutional capability for change is proven.",
            ),
        ),
        SubQuestion(
            id="rd_commitment",
            text="Is R&D spending sufficient relative to peers, given the disruption risk faced?",
            weight=0.25,
            score_anchors=_anchor(
                "R&D is sub-scale relative to disruption risk — under-investing in optionality.",
                "R&D is in line with peers but not aggressive given the threat profile.",
                "R&D is well above peer average and explicitly funds disruption-defense / next-platform optionality.",
            ),
        ),
        SubQuestion(
            id="optionality",
            text="Are there nascent products or platforms within the company that could plausibly replace incumbent revenue if it's disrupted?",
            weight=0.15,
            score_anchors=_anchor(
                "No visible Plan B — full reliance on incumbent revenue stream.",
                "Some bets in flight but at sub-scale; replacement potential uncertain.",
                "Multiple credible bets with line-of-sight to replacing incumbent revenue if disrupted.",
            ),
        ),
    ),
)

CUSTOMER_CONCENTRATION = QualitativeDimension(
    id="customer_concentration",
    category="risk",
    title="Customer Concentration",
    source_category=SourceCategory.LLM_AUGMENTED,
    staleness_days=180,
    description=(
        "Material customer concentration disclosed in 10-K — typically "
        "stated as 'no single customer accounted for more than 10% of "
        "revenue' or, where present, named top customers and their share. "
        "Deferred to week 4-6; LLM extraction needed because this is "
        "almost always narrative, not structured."
    ),
)

CYCLICALITY = QualitativeDimension(
    id="cyclicality",
    category="risk",
    title="Cyclicality",
    source_category=SourceCategory.DETERMINISTIC,
    staleness_days=180,
    description=(
        "Revenue cyclicality measured via z-score of revenue growth around "
        "the long-term trend. Already computed in `cyclicality_z_score`; "
        "this dimension just maps that score into the 1-7 framework."
    ),
    formula_citation=(
        "cyclicality_v1: maps existing `derived_revenue_z_score` (or "
        "`is_cyclical_peak` flag) into 1-7 buckets. See "
        "aletheia.qualitative.computers.cyclicality."
    ),
    code_version=1,
)


# ─────────────────────────────────────────────────────────────────────────────
# Management — 2 dimensions (both PENDING_DATA — DEF 14A proxy not yet wired)
# ─────────────────────────────────────────────────────────────────────────────

TENURE_CONTINUITY = QualitativeDimension(
    id="management_tenure_continuity",
    category="management",
    title="Tenure & Continuity",
    source_category=SourceCategory.LLM_AUGMENTED,
    staleness_days=180,
    description=(
        "CEO and senior-team tenure, board composition, and continuity "
        "through transitions. Extracted from the latest DEF 14A proxy "
        "statement via the Phase C extraction bundle (Gemini, one LLM "
        "call covers both management dims)."
    ),
)

ALIGNMENT_WITH_SHAREHOLDERS = QualitativeDimension(
    id="management_alignment",
    category="management",
    title="Alignment with Shareholders",
    source_category=SourceCategory.LLM_AUGMENTED,
    staleness_days=180,
    description=(
        "Insider ownership, compensation structure (equity vs cash, "
        "performance metrics), and historical actions consistent with "
        "long-term ownership. Extracted from the latest DEF 14A proxy "
        "statement via the Phase C extraction bundle."
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Catalog registry — single source of truth for the rest of the codebase.
# Order within each category is intentional; the UI renders top-to-bottom.
# ─────────────────────────────────────────────────────────────────────────────

DIMENSIONS: Dict[str, QualitativeDimension] = {
    d.id: d for d in (
        # Quality
        MOAT_STRENGTH,
        ROIIC_TREND,
        PRICING_POWER,
        BRAND_STRENGTH,
        SWITCHING_COSTS,
        # Capital Allocation
        CAPITAL_ALLOCATION_TRACK_RECORD,
        REINVESTMENT_OPPORTUNITY,
        BUYBACK_DISCIPLINE,
        DIVIDEND_POLICY,
        # Competitive Position
        MARKET_POSITION,
        COMPETITOR_IDENTIFICATION,
        INDUSTRY_CONCENTRATION,
        COMPETITIVE_TRAJECTORY,
        # Risk
        REGULATORY_EXPOSURE,
        TECHNOLOGY_DISRUPTION_RISK,
        CUSTOMER_CONCENTRATION,
        CYCLICALITY,
        # Management
        TENURE_CONTINUITY,
        ALIGNMENT_WITH_SHAREHOLDERS,
    )
}


# Category ordering for the UI tab. Each entry is (category_id, display_label).
CATEGORIES = (
    ("quality",            "Quality"),
    ("capital_allocation", "Capital Allocation"),
    ("competitive",        "Competitive Position"),
    ("risk",               "Risk"),
    ("management",         "Management"),
)


# Category composite weights — equal-weighted within each category for week 1.
# Future refinement: catalog can declare per-dimension weights for the
# composite (e.g. moat_strength weighted higher within Quality).
def category_composite_weights(category: str) -> Dict[str, float]:
    """Return {dimension_id: weight} for the category composite.

    Equal-weighted across all dimensions in the category for week 1.
    PENDING_DATA dimensions are excluded from the composite — they don't
    contribute to or detract from the category score until their data
    infrastructure ships."""
    members = [
        d for d in DIMENSIONS.values()
        if d.category == category
        and d.source_category != SourceCategory.PENDING_DATA
    ]
    if not members:
        return {}
    w = 1.0 / len(members)
    return {d.id: w for d in members}


__all__ = ["DIMENSIONS", "CATEGORIES", "category_composite_weights"]
