"""
aletheia/tools/conviction_scorer.py

5-Pillar Structured Conviction Scorer
======================================
Replaces the LLM -10/+10 output with a deterministic, auditable
5-pillar score (5-25 scale) that maps directly to the framework.

Framework Section 5.1:
  Score each pillar 1–5.
  Full positions:  23+ (avg 4.6+)
  Starter:         20–22
  Watchlist:       below 20

5 Pillars:
  P1 — Moat Strength          (from forensic agent moat score + value chain)
  P2 — Fundamental Health     (ROIC, FCF margin, net debt, data quality)
  P3 — Secular Tailwind       (revenue CAGR, sector, cyclicality)
  P4 — Margin of Safety       (base case MoS from Phase 2 DCF)
  P5 — Leadership Quality     (SBC discipline, buyback quality, operating leverage)

Section 5.2 — Multiple-Justified Conviction:
  After pillar scoring, apply a multiple justification gate.
  If EV/EBITDA trades at >100% premium to justified AND implied CAGR
  is >2x historical, cap the total score at 18 regardless of pillar scores.
  This prevents a high-moat company from reaching conviction if the
  price already reflects all the quality.

Usage:
    from aletheia.tools.conviction_scorer import ConvictionScorer
    scorer = ConvictionScorer()
    result = scorer.score("TICKER", state)
    print(result.summary())
"""

import math
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from pathlib import Path
import json


# ─────────────────────────────────────────────────────────────────────────────
# Scoring rubric — exact thresholds from framework Section 5.1
# ─────────────────────────────────────────────────────────────────────────────

# P1 — Moat Strength
# Source: forensic_report.moat_score (0-10 from LLM agent)
# Maps 7-10 range to 1-5 pillar score
MOAT_THRESHOLDS = [
    (9.0, 5, "Multi-layered moats confirmed — network effects + switching costs + data flywheel"),
    (8.0, 4, "Strong moat with at least 2 confirmed powers"),
    (7.0, 3, "Moderate moat — single power confirmed with evidence"),
    (5.0, 2, "Weak moat — benefit identified, barrier unconfirmed"),
    (0.0, 1, "No confirmed moat — commodity product, easily replicated"),
]

# P2 — Fundamental Health
# Composite of ROIC vs WACC, FCF margin, and net debt position
# Each component scores 0-5, weighted average
def _p2_score(roic, wacc, fcf_margin, net_debt_bn, ebitda_bn, data_quality,
              roic_weight=0.40, fcf_weight=0.35, debt_weight=0.25,
              roic_applicable=True, fcf_applicable=True, stage="",
              sector="", roe=None):

    # Financial sector override — use ROE instead of ROIC
    # Banks: ROE > 15% excellent, > 10% good, > 7% adequate
    # Skip FCF margin and Net Debt (deposits are not debt)
    FINANCIAL_SECTORS = {'Financials', 'Banking', 'Insurance'}
    if sector in FINANCIAL_SECTORS and roe is not None:
        reasons = []
        if roe > 0.15:
            score = 5; reasons.append(f"ROE {roe:.1%} — exceptional for financial sector")
        elif roe > 0.12:
            score = 4; reasons.append(f"ROE {roe:.1%} — strong financial returns")
        elif roe > 0.08:
            score = 3; reasons.append(f"ROE {roe:.1%} — adequate financial returns")
        elif roe > 0.05:
            score = 2; reasons.append(f"ROE {roe:.1%} — below-average financial returns")
        else:
            score = 1; reasons.append(f"ROE {roe:.1%} — weak financial returns")
        # Data quality adjustment
        if data_quality is not None and data_quality < 0.80:
            score = max(1, score - 1)
            reasons.append(f"⚠ Data quality {data_quality:.2f} — score reduced")
        return max(1, min(5, score)), reasons

    score = 0.0
    reasons = []

    # Component 1: ROIC vs WACC spread (weight 40%)
    if roic_applicable and roic is not None and wacc is not None:
        spread = roic - wacc
        if spread > 0.15:
            s = 5; r = f"ROIC-WACC spread {spread*100:+.1f}pp — exceptional value creation"
        elif spread > 0.08:
            s = 4; r = f"ROIC-WACC spread {spread*100:+.1f}pp — strong value creation"
        elif spread > 0.02:
            s = 3; r = f"ROIC-WACC spread {spread*100:+.1f}pp — modest value creation"
        elif spread > -0.02:
            s = 2; r = f"ROIC ≈ WACC — marginal value creation"
        else:
            s = 1; r = f"ROIC-WACC spread {spread*100:+.1f}pp — value destroying"
        score += s * roic_weight
        reasons.append(r)
    elif not roic_applicable:
        score += 3 * roic_weight # Neutral score if not applicable
        reasons.append("ROIC vs WACC — not applicable for this lifecycle stage")

    # Component 2: FCF margin (weight 35%)
    if fcf_applicable and fcf_margin is not None:
        if fcf_margin > 20:
            s = 5; r = f"FCF margin {fcf_margin:.1f}% — fortress cash generation"
        elif fcf_margin > 12:
            s = 4; r = f"FCF margin {fcf_margin:.1f}% — strong FCF"
        elif fcf_margin > 5:
            s = 3; r = f"FCF margin {fcf_margin:.1f}% — adequate FCF"
        elif fcf_margin > 0:
            s = 2; r = f"FCF margin {fcf_margin:.1f}% — thin FCF"
        else:
            s = 1; r = f"FCF margin {fcf_margin:.1f}% — FCF negative"
        score += s * fcf_weight
        reasons.append(r)
    elif not fcf_applicable:
        score += 3 * fcf_weight
        reasons.append("FCF margin — not applicable for this lifecycle stage")

    # Component 3: Net debt / EBITDA (weight 25%)
    if debt_weight > 0:
        if net_debt_bn is not None and ebitda_bn and ebitda_bn > 0:
            nd_ebitda = net_debt_bn / ebitda_bn
            if nd_ebitda < -1.0:
                s = 5; r = f"Net cash position ({nd_ebitda:.1f}× EBITDA) — fortress balance sheet"
            elif nd_ebitda < 0:
                s = 4; r = f"Net cash position ({nd_ebitda:.1f}× EBITDA)"
            elif nd_ebitda < 1.5:
                s = 4; r = f"Net Debt/EBITDA {nd_ebitda:.1f}× — conservative leverage"
            elif nd_ebitda < 3.0:
                s = 3; r = f"Net Debt/EBITDA {nd_ebitda:.1f}× — moderate leverage"
            elif nd_ebitda < 4.0:
                s = 2; r = f"Net Debt/EBITDA {nd_ebitda:.1f}× — elevated leverage"
            else:
                s = 1; r = f"Net Debt/EBITDA {nd_ebitda:.1f}× — high leverage, distress risk"
            
            if stage == 'declining' and s == 5:
                s = 3
                r += " (capped: declining business — cash runway finite)"
                
            score += s * debt_weight
            reasons.append(r)
        elif net_debt_bn is not None and net_debt_bn < 0:
            # Net cash, no EBITDA context needed
            s = 5; r = "Net cash position — fortress balance sheet"
            if stage == 'declining' and s == 5:
                s = 3
                r += " (capped: declining business — cash runway finite)"
            score += s * debt_weight
            reasons.append(r)
    # Data quality adjustment: poor quality reduces max score
    if data_quality is not None and data_quality < 0.80:
        score *= 0.85
        reasons.append(f"⚠ Data quality {data_quality:.2f} — score haircut applied")

    return max(1, min(5, round(score))), reasons


# P3 — Secular Tailwind
# Revenue CAGR + sector classification
# Higher weight to recent CAGR but bounded by sector context
def _p3_score(rev_cagr, hist_cagr, sector="", cyclicality_z=None, is_peak=False,
              implied_cagr=None, cagr_strong=0.20, cagr_good=0.12, cagr_moderate=0.07, cagr_slow=0.03, stage=""):
    reasons = []

    # Use robust historical CAGR as primary signal
    cagr = hist_cagr or rev_cagr or 0

    if cagr > cagr_strong:
        base = 5
        r = f"Revenue CAGR {cagr:.1%} — {'strong growth for ' + stage + ' stage' if stage and stage != 'growth_compounder' else 'hypergrowth, structural demand'}"
    elif cagr > cagr_good:
        base = 4
        r = f"Revenue CAGR {cagr:.1%} — {'solid growth for ' + stage + ' stage' if stage and stage != 'growth_compounder' else 'strong growth compounder'}"
    elif cagr > cagr_moderate:
        base = 3; r = f"Revenue CAGR {cagr:.1%} — moderate growth"
    elif cagr > cagr_slow:
        base = 2; r = f"Revenue CAGR {cagr:.1%} — slow growth"
    else:
        base = 1; r = f"Revenue CAGR {cagr:.1%} — stagnant or declining"
    reasons.append(r)

    # Cyclicality penalty
    if is_peak:
        base = max(1, base - 1)
        reasons.append("⚠ Cyclicality peak detected — base revenue likely inflated")
    elif cyclicality_z and cyclicality_z > 1.5:
        base = max(1, base - 1)
        reasons.append(f"Cyclicality Z-score {cyclicality_z:.1f} — elevated, applying penalty")

    # Sector tailwind boost / penalty
    secular_tailwinds = ["technology", "software", "semiconductor", "ai", "cloud",
                         "healthcare", "biotech", "renewable", "digital", "cyber"]
    secular_headwinds = ["tobacco", "coal", "print", "traditional retail", "cable"]

    sector_lower = sector.lower()
    if any(t in sector_lower for t in secular_tailwinds):
        base = min(5, base + 1)
        reasons.append(f"Sector '{sector}' — confirmed secular tailwind")
    elif any(h in sector_lower for h in secular_headwinds):
        base = max(1, base - 1)
        reasons.append(f"Sector '{sector}' — secular headwind")

    # Tailwind pricing penalty — if market has already priced the tailwind in,
    # the investor gets no benefit from it at current entry price.
    # TSLA: implied 79.8% / historical 28.0% = 2.85× → -2 penalty
    # GOOGL: implied 23.1% / historical 11.7% = 1.97× → -1 penalty
    # MSFT: implied 17.3% / historical 20.4% = 0.85× → no penalty (below threshold)
    if implied_cagr is not None and hist_cagr and hist_cagr > 0:
        ratio = implied_cagr / hist_cagr
        if ratio > 2.5:
            base = max(1, base - 2)
            reasons.append(
                f"Market implied CAGR {ratio:.1f}× historical — "
                f"tailwind fully priced, no entry benefit"
            )
        elif ratio > 1.5:
            base = max(1, base - 1)
            reasons.append(
                f"Market implied CAGR {ratio:.1f}× historical — "
                f"tailwind partially priced"
            )

    return max(1, min(5, base)), reasons

# P4 — Margin of Safety
# Directly from Phase 2 base case DCF intrinsic value vs market price
MOS_THRESHOLDS = [
    (0.30,  5, "Trading at <70% of base DCF IV — fortress margin of safety"),
    (0.15,  4, "15-30% margin of safety — strong entry point"),
    (0.00,  3, "0-15% margin of safety — fair value, limited upside"),
    (-0.20, 2, "Slight premium to intrinsic value — patience required"),
    (-1.00, 1, "Significant premium — price embeds optimistic assumptions"),
]

# P5 — Leadership Quality
# Composite: SBC discipline + operating leverage + value chain
def _p5_score(sbc_pct_fcf, op_leverage_score, upstream_leak, strategic_leverage, moat_evidence=""):
    score = 0.0
    reasons = []

    # SBC discipline (weight 40%)
    if sbc_pct_fcf is not None:
        if sbc_pct_fcf < 3:
            s = 5; r = f"SBC {sbc_pct_fcf:.1f}% of FCF — minimal dilution"
        elif sbc_pct_fcf < 6:
            s = 4; r = f"SBC {sbc_pct_fcf:.1f}% of FCF — acceptable dilution"
        elif sbc_pct_fcf < 10:
            s = 3; r = f"SBC {sbc_pct_fcf:.1f}% of FCF — elevated, investigate"
        elif sbc_pct_fcf < 15:
            s = 2; r = f"SBC {sbc_pct_fcf:.1f}% of FCF — quiet dilution (flag)"
        else:
            s = 1; r = f"SBC {sbc_pct_fcf:.1f}% of FCF — severe dilution, employees compensated at shareholder expense"
        score += s * 0.40
        reasons.append(r)
    else:
        score += 3 * 0.40  # neutral if missing
        reasons.append("SBC data unavailable — neutral score")

    # Operating leverage (weight 30%) — from forensic agent 0-10 scale
    if op_leverage_score is not None:
        s = max(1, min(5, round(op_leverage_score / 2)))  # map 0-10 to 1-5
        r = f"Operating leverage {op_leverage_score:.1f}/10"
        score += s * 0.30
        reasons.append(r)
    else:
        score += 3 * 0.30

    # Value chain positioning (weight 30%)
    if upstream_leak is True:
        s = 2; r = "Upstream value leak detected — suppliers capture excess margin"
    elif strategic_leverage is not None:
        if strategic_leverage >= 8:
            s = 5; r = f"Strategic leverage {strategic_leverage}/10 — dominant positioning"
        elif strategic_leverage >= 6:
            s = 4; r = f"Strategic leverage {strategic_leverage}/10 — strong positioning"
        elif strategic_leverage >= 4:
            s = 3; r = f"Strategic leverage {strategic_leverage}/10 — moderate positioning"
        else:
            s = 2; r = f"Strategic leverage {strategic_leverage}/10 — weak positioning"
    else:
        s = 3; r = "Value chain data unavailable — neutral score"
    score += s * 0.30
    reasons.append(r)

    return max(1, min(5, round(score))), reasons


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PillarScore:
    name: str
    score: int          # 1-5
    weight: float       # informational only
    reasons: List[str]  # evidence trail

    def __str__(self):
        stars = "●" * self.score + "○" * (5 - self.score)
        return f"  P{['','1','2','3','4','5'].index(self.name[0]) if self.name[0].isdigit() else '?'} [{stars}] {self.score}/5  {self.name}"


@dataclass
class ConvictionResult:
    ticker: str

    # Individual pillar scores
    p1_moat:      PillarScore = None
    p2_health:    PillarScore = None
    p3_tailwind:  PillarScore = None
    p4_mos:       PillarScore = None
    p5_leadership: PillarScore = None

    # Totals
    raw_total: int = 0          # Sum of 5 pillars (5-25)
    capped_total: int = 0       # After multiple-justified cap
    cap_applied: bool = False
    cap_reason: str = ""

    # Position recommendation
    position_tier: str = ""     # "full" / "starter" / "watchlist" / "avoid"
    position_size_pct: str = "" # suggested % of portfolio

    # Legacy compatibility — maps 5-25 to -10/+10 for existing lead agent
    conviction_score: int = 0   # -10 to +10

    # Reflexivity flags
    reflexivity_flags: List[str] = field(default_factory=list)
    reflexivity_cap: bool = False  # position capped at 75% if True

    # Multiple justification context
    multiple_premium: Optional[float] = None
    implied_hist_ratio: Optional[float] = None

    # Lifecycle Context
    lifecycle_stage: Optional[int] = None
    lifecycle_label: Optional[str] = None

    def summary(self) -> str:
        lines = [
            f"\n{'='*60}",
            f"  CONVICTION SCORECARD: {self.ticker}",
            f"{'='*60}",
        ]
        for p in [self.p1_moat, self.p2_health, self.p3_tailwind,
                  self.p4_mos, self.p5_leadership]:
            if p:
                stars = "●" * p.score + "○" * (5 - p.score)
                lines.append(f"  [{stars}] {p.score}/5  {p.name}")
                for r in p.reasons:
                    lines.append(f"         → {r}")

        lines += [
            f"  {'─'*54}",
            f"  Raw total:   {self.raw_total}/25",
        ]
        if self.cap_applied:
            lines.append(f"  Capped at:   {self.capped_total}/25  ← {self.cap_reason}")
        else:
            lines.append(f"  Final total: {self.capped_total}/25")

        lines += [
            f"  Position:    {self.position_tier.upper()}  ({self.position_size_pct})",
            f"  Conv score:  {self.conviction_score:+d} / 10  (legacy scale)",
        ]

        if self.reflexivity_flags:
            lines.append(f"\n  ⚠ REFLEXIVITY FLAGS:")
            for f_ in self.reflexivity_flags:
                lines.append(f"    → {f_}")
            if self.reflexivity_cap:
                lines.append("    Position capped at 75% of conviction-based size")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "p1_moat": self.p1_moat.score if self.p1_moat else None,
            "p2_health": self.p2_health.score if self.p2_health else None,
            "p3_tailwind": self.p3_tailwind.score if self.p3_tailwind else None,
            "p4_mos": self.p4_mos.score if self.p4_mos else None,
            "p5_leadership": self.p5_leadership.score if self.p5_leadership else None,
            "raw_total": self.raw_total,
            "capped_total": self.capped_total,
            "cap_applied": self.cap_applied,
            "cap_reason": self.cap_reason,
            "position_tier": self.position_tier,
            "position_size_pct": self.position_size_pct,
            "conviction_score": self.conviction_score,
            "reflexivity_flags": self.reflexivity_flags,
            "reflexivity_cap": self.reflexivity_cap,
            "p1_reasons": self.p1_moat.reasons if self.p1_moat else [],
            "p2_reasons": self.p2_health.reasons if self.p2_health else [],
            "p3_reasons": self.p3_tailwind.reasons if self.p3_tailwind else [],
            "p4_reasons": self.p4_mos.reasons if self.p4_mos else [],
            "p5_reasons": self.p5_leadership.reasons if self.p5_leadership else [],
            "lifecycle_stage": self.lifecycle_stage,
            "lifecycle_label": self.lifecycle_label,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Main scorer
# ─────────────────────────────────────────────────────────────────────────────

class ConvictionScorer:
    """
    Deterministic 5-pillar conviction scorer.

    Reads from the LangGraph agent state (which contains all agent outputs
    and Phase 2 valuation data) and produces a structured conviction score.

    Can also be called standalone with a report JSON for batch scoring.
    """

    def score_from_state(self, ticker: str, state: dict, calc_input: 'CalculationInput' = None) -> ConvictionResult:
        """Score from LangGraph agent state dict."""
        forensic    = state.get("forensic_report", {}) or {}
        vc          = state.get("value_chain_report", {}) or {}
        context     = state.get("strategic_context_report", {}) or {}
        p2          = state.get("phase2_valuation", {}) or {}
        strat       = state.get("strategist_report", {}) or {}

        # Extract all inputs
        moat_score       = self._safe(forensic.get("moat_score"))
        op_leverage      = self._safe(forensic.get("operating_leverage_score"))
        sbc_pct_fcf      = None  # from DB below
        upstream_leak    = vc.get("upstream_leak")
        strategic_lev    = self._safe(vc.get("strategic_leverage_score") or vc.get("strategic_leverage"))
        cyclicality_z    = self._safe(context.get("revenue_z_score"))
        applies_haircut  = bool(context.get("applies_cyclical_haircut"))
        is_peak          = bool(context.get("is_cyclical_peak"))

        # Phase 2 valuation
        dcf_dict         = p2.get("three_scenario_dcf", {}) or p2.get("dcf", {}) or {}
        base_mos         = self._safe(dcf_dict.get("base", {}).get("margin_of_safety") if "base" in dcf_dict else dcf_dict.get("base_upside"))
        md               = p2.get("multiple_decomposition", {}) or {}
        roic             = self._safe(md.get("roic"))
        wacc             = self._safe(p2.get("wacc"))
        value_creation   = md.get("value_creation", "")
        multiple_premium = self._safe(md.get("premium_pct"))
        rdcf             = p2.get("reverse_dcf", {}) or {}
        implied_cagr     = self._safe(rdcf.get("implied_cagr_10y"))
        # Reverse-DCF exposes the historical anchor as ``historical_cagr_5y``;
        # the legacy ``historical_cagr`` key is absent, which silently disabled
        # the implied/historical penalty for BOTH P3 (tailwind priced-in) and
        # P4 (optical discount). Read the real field, fall back to legacy.
        hist_cagr        = self._safe(rdcf.get("historical_cagr_5y")
                                      or rdcf.get("historical_cagr"))
        rdcf_signal      = rdcf.get("signal")

        # From DB for FCF margin, net debt, revenue CAGR
        df = calc_input.df
        if not df.empty:
            import numpy as np
            row = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0]
            def g(col):
                v = row.get(col)
                return float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else None
            fcf_margin   = g("derived_FCF_Margin_Pct")
            net_debt_bn  = (g("derived_NetDebt") or 0) / 1e9
            ebitda_bn    = (g("derived_EBITDA") or 0) / 1e9
            data_quality = g("overall_quality_score")
            sbc_pct_fcf  = g("clean_SBC_PctFCF")
            # Revenue CAGR from multi-period median
            rev_series = df.sort_values("fiscal_year")["clean_Revenue"].dropna()
            rev_cagr = self._robust_cagr(rev_series)
        else:
            fcf_margin = ebitda_bn = net_debt_bn = data_quality = rev_cagr = None

        # Sector from universe config
        sector = calc_input.classification.sector if calc_input.classification else ""
        
        fin = state.get("financial_translation", {}) or {}
        ratios = fin.get("ratios", {}) or {}
        roe = self._safe(ratios.get("roe"))

        return self._compute(
            ticker=ticker,
            moat_score=moat_score,
            roic=roic, wacc=wacc,
            fcf_margin=fcf_margin,
            net_debt_bn=net_debt_bn,
            ebitda_bn=ebitda_bn,
            data_quality=data_quality,
            rev_cagr=rev_cagr,
            hist_cagr=hist_cagr,
            sector=sector,
            cyclicality_z=cyclicality_z,
            is_peak=applies_haircut,
            base_mos=base_mos,
            sbc_pct_fcf=sbc_pct_fcf,
            op_leverage=op_leverage,
            upstream_leak=upstream_leak,
            strategic_lev=strategic_lev,
            multiple_premium=multiple_premium,
            implied_cagr=implied_cagr,
            calc_input=calc_input,
            roe=roe,
            state=state,
            reverse_dcf_signal=rdcf_signal,
        )

    def score_from_report(self, ticker: str, calc_input: 'CalculationInput' = None, report_path: str = None) -> ConvictionResult:
        """Score from a saved report JSON file."""
        if report_path is None:
            report_path = f"valuation_data/serving/latest/{ticker.upper()}_report.json"

        path = Path(report_path)
        if not path.exists():
            raise FileNotFoundError(f"No report at {report_path}")

        report = json.loads(path.read_text())
        er     = report.get("1_economic_reality", {}) or {}
        fin    = report.get("2_financial_translation", {}) or {}
        val    = report.get("4_valuation_synthesis", {}) or {}
        p2     = val.get("phase2_valuation", {}) or {}

        cf     = fin.get("clean_financials", {}) or {}
        ratios = fin.get("ratios", {}) or {}
        moat   = er.get("moat", {}) or {}
        vc_er  = er.get("value_chain", {}) or {}
        ind    = er.get("industry_structure", {}) or {}

        dcf3   = p2.get("three_scenario_dcf", {}) or {}
        md     = p2.get("multiple_decomposition", {}) or {}
        rdcf   = p2.get("reverse_dcf", {}) or {}

        roe = self._safe(ratios.get("roe"))

        return self._compute(
            ticker=ticker,
            moat_score=self._safe(moat.get("score")),
            roic=(self._safe(ratios.get("roic")) or self._safe(md.get("roic"))),
            wacc=(self._safe(p2.get("wacc")) or self._safe(md.get("wacc"))),
            fcf_margin=self._safe(ratios.get("fcf_margin_pct")),
            net_debt_bn=self._safe(cf.get("net_debt_bn")),
            ebitda_bn=self._safe(cf.get("ebitda_bn")),
            data_quality=self._safe(cf.get("data_quality")),
            rev_cagr=None,  # not in report — use hist_cagr
            hist_cagr=self._safe(rdcf.get("historical_cagr_5y")
                                 or rdcf.get("historical_cagr")),
            sector=calc_input.classification.sector if (calc_input and calc_input.classification) else "",
            cyclicality_z=self._safe(ind.get("cyclicality_z_score")),
            is_peak=bool(context.get("applies_cyclical_haircut")),
            base_mos=self._safe(dcf3.get("base", {}).get("margin_of_safety") if dcf3.get("base") else None),
            sbc_pct_fcf=self._safe(ratios.get("sbc_pct_fcf")) or (
                # Fallback: compute from raw values if stored pct is None/NaN
                (self._safe(cf.get("sbc_bn", 0)) * 1e9 / (self._safe(cf.get("fcf_bn", 0)) * 1e9) * 100)
                if cf.get("sbc_bn") and cf.get("fcf_bn") and self._safe(cf.get("fcf_bn", 0)) > 0
                else None
            ),
            op_leverage=(
                # New: read float directly (post audit-fix reports)
                self._safe((er.get("business_model") or {}).get("operating_leverage_score"))
                # Fallback: legacy string parse for older cached report JSONs
                or self._safe(
                    (er.get("business_model") or {}).get("cost_structure", "")
                    .replace("Operating Leverage Score: ", "").replace("/10", "")
                    if isinstance((er.get("business_model") or {}).get("cost_structure"), str)
                    else None
                )
            ),
            upstream_leak=vc_er.get("upstream_leak"),
            strategic_lev=self._safe(vc_er.get("strategic_leverage")),
            multiple_premium=self._safe(md.get("premium_pct")),
            implied_cagr=self._safe(rdcf.get("implied_cagr_10y")),
            calc_input=calc_input,
            roe=roe,
            reverse_dcf_signal=rdcf.get("signal"),
        )

    def _compute(
        self,
        ticker: str,
        moat_score, roic, wacc, fcf_margin,
        net_debt_bn, ebitda_bn, data_quality,
        rev_cagr, hist_cagr, sector,
        cyclicality_z, is_peak, base_mos,
        sbc_pct_fcf, op_leverage, upstream_leak,
        strategic_lev, multiple_premium, implied_cagr,
        calc_input: 'CalculationInput' = None,
        roe=None,
        state=None,
        reverse_dcf_signal=None,
    ) -> ConvictionResult:

        result = ConvictionResult(ticker=ticker)

        if calc_input and calc_input.lifecycle_thresholds:
            thresholds = calc_input.lifecycle_thresholds
        else:
            # Fallback for old tests that don't inject it properly (though they should)
            class MockStage:
                value = "growth_compounder"
                name = "Growth Compounder (Profitable, Reinvesting)"
            class MockThresholds:
                stage = MockStage()
                cagr_strong = 0.20
                cagr_good = 0.12
                cagr_moderate = 0.07
                cagr_slow = 0.03
                mos_strong = 0.30
                mos_good = 0.15
            thresholds = MockThresholds()

        # Store stage in result for reporting
        result.lifecycle_stage = thresholds.stage.value
        result.lifecycle_label = thresholds.stage.name
        result.multiple_premium = multiple_premium
        result.implied_hist_ratio = (
            implied_cagr / hist_cagr
            if implied_cagr is not None and hist_cagr and hist_cagr > 0
            else None
        )

        # ── Pillar 1: Moat Strength ───────────────────────────────────────────
        p1_score = 1
        p1_reasons = []
        if moat_score is not None:
            for threshold, score, reason in MOAT_THRESHOLDS:
                if moat_score >= threshold:
                    p1_score = score
                    p1_reasons = [f"Moat {moat_score:.1f}/10 — {reason}"]
                    break
        else:
            p1_reasons = ["Moat score unavailable — defaulting to 1"]
        result.p1_moat = PillarScore("Moat Strength", p1_score, 0.25, p1_reasons)

        # ── Pillar 2: Fundamental Health ──────────────────────────────────────
        p2_score, p2_reasons = _p2_score(
            roic, wacc, fcf_margin, net_debt_bn, ebitda_bn, data_quality,
            roic_weight=thresholds.p2_roic_weight,
            fcf_weight=thresholds.p2_fcf_weight,
            debt_weight=thresholds.p2_debt_weight,
            roic_applicable=thresholds.roic_applicable,
            fcf_applicable=thresholds.fcf_margin_applicable,
            stage=thresholds.stage.value,
            sector=sector,
            roe=roe,
        )
        result.p2_health = PillarScore("Fundamental Health", p2_score, 0.25, p2_reasons)

        # ── Pillar 3: Secular Tailwind ────────────────────────────────────────
        p3_score, p3_reasons = _p3_score(
            rev_cagr, hist_cagr, sector, cyclicality_z, is_peak,
            implied_cagr=implied_cagr,
            cagr_strong=thresholds.cagr_strong,
            cagr_good=thresholds.cagr_good,
            cagr_moderate=thresholds.cagr_moderate,
            cagr_slow=thresholds.cagr_slow,
            stage=thresholds.stage.value,
        )
        result.p3_tailwind = PillarScore("Secular Tailwind", p3_score, 0.20, p3_reasons)

        # ── Pillar 4: Margin of Safety ────────────────────────────────────────
        p4_score = 1
        p4_reasons = []
        if base_mos is not None:
            for threshold, score, reason in MOS_THRESHOLDS:
                if base_mos >= threshold:
                    p4_score = score
                    p4_reasons = [f"Base MoS {base_mos:+.1%} — {reason}"]
                    break

            # Reconcile the MoS pillar with the reverse-DCF verdict. A large
            # raw MoS is NOT a real margin of safety when the base-DCF IV that
            # produced it rests on growth the market already prices in — the
            # discount is optical, not undervaluation. Previously P4 printed
            # "fortress margin of safety" even when the reverse-DCF signal was
            # CAUTION/FLAG and the contrarian flagged "Growth Extrapolation
            # Bias" — the framework shouting at itself.
            #
            # The reverse-DCF `signal` is the canonical verdict (it already
            # weighs implied CAGR vs BOTH historical and sector-75th). Cap P4
            # by it so the pillar can never out-rank the verdict:
            #   flag             → cap 2  (extraordinary growth priced — FAIL tier)
            #   caution          → cap 3  (above-norm growth priced — CAUTION tier)
            #   priced_for_growth→ cap 4  (growth premium being paid)
            #   fair_value/deep_value → no cap (genuine discount)
            # Fall back to the constitution implied/historical ratio bands
            # (>2.0 FAIL, >1.3 CAUTION) when the signal isn't available, so the
            # pillar and the lead's constitution check stay consistent.
            _sig = (reverse_dcf_signal or "").strip().lower()
            _cap = None
            _tier = None
            if _sig == "flag":
                _cap, _tier = 2, "reverse-DCF FLAG (extraordinary growth priced)"
            elif _sig == "caution":
                _cap, _tier = 3, "reverse-DCF CAUTION (above-norm growth priced)"
            elif _sig == "priced_for_growth":
                _cap, _tier = 4, "reverse-DCF growth-premium (above historical)"
            elif _sig in ("", None) and implied_cagr is not None and hist_cagr and hist_cagr > 0:
                _ratio = implied_cagr / hist_cagr
                if _ratio > 2.0:
                    _cap, _tier = 2, f"implied CAGR {_ratio:.1f}× historical (FAIL)"
                elif _ratio > 1.3:
                    _cap, _tier = 3, f"implied CAGR {_ratio:.1f}× historical (CAUTION)"
            if _cap is not None and p4_score > _cap:
                # Relabel — never print "fortress" alongside a CAUTION/FLAG
                # verdict. The headline now reflects the optical discount.
                p4_score = _cap
                p4_reasons = [
                    f"⚠ Optical discount — base MoS {base_mos:+.1%} but {_tier}; "
                    f"the discount reflects priced-in growth, not undervaluation"
                ]
        else:
            p4_reasons = ["Margin of safety unavailable — defaulting to 1"]
        result.p4_mos = PillarScore("Margin of Safety", p4_score, 0.20, p4_reasons)

        # ── Pillar 5: Leadership Quality ──────────────────────────────────────
        try:
            op_lev_float = float(op_leverage) if op_leverage is not None else None
        except (TypeError, ValueError):
            op_lev_float = None
        p5_score, p5_reasons = _p5_score(sbc_pct_fcf, op_lev_float, upstream_leak, strategic_lev)
        result.p5_leadership = PillarScore("Leadership Quality", p5_score, 0.10, p5_reasons)

        # ── Raw total ─────────────────────────────────────────────────────────
        result.raw_total = p1_score + p2_score + p3_score + p4_score + p5_score
        result.capped_total = result.raw_total

        # ── Section 5.2 — Multiple-justified cap ──────────────────────────────
        # Cap at 18 if: premium > 100% AND implied CAGR > 2× historical
        if (multiple_premium is not None and multiple_premium > 1.0
                and result.implied_hist_ratio is not None and result.implied_hist_ratio > 2.0):
            if result.raw_total > 18:
                result.capped_total = 18
                result.cap_applied = True
                result.cap_reason = (
                    f"Multiple-justified cap: {multiple_premium:+.0%} EV/EBITDA premium "
                    f"AND implied CAGR {result.implied_hist_ratio:.1f}× historical"
                )

        # ── Reflexivity flags (Section 10.6) ──────────────────────────────────
        if sbc_pct_fcf is not None and sbc_pct_fcf > 10:
            result.reflexivity_flags.append(
                f"High-SBC company ({sbc_pct_fcf:.1f}% of FCF) — "
                f"talent retention through equity creates reflexivity risk. "
                f"Bear case widened: stock decline triggers attrition → revenue impairment."
            )
            result.reflexivity_cap = True

        # Simple check for acquisitive behavior from moat evidence
        if moat_score and moat_score < 6 and p1_score <= 2:
            pass  # Would add acquisitive flag if we had M&A data

        # ── Position tier ─────────────────────────────────────────────────────
        total = result.capped_total
        if total >= 23:
            result.position_tier    = "high_conviction"
            result.position_size_pct = "Exceptional — all framework criteria met"
        elif total >= 20:
            result.position_tier    = "conviction"
            result.position_size_pct = "Strong — minor valuation or quality gap"
        elif total >= 15:
            result.position_tier    = "monitor"
            result.position_size_pct = "Investable quality — price or risk concern"
        else:
            result.position_tier    = "pass"
            result.position_size_pct = "Below minimum analytical threshold"

        if result.reflexivity_cap:
            result.position_size_pct += " (capped at 75% due to reflexivity flag)"

        # ── Map to legacy -10/+10 scale for lead agent compatibility ──────────
        # 5-25 → -10 to +10 linear mapping
        # Score 5  → -10 (absolute avoid)
        # Score 15 → 0   (neutral)
        # Score 25 → +10 (strong buy)
        result.conviction_score = round((result.capped_total - 15) * (20 / 20)) - 0
        result.conviction_score = max(-10, min(10, result.conviction_score))

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _safe(v) -> Optional[float]:
        if v is None:
            return None
        try:
            f = float(v)
            return None if math.isnan(f) else f
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _robust_cagr(series) -> Optional[float]:
        import numpy as np
        candidates = []
        rev_now = float(series.iloc[-1]) if len(series) > 0 else None
        if not rev_now:
            return None
        for y in [3, 5, 7, 10]:
            if len(series) >= y:
                r0 = float(series.iloc[-y])
                if r0 > 0:
                    candidates.append((rev_now / r0) ** (1/y) - 1)
        if len(candidates) >= 3:
            s = sorted(candidates)
            return float(np.median(s[1:-1]))
        elif candidates:
            return float(np.median(candidates))
        return None




# ─────────────────────────────────────────────────────────────────────────────
# LangGraph integration — drop-in for lead_agent
# ─────────────────────────────────────────────────────────────────────────────

def score_conviction(state: Dict, calc_input: 'CalculationInput') -> Dict:
    """
    Standalone function for use in LangGraph or lead_agent.

    Returns dict with conviction_score (legacy -10/+10) plus full
    pillar breakdown for inclusion in the final report.

    Usage in lead_agent:
        from aletheia.tools.conviction_scorer import score_conviction
        conviction_data = score_conviction(state, calc_input)
        conviction = conviction_data["conviction_score"]   # replaces LLM score
    """
    ticker = state.get("ticker", "UNKNOWN")
    scorer = ConvictionScorer()
    
    result = scorer.score_from_state(ticker, state, calc_input)
    print(result.summary())
    return result.to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# CLI — score all universe tickers from saved reports
# ─────────────────────────────────────────────────────────────────────────────

# CLI entrypoint removed to avoid architectural violations.
