import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional
from langchain_core.messages import HumanMessage
from aletheia.utils.tracing import tracer
from aletheia.utils.report_generator import ReportGenerator

# NOTE: LLM imports (ChatGoogleGenerativeAI, ChatPromptTemplate, MODEL_NAME,
# TEMPERATURE, LeadAgentOutput, os) were removed in the week-5 deterministic-
# assembly migration. Lead no longer makes an LLM call; narrative is
# assembled from thesis_synthesizer's structured output via pure Python.
# If you find yourself wanting to re-add them, the architectural decision
# was: lead is the assembly layer, not an analysis layer. New analysis
# belongs in an upstream agent.


def _capture_git_sha() -> str | None:
    """Resolve the current commit SHA at module-import time. Cached for
    the rest of the process so we don't pay the subprocess cost per run.
    Returns None if not in a git checkout (e.g. CI sandbox)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
            cwd=Path(__file__).resolve().parents[2],
        )
        sha = out.stdout.strip()
        return sha if sha and out.returncode == 0 else None
    except Exception:
        return None


_GIT_SHA = _capture_git_sha()


def _ratios_block(_g) -> dict:
    """Build the financial-translation ratios dict, with ROE suppression
    for negative-equity tickers.

    ROE is meaningless when book equity is negative (aggressive-buyback
    names: LOW, HD, AZO, DRI etc. — treasury-stock subtraction drives
    book equity below zero, making NI/equity ratios produce misleading
    negative percentages). Suppress to None and surface the reason via
    `roe_note`. ROIC stays — it uses invested capital, which remains a
    meaningful denominator for these companies.

    `_g` is the column-getter closure passed in from the caller; it
    handles NaN-to-None coercion uniformly with the rest of the block.
    """
    equity = _g("raw_TotalEquity")
    if equity is not None and equity <= 0:
        roe_value = None
        roe_note = "negative book equity from buybacks; use ROIC instead"
    else:
        roe_value = _g("derived_ROE")
        roe_note = None

    return {
        "gross_margin_pct":    _g("derived_GrossMargin_Pct"),
        "ebit_margin_pct":     _g("derived_EBIT_Margin_Pct"),
        "ebitda_margin_pct":   _g("derived_EBITDA_Margin_Pct"),
        "fcf_margin_pct":      _g("derived_FCF_Margin_Pct"),
        "roic":                _g("derived_ROIC"),
        "roe":                 roe_value,
        "roe_note":            roe_note,
        "sbc_pct_fcf":         _g("clean_SBC_PctFCF"),
        "share_dilution_pct":  _g("clean_ShareDilution_Pct"),
        "cash_tax_rate":       _g("clean_CashTaxRate"),
    }


def _implied_growth_label(p2: dict) -> str | None:
    """Return the human-readable string for ``dcf_model.implied_growth``.

    Depends on which engine produced the valuation:
      - FCFF: a real ReverseDCF implied CAGR is available via
        ``p2.implied_cagr`` — formatted as a percentage string when
        present, falling back to ``None`` when ReverseDCF errored.
      - Rate-base: no ReverseDCF runs (the model isn't a free-FCF
        projection). Return a descriptive label naming the model
        used so downstream consumers don't show the old misleading
        ``"Pending Expectations Engine"`` placeholder.
      - Future engines (DDM, embedded-value): same pattern — label
        names the model, no ReverseDCF.
    """
    engine = p2.get("engine") or "fcff"
    if engine == "fcff":
        cagr = p2.get("implied_cagr")
        if cagr is None:
            return None
        return f"{cagr * 100:.1f}% (ReverseDCF implied 10-year revenue CAGR)"
    if engine == "rate_base":
        # Rate-base perpetuity model — implied growth is the rate-plan g
        # (analyst-curated input), not a market-implied CAGR. The
        # ReverseDCF math doesn't apply to regulator-set growth.
        return (
            "Rate-base perpetuity (FPL rate plan); no market-implied "
            "CAGR — ReverseDCF doesn't apply to regulator-set growth"
        )
    return f"Implied growth from {engine} engine"


class ServingReportWriter:
    def __init__(self):
        self.base_path = Path("valuation_data/serving/latest")
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.html_gen = ReportGenerator(str(self.base_path))

    def save_report(self, ticker: str, report: dict,
                    state: Optional[dict] = None):
        path_json = self.base_path / f"{ticker.upper()}_report.json"

        # Gate D — stamp the `_validation` block before writing. Pulls
        # the Gate A receipt from DuckDB (latest record's
        # `fmp_validation_json`) and Gate B receipt from state.
        # Final-assembly checks (numeric fidelity, scenario monotonicity)
        # run inside build_receipt_block. Fail-soft: any error stamps a
        # `_validation: {status: skipped, skip_reason: ...}` and the
        # report still writes.
        try:
            from aletheia.data.fmp_validation import build_receipt_block
            from aletheia.data.database import InvestmentDatabase as _IDB

            ingestion_receipt = None
            try:
                _db = _IDB(verbose=False)
                _df = _db.get_latest(ticker)
                _db.close()
                if not _df.empty:
                    _r = _df[_df["fiscal_year"] == _df["fiscal_year"].max()].iloc[0]
                    raw = _r.get("fmp_validation_json")
                    if raw:
                        try:
                            ingestion_receipt = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            ingestion_receipt = None
            except Exception:
                ingestion_receipt = None

            ft_fy = ((report.get("2_financial_translation") or {})
                     .get("clean_financials") or {}).get("fiscal_year")

            report["_validation"] = build_receipt_block(
                ticker=ticker,
                fiscal_year=ft_fy,
                state=state or {},
                serving_report=report,
                ingestion_receipt=ingestion_receipt,
            )
        except Exception as exc:
            report["_validation"] = {
                "schema_version": 2,
                "ticker":         ticker,
                "stamped_at":     datetime.now().isoformat(),
                "status":         "skipped",
                "skip_reason":    f"validator_error:{type(exc).__name__}",
                "ingestion":      {"status": "skipped", "skip_reason": "stamp_error"},
                "calc":           {"status": "skipped", "skip_reason": "stamp_error"},
                "final_assembly": {"status": "skipped", "skip_reason": "stamp_error"},
            }

        try:
            with open(path_json, "w") as f:
                json.dump(report, f, indent=2)
            tracer.log_step("ServingReportWriter", {"ticker": ticker},
                            {"status": "saved", "path": str(path_json)})
            print(f"✅ Lead: JSON Report saved to {path_json}")
        except Exception as e:
            tracer.log_step("ServingReportWriter", {"ticker": ticker},
                            {"status": "failed_json", "error": str(e)})

        # Dual-write: persist LLM-authored payload to versioned DB.
        # Deterministic blocks (financials, DCF, capital stack) are NOT
        # written here — they're recomputed from company_records_latest
        # at read time. The whitelist in upsert_agent_run rejects any
        # accidental deterministic-field smuggling.
        try:
            from aletheia.data.database import InvestmentDatabase
            synthesis = report.get("4_valuation_synthesis", {}) or {}
            ft_clean  = (report.get("2_financial_translation", {}) or {}).get("clean_financials", {}) or {}
            # Nest thesis_synthesis under investment_thesis so the
            # full-pipeline write matches the partial-rerun write
            # (api_main.py:/ticker/{T}/thesis_synthesis/refresh). The
            # agent_runs schema only allows the 4 whitelisted keys, so
            # the synthesizer's structured output rides inside
            # investment_thesis. Readers (UI, staleness check) primarily
            # use the serving JSON, but agent_runs is the audit trail —
            # keeping both shapes aligned avoids a code-path-specific gap.
            llm_payload = {
                "economic_reality":    report.get("1_economic_reality") or {},
                "contrarian_analysis": synthesis.get("contrarian_analysis") or {},
                "investment_thesis":   {
                    **(synthesis.get("investment_thesis") or {}),
                    "thesis_synthesis": synthesis.get("thesis_synthesis") or {},
                },
                "agent_scenarios":     synthesis.get("agent_scenarios")     or [],
            }
            fy = ft_clean.get("fiscal_year") or 0
            db = InvestmentDatabase(verbose=False)
            version = db.upsert_agent_run(
                ticker=ticker,
                fiscal_year=int(fy) if fy else 0,
                llm_payload=llm_payload,
                git_sha=_GIT_SHA,
            )
            db.close()
            print(f"✅ Lead: agent_runs row written (v{version})")
        except Exception as e:
            # Dual-write is additive — if it fails we still have the JSON.
            # Don't fail the run for a DB write hiccup.
            tracer.log_step("ServingReportWriter", {"ticker": ticker},
                            {"status": "failed_agent_runs_db", "error": str(e)})
            print(f"⚠ Lead: agent_runs DB write failed: {e}")

        try:
            # Executive MD was a strict subset of Detailed MD with no
            # unique consumer; dropped in the report-consolidation pass.
            # HTML stays (inline-rendered in the Reports tab); Detailed
            # MD stays (download surface). Both will gain embedded
            # thesis_synthesis content via report_generator updates.
            html_path = self.html_gen.generate_html(ticker, report)
            print(f"✅ Lead: HTML saved to {html_path}")
            detailed_path = self.html_gen.generate_detailed_markdown(ticker, report)
            print(f"✅ Lead: Detailed markdown saved to {detailed_path}")
        except Exception as e:
            print(f"❌ Lead: Report generation failed: {e}")

        # DCF Excel — same cadence as HTML + Detailed MD so the Reports
        # tab's "⬇ DCF Excel" button never shows stale-on-disk content.
        # Independent try/except so an xlsx-writer failure doesn't drop
        # the HTML/MD outputs above.
        try:
            from aletheia.tools.dcf_excel_exporter import export_ticker
            xlsx_path = export_ticker(ticker)
            if xlsx_path:
                print(f"✅ Lead: DCF Excel saved to {xlsx_path}")
        except Exception as e:
            print(f"❌ Lead: DCF Excel generation failed: {e}")


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
    contrarian = state.get("contrarian_report", {}) or {}
    p2         = state.get("phase2_valuation", {}) or {}
    # Note: `valuation_report` (legacy fundamentalist output) was removed in
    # the agent-consolidation cleanup. Terminal growth and DCF assumptions
    # now read directly from p2 (phase2_valuation, written by calc_node).

    # IPS / MoS source: calc_node stores the DCFEngine output in
    # `phase2_valuation.dcf` (a flat dict from DCFResult.to_dict). That
    # dict has `{scenario}_intrinsic_per_share` and `{scenario}_upside`
    # keys (where upside = (iv/current_price)-1, i.e. MoS). The legacy
    # `intrinsic_per_share` / `margin_of_safety` dicts at the top of
    # phase2_valuation were never populated — reading from them yielded
    # None across the entire universe (regression caught 2026-05-08).
    _p2_dcf = p2.get("dcf", {}) or {}
    p2_intrinsic = {
        "bear": _p2_dcf.get("bear_intrinsic_per_share"),
        "base": _p2_dcf.get("base_intrinsic_per_share"),
        "bull": _p2_dcf.get("bull_intrinsic_per_share"),
    }
    p2_mos = {
        "bear": _p2_dcf.get("bear_upside"),
        "base": _p2_dcf.get("base_upside"),
        "bull": _p2_dcf.get("bull_upside"),
    }

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
                "ratios": _ratios_block(_g),
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
    # Terminal-growth source: phase2.dcf.base_terminal_g (written by calc_node
    # via DCFEngine.to_dict()). Previously read from valuation_report.
    # assumptions_used.terminal_growth_rate, which became dead after fundamentalist
    # was removed from the graph — that path silently returned 0 and the check
    # vacuously PASSed for every ticker. Bug fixed in agent consolidation cleanup.
    compliance_log = []

    # Terminal-growth-vs-moat compliance:
    # Long-run nominal GDP grows ~4-4.5%, real ~2-2.5%. A perpetual
    # terminal growth above 3% means the company's share of GDP grows
    # without bound — only economically defensible for world-class
    # moats (Apple/MSFT/V/GOOGL territory, i.e. moat ≥ 9 on the 0-10
    # scale). Strict inequality: a moat of exactly 8 doesn't clear the
    # bar. Phrasing matches the rule rather than the negation.
    term_g     = (p2.get("dcf", {}) or {}).get("base_terminal_g") or 0
    moat_score = forensic.get("moat_score", 0)
    if term_g > 0.03 and moat_score < 9:
        compliance_log.append(
            f"❌ FAIL: Terminal growth {term_g:.1%} requires moat ≥ 9 "
            f"(long-run-GDP perpetuity); ticker has moat {moat_score:.1f}."
        )
    else:
        compliance_log.append(
            f"✅ PASS: Terminal Cap. (g={term_g:.1%}, moat={moat_score})"
        )

    implied_cagr    = p2.get("implied_cagr")
    historical_cagr = p2.get("historical_cagr")
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

    # ── Contrarian context ────────────────────────────────────────────────────
    contrarian_structured = contrarian.get("structured_analysis", {}) or {}
    contrarian_bear       = contrarian_structured.get("bear_case_summary", "")
    contrarian_bias       = contrarian_structured.get("bias_detected", "")
    contrarian_sentiment  = contrarian_structured.get("sentiment_score")
    contrarian_quant      = contrarian.get("quant_challenge", "")

    # ── Deterministic narrative assembly (replaces lead's LLM call) ──────────
    # Previously, lead made an LLM call producing growth_decay_assessment +
    # contrarian_rebuttal, which were concatenated into `narrative`.
    # thesis_synthesizer now produces a structured thesis_statement +
    # base_case (with cited_signals enforcement) that strictly subsumes
    # what the lead-LLM call produced.
    #
    # The week-5 deterministic-assembly migration drops lead's LLM call
    # entirely and assembles `narrative` from thesis_synthesizer's output.
    # This:
    #   - Removes 1 LLM call per pipeline run (5 → 4 → 3 across the consolidation)
    #   - Eliminates duplication (lead's LLM was producing parallel
    #     bear-case rebuttal that thesis_synthesizer already emits via
    #     bear_case.claim, and parallel growth-decay assessment that
    #     thesis_synthesizer emits via base_case.claim)
    #   - Inherits thesis_synthesizer's structural enforcement
    #     (cited_signals, vocabulary check, AST lock)
    #
    # If thesis_synthesizer didn't run (e.g. legacy entry points that
    # bypass the graph), narrative falls back to a structured summary
    # built from phase2/contrarian state.
    thesis = state.get("thesis_synthesis") or {}
    if thesis and thesis.get("thesis_statement"):
        narrative_parts = [thesis.get("thesis_statement", "")]
        if thesis.get("base_case", {}).get("claim"):
            narrative_parts.append(thesis["base_case"]["claim"])
        if thesis.get("bear_case", {}).get("claim"):
            narrative_parts.append("Bear case: " + thesis["bear_case"]["claim"])
        narrative = "\n\n".join(p for p in narrative_parts if p)
    else:
        # Fallback for legacy paths that bypass thesis_synthesizer.
        # Pure-Python summary from state — no LLM call.
        narrative = (
            f"Phase 2 valuation for {ticker}: {p2.get('summary', 'unavailable')}. "
            f"Contrarian view: {contrarian_bias or 'no bias detected'}. "
            f"Bear case: {(contrarian_bear or 'unavailable')[:300]}"
        )

    # ── Conviction scorer ─────────────────────────────────────────────────────
    # calc_node already computed the deterministic conviction and put it in
    # state["conviction"]. Lead READS from state per the architecture
    # invariant (no LLM-in-the-loop survives). Fallback path only fires when
    # calc_node didn't run (e.g. legacy run_valuation.py).
    conviction_data = state.get("conviction") or {}
    if not conviction_data:
        try:
            from aletheia.tools.conviction_scorer import ConvictionScorer
            _scorer = ConvictionScorer()
            conviction_data = _scorer.score_from_state(ticker, state).to_dict()
        except Exception as _e:
            print(f"⚠ Conviction scorer fallback: {_e}")
            conviction_data = {}

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
                # `intrinsic_value` here historically held an EV (despite the
                # name) emitted by the legacy fundamentalist→ProForma path.
                # That field was a unit-confused leftover (EV in dollars, not
                # per-share intrinsic) and is now superseded by
                # `phase2_valuation.three_scenario_dcf.{bear,base,bull}.
                # intrinsic_per_share`. Kept as None for legacy schema shape;
                # remove entirely when the export pipeline drops the field.
                "intrinsic_value":  None,
                "upside_percent":   None,
                # Implied-growth label depends on which engine ran. FCFF has
                # a real reverse-DCF implied CAGR (from p2.implied_cagr).
                # Rate-base doesn't run reverse DCF — labeled accordingly.
                # The literal string "Pending Expectations Engine" (legacy
                # placeholder) is replaced by an engine-aware label.
                "implied_growth":   _implied_growth_label(p2),
            },
            "phase2_valuation": {
                # Phase A.4: surface which engine produced these numbers
                # (fcff / rate_base / future ddm + embedded_value). UI
                # provenance pill + thesis synthesizer branching read this.
                "engine":          p2.get("engine"),
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
                # Price provenance — snapshot every IPS/MoS on this report was computed against.
                "current_price":    p2.get("dcf", {}).get("current_price"),
                "market_cap":       p2.get("dcf", {}).get("market_cap"),
                "shares_diluted":   p2.get("dcf", {}).get("shares_diluted"),
                "run_date":         p2.get("dcf", {}).get("run_date"),
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
                # MoS source: phase2.three_scenario_dcf.base.margin_of_safety
                # (calc_node-written). Previously fell back to legacy
                # valuation_report.calculated_upside when fundamentalist ran.
                "margin_of_safety":   p2_mos.get("base"),
                "narrative":          narrative,
                "constitution_checks": compliance_log,
                "pillar_scores":      pillar_scores,
            },
            # Structured thesis from thesis_synthesizer (added in week-1.5
            # consolidation). Lead surfaces it into the final report so
            # the Streamlit UI + export pipeline can render it. The legacy
            # `investment_thesis` block above stays for now — week-5 work
            # replaces lead's LLM synthesis with deterministic assembly,
            # at which point the legacy block can fold into thesis_synthesis.
            "thesis_synthesis":     state.get("thesis_synthesis", {}) or {},
            # Phase C — Agent-proposed alternate scenarios with provenance.
            # Each entry already contains: name, scenario_type, proposed_by,
            # rationale, overrides_applied, dcf, intrinsic_per_share_base,
            # upside_pct_base. Lead persists them as-is so contrarian/UI
            # can reference them by name.
            "agent_scenarios":      state.get("scenario_results", []),
        },
    }

    writer = ServingReportWriter()
    writer.save_report(ticker, final_report, state=state)

    output = {
        "final_report": final_report,
        "messages": [HumanMessage(
            content=f"Lead: Report generated for {ticker}. Conviction: {conviction}."
        )]
    }
    tracer.log_step("Lead", state, output)
    return output
