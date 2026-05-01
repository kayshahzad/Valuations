import os
import json
from datetime import datetime
from pathlib import Path
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from aletheia.state import LeadAgentOutput
from config import MODEL_NAME, TEMPERATURE
from aletheia.utils.tracing import tracer
from aletheia.utils.report_generator import ReportGenerator


class ServingReportWriter:
    def __init__(self):
        self.base_path = Path("valuation_data/serving/latest")
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.html_gen = ReportGenerator(str(self.base_path))

    def save_report(self, ticker: str, report: dict):
        path_json = self.base_path / f"{ticker.upper()}_report.json"
        try:
            with open(path_json, "w") as f:
                json.dump(report, f, indent=2)
            tracer.log_step("ServingReportWriter", {"ticker": ticker},
                            {"status": "saved", "path": str(path_json)})
            print(f"✅ Lead: JSON Report saved to {path_json}")
        except Exception as e:
            tracer.log_step("ServingReportWriter", {"ticker": ticker},
                            {"status": "failed_json", "error": str(e)})

        try:
            html_path = self.html_gen.generate_html(ticker, report)
            print(f"✅ Lead: HTML saved to {html_path}")
            md_path = self.html_gen.generate_markdown(ticker, report)
            print(f"✅ Lead: Markdown saved to {md_path}")
            detailed_path = self.html_gen.generate_detailed_markdown(ticker, report)
            print(f"✅ Lead: Detailed markdown saved to {detailed_path}")
        except Exception as e:
            print(f"❌ Lead: Report generation failed: {e}")


def lead_agent(state):
    """
    Lead Agent — Consensus & Consolidation.

    Audit fixes applied (2026-04-28):
      FIX 1: operating_leverage_score stored as float (was string)
      FIX 2: moat.cost_advantage added
      FIX 3: moat.intangibles added
      FIX 4: moat.evidence added
      FIX 5: strategic_context section added (all 6 fields)
      FIX 6: contrarian_analysis section added to 4_valuation_synthesis
      FIX 7: dcf_adjustments audit trail added to 4_valuation_synthesis
      CLEANUP: serving_base removed, upside_percent uses calculated_upside directly
    """
    print("---LEAD AGENT (Consolidation)---")

    ticker = state["ticker"]

    # ── Verify required upstream context ──────────────────────────────────────
    if "forensic_report" not in state or "value_chain_report" not in state:
        raise ValueError("FATAL: Pipeline execution halted. 'forensic_report' or 'value_chain_report' missing from active state. lead.py requires active context, not stale JSON.")

    # ── Gather all agent outputs ──────────────────────────────────────────────
    strat      = state.get("strategist_report", {}) or {}
    forensic   = state.get("forensic_report", {}) or {}
    vc         = state.get("value_chain_report", {}) or {}
    context    = state.get("strategic_context_report", {}) or {}
    val        = state.get("valuation_report", {}) or {}
    contrarian = state.get("contrarian_report", {}) or {}
    p2         = state.get("phase2_valuation", {}) or {}

    p2_intrinsic = p2.get("intrinsic_per_share", {}) or {}
    p2_mos       = p2.get("margin_of_safety", {}) or {}

    # ── Part 1: Economic Reality ──────────────────────────────────────────────
    economic_reality = {
        "business_model": {
            # FIX 1: float, not string
            "operating_leverage_score":    forensic.get("operating_leverage_score", 0),
            "cost_structure":              forensic.get("operating_leverage_analysis", ""),
            "business_description":        forensic.get("business_description", ""),
            "revenue_segments":            forensic.get("revenue_segments", []),
            "key_customers":               forensic.get("key_customers", []),
            "competitive_landscape":       forensic.get("competitive_landscape", ""),
            "regulatory_risk":             forensic.get("regulatory_risk", ""),
        },
        "value_chain": {
            "power_ratio":              vc.get("power_ratio"),
            "upstream_leak":            vc.get("upstream_value_leak"),
            "strategic_leverage":       vc.get("strategic_leverage_score"),
            "bottleneck_analysis":      vc.get("bottleneck_analysis", ""),
            "substitution_risk_score":  vc.get("substitution_risk_score"),
            "top_substitutes":          vc.get("top_substitutes", ""),
            "pricing_power_assessment": vc.get("pricing_power_assessment", ""),
            "pass_through_capability":  vc.get("pass_through_capability"),
            "analysis_summary":         vc.get("analysis_summary", ""),
        },
        "moat": {
            "score":            forensic.get("moat_score"),
            "switching_costs":  forensic.get("moat_attributes", {}).get("switching_costs"),
            "network_effects":  forensic.get("moat_attributes", {}).get("network_effects"),
            # FIX 2 + 3: previously dropped
            "cost_advantage":   forensic.get("moat_attributes", {}).get("cost_advantage"),
            "intangibles":      forensic.get("moat_attributes", {}).get("intangibles"),
            # FIX 4: previously dropped
            "evidence":                 forensic.get("moat_evidence", ""),
            "has_pricing_power":        forensic.get("has_pricing_power"),
            "pricing_power_evidence":   forensic.get("pricing_power_evidence", ""),
        },
        "industry_structure": {
            "cyclicality_z_score": context.get("revenue_z_score"),
            "is_peak":             context.get("is_cyclical_peak"),
        },
        # FIX 5: entire section was missing
        "strategic_context": {
            "deferred_revenue_trend":     context.get("deferred_revenue_trend", ""),
            "quality_of_growth_risk":     context.get("quality_of_growth_risk"),
            "intangible_risk_assessment": context.get("intangible_risk_assessment", ""),
            "revenue_at_risk_percent":    context.get("revenue_at_risk_percent"),
            "terminal_haircut":           context.get("terminal_haircut"),
            "summary":                    context.get("summary", ""),
        },
    }

    # ── Part 2: Financial Translation — DuckDB Gold ───────────────────────────
    try:
        from aletheia.data.database import InvestmentDatabase
        import numpy as np
        _db = InvestmentDatabase(verbose=False)
        _df = _db.get_latest(ticker)
        _db.close()
        if not _df.empty:
            _r = _df[_df["fiscal_year"] == _df["fiscal_year"].max()].iloc[0]
            def _g(col, fb=None):
                v = _r.get(col)
                return float(v) if v is not None and not (
                    isinstance(v, float) and np.isnan(v)) else fb
            financial_translation = {
                "clean_financials": {
                    "revenue_bn":          _g("clean_Revenue", 0) / 1e9,
                    "ebitda_bn":           _g("derived_EBITDA", 0) / 1e9,
                    "fcf_bn":              _g("derived_FCF", 0) / 1e9,
                    "nopat_bn":            _g("clean_NOPAT", 0) / 1e9,
                    "invested_capital_bn": _g("derived_InvestedCapital", 0) / 1e9,
                    "net_debt_bn":         _g("derived_NetDebt", 0) / 1e9,
                    "sbc_bn":              _g("clean_SBC", 0) / 1e9,
                    "fiscal_year":         int(_df["fiscal_year"].max()),
                    "data_quality":        _g("overall_quality_score"),
                },
                "ratios": {
                    "gross_margin_pct":    _g("derived_GrossMargin_Pct"),
                    "ebit_margin_pct":     _g("derived_EBIT_Margin_Pct"),
                    "ebitda_margin_pct":   _g("derived_EBITDA_Margin_Pct"),
                    "fcf_margin_pct":      _g("derived_FCF_Margin_Pct"),
                    "roic":                _g("derived_ROIC"),
                    "roe":                 _g("derived_ROE"),
                    "sbc_pct_fcf":         _g("clean_SBC_PctFCF"),
                    "share_dilution_pct":  _g("clean_ShareDilution_Pct"),
                    "cash_tax_rate":       _g("clean_CashTaxRate"),
                },
                "quality_screens": {
                    "beneish_m_score": _g("beneish_m_score"),
                    "sloan_accrual":   _g("sloan_accrual_ratio"),
                    "domain_scores": {
                        k.replace("domain_score_", ""): _g(k)
                        for k in _df.columns if k.startswith("domain_score_")
                    }
                },
                "cleaning_flags": {
                    "warning_count":     int(_r.get("warning_count", 0) or 0),
                    "error_count":       int(_r.get("error_count", 0) or 0),
                    "pension_deficit_bn": _g("clean_PensionDeficit_ForEquityBridge", 0) / 1e9,
                    "lease_debt_bn":     _g("clean_LeaseDebt_ForEquityBridge", 0) / 1e9,
                    "jva_income_bn":     _g("clean_JVA_Income_Isolated", 0) / 1e9,
                }
            }
        else:
            financial_translation = {"error": f"No DB data for {ticker}"}
    except Exception as _e:
        financial_translation = {"error": str(_e)}

    # ── Part 3: Capital Structure + concentration risk ────────────────────────
    capital_structure = dict(strat) if strat else {}
    capital_structure["concentration_risk"]    = forensic.get("concentration_risk")
    capital_structure["concentration_details"] = forensic.get("concentration_details", "")

    try:
        # Calculate floor price per share
        shares = p2.get("bridge", {}).get("base", {}).get("shares_diluted")
        if not shares:
            shares = p2.get("three_scenario_dcf", {}).get("base", {}).get("shares_diluted")
            
        floor_val = capital_structure.get("risk_factors", {}).get("downside", {}).get("floor_value")
        if shares and floor_val:
            capital_structure["risk_factors"]["downside"]["floor_price_per_share"] = floor_val / shares
    except Exception:
        pass

    # ── Constitution checks ───────────────────────────────────────────────────
    params         = val.get("assumptions_used", {})
    compliance_log = []

    term_g     = params.get("terminal_growth_rate", 0)
    moat_score = forensic.get("moat_score", 0)
    if term_g > 0.03 and moat_score <= 8:
        compliance_log.append(
            f"❌ FAIL: Terminal Cap. g ({term_g:.1%}) > 3% but Moat ({moat_score}) <= 8.")
    else:
        compliance_log.append("✅ PASS: Terminal Cap.")

    implied_cagr    = p2.get("implied_cagr")
    historical_cagr = p2.get("historical_cagr")
    rdcf_signal     = p2.get("reverse_dcf_signal", "unknown")
    if implied_cagr and historical_cagr and historical_cagr > 0:
        ratio = implied_cagr / historical_cagr
        if ratio > 2.0:
            compliance_log.append(
                f"❌ FAIL: Implied CAGR ({implied_cagr:.1%}) is {ratio:.1f}x historical "
                f"({historical_cagr:.1%}). Requires TAM justification.")
        elif ratio > 1.3:
            compliance_log.append(
                f"⚠️ CAUTION: Implied CAGR ({implied_cagr:.1%}) exceeds historical "
                f"({historical_cagr:.1%}) by {ratio:.1f}x.")
        else:
            compliance_log.append(
                f"✅ PASS: Implied CAGR ({implied_cagr:.1%}) within 1.3x of historical.")

    premium_pct = p2.get("ev_ebitda_premium_pct", 0)
    mult_signal = p2.get("multiple_signal", "")
    if premium_pct > 1.0:
        compliance_log.append(
            f"❌ FAIL: EV/EBITDA at {premium_pct:+.0%} premium. Signal: {mult_signal}.")
    elif premium_pct > 0.30:
        compliance_log.append(
            f"⚠️ CAUTION: EV/EBITDA at {premium_pct:+.0%} premium.")
    else:
        compliance_log.append(
            f"✅ PASS: Multiple premium {premium_pct:+.0%} within range.")

    compliance_section = "\n".join(compliance_log)

    # ── Contrarian context ────────────────────────────────────────────────────
    contrarian_structured = contrarian.get("structured_analysis", {}) or {}
    contrarian_bear       = contrarian_structured.get("bear_case_summary", "")
    contrarian_bias       = contrarian_structured.get("bias_detected", "")
    contrarian_sentiment  = contrarian_structured.get("sentiment_score")
    contrarian_quant      = contrarian.get("quant_challenge", "")

    # ── Phase 2 context string ────────────────────────────────────────────────
    p2_context = ""
    if p2_intrinsic and implied_cagr:
        bear_iv   = p2_intrinsic.get("bear", 0)
        base_iv   = p2_intrinsic.get("base", 0)
        bull_iv   = p2_intrinsic.get("bull", 0)
        cur_price = p2.get("dcf", {}).get("current_price", 0)
        p2_context = (
            f"3-scenario DCF: Bear=${bear_iv:,.0f} | Base=${base_iv:,.0f} | "
            f"Bull=${bull_iv:,.0f} vs Price=${cur_price:,.0f}. "
            f"Base MoS={p2_mos.get('base', 0):+.1%}. "
            f"Implied CAGR={implied_cagr:.1%} vs hist={historical_cagr:.1%} [{rdcf_signal}]. "
            f"EV/EBITDA premium={premium_pct:+.0%} [{mult_signal}]. "
            f"ROIC-WACC spread={p2.get('roic_wacc_spread', 0):+.1%} "
            f"[{p2.get('value_creation', '?')}]."
        )
    else:
        p2_context = p2.get("summary", "")

    # ── LLM synthesis ─────────────────────────────────────────────────────────
    dcf = val.get("dcf_result", {})

    if not os.environ.get("GOOGLE_API_KEY"):
        narrative = "Mock Narrative (No API Key)"
    else:
        llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=TEMPERATURE)
        structured_llm = llm.with_structured_output(LeadAgentOutput)

        prompt = ChatPromptTemplate.from_template("""You are the Lead Investment Committee (Aletheia).
Synthesize a final investment thesis for {ticker}.

CONSTITUTION COMPLIANCE:
{compliance_check}

PHASE 2 VALUATION:
{p2_context}

MOAT & BUSINESS QUALITY:
  Moat score:      {moat_score}/10
  Moat evidence:   {moat_evidence}
  Pricing power:   {has_pricing_power}
  Cost structure:  {cost_structure}
  Business:        {business_description}
  Value chain:     {vc_summary}
  Strat context:   {context_summary}

BEAR CASE (Contrarian):
  Bias:    {contrarian_bias}
  Summary: {contrarian_bear}

TASKS:
1. Conviction Score (-10 to +10) and Margin of Safety.
2. Growth decay assessment — is implied CAGR realistic given moat and history?
3. Contrarian rebuttal — where is the bear case wrong, where is it right?

Be specific and mathematical. Return structured JSON.
""")

        chain = prompt | structured_llm

        try:
            res: LeadAgentOutput = chain.invoke({
                "ticker":               ticker,
                "compliance_check":     compliance_section,
                "p2_context":           p2_context,
                "moat_score":           forensic.get("moat_score", 0),
                "moat_evidence":        forensic.get("moat_evidence", ""),
                "has_pricing_power":    forensic.get("has_pricing_power", False),
                "cost_structure":       forensic.get("operating_leverage_analysis", ""),
                "business_description": forensic.get("business_description", ""),
                "vc_summary":           vc.get("analysis_summary", ""),
                "context_summary":      context.get("summary", ""),
                "contrarian_bias":      contrarian_bias,
                "contrarian_bear":      contrarian_bear,
            })
            narrative = f"{res.growth_decay_assessment}\n\n{res.contrarian_rebuttal}"
        except Exception as e:
            narrative = f"LLM Generation Failed: {e}"

    # ── Conviction scorer ─────────────────────────────────────────────────────
    from aletheia.tools.conviction_scorer import ConvictionScorer
    _scorer = ConvictionScorer()
    try:
        conviction_data = _scorer.score_from_state(ticker, state).to_dict()
    except Exception as _e:
        print(f"⚠ Conviction scorer fallback: {_e}")
        from aletheia.tools.conviction_scorer import score_conviction
        conviction_data = score_conviction(state)

    conviction    = conviction_data.get("conviction_score", 0)
    pillar_scores = conviction_data

    # ── Assemble final report ─────────────────────────────────────────────────
    final_report = {
        "ticker":        ticker,
        "generated_at":  datetime.now().isoformat(),
        "1_economic_reality": economic_reality,
        "2_financial_translation": financial_translation,
        "3_capital_structure_risk": capital_structure,
        "4_valuation_synthesis": {
            "dcf_model": {
                "intrinsic_value":  dcf.get("equity_value"),
                # CLEANUP: use fundamentalist calculated_upside directly
                "upside_percent":   val.get("calculated_upside"),
                "implied_growth":   "Pending Expectations Engine",
            },
            "phase2_valuation": {
                "three_scenario_dcf": {
                    "bear": {
                        "intrinsic_per_share": p2_intrinsic.get("bear"),
                        "margin_of_safety":    p2_mos.get("bear"),
                        "ev":                  p2.get("dcf", {}).get("bear_ev"),
                    },
                    "base": {
                        "intrinsic_per_share": p2_intrinsic.get("base"),
                        "margin_of_safety":    p2_mos.get("base"),
                        "ev":                  p2.get("dcf", {}).get("base_ev"),
                    },
                    "bull": {
                        "intrinsic_per_share": p2_intrinsic.get("bull"),
                        "margin_of_safety":    p2_mos.get("bull"),
                        "ev":                  p2.get("dcf", {}).get("bull_ev"),
                    },
                },
                "reverse_dcf": {
                    "implied_cagr_10y": p2.get("implied_cagr"),
                    "historical_cagr":  p2.get("historical_cagr"),
                    "signal":           p2.get("reverse_dcf_signal"),
                    "reasons":          p2.get("reverse_dcf_reasons", []),
                },
                "multiple_decomposition": {
                    "market_ev_ebitda":    p2.get("ev_ebitda_market"),
                    "justified_ev_ebitda": p2.get("ev_ebitda_justified"),
                    "premium_pct":         p2.get("ev_ebitda_premium_pct"),
                    "signal":              p2.get("multiple_signal"),
                    "roic_wacc_spread":    p2.get("roic_wacc_spread"),
                    "value_creation":      p2.get("value_creation"),
                    "roic": p2.get("multiple_decomposition", {}).get("roic"),
                    "wacc": p2.get("dcf", {}).get("wacc_base"),
                },
                # FIX 7: dcf_adjustments audit trail
                "dcf_adjustments":  p2.get("dcf_adjustments", {}),
                "wacc":             p2.get("dcf", {}).get("wacc_base"),
                "beta":             p2.get("dcf", {}).get("beta"),
                "risk_free_rate":   p2.get("dcf", {}).get("risk_free_rate"),
            },
            # FIX 6: contrarian_analysis was completely absent
            "contrarian_analysis": {
                "bias_detected":     contrarian_bias,
                "bear_case_summary": contrarian_bear,
                "sentiment_score":   contrarian_sentiment,
                "quant_challenge":   contrarian_quant,
            },
            "investment_thesis": {
                "conviction_score":   conviction,
                "margin_of_safety":   p2_mos.get("base") or val.get("calculated_upside"),
                "narrative":          narrative,
                "constitution_checks": compliance_log,
                "pillar_scores":      pillar_scores,
            },
        },
    }

    writer = ServingReportWriter()
    writer.save_report(ticker, final_report)

    output = {
        "final_report": final_report,
        "messages": [HumanMessage(
            content=f"Lead: Report generated for {ticker}. Conviction: {conviction}."
        )]
    }
    tracer.log_step("Lead", state, output)
    return output
