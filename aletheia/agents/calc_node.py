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


def _phase2_dcf_dict(valuation_result, dcf_result) -> Dict[str, Any]:
    """Project a ValuationResult into the legacy ``phase2["dcf"]``
    dict shape that downstream consumers (lead.py,
    thesis_synthesizer, Streamlit views) read.

    Two paths:
      1. **FCFF result** (``engine="fcff"``): ``dcf_result`` is the
         underlying ``DCFResult``. We just call its ``to_dict()`` —
         identical to pre-Phase-A.4 behavior. Zero regression risk
         for the 35 FCFF-compatible tickers.

      2. **Non-FCFF result** (rate-base today, DDM/embedded-value
         later): build a minimal compat dict from the ValuationResult.
         Only fields lead.py + thesis_synthesizer actually read are
         populated; bull/bear scenario fields stay None since
         rate-base produces a single-point IV. The ``engine`` field
         lets consumers branch when they want to render differently.
    """
    if dcf_result is not None:
        # FCFF path — defer to the existing rich serializer
        return dcf_result.to_dict()

    # Non-FCFF: synthesize the dict shape lead.py expects
    snap = valuation_result.inputs_snapshot or {}
    return {
        "ticker":         valuation_result.ticker,
        "fiscal_year":    valuation_result.fiscal_year,
        "current_price":  valuation_result.current_price,
        "shares_diluted": snap.get("shares_diluted"),
        "market_cap":     (
            (valuation_result.current_price or 0)
            * (snap.get("shares_diluted") or 0) or None
        ),
        "risk_free_rate": snap.get("risk_free_rate"),
        "beta":           snap.get("beta"),
        "wacc_base":      snap.get("cost_of_equity"),
        # Base-case fields lead.py reads for IPS/MoS
        "base_intrinsic_per_share": valuation_result.intrinsic_per_share,
        "base_upside":              valuation_result.margin_of_safety,
        "base_ev":                  valuation_result.equity_value,
        # No bull/bear scenarios in rate-base; consumers fall back to
        # base for any "if scenario" branches
        "bull_intrinsic_per_share": None,
        "bear_intrinsic_per_share": None,
        # Engine-specific payload for the Deep Dive view
        "engine_specific":          valuation_result.engine_specific,
    }


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

    # ── Step 1: Valuation (router dispatches by business_model) ─────────────
    # Phase A.4: replaced direct DCFEngine call with ValuationRouter.
    # Router picks the right engine per classification.business_model:
    #   - fcff_compatible  → FcffEngine (wraps DCFEngine; zero behavior change)
    #   - routing_required → RateBaseEngine (NEE today; Phase A.7 extends)
    #   - ddm_required / embedded_value_required → EngineNotImplementedError
    #     (Phase A.7 will fill these in)
    dcf_result = None
    valuation_result = None
    bypass_reason = None
    try:
        from aletheia.tools.valuation_router import (
            EngineNotImplementedError, ValuationRouter,
        )
        valuation_result = ValuationRouter().execute(calc_input)
        # Extract the underlying DCFResult when the FCFF engine ran; it's
        # the rich object ReverseDCF + MultipleDecomposition + lead.py
        # expect. Non-FCFF engines write a compatibility dict directly.
        dcf_result = valuation_result.engine_specific.get("dcf_result")
        phase2["dcf"] = _phase2_dcf_dict(valuation_result, dcf_result)
        phase2["wacc"] = (
            dcf_result.wacc if dcf_result is not None
            else valuation_result.inputs_snapshot.get("cost_of_equity")
        )
        # Phase A.4: surface which engine ran. Used by Deep Dive UI
        # provenance pill + serving-report engine field.
        phase2["engine"] = valuation_result.engine
        # Surface the engine's valuation decomposition (e.g. the two-stage AFFO
        # table for REITs / the DDM dividend ladder) so the memo + UI can render
        # the math. Top-level key mirrors the no-LLM rebuild path.
        phase2["valuation_decomposition"] = (
            (valuation_result.engine_specific or {}).get("decomposition"))
        phase2["specialized_inputs"] = valuation_result.inputs_snapshot
        if dcf_result is not None and dcf_result.errors:
            errors.extend(dcf_result.errors)
        if valuation_result.warnings:
            errors.extend(valuation_result.warnings)

        if valuation_result.engine == "fcff" and dcf_result is not None and \
                dcf_result.bull and dcf_result.base and dcf_result.bear:
            print(f"  ✓ FCFF DCF: bull=${dcf_result.bull.enterprise_value/1e9:.0f}B "
                  f"base=${dcf_result.base.enterprise_value/1e9:.0f}B "
                  f"bear=${dcf_result.bear.enterprise_value/1e9:.0f}B")
        elif valuation_result.engine == "rate_base":
            ips = valuation_result.intrinsic_per_share
            ips_str = f"${ips:.2f}" if ips is not None else "N/A"
            ev = valuation_result.equity_value
            ev_str = f"${ev/1e9:.0f}B" if ev is not None else "N/A"
            print(f"  ✓ Rate-base DCF: IPS={ips_str}  Equity={ev_str}")
        else:
            print(f"  ✓ Valuation: completed ({valuation_result.engine}, partial)")
    except EngineNotImplementedError as e:
        bypass_reason = str(e)
        print(f"  ⊘ Valuation bypassed: {e}")
    except NotImplementedError as e:
        # Legacy path — DCFEngine's hard-stop fired (e.g. KNOWN_ISSUES bypass)
        bypass_reason = str(e)
        print(f"  ⊘ DCF bypassed: {e}")
    except Exception as e:
        errors.append(f"Valuation router failed: {e}")
        print(f"  ✗ Valuation failed: {e}")

    # ── Step 2: Reverse DCF ─────────────────────────────────────────────────
    # Pass the engine BASE scenario's assumption stack through so the
    # reverse DCF solves for "what CAGR makes IV = market price under
    # the same terminal margin / WACC / terminal growth the base case
    # uses?" — instead of running independently against its own
    # hardcoded constants. Without this, base IV could move materially
    # (e.g., terminal growth clamp from 3.5% → 2.5%) while reverse DCF
    # silently retains a stale implied CAGR.
    if dcf_result is not None:
        try:
            from aletheia.tools.reverse_dcf import ReverseDCF
            rdcf = ReverseDCF(verbose=False)
            base_sc = getattr(dcf_result, "base", None)
            base_assumptions = getattr(base_sc, "assumptions", None) if base_sc else None
            kwargs = {}
            if base_assumptions is not None:
                kwargs["wacc_override"] = float(base_assumptions.wacc)
                kwargs["margin_override"] = float(base_assumptions.ebit_margin_terminal)
                kwargs["terminal_growth_override"] = float(base_assumptions.terminal_growth)
            rdcf_result = rdcf.run(calc_input, **kwargs)
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

    # ── Step 6b: Value source decomposition (deterministic, spec §3 / Build 4)
    # Re-attributes existing signals into Operating/Financial/Multiple shares +
    # a governance modifier so the conviction gate can scale by return
    # durability. Computed for both FCFF (dcf_result) and specialized engines
    # (REIT/DDM via p2 specialized_inputs). Stored in phase2 BEFORE conviction
    # so the value-source cap (Build 5) can read it.
    try:
        from aletheia.tools.value_source_decomposition import (
            build_value_source_decomposition,
        )
        if dcf_result is not None or phase2.get("specialized_inputs"):
            vsd = build_value_source_decomposition(
                calc_input, dcf_result, p2=phase2, state=state,
            )
            phase2["value_source_decomposition"] = vsd
            if vsd.get("available"):
                print(f"  ✓ Value-source: op {vsd['operating_share']:.0%} / "
                      f"fin {vsd['financial_share']:.0%} / "
                      f"mult {vsd['multiple_share']:.0%} (gov {vsd['gov_modifier']:+d})")
    except Exception as e:
        errors.append(f"ValueSourceDecomposition failed: {e}")
        print(f"  ✗ Value-source decomposition failed: {e}")

    # ── Step 6c: SaaS analysis overlay (deterministic, gated, Build B) ──────
    # Subscription-economics KPIs (owner's-earnings FCF, magic number, gross-
    # margin trend, drawdown, cyclicality reconciliation). Computes ONLY for
    # SaaS names (is_saas_company); non-SaaS get available=False. No conviction
    # impact — purely diagnostic context for the memo/deep-dive.
    try:
        from aletheia.tools.saas_metrics import build_saas_metrics
        _mcap = getattr(dcf_result, "market_cap", None) if dcf_result is not None else None
        _sm = build_saas_metrics(calc_input, market_cap=_mcap)
        if _sm.get("available"):
            phase2["saas_metrics"] = _sm
            print("  ✓ SaaS overlay: owner's-earnings + magic# + cyclicality flag")
    except Exception as e:
        errors.append(f"SaaSMetrics failed: {e}")

    # ── Step 6d: CADS coverage (deterministic, §9 credit floor, Phase 2) ────
    # Cash available for debt service (EBITDA − CapEx). Catches the case where
    # the valuation looks fine but operations can't self-fund debt (EQIX
    # capex-sinkhole). CF-R5 trigger fires deterministically into the risk view.
    try:
        from aletheia.tools.cads_coverage import build_cads_coverage
        _cads = build_cads_coverage(calc_input)
        if _cads.get("available"):
            phase2["cads"] = _cads
            if _cads.get("trigger"):
                print(f"  ⚠ CADS trigger: {_cads.get('trigger_reason')}")
    except Exception as e:
        errors.append(f"CADS failed: {e}")

    # ── Step 6e: Capital-structure flag + four-method convergence (Phase 0/1/3)
    # The stability flag selects the discount-rate family; the convergent set
    # values the firm four ways as a correctness diagnostic (additive, not the
    # headline IV).
    try:
        from aletheia.tools.capital_structure_flag import build_capital_structure_flag
        _mcap = getattr(dcf_result, "market_cap", None) if dcf_result is not None else None
        _csf = build_capital_structure_flag(calc_input, market_cap=_mcap)
        if _csf.get("available"):
            phase2["capital_structure_flag"] = _csf
    except Exception as e:
        errors.append(f"CapStructFlag failed: {e}")
    try:
        from aletheia.tools.valuation_methods import build_valuation_methods
        _vm = build_valuation_methods(calc_input, dcf_result, phase2)
        if _vm.get("available"):
            phase2["valuation_methods"] = _vm
            print(f"  ✓ Four-method convergence: family {_vm.get('rate_family')} "
                  f"trio spread {(_vm.get('ev_trio_spread') or 0)*100:.1f}%")
    except Exception as e:
        errors.append(f"ValuationMethods failed: {e}")

    # ── Step 6f: Bank convergent set (residual income / justified P/B / DDM) ──
    # The financial-sector analog of the four-method FCFF convergence. Banks have
    # no FCFF kernel, so the four-method engine refuses them; this values equity
    # off book + ROE three ways and reconciles vs the headline DDM (catching the
    # low-payout understatement). Gated on the business model — non-banks no-op.
    try:
        from aletheia.tools.bank_valuation_methods import build_bank_valuation_methods
        _bvm = build_bank_valuation_methods(calc_input, valuation_result, phase2)
        if _bvm.get("available"):
            phase2["bank_valuation_methods"] = _bvm
            _ri = (_bvm.get("methods") or {}).get("residual_income", {}).get("iv")
            print(f"  ✓ Bank convergent set: residual income IV "
                  f"${_ri:,.0f}" if _ri else "  ✓ Bank convergent set computed")
    except Exception as e:
        errors.append(f"BankValuationMethods failed: {e}")

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
                state={"phase2_valuation": phase2},   # exposes value_source_decomposition (Build 5/6)
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
        # Pass the DCFResult object (not JSON-serializable) through state
        # so lead.py can build the 5_financial_metrics block. Underscore
        # prefix marks it as Python-only — never serialized into the
        # report JSON. None for non-FCFF engines (rate-base / DDM / EV).
        "_dcf_result": dcf_result,
        "messages": [HumanMessage(content=f"CalcNode: {ticker} — {len(errors)} errors")],
    }
    tracer.log_step("CalcNode", state, output)
    return output
