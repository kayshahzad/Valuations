"""
aletheia/tools/lifecycle_classifier.py

Lifecycle Stage Classifier
===========================
Framework Section 14.1 — "One Framework Does Not Fit All"

Classifies a company into one of five lifecycle stages and returns
adjusted screening thresholds and conviction pillar weights for that stage.

The single most common analytical error is applying identical screening
thresholds to companies at fundamentally different lifecycle stages.
A hypergrowth SaaS company correctly fails every profitability screen —
that is the right behaviour for its stage. A mature cash cow correctly
fails every growth screen — that is not a reason to avoid it.

Five stages (from framework Table 84):
  STARTUP         — pre-revenue or early revenue, burning cash
  HYPERGROWTH     — scaling, pre-profit, unit economics primary
  GROWTH_COMPOUNDER — profitable, reinvesting, ROIC vs WACC is core signal
  MATURE          — cash cow, FCF yield + capital return quality
  DECLINING       — structural or cyclical decline

Usage:
    from aletheia.tools.lifecycle_classifier import LifecycleClassifier
    clf = LifecycleClassifier()
    result = clf.classify("MSFT", state)
    print(result.summary())

    # Batch from reports
    for ticker in universe:
        result = clf.classify_from_report(ticker)
        print(f"{ticker}: {result.stage} — {result.primary_lens}")
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Any


# ─────────────────────────────────────────────────────────────────────────────
# Stage enum
# ─────────────────────────────────────────────────────────────────────────────

class Stage(str, Enum):
    STARTUP           = "startup"
    HYPERGROWTH       = "hypergrowth"
    GROWTH_COMPOUNDER = "growth_compounder"
    MATURE            = "mature"
    DECLINING         = "declining"


STAGE_LABELS = {
    Stage.STARTUP:           "Startup / Pre-revenue",
    Stage.HYPERGROWTH:       "Hypergrowth (Scaling, Pre-profit)",
    Stage.GROWTH_COMPOUNDER: "Growth Compounder (Profitable, Reinvesting)",
    Stage.MATURE:            "Mature Cash Cow",
    Stage.DECLINING:         "Declining / Turnaround",
}

STAGE_COLORS = {
    Stage.STARTUP:           "purple",
    Stage.HYPERGROWTH:       "blue",
    Stage.GROWTH_COMPOUNDER: "green",
    Stage.MATURE:            "amber",
    Stage.DECLINING:         "red",
}


# ─────────────────────────────────────────────────────────────────────────────
# Threshold adjustments per stage
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StageThresholds:
    """
    Adjusted screening thresholds and conviction pillar weights for a given stage.
    These replace or supplement the standard thresholds defined in Section 4.1.
    """
    stage: Stage

    # Primary valuation lens (from Table 84)
    primary_lens: str = ""

    # Which P2 metrics are primary vs secondary vs not applicable
    roic_applicable: bool = True       # Use ROIC vs WACC in P2
    fcf_margin_applicable: bool = True # Use FCF margin in P2
    gross_margin_primary: bool = False # Gross margin replaces ROIC (hypergrowth)
    nrr_primary: bool = False          # NRR replaces growth screen (SaaS)

    # P2 Pillar weights override (must sum to 1.0)
    p2_roic_weight:    float = 0.40
    p2_fcf_weight:     float = 0.35
    p2_debt_weight:    float = 0.25

    # P3 CAGR thresholds (adjusted for stage)
    cagr_strong:   float = 0.20   # above this → P3 base 5/5
    cagr_good:     float = 0.12   # above this → P3 base 4/5
    cagr_moderate: float = 0.07   # above this → P3 base 3/5
    cagr_slow:     float = 0.03   # above this → P3 base 2/5

    # P4 MoS thresholds (mature companies get stricter gates)
    mos_strong:    float = 0.30   # → P4 5/5
    mos_good:      float = 0.15   # → P4 4/5

    # ROIC thresholds for P2 (hypergrowth companies get looser gates)
    roic_exceptional: float = 0.25  # ROIC-WACC spread → P2 component 5/5
    roic_strong:      float = 0.08  # → 4/5
    roic_modest:      float = 0.02  # → 3/5

    # FCF margin thresholds
    fcf_excellent: float = 0.20   # → FCF component 5/5
    fcf_strong:    float = 0.12   # → 4/5
    fcf_adequate:  float = 0.05   # → 3/5

    # Key metrics to emphasize in the report
    primary_metrics: List[str] = field(default_factory=list)
    secondary_metrics: List[str] = field(default_factory=list)
    not_applicable: List[str] = field(default_factory=list)

    # Special gates
    requires_unit_economics: bool = False   # SaaS/platform
    requires_runway_check: bool = False     # Startup
    requires_restructuring_thesis: bool = False  # Declining

    # WACC adjustment (bps) relative to base
    wacc_adjustment_bps: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Stage threshold definitions — from framework Table 84
# ─────────────────────────────────────────────────────────────────────────────

STAGE_THRESHOLDS: Dict[Stage, StageThresholds] = {

    Stage.STARTUP: StageThresholds(
        stage=Stage.STARTUP,
        primary_lens="Real options + team quality + TAM size + technology differentiation",
        roic_applicable=False,
        fcf_margin_applicable=False,
        gross_margin_primary=True,
        p2_roic_weight=0.0,
        p2_fcf_weight=0.30,
        p2_debt_weight=0.70,  # Runway / survival is the key P2 driver
        cagr_strong=0.60,     # Startups need hypergrowth to score well
        cagr_good=0.40,
        cagr_moderate=0.20,
        cagr_slow=0.10,
        primary_metrics=[
            "gross_margin_pct",
            "revenue_cagr_3y",
            "net_debt_bn",         # Runway proxy
            "data_quality",
        ],
        secondary_metrics=["ebitda_bn"],
        not_applicable=[
            "roic", "fcf_margin_pct", "sbc_pct_fcf",
            "beneish_m_score", "sloan_accrual_ratio",
        ],
        requires_unit_economics=True,
        requires_runway_check=True,
        wacc_adjustment_bps=200,   # Higher uncertainty premium
    ),

    Stage.HYPERGROWTH: StageThresholds(
        stage=Stage.HYPERGROWTH,
        primary_lens="Unit economics + revenue quality + market share trajectory",
        roic_applicable=False,      # ROIC negative by design — not applicable
        fcf_margin_applicable=False, # FCF negative by design — use gross margin
        gross_margin_primary=True,
        nrr_primary=True,
        p2_roic_weight=0.0,
        p2_fcf_weight=0.20,
        p2_debt_weight=0.80,  # Survival and runway are the key P2 signals
        cagr_strong=0.40,     # Hypergrowth companies need high CAGR to score 5/5
        cagr_good=0.25,
        cagr_moderate=0.15,
        cagr_slow=0.08,
        mos_strong=0.50,       # Higher MoS required because terminal value uncertainty is extreme
        mos_good=0.30,
        primary_metrics=[
            "gross_margin_pct",
            "revenue_cagr_3y",
            "implied_cagr",
            "historical_cagr",
        ],
        secondary_metrics=[
            "fcf_bn",              # Informational — not scored
            "ebitda_bn",
        ],
        not_applicable=[
            "roic", "fcf_margin_pct",
            "beneish_m_score",     # Pre-profit companies rarely manipulate earnings
        ],
        requires_unit_economics=True,
        wacc_adjustment_bps=100,   # Growth uncertainty premium
    ),

    Stage.GROWTH_COMPOUNDER: StageThresholds(
        stage=Stage.GROWTH_COMPOUNDER,
        primary_lens="ROIC vs WACC (core signal) + reinvestment efficiency",
        # Standard thresholds — this is where the existing framework is strongest
        roic_applicable=True,
        fcf_margin_applicable=True,
        p2_roic_weight=0.40,
        p2_fcf_weight=0.35,
        p2_debt_weight=0.25,
        cagr_strong=0.20,
        cagr_good=0.12,
        cagr_moderate=0.07,
        cagr_slow=0.03,
        primary_metrics=[
            "roic", "derived_ROIC",
            "fcf_margin_pct",
            "revenue_cagr",
            "ev_ebitda",
            "justified_ev_ebitda",
        ],
        secondary_metrics=[
            "gross_margin_pct",
            "sbc_pct_fcf",
            "net_debt_bn",
        ],
        not_applicable=[],
        wacc_adjustment_bps=0,
    ),

    Stage.MATURE: StageThresholds(
        stage=Stage.MATURE,
        primary_lens="FCF yield + capital return quality + moat durability",
        roic_applicable=True,
        fcf_margin_applicable=True,
        p2_roic_weight=0.25,      # ROIC less important — maintenance is the goal
        p2_fcf_weight=0.50,       # FCF generation is the primary value driver
        p2_debt_weight=0.25,
        cagr_strong=0.10,         # Lower CAGR expectations — P3 calibrated for mature
        cagr_good=0.06,
        cagr_moderate=0.03,
        cagr_slow=0.01,
        fcf_excellent=0.15,       # Mature companies: lower FCF bar still excellent
        fcf_strong=0.08,
        fcf_adequate=0.03,
        primary_metrics=[
            "fcf_margin_pct",
            "fcf_bn",
            "net_debt_bn",
            "roic",
            "base_mos",
        ],
        secondary_metrics=[
            "revenue_cagr",
            "gross_margin_pct",
            "sbc_pct_fcf",
        ],
        not_applicable=[
            "implied_cagr",  # Reverse DCF less meaningful for low-growth mature names
        ],
        wacc_adjustment_bps=-25,  # Mature, stable CF → lower risk premium
    ),

    Stage.DECLINING: StageThresholds(
        stage=Stage.DECLINING,
        primary_lens="Asset value + restructuring optionality + balance sheet survival",
        roic_applicable=True,
        fcf_margin_applicable=True,
        p2_roic_weight=0.20,
        p2_fcf_weight=0.30,
        p2_debt_weight=0.50,  # Survival dominates — debt level is critical
        cagr_strong=0.05,
        cagr_good=0.02,
        cagr_moderate=-0.02,
        cagr_slow=-0.10,
        mos_strong=0.50,      # Deep discount required to compensate for structural risk
        mos_good=0.35,
        primary_metrics=[
            "net_debt_bn",
            "fcf_bn",
            "base_mos",
            "bear_iv",
        ],
        secondary_metrics=[
            "roic",
            "gross_margin_pct",
        ],
        not_applicable=[
            "revenue_cagr",    # Declining revenue is expected — don't penalise growth screens
        ],
        requires_restructuring_thesis=True,
        wacc_adjustment_bps=150,  # Distress premium
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Classification result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ClassificationResult:
    ticker: str
    stage: Stage
    confidence: float          # 0.0–1.0
    thresholds: StageThresholds

    # Input signals used for classification
    revenue_cagr_3y: Optional[float] = None
    revenue_cagr_5y: Optional[float] = None
    fcf_margin: Optional[float] = None
    gross_margin: Optional[float] = None
    roic: Optional[float] = None
    revenue_bn: Optional[float] = None
    is_profitable: bool = False

    # Evidence trail
    signals: List[str] = field(default_factory=list)
    overrides: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return STAGE_LABELS[self.stage]

    @property
    def primary_lens(self) -> str:
        return self.thresholds.primary_lens

    def summary(self) -> str:
        lines = [
            f"\n{'='*60}",
            f"  LIFECYCLE CLASSIFICATION: {self.ticker}",
            f"  Stage: {self.label}",
            f"  Confidence: {self.confidence:.0%}",
            f"  Primary lens: {self.primary_lens}",
            f"{'─'*60}",
            f"  Input signals:",
        ]
        for sig in self.signals:
            lines.append(f"    → {sig}")
        if self.overrides:
            lines.append(f"  Overrides applied:")
            for ov in self.overrides:
                lines.append(f"    ⚠ {ov}")
        lines += [
            f"{'─'*60}",
            f"  Not applicable screens: {', '.join(self.thresholds.not_applicable) or 'none'}",
            f"  Requires unit economics: {self.thresholds.requires_unit_economics}",
            f"  WACC adjustment: {self.thresholds.wacc_adjustment_bps:+d}bps",
            f"{'='*60}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "ticker":             self.ticker,
            "stage":              self.stage.value,
            "stage_label":        self.label,
            "confidence":         self.confidence,
            "primary_lens":       self.primary_lens,
            "revenue_cagr_3y":    self.revenue_cagr_3y,
            "fcf_margin":         self.fcf_margin,
            "gross_margin":       self.gross_margin,
            "roic":               self.roic,
            "is_profitable":      self.is_profitable,
            "signals":            self.signals,
            "overrides":          self.overrides,
            "not_applicable":     self.thresholds.not_applicable,
            "requires_unit_economics": self.thresholds.requires_unit_economics,
            "wacc_adjustment_bps": self.thresholds.wacc_adjustment_bps,
            "p2_weights": {
                "roic":  self.thresholds.p2_roic_weight,
                "fcf":   self.thresholds.p2_fcf_weight,
                "debt":  self.thresholds.p2_debt_weight,
            },
            "cagr_thresholds": {
                "strong":   self.thresholds.cagr_strong,
                "good":     self.thresholds.cagr_good,
                "moderate": self.thresholds.cagr_moderate,
                "slow":     self.thresholds.cagr_slow,
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# Classifier
# ─────────────────────────────────────────────────────────────────────────────

class LifecycleClassifier:
    """
    Classifies a company by lifecycle stage using a decision tree of
    observable financial signals. No LLM required — fully deterministic.

    Classification order (waterfalls from most specific to least):
      1. DECLINING   — revenue shrinking AND profitability deteriorating
      2. STARTUP     — revenue < $100M OR no meaningful revenue history
      3. HYPERGROWTH — revenue CAGR > 25% AND FCF negative
      4. MATURE      — revenue CAGR < 8% AND FCF margin strong
      5. GROWTH_COMPOUNDER — everything else (the framework's core case)

    Manual overrides via config/universe.csv (lifecycle_stage column).
    """

    UNIVERSE_CONFIG = "config/universe.csv"
    REPORT_DIR      = Path("valuation_data/serving/latest")

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._overrides = self._load_overrides()

    def _load_overrides(self) -> Dict[str, Stage]:
        """Load manual lifecycle stage overrides from universe config."""
        overrides = {}
        try:
            import csv
            with open(self.UNIVERSE_CONFIG) as f:
                for row in csv.DictReader(f):
                    stage_str = row.get("lifecycle_stage")
                    if stage_str:
                        stage_str = stage_str.strip().lower()
                    if stage_str:
                        try:
                            overrides[row["ticker"].upper()] = Stage(stage_str)
                        except ValueError:
                            pass
        except FileNotFoundError:
            pass
        return overrides

    def classify_from_report(self, ticker: str) -> ClassificationResult:
        """Classify from saved report JSON file."""
        path = self.REPORT_DIR / f"{ticker.upper()}_report.json"
        if not path.exists():
            raise FileNotFoundError(f"No report at {path}")

        report = json.loads(path.read_text())
        ft   = report.get("2_financial_translation", {}) or {}
        cf   = ft.get("clean_financials", {}) or {}
        rat  = ft.get("ratios", {}) or {}
        p2   = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {}) or {}
        rdcf = p2.get("reverse_dcf", {}) or {}

        # Extract inputs
        revenue_bn  = self._safe(cf.get("revenue_bn"))
        fcf_margin  = self._safe(rat.get("fcf_margin_pct"))
        gross_margin= self._safe(rat.get("gross_margin_pct"))
        roic        = self._safe(rat.get("roic"))
        hist_cagr   = self._safe(rdcf.get("historical_cagr"))

        # Try to get 3Y CAGR from DB
        cagr_3y = cagr_5y = None
        try:
            from aletheia.data.database import InvestmentDatabase
            import numpy as np
            db = InvestmentDatabase(verbose=False)
            df = db.get_latest(ticker.upper())
            db.close()
            if not df.empty:
                rev = df.sort_values("fiscal_year")["clean_Revenue"].dropna()
                rev_now = float(rev.iloc[-1]) if len(rev) > 0 else None
                if rev_now:
                    for y, attr in [(3, "cagr_3y"), (5, "cagr_5y")]:
                        if len(rev) >= y:
                            r0 = float(rev.iloc[-y])
                            if r0 > 0:
                                val = (rev_now / r0) ** (1/y) - 1
                                locals()[attr]  # just check it exists
                                if attr == "cagr_3y":
                                    cagr_3y = val
                                else:
                                    cagr_5y = val
        except Exception:
            pass

        # Use hist_cagr as fallback
        if cagr_3y is None:
            cagr_3y = hist_cagr

        return self._classify(
            ticker=ticker.upper(),
            revenue_bn=revenue_bn,
            revenue_cagr_3y=cagr_3y,
            revenue_cagr_5y=cagr_5y or hist_cagr,
            fcf_margin=fcf_margin,
            gross_margin=gross_margin,
            roic=roic,
        )

    def classify_from_state(self, ticker: str, state: dict) -> ClassificationResult:
        """Classify from LangGraph agent state."""
        p2   = state.get("phase2_valuation", {}) or {}
        rdcf = p2.get("reverse_dcf", {}) or {}
        md   = p2.get("multiple_decomposition", {}) or {}

        try:
            from aletheia.data.database import InvestmentDatabase
            import numpy as np
            db = InvestmentDatabase(verbose=False)
            df = db.get_latest(ticker)
            db.close()
            row = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0] if not df.empty else None
            def g(col):
                v = row.get(col) if row is not None else None
                return float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None
            revenue_bn   = (g("clean_Revenue") or 0) / 1e9
            fcf_margin   = g("derived_FCF_Margin_Pct")
            gross_margin = g("derived_GrossMargin_Pct")
            roic         = g("derived_ROIC") or self._safe(md.get("roic"))
            rev_series   = df.sort_values("fiscal_year")["clean_Revenue"].dropna()
            rev_now      = float(rev_series.iloc[-1]) if len(rev_series) > 0 else None
            cagr_3y = cagr_5y = None
            if rev_now:
                for y, name in [(3, "cagr_3y"), (5, "cagr_5y")]:
                    if len(rev_series) >= y:
                        r0 = float(rev_series.iloc[-y])
                        if r0 > 0:
                            val = (rev_now / r0) ** (1/y) - 1
                            if y == 3: cagr_3y = val
                            else:      cagr_5y = val
        except Exception:
            revenue_bn = fcf_margin = gross_margin = roic = cagr_3y = cagr_5y = None

        hist_cagr = self._safe(rdcf.get("historical_cagr"))

        return self._classify(
            ticker=ticker.upper(),
            revenue_bn=revenue_bn,
            revenue_cagr_3y=cagr_3y or hist_cagr,
            revenue_cagr_5y=cagr_5y or hist_cagr,
            fcf_margin=fcf_margin,
            gross_margin=gross_margin,
            roic=roic,
        )

    def _classify(
        self,
        ticker: str,
        revenue_bn: Optional[float],
        revenue_cagr_3y: Optional[float],
        revenue_cagr_5y: Optional[float],
        fcf_margin: Optional[float],
        gross_margin: Optional[float],
        roic: Optional[float],
    ) -> ClassificationResult:

        signals  = []
        overrides_applied = []
        confidence = 0.85

        # ── Check for manual override in config ───────────────────────────────
        if ticker in self._overrides:
            stage = self._overrides[ticker]
            overrides_applied.append(
                f"Manual override from config/universe.csv: {stage.value}"
            )
            signals.append(f"Config override → {STAGE_LABELS[stage]}")
            confidence = 1.0
            return ClassificationResult(
                ticker=ticker, stage=stage, confidence=confidence,
                thresholds=STAGE_THRESHOLDS[stage],
                revenue_cagr_3y=revenue_cagr_3y,
                fcf_margin=fcf_margin, gross_margin=gross_margin,
                roic=roic, revenue_bn=revenue_bn,
                is_profitable=(fcf_margin or 0) > 0,
                signals=signals, overrides=overrides_applied,
            )

        # ── Signal helpers ───────────────────────────────────────────────────
        cagr = revenue_cagr_3y   # primary CAGR signal (most recent)
        cagr5 = revenue_cagr_5y  # secondary

        is_profitable     = (fcf_margin is not None and fcf_margin > 0)
        is_fcf_negative   = (fcf_margin is not None and fcf_margin < 0)
        is_tiny           = (revenue_bn is not None and revenue_bn < 0.5)  # < $500M
        is_pre_revenue    = (revenue_bn is None or revenue_bn < 0.05)
        is_high_growth    = (cagr is not None and cagr > 0.25)
        is_mod_growth     = (cagr is not None and 0.08 <= cagr <= 0.25)
        is_low_growth     = (cagr is not None and 0 < cagr < 0.08)
        is_no_growth      = (cagr is not None and -0.02 <= cagr <= 0.02)
        is_declining      = (cagr is not None and cagr < -0.02)
        is_decelerating   = (cagr is not None and cagr5 is not None
                             and cagr5 > 0 and cagr < cagr5 * 0.5)
        has_strong_roic   = (roic is not None and roic > 0.15)
        has_weak_roic     = (roic is not None and roic is not None and roic < 0.05)
        has_good_gm       = (gross_margin is not None and gross_margin > 40)
        has_poor_gm       = (gross_margin is not None and gross_margin < 20)

        # ── Decision tree ─────────────────────────────────────────────────────

        # 1. DECLINING — revenue contracting OR near-zero with weak fundamentals
        # < -2% is clearly declining. -0.7% (TSLA) with ROIC < WACC is also declining.
        is_clearly_declining = (cagr is not None and cagr < -0.02)
        # Borderline declining: near-zero/negative CAGR + weak ROIC (≤6%) + deceleration
        # TSLA: -0.7% CAGR, 5.0% ROIC (below most WACCs), 5Y CAGR was 18% → sharp deceleration
        is_borderline_declining = (
            cagr is not None and -0.02 <= cagr <= 0.02
            and (roic is not None and roic <= 0.06)    # ≤6% ROIC — below most WACCs
            and is_decelerating                         # accelerating deceleration required
        )

        if is_clearly_declining or is_borderline_declining:
            if is_clearly_declining:
                signals.append(f"Revenue CAGR {cagr:.1%} < -2% — revenue contracting")
            else:
                signals.append(
                    f"Revenue CAGR {cagr:.1%} near zero, ROIC {(roic or 0):.1%} "
                    f"below WACC — deteriorating trajectory"
                )
            if has_weak_roic:
                signals.append(f"ROIC {roic:.1%} — value destroying, growth worsens outlook")
                confidence = 0.88
            elif is_fcf_negative:
                signals.append("FCF negative — cash burning while revenue stagnates")
                confidence = 0.82
            else:
                signals.append("Marginal growth — may be cyclical, investigate")
                confidence = 0.68
            stage = Stage.DECLINING

        # 2. STARTUP — pre-revenue or very early stage
        elif is_pre_revenue or is_tiny:
            signals.append(
                f"Revenue {'< $50M' if is_pre_revenue else f'${revenue_bn:.1f}B (< $500M)'}"
                " — early stage"
            )
            if is_fcf_negative:
                signals.append("FCF negative — investing in growth")
            stage = Stage.STARTUP
            confidence = 0.80

        # 3. HYPERGROWTH — fast revenue + not yet consistently profitable
        elif is_high_growth and (is_fcf_negative or (fcf_margin is not None and fcf_margin < 0.05)):
            signals.append(f"Revenue CAGR {cagr:.1%} > 25% — hypergrowth")
            signals.append(
                f"FCF margin {fcf_margin:.1f}%" if fcf_margin is not None
                else "FCF data unavailable"
            )
            if has_good_gm:
                signals.append(
                    f"Gross margin {gross_margin:.1f}% > 40% — "
                    "pre-profit but structurally sound unit economics"
                )
                confidence = 0.88
            else:
                signals.append(
                    f"Gross margin {gross_margin:.1f}% — "
                    "investigate unit economics before conviction"
                )
                confidence = 0.75
            stage = Stage.HYPERGROWTH

        # 4. MATURE — low growth (whether FCF positive or thin)
        # Key: if borderline_declining didn't fire, low-growth + any FCF → Mature
        # CNC: 6.8% CAGR, -0.3% FCF — healthcare plan timing, structurally mature
        elif is_low_growth or is_no_growth:
            signals.append(f"Revenue CAGR {cagr:.1%} < 8% — low growth profile")
            if is_profitable and (fcf_margin or 0) > 0.08:
                signals.append(
                    f"FCF margin {fcf_margin:.1f}% — strong cash generation at maturity"
                )
                stage = Stage.MATURE
                confidence = 0.88
            elif is_profitable:
                signals.append(
                    f"FCF margin {fcf_margin:.1f}% — thin but positive"
                )
                stage = Stage.MATURE
                confidence = 0.78
            elif (fcf_margin is not None and fcf_margin > -0.05
                  and (roic is not None and roic > 0.05)):
                # Slightly negative FCF but ROIC above distress — mature with timing issues
                signals.append(
                    f"FCF margin {fcf_margin:.1f}% slightly negative — "
                    "likely working capital timing at maturity (e.g. healthcare plans)"
                )
                stage = Stage.MATURE
                confidence = 0.70
            else:
                # Only reach here if borderline_declining criteria not met —
                # rare case: low growth + significantly negative FCF + weak ROIC
                signals.append("FCF significantly negative with low growth — monitor closely")
                stage = Stage.MATURE  # Still Mature unless declining criteria fired
                confidence = 0.55

        # 5. GROWTH_COMPOUNDER — profitable + moderate growth (the framework's core case)
        elif is_mod_growth:
            signals.append(f"Revenue CAGR {cagr:.1%} — strong growth")
            if is_profitable:
                signals.append(
                    f"FCF margin {fcf_margin:.1f}% — "
                    "profitable while reinvesting"
                )
                if has_strong_roic:
                    signals.append(
                        f"ROIC {roic:.1%} — above WACC, value-creating growth"
                    )
                    confidence = 0.93
                else:
                    confidence = 0.82
                stage = Stage.GROWTH_COMPOUNDER
            else:
                # Fast growth but not profitable → could be hypergrowth or struggling
                signals.append("FCF negative despite moderate growth — monitor")
                stage = Stage.HYPERGROWTH
                confidence = 0.65

        # 6. High growth + profitable → growth compounder in acceleration
        elif is_high_growth and is_profitable:
            signals.append(f"Revenue CAGR {cagr:.1%} > 25% with positive FCF")
            signals.append("Rare combination — growth compounder in hypergrowth phase")
            if has_strong_roic:
                signals.append(f"ROIC {roic:.1%} — exceptional capital efficiency")
                confidence = 0.90
            stage = Stage.GROWTH_COMPOUNDER

        else:
            # Fallback — insufficient data
            signals.append("Insufficient signal strength — defaulting to growth compounder")
            stage = Stage.GROWTH_COMPOUNDER
            confidence = 0.50

        # ── Deceleration warning (informational, not a reclassification) ─────
        if is_decelerating and stage != Stage.DECLINING:
            signals.append(
                f"⚠ Deceleration detected: 3Y CAGR {cagr:.1%} vs 5Y CAGR {cagr5:.1%} "
                f"— growth may be maturing"
            )
            confidence = max(0.50, confidence - 0.10)

        result = ClassificationResult(
            ticker=ticker,
            stage=stage,
            confidence=confidence,
            thresholds=STAGE_THRESHOLDS[stage],
            revenue_cagr_3y=revenue_cagr_3y,
            revenue_cagr_5y=revenue_cagr_5y,
            fcf_margin=fcf_margin,
            gross_margin=gross_margin,
            roic=roic,
            revenue_bn=revenue_bn,
            is_profitable=is_profitable,
            signals=signals,
            overrides=overrides_applied,
        )

        if self.verbose:
            print(result.summary())

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Batch scoring
    # ─────────────────────────────────────────────────────────────────────────

    def classify_universe(self, tickers: List[str]) -> Dict[str, ClassificationResult]:
        results = {}
        for ticker in tickers:
            try:
                results[ticker] = self.classify_from_report(ticker)
            except Exception as e:
                if self.verbose:
                    print(f"  {ticker}: {e}")
        return results

    def universe_table(self, results: Dict[str, ClassificationResult]) -> str:
        lines = [
            f"\n{'LIFECYCLE CLASSIFICATION — UNIVERSE':^70}",
            "─" * 70,
            f"{'Ticker':>6} │ {'Stage':>22} │ {'Conf':>5} │ "
            f"{'3Y CAGR':>8} │ {'FCF Marg':>9} │ {'ROIC':>7}",
            "─" * 70,
        ]
        for ticker, r in sorted(results.items(),
                                 key=lambda x: list(Stage).index(x[1].stage)):
            lines.append(
                f"  {r.ticker:>4} │ {r.label:>22} │ {r.confidence:>5.0%} │ "
                f"{r.revenue_cagr_3y*100:>7.1f}% │ "
                f"{(r.fcf_margin or 0):>8.1f}% │ "
                f"{(r.roic or 0)*100:>6.1f}%"
            )
        lines.append("─" * 70)
        return "\n".join(lines)

    @staticmethod
    def _safe(v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            f = float(v)
            return None if math.isnan(f) else f
        except (TypeError, ValueError):
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Integrate with conviction scorer
# ─────────────────────────────────────────────────────────────────────────────

def get_stage_adjusted_thresholds(ticker: str, state: dict = None) -> StageThresholds:
    """
    Drop-in for conviction_scorer.py — returns stage-adjusted thresholds
    to replace hardcoded values in _p2_score, _p3_score, and _p4_score.

    Usage in conviction_scorer._compute():
        thresholds = get_stage_adjusted_thresholds(ticker)
        # Use thresholds.cagr_strong instead of 0.20
        # Use thresholds.p2_roic_weight instead of 0.40
    """
    clf = LifecycleClassifier()
    try:
        if state:
            result = clf.classify_from_state(ticker, state)
        else:
            result = clf.classify_from_report(ticker)
        return result.thresholds
    except Exception:
        # Fallback to growth compounder (the framework's core case)
        return STAGE_THRESHOLDS[Stage.GROWTH_COMPOUNDER]


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    tickers = sys.argv[1:] if len(sys.argv) > 1 else [
        "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "CNC"
    ]

    clf = LifecycleClassifier(verbose=True)
    results = clf.classify_universe(tickers)
    print(clf.universe_table(results))

    # Show threshold adjustments for any non-standard stages
    print("\nThreshold adjustments for non-growth-compounder stages:")
    for ticker, r in results.items():
        if r.stage != Stage.GROWTH_COMPOUNDER:
            t = r.thresholds
            print(f"\n  {ticker} ({r.label}):")
            print(f"    P2 weights: ROIC {t.p2_roic_weight:.0%} | FCF {t.p2_fcf_weight:.0%} | Debt {t.p2_debt_weight:.0%}")
            print(f"    P3 CAGR thresholds: 5/5 > {t.cagr_strong:.0%} | 4/5 > {t.cagr_good:.0%}")
            print(f"    P4 MoS thresholds:  5/5 > {t.mos_strong:.0%} | 4/5 > {t.mos_good:.0%}")
            print(f"    WACC adj: {t.wacc_adjustment_bps:+d}bps")
            print(f"    Not applicable: {t.not_applicable or 'none'}")
