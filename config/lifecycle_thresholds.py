from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List

class Stage(str, Enum):
    STARTUP                     = "startup"
    HYPERGROWTH                 = "hypergrowth"
    GROWTH_COMPOUNDER           = "growth_compounder"
    GROWTH_COMPOUNDER_SOFTWARE  = "growth_compounder_software"
    GROWTH_COMPOUNDER_CONSUMER  = "growth_compounder_consumer"
    GROWTH_COMPOUNDER_PHARMA    = "growth_compounder_pharma"
    MATURE                      = "mature"
    DECLINING                   = "declining"

STAGE_LABELS = {
    Stage.STARTUP:                    "Startup / Pre-revenue",
    Stage.HYPERGROWTH:                "Hypergrowth (Scaling, Pre-profit)",
    Stage.GROWTH_COMPOUNDER:          "Growth Compounder (Profitable, Reinvesting)",
    Stage.GROWTH_COMPOUNDER_SOFTWARE: "Growth Compounder — Software / Cloud",
    Stage.GROWTH_COMPOUNDER_CONSUMER: "Growth Compounder — Consumer / Retail",
    Stage.GROWTH_COMPOUNDER_PHARMA:   "Growth Compounder — Pharma / Healthcare",
    Stage.MATURE:                     "Mature Cash Cow",
    Stage.DECLINING:                  "Declining / Turnaround",
}

@dataclass
class StageThresholds:
    stage: Stage
    primary_lens: str = ""
    roic_applicable: bool = True
    fcf_margin_applicable: bool = True
    gross_margin_primary: bool = False
    nrr_primary: bool = False
    p2_roic_weight:    float = 0.40
    p2_fcf_weight:     float = 0.35
    p2_debt_weight:    float = 0.25
    cagr_strong:   float = 0.20
    cagr_good:     float = 0.12
    cagr_moderate: float = 0.07
    cagr_slow:     float = 0.03
    mos_strong:    float = 0.30
    mos_good:      float = 0.15
    roic_exceptional: float = 0.25
    roic_strong:      float = 0.08
    roic_modest:      float = 0.02
    fcf_excellent: float = 0.20
    fcf_strong:    float = 0.12
    fcf_adequate:  float = 0.05
    primary_metrics: List[str] = field(default_factory=list)
    secondary_metrics: List[str] = field(default_factory=list)
    not_applicable: List[str] = field(default_factory=list)
    requires_unit_economics: bool = False
    requires_runway_check: bool = False
    requires_restructuring_thesis: bool = False
    wacc_adjustment_bps: int = 0

STAGE_THRESHOLDS: Dict[Stage, StageThresholds] = {
    Stage.STARTUP: StageThresholds(
        stage=Stage.STARTUP,
        primary_lens="Real options + team quality + TAM size + technology differentiation",
        roic_applicable=False,
        fcf_margin_applicable=False,
        gross_margin_primary=True,
        p2_roic_weight=0.0,
        p2_fcf_weight=0.30,
        p2_debt_weight=0.70,
        cagr_strong=0.60,
        cagr_good=0.40,
        cagr_moderate=0.20,
        cagr_slow=0.10,
        primary_metrics=["gross_margin_pct", "revenue_cagr_3y", "net_debt_bn", "data_quality"],
        secondary_metrics=["ebitda_bn"],
        not_applicable=["roic", "fcf_margin_pct", "sbc_pct_fcf", "beneish_m_score", "sloan_accrual_ratio"],
        requires_unit_economics=True,
        requires_runway_check=True,
        wacc_adjustment_bps=200,
    ),
    Stage.HYPERGROWTH: StageThresholds(
        stage=Stage.HYPERGROWTH,
        primary_lens="Unit economics + revenue quality + market share trajectory",
        roic_applicable=False,
        fcf_margin_applicable=False,
        gross_margin_primary=True,
        nrr_primary=True,
        p2_roic_weight=0.0,
        p2_fcf_weight=0.20,
        p2_debt_weight=0.80,
        cagr_strong=0.40,
        cagr_good=0.25,
        cagr_moderate=0.15,
        cagr_slow=0.08,
        mos_strong=0.50,
        mos_good=0.30,
        primary_metrics=["gross_margin_pct", "revenue_cagr_3y", "implied_cagr", "historical_cagr"],
        secondary_metrics=["fcf_bn", "ebitda_bn"],
        not_applicable=["roic", "fcf_margin_pct", "beneish_m_score"],
        requires_unit_economics=True,
        wacc_adjustment_bps=100,
    ),
    Stage.GROWTH_COMPOUNDER: StageThresholds(
        stage=Stage.GROWTH_COMPOUNDER,
        primary_lens="ROIC vs WACC (core signal) + reinvestment efficiency",
        roic_applicable=True,
        fcf_margin_applicable=True,
        p2_roic_weight=0.40,
        p2_fcf_weight=0.35,
        p2_debt_weight=0.25,
        cagr_strong=0.20,
        cagr_good=0.12,
        cagr_moderate=0.07,
        cagr_slow=0.03,
        primary_metrics=["roic", "derived_ROIC", "fcf_margin_pct", "revenue_cagr", "ev_ebitda", "justified_ev_ebitda"],
        secondary_metrics=["gross_margin_pct", "sbc_pct_fcf", "net_debt_bn"],
        not_applicable=[],
        wacc_adjustment_bps=0,
    ),
    # Growth-compounder sub-profiles inherit the parent stage's scoring
    # buckets (cagr_*, fcf_*, etc.) — those govern conviction-pillar
    # thresholds and are about quality discrimination, not DCF projection.
    # The DCF projection differentiation lives in LIFECYCLE_PROFILES.
    Stage.GROWTH_COMPOUNDER_SOFTWARE: StageThresholds(
        stage=Stage.GROWTH_COMPOUNDER_SOFTWARE,
        primary_lens="ROIC vs WACC + reinvestment efficiency (software/cloud)",
        roic_applicable=True,
        fcf_margin_applicable=True,
        p2_roic_weight=0.40,
        p2_fcf_weight=0.35,
        p2_debt_weight=0.25,
        cagr_strong=0.20,
        cagr_good=0.12,
        cagr_moderate=0.07,
        cagr_slow=0.03,
        primary_metrics=["roic", "derived_ROIC", "fcf_margin_pct", "revenue_cagr",
                         "ev_ebitda", "justified_ev_ebitda"],
        secondary_metrics=["gross_margin_pct", "sbc_pct_fcf", "net_debt_bn"],
        not_applicable=[],
        wacc_adjustment_bps=0,
    ),
    Stage.GROWTH_COMPOUNDER_CONSUMER: StageThresholds(
        stage=Stage.GROWTH_COMPOUNDER_CONSUMER,
        primary_lens="ROIC vs WACC + reinvestment efficiency (consumer/retail)",
        roic_applicable=True,
        fcf_margin_applicable=True,
        p2_roic_weight=0.40,
        p2_fcf_weight=0.35,
        p2_debt_weight=0.25,
        cagr_strong=0.20,
        cagr_good=0.12,
        cagr_moderate=0.07,
        cagr_slow=0.03,
        primary_metrics=["roic", "derived_ROIC", "fcf_margin_pct", "revenue_cagr",
                         "ev_ebitda", "justified_ev_ebitda"],
        secondary_metrics=["gross_margin_pct", "sbc_pct_fcf", "net_debt_bn"],
        not_applicable=[],
        wacc_adjustment_bps=0,
    ),
    Stage.GROWTH_COMPOUNDER_PHARMA: StageThresholds(
        stage=Stage.GROWTH_COMPOUNDER_PHARMA,
        primary_lens="ROIC vs WACC + reinvestment efficiency (pharma/healthcare)",
        roic_applicable=True,
        fcf_margin_applicable=True,
        p2_roic_weight=0.40,
        p2_fcf_weight=0.35,
        p2_debt_weight=0.25,
        cagr_strong=0.20,
        cagr_good=0.12,
        cagr_moderate=0.07,
        cagr_slow=0.03,
        primary_metrics=["roic", "derived_ROIC", "fcf_margin_pct", "revenue_cagr",
                         "ev_ebitda", "justified_ev_ebitda"],
        secondary_metrics=["gross_margin_pct", "sbc_pct_fcf", "net_debt_bn"],
        not_applicable=[],
        wacc_adjustment_bps=0,
    ),
    Stage.MATURE: StageThresholds(
        stage=Stage.MATURE,
        primary_lens="FCF yield + capital return quality + moat durability",
        roic_applicable=True,
        fcf_margin_applicable=True,
        p2_roic_weight=0.25,
        p2_fcf_weight=0.50,
        p2_debt_weight=0.25,
        cagr_strong=0.10,
        cagr_good=0.06,
        cagr_moderate=0.03,
        cagr_slow=0.01,
        fcf_excellent=0.15,
        fcf_strong=0.08,
        fcf_adequate=0.03,
        primary_metrics=["fcf_margin_pct", "fcf_bn", "div_yield", "net_debt_bn", "roic"],
        secondary_metrics=["revenue_cagr", "ebitda_bn"],
        not_applicable=[],
        wacc_adjustment_bps=0,
    ),
    Stage.DECLINING: StageThresholds(
        stage=Stage.DECLINING,
        primary_lens="Asset value + debt coverage + turnaround probability",
        roic_applicable=True,
        fcf_margin_applicable=True,
        p2_roic_weight=0.0,
        p2_fcf_weight=0.50,
        p2_debt_weight=0.50,
        cagr_strong=0.05,
        cagr_good=0.0,
        cagr_moderate=-0.05,
        cagr_slow=-0.10,
        mos_strong=0.50,
        mos_good=0.35,
        primary_metrics=["net_debt_bn", "fcf_bn", "fcf_margin_pct", "book_value_per_share"],
        secondary_metrics=["revenue_cagr", "ebitda_bn"],
        not_applicable=["roic"],
        requires_restructuring_thesis=True,
        wacc_adjustment_bps=300,
    ),
}
