"""
aletheia/agents/calc_node.py

Deterministic calculation node. Runs ALL numerical analysis BEFORE any agent
narrative runs, then writes the results to state. Subsequent narrative agents
(forensic, value_chain, context, contrarian, lead) read from this state and
explain it — they never compute new numbers that flow back into a calculation.

Architecture invariants enforced here:
  - calc_node never reads from forensic_report / value_chain_report /
    strategic_context_report (those don't exist yet at this point in the DAG)
  - calc_node never accepts kwargs that mutate calc inputs
  - All inputs come from CalculationInput, which is built deterministically
    from UNIVERSE classification + DuckDB cleaned data
  - Conviction P1 (moat) and P5 (operating leverage) inputs come from
    deterministic financial fingerprints, not from LLM-derived narrative

State written:
  state["phase2_valuation"] = {
      "dcf":         DCFResult.to_dict(),
      "reverse_dcf": ReverseDCFResult.to_dict(),
      "multiples":   MultipleResult.to_dict(),
      "wacc":        float,
      "errors":      [str, ...],
      "summary":     str,
  }
  state["cyclicality"] = {
      "z_score": float,
      "is_peak": bool,
      "applies_cyclical_haircut": bool,
      "avg_3yr": float,
      "db_context": dict,
  }
  state["conviction"] = ConvictionResult.to_dict()
  state["calc_bypassed"] = Optional[str]   # set if DCF is sector-bypassed

Note: `operating_leverage` and `moat_fingerprint` are computed inside this
node and consumed by ConvictionScorer locally, but are NOT written to
state — the JSON-as-truth investigation confirmed no downstream consumer
reads them. Removing them prevents accidental future coupling.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage

from aletheia.utils.tracing import tracer
from aletheia.utils.calc_input_builder import make_calc_input


def calc_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run all deterministic calculations for the current ticker and write the
    results to state. No agent state is read, no LLM is invoked.
    """
    print("---CALC NODE (Deterministic Phase 2)---")

    ticker = state.get("ticker", "UNKNOWN")
    phase2: Dict[str, Any] = {}
    errors: list[str] = []

    # ── Step 0: Build CalculationInput ───────────────────────────────────────
    try:
        calc_input = make_calc_input(ticker)
    except Exception as e:
        errors.append(f"CalculationInput build failed: {e}")
        print(f"  ✗ CalculationInput build failed: {e}")
        return {
            "phase2_valuation": {"errors": errors},
            "calc_bypassed": None,
            "messages": [HumanMessage(
                content=f"CalcNode: {ticker} input build failed: {e}"
            )],
        }

    # ── Step 1: DCF Engine ──────────────────────────────────────────────────
    dcf_result = None
    bypass_reason = None
    try:
        from aletheia.tools.dcf_engine import DCFEngine
        engine = DCFEngine(verbose=False)
        dcf_result = engine.run(calc_input)
        if dcf_result.errors:
            errors.extend(dcf_result.errors)
        phase2["dcf"] = dcf_result.to_dict()
        phase2["wacc"] = dcf_result.wacc
        if dcf_result.bull and dcf_result.base and dcf_result.bear:
            print(f"  ✓ DCF: bull=${dcf_result.bull.enterprise_value/1e9:.0f}B "
                  f"base=${dcf_result.base.enterprise_value/1e9:.0f}B "
                  f"bear=${dcf_result.bear.enterprise_value/1e9:.0f}B")
        else:
            print("  ✓ DCF: completed (partial)")
    except NotImplementedError as e:
        bypass_reason = str(e)
        print(f"  ⊘ DCF bypassed: {e}")
    except Exception as e:
        errors.append(f"DCFEngine failed: {e}")
        print(f"  ✗ DCF failed: {e}")

    # ── Step 2: Reverse DCF ─────────────────────────────────────────────────
    if dcf_result is not None:
        try:
            from aletheia.tools.reverse_dcf import ReverseDCF
            rdcf = ReverseDCF(verbose=False)
            rdcf_result = rdcf.run(calc_input)
            phase2["reverse_dcf"]      = rdcf_result.to_dict()
            phase2["implied_cagr"]      = rdcf_result.implied_revenue_cagr_10y
            phase2["historical_cagr"]   = rdcf_result.historical_cagr_5y
            phase2["reverse_dcf_signal"]   = rdcf_result.signal
            phase2["reverse_dcf_reasons"]  = rdcf_result.signal_reasons
            print(f"  ✓ ReverseDCF: implied={rdcf_result.implied_revenue_cagr_10y:.1%} "
                  f"hist={rdcf_result.historical_cagr_5y:.1%} [{rdcf_result.signal}]")
        except NotImplementedError as e:
            print(f"  ⊘ ReverseDCF bypassed: {e}")
        except Exception as e:
            errors.append(f"ReverseDCF failed: {e}")
            print(f"  ✗ ReverseDCF failed: {e}")

    # ── Step 3: Multiple Decomposition ──────────────────────────────────────
    if dcf_result is not None:
        try:
            from aletheia.tools.multiple_decomposition import MultipleDecomposition
            md = MultipleDecomposition(verbose=False)
            md_result = md.run(calc_input)
            phase2["multiples"]            = md_result.to_dict()
            phase2["ev_ebitda_market"]      = md_result.market_ev_ebitda
            phase2["ev_ebitda_justified"]   = md_result.justified_ev_ebitda
            phase2["ev_ebitda_premium_pct"] = md_result.ev_ebitda_premium_pct
            phase2["multiple_signal"]       = md_result.signal
            phase2["roic_wacc_spread"]      = md_result.roic_wacc_spread
            phase2["value_creation"]        = md_result.value_creation
            # Mirror into the shape ConvictionScorer expects
            phase2["multiple_decomposition"] = {
                "roic":          md_result.roic,
                "wacc":          phase2.get("wacc"),
                "value_creation": md_result.value_creation,
                "premium_pct":   md_result.ev_ebitda_premium_pct,
            }
            print(f"  ✓ Multiples: {md_result.market_ev_ebitda:.1f}x market "
                  f"vs {md_result.justified_ev_ebitda:.1f}x justified "
                  f"[{md_result.signal}]")
        except NotImplementedError as e:
            print(f"  ⊘ Multiples bypassed: {e}")
        except Exception as e:
            errors.append(f"MultipleDecomposition failed: {e}")
            print(f"  ✗ Multiples failed: {e}")

    # ── Step 4: Cyclicality (z-score, peak detection) ───────────────────────
    cyclicality: Dict[str, Any] = {}
    try:
        from aletheia.tools.cyclicality import calculate_z_score
        z_score, is_peak, applies_haircut, avg_3yr, db_context = (
            calculate_z_score(calc_input)
        )
        cyclicality = {
            "z_score": z_score,
            "is_peak": is_peak,
            "applies_cyclical_haircut": applies_haircut,
            "avg_3yr": avg_3yr,
            "db_context": db_context,
        }
        print(f"  ✓ Cyclicality: z={z_score:+.2f} peak={is_peak} haircut={applies_haircut}")
    except Exception as e:
        errors.append(f"Cyclicality failed: {e}")
        print(f"  ✗ Cyclicality failed: {e}")

    # ── Step 5: Operating leverage (deterministic, from DB margins) ─────────
    op_leverage: Dict[str, Any] = {}
    try:
        from aletheia.tools.forensic_metrics import compute_operating_leverage_score
        df = calc_input.df
        if not df.empty:
            row = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0]
            gm = row.get("derived_GrossMargin_Pct")
            ebm = row.get("derived_EBIT_Margin_Pct")
            score = compute_operating_leverage_score(gm, ebm)
            op_leverage = {
                "score": score,
                "gross_margin_pct": float(gm) if gm is not None else None,
                "ebit_margin_pct": float(ebm) if ebm is not None else None,
            }
            print(f"  ✓ OpLeverage: score={score} (gm={gm}, ebm={ebm})")
    except Exception as e:
        errors.append(f"OperatingLeverage failed: {e}")
        print(f"  ✗ OpLeverage failed: {e}")

    # ── Step 6: Moat fingerprint (deterministic, see MOAT_FINGERPRINT_METHODOLOGY.md) ─
    # Per the architecture lock, the calc layer (aletheia/tools/) must not
    # import config. The peer-set lookup for cyclical names happens here in
    # the agent layer (which CAN import config) and is INJECTED into the
    # fingerprint tool as cyclical_peer_gm_cv_median.
    # `mf` is consumed inline by ConvictionScorer below; not returned in
    # state (no downstream consumer reads `moat_fingerprint` from state).
    mf = None
    try:
        from aletheia.tools.moat_fingerprint import (
            compute_moat_fingerprint,
            CYCLICAL_LIFECYCLES,
            CYCLICAL_SECTORS,
            CYCLICAL_PEER_SET_MIN,
            WINDOW_MIN_YEARS,
            _select_window,
            _coefficient_of_variation,
        )
        peer_median: Optional[float] = None
        cls = calc_input.classification
        if cls and (cls.lifecycle in CYCLICAL_LIFECYCLES or cls.sector in CYCLICAL_SECTORS):
            from config.ticker_classification import UNIVERSE
            from aletheia.data.database import InvestmentDatabase
            import numpy as np
            peer_tickers = [
                t for t, c in UNIVERSE.items()
                if t != cls.ticker and (
                    c.lifecycle in CYCLICAL_LIFECYCLES or c.sector in CYCLICAL_SECTORS
                )
            ]
            if len(peer_tickers) >= CYCLICAL_PEER_SET_MIN - 1:
                _db = InvestmentDatabase(verbose=False)
                cvs: list = []
                try:
                    for peer in peer_tickers:
                        try:
                            pdf = _db.get_latest(peer)
                        except Exception:
                            continue
                        if pdf.empty or "derived_GrossMargin_Pct" not in pdf.columns:
                            continue
                        window = _select_window(pdf)
                        gms = [
                            float(v) for v in window["derived_GrossMargin_Pct"]
                            if v is not None and not (isinstance(v, float) and np.isnan(v))
                        ]
                        if len(gms) < WINDOW_MIN_YEARS:
                            continue
                        cv = _coefficient_of_variation(gms)
                        if cv is not None:
                            cvs.append(cv)
                finally:
                    _db.close()
                if len(cvs) >= CYCLICAL_PEER_SET_MIN - 1:
                    peer_median = float(np.median(cvs))

        mf = compute_moat_fingerprint(calc_input, cyclical_peer_gm_cv_median=peer_median)
        if mf.score is not None:
            print(f"  ✓ MoatFP: score={mf.score}/5 "
                  f"(roic={mf.roic_persistence_score}, gm={mf.gm_stability_score}, "
                  f"capex={mf.capex_intensity_score}, yrs={mf.window_years})")
        else:
            print(f"  ⊘ MoatFP: null ({mf.rationale})")
    except Exception as e:
        errors.append(f"MoatFingerprint failed: {e}")
        print(f"  ✗ MoatFP failed: {e}")

    # ── Step 7: Conviction score (deterministic, no agent reads) ────────────
    # Pull every input from calc-layer state. Agent narrative does NOT
    # influence this score — that's the architecture invariant the determinism
    # gate enforces.
    conviction: Dict[str, Any] = {}
    if dcf_result is not None:
        try:
            from aletheia.tools.conviction_scorer import ConvictionScorer

            # Map 1-5 moat fingerprint → 0-10 input expected by MOAT_THRESHOLDS,
            # placed inside each tier so the threshold table reproduces the
            # fingerprint pillar exactly:
            #   fingerprint 5 → 9.5 → MOAT_THRESHOLDS pillar 5
            #   fingerprint 4 → 8.5 → pillar 4
            #   fingerprint 3 → 7.5 → pillar 3
            #   fingerprint 2 → 6.0 → pillar 2
            #   fingerprint 1 → 3.0 → pillar 1
            FP_TO_TENPT = {1: 3.0, 2: 6.0, 3: 7.5, 4: 8.5, 5: 9.5}
            moat_score_input = (
                FP_TO_TENPT[mf.score] if (mf is not None and mf.score is not None)
                else None
            )

            # Margin of safety from DCF base case
            base_mos = None
            if dcf_result.base and dcf_result.current_price:
                base_ips = dcf_result.intrinsic_per_share(
                    dcf_result.base.enterprise_value, dcf_result.net_debt
                )
                if base_ips:
                    base_mos = (base_ips / dcf_result.current_price) - 1

            # Multiple decomposition outputs (already in phase2)
            md_dict = phase2.get("multiple_decomposition", {})
            implied_cagr = phase2.get("implied_cagr")
            historical_cagr = phase2.get("historical_cagr")

            scorer = ConvictionScorer()
            res = scorer._compute(
                ticker=ticker,
                moat_score=moat_score_input,                          # P1 — from fingerprint
                roic=dcf_result.roic, wacc=dcf_result.wacc,
                fcf_margin=op_leverage.get("ebit_margin_pct"),         # informational
                net_debt_bn=(dcf_result.net_debt or 0) / 1e9,
                ebitda_bn=(dcf_result.ebitda or 0) / 1e9,
                data_quality=None,
                rev_cagr=historical_cagr,
                hist_cagr=historical_cagr,
                sector=calc_input.classification.sector if calc_input.classification else "",
                cyclicality_z=cyclicality.get("z_score"),
                is_peak=cyclicality.get("is_peak"),
                base_mos=base_mos,
                sbc_pct_fcf=None,
                op_leverage=op_leverage.get("score"),                  # P5 — deterministic
                upstream_leak=None,                                    # was LLM, retired
                strategic_lev=None,                                    # was LLM, retired
                multiple_premium=md_dict.get("premium_pct"),
                implied_cagr=implied_cagr,
                calc_input=calc_input,
                roe=None,
            )
            conviction = res.to_dict()
            print(f"  ✓ Conviction: {res.conviction_score:+d}/±10 "
                  f"({res.capped_total}/25, tier={res.position_tier})")
        except NotImplementedError as e:
            print(f"  ⊘ Conviction bypassed (DCF skipped): {e}")
        except Exception as e:
            errors.append(f"ConvictionScorer failed: {e}")
            print(f"  ✗ Conviction failed: {e}")

    # ── Summary ──────────────────────────────────────────────────────────────
    summary_lines = [f"=== CalcNode: {ticker} ==="]
    if dcf_result and dcf_result.base:
        ips = dcf_result.intrinsic_per_share(
            dcf_result.base.enterprise_value, dcf_result.net_debt
        ) if dcf_result.base else None
        if ips and dcf_result.current_price:
            mos = (ips / dcf_result.current_price) - 1
            summary_lines.append(f"DCF: base IPS=${ips:,.2f} MoS={mos:+.1%}")
    if phase2.get("implied_cagr"):
        summary_lines.append(
            f"RDCF: implied={phase2['implied_cagr']:.1%} "
            f"hist={phase2.get('historical_cagr',0):.1%}"
        )
    if phase2.get("ev_ebitda_market"):
        summary_lines.append(
            f"EV/EBITDA: {phase2['ev_ebitda_market']:.1f}x mkt vs "
            f"{phase2.get('ev_ebitda_justified',0):.1f}x justified"
        )
    if cyclicality:
        summary_lines.append(
            f"Cyc: z={cyclicality['z_score']:+.2f} peak={cyclicality['is_peak']}"
        )
    if errors:
        summary_lines.append(f"Errors: {'; '.join(errors)}")

    phase2["summary"] = "\n".join(summary_lines)
    phase2["errors"] = errors
    if bypass_reason:
        phase2["dcf_bypass"] = bypass_reason

    # Note: `operating_leverage` and `moat_fingerprint` are computed above
    # and consumed inline by ConvictionScorer (which writes its result into
    # `conviction`). They are deliberately NOT returned in state — the
    # JSON-as-truth investigation confirmed no downstream consumer reads
    # them from state. Removing them from the output reduces orphan churn
    # and prevents future code from accidentally treating them as durable.
    # ── Gate B — pre-agent FMP cross-check on calc outputs ──────────────
    # Stamp-not-abort: blocking-tier drift gets recorded onto state but
    # the calc_node returns normally so downstream agents proceed and
    # the existing partial-rerun design (`/thesis_synthesis/refresh`)
    # stays intact. Gate F (universe-level) is what fails the regen
    # when systematic drift accumulates across tickers. Fail-soft on
    # any FMP unavailability — no calc-layer disruption.
    try:
        from aletheia.data.fmp_validation import validate_calc_output
        calc_validation = validate_calc_output(ticker, phase2)
    except Exception as exc:
        calc_validation = {
            "status":      "skipped",
            "skip_reason": f"validator_error:{type(exc).__name__}",
            "fields":      {},
            "blocking_fields": [],
        }

    output: Dict[str, Any] = {
        "phase2_valuation": phase2,
        "cyclicality": cyclicality,
        "conviction": conviction,
        "calc_bypassed": bypass_reason,
        "_calc_validation": calc_validation,
        "messages": [HumanMessage(content=f"CalcNode: {ticker} — {len(errors)} errors")],
    }
    tracer.log_step("CalcNode", state, output)
    return output
