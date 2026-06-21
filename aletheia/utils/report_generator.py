"""
aletheia/utils/report_generator.py

Generates executive reports (HTML + Markdown) from the full report JSON.

Fixes vs original:
  1. Gross/FCF margin double-multiplication bug in HTML (was showing 1800%)
  2. Phase 2 valuation data (3-scenario DCF, reverse DCF, NorthWestern multiples)
     is now fully rendered in all three report formats
  3. Constitution checks rendered with pass/fail indicators
  4. Intrinsic value now reads from phase2_valuation (not null dcf_model)
  5. WACC reads from phase2_valuation.wacc (live computed) not dcf_model

Design rule: NEVER default missing numeric values to 0.
Missing renders as "N/A" to avoid fake 0% WACC, $0 intrinsic value.
"""

from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple


# Decision-condition observable evaluator — twin of the live UI version
# in ``aletheia/ui/deep_dive_view.py``. Parses the canonical
# ``phase2.field op value`` expression and returns True/False/None.
# Keep behavior in sync with the UI evaluator so the HTML report's
# MET/WATCHING status agrees with the on-screen dashboard.
_OBSERVABLE_RE = re.compile(
    r"^\s*(?P<path>[A-Za-z_][\w.]*)\s*(?P<op>>=|<=|==|>|<)\s*"
    r"(?P<value>-?\d+(?:\.\d+)?)\s*$"
)


def _evaluate_observable(
    observable: Optional[str], p2v: Dict[str, Any]
) -> Optional[bool]:
    """Evaluate a decision-condition ``observable`` expression against the
    phase2 valuation block. Returns True when the trigger is met now,
    False when not, None when the expression is free-form text or the
    path doesn't resolve.

    Only ``phase2.X`` paths are supported — the thesis_synthesizer prompt
    enforces phase2-rooted observables, and the report JSON doesn't carry
    a separate ``dcf`` root the way the live dashboard does."""
    if not observable or not isinstance(observable, str):
        return None
    m = _OBSERVABLE_RE.match(observable)
    if m is None:
        return None
    path = m.group("path")
    op = m.group("op")
    try:
        threshold = float(m.group("value"))
    except ValueError:
        return None
    parts = path.split(".")
    if not parts or parts[0] != "phase2":
        return None
    cur: Any = p2v
    for k in parts[1:]:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            cur = getattr(cur, k, None)
        if cur is None:
            return None
    try:
        actual = float(cur)
    except (TypeError, ValueError):
        return None
    if op == ">":  return actual >  threshold
    if op == "<":  return actual <  threshold
    if op == ">=": return actual >= threshold
    if op == "<=": return actual <= threshold
    if op == "==": return actual == threshold
    return None


class ReportGenerator:
    """
    Generates HTML + two Markdown reports from the Aletheia report JSON.
    All four report files written to serving/latest per ticker.
    """

    def __init__(self, output_dir: str = "valuation_data/serving/latest"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def generate_html(self, ticker: str, data: Dict[str, Any]) -> str:
        """Full Deep-Dive parity HTML — every section the on-screen Deep
        Dive renders is mirrored here. Page is print-friendly so browser
        ⌘P → "Save as PDF" produces a clean export."""
        meta  = data.get("generated_at") or datetime.now().isoformat()
        econ  = data.get("1_economic_reality") or {}
        fin   = data.get("2_financial_translation") or {}
        risk  = data.get("3_capital_structure_risk") or {}
        val   = data.get("4_valuation_synthesis") or {}
        thesis = val.get("investment_thesis") or {}
        thesis_synth = val.get("thesis_synthesis") or {}
        contrarian = val.get("contrarian_analysis") or {}
        p2     = val.get("phase2_valuation") or {}

        pillar_scores = thesis.get("pillar_scores") or {}
        moat = econ.get("moat") or {}
        vc   = econ.get("value_chain") or {}
        sc   = econ.get("strategic_context") or {}
        bm   = econ.get("business_model") or {}
        industry = econ.get("industry_structure") or {}
        # Quantitative detail block (added by lead.py post Stage 4).
        # Renders empty-safe when the block is missing on legacy reports.
        metrics = data.get("5_financial_metrics") or {}
        # Current-State Awareness payload (attached by lead.py before write).
        current_state = data.get("current_state") or {}
        # Downside-protection payload (memo §8; attached by lead.py).
        downside_protection = data.get("downside_protection") or {}
        # Discount-rate detail (memo §7; attached by lead.py).
        wacc_analysis = data.get("wacc_analysis") or {}
        # Bottom-up business analysis (§4) + assumption grounding (keystone).
        business_analysis = data.get("business_analysis") or {}
        assumption_grounding = data.get("assumption_grounding") or {}
        # Market context (memo §8): earnings surprises, ratings, ESG, recent news.
        market_context = data.get("market_context") or {}

        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Deep Dive: {ticker}</title>
  <style>{self._get_css()}</style>
</head>
<body>
  <div class="container">
    {self._render_header(ticker, meta, thesis)}
    {self._render_cyclicality_banner(industry)}
    {self._render_toc()}
    {self._section(1,
        self._render_executive_decision(thesis, p2, downside_protection)
        + f'<div class="grid-2">{self._render_executive_summary(thesis)}'
        + f'{self._render_valuation_card(p2, risk)}</div>',
        gap="tier reconciliation (scorecard vs prose) not auto-checked")}
    {self._section(2,
        self._render_pt_values(current_state)
        + self._render_cross_source(current_state, wacc_analysis),
        gap="peer set is the coarse sector table (e.g. LDOS→'Technology')")}
    {self._section(3, self._render_current_state(current_state),
        gap="only Y1 growth reconciled; margin/capex, pattern detection, "
            "event→assumption link not yet built")}
    {self._section(4,
        self._render_business_snapshot(bm)
        + self._render_saas_metrics(p2)
        + self._render_business_analysis(business_analysis)
        + self._render_historical_fundamentals(metrics)
        + self._render_economic_engine(econ)
        + self._render_financial_engine(fin),
        gap="market share, TAM, contracts, unit economics pending structured "
            "extraction (bottom-up Phases 2-3)")}
    {self._section(5,
        self._render_pillar_scorecard(pillar_scores)
        + self._render_moat_detail(moat)
        + self._render_value_chain_detail(vc)
        + self._render_strategic_context_detail(sc, business_analysis),
        gap="current-state pillar exists but is not in the scored 25-pt total")}
    {self._section(6,
        self._render_value_source_decomposition(p2)
        + self._render_valuation_methods(p2)
        + self._render_bank_valuation_methods(p2)
        + self._render_assumption_grounding(assumption_grounding)
        + self._render_specialized_valuation(p2)
        + self._render_rate_base_sotp(p2)
        + self._render_mlp_valuation(p2)
        + self._render_residual_income(p2)
        + self._render_scenario_triangle(p2)
        + self._render_phase2_section(p2)
        + self._render_reverse_dcf_detail(p2)
        + self._render_dcf_assumptions(metrics)
        + self._render_comprehensive_ratios(metrics),
        gap="equal scenario weighting (20/20/20/20/20); no 3-stage phased "
            "assumptions")}
    {self._section(7,
        self._render_wacc_analysis(wacc_analysis)
        + self._render_wacc_build(metrics),
        gap="premia shown but not applied; bear WACC not full-risk-adjusted")}
    {self._section(8,
        self._render_sector_market(current_state)
        + self._render_market_context(market_context),
        gap="cross-signal synthesis (integrating the four signals) not built")}
    {self._section(9,
        self._render_downside_protection(downside_protection)
        + self._render_cads_flag(p2)
        + self._render_contrarian_block(contrarian)
        + self._render_risk_engine(risk, p2),
        gap="contrarian↔conclusion reconciliation; drawdown history & "
            "portfolio correlation absent")}
    {self._section(10, self._render_structured_thesis(thesis_synth, p2),
        gap="conditions use CAGR metrics, not $ price levels; no scaling ladder")}
    {self._section(11,
        self._render_audit_trail(current_state)
        + self._render_constitution(thesis),
        gap="schema-contract flags not surfaced prominently in the memo")}
    {self._section(12, self._render_required_judgment(thesis_synth))}
    <div class="footer">
      Generated by Aletheia AI | {self._safe_date(meta)} | CONFIDENTIAL
    </div>
  </div>
</body>
</html>"""

        path = self.output_dir / f"{ticker}_Executive_Report.html"
        path.write_text(html, encoding="utf-8")
        return str(path)

    # `generate_markdown` (Executive MD) was a strict subset of
    # `generate_detailed_markdown` with no unique consumer. Removed in
    # the report-consolidation pass. The Detailed MD is now the single
    # markdown surface.

    def generate_detailed_markdown(self, ticker: str, data: Dict[str, Any]) -> str:
        meta   = data.get("generated_at") or datetime.now().isoformat()
        econ   = data.get("1_economic_reality") or {}
        fin    = data.get("2_financial_translation") or {}
        risk   = data.get("3_capital_structure_risk") or {}
        val    = data.get("4_valuation_synthesis") or {}
        thesis = val.get("investment_thesis") or {}
        thesis_synth = val.get("thesis_synthesis") or {}
        p2     = val.get("phase2_valuation") or {}

        conviction = thesis.get("conviction_score")
        narrative  = (thesis.get("narrative") or "No narrative available.").strip()
        checks     = thesis.get("constitution_checks") or []

        cf     = (fin.get("clean_financials") or {})
        ratios = fin.get("ratios") or {}
        qs     = fin.get("quality_screens") or {}
        clf    = fin.get("cleaning_flags") or {}
        moat   = econ.get("moat") or {}
        vc     = econ.get("value_chain") or {}

        p2_dcf = p2.get("three_scenario_dcf") or {}
        rdcf   = p2.get("reverse_dcf") or {}
        md_dec = p2.get("multiple_decomposition") or {}
        wacc   = self._to_float(p2.get("wacc")) or \
                 self._to_float(self._get(risk, ["capital_stack", "wacc"]))

        base   = p2_dcf.get("base") or {}
        bear   = p2_dcf.get("bear") or {}
        bull   = p2_dcf.get("bull") or {}

        lines: List[str] = []

        lines += [
            f"# 📘 Detailed Valuation Report: {ticker}",
            f"**Date**: {self._safe_date(meta)}",
            "**Package**: Full Diligence",
            "**Status**: GENERATED",
            "\n---\n",
            "## 1. 📝 Investment Thesis",
            f"### Conviction Score: {conviction} (on -10 to +10 scale)",
            narrative,
            "\n---\n",
        ]

        # Structured thesis from thesis_synthesizer (Phase 1.5+). Only
        # rendered when present — legacy reports fall back to the
        # narrative summary above.
        structured_md = self._render_structured_thesis_md(thesis_synth, p2)
        if structured_md:
            lines += [structured_md, "\n---\n"]

        # Phase 2 DCF — engine scenarios. These bound the analytically
        # defensible IV range by tilting EVERY assumption lever (terminal
        # growth, Y1-5/Y6-10 growth, terminal margin, WACC) in the same
        # direction simultaneously. They are NOT thesis-grade upside/
        # downside; they are an "all levers compounded" reference. For
        # specific-thesis IV scenarios, see the agent-proposed scenarios
        # in the thesis_synthesis section above.
        lines += [
            "## 2. 🎯 Engine DCF Scenarios (Phase 2)",
            "",
            "_Engine scenarios combine all assumption levers in one direction "
            "— they bound the analytically defensible IV range. For "
            "thesis-grade upside/downside (single-feature changes), see "
            "the agent-proposed scenarios in the structured-thesis section._",
            "",
            "| Scenario | IV/Share | Margin of Safety | EV |",
            "| :--- | :--- | :--- | :--- |",
            f"| Engine Floor (compound bear) | {self._fmt_cur(self._to_float(bear.get('intrinsic_per_share')))} | {self._fmt_pct(self._to_float(bear.get('margin_of_safety')))} | {self._fmt_bn(self._to_float(bear.get('ev')) / 1e9 if bear.get('ev') else None)} |",
            f"| Engine Base (central)        | {self._fmt_cur(self._to_float(base.get('intrinsic_per_share')))} | {self._fmt_pct(self._to_float(base.get('margin_of_safety')))} | {self._fmt_bn(self._to_float(base.get('ev')) / 1e9 if base.get('ev') else None)} |",
            f"| Engine Ceiling (compound bull) | {self._fmt_cur(self._to_float(bull.get('intrinsic_per_share')))} | {self._fmt_pct(self._to_float(bull.get('margin_of_safety')))} | {self._fmt_bn(self._to_float(bull.get('ev')) / 1e9 if bull.get('ev') else None)} |",
            "",
            f"**WACC (live)**: {self._fmt_pct(wacc)}",
            f"**Beta (5Y weekly vs SPY)**: {self._fmt_num(self._to_float(p2.get('beta')), 2)}",
            f"**Risk-Free Rate (^TNX)**: {self._fmt_pct(self._to_float(p2.get('risk_free_rate')))}",
            "\n---\n",
        ]

        # Reverse DCF
        impl = self._to_float(rdcf.get("implied_cagr_10y"))
        hist = self._to_float(rdcf.get("historical_cagr") or rdcf.get("historical_cagr_5y"))
        lines += [
            "## 3. 🔍 Reverse DCF — Market Implied Growth",
            "",
            "| Metric | Value |",
            "| :--- | :--- |",
            f"| Implied 10-Year Revenue CAGR | {self._fmt_pct(impl)} |",
            f"| Historical CAGR (robust median) | {self._fmt_pct(hist)} |",
            f"| Ratio (Implied / Historical) | {self._fmt_ratio(rdcf)} |",
            f"| Signal | {(rdcf.get('signal') or 'N/A').upper()} |",
        ]
        for reason in (rdcf.get("reasons") or []):
            lines.append(f"\n> {reason}")
        lines.append("\n---\n")

        # Multiple decomposition
        lines += [
            "## 4. 📐 Multiple Decomposition (NorthWestern Formula)",
            "",
            "> EV/EBITDA = [NOPAT×(1−g/ROIC)/EBITDA] ÷ (WACC−g)",
            "",
            "| Metric | Value |",
            "| :--- | :--- |",
            f"| Market EV/EBITDA | {self._fmt_num(self._to_float(md_dec.get('market_ev_ebitda')), 1)}× |",
            f"| Justified EV/EBITDA (NorthWestern) | {self._fmt_num(self._to_float(md_dec.get('justified_ev_ebitda')), 1)}× |",
            f"| Premium to Justified | {self._fmt_pct(self._to_float(md_dec.get('premium_pct')))} |",
            f"| ROIC | {self._fmt_pct(self._to_float(md_dec.get('roic')))} |",
            f"| WACC | {self._fmt_pct(wacc)} |",
            f"| ROIC−WACC Spread | {self._fmt_pct(self._to_float(md_dec.get('roic_wacc_spread')))} |",
            f"| Value Creation | {(md_dec.get('value_creation') or 'N/A').upper()} |",
            f"| Signal | {(md_dec.get('signal') or 'N/A').upper()} |",
            "\n---\n",
        ]

        # Constitution
        lines += [
            "## 5. ⚖️ Constitution Checks",
            "",
            self._fmt_checks_md(checks),
            "\n---\n",
        ]

        # Fundamentals
        lines += [
            "## 6. 💹 Financial Analysis (Phase 1 Cleaned)",
            "### Core P&L (TTM)",
            "| Line Item | Value |",
            "| :--- | :--- |",
            f"| Revenue | {self._fmt_bn(cf.get('revenue_bn'))} |",
            f"| EBITDA | {self._fmt_bn(cf.get('ebitda_bn'))} |",
            f"| FCF | {self._fmt_bn(cf.get('fcf_bn'))} |",
            f"| NOPAT | {self._fmt_bn(cf.get('nopat_bn'))} |",
            "",
            "### Balance Sheet",
            "| Line Item | Value |",
            "| :--- | :--- |",
            f"| Net Debt | {self._fmt_bn(cf.get('net_debt_bn'))} |",
            f"| Invested Capital | {self._fmt_bn(cf.get('invested_capital_bn'))} |",
            f"| SBC | {self._fmt_bn(cf.get('sbc_bn'))} |",
            f"| Data Quality Score | {self._fmt_num(self._to_float(cf.get('data_quality')), 2)} |",
            "",
            "### Efficiency Ratios",
            "| Ratio | Value |",
            "| :--- | :--- |",
            f"| Gross Margin | {self._fmt_ratio_pct(ratios.get('gross_margin_pct'))} |",
            f"| EBIT Margin | {self._fmt_ratio_pct(ratios.get('ebit_margin_pct'))} |",
            f"| EBITDA Margin | {self._fmt_ratio_pct(ratios.get('ebitda_margin_pct'))} |",
            f"| FCF Margin | {self._fmt_ratio_pct(ratios.get('fcf_margin_pct'))} |",
            f"| ROIC | {self._fmt_pct(self._to_float(ratios.get('roic')))} |",
            f"| ROE | {self._fmt_pct(self._to_float(ratios.get('roe')))} |",
            f"| SBC % FCF | {self._fmt_ratio_pct(ratios.get('sbc_pct_fcf'))} |",
            f"| Cash Tax Rate | {self._fmt_pct(self._to_float(ratios.get('cash_tax_rate')))} |",
            "\n---\n",
        ]

        # Domain scores
        domain_scores = qs.get("domain_scores") or {}
        if domain_scores:
            lines += [
                "## 7. 🧹 Data Cleaning Quality (9-Domain)",
                "",
                "| Domain | Score |",
                "| :--- | :--- |",
            ]
            domain_labels = {
                "D1_NonRecurring": "D1 Non-Recurring Stripping",
                "D2_JVA": "D2 JVA Separation",
                "D3_EBITNorm": "D3 EBIT Normalization",
                "D4_AccountingPolicy": "D4 Accounting Policy",
                "D5_Lease": "D5 Lease Normalization",
                "D6_Pension": "D6 Pension Cleaning",
                "D7_SBC": "D7 SBC Adjustment",
                "D8_Revenue": "D8 Revenue Recognition",
                "D9_WorkingCapital": "D9 Working Capital",
                "D10_Tax": "D10 Tax Sustainability",
            }
            for key, score in sorted(domain_scores.items()):
                label = domain_labels.get(key, key)
                score_v = self._to_float(score)
                icon = "✅" if score_v and score_v >= 0.9 else "⚠️" if score_v and score_v >= 0.7 else "❌"
                lines.append(f"| {icon} {label} | {self._fmt_num(score_v, 2)} |")

            warn  = clf.get("warning_count", 0)
            err   = clf.get("error_count", 0)
            lines += ["", f"**Warnings**: {warn}  |  **Errors**: {err}", "\n---\n"]

        # Economic reality
        lines += [
            "## 8. 🌎 Economic Reality & Moat",
            f"### Moat Score: {moat.get('score', 'N/A')}/10",
            f"- Switching Costs: {moat.get('switching_costs', 'N/A')}",
            f"- Network Effects: {moat.get('network_effects', 'N/A')}",
            "",
            "### Value Chain",
            f"- Power Ratio: {vc.get('power_ratio', 'N/A')}",
            f"- Upstream Value Leak: {vc.get('upstream_leak', 'N/A')}",
            f"- Strategic Leverage: {vc.get('strategic_leverage', 'N/A')}",
            "\n---\n",
        ]

        # Capital structure
        cap = risk.get("capital_stack") or {}
        rf_data = risk.get("risk_factors") or {}
        liq = rf_data.get("liquidity") or {}
        lines += [
            "## 9. 🛡️ Capital Structure & Risk",
            f"- WACC: {self._fmt_pct(self._to_float(cap.get('wacc')))}",
            f"- Cost of Equity: {self._fmt_pct(self._to_float(cap.get('cost_of_equity')))}",
            f"- Beta: {self._fmt_num(self._to_float(cap.get('beta')), 2)}",
            f"- Liquidity Alert: {liq.get('liquidity_alert', False)}",
            f"- Refinancing Risk Score: {liq.get('refinancing_risk_score', 'N/A')}/10",
        ]

        md = "\n".join(lines)
        path = self.output_dir / f"{ticker}_Detailed_Report.md"
        path.write_text(md, encoding="utf-8")
        return str(path)

    # ─────────────────────────────────────────────────────────────────────────
    # Memo-section scaffolding (the 12-section completeness map)
    # ─────────────────────────────────────────────────────────────────────────

    _SECTION_TITLES = [
        "Executive decision", "Cross-source triangulation",
        "Current-state reconciliation", "Business fundamentals",
        "Quality pillars", "Valuation analysis", "Discount-rate detail",
        "Sector & market context", "Risk / downside protection",
        "Decision framework", "Data quality & audit trail",
        "Required analyst judgment",
    ]

    def _render_toc(self) -> str:
        items = "".join(
            f'<a href="#sec{i+1}" style="display:inline-block;margin:2px 10px 2px 0;'
            f'font-size:12px;color:#2c3e50;text-decoration:none">'
            f'{i+1}. {t}</a>'
            for i, t in enumerate(self._SECTION_TITLES))
        return (f'<div class="card" style="background:#f7f9fb">'
                f'<div style="font-size:12px;font-weight:600;color:#666;'
                f'margin-bottom:4px">MEMO CONTENTS</div>{items}</div>')

    def _section(self, num: int, body: str, gap: str = "") -> str:
        """Wrap a memo section with a numbered anchor header. Returns '' when the
        body is empty (so blank sections don't clutter the memo)."""
        if not (body or "").strip() and not gap:
            return ""
        title = self._SECTION_TITLES[num - 1]
        gap_html = (f'<div style="background:#fcf3f3;border-left:3px solid #c0392b;'
                    f'padding:6px 10px;margin:6px 0;font-size:12px;color:#7a2020">'
                    f'⚠ Open gaps: {gap}</div>') if gap else ""
        return (
            f'<section id="sec{num}" style="margin-top:18px">'
            f'<h2 style="border-bottom:2px solid #2c3e50;padding-bottom:4px">'
            f'{num}. {title}</h2>{gap_html}{body}</section>')

    def _render_executive_decision(self, thesis: Dict[str, Any],
                                   p2: Dict[str, Any],
                                   dp: Dict[str, Any]) -> str:
        """§1 decision banner: tier · MoS · suggested position size."""
        ps = thesis.get("pillar_scores") or {}
        tier = (ps.get("position_tier") or "—").upper()
        total = ps.get("capped_total", "—")
        base = ((p2.get("three_scenario_dcf") or {}).get("base") or {})
        mos = base.get("margin_of_safety")
        mos_s = self._fmt_pct_or_dash(mos)
        siz = (dp or {}).get("position_sizing") or {}
        size_s = siz.get("label", "—")
        return (
            f'<div class="card" style="page-break-inside:avoid">'
            f'<div style="display:flex;gap:28px;flex-wrap:wrap;align-items:flex-end">'
            f'<div><div style="font-size:11px;color:#888">CONVICTION TIER</div>'
            f'<div style="font-size:20px;font-weight:700">{tier}</div>'
            f'<div style="font-size:11px;color:#888">{total}/25 pillars</div></div>'
            f'<div><div style="font-size:11px;color:#888">MARGIN OF SAFETY</div>'
            f'<div style="font-size:20px;font-weight:700">{mos_s}</div></div>'
            f'<div><div style="font-size:11px;color:#888">SUGGESTED SIZE</div>'
            f'<div style="font-size:20px;font-weight:700">{size_s}</div>'
            f'<div style="font-size:11px;color:#888">{siz.get("basis","")}</div></div>'
            f'</div></div>')

    def _render_pt_values(self, cs: Dict[str, Any]) -> str:
        """§2 explicit Wall-Street price-target $ values."""
        s = (cs or {}).get("analyst_sentiment") or {}
        if not s.get("available"):
            return ""
        avg, hi, lo = s.get("target_avg"), s.get("target_high"), s.get("target_low")
        if avg is None and hi is None and lo is None:
            return ""
        def _d(v): return f"${v:,.0f}" if isinstance(v, (int, float)) else "—"
        iu = self._fmt_pct_or_dash(s.get("implied_upside"))
        disp = self._fmt_pct_or_dash(s.get("dispersion"))
        return (
            f'<div class="card"><h3>🎯 Wall Street price targets</h3>'
            f'<p style="margin:2px 0">Average <strong>{_d(avg)}</strong> · '
            f'high {_d(hi)} · low {_d(lo)} · implied {iu} vs price · '
            f'dispersion {disp} ({s.get("dispersion_label","")})</p>'
            f'<p style="font-size:12px;color:#888;margin:2px 0">Consensus '
            f'{s.get("consensus") or "—"}; {s.get("ratings",{}).get("buy",0)} buy / '
            f'{s.get("ratings",{}).get("hold",0)} hold / '
            f'{s.get("ratings",{}).get("sell",0)} sell</p></div>')

    def _render_audit_trail(self, cs: Dict[str, Any]) -> str:
        """§11 override / acknowledgment audit trail (who/when/why)."""
        flags = [f for f in ((cs or {}).get("flags") or []) if f.get("ack")]
        if not flags:
            return ""
        rows = ""
        for f in flags:
            a = f.get("ack") or {}
            rows += (f"<tr><td>{f.get('category','')}</td>"
                     f"<td>{f.get('severity','')}</td>"
                     f"<td>{a.get('decision','')}</td>"
                     f"<td>{a.get('rationale') or '—'}</td>"
                     f"<td>{a.get('decided_by','')}, {(a.get('decided_at') or '')[:10]}</td></tr>")
        return (
            f'<div class="card"><h3>🧾 Flag acknowledgment audit trail</h3>'
            f"<table class='table-styled'><thead><tr><th>Flag</th><th>Severity</th>"
            f"<th>Decision</th><th>Rationale</th><th>By / when</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")

    # The six bottom-up themes (mirror the business-analysis map A–F).
    _BA_THEMES = [
        ("A", "What the company sells", "The product-level reality behind the revenue line", "A. What it sells"),
        ("B", "Market size & capture", "Where the company sits in the market, room to grow", "B. Market size"),
        ("C", "Unit economics", "The economic structure beneath the aggregate margins", "C. Unit economics"),
        ("D", "Growth source decomposition", "Is the company growing the pie or taking share?", "D. Growth source"),
        ("E", "Innovation & trend positioning", "Position relative to technology and demand shifts", "E. Innovation"),
        ("F", "Industry context & dynamics", "Where the industry is in its lifecycle", "F. Industry"),
    ]

    def _render_business_analysis(self, ba: Dict[str, Any]) -> str:
        """§4 bottom-up business analysis — one labeled sub-section per theme
        (A–F), each showing its extracted/derived content + the dimensions still
        pending. Empty-safe."""
        if not ba or not ba.get("available"):
            return ""
        pct = self._fmt_pct_or_dash
        gd = ba.get("growth_decomposition") or {}
        ex = ba.get("extracted") or {}
        cov = ba.get("coverage") or []
        tpl = ba.get("sector_template") or {}

        def _kv(label, val, methodology=None):
            if not val:
                return ""
            extra = (f" <span style='color:#888'>({methodology})</span>"
                     if methodology else "")
            return f"<p style='margin:2px 0'><strong>{label}:</strong> {val}{extra}</p>"

        def _list(label, items, fmt):
            lis = "".join(f"<li>{fmt(i)}</li>" for i in (items or [])[:6])
            return (f"<p style='margin:4px 0 0 0'><strong>{label}</strong></p>"
                    f"<ul style='margin:2px 0 6px 0'>{lis}</ul>") if lis else ""

        def _theme_body(letter):
            if letter == "A":
                return (_list("Product lines", ex.get("product_lines"), lambda p:
                              f"<strong>{p.get('name','')}</strong>"
                              + (f" — {p.get('pricing_model')}" if p.get('pricing_model') else "")
                              + (f" <span style='color:#888'>({p.get('segment')})</span>" if p.get('segment') else ""))
                        + _list("Major customers / contracts", ex.get("major_customers"), lambda c:
                                f"<strong>{c.get('name','')}</strong>"
                                + (f" — {c.get('relationship')}" if c.get('relationship') else "")
                                + (f", recompete {c.get('recompete_or_renewal')}" if c.get('recompete_or_renewal') else "")
                                + (f", {c.get('pct_revenue')} of rev" if c.get('pct_revenue') else ""))
                        + _kv("Notable customers", ", ".join(ex.get("notable_customers") or []))
                        + _kv("Industry verticals", ", ".join(ex.get("industry_verticals") or []))
                        + _kv("Customer concentration", ex.get("customer_concentration"))
                        + _kv("Net retention", ex.get("net_retention"))
                        + _kv("Distribution channels", ", ".join(ex.get("distribution_channels") or [])))
            if letter == "B":
                tam = ba.get("tam") or {}
                tam_html = ""
                if tam.get("tam_estimate"):
                    band = ""
                    if tam.get("tam_low") or tam.get("tam_high"):
                        band = (f" <span style='color:#888'>[{tam.get('tam_low') or '?'} – "
                                f"{tam.get('tam_high') or '?'}]</span>")
                    extra = []
                    if tam.get("tam_approach"):
                        extra.append(f"approach: {tam['tam_approach']}")
                    if tam.get("tam_confidence"):
                        extra.append(f"confidence: {tam['tam_confidence']}")
                    if tam.get("implied_share") is not None:
                        sh = f"implied share {pct(tam['implied_share'])}"
                        if tam.get("implied_share_low") is not None and tam.get("implied_share_high") is not None:
                            sh += f" [{pct(tam['implied_share_low'])}–{pct(tam['implied_share_high'])}]"
                        extra.append(sh)
                    meta = (f" <span style='color:#888'>({'; '.join(extra)})</span>"
                            if extra else "")
                    tam_html = (f"<p style='margin:2px 0'><strong>TAM:</strong> "
                                f"{tam['tam_estimate']}{band}{meta}</p>")
                    if tam.get("tam_methodology"):
                        tam_html += (f"<p style='margin:0 0 4px 0;font-size:11px;color:#888'>"
                                     f"{tam['tam_methodology']}</p>")
                return (tam_html
                        + _kv("Market share", ex.get("market_share"))
                        + _kv("Share trajectory", ex.get("market_share_trajectory"))
                        + _kv("New market / category creation", ex.get("category_creation"))
                        + _kv("Whitespace runway", ex.get("whitespace_runway"))
                        + _kv("Adjacent TAMs", ", ".join(ex.get("adjacent_tams") or [])))
            if letter == "C":
                # Deterministic operating-leverage quant (incremental EBIT margin).
                ol = ba.get("operating_leverage") or {}
                ol_html = ""
                if ol.get("available"):
                    ol_html = (f"<p style='margin:2px 0'><strong>Operating leverage "
                               f"(incremental margin):</strong> {pct(ol.get('incremental_margin'))} "
                               f"vs current {pct(ol.get('current_margin'))} — "
                               f"<em>{ol.get('label','')}</em> "
                               f"<span style='color:#888'>({ol.get('source','')})</span></p>")
                body = (ol_html
                        + _kv("Contract economics", ex.get("contract_economics"))
                        + _kv("CAC / LTV", ex.get("cac_ltv"))
                        + _kv("Unit cost", ex.get("unit_cost"))
                        + _kv("Segment margin trajectory", ex.get("segment_margin_trajectory"))
                        + _kv("Operating leverage (qual.)", ex.get("operating_leverage")))
                seg = ba.get("segment_economics") or {}
                if seg.get("available") and seg.get("segments"):
                    srows = ""
                    for s in seg["segments"]:
                        srows += (f"<tr><td>{s.get('segment','')}</td>"
                                  f"<td style='text-align:right'>{pct(s.get('rev_pct'))}</td>"
                                  f"<td style='text-align:right'>{pct(s.get('yoy_growth'))}</td>"
                                  f"<td style='text-align:right'>{s.get('margin') or '—'}</td>"
                                  f"<td style='color:#666;font-size:11px'>{s.get('margin_trend') or ''}</td></tr>")
                    body += (f"<p style='margin:6px 0 2px 0'><strong>Segment economics</strong> "
                             f"<span style='color:#888'>(FY{seg.get('fiscal_year','')} rev mix from FMP; "
                             f"margins ★ on Stage-4)</span></p>"
                             f"<table class='table-styled'><thead><tr><th>Segment</th>"
                             f"<th style='text-align:right'>% rev</th>"
                             f"<th style='text-align:right'>YoY</th>"
                             f"<th style='text-align:right'>Op margin</th><th>Trend</th>"
                             f"</tr></thead><tbody>{srows}</tbody></table>")
                return body
            if letter == "D":
                out = ""
                if gd.get("available"):
                    breaks = gd.get("break_years") or []
                    if gd.get("ma_separable") is False:
                        m = gd.get("ma_spend") or {}
                        yrs = ", ".join(f"FY{x['year']}" for x in m.get("years", []))
                        out += (f'<p style="margin:2px 0"><strong>Growth source:</strong> raw CAGR '
                                f'{pct(gd.get("raw_cagr"))}; <strong>M&amp;A spend material</strong> '
                                f'({self._fmt_bn_or_dash((m.get("total_spend") or 0)/1e9)} over {yrs}, '
                                f'{pct(m.get("cum_pct_of_revenue"))} of revenue) — organic '
                                f'&le; {pct(gd.get("organic_cagr"))} '
                                f'(<em>not separable from M&amp;A via revenue trends</em>)</p>')
                    else:
                        out += (f'<p style="margin:2px 0"><strong>Growth source:</strong> raw CAGR '
                                f'{pct(gd.get("raw_cagr"))} = organic {pct(gd.get("organic_cagr"))} '
                                f'+ M&A {pct(gd.get("ma_contribution_pp"))}'
                                + (f' (breaks FY{breaks})' if breaks else '')
                                + f' — <em>{gd.get("split","")}</em></p>')
                    if gd.get("share_gain_pp") is not None:
                        out += (f'<p style="margin:2px 0"><strong>Market vs share:</strong> '
                                f'organic {pct(gd.get("organic_cagr"))} vs sector market '
                                f'{pct(gd.get("market_growth_ref"))} → share '
                                f'{pct(gd.get("share_gain_pp"))} (<em>{gd.get("share_label","")}</em>)</p>')
                return out
            if letter == "E":
                return (_kv("R&D pipeline", ex.get("rd_pipeline"))
                        + _kv("Trend positioning", ex.get("trend_positioning"))
                        + _list("New product launches", ex.get("new_product_launches"), lambda l:
                                f"<strong>{l.get('name','')}</strong>"
                                + (f" ({l.get('timing')})" if l.get('timing') else "")
                                + (f" — {l.get('traction')}" if l.get('traction') else ""))
                        + _kv("Disruption risk", ex.get("disruption_risk"))
                        + _kv("Acquisition strategy", ex.get("acquisition_strategy")))
            if letter == "F":
                return (_kv("Industry lifecycle stage", ba.get("lifecycle"))
                        + _kv("Competitive intensity", ex.get("competitive_intensity"))
                        + _kv("Key-product competitive positioning", ex.get("competitive_positioning"))
                        + _kv("Customer power (trajectory)", ex.get("customer_power"))
                        + _kv("Supplier power (trajectory)", ex.get("supplier_power"))
                        + _kv("Regulatory trajectory", ex.get("regulatory_trajectory")))
            return ""

        # Sector emphasis header.
        tpl_html = ""
        if tpl.get("emphasis"):
            tpl_html = (f'<p style="margin:2px 0 8px 0;padding:6px 10px;'
                        f'background:#f7f9fb;border-radius:6px"><strong>Sector emphasis '
                        f'({tpl.get("label","")}):</strong> {" · ".join(tpl["emphasis"])}</p>')

        # Curated peer-set stats (true peers via FMP).
        ps = ba.get("peer_stats") or {}
        if ps.get("available"):
            bits = []
            if ps.get("market_growth_median") is not None:
                bits.append(f"rev CAGR {pct(ps['market_growth_median'])}")
            if ps.get("ev_ebitda_median") is not None:
                bits.append(f"EV/EBITDA {self._fmt_x_or_dash(ps['ev_ebitda_median'])}")
            if ps.get("op_margin_median") is not None:
                bits.append(f"op margin {pct(ps['op_margin_median'])}")
            tpl_html += (f'<p style="margin:2px 0 8px 0"><strong>Peer set</strong> '
                         f'({", ".join(ps.get("peers") or [])}): median '
                         f'{" · ".join(bits)}</p>')

        # One sub-section per theme A–F.
        sections = ""
        for letter, title, subtitle, cov_prefix in self._BA_THEMES:
            body = _theme_body(letter)
            theme_cov = [c for c in cov if c.get("theme", "").startswith(cov_prefix)]
            pend = [c["dimension"] for c in theme_cov if c.get("status") == "pending"]
            na = [c for c in theme_cov if c.get("status") == "n_a"]
            nd = [c["dimension"] for c in theme_cov if c.get("status") == "not_disclosed"]
            prio = {c["dimension"] for c in theme_cov if c.get("priority")}
            pend_html = ""
            if pend:
                marked = ["★ " + d if d in prio else d for d in pend]
                pend_html = (f'<p style="font-size:11px;color:#b0b0b0;margin:2px 0">'
                             f'Pending extraction: {", ".join(marked)}</p>')
            if nd:
                pend_html += (f'<p style="font-size:11px;color:#b0b0b0;margin:2px 0">'
                              f'Not disclosed in filing: {", ".join(nd)}</p>')
            if na:
                na_txt = ", ".join(f"{c['dimension']}"
                                   + (f" ({c['reason']})" if c.get("reason") else "")
                                   for c in na)
                pend_html += (f'<p style="font-size:11px;color:#b0b0b0;margin:2px 0">'
                              f'Not applicable: {na_txt}</p>')
            if not body and not pend_html:
                continue
            sections += (
                f'<div style="margin:8px 0">'
                f'<h4 style="margin:6px 0 2px 0">{letter} · {title}</h4>'
                f'<p style="font-size:11px;color:#888;margin:0 0 4px 0">{subtitle}</p>'
                f'{body or ""}{pend_html}</div>')

        return f"""
  <div class="card" style="page-break-inside:avoid">
  <h3>🔬 Bottom-up business analysis</h3>
  <p style="font-size:12px;color:#666;margin:2px 0 6px 0">
    The business reality beneath the revenue line, by theme.
    {ba.get('n_present','?')}/{ba.get('n_total','?')} dimensions populated
    {f"· {ba.get('n_not_disclosed')} not disclosed " if ba.get('n_not_disclosed') else ''}
    {f"· {ba.get('n_na')} n/a " if ba.get('n_na') else ''}; ★ = sector priority.
  </p>
  {tpl_html}{sections}
  </div>"""

    def _render_assumption_grounding(self, ag: Dict[str, Any]) -> str:
        """Assumption grounding (keystone): engine assumptions vs business-
        grounded references. Shown, not applied. Empty-safe."""
        if not ag or not ag.get("available"):
            return ""
        pct = self._fmt_pct_or_dash
        rows = ""
        for r in ag.get("rows") or []:
            d = r.get("delta")
            dcolor = ("#c0392b" if isinstance(d, (int, float)) and abs(d) >= 0.02
                      else "#3f3f46")
            note = r.get("note", "")
            b = r.get("build_up") or {}
            if b.get("band_low") is not None and b.get("band_high") is not None:
                drivers = []
                if b.get("market_growth") is not None:
                    drivers.append(f"market {pct(b['market_growth'])}")
                if b.get("share_gain") is not None:
                    drivers.append(f"share {pct(b['share_gain'])}")
                if b.get("ma_run_rate"):
                    drivers.append(f"M&A run-rate {pct(b['ma_run_rate'])}")
                note += (f"<br><span style='color:#888'>build-up: "
                         f"{' + '.join(drivers)} → band {pct(b['band_low'])}–{pct(b['band_high'])}</span>")
            comp = r.get("computation") or ""
            ovf = r.get("override_field")
            apply_cell = (f"<span style='color:#2563eb'>{ovf} ← {pct(r.get('override_value'))}</span>"
                          if ovf and r.get("override_value") is not None else "—")
            rows += (f"<tr><td>{r.get('assumption','')}</td>"
                     f"<td style='text-align:right'>{pct(r.get('engine_value'))}</td>"
                     f"<td style='text-align:right'>{pct(r.get('grounded_value'))}</td>"
                     f"<td style='text-align:right;color:{dcolor}'>{pct(d)}</td>"
                     f"<td style='color:#666;font-size:11px'>{comp}</td>"
                     f"<td style='color:#666;font-size:11px'>{note}</td>"
                     f"<td style='font-size:11px'>{apply_cell}</td></tr>")
        return f"""
  <div class="card" style="page-break-inside:avoid">
  <h3>🧲 Assumption grounding (business → top-down bridge)</h3>
  <p style="font-size:12px;color:#666;margin:2px 0 6px 0">
    Each top-down DCF assumption vs a business-grounded reference, with the
    computation shown. The <em>Apply&nbsp;to</em> column names the DCF override
    field + value an analyst can push in the app (validated before it changes
    the IV). Shown for triangulation — not auto-applied.
  </p>
  <table class='table-styled'><thead><tr><th>Assumption</th><th style='text-align:right'>Engine</th>
  <th style='text-align:right'>Grounded</th><th style='text-align:right'>Δ</th><th>Computation</th><th>Basis</th>
  <th>Apply&nbsp;to</th></tr></thead><tbody>{rows}</tbody></table>
  {self._render_margin_capex_reconciliation(ag.get('reconciliation'))}
  </div>"""

    def _render_margin_capex_reconciliation(self, rec: Dict[str, Any]) -> str:
        """Memo #7 — one-line margin/capex reconciliation verdict."""
        if not rec:
            return ""
        tm = rec.get("terminal_margin_verdict")
        cx = rec.get("capex_verdict")
        traj = rec.get("segment_margin_trajectory")
        if not tm and not cx:
            return ""
        return (f'<p style="font-size:12px;color:#444;margin:8px 0 0 0">'
                f'<strong>Margin/CapEx reconciliation (memo&nbsp;#7):</strong> '
                f'terminal margin <em>{tm}</em>'
                f'{f" (segments {traj})" if traj else ""}; '
                f'forward capex <em>{cx}</em>.</p>')

    def _render_required_judgment(self, ts: Dict[str, Any]) -> str:
        """§12 Required analyst judgment — the gaps the framework defers to the
        analyst (from the structured thesis). Empty-safe."""
        raj = (ts or {}).get("required_analyst_judgment") or []
        if not raj:
            return ""
        items = "".join(f"<li>{j}</li>" for j in raj)
        return (f'<div class="card"><h3>🧑‍⚖️ Required analyst judgment</h3>'
                f'<p style="font-size:12px;color:#666;margin:2px 0">Items the '
                f'framework intentionally defers to the analyst.</p>'
                f"<ul style='margin:4px 0 0 0'>{items}</ul></div>")

    # ─────────────────────────────────────────────────────────────────────────
    # HTML section renderers
    # ─────────────────────────────────────────────────────────────────────────

    def _render_header(self, ticker: str, meta: str, thesis: Dict[str, Any]) -> str:
        conviction = thesis.get("conviction_score")
        conv_num   = int(conviction) if conviction is not None else 0
        color = "#27ae60" if conv_num >= 3 else "#f39c12" if conv_num >= 0 else "#c0392b"
        return f"""
<div style="display:flex;justify-content:space-between;align-items:center">
  <div>
    <h1>{ticker} Investment Memo</h1>
    <div style="color:#7f8c8d">Generated on {self._safe_date(meta)}</div>
  </div>
  <div style="text-align:center">
    <div class="score-circle" style="background:{color}">{conv_num:+d}</div>
    <div style="margin-top:5px;font-weight:bold">CONVICTION</div>
  </div>
</div>""".strip()

    def _render_executive_summary(self, thesis: Dict[str, Any]) -> str:
        narrative = (thesis.get("narrative") or "No narrative available.").replace("\n", "<br>")
        return f"""
<div class="card">
  <h3>📝 Executive Summary</h3>
  <p style="line-height:1.6">{narrative}</p>
</div>""".strip()

    def _render_structured_thesis(
        self, ts: Dict[str, Any], p2: Optional[Dict[str, Any]] = None
    ) -> str:
        """Full thesis_synthesizer output: bull/base/bear cases with
        cited_signals, decision_conditions, update_conditions, confidence,
        time_horizon. Empty string if no thesis_synthesis (legacy reports
        pre-Phase 1.5).

        ``p2`` (phase2_valuation) is threaded in so each decision-condition
        row can evaluate its ``observable`` expression and render a live
        MET/WATCHING status chip — without it, the table only encodes
        the analyst-target priority, not the current trigger state."""
        p2 = p2 or {}
        if not ts or not ts.get("thesis_statement"):
            return ""

        statement = ts.get("thesis_statement", "")
        confidence = (ts.get("thesis_confidence") or "—").upper()
        horizon = (ts.get("time_horizon") or "—").replace("_", " ").upper()
        conf_color = {
            "HIGH": "#10b981", "MEDIUM": "#f59e0b", "LOW": "#ef4444",
            "INSUFFICIENT_SIGNAL": "#a1a1aa",
        }.get(confidence, "#a1a1aa")

        case_blocks = []
        for label, key, color in [
            ("BULL CASE", "bull_case", "#10b981"),
            ("BASE CASE", "base_case", "#f59e0b"),
            ("BEAR CASE", "bear_case", "#ef4444"),
        ]:
            cc = ts.get(key) or {}
            claim = cc.get("claim", "") or ""
            if not claim:
                continue
            cites = cc.get("cited_signals") or []
            cite_chips = ""
            if cites:
                # `display:inline-block` + `white-space:nowrap` keeps each
                # chip as one cohesive token (so spaces inside citation
                # paths like "Housing Turnover Rebound" don't split the
                # chip mid-word). Parent `display:flex; flex-wrap:wrap`
                # handles row breaks BETWEEN chips instead of letting them
                # overflow the card's right edge — the bug fix here.
                chips = "".join(
                    f'<span style="display:inline-block;white-space:nowrap;'
                    f'font-family:monospace;font-size:10px;'
                    f'color:#71717a;background:#f4f4f5;padding:2px 6px;'
                    f'border-radius:3px;">{c}</span>'
                    for c in cites
                )
                cite_chips = (
                    f'<div style="margin-top:8px;display:flex;flex-wrap:wrap;'
                    f'align-items:baseline;gap:4px;">'
                    f'<span style="font-family:monospace;font-size:10px;'
                    f'color:#71717a;text-transform:uppercase;letter-spacing:0.5px;'
                    f'margin-right:4px;">Cites:</span>{chips}</div>'
                )
            case_blocks.append(
                f'<div style="border:1px solid #e4e4e7;border-left:4px solid {color};'
                f'padding:14px 18px;border-radius:0 6px 6px 0;margin-bottom:10px;">'
                f'<div style="font-family:monospace;font-size:11px;'
                f'color:{color};letter-spacing:0.7px;text-transform:uppercase;'
                f'margin-bottom:6px;font-weight:700;">{label}</div>'
                f'<div style="font-size:14px;line-height:1.7;">{claim}</div>'
                f'{cite_chips}</div>'
            )

        # Decision conditions table
        dcs = ts.get("decision_conditions") or []
        dc_rows = []
        action_color = {
            "EXIT": "#ef4444", "TRIM": "#ef4444", "SELL": "#ef4444", "PASS": "#ef4444",
            "HOLD": "#f59e0b", "WATCH": "#f59e0b", "REVIEW": "#f59e0b",
            "ADD": "#10b981", "BUY": "#10b981", "ACCUMULATE": "#10b981",
        }
        priority_color = {"red": "#ef4444", "amber": "#f59e0b", "green": "#10b981"}
        for dc in dcs:
            pri = (dc.get("priority") or "").lower()
            act = (dc.get("action") or "").upper()
            trigger = dc.get("trigger", "") or "—"
            obs = dc.get("observable", "") or ""
            p_color = priority_color.get(pri, "#a1a1aa")
            a_color = action_color.get(act, "#a1a1aa")

            is_met = _evaluate_observable(obs, p2)
            if is_met is True:
                status_html = (
                    '<span style="font-family:monospace;font-size:10px;'
                    'font-weight:700;letter-spacing:0.5px;color:#10b981;">'
                    '● MET</span>'
                )
            elif is_met is False:
                status_html = (
                    '<span style="font-family:monospace;font-size:10px;'
                    'font-weight:700;letter-spacing:0.5px;color:#f59e0b;">'
                    '● WATCHING</span>'
                )
            else:
                status_html = (
                    '<span style="font-family:monospace;font-size:10px;'
                    'color:#a1a1aa;">—</span>'
                )

            dc_rows.append(
                f'<tr style="border-bottom:1px solid #e4e4e7">'
                f'<td style="padding:8px 12px;color:{p_color};font-family:monospace;'
                f'font-size:11px;font-weight:700;text-transform:uppercase;">● {pri or "—"}</td>'
                f'<td style="padding:8px 12px;">{status_html}</td>'
                f'<td style="padding:8px 12px;font-size:13px;">{trigger}'
                f'<div style="font-family:monospace;font-size:11px;color:#71717a;'
                f'margin-top:4px;">{obs}</div></td>'
                f'<td style="padding:8px 12px;text-align:right;">'
                f'<span style="font-family:monospace;font-size:10px;font-weight:700;'
                f'letter-spacing:0.6px;color:{a_color};border:1px solid {a_color};'
                f'padding:3px 9px;border-radius:3px;">{act or "—"}</span></td>'
                f'</tr>'
            )
        dc_table = ""
        if dc_rows:
            dc_table = (
                f'<h4 style="margin-top:18px;">Decision Conditions</h4>'
                f'<table style="width:100%;border-collapse:collapse;'
                f'border:1px solid #e4e4e7;border-radius:6px;overflow:hidden;">'
                f'<thead><tr style="background:#f4f4f5;">'
                f'<th style="padding:10px 12px;text-align:left;font-size:11px;'
                f'text-transform:uppercase;letter-spacing:0.5px;color:#71717a;">Priority</th>'
                f'<th style="padding:10px 12px;text-align:left;font-size:11px;'
                f'text-transform:uppercase;letter-spacing:0.5px;color:#71717a;">Status</th>'
                f'<th style="padding:10px 12px;text-align:left;font-size:11px;'
                f'text-transform:uppercase;letter-spacing:0.5px;color:#71717a;">Trigger / Observable</th>'
                f'<th style="padding:10px 12px;text-align:right;font-size:11px;'
                f'text-transform:uppercase;letter-spacing:0.5px;color:#71717a;">Action</th>'
                f'</tr></thead>'
                f'<tbody>{"".join(dc_rows)}</tbody></table>'
            )

        # Required analyst judgment + Update conditions
        raj = ts.get("required_analyst_judgment") or []
        upd = ts.get("update_conditions") or []
        side_blocks = ""
        if raj or upd:
            raj_html = ""
            if raj:
                raj_html = (
                    f'<div style="flex:1;"><h4>Required Analyst Judgment</h4>'
                    f'<p style="font-size:12px;color:#71717a;margin-top:-6px;">'
                    f'Gaps the framework defers to the analyst</p>'
                    f'<ul style="font-size:13px;line-height:1.6;">'
                    + "".join(f"<li>{r}</li>" for r in raj) + "</ul></div>"
                )
            upd_html = ""
            if upd:
                upd_html = (
                    f'<div style="flex:1;"><h4>Update Conditions</h4>'
                    f'<p style="font-size:12px;color:#71717a;margin-top:-6px;">'
                    f'What would invalidate this thesis</p>'
                    f'<ul style="font-size:13px;line-height:1.6;">'
                    + "".join(f"<li>{u}</li>" for u in upd) + "</ul></div>"
                )
            side_blocks = (
                f'<div style="display:flex;gap:24px;margin-top:18px;">'
                f'{raj_html}{upd_html}</div>'
            )

        return f"""
<div class="card" style="page-break-inside:avoid">
  <h3>🧭 Structured Investment Thesis</h3>
  <div style="font-family:monospace;font-size:11px;color:#71717a;
              margin-bottom:12px;letter-spacing:0.3px;">
    Confidence: <span style="color:{conf_color};font-weight:700">{confidence}</span>
    &nbsp;·&nbsp;
    Horizon: <span style="font-weight:700">{horizon}</span>
  </div>
  <div style="border-left:4px solid {conf_color};padding:14px 20px;
              background:#fafafa;border-radius:0 6px 6px 0;
              font-size:15px;line-height:1.7;margin-bottom:18px;font-weight:500;">
    {statement}
  </div>
  {"".join(case_blocks)}
  {dc_table}
  {side_blocks}
</div>""".strip()

    def _render_valuation_card(self, p2: Dict[str, Any], risk: Dict[str, Any]) -> str:
        dcf3 = p2.get("three_scenario_dcf") or {}
        base = dcf3.get("base") or {}
        bear = dcf3.get("bear") or {}
        bull = dcf3.get("bull") or {}
        base_iv  = self._to_float(base.get("intrinsic_per_share"))
        base_mos = self._to_float(base.get("margin_of_safety"))
        wacc = self._to_float(p2.get("wacc")) or \
               self._to_float(self._get(risk, ["capital_stack", "wacc"]))
        rdcf = p2.get("reverse_dcf") or {}
        impl = self._to_float(rdcf.get("implied_cagr_10y"))
        hist = self._to_float(rdcf.get("historical_cagr") or rdcf.get("historical_cagr_5y"))

        mos_color = "green" if base_mos and base_mos > 0 else "red"

        # Engine scenarios bound the analytically defensible IV range by
        # tilting all assumption levers in one direction simultaneously.
        # See the structured-thesis section (above) for thesis-grade
        # upside/downside scenarios proposed by upstream agents.
        return f"""
<div class="card">
  <h3>🎯 Valuation Synthesis (Phase 2)</h3>
  <div class="metric-box" style="margin-bottom:10px">
    <span class="metric-val" style="color:{mos_color}">{self._fmt_pct(base_mos)}</span>
    <span class="metric-label">Base Margin of Safety</span>
  </div>
  <table class="table-styled">
    <tr><td title="Compound bear: every assumption lever tilted bear">Engine Floor / Share</td>
        <td style="font-weight:bold">{self._fmt_cur(self._to_float(bear.get('intrinsic_per_share')))}</td></tr>
    <tr><td title="Central case">Engine Base / Share</td>
        <td style="font-weight:bold;color:{mos_color}">{self._fmt_cur(base_iv)}</td></tr>
    <tr><td title="Compound bull: every assumption lever tilted bull">Engine Ceiling / Share</td>
        <td style="font-weight:bold">{self._fmt_cur(self._to_float(bull.get('intrinsic_per_share')))}</td></tr>
    <tr><td>WACC (live)</td><td>{self._fmt_pct(wacc)}</td></tr>
    <tr><td>Implied CAGR</td><td>{self._fmt_pct(impl)}</td></tr>
    <tr><td>Historical CAGR</td><td>{self._fmt_pct(hist)}</td></tr>
  </table>
  <div style="font-size:0.78em;color:#7f8c8d;margin-top:8px;line-height:1.5">
    Engine scenarios combine all assumption levers in one direction —
    they bound the analytically defensible range. For thesis-grade
    upside/downside, see the structured-thesis section.
  </div>
</div>""".strip()

    def _render_phase2_section(self, p2: Dict[str, Any]) -> str:
        rdcf   = p2.get("reverse_dcf") or {}
        md_dec = p2.get("multiple_decomposition") or {}

        impl   = self._to_float(rdcf.get("implied_cagr_10y"))
        hist   = self._to_float(rdcf.get("historical_cagr") or rdcf.get("historical_cagr_5y"))
        signal = (rdcf.get("signal") or "").upper()

        ev_market = self._to_float(md_dec.get("market_ev_ebitda"))
        ev_just   = self._to_float(md_dec.get("justified_ev_ebitda"))
        premium   = self._to_float(md_dec.get("premium_pct"))
        spread    = self._to_float(md_dec.get("roic_wacc_spread"))
        vc_signal = (md_dec.get("signal") or "").upper()

        spread_color = "#27ae60" if spread and spread > 0 else "#c0392b"
        prem_color   = "#c0392b" if premium and premium > 0 else "#27ae60"

        reasons_html = "".join(
            f"<li style='margin-bottom:6px'>{r}</li>"
            for r in (rdcf.get("reasons") or [])
        )

        return f"""
<h2>Phase 2: Quantitative Valuation Intelligence</h2>
<div class="grid-2">
  <div class="card">
    <h4>🔍 Reverse DCF — What Is the Market Pricing In?</h4>
    <table class="table-styled">
      <tr><td>Implied 10Y CAGR</td><td style="font-weight:bold">{self._fmt_pct(impl)}</td></tr>
      <tr><td>Historical CAGR</td><td>{self._fmt_pct(hist)}</td></tr>
      <tr><td>Ratio (Impl/Hist)</td><td style="color:{('#c0392b' if impl and hist and impl/hist > 1.5 else '#27ae60')}">{self._fmt_ratio(rdcf)}</td></tr>
      <tr><td>Signal</td><td><b>{signal}</b></td></tr>
    </table>
    {"<ul style='font-size:0.85em;padding-left:18px;margin-top:10px;color:#555'>" + reasons_html + "</ul>" if reasons_html else ""}
  </div>
  <div class="card">
    <h4>📐 Multiple Decomposition (NorthWestern Formula)</h4>
    <div style="font-size:0.8em;color:#888;margin-bottom:8px">
      EV/EBITDA = [NOPAT×(1−g/ROIC)/EBITDA] ÷ (WACC−g)
    </div>
    <table class="table-styled">
      <tr><td>Market EV/EBITDA</td><td style="font-weight:bold">{self._fmt_num(ev_market, 1)}×</td></tr>
      <tr><td>Justified EV/EBITDA</td><td style="color:#27ae60">{self._fmt_num(ev_just, 1)}×</td></tr>
      <tr><td>Premium to Justified</td><td style="color:{prem_color}">{self._fmt_pct(premium)}</td></tr>
      <tr><td>ROIC−WACC Spread</td><td style="color:{spread_color}">{self._fmt_pct(spread)}</td></tr>
      <tr><td>Signal</td><td><b>{vc_signal}</b></td></tr>
    </table>
  </div>
</div>""".strip()

    def _render_constitution(self, thesis: Dict[str, Any]) -> str:
        checks = thesis.get("constitution_checks") or []
        if not checks:
            return ""

        rows = ""
        for check in checks:
            check_str = str(check)
            is_pass   = "PASS" in check_str or "✅" in check_str
            is_warn   = "CAUTION" in check_str or "⚠" in check_str
            bg     = "#d5f5e3" if is_pass else "#fef9e7" if is_warn else "#fadbd8"
            border = "#27ae60" if is_pass else "#f39c12" if is_warn else "#c0392b"
            icon   = "✅" if is_pass else "⚠️" if is_warn else "❌"
            # Clean up unicode escape sequences
            clean = check_str.replace("✅", "").replace("❌", "").replace("⚠️", "").strip()
            rows += f"""<tr style="background:{bg};border-left:4px solid {border}">
              <td style="padding:8px 6px;font-size:1.3em">{icon}</td>
              <td style="padding:8px">{clean}</td>
            </tr>"""

        return f"""
<h2>⚖️ Constitution Compliance</h2>
<div class="card" style="padding:0;overflow:hidden">
  <table class="table-styled" style="margin:0">
    <thead><tr>
      <th style="width:40px"></th>
      <th>Check</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>""".strip()

    def _render_economic_engine(self, econ: Dict[str, Any]) -> str:
        moat = econ.get("moat") or {}
        vc   = econ.get("value_chain") or {}
        ind  = econ.get("industry_structure") or {}

        moat_score = int(moat.get("score") or 0)
        moat_color = "#27ae60" if moat_score >= 9 else "#f39c12" if moat_score >= 7 else "#c0392b"

        return f"""
<h3>🌎 Economic Reality Engine</h3>
<div class="grid-3">
  <div class="card">
    <h4>🏰 Moat Assessment</h4>
    <div class="metric-box">
      <span class="metric-val" style="color:{moat_color}">{moat_score}/10</span>
      <span class="metric-label">Moat Score (Helmer 7 Powers)</span>
    </div>
    <ul style="font-size:0.9em;padding-left:20px;margin-top:10px">
      <li>Switching Costs: <b>{moat.get('switching_costs', 'N/A')}</b></li>
      <li>Network Effects: <b>{moat.get('network_effects', 'N/A')}</b></li>
    </ul>
  </div>
  <div class="card">
    <h4>🔗 Value Chain (Porter)</h4>
    <table class="table-styled">
      <tr><td>Power Ratio</td><td>{vc.get('power_ratio', 'N/A')}</td></tr>
      <tr><td>Upstream Leak</td>
        <td style="color:{'#c0392b' if vc.get('upstream_leak') else '#27ae60'}">
          {'⚠ YES' if vc.get('upstream_leak') else '✓ NO'}
        </td>
      </tr>
      <tr><td>Strategic Leverage</td><td>{vc.get('strategic_leverage', 'N/A')}</td></tr>
    </table>
  </div>
  <div class="card">
    <h4>📊 Industry Structure</h4>
    <table class="table-styled">
      <tr><td>Cyclicality Z-Score</td><td>{self._fmt_num(self._to_float(ind.get('cyclicality_z_score')), 2)}</td></tr>
      <tr><td>At Cyclical Peak</td>
        <td style="color:{'#c0392b' if ind.get('is_peak') else '#27ae60'}">
          {'⚠ YES' if ind.get('is_peak') else '✓ NO'}
        </td>
      </tr>
    </table>
  </div>
</div>""".strip()

    def _render_financial_engine(self, fin: Dict[str, Any]) -> str:
        cf     = (fin.get("clean_financials") or {})
        ratios = fin.get("ratios") or {}

        # FIX: gross_margin_pct is stored as % (e.g. 18.0), not decimal.
        # _fmt_ratio_pct handles this correctly — divides by 100 before formatting.
        return f"""
<h3>💹 Financial Translation (Phase 1 Cleaned)</h3>
<div class="card">
  <div class="grid-3">
    <div>
      <h4>Core P&L (TTM)</h4>
      <table class="table-styled">
        <tr><td>Revenue</td><td>{self._fmt_bn(cf.get('revenue_bn'))}</td></tr>
        <tr><td>EBITDA</td><td>{self._fmt_bn(cf.get('ebitda_bn'))}</td></tr>
        <tr><td>FCF</td><td>{self._fmt_bn(cf.get('fcf_bn'))}</td></tr>
        <tr><td>NOPAT</td><td>{self._fmt_bn(cf.get('nopat_bn'))}</td></tr>
      </table>
    </div>
    <div>
      <h4>Balance Sheet</h4>
      <table class="table-styled">
        <tr><td>Net Debt</td><td>{self._fmt_bn(cf.get('net_debt_bn'))}</td></tr>
        <tr><td>Invested Capital</td><td>{self._fmt_bn(cf.get('invested_capital_bn'))}</td></tr>
        <tr><td>SBC</td><td>{self._fmt_bn(cf.get('sbc_bn'))}</td></tr>
      </table>
    </div>
    <div>
      <h4>Efficiency Ratios</h4>
      <table class="table-styled">
        <tr><td>Gross Margin</td><td>{self._fmt_ratio_pct(ratios.get('gross_margin_pct'))}</td></tr>
        <tr><td>EBIT Margin</td><td>{self._fmt_ratio_pct(ratios.get('ebit_margin_pct'))}</td></tr>
        <tr><td>FCF Margin</td><td>{self._fmt_ratio_pct(ratios.get('fcf_margin_pct'))}</td></tr>
        <tr><td>ROIC</td><td>{self._fmt_pct(self._to_float(ratios.get('roic')))}</td></tr>
        <tr><td>ROE</td><td>{self._fmt_pct(self._to_float(ratios.get('roe')))}</td></tr>
      </table>
    </div>
  </div>
</div>""".strip()

    def _render_risk_engine(self, risk: Dict[str, Any], p2: Dict[str, Any] = None) -> str:
        cap = risk.get("capital_stack") or {}
        rf  = (risk.get("risk_factors") or {}).get("liquidity") or {}
        ds  = (risk.get("risk_factors") or {}).get("downside") or {}

        # WACC display: prefer phase2.wacc (canonical, used by DCF math
        # AND by the dashboard) over capital_stack.wacc (which carries a
        # 9% hard floor that creates a visible mismatch). Fall back to
        # capital_stack.wacc only if phase2.wacc is missing.
        p2_wacc = self._to_float((p2 or {}).get("wacc"))
        cap_wacc = self._to_float(cap.get('wacc'))
        wacc_display = p2_wacc if p2_wacc else cap_wacc

        return f"""
<h3>🛡️ Capital Structure & Risk</h3>
<div class="grid-2">
  <div class="card">
    <h4>💰 Capital Stack</h4>
    <table class="table-styled">
      <tr><td>WACC</td><td>{self._fmt_pct(wacc_display)}</td></tr>
      <tr><td>Cost of Equity</td><td>{self._fmt_pct(self._to_float(cap.get('cost_of_equity')))}</td></tr>
      <tr><td>Beta</td><td>{self._fmt_num(self._to_float(cap.get('beta')), 2)}</td></tr>
    </table>
    <h4 style="margin-top:12px">⚠️ Liquidity</h4>
    <table class="table-styled">
      <tr><td>Liquidity Alert</td>
        <td style="color:{'#c0392b' if rf.get('liquidity_alert') else '#27ae60'}">
          {'⚠ YES' if rf.get('liquidity_alert') else '✓ NO'}
        </td>
      </tr>
      <tr><td>Refinancing Risk</td><td>{rf.get('refinancing_risk_score', 'N/A')}/10</td></tr>
    </table>
  </div>
  <div class="card">
    <h4>📉 Downside Protection</h4>
    <table class="table-styled">
      <tr><td>Tangible Book Value</td><td>{self._fmt_cur(self._to_float(ds.get('tangible_book_value')))}</td></tr>
      <tr><td>Earnings Power Value</td><td>{self._fmt_cur(self._to_float(ds.get('earnings_power_value')))}</td></tr>
      <tr><td>Floor Value</td><td>{self._fmt_cur(self._to_float(ds.get('floor_value')))}</td></tr>
    </table>
  </div>
</div>""".strip()

    # ─────────────────────────────────────────────────────────────────────────
    # Deep-Dive parity renderers — mirror the on-screen Deep Dive sections
    # that the legacy executive HTML lacked. Same data sources, same field
    # names; styling adapted to the static-HTML CSS framework.
    # ─────────────────────────────────────────────────────────────────────────

    _PILLAR_DEFS = [
        ("Moat",             "p1_moat",       "Durability of competitive advantage"),
        ("Health",           "p2_health",     "Balance sheet + cash flow durability"),
        ("Tailwind",         "p3_tailwind",   "Industry/macro structural growth"),
        ("Margin of Safety", "p4_mos",        "Discount of price to intrinsic value"),
        ("Leadership",       "p5_leadership", "Capital allocation + management quality"),
    ]

    def _render_current_state(self, cs: Dict[str, Any]) -> str:
        """§3 Current-state reconciliation — engine assumptions vs current
        signals + flags. The reused market/sector/sentiment/policy signals are
        rendered separately in §2 (cross-source) and §8 (sector & market).
        Empty-safe."""
        if not cs or cs.get("error"):
            return ""
        pct = self._fmt_pct_or_dash
        sev = cs.get("unresolved_severity", cs.get("max_severity", "NONE"))
        pillar = cs.get("pillar_score")
        icon = "⛔" if sev == "HIGH" else "⚠️" if sev == "MEDIUM" else "✓"
        pill = f" · pillar {pillar}/5" if pillar else ""

        parts: List[str] = []
        recs = cs.get("reconciliation") or []
        if recs:
            tr = ""
            for r in recs:
                eng, sig, dl = r.get("engine"), r.get("signal"), r.get("delta")
                tr += (
                    f"<tr><td>{r.get('assumption','')}</td>"
                    f"<td style='text-align:right'>{pct(eng)}</td>"
                    f"<td style='text-align:right'>{pct(sig)} "
                    f"<span style='color:#888'>({r.get('signal_label','')})</span></td>"
                    f"<td style='text-align:right'>{pct(dl)}</td>"
                    f"<td>{r.get('recommendation','')}</td></tr>")
            parts.append(
                "<table class='table-styled'><thead><tr><th>Assumption</th>"
                "<th style='text-align:right'>Engine</th>"
                "<th style='text-align:right'>Signal</th>"
                "<th style='text-align:right'>Δ</th><th>Recommendation</th>"
                f"</tr></thead><tbody>{tr}</tbody></table>")
        flags = cs.get("flags") or []
        if flags:
            li = ""
            for f in flags:
                fsev = f.get("severity")
                dot = "🔴" if fsev == "HIGH" else "🟠" if fsev == "MEDIUM" else "🟡"
                if f.get("acknowledged"):
                    dot = "✅"
                li += f"<li>{dot} <strong>{fsev}</strong> · {f.get('message','')}</li>"
            parts.append(f"<ul style='margin:6px 0 0 0'>{li}</ul>")
        if not parts:
            return ""
        return f"""
  <div class="card" style="page-break-inside:avoid">
  <h3>🧭 Current-State Awareness {icon}{pill}</h3>
  <p style="font-size:12px;color:#666;margin:2px 0 8px 0">
    Reconciles the engine's historical-anchored assumptions against current
    signals. Deterministic / cached — no extra LLM cost.
  </p>
  {"".join(parts)}
  </div>"""

    def _render_cross_source(self, cs: Dict[str, Any],
                             wa: Dict[str, Any]) -> str:
        """§2 Cross-source triangulation — sector/self multiples, implied WACC,
        analyst-sentiment recap. (Explicit PT $ values render in _render_pt_values.)"""
        cs = cs or {}
        pct = self._fmt_pct_or_dash
        bits: List[str] = []
        sv = cs.get("sector_valuation") or {}
        if sv.get("available"):
            own = ""
            if sv.get("own_5y_ev_ebitda") is not None:
                own = (f"; vs own 5Y avg {self._fmt_x_or_dash(sv.get('own_5y_ev_ebitda'))} "
                       f"({pct(sv.get('own_5y_premium_pct'),0)}, {sv.get('own_5y_label','')})")
            peer = ""
            if sv.get("peer_ev_ebitda_median") is not None:
                pset = ", ".join(sv.get("peer_set") or [])
                peer = (f"; vs peer median {self._fmt_x_or_dash(sv.get('peer_ev_ebitda_median'))} "
                        f"({pct(sv.get('peer_premium_pct'),0)}, {sv.get('peer_label','')}"
                        f"{f' — {pset}' if pset else ''})")
            bits.append(
                f"<li><strong>🏭 Multiples:</strong> {sv.get('label','')} — "
                f"EV/EBITDA {self._fmt_x_or_dash(sv.get('market_ev_ebitda'))} vs "
                f"{sv.get('sector') or 'sector'} median "
                f"{self._fmt_x_or_dash(sv.get('sector_median_ev_ebitda'))} "
                f"({pct(sv.get('premium_pct'),0)}){peer}{own}</li>")
        if (wa or {}).get("available") and wa.get("implied_wacc") is not None:
            bps = wa.get("implied_vs_base_bps")
            bits.append(f"<li><strong>📉 Implied WACC:</strong> {pct(wa.get('implied_wacc'))} "
                        f"(market) vs {pct((wa.get('components') or {}).get('wacc_base'))} "
                        f"(model), {bps:+d} bps</li>")
        sent = cs.get("analyst_sentiment") or {}
        if sent.get("available"):
            bits.append(f"<li><strong>🗣️ Analyst sentiment:</strong> {sent.get('label','')} "
                        f"(target {pct(sent.get('implied_upside'),0)} vs price)</li>")
        if not bits:
            return ""
        return (f'<div class="card"><h3>🔀 Cross-source triangulation</h3>'
                f"<ul style='margin:4px 0 0 0'>{''.join(bits)}</ul></div>")

    def _render_market_context(self, mc: Dict[str, Any]) -> str:
        """§8 Market context — earnings-surprise history, sell-side ratings,
        ESG (placeholder), and recent material news. Empty-safe."""
        if not mc:
            return ""
        pct = self._fmt_pct_or_dash
        cur = self._fmt_cur
        cards: List[str] = []

        # Earnings-surprise history.
        es = mc.get("earnings_surprises") or {}
        if es.get("available"):
            rows = ""
            for q in (es.get("quarters") or [])[:6]:
                col = {"beat": "#27ae60", "miss": "#c0392b"}.get(q.get("label"), "#666")
                rows += (f"<tr><td>{q.get('date','')}</td>"
                         f"<td style='text-align:right'>{cur(q.get('eps_actual'),2)}</td>"
                         f"<td style='text-align:right'>{cur(q.get('eps_estimated'),2)}</td>"
                         f"<td style='text-align:right;color:{col}'>{pct(q.get('surprise_pct'))}</td>"
                         f"<td style='color:{col}'>{q.get('label','')}</td></tr>")
            streak = es.get("beat_streak") or 0
            head = (f"{es.get('n_beat')}/{es.get('n_reported')} beats, avg surprise "
                    f"{pct(es.get('avg_surprise_pct'))}"
                    + (f", {streak}-qtr beat streak" if streak >= 2 else ""))
            cards.append(
                f"<h4 style='margin:6px 0 2px 0'>📈 Earnings surprises</h4>"
                f"<p style='font-size:12px;color:#666;margin:0 0 4px 0'>{head} "
                f"<span style='color:#888'>({es.get('source','')})</span></p>"
                f"<table class='table-styled'><thead><tr><th>Quarter</th><th style='text-align:right'>EPS act</th>"
                f"<th style='text-align:right'>EPS est</th><th style='text-align:right'>Surprise</th><th></th></tr></thead>"
                f"<tbody>{rows}</tbody></table>")

        # Sell-side ratings consolidation.
        r = mc.get("ratings") or {}
        if r.get("available"):
            dist = r.get("distribution") or {}
            dist_s = " / ".join(f"{k} {v}" for k, v in dist.items() if v)
            pt = r.get("price_target") or {}
            pt_s = ""
            if pt.get("avg"):
                pt_s = (f" · PT avg {cur(pt.get('avg'))}"
                        + (f" (range {cur(pt.get('low'))}–{cur(pt.get('high'))})"
                           if pt.get("low") and pt.get("high") else "")
                        + (f", {pt.get('n_analysts')} analysts" if pt.get("n_analysts") else ""))
            actions = " · ".join(
                f"{a.get('firm')}: {a.get('grade')}"
                + (f" ({a.get('action')})" if a.get("action") and a.get("action") != "maintain" else "")
                for a in (r.get("recent_actions") or [])[:6])
            cards.append(
                f"<h4 style='margin:10px 0 2px 0'>🏦 Sell-side ratings</h4>"
                f"<p style='margin:2px 0'><strong>Consensus: {r.get('consensus','—')}</strong> "
                f"({dist_s}){pt_s}; {r.get('recent_upgrades_30',0)}↑ / "
                f"{r.get('recent_downgrades_30',0)}↓ last 30 actions</p>"
                f"<p style='font-size:11px;color:#888;margin:2px 0'>Recent: {actions}</p>"
                f"<p style='font-size:11px;color:#b0b0b0;margin:2px 0'>"
                f"Independent-research ratings (CFRA/Morningstar/Argus/Market Edge) "
                f"require a licensed feed not currently connected.</p>")

        # ESG (placeholder until a feed is connected).
        esg = mc.get("esg") or {}
        if esg.get("available"):
            cards.append(
                f"<h4 style='margin:10px 0 2px 0'>🌱 ESG</h4>"
                f"<p style='margin:2px 0'>MSCI {esg.get('msci_rating','—')} · "
                f"Sustainalytics risk {esg.get('sustainalytics_risk','—')}</p>")
        elif esg:
            cards.append(
                f"<h4 style='margin:10px 0 2px 0'>🌱 ESG</h4>"
                f"<p style='font-size:11px;color:#b0b0b0;margin:2px 0'>{esg.get('note','')}</p>")

        # Recent material news.
        news = mc.get("news") or {}
        if news.get("available"):
            items = ""
            for it in (news.get("items") or [])[:5]:
                title = it.get("title", "")
                if it.get("url"):
                    title = f"<a href='{it['url']}'>{title}</a>"
                items += (f"<li><span style='color:#888'>{it.get('date','')}</span> "
                          f"{title} <span style='color:#888'>— {it.get('publisher','')}</span></li>")
            cards.append(
                f"<h4 style='margin:10px 0 2px 0'>📰 Recent material news "
                f"<span style='font-size:11px;color:#888'>(last {news.get('window_days',90)}d)</span></h4>"
                f"<ul style='margin:2px 0 0 0;font-size:13px'>{items}</ul>")

        if not cards:
            return ""
        return f'<div class="card"><h3>🗞️ Market context</h3>{"".join(cards)}</div>'

    def _render_sector_market(self, cs: Dict[str, Any]) -> str:
        """§8 Sector & market context — market signal + policy/regulatory
        (+ a sector recap). Empty-safe."""
        cs = cs or {}
        pct = self._fmt_pct_or_dash
        bits: List[str] = []
        ms = cs.get("market_signal") or {}
        if ms.get("available"):
            extra = []
            if ms.get("return_3m") is not None: extra.append(f"3M {pct(ms['return_3m'],0)}")
            if ms.get("return_6m") is not None: extra.append(f"6M {pct(ms['return_6m'],0)}")
            if ms.get("dist_ma200") is not None: extra.append(f"{pct(ms['dist_ma200'],0)} vs 200DMA")
            bits.append(f"<li><strong>📈 Market signal:</strong> {ms.get('label','')}"
                        + (f" ({', '.join(extra)})" if extra else "") + "</li>")
        pr = cs.get("policy_regulatory") or {}
        if pr.get("available"):
            seg = []
            if pr.get("exposure_label"):
                scn = pr.get("exposure_score")
                seg.append(f"10-K exposure {pr['exposure_label']}"
                           + (f" ({scn:.0f}/7)" if isinstance(scn, (int, float)) else ""))
            n_act = len(pr.get("recent_actions") or [])
            if n_act:
                seg.append(f"{n_act} recent event(s), net {pr.get('net_event_direction','none')}")
            bits.append(f"<li><strong>⚖️ Policy / regulatory:</strong> {' · '.join(seg)}</li>")
        if not bits:
            return ""
        return (f'<div class="card"><h3>🌐 Sector & market context</h3>'
                f"<ul style='margin:4px 0 0 0'>{''.join(bits)}</ul></div>")

    def _render_wacc_analysis(self, wa: Dict[str, Any]) -> str:
        """Discount-rate detail card (memo §7): component build-up, premia +
        adjusted WACC, sensitivity table, implied WACC. Empty-safe."""
        if not wa or not wa.get("available"):
            return ""
        pct = self._fmt_pct_or_dash
        c = wa.get("components") or {}
        pr = wa.get("premia") or {}
        q = wa.get("quality") or {}

        # Component build-up line.
        comp = (
            f'<p style="margin:4px 0"><strong>Build-up:</strong> '
            f'rf {pct(c.get("risk_free_rate"))} + β {self._fmt_num_or_dash(c.get("beta"))}'
            f'×ERP {pct(c.get("erp"))} = Ke {pct(c.get("cost_of_equity"))}'
            f' · Kd≈{pct(c.get("cost_of_debt_implied"))}'
            f' · weights E {pct(c.get("equity_weight"),0)}/D {pct(c.get("debt_weight"),0)}'
            f' → <strong>WACC {pct(c.get("wacc_base"))}</strong></p>')

        # Premia → adjusted WACC.
        prem = (
            f'<p style="margin:4px 0"><strong>Risk build-up premia:</strong> '
            f'size {pct(pr.get("size"))}, country {pct(pr.get("country"))} '
            f'({pr.get("country_used") or "—"}), idiosyncratic {pct(pr.get("idiosyncratic"))}'
            f' → <strong>adjusted WACC {pct(wa.get("adjusted_wacc"))}</strong> '
            f'(IV {self._fmt_num_or_dash(wa.get("iv_at_adjusted_wacc"))} vs base '
            f'{self._fmt_num_or_dash(wa.get("iv_base"))})</p>'
            + (f'<p style="font-size:12px;color:#888;margin:0 0 4px 0">idiosyncratic: '
               f'{", ".join(pr.get("idiosyncratic_reasons") or []) or "none"} · '
               f'premia shown for triangulation; not auto-applied to the headline IV</p>'))

        # Industry-β reference (diagnostic, not a competing IV — headline keeps
        # the market β by design). Numbers, not verdicts; shows the lever + swing.
        sb = wa.get("sector_beta_scenario")
        sect = ""
        if sb:
            d = sb.get("iv_delta_pct") or 0.0
            sect = (
                f'<p style="margin:6px 0"><strong>Industry-β reference</strong> '
                f'<span style="color:#888">(diagnostic, not a competing IV)</span>: '
                f'β {sb["headline_beta"]:.2f} → {sb["benchmark"]} {sb["sector_beta"]:.2f} · '
                f'WACC {pct(sb["headline_wacc"])} → {pct(sb["sector_wacc"])} · '
                f'IV ${sb["headline_iv"]:,.0f} → ${sb["sector_iv"]:,.0f} '
                f'<strong>({d:+.0%})</strong></p>'
                f'<p style="font-size:12px;color:#888;margin:0 0 4px 0">What the WACC '
                f'and IV would be if priced at the industry-average β rather than this '
                f'name\'s market β — the swing sizes the lever; headline unchanged.</p>')

        # Implied WACC.
        iw = wa.get("implied_wacc")
        impl = ""
        if iw is not None:
            bps = wa.get("implied_vs_base_bps")
            impl = (f'<p style="margin:4px 0"><strong>Implied WACC:</strong> '
                    f'{pct(iw)} — the rate the market price implies '
                    f'({bps:+d} bps vs model base). '
                    f'{"Market more cautious than the model." if (bps or 0) > 0 else "Market more optimistic than the model." if (bps or 0) < 0 else ""}</p>')

        # Sensitivity table.
        rows = ""
        for s in wa.get("sensitivity") or []:
            tag = " <strong>(base)</strong>" if s.get("is_base") else ""
            rows += (f"<tr><td style='text-align:right'>{s.get('delta_bps'):+d} bps{tag}</td>"
                     f"<td style='text-align:right'>{pct(s.get('wacc'))}</td>"
                     f"<td style='text-align:right'>{self._fmt_num_or_dash(s.get('iv'))}</td>"
                     f"<td style='text-align:right'>{pct(s.get('vs_price_pct'),0)}</td></tr>")
        table = (f"<table class='table-styled'><thead><tr>"
                 f"<th style='text-align:right'>WACC Δ</th><th style='text-align:right'>WACC</th>"
                 f"<th style='text-align:right'>IV/sh</th><th style='text-align:right'>vs price</th>"
                 f"</tr></thead><tbody>{rows}</tbody></table>")

        qual = (f'<p style="font-size:12px;color:#666;margin:4px 0 0 0">'
                f'Discount-rate quality {q.get("score","—")}/{q.get("max","—")} · '
                f'ERP: {q.get("erp_method","")} · {q.get("notes","")}</p>')

        return f"""
  <div class="card" style="page-break-inside:avoid">
  <h3>📉 Discount-Rate Detail (WACC)</h3>
  <p style="font-size:12px;color:#666;margin:2px 0 6px 0">
    WACC drives ~15-25% of IV per 100 bps. Build-up, risk premia, the IV
    sensitivity to the rate, and the discount rate the market price implies.
  </p>
  {comp}{prem}{sect}{impl}{table}{qual}
  </div>"""

    def _render_cads_flag(self, p2: Dict[str, Any]) -> str:
        """CADS credit floor (Phase 2 / CF-R5) — a deterministic §9 coverage
        signal. Renders for any ticker with CADS computed; loud when the
        coverage trigger fires (negative CADS / sub-1.0× the capex-sinkhole)."""
        cads = (p2 or {}).get("cads") or {}
        if not cads.get("available"):
            return ""
        cur = self._fmt_cur
        latest = cads.get("latest") or {}
        cads_v = self._to_float(latest.get("cads"))
        cov = self._to_float(cads.get("coverage"))
        cov_s = (f"{cov:.2f}×" if cov is not None else "n/a")
        line = (f"CADS (EBITDA − CapEx, FY{latest.get('year','')}): "
                f"{cur(cads_v / 1e6 if cads_v is not None else None, 0)}M · "
                f"debt-service coverage {cov_s}")
        if cads.get("trigger"):
            return (f'<div class="card" style="border-left:4px solid #ef4444">'
                    f'<h3>⚠ CADS coverage — credit floor breached (CF-R5)</h3>'
                    f'<p>{line}</p>'
                    f'<p style="color:#b91c1c">{cads.get("trigger_reason","")}</p>'
                    f'<p style="font-size:11px;color:#999">'
                    f'{"; ".join(cads.get("notes") or [])}</p></div>')
        return (f'<div class="card"><h3>CADS coverage (credit floor)</h3>'
                f'<p>{line} — adequate.</p></div>')

    def _render_downside_protection(self, dp: Dict[str, Any]) -> str:
        """Downside-protection card (memo §8): asymmetry ratio, downside
        ladder, required-MoS-by-risk, position-sizing band. Empty-safe."""
        if not dp or not dp.get("available"):
            return ""
        pct = self._fmt_pct_or_dash
        asym = dp.get("asymmetry_ratio")
        asym_s = f"{asym:.1f}×" if isinstance(asym, (int, float)) else "—"
        verdict = (dp.get("asymmetry_verdict") or "n/a")
        vcolor = {"favorable": "#27ae60", "balanced": "#f39c12",
                  "unfavorable": "#c0392b"}.get(verdict, "#888")

        # Headline metric row.
        probs = dp.get("scenario_probabilities") or {}
        prob_s = (f"bull {probs.get('bull',0)*100:.0f}/base {probs.get('base',0)*100:.0f}/"
                  f"bear {probs.get('bear',0)*100:.0f}" if probs else "")
        head = (
            f'<div style="display:flex;gap:24px;flex-wrap:wrap;margin:6px 0 10px 0">'
            f'<div><div style="font-size:11px;color:#888">Expected return (prob-wtd)</div>'
            f'<div style="font-size:18px;font-weight:600">{pct(dp.get("expected_return_pct"))}</div></div>'
            f'<div><div style="font-size:11px;color:#888">Worst-case stress</div>'
            f'<div style="font-size:18px;font-weight:600;color:#c0392b">{pct(dp.get("worst_case_pct"))}</div></div>'
            f'<div><div style="font-size:11px;color:#888">Asymmetry (E[up]÷E[down])</div>'
            f'<div style="font-size:18px;font-weight:600;color:{vcolor}">{asym_s} · {verdict}</div></div>'
            f'</div>'
            f'<p style="font-size:11px;color:#888;margin:-4px 0 8px 0">'
            f'Probability-weighted EV over DCF bull/base/bear ({prob_s}); the '
            f'multiple-de-rating is a separate market-floor stress (in the ladder), '
            f'not in the ratio.</p>')

        # Downside ladder table.
        rows = ""
        for e in dp.get("downside_scenarios") or []:
            rows += (f"<tr><td>{e.get('name','')}</td>"
                     f"<td style='text-align:right'>${e.get('value',0):,.2f}</td>"
                     f"<td style='text-align:right;color:#c0392b'>{pct(e.get('vs_price_pct'))}</td>"
                     f"<td>{e.get('severity','')}</td>"
                     f"<td style='color:#666'>{e.get('basis','')}</td></tr>")
        ladder = (f"<table class='table-styled'><thead><tr><th>Downside scenario</th>"
                  f"<th style='text-align:right'>Value/sh</th><th style='text-align:right'>vs price</th><th>Severity</th><th>Basis</th>"
                  f"</tr></thead><tbody>{rows}</tbody></table>") if rows else ""

        # Required MoS + sizing.
        req = dp.get("required_mos") or {}
        mosv = (dp.get("mos_verdict") or "n/a").replace("_", " ")
        siz = dp.get("position_sizing") or {}
        footer = (
            f'<p style="margin:8px 0 2px 0"><strong>Margin of safety:</strong> '
            f'actual {pct(dp.get("actual_mos"))} vs required {pct(req.get("mos_good"))} '
            f'(good) / {pct(req.get("mos_strong"))} (strong) for a '
            f'<em>{req.get("stage","")}</em> business → <strong>{mosv}</strong></p>'
            f'<p style="margin:2px 0"><strong>Suggested size:</strong> '
            f'{siz.get("label","—")} <span style="color:#666">({siz.get("basis","")})</span></p>')

        # Optional LLM extras.
        fm = dp.get("failure_modes") or []
        fm_html = ""
        if fm:
            items = ""
            for m in fm[:5]:
                if isinstance(m, dict):
                    watch = (f" <span style='color:#666'>— watch: {m['monitoring_metric']}</span>"
                             if m.get("monitoring_metric") else "")
                    impact = (f"<br><span style='color:#888;font-size:12px'>{m['impact']}</span>"
                              if m.get("impact") else "")
                    items += f"<li><strong>{m.get('name','')}</strong>{watch}{impact}</li>"
                else:
                    items += f"<li>{m}</li>"
            fm_html = (f"<p style='margin:8px 0 2px 0'><strong>Failure modes "
                       f"(permanent-impairment events to monitor):</strong></p><ul>{items}</ul>")
        pm = dp.get("premortem")
        pm_html = (f'<p style="margin:6px 0;padding:8px 10px;background:#fcf3f3;'
                   f'border-radius:6px"><strong>🔮 Pre-mortem:</strong> {pm}</p>') if pm else ""

        return f"""
  <div class="card" style="page-break-inside:avoid">
  <h3>🛟 Downside Protection</h3>
  <p style="font-size:12px;color:#666;margin:2px 0 6px 0">
    How much can be lost vs made, what the downside looks like, whether the
    margin of safety is adequate for the risk, and the implied position size.
    Deterministic from the DCF scenarios + multiples + lifecycle thresholds.
  </p>
  {head}{ladder}{footer}{fm_html}{pm_html}
  </div>"""

    def _render_cyclicality_banner(self, industry: Dict[str, Any]) -> str:
        """Top-of-page banner when a ticker is at a cyclical peak.
        Z-score >2 means current cycle position is two standard
        deviations above long-run mean."""
        if not industry:
            return ""
        z = self._to_float(industry.get("cyclicality_z_score"))
        is_peak = bool(industry.get("is_peak"))
        if z is None and not is_peak:
            return ""
        if not is_peak and (z is None or abs(z) < 2):
            return ""
        z_str = f"{z:+.2f}σ" if z is not None else "—"
        return (
            f'<div style="background:#fef9e7;border-left:4px solid #f39c12;'
            f'padding:12px 16px;border-radius:0 6px 6px 0;margin:12px 0;'
            f'font-size:13px;">'
            f'<strong style="color:#b9770e">⚠ Cyclical-peak signal:</strong> '
            f'cyclicality z-score {z_str} '
            f'{"(at/near peak)" if is_peak else "(historically elevated)"}'
            f'</div>'
        )

    def _render_pillar_scorecard(self, ps: Dict[str, Any]) -> str:
        """5-pillar horizontal-bar scorecard. Mirrors deep_dive_view._pillar_section."""
        if not ps:
            return ""
        capped = ps.get("capped_total", "—")
        tier   = (ps.get("position_tier") or "—").upper()
        position_size = self._to_float(ps.get("position_size_pct"))
        position_size_str = f"{position_size*100:.1f}%" if position_size else "—"

        rows = []
        for name, key, hint in self._PILLAR_DEFS:
            raw = ps.get(key)
            if isinstance(raw, dict):
                score = raw.get("score") or 0
            else:
                score = float(raw or 0)
            score_norm = max(0, min(int(score), 5))
            color = "#27ae60" if score >= 4 else "#f39c12" if score >= 2 else "#c0392b"
            # 5 segments — colored up to score, neutral track for the rest
            segs = "".join(
                f'<span style="display:inline-block;width:34px;height:13px;'
                f'background:{color if i <= score_norm else "#e4e4e7"};'
                f'margin-right:3px;border-radius:3px"></span>'
                for i in range(1, 6)
            )
            # Reasons: pulled from p<n>_reasons sibling key
            reasons_key = key.replace("_moat","").replace("_health","").replace(
                "_tailwind","").replace("_mos","").replace("_leadership","")
            reasons_list = ps.get(f"{reasons_key}_reasons")
            reason_html = ""
            if isinstance(reasons_list, list) and reasons_list:
                items = "".join(f"<li>{r}</li>" for r in reasons_list[:3])
                reason_html = (
                    f'<ul style="margin:4px 0 0 174px;padding-left:18px;'
                    f'color:#71717a;font-size:11.5px;line-height:1.5;">{items}</ul>'
                )
            rows.append(
                f'<div style="display:flex;align-items:center;gap:14px;'
                f'padding:8px 0;border-bottom:1px solid #f4f4f5;">'
                f'<div style="flex-basis:150px;font-size:13px;font-weight:600;">{name}</div>'
                f'<div style="flex-basis:200px">{segs}</div>'
                f'<div style="flex-basis:48px;text-align:right;color:{color};'
                f'font-family:monospace;font-weight:700;font-size:13px">{int(score)}/5</div>'
                f'<div style="flex:1;color:#71717a;font-size:12px">{hint}</div>'
                f'</div>{reason_html}'
            )

        return f"""
<div class="card" style="page-break-inside:avoid">
  <h3>🏛️ Pillar Scorecard
    <span style="font-family:monospace;font-size:13px;color:#71717a;font-weight:400;
                 margin-left:10px;">total {capped}/25 · tier {tier} · size {position_size_str}</span>
  </h3>
  {"".join(rows)}
</div>""".strip()

    def _render_business_snapshot(self, bm: Dict[str, Any]) -> str:
        """Business model: description + KPI strip + revenue segments + key customers
        + competitive landscape + cost structure + regulatory risk. Mirrors
        deep_dive_view._business_snapshot."""
        if not bm:
            return ""
        desc = bm.get("business_description") or ""
        op_lev = self._to_float(bm.get("operating_leverage_score"))
        segs = bm.get("revenue_segments") or []
        custs = bm.get("key_customers") or []

        desc_html = (
            f'<div style="background:#fafafa;padding:14px 16px;border-radius:6px;'
            f'font-size:14px;line-height:1.6;margin-bottom:14px;">{desc}</div>'
            if desc else ""
        )

        kpi_strip = (
            f'<div style="display:grid;grid-template-columns:repeat(3,1fr);'
            f'gap:14px;margin-bottom:14px;">'
            f'<div class="metric-box"><span class="metric-val">'
            f'{f"{op_lev:.1f}/10" if op_lev is not None else "—"}</span>'
            f'<span class="metric-label">Operating leverage</span></div>'
            f'<div class="metric-box"><span class="metric-val">{len(segs) or "—"}</span>'
            f'<span class="metric-label">Revenue segments</span></div>'
            f'<div class="metric-box"><span class="metric-val">{len(custs) or "—"}</span>'
            f'<span class="metric-label">Disclosed key customers</span></div>'
            f'</div>'
        )

        # Revenue segments table
        seg_html = ""
        if segs:
            seg_rows = []
            for s in segs:
                pct = self._to_float(s.get("pct_revenue"))
                pct_str = f"{pct:.1f}%" if pct is not None else "—"
                bar_width = min(max(pct or 0, 0), 100)
                seg_rows.append(
                    f'<tr><td>{s.get("segment", "—")}</td>'
                    f'<td style="width:280px"><div style="background:#e4e4e7;'
                    f'border-radius:3px;height:14px;width:200px;display:inline-block;'
                    f'vertical-align:middle;margin-right:6px;"><div style="background:#27ae60;'
                    f'height:14px;width:{bar_width*2}px;border-radius:3px;"></div></div>'
                    f'<span style="font-family:monospace;font-size:12px;">{pct_str}</span></td>'
                    f'<td style="text-transform:uppercase;font-family:monospace;'
                    f'font-size:11px;color:#71717a;">'
                    f'{s.get("growth_trend") or "—"}</td></tr>'
                )
            seg_html = (
                f'<h4>Revenue segments</h4>'
                f'<table class="table-styled">'
                f'<thead><tr><th>Segment</th><th>% of revenue</th><th>Trend</th></tr></thead>'
                f'<tbody>{"".join(seg_rows)}</tbody></table>'
            )

        # Customers + narrative fields
        cust_html = ""
        if custs:
            cust_html = (
                f'<h4 style="margin-top:14px">Key customers</h4>'
                f'<ul style="font-size:13px;line-height:1.6;">'
                + "".join(f"<li>{c}</li>" for c in custs) + "</ul>"
            )
        narrative_html = ""
        for label, key in [
            ("Competitive landscape", "competitive_landscape"),
            ("Cost structure",        "cost_structure"),
            ("Regulatory risk",       "regulatory_risk"),
        ]:
            v = bm.get(key)
            if v:
                narrative_html += (
                    f'<h4 style="margin-top:14px">{label}</h4>'
                    f'<div style="font-size:13px;line-height:1.6;color:#3f3f46;">{v}</div>'
                )

        return f"""
<div class="card" style="page-break-inside:avoid">
  <h3>🏢 Business Snapshot</h3>
  {desc_html}
  {kpi_strip}
  {seg_html}
  {cust_html}
  {narrative_html}
</div>""".strip()

    # Specialized two-stage engines (REIT → AFFO, DDM → dividends) carry their
    # own per-share cash-flow decomposition instead of the FCFF scenario triangle.
    _SPECIALIZED_VAL = {
        "reit": ("Two-stage AFFO valuation", "AFFO/sh",
                 "REIT analog of a DCF: the equity is valued as a two-stage "
                 "AFFO-per-share growth stream (AFFO is the REIT's distributable "
                 "cash). Shown, not FCFF — GAAP earnings understate a REIT."),
        "ddm":  ("Two-stage dividend discount", "DPS",
                 "Equity valued as a two-stage dividend-per-share growth stream."),
    }

    def _render_saas_metrics(self, p2: Dict[str, Any]) -> str:
        """SaaS unit economics & forward signals (plan Build D). Renders only
        for SaaS names (gated upstream — `saas_metrics` is only attached when
        is_saas_company); empty otherwise."""
        sm = (p2 or {}).get("saas_metrics") or {}
        if not sm.get("available"):
            return ""
        cur = self._fmt_cur
        pct = self._fmt_pct
        num = self._fmt_num
        parts: List[str] = ["<h3>SaaS unit economics & forward signals</h3>"]

        oe = sm.get("owners_earnings") or {}
        if oe.get("owners_earnings_fcf") is not None:
            row = (f"<strong>Owner's-earnings FCF</strong>: "
                   f"{cur(self._to_float(oe.get('owners_earnings_fcf')) / 1e9 if oe.get('owners_earnings_fcf') else None, 2)}B "
                   f"= FCF {cur(self._to_float(oe.get('fcf')) / 1e9 if oe.get('fcf') else None, 2)}B "
                   f"− SBC {cur(self._to_float(oe.get('sbc')) / 1e9 if oe.get('sbc') else None, 2)}B")
            if oe.get("fcf_yield") is not None:
                row += (f" · yield {pct(oe.get('fcf_yield'))} → "
                        f"<strong>{pct(oe.get('owners_earnings_yield'))}</strong> net of dilution")
            parts.append(f"<p>{row}<br><span style='font-size:11px;color:#888'>"
                         f"{oe.get('label','')}</span></p>")

        mn = sm.get("magic_number") or {}
        if mn.get("value") is not None:
            parts.append(f"<p><strong>Magic number</strong> (Δrev/S&M): "
                         f"{num(self._to_float(mn.get('value')), 2)} "
                         f"<span style='font-size:11px;color:#888'>"
                         f"(&lt;0.75 = inefficient sales motion)</span></p>")
        elif mn.get("reason"):
            parts.append(f"<p><strong>Magic number</strong>: N/A — "
                         f"<span style='font-size:11px;color:#888'>{mn['reason']}</span></p>")

        cp = sm.get("cac_payback") or {}
        if cp.get("months") is not None:
            parts.append(f"<p><strong>CAC payback</strong> (proxy): "
                         f"{num(self._to_float(cp.get('months')), 0)} months</p>")

        gm = sm.get("gross_margin_trend") or {}
        if gm.get("latest") is not None:
            parts.append(f"<p><strong>Gross margin</strong>: "
                         f"{num(self._to_float(gm.get('latest')), 1)}% "
                         f"({'+' if (gm.get('delta_5y_pp') or 0) >= 0 else ''}"
                         f"{num(self._to_float(gm.get('delta_5y_pp')), 1)}pp 5Y)</p>")

        dd = sm.get("drawdown") or {}
        if dd.get("max_drawdown") is not None:
            parts.append(f"<p><strong>Max drawdown</strong>: {pct(dd.get('max_drawdown'))} · "
                         f"corr vs {dd.get('benchmark','SPY')} "
                         f"{num(self._to_float(dd.get('corr_benchmark')), 2)}</p>")

        bl = sm.get("billings")
        if isinstance(bl, dict) and bl.get("billings") is not None:
            parts.append(f"<p><strong>Billings</strong>: "
                         f"{cur(self._to_float(bl.get('billings')) / 1e9, 2)}B</p>")
        elif isinstance(bl, dict) and bl.get("reason"):
            parts.append(f"<p><strong>Billings</strong>: "
                         f"<span style='font-size:11px;color:#888'>{bl['reason']}</span></p>")

        for fl in sm.get("flags") or []:
            if fl.get("kind") == "cyclicality_reconciliation":
                parts.append(f"<p style='color:#10b981;font-size:12px'>"
                             f"✓ {fl.get('message','')}</p>")

        gaps = sm.get("data_gaps") or []
        if gaps:
            parts.append("<p style='font-size:11px;color:#999;font-style:italic'>"
                         "Disclosure gaps (NRR/RPO/billings need MD&A extraction): "
                         + "; ".join(gaps) + "</p>")
        return "".join(parts)

    def _render_valuation_methods(self, p2: Dict[str, Any]) -> str:
        """Capital-structure stability flag → discount-rate family + the four-
        method convergence (FCF/WACC, CCF, APV, ECF). Empty when unavailable."""
        out = []
        csf = (p2 or {}).get("capital_structure_flag") or {}
        if csf.get("available"):
            nd = csf.get("net_debt_to_ebitda") or {}
            out.append(
                f"<h3>Capital-structure stability flag</h3>"
                f"<p><strong>{csf.get('klass')}</strong> → discount-rate "
                f"<strong>Family {csf.get('rate_family')}</strong> "
                f"(net-debt/EBITDA {nd.get('first',0):.2f}→{nd.get('last',0):.2f})</p>"
                f"<p style='font-size:12px;color:#666'>"
                f"{'; '.join(csf.get('notes') or [])}</p>")
        vm = (p2 or {}).get("valuation_methods") or {}
        if vm.get("available"):
            ev = vm.get("ev") or {}
            bn = self._fmt_bn
            sp = vm.get("ev_trio_spread")
            ecf_gap = vm.get("ecf_gap_vs_trio")
            out.append(
                "<h3>Four-method convergence (Liberti)</h3>"
                "<table style='width:100%;border-collapse:collapse'>"
                "<tr><td>FCF / WACC</td><td style='text-align:right'>"
                f"{bn(self._to_float(ev.get('wacc_fcf')) / 1e9 if ev.get('wacc_fcf') else None)}</td></tr>"
                "<tr><td>CCF / r_A</td><td style='text-align:right'>"
                f"{bn(self._to_float(ev.get('ccf')) / 1e9 if ev.get('ccf') else None)}</td></tr>"
                "<tr><td>APV (V_U + PV tax shield)</td><td style='text-align:right'>"
                f"{bn(self._to_float(ev.get('apv')) / 1e9 if ev.get('apv') else None)}</td></tr>"
                "<tr><td>ECF / r_E → EV</td><td style='text-align:right'>"
                f"{bn(self._to_float(ev.get('from_ecf')) / 1e9 if ev.get('from_ecf') else None)}</td></tr>"
                "</table>"
                f"<p style='font-size:12px;color:#666'>EV-trio spread "
                f"{(sp*100):.1f}% ({'converged' if vm.get('converged_trio') else 'wide'}); "
                f"ECF residual {(ecf_gap*100):+.1f}% — {vm.get('ecf_residual_attribution','')}. "
                f"Additive diagnostic, not the headline IV.</p>")
        if not out:
            return ""
        return "".join(out)

    def _render_bank_valuation_methods(self, p2: Dict[str, Any]) -> str:
        """Bank convergent set — residual income / justified P/B / Gordon DDM,
        the financial-sector analog of the four-method FCFF convergence.
        Reconciles vs the headline DDM and flags the low-payout understatement.
        Empty for non-financial filers."""
        bvm = (p2 or {}).get("bank_valuation_methods") or {}
        if not bvm.get("available"):
            return ""
        m = bvm.get("methods") or {}
        ri = m.get("residual_income") or {}
        jpb = m.get("justified_pb") or {}
        gor = m.get("gordon_ddm") or {}
        rec = bvm.get("reconciliation") or {}
        inp = bvm.get("inputs") or {}

        def _usd(v):
            return f"${self._to_float(v):,.0f}" if v is not None else "—"

        ddm_iv = (bvm.get("headline_ddm") or {}).get("iv")
        gordon_cell = (_usd(gor.get("iv")) if gor.get("valid")
                       else "<span style='color:#b45309'>undefined (g≥Ke)</span>")
        jpb_mult = self._to_float(jpb.get("multiple"))
        jpb_lbl = (f"Justified P/B ({jpb_mult:.2f}× book, steady-state)"
                   if jpb_mult is not None else "Justified P/B (steady-state)")

        rows = (
            f"<tr><td>Residual income (two-stage)</td>"
            f"<td style='text-align:right'>{_usd(ri.get('iv'))}</td>"
            f"<td style='text-align:right;color:#666'>"
            f"{self._to_float(ri.get('implied_pb')):.2f}× book</td></tr>"
            f"<tr><td>{jpb_lbl}</td>"
            f"<td style='text-align:right'>{_usd(jpb.get('iv_steady_state'))}</td>"
            f"<td style='text-align:right;color:#666'>floor</td></tr>"
            f"<tr><td>Gordon DDM (normalized)</td>"
            f"<td style='text-align:right'>{gordon_cell}</td>"
            f"<td style='text-align:right;color:#666'>cash only</td></tr>"
            f"<tr><td>Headline DDM (config two-stage)</td>"
            f"<td style='text-align:right'>{_usd(ddm_iv)}</td>"
            f"<td style='text-align:right;color:#666'>routed IV</td></tr>")

        band = rec.get("fair_value_band") or []
        band_str = (f"${band[0]:,.0f}–${band[1]:,.0f}"
                    if len(band) == 2 else "—")

        understated = rec.get("low_payout_understatement")
        warn = ""
        if understated:
            dvr = self._to_float(rec.get("ddm_vs_residual_income_pct"))
            warn = (f"<p style='font-size:12px;color:#b45309'>⚠ The routed DDM sits "
                    f"{abs(dvr):.0%} below residual income — it captures only the "
                    f"{self._to_float(inp.get('payout')):.0%} payout, not the "
                    f"{(self._to_float(inp.get('roe_normalized'))-self._to_float(inp.get('ke'))):.1%} "
                    f"ROE-spread compounding on retained book. Read residual income "
                    f"as the fuller equity value; DDM is the cash-distribution floor.</p>")

        ge = ""
        if bvm.get("convergence", {}).get("near_term_excess_growth"):
            ge = (f"<p style='font-size:12px;color:#666'>Near-term g = ROE·retention "
                  f"= {self._to_float(inp.get('near_term_growth')):.1%} ≥ Ke "
                  f"{self._to_float(inp.get('ke')):.1%}: single-stage justified P/B / "
                  f"Gordon are undefined; the two-stage residual income is the only "
                  f"well-posed form (super-growth bank).</p>")

        prov = (f"<p style='font-size:11px;color:#999'>Deterministic: BVPS "
                f"${self._to_float(inp.get('bvps0')):,.2f}, ROE "
                f"{self._to_float(inp.get('roe_normalized')):.1%} (norm), payout "
                f"{self._to_float(inp.get('payout')):.0%} [{inp.get('payout_source')}], "
                f"Ke {self._to_float(inp.get('ke')):.1%} [{inp.get('ke_source')}], "
                f"terminal g {self._to_float(inp.get('terminal_growth')):.1%}. "
                f"Additive diagnostic, not the headline IV.</p>")

        return (
            "<h3>Bank valuation — convergent set (residual income)</h3>"
            "<p style='font-size:13px;color:#666'>Banks have no unlevered FCF, so "
            "the four-method engine doesn't apply. Equity is valued off book + ROE "
            "three ways; in steady state (constant ROE, g&lt;Ke) they are identical "
            "— the bank analog of the four-method convergence.</p>"
            "<table style='width:100%;border-collapse:collapse'>"
            "<tr><th style='text-align:left'>Method</th>"
            "<th style='text-align:right'>Intrinsic / share</th>"
            "<th style='text-align:right'>Read</th></tr>"
            f"{rows}</table>"
            f"<p style='margin-top:8px'><strong>Fair-value band {band_str}</strong> "
            "(justified-P/B floor → two-stage residual income).</p>"
            f"{warn}{ge}{prov}")

    def _render_value_source_decomposition(self, p2: Dict[str, Any]) -> str:
        """Value Source Decomposition (spec §3): attribute expected return across
        Operating / Financial / Multiple engines + a Governance modifier, so the
        reader sees how DURABLE the return source is. Empty when unavailable."""
        vsd = (p2 or {}).get("value_source_decomposition") or {}
        if not vsd.get("available"):
            return ""
        pct = self._fmt_pct
        op = self._to_float(vsd.get("operating_share")) or 0.0
        fin = self._to_float(vsd.get("financial_share")) or 0.0
        mult = self._to_float(vsd.get("multiple_share")) or 0.0
        gov = vsd.get("gov_modifier")
        gov_str = {1: "+1 (aligned / disciplined)", 0: "0 (neutral)",
                   -1: "−1 (transfer risk)"}.get(gov, "—")

        # Verdict from the §4 conviction table (durability gate).
        if mult > 0.40:
            verdict, vcolor = "PASS / watch only — return depends on re-rating", "#ef4444"
        elif op >= 0.60 and mult <= 0.25:
            verdict, vcolor = "CONVICTION eligible — durable operating-led return", "#10b981"
        else:
            verdict, vcolor = "MONITOR max — mixed durability", "#f59e0b"
        if gov == -1:
            verdict += " · governance −1 (downgrade one tier)"

        bar = (
            "<div style='display:flex;height:22px;border-radius:4px;overflow:hidden;"
            "font-size:11px;color:#fff;text-align:center;line-height:22px'>"
            f"<div style='width:{op*100:.0f}%;background:#10b981' title='Operating'>{op*100:.0f}%</div>"
            f"<div style='width:{fin*100:.0f}%;background:#3b82f6' title='Financial'>{fin*100:.0f}%</div>"
            f"<div style='width:{mult*100:.0f}%;background:#ef4444' title='Multiple'>{mult*100:.0f}%</div>"
            "</div>")

        cands = vsd.get("mult_contrib_candidates") or {}
        reads = " · ".join(f"{k}: {pct(v)}/yr" for k, v in cands.items()) or "—"
        cur_mult = vsd.get("current_multiple") or "—"
        funding = vsd.get("buyback_funding") or "—"

        # R17 — signed direction so the multiple SHARE isn't misread as a
        # positive return when the contribution is a de-rating.
        direction = vsd.get("mult_direction") or "—"
        gate = self._to_float(vsd.get("mult_contrib_gate"))
        gate_s = f"{direction} {pct(gate)}/yr" if gate is not None else direction
        dir_color = "#ef4444" if direction == "de-rating" else "#666"

        rows = (
            f"<tr><td>Operating (organic growth × leverage)</td>"
            f"<td style='text-align:right'>{pct(op)}</td></tr>"
            f"<tr><td>Financial (dividend + net buyback)</td>"
            f"<td style='text-align:right'>{pct(fin)}</td></tr>"
            f"<tr><td>Multiple — <span style='color:{dir_color}'>{gate_s}</span></td>"
            f"<td style='text-align:right'>{pct(mult)}</td></tr>"
            f"<tr><td>Governance modifier</td>"
            f"<td style='text-align:right'>{gov_str}</td></tr>")

        # R17 — anchor-divergence note when the re-rating anchors disagree.
        div = vsd.get("mult_anchor_divergence") or {}
        div_html = ""
        if div.get("wide"):
            why = ("disagree on SIGN" if div.get("sign_disagreement")
                   else f"span {div.get('spread_pp',0)*100:.0f}pp/yr")
            div_html = (f"<p style='font-size:12px;color:#b45309'>⚠ Multiple "
                        f"anchors {why} — using the conservative "
                        f"({pct(self._to_float(div.get('selected_conservative')))}/yr); "
                        f"read the multiple band as uncertain, not a point.</p>")

        return (
            "<h3>Value Source Decomposition</h3>"
            "<p style='font-size:13px;color:#666'>Of the expected total return, "
            "what share comes from each engine — and how durable is it? "
            "Operating compounds; Multiple mean-reverts.</p>"
            f"{bar}"
            "<table style='width:100%;border-collapse:collapse;margin-top:8px'>"
            f"{rows}</table>"
            f"<p style='margin-top:8px'><strong style='color:{vcolor}'>{verdict}</strong></p>"
            f"{div_html}"
            f"<p style='font-size:12px;color:#666'>Current multiple: {cur_mult} · "
            f"re-rating reads — {reads} · buyback funding: {funding}</p>"
            f"<p style='font-size:11px;color:#999;font-style:italic'>{vsd.get('honest_flag','')}</p>"
        )

    def _render_specialized_valuation(self, p2: Dict[str, Any]) -> str:
        """Two-stage per-share decomposition for specialized engines (REIT AFFO /
        DDM dividends). Renders only for those engines; empty for FCFF."""
        engine = (p2 or {}).get("engine")
        meta = self._SPECIALIZED_VAL.get(engine)
        if not meta:
            return ""
        dec = p2.get("valuation_decomposition") or {}
        yearly = dec.get("yearly") or []
        if not yearly:
            return ""
        title, cf_label, blurb = meta
        cur = self._fmt_cur
        pct = self._fmt_pct
        inp = p2.get("specialized_inputs") or {}
        ke = inp.get("cost_of_equity")
        gx = inp.get("explicit_growth")
        tg = inp.get("terminal_growth")
        cf0 = (inp.get("current_affo_per_share")
               or inp.get("current_dps_annualized"))

        rows = ""
        for y in yearly:
            rows += (f"<tr><td>Y{y.get('year')}</td>"
                     f"<td style='text-align:right'>{cur(self._to_float(y.get('dps')), 2)}</td>"
                     f"<td style='text-align:right'>{cur(self._to_float(y.get('pv')), 2)}</td></tr>")
        iv = self._to_float(dec.get("intrinsic_per_share"))
        tv_share = self._to_float(dec.get("tv_share"))
        affo_ps = self._to_float(cf0)
        implied_mult = (iv / affo_ps) if (iv and affo_ps) else None

        inputs_line = (
            f"{cf_label} {cur(affo_ps, 2)} · grow {pct(gx)} for "
            f"{inp.get('explicit_years','?')}y → terminal {pct(tg)} · "
            f"Ke {pct(ke)}")
        summary = (
            f"<tr><td><strong>PV of explicit ({len(yearly)}y)</strong></td>"
            f"<td></td><td style='text-align:right'><strong>{cur(self._to_float(dec.get('pv_explicit')), 2)}</strong></td></tr>"
            f"<tr><td>Terminal value (Gordon)</td><td></td>"
            f"<td style='text-align:right'>{cur(self._to_float(dec.get('terminal_value')), 2)}</td></tr>"
            f"<tr><td>PV of terminal</td><td></td>"
            f"<td style='text-align:right'>{cur(self._to_float(dec.get('pv_terminal')), 2)}"
            + (f" <span style='color:#888'>({pct(tv_share)} of IV)</span>" if tv_share is not None else "")
            + "</td></tr>"
            f"<tr style='border-top:2px solid #333'><td><strong>Intrinsic value / share</strong></td>"
            f"<td></td><td style='text-align:right'><strong>{cur(iv, 2)}</strong>"
            + (f" <span style='color:#888'>({implied_mult:.1f}× {cf_label})</span>" if implied_mult else "")
            + "</td></tr>")

        return f"""
  <div class="card" style="page-break-inside:avoid">
  <h3>📐 {title}</h3>
  <p style="font-size:12px;color:#666;margin:2px 0 6px 0">{blurb}</p>
  <p style="font-size:12px;color:#444;margin:2px 0 6px 0"><strong>Inputs:</strong> {inputs_line}</p>
  <table class='table-styled'><thead><tr><th>Year</th><th style='text-align:right'>{cf_label}</th><th style='text-align:right'>PV</th></tr></thead>
  <tbody>{rows}{summary}</tbody></table>
  </div>"""

    def _render_rate_base_sotp(self, p2: Dict[str, Any]) -> str:
        """Regulated-utility sum-of-parts (rate-base engine): the FPL regulated
        leg (allowed ROE on the equity-funded rate base) + the non-regulated
        NEER leg (residual earnings × contracted-generation multiple). Empty for
        non-rate-base engines."""
        if (p2 or {}).get("engine") != "rate_base":
            return ""
        dec = p2.get("valuation_decomposition") or {}
        sop = dec.get("sum_of_parts")
        inp = p2.get("specialized_inputs") or {}
        cur = self._fmt_cur
        pct = self._fmt_pct
        bn = self._fmt_bn

        def _bn(v):
            f = self._to_float(v)
            return bn(f / 1e9) if f is not None else "—"

        eqr = self._to_float(inp.get("equity_ratio"))
        inputs_line = (
            f"Rate base {_bn(inp.get('rate_base'))} · equity ratio "
            f"{pct(eqr)} · allowed ROE {pct(inp.get('allowed_roe'))} · "
            f"grow {pct(inp.get('explicit_growth'))} for "
            f"{inp.get('explicit_years','?')}y → terminal "
            f"{pct(inp.get('terminal_growth'))} · Ke {pct(inp.get('cost_of_equity'))}")

        if not sop:
            # Regulated-only utility (no non-regulated segment configured).
            iv = self._to_float(dec.get("equity_value"))
            return f"""
  <div class="card" style="page-break-inside:avoid">
  <h3>📐 Rate-base DCF (regulated utility)</h3>
  <p style="font-size:12px;color:#666;margin:2px 0 6px 0">Allowed ROE earned on
  the equity-funded portion of the rate base; two-stage with a GDP-level terminal.</p>
  <p style="font-size:12px;color:#444">{inputs_line}</p>
  <p><strong>Regulated equity value {_bn(iv)}</strong></p>
  </div>"""

        rps = self._to_float(sop.get("regulated_per_share"))
        sps = self._to_float(sop.get("segment_per_share"))
        iv = rps + sps if (rps is not None and sps is not None) else None
        resid = self._to_float(sop.get("segment_residual_earnings"))
        mult = self._to_float(sop.get("segment_multiple"))
        cni = self._to_float(sop.get("consolidated_net_income"))
        ree = self._to_float(sop.get("regulated_equity_earnings"))

        return f"""
  <div class="card" style="page-break-inside:avoid">
  <h3>📐 Sum-of-parts valuation (regulated + non-regulated)</h3>
  <p style="font-size:12px;color:#666;margin:2px 0 6px 0">A utility holdco's
  competitive arm isn't in the regulated rate base, so the rate-base leg alone
  understates the equity. The non-regulated leg is the residual earnings
  (consolidated NI − regulated allowed equity earnings, which also nets holdco
  drag) at a contracted-generation multiple.</p>
  <p style="font-size:12px;color:#444"><strong>Regulated inputs:</strong> {inputs_line}</p>
  <table class='table-styled'><thead><tr><th>Leg</th>
  <th style='text-align:right'>Equity value</th>
  <th style='text-align:right'>Per share</th></tr></thead><tbody>
  <tr><td>Regulated rate base (FPL)</td>
  <td style='text-align:right'>{_bn(sop.get('regulated_value'))}</td>
  <td style='text-align:right'>{cur(rps, 2)}</td></tr>
  <tr><td>Non-regulated (NEER): residual {_bn(resid)} × {mult:.0f}×</td>
  <td style='text-align:right'>{_bn(sop.get('segment_value'))}</td>
  <td style='text-align:right'>{cur(sps, 2)}</td></tr>
  <tr style='border-top:2px solid #333'><td><strong>Sum-of-parts intrinsic value</strong></td>
  <td style='text-align:right'><strong>{_bn(sop.get('total_equity_value'))}</strong></td>
  <td style='text-align:right'><strong>{cur(iv, 2)}</strong></td></tr>
  </tbody></table>
  <p style="font-size:11px;color:#999">Residual = consolidated NI {_bn(cni)} −
  regulated allowed equity earnings {_bn(ree)}. The multiple is the key analyst
  lever — recalibrate vs renewables peer comps.</p>
  </div>"""

    def _render_mlp_valuation(self, p2: Dict[str, Any]) -> str:
        """Midstream MLP: EV/EBITDA → equity-per-unit bridge + leverage read +
        distribution-discount cross-check. Empty for non-MLP engines."""
        if (p2 or {}).get("engine") != "mlp":
            return ""
        dec = p2.get("valuation_decomposition") or {}
        if not dec:
            return ""
        cur = self._fmt_cur
        ndte = self._to_float(dec.get("net_debt_to_ebitda"))
        mult = self._to_float(dec.get("ev_ebitda_multiple"))

        def _bn(v):
            f = self._to_float(v)
            return self._fmt_bn(f / 1e9) if f is not None else "—"

        dl = dec.get("distribution_leg") or {}
        dist_html = ""
        if dl.get("intrinsic_per_unit") is not None:
            vs = self._to_float(dl.get("vs_headline_pct"))
            yld = self._to_float(dl.get("distribution_yield"))
            base = (f"Distribution cross-check: "
                    f"{cur(self._to_float(dl.get('intrinsic_per_unit')), 2)}/unit "
                    f"(DPU {cur(self._to_float(dl.get('current_dpu')), 2)}, yield "
                    f"{yld*100:.1f}%)" if yld is not None else "")
            if vs is not None and vs > 0.25:
                dist_html = (f"<p style='font-size:12px;color:#b45309'>⚠ {base} — "
                             f"sits {vs*100:.0f}% above the EV/EBITDA headline; the "
                             "income view takes the payout at face value, but for a "
                             "levered MLP the asset-based anchor is the conservative "
                             "read.</p>")
            else:
                dist_html = f"<p style='font-size:12px;color:#666'>{base}</p>"

        return f"""
  <div class="card" style="page-break-inside:avoid">
  <h3>📐 MLP valuation — EV/EBITDA</h3>
  <p style="font-size:12px;color:#666;margin:2px 0 6px 0">Midstream is a fee-based
  toll-road: value the stable EBITDA on a peer multiple, then net the (large) debt
  to get equity per unit. FCFF mis-frames the growth capex and hides the leverage.</p>
  <table class='table-styled'><thead><tr><th>Step</th>
  <th style='text-align:right'>Value</th></tr></thead><tbody>
  <tr><td>EBITDA × {mult:.1f}× → enterprise value</td>
  <td style='text-align:right'>{_bn(dec.get('enterprise_value'))}</td></tr>
  <tr><td>− Net debt ({ndte:.1f}× EBITDA)</td>
  <td style='text-align:right'>({_bn(dec.get('net_debt'))})</td></tr>
  <tr><td>Equity value</td>
  <td style='text-align:right'>{_bn(dec.get('equity_value'))}</td></tr>
  <tr style='border-top:2px solid #333'><td><strong>Intrinsic value / unit</strong></td>
  <td style='text-align:right'><strong>{cur(self._to_float(dec.get('per_unit')), 2)}</strong></td></tr>
  </tbody></table>
  {dist_html}
  <p style="font-size:11px;color:#999">EV/EBITDA multiple is the key analyst lever
  — recalibrate vs midstream peers (EPD/KMI/WMB/OKE).</p>
  </div>"""

    def _render_residual_income(self, p2: Dict[str, Any]) -> str:
        """Residual income on a normalized ROE: book + PV of returns above Ke,
        with the justified-P/B steady-state cross-check. Empty for other engines."""
        if (p2 or {}).get("engine") != "residual_income":
            return ""
        dec = p2.get("valuation_decomposition") or {}
        if not dec:
            return ""
        inp = p2.get("specialized_inputs") or {}
        cur = self._fmt_cur
        pct = self._fmt_pct
        bvps = self._to_float(dec.get("bvps0"))
        pvx = self._to_float(dec.get("pv_explicit_ri"))
        pvt = self._to_float(dec.get("pv_terminal_ri"))
        iv = self._to_float(dec.get("iv_residual_income"))
        implied_pb = self._to_float(dec.get("implied_pb"))
        jpb_iv = self._to_float(dec.get("iv_justified_pb_steady"))
        jpb_mult = self._to_float(dec.get("justified_pb_multiple"))

        cross = ""
        if jpb_iv is not None:
            cross = (f"<tr><td>Justified-P/B cross-check "
                     f"({jpb_mult:.2f}× book, steady-state)</td>"
                     f"<td style='text-align:right'>{cur(jpb_iv, 2)}</td></tr>")

        return f"""
  <div class="card" style="page-break-inside:avoid">
  <h3>📐 Residual income — normalized ROE</h3>
  <p style="font-size:12px;color:#666;margin:2px 0 6px 0">Equity = book + the PV
  of returns earned ABOVE the cost of equity, on a normalized (ex-impairment) ROE.
  The frame for a no-dividend float business: DDM is undefined and FCFF mis-frames
  thin-margin pass-through revenue.</p>
  <table class='table-styled'><thead><tr><th>Component</th>
  <th style='text-align:right'>Per share</th></tr></thead><tbody>
  <tr><td>Book value per share</td>
  <td style='text-align:right'>{cur(bvps, 2)}</td></tr>
  <tr><td>+ PV of excess returns (explicit)</td>
  <td style='text-align:right'>{cur(pvx, 2)}</td></tr>
  <tr><td>+ PV of terminal residual income</td>
  <td style='text-align:right'>{cur(pvt, 2)}</td></tr>
  <tr style='border-top:2px solid #333'><td><strong>Residual-income intrinsic value</strong></td>
  <td style='text-align:right'><strong>{cur(iv, 2)}</strong>"""+ (
      f" <span style='color:#888'>({implied_pb:.2f}× book)</span>" if implied_pb else "") + f"""</td></tr>
  {cross}
  </tbody></table>
  <p style="font-size:11px;color:#999">Normalized ROE {pct(self._to_float(dec.get('roe_normalized')))}
  [{inp.get('roe_source','—')}] · Ke {pct(self._to_float(dec.get('ke')))}{' (override)' if inp.get('ke_override_used') else ''}
  · near-term g {pct(self._to_float(dec.get('near_term_growth')))} → terminal
  {pct(self._to_float(dec.get('terminal_growth')))}. Normalized ROE & Ke are the
  key analyst levers.</p>
  </div>"""

    def _render_scenario_triangle(self, p2: Dict[str, Any]) -> str:
        """Bull/Base/Bear IV horizontal-bar chart. Pure HTML/CSS — no JS dep."""
        dcf3 = p2.get("three_scenario_dcf") or {}
        bear = dcf3.get("bear") or {}
        base = dcf3.get("base") or {}
        bull = dcf3.get("bull") or {}
        rows = [
            ("BEAR", bear, "#ef4444"),
            ("BASE", base, "#f59e0b"),
            ("BULL", bull, "#10b981"),
        ]
        ivs = [(self._to_float(s.get("intrinsic_per_share")) or 0) for _, s, _ in rows]
        if not any(ivs):
            return ""
        max_iv = max(ivs) or 1

        bars = []
        for label, s, color in rows:
            iv = self._to_float(s.get("intrinsic_per_share"))
            mos = self._to_float(s.get("margin_of_safety"))
            width = (iv or 0) / max_iv * 100
            iv_str = self._fmt_cur(iv)
            mos_str = self._fmt_pct(mos) if mos is not None else "—"
            mos_color = "#10b981" if mos and mos > 0 else "#f59e0b" if mos and mos > -0.2 else "#ef4444"
            bars.append(
                f'<div style="display:grid;grid-template-columns:60px 1fr 90px 110px;'
                f'gap:10px;align-items:center;margin:8px 0;">'
                f'<div style="font-family:monospace;font-size:12px;font-weight:700;'
                f'color:{color};">{label}</div>'
                f'<div style="background:#f4f4f5;border-radius:3px;height:18px;">'
                f'<div style="background:{color};height:18px;width:{width:.1f}%;'
                f'border-radius:3px;"></div></div>'
                f'<div style="font-family:monospace;font-size:13px;font-weight:700;'
                f'text-align:right;">{iv_str}</div>'
                f'<div style="font-family:monospace;font-size:12px;color:{mos_color};'
                f'text-align:right;font-weight:600;">{mos_str} MoS</div></div>'
            )

        return f"""
<div class="card" style="page-break-inside:avoid">
  <h3>📊 Three-scenario DCF — intrinsic value per share</h3>
  {"".join(bars)}
  <div style="font-size:11.5px;color:#71717a;margin-top:10px;line-height:1.5">
    Each scenario tilts all assumption levers (revenue growth, terminal margin,
    WACC, capex %, tax rate) in one direction. Engine floor/ceiling bound the
    analytically defensible range.
  </div>
</div>""".strip()

    def _render_moat_detail(self, moat: Dict[str, Any]) -> str:
        """Full moat block — Helmer 7 powers + pricing-power evidence + narrative."""
        if not moat:
            return ""
        score = self._to_float(moat.get("score")) or 0
        color = "#27ae60" if score >= 7 else "#f39c12" if score >= 4 else "#c0392b"
        dims = [
            ("Cost advantage", moat.get("cost_advantage")),
            ("Network effects", moat.get("network_effects")),
            ("Switching costs", moat.get("switching_costs")),
            ("Intangibles", moat.get("intangibles")),
        ]
        dim_cells = "".join(
            f'<div style="text-align:center;">'
            f'<div style="font-family:monospace;font-size:11.5px;color:#71717a;'
            f'text-transform:uppercase;letter-spacing:0.4px;margin-bottom:4px;">{name}</div>'
            f'<div style="font-size:22px;font-weight:700;'
            f'color:{"#27ae60" if present else "#a1a1aa"};">'
            f'{"✓" if present else "—"}</div></div>'
            for name, present in dims
        )

        evidence = moat.get("evidence")
        evidence_html = (
            f'<div style="margin-top:14px;padding:12px;background:#fafafa;'
            f'border-radius:6px;font-size:13px;line-height:1.6;color:#3f3f46;'
            f'font-style:italic;">{evidence}</div>'
            if evidence else ""
        )

        pp_html = ""
        if moat.get("has_pricing_power") or moat.get("pricing_power_evidence"):
            parts = []
            if moat.get("has_pricing_power"):
                parts.append('<span style="color:#27ae60;font-weight:600">✓ Pricing power confirmed</span>')
            if moat.get("pricing_power_evidence"):
                parts.append(f'<div style="margin-top:6px;font-size:13px;line-height:1.6;">{moat["pricing_power_evidence"]}</div>')
            pp_html = (
                f'<h4 style="margin-top:14px">Pricing power evidence</h4>'
                f'{"".join(parts)}'
            )

        return f"""
<div class="card" style="page-break-inside:avoid">
  <h3>🏰 Moat — Helmer 7 Powers</h3>
  <div style="display:grid;grid-template-columns:160px 1fr;gap:24px;align-items:center;">
    <div style="text-align:center;">
      <div style="font-size:48px;font-weight:800;color:{color};line-height:1;">{score:.1f}</div>
      <div style="font-family:monospace;font-size:11px;color:#71717a;margin-top:6px;
                  letter-spacing:0.6px;text-transform:uppercase">/ 10</div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;">
      {dim_cells}
    </div>
  </div>
  {evidence_html}
  {pp_html}
</div>""".strip()

    def _render_value_chain_detail(self, vc: Dict[str, Any]) -> str:
        """Porter Value Chain — power ratio, substitution risk, bottlenecks."""
        if not vc:
            return ""
        rows = []
        if vc.get("strategic_leverage") is not None:
            rows.append(("Strategic leverage", f"{vc['strategic_leverage']}/10"))
        if vc.get("power_ratio") is not None:
            rows.append(("Power ratio", str(vc["power_ratio"])))
        if vc.get("substitution_risk_score") is not None:
            rows.append(("Substitution risk", f"{vc['substitution_risk_score']}/10"))
        if vc.get("upstream_leak") is not None:
            rows.append(("Upstream leak",
                         "⚠ YES" if vc.get("upstream_leak") else "✓ NO"))
        if vc.get("pass_through_capability") is not None:
            rows.append(("Pass-through pricing",
                         "✓ YES" if vc.get("pass_through_capability") else "NO"))

        rows_html = "".join(
            f'<tr><td>{k}</td><td>{v}</td></tr>' for k, v in rows
        )
        table = (
            f'<table class="table-styled">{rows_html}</table>' if rows_html else ""
        )

        narrative = ""
        for label, key in [
            ("Analysis summary",         "analysis_summary"),
            ("Bottleneck analysis",      "bottleneck_analysis"),
            ("Top substitutes",          "top_substitutes"),
            ("Pricing-power assessment", "pricing_power_assessment"),
        ]:
            v = vc.get(key)
            if v:
                narrative += (
                    f'<h4 style="margin-top:14px">{label}</h4>'
                    f'<div style="font-size:13px;line-height:1.6;color:#3f3f46;">{v}</div>'
                )

        return f"""
<div class="card" style="page-break-inside:avoid">
  <h3>🔗 Value Chain (Porter)</h3>
  {table}
  {narrative}
</div>""".strip()

    def _render_strategic_context_detail(self, sc: Dict[str, Any],
                                         business_analysis: Dict[str, Any] = None) -> str:
        """Strategic context — revenue at risk, terminal haircut, narrative."""
        if not sc:
            return ""
        # revenue_at_risk_percent is in percent units (e.g. 5.0 = 5%) — no ×100.
        # Prefer the DETERMINISTIC value (% of revenue in declining segments)
        # over the LLM field, which defaulted to 0.0 for every ticker.
        rar = self._to_float(sc.get("revenue_at_risk_percent"))
        try:
            from aletheia.tools.business_analysis import revenue_at_risk_from_segments
            det = revenue_at_risk_from_segments(business_analysis)
            if det is not None:
                rar = det
        except Exception:
            pass
        rar_str = f"{rar:.1f}%" if rar is not None else "—"
        rows = [
            ("Revenue at risk",   rar_str),
            # Descriptive labels avoid "✓ NO" ambiguity — readers see
            # the glyph + word as one parse, not as "yes/no answered ✓".
            ("Quality of growth",
             "⚠ Concern flagged" if sc.get("quality_of_growth_risk") else "✓ Clean"),
            ("Terminal haircut",
             "⚠ Applied" if sc.get("terminal_haircut") else "✓ Not applied"),
        ]
        rows_html = "".join(f'<tr><td>{k}</td><td>{v}</td></tr>' for k, v in rows)

        narrative = ""
        for label, key in [
            ("Summary",                 "summary"),
            ("Deferred-revenue trend",  "deferred_revenue_trend"),
            ("Intangible / patent risk","intangible_risk_assessment"),
        ]:
            v = sc.get(key)
            if v and len(str(v)) > 20:
                narrative += (
                    f'<h4 style="margin-top:14px">{label}</h4>'
                    f'<div style="font-size:13px;line-height:1.6;color:#3f3f46;">{v}</div>'
                )

        return f"""
<div class="card" style="page-break-inside:avoid">
  <h3>🧭 Strategic Context</h3>
  <table class="table-styled">{rows_html}</table>
  {narrative}
</div>""".strip()

    def _render_reverse_dcf_detail(self, p2: Dict[str, Any]) -> str:
        """Reverse-DCF horizontal-bar chart + reasons. Mirrors deep_dive_view._reverse_dcf_chart."""
        rdcf = p2.get("reverse_dcf") or {}
        if not rdcf:
            return ""
        impl = self._to_float(rdcf.get("implied_cagr_10y"))
        hist = self._to_float(rdcf.get("historical_cagr") or rdcf.get("historical_cagr_5y"))
        if impl is None and hist is None:
            return ""

        # Determine bar widths — scale to whichever is larger by MAGNITUDE, so a
        # negative implied CAGR (market prices a declining business) still draws
        # a proportional bar instead of being clipped to nothing.
        max_v = max(abs(impl or 0), abs(hist or 0)) or 0.01
        impl_w = (abs(impl or 0) / max_v) * 100
        hist_w = (abs(hist or 0) / max_v) * 100
        impl_color = "#c0392b" if (impl or 0) < 0 else "#f59e0b"  # red when negative
        signal = (rdcf.get("signal") or "—").upper()

        def _bar(label: str, w: float, val_str: str, color: str) -> str:
            return (
                f'<div style="display:grid;grid-template-columns:120px 1fr 80px;'
                f'gap:10px;align-items:center;margin:6px 0;">'
                f'<div style="font-family:monospace;font-size:12px;color:#3f3f46;">{label}</div>'
                f'<div style="background:#f4f4f5;border-radius:3px;height:16px;">'
                f'<div style="background:{color};height:16px;width:{max(w,0):.1f}%;'
                f'border-radius:3px;"></div></div>'
                f'<div style="font-family:monospace;font-size:13px;font-weight:700;'
                f'text-align:right;">{val_str}</div></div>'
            )

        bars = _bar("Implied (market)",  impl_w, self._fmt_pct(impl), impl_color) \
             + _bar("Historical (5y)",   hist_w, self._fmt_pct(hist), "#3b82f6")

        ratio_str = ""
        if impl is not None and hist and hist > 0:
            ratio = impl / hist
            ratio_color = "#c0392b" if ratio > 1.5 else "#27ae60"
            ratio_str = (
                f'<div style="font-family:monospace;font-size:12px;color:#71717a;'
                f'margin-top:6px;">Implied/Historical ratio: '
                f'<span style="color:{ratio_color};font-weight:700;">{ratio:.2f}×</span></div>'
            )

        reasons = rdcf.get("reasons") or []
        reasons_html = ""
        if reasons:
            reasons_html = (
                f'<h4 style="margin-top:14px">Why this signal</h4>'
                f'<ul style="font-size:13px;line-height:1.6;">'
                + "".join(f"<li>{r}</li>" for r in reasons) + "</ul>"
            )

        return f"""
<div class="card" style="page-break-inside:avoid">
  <h3>🔄 Reverse DCF — growth priced in</h3>
  <div style="font-family:monospace;font-size:11.5px;color:#71717a;margin-bottom:10px;">
    Signal: <strong style="color:#3f3f46;">{signal}</strong>
  </div>
  {bars}
  {ratio_str}
  {reasons_html}
</div>""".strip()

    # ─────────────────────────────────────────────────────────────────────────
    # 5_financial_metrics renderers (Option B — quant detail in the JSON)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_pct_or_dash(v: Any, dp: int = 1) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v) * 100:.{dp}f}%"
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _fmt_x_or_dash(v: Any, dp: int = 1) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v):,.{dp}f}×"
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _fmt_num_or_dash(v: Any, dp: int = 2) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v):,.{dp}f}"
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _fmt_bn_or_dash(v: Any, dp: int = 1) -> str:
        """v is already in billions."""
        if v is None:
            return "—"
        try:
            return f"${float(v):,.{dp}f}B"
        except (TypeError, ValueError):
            return "—"

    def _render_historical_fundamentals(self, metrics: Dict[str, Any]) -> str:
        """5-year FY pivot — Revenue / EBITDA / OpInc / FCF / NI / EPS /
        margins. Empty string when no historical rows are available."""
        rows = (metrics or {}).get("historical") or []
        if not rows:
            return ""

        years = [str(r["fiscal_year"]) for r in rows]
        header_cells = "".join(f"<th>FY{y}</th>" for y in years)

        def line(label: str, key: str, fmt) -> str:
            cells = "".join(f"<td>{fmt(r.get(key))}</td>" for r in rows)
            return f"<tr><td><b>{label}</b></td>{cells}</tr>"

        body = []
        body.append(line("Revenue",        "revenue_bn",     lambda v: self._fmt_bn_or_dash(v)))
        body.append(line("EBITDA",         "ebitda_bn",      lambda v: self._fmt_bn_or_dash(v)))
        body.append(line("Operating Inc.", "op_income_bn",   lambda v: self._fmt_bn_or_dash(v)))
        body.append(line("FCF",            "fcf_bn",         lambda v: self._fmt_bn_or_dash(v)))
        body.append(line("Net Income",     "net_income_bn",  lambda v: self._fmt_bn_or_dash(v)))
        body.append(line("Diluted EPS",    "eps_diluted",    lambda v: f"${self._fmt_num_or_dash(v, 2)}" if v else "—"))
        body.append(line("Diluted Shares (M)", "shares_diluted_m",
                         lambda v: self._fmt_num_or_dash(v, 0)))
        body.append(line("Gross Margin",   "gross_margin",   lambda v: self._fmt_pct_or_dash(v)))
        body.append(line("EBIT Margin",    "ebit_margin",    lambda v: self._fmt_pct_or_dash(v)))
        body.append(line("EBITDA Margin",  "ebitda_margin",  lambda v: self._fmt_pct_or_dash(v)))
        body.append(line("FCF Margin",     "fcf_margin",     lambda v: self._fmt_pct_or_dash(v)))

        return f"""
<div class="card" style="page-break-inside:avoid">
  <h3>📜 5-Year Historical Fundamentals</h3>
  <table class="table-styled">
    <thead><tr><th>Line item</th>{header_cells}</tr></thead>
    <tbody>{"".join(body)}</tbody>
  </table>
  <div style="font-size:11.5px;color:#71717a;margin-top:8px;line-height:1.5">
    Pulled from cleaned FY rows in DuckDB at report-generation time. Margins
    are computed from the same rows (no separate ratios cache).
  </div>
</div>""".strip()

    def _render_comprehensive_ratios(self, metrics: Dict[str, Any]) -> str:
        """6 ratio sub-tables in a 2-column grid: profitability, liquidity,
        leverage, valuation, quality, growth."""
        ratios = (metrics or {}).get("ratios") or {}
        if not ratios:
            return ""

        def _table(title: str, rows: List[Tuple[str, str]]) -> str:
            body = "".join(
                f"<tr><td>{label}</td><td style='text-align:right'><b>{val}</b></td></tr>"
                for label, val in rows
            )
            return (
                f'<div class="card" style="page-break-inside:avoid">'
                f'<h4>{title}</h4>'
                f'<table class="table-styled">{body}</table>'
                f'</div>'
            )

        prof = ratios.get("profitability") or {}
        liq  = ratios.get("liquidity") or {}
        lev  = ratios.get("leverage") or {}
        val  = ratios.get("valuation") or {}
        qual = ratios.get("quality") or {}
        grw  = ratios.get("growth") or {}

        prof_rows = [
            ("Gross margin",   self._fmt_pct_or_dash(prof.get("gross_margin"))),
            ("EBIT margin",    self._fmt_pct_or_dash(prof.get("ebit_margin"))),
            ("EBITDA margin",  self._fmt_pct_or_dash(prof.get("ebitda_margin"))),
            ("Net margin",     self._fmt_pct_or_dash(prof.get("net_margin"))),
            ("FCF margin",     self._fmt_pct_or_dash(prof.get("fcf_margin"))),
            ("ROIC",           self._fmt_pct_or_dash(prof.get("roic"))),
            ("ROE",            self._fmt_pct_or_dash(prof.get("roe"))),
            ("ROA",            self._fmt_pct_or_dash(prof.get("roa"))),
        ]
        liq_rows = [
            ("Current ratio",  self._fmt_x_or_dash(liq.get("current_ratio"), 2)),
            ("Quick ratio",    self._fmt_x_or_dash(liq.get("quick_ratio"), 2)),
            ("Cash ratio",     self._fmt_x_or_dash(liq.get("cash_ratio"), 2)),
        ]
        lev_rows = [
            ("Debt / Equity",            self._fmt_x_or_dash(lev.get("debt_to_equity"), 2)),
            ("Debt / EBITDA",            self._fmt_x_or_dash(lev.get("debt_to_ebitda"), 2)),
            ("Net Debt / EBITDA",        self._fmt_x_or_dash(lev.get("net_debt_to_ebitda"), 2)),
            ("Interest coverage",        self._fmt_x_or_dash(lev.get("interest_coverage"), 1)),
        ]
        val_rows = [
            ("P/E",            self._fmt_x_or_dash(val.get("pe"), 1)),
            ("P/B",            self._fmt_x_or_dash(val.get("pb"), 1)),
            ("P/S",            self._fmt_x_or_dash(val.get("ps"), 1)),
            ("EV / Sales",     self._fmt_x_or_dash(val.get("ev_sales"), 1)),
            ("EV / EBITDA",    self._fmt_x_or_dash(val.get("ev_ebitda"), 1)),
            ("FCF yield",      self._fmt_pct_or_dash(val.get("fcf_yield"), 2)),
            ("Dividend yield", self._fmt_pct_or_dash(val.get("dividend_yield"), 2)),
        ]
        qual_rows = [
            ("SBC % of FCF",       self._fmt_pct_or_dash(qual.get("sbc_pct_fcf"))),
            ("Share dilution %",   self._fmt_pct_or_dash(qual.get("share_dilution_pct"))),
            ("Payout ratio",       self._fmt_pct_or_dash(qual.get("payout_ratio"))),
            ("Accrual ratio",      self._fmt_pct_or_dash(qual.get("accrual_ratio"), 2)),
        ]
        grw_rows = [
            ("Revenue CAGR 1Y",  self._fmt_pct_or_dash(grw.get("revenue_cagr_1y"))),
            ("Revenue CAGR 3Y",  self._fmt_pct_or_dash(grw.get("revenue_cagr_3y"))),
            ("Revenue CAGR 5Y",  self._fmt_pct_or_dash(grw.get("revenue_cagr_5y"))),
            ("EBITDA CAGR 5Y",   self._fmt_pct_or_dash(grw.get("ebitda_cagr_5y"))),
            ("FCF CAGR 5Y",      self._fmt_pct_or_dash(grw.get("fcf_cagr_5y"))),
            ("EPS growth (1Y)",  self._fmt_pct_or_dash(grw.get("eps_growth_ttm"))),
        ]

        return f"""
<h2>📐 Comprehensive Ratios</h2>
<div class="grid-2">
  {_table("💰 Profitability", prof_rows)}
  {_table("💧 Liquidity",     liq_rows)}
</div>
<div class="grid-2">
  {_table("⚖️ Leverage",       lev_rows)}
  {_table("📈 Valuation",      val_rows)}
</div>
<div class="grid-2">
  {_table("✨ Quality",        qual_rows)}
  {_table("🚀 Growth",         grw_rows)}
</div>""".strip()

    def _render_dcf_assumptions(self, metrics: Dict[str, Any]) -> str:
        """Bull/Base/Bear scenario assumption stacks side-by-side. Empty
        when dcf_assumptions wasn't captured (non-FCFF engines)."""
        asmp = (metrics or {}).get("dcf_assumptions") or {}
        if not asmp:
            return ""

        rows = [
            ("Revenue CAGR Y1-5",   "revenue_cagr_y1_5",   self._fmt_pct_or_dash),
            ("Revenue CAGR Y6-10",  "revenue_cagr_y6_10",  self._fmt_pct_or_dash),
            ("Terminal EBIT margin","ebit_margin_terminal",self._fmt_pct_or_dash),
            ("Terminal growth",     "terminal_growth",     self._fmt_pct_or_dash),
            ("WACC",                "wacc",                self._fmt_pct_or_dash),
            ("Tax rate",            "tax_rate",            self._fmt_pct_or_dash),
            ("CapEx % revenue",     "capex_pct_revenue",   self._fmt_pct_or_dash),
            ("D&A % revenue",       "da_pct_revenue",      self._fmt_pct_or_dash),
            ("NWC % revenue",       "nwc_pct_revenue",     self._fmt_pct_or_dash),
        ]

        bear = asmp.get("bear") or {}
        base = asmp.get("base") or {}
        bull = asmp.get("bull") or {}

        body = []
        for label, key, fmt in rows:
            body.append(
                f'<tr><td>{label}</td>'
                f'<td style="text-align:right;color:#ef4444"><b>{fmt(bear.get(key))}</b></td>'
                f'<td style="text-align:right;color:#f59e0b"><b>{fmt(base.get(key))}</b></td>'
                f'<td style="text-align:right;color:#10b981"><b>{fmt(bull.get(key))}</b></td>'
                f'</tr>'
            )

        return f"""
<div class="card" style="page-break-inside:avoid">
  <h3>🎚️ DCF Scenario Assumptions</h3>
  <table class="table-styled">
    <thead>
      <tr>
        <th>Lever</th>
        <th style="text-align:right;color:#ef4444">BEAR</th>
        <th style="text-align:right;color:#f59e0b">BASE</th>
        <th style="text-align:right;color:#10b981">BULL</th>
      </tr>
    </thead>
    <tbody>{"".join(body)}</tbody>
  </table>
  <div style="font-size:11.5px;color:#71717a;margin-top:8px;line-height:1.5">
    Each scenario tilts all assumption levers in one direction — bull cases
    use higher growth + margins + lower WACC, bear cases the inverse. The
    base case mirrors the FY-anchored DCFEngine defaults.
  </div>
</div>""".strip()

    def _render_wacc_build(self, metrics: Dict[str, Any]) -> str:
        """Cost-of-capital breakdown. Renders the Rf + β × ERP = Ke walk,
        plus the WACC blend (when capital structure is known). Falls back
        to a single-line presentation when only Ke is available."""
        wb = (metrics or {}).get("wacc_build") or {}
        if not wb:
            return ""

        rf  = wb.get("risk_free_rate")
        beta = wb.get("beta")
        erp  = wb.get("equity_risk_premium")
        ke   = wb.get("cost_of_equity")
        wacc = wb.get("wacc")
        kd_at = wb.get("cost_of_debt_aftertax")
        we    = wb.get("weight_equity")
        wd    = wb.get("weight_debt")

        ke_formula = (
            f'Ke = Rf <b>{self._fmt_pct_or_dash(rf, 2)}</b> '
            f'+ β <b>{self._fmt_num_or_dash(beta, 2)}</b> '
            f'× ERP <b>{self._fmt_pct_or_dash(erp, 2)}</b> '
            f'= <span style="color:#27ae60;font-weight:700">'
            f'{self._fmt_pct_or_dash(ke, 2)}</span>'
        )

        wacc_block = (
            f'<div style="margin-top:12px;font-size:13px;line-height:1.7;">'
            f'WACC = Ke × Equity weight + Kd(1-t) × Debt weight'
            f'</div>'
            f'<div style="margin-top:6px;font-family:DM Mono,monospace;'
            f'font-size:13px;color:#3f3f46;">'
            f'WACC = '
            f'<b>{self._fmt_pct_or_dash(ke, 2)}</b> × <b>{self._fmt_pct_or_dash(we, 1)}</b> '
            f'+ <b>{self._fmt_pct_or_dash(kd_at, 2)}</b> × <b>{self._fmt_pct_or_dash(wd, 1)}</b> '
            f'= <span style="color:#27ae60;font-weight:700">'
            f'{self._fmt_pct_or_dash(wacc, 2)}</span>'
            f'</div>'
        ) if (we is not None or wd is not None) else (
            f'<div style="margin-top:12px;font-size:13px;line-height:1.6;color:#71717a;">'
            f'Capital structure decomposition unavailable for this filer. '
            f'Reported WACC: <b style="color:#27ae60">{self._fmt_pct_or_dash(wacc, 2)}</b> '
            f'(typically Ke for net-cash filers; the DCF engine treats firms '
            f'with negative net debt as equity-funded).'
            f'</div>'
        )

        return f"""
<div class="card" style="page-break-inside:avoid">
  <h3>📊 Cost of Capital Build</h3>
  <div style="font-family:DM Mono,monospace;font-size:13.5px;line-height:1.7;">
    {ke_formula}
  </div>
  {wacc_block}
</div>""".strip()

    # ─────────────────────────────────────────────────────────────────────────
    # Contrarian / existing renderers
    # ─────────────────────────────────────────────────────────────────────────

    def _render_contrarian_block(self, ca: Dict[str, Any]) -> str:
        """Contrarian bear case — bias, sentiment, bear case summary, quant challenge."""
        if not ca:
            return ""
        bias = ca.get("bias_detected", "None")
        bear = ca.get("bear_case_summary", "") or ""
        sentiment = ca.get("sentiment_score")

        sentiment_html = ""
        if isinstance(sentiment, (int, float)):
            s_color = "#c0392b" if sentiment < -3 else "#f39c12" if sentiment < 3 else "#27ae60"
            sentiment_html = (
                f' · <span style="color:{s_color};font-weight:600">sentiment {sentiment:+d}</span>'
            )

        quant = ca.get("quant_challenge") or ""
        quant_html = (
            f'<h4 style="margin-top:14px">Quantitative adversarial challenge</h4>'
            f'<div style="font-size:13px;line-height:1.6;color:#3f3f46;">{quant}</div>'
            if quant else ""
        )

        return f"""
<div class="card" style="page-break-inside:avoid;border-left:4px solid #ef4444;">
  <h3>👹 Contrarian Bear Case</h3>
  <div style="background:rgba(239,68,68,0.06);padding:14px 16px;border-radius:6px;
              font-size:13.5px;line-height:1.7;">
    <div style="margin-bottom:8px;">
      <strong style="color:#c0392b">Bias detected:</strong> {bias}{sentiment_html}
    </div>
    <div>{bear}</div>
  </div>
  {quant_html}
</div>""".strip()

    def _render_structured_thesis_md(
        self, ts: Dict[str, Any], p2: Optional[Dict[str, Any]] = None
    ) -> str:
        """Markdown twin of `_render_structured_thesis`. Returns "" when
        thesis_synthesis is absent (legacy reports pre-Phase 1.5).

        ``p2`` is threaded in so each decision-condition row can show a
        live MET/WATCHING status alongside its priority — same evaluator
        as the HTML and dashboard versions for cross-surface consistency."""
        if not ts or not ts.get("thesis_statement"):
            return ""
        p2 = p2 or {}

        statement = ts.get("thesis_statement", "")
        confidence = (ts.get("thesis_confidence") or "—").upper()
        horizon = (ts.get("time_horizon") or "—").replace("_", " ").upper()

        out: List[str] = [
            "## 1b. 🧭 Structured Investment Thesis",
            f"**Confidence**: {confidence}  ·  **Horizon**: {horizon}",
            "",
            f"> {statement}",
            "",
        ]

        for label, key in [
            ("Bull case", "bull_case"),
            ("Base case", "base_case"),
            ("Bear case", "bear_case"),
        ]:
            cc = ts.get(key) or {}
            claim = cc.get("claim", "") or ""
            if not claim:
                continue
            cites = cc.get("cited_signals") or []
            out.append(f"### {label}")
            out.append(claim)
            if cites:
                cite_str = " · ".join(f"`{c}`" for c in cites)
                out.append(f"**Cites**: {cite_str}")
            out.append("")

        dcs = ts.get("decision_conditions") or []
        if dcs:
            out += [
                "### Decision conditions",
                "",
                "| Priority | Status | Trigger | Observable | Action |",
                "| :--- | :--- | :--- | :--- | :--- |",
            ]
            for dc in dcs:
                pri = (dc.get("priority") or "—").upper()
                act = (dc.get("action") or "—").upper()
                trigger = (dc.get("trigger", "") or "—").replace("|", "\\|")
                obs_raw = dc.get("observable", "") or ""
                obs = (obs_raw or "—").replace("|", "\\|")
                is_met = _evaluate_observable(obs_raw, p2)
                if is_met is True:
                    status = "● MET"
                elif is_met is False:
                    status = "● WATCHING"
                else:
                    status = "—"
                out.append(
                    f"| **{pri}** | {status} | {trigger} | `{obs}` | **{act}** |"
                )
            out.append("")

        raj = ts.get("required_analyst_judgment") or []
        if raj:
            out += ["### Required analyst judgment",
                    "_Gaps the framework defers to the analyst_", ""]
            out += [f"- {r}" for r in raj]
            out.append("")

        upd = ts.get("update_conditions") or []
        if upd:
            out += ["### Update conditions",
                    "_What would invalidate this thesis_", ""]
            out += [f"- {u}" for u in upd]
            out.append("")

        # Metadata receipt at the bottom — fingerprint, coverage, citation set
        md = ts.get("_metadata") or {}
        if md:
            cov = md.get("coverage_state", "—")
            n_a = md.get("n_assessed", "?")
            n_t = md.get("n_assessable", "?")
            fp = md.get("dashboard_state_fingerprint", "")[:16]
            out += [
                f"_Generated against dashboard coverage `{cov}` ({n_a}/{n_t} dims), "
                f"fingerprint `{fp}`._",
                "",
            ]

        return "\n".join(out)

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _fmt_checks_md(self, checks: List[Any]) -> str:
        if not checks:
            return "_No constitution checks available._"
        lines = []
        for check in checks:
            s = str(check)
            is_pass = "PASS" in s or "✅" in s
            is_warn = "CAUTION" in s or "⚠" in s
            icon = "✅" if is_pass else "⚠️" if is_warn else "❌"
            # Remove embedded icon characters to avoid duplication
            clean = s.replace("✅", "").replace("❌", "").replace("⚠️", "").strip()
            lines.append(f"- {icon} {clean}")
        return "\n".join(lines)

    def _fmt_ratio(self, rdcf: Dict[str, Any]) -> str:
        """Format implied/historical CAGR ratio."""
        impl = self._to_float(rdcf.get("implied_cagr_10y"))
        hist = self._to_float(rdcf.get("historical_cagr") or rdcf.get("historical_cagr_5y"))
        if impl is not None and hist and hist > 0:
            return f"{impl/hist:.1f}×"
        return "N/A"

    def _fmt_ratio_pct(self, val: Any) -> str:
        """
        Format a percentage value that is stored as percentage points (e.g. 18.0 not 0.18).
        Divides by 100 before applying _fmt_pct.
        This fixes the double-multiplication bug in the original HTML renderer.
        """
        v = self._to_float(val)
        if v is None:
            return "N/A"
        return self._fmt_pct(v / 100.0)

    def _fmt_bn(self, val: Any) -> str:
        """Format a value stored in billions (e.g. 94.8 → '$94.8B')."""
        v = self._to_float(val)
        if v is None:
            return "N/A"
        return f"${v:.1f}B"

    def _get_css(self) -> str:
        return """:root {
    --primary: #2c3e50; --secondary: #34495e; --accent: #e67e22;
    --bg: #ecf0f1; --card-bg: #ffffff; --text: #2c3e50;
    --pass: #27ae60; --fail: #c0392b;
}
body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); -webkit-print-color-adjust: exact; }
.container { max-width: 1100px; margin: 0 auto; background: white; padding: 40px; box-shadow: 0 0 20px rgba(0,0,0,0.1); }
h1, h2, h3 { color: var(--primary); border-bottom: 2px solid var(--bg); padding-bottom: 10px; }
h1 { font-size: 2.5em; margin-bottom: 0.5em; }
h2 { font-size: 1.6em; margin-top: 1.8em; color: var(--secondary); }
h4 { color: var(--secondary); margin-bottom: 8px; }
.grid-2 { display: grid; grid-template-columns: 1.5fr 1fr; gap: 20px; margin: 16px 0; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; }
.card { background: var(--card-bg); padding: 20px; border: 1px solid #ddd; border-radius: 8px; }
.metric-box { text-align: center; padding: 10px; background: #f8f9fa; border-radius: 5px; }
.metric-val { font-size: 1.5em; font-weight: bold; display: block; }
.metric-label { font-size: 0.8em; text-transform: uppercase; color: #7f8c8d; }
.score-circle { width: 80px; height: 80px; border-radius: 50%; background: var(--primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 1.4em; font-weight: bold; margin: 0 auto; }
.table-styled { width: 100%; border-collapse: collapse; margin-top: 8px; }
.table-styled th, .table-styled td { padding: 7px 8px; border-bottom: 1px solid #eee; text-align: left; font-size: 0.9em; }
.table-styled th { background: #f8f9fa; color: var(--secondary); font-weight: 600; }
.footer { margin-top: 50px; font-size: 0.8em; color: #95a5a6; text-align: center; border-top: 1px solid #eee; padding-top: 20px; }
@media print { body { background: white; } .container { box-shadow: none; padding: 0; } }"""

    @staticmethod
    def _safe_date(meta: str) -> str:
        if not meta:
            return datetime.now().strftime("%Y-%m-%d")
        return meta.split("T")[0] if "T" in meta else meta

    @staticmethod
    def _get(d: Dict[str, Any], path: Sequence[str]) -> Any:
        cur: Any = d
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur

    @staticmethod
    def _to_float(v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            f = float(v)
            import math
            return None if math.isnan(f) else f
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fmt_cur(v: Optional[float], decimals: int = 0) -> str:
        if v is None:
            return "N/A"
        return f"${v:,.{decimals}f}"

    @staticmethod
    def _fmt_pct(v: Optional[float]) -> str:
        if v is None:
            return "N/A"
        return f"{v:.1%}"

    @staticmethod
    def _fmt_num(v: Optional[float], decimals: int = 2) -> str:
        if v is None:
            return "N/A"
        return f"{v:,.{decimals}f}"
