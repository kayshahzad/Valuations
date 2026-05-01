"""
aletheia/agents/valuation_node.py

All DCF adjustments computed from deterministic Python rules.
LLM boolean/score inputs → Python magnitude rules → DCF engine.
No LLM-generated floats in DCF inputs.

Adjustment rules (all hardcoded here, auditable):
  concentration_risk (bool)       → WACC + 150bps
  has_pricing_power (bool)        → growth decay - 0.01
  strategic_leverage_score (1-10) → terminal growth adj (lookup table)
  terminal_haircut (bool)         → terminal growth cap at 2.0%
  is_cyclical_peak (bool)         → use recommended_base_revenue (Python numpy)
"""

from langchain_core.messages import HumanMessage
from aletheia.utils.tracing import tracer


def compute_dcf_adjustments(forensic: dict, vc: dict, ctx: dict) -> dict:
    """
    Convert LLM boolean/score outputs to deterministic DCF adjustments.
    All magnitude rules are hardcoded here — no LLM numbers.
    """
    # ── WACC penalty ──────────────────────────────────────────────────────────
    # Source: forensic boolean. Magnitude: hardcoded Framework §10.3
    concentration_risk = bool(forensic.get("concentration_risk", False))
    wacc_penalty = 0.015 if concentration_risk else 0.0

    # ── Growth decay reduction ────────────────────────────────────────────────
    # Source: forensic boolean. Magnitude: hardcoded
    has_pricing_power = bool(forensic.get("has_pricing_power", False))
    growth_decay_reduction = 0.01 if has_pricing_power else 0.0

    # ── Terminal growth adjustment ────────────────────────────────────────────
    # Source: value_chain score 1-10. Magnitude: deterministic lookup table
    # Replaces LLM-generated recommended_terminal_growth_adj float
    strategic_leverage = float(vc.get("strategic_leverage_score", 5.0) or 5.0)
    if strategic_leverage >= 8.0:
        terminal_growth_adj = +0.005   # Dominant platform: +50bps
    elif strategic_leverage >= 6.0:
        terminal_growth_adj = 0.0      # Strong: neutral
    elif strategic_leverage >= 4.0:
        terminal_growth_adj = -0.005   # Moderate: -50bps
    else:
        terminal_growth_adj = -0.010   # Commodity: -100bps

    # ── Terminal growth cap ───────────────────────────────────────────────────
    # Source: context boolean. Cap: hardcoded 2.0%
    terminal_haircut = bool(ctx.get("terminal_haircut", False))
    terminal_growth_cap = 0.020 if terminal_haircut else None

    # ── Cyclical peak base revenue ────────────────────────────────────────────
    # Source: context. recommended_base_revenue is Python numpy mean — not LLM
    applies_cyclical_haircut = bool(ctx.get("applies_cyclical_haircut", False))
    recommended_base_rev = ctx.get("recommended_base_revenue")
    base_revenue_override = recommended_base_rev if (
        applies_cyclical_haircut and recommended_base_rev) else None

    return {
        # Magnitudes — Python rules
        "wacc_penalty":              wacc_penalty,
        "growth_decay_reduction":    growth_decay_reduction,
        "terminal_growth_adj":       terminal_growth_adj,
        "terminal_growth_cap":       terminal_growth_cap,
        "base_revenue_override":     base_revenue_override,
        # Source signals — LLM booleans/scores
        "concentration_risk":        concentration_risk,
        "has_pricing_power":         has_pricing_power,
        "strategic_leverage_score":  strategic_leverage,
        "terminal_haircut":          terminal_haircut,
        "is_cyclical_peak":          bool(ctx.get("is_cyclical_peak", False)),
        "applies_cyclical_haircut":  applies_cyclical_haircut,
        # Audit trail
        "rules": {
            "wacc_penalty":      "concentration_risk → +150bps (Framework §10.3)",
            "growth_decay":      "has_pricing_power → decay_rate - 0.01",
            "terminal_growth":   "strategic_leverage 1-10 → lookup table ±0.5-1.0%",
            "terminal_cap":      "terminal_haircut → cap at 2.0%",
            "base_revenue":      "applies_cyclical_haircut → DuckDB 3Y numpy average",
        }
    }


def valuation_node(state: dict) -> dict:
    """
    Phase 2 valuation intelligence.
    All DCF adjustments from Python rules. No LLM numbers in DCF.
    """
    print("---VALUATION NODE (Phase 2 Intelligence)---")

    ticker  = state.get("ticker", "UNKNOWN")
    phase2  = {}
    errors  = []

    forensic   = state.get("forensic_report", {}) or {}
    vc         = state.get("value_chain_report", {}) or {}
    ctx        = state.get("strategic_context_report", {}) or {}
    strategist = state.get("strategist_report", {}) or {}
    capital_stack = strategist.get("capital_stack", {}) or {}
    strategist_wacc = capital_stack.get("wacc")

    # ── Compute adjustments ───────────────────────────────────────────────────
    adj = compute_dcf_adjustments(forensic, vc, ctx)
    phase2["dcf_adjustments"] = adj

    active = [k for k in ("wacc_penalty", "growth_decay_reduction",
                           "terminal_growth_adj", "terminal_growth_cap",
                           "base_revenue_override") if adj.get(k)]
    if active:
        print(f"  ℹ Adjustments active: {active}")

    # ── Step 1: DCF Engine ────────────────────────────────────────────────────
    try:
        from aletheia.tools.dcf_engine import DCFEngine
        engine = DCFEngine(verbose=False)

        dcf_kwargs = {}
        if strategist_wacc is not None:
            dcf_kwargs["wacc_override"] = strategist_wacc + adj["wacc_penalty"]
        elif adj["wacc_penalty"]:
            dcf_kwargs["wacc_penalty"] = adj["wacc_penalty"]

        if adj["growth_decay_reduction"]:
            dcf_kwargs["growth_decay_reduction"] = adj["growth_decay_reduction"]
        if adj["terminal_growth_adj"]:
            dcf_kwargs["terminal_growth_adj"] = adj["terminal_growth_adj"]
        if adj["terminal_growth_cap"] is not None:
            dcf_kwargs["terminal_growth_cap"] = adj["terminal_growth_cap"]
        if adj["base_revenue_override"]:
            dcf_kwargs["base_revenue_override"] = adj["base_revenue_override"]
            print(f"  ℹ Peak: using DuckDB 3Y avg "
                  f"${adj['base_revenue_override']/1e9:.1f}B as base year")
        
        if adj.get("applies_cyclical_haircut"):
            dcf_kwargs["applies_cyclical_haircut"] = True

        dcf_result = engine.run(ticker, **dcf_kwargs)
        if dcf_result.errors:
            errors.extend(dcf_result.errors)

        phase2["dcf"] = dcf_result.to_dict()
        phase2["dcf_object"] = dcf_result

        print(f"  ✓ DCF: bull=${dcf_result.bull.enterprise_value/1e9:.0f}B "
              f"base=${dcf_result.base.enterprise_value/1e9:.0f}B "
              f"bear=${dcf_result.bear.enterprise_value/1e9:.0f}B"
              if dcf_result.bull and dcf_result.base and dcf_result.bear
              else "  ✓ DCF: completed")

    except Exception as e:
        errors.append(f"DCFEngine failed: {e}")
        print(f"  ✗ DCF failed: {e}")
        dcf_result = None

    # ── Step 2: Equity Bridge ─────────────────────────────────────────────────
    bridge_results = {}
    intrinsic_per_share = {}
    margin_of_safety = {}

    if dcf_result and not dcf_result.errors:
        try:
            from aletheia.tools.equity_bridge import EquityBridge
            bridge = EquityBridge(verbose=False)
            bridges = bridge.build_for_dcf(ticker, dcf_result)
            for s, b in bridges.items():
                bridge_results[s]      = b.to_dict()
                intrinsic_per_share[s] = b.intrinsic_per_share
                margin_of_safety[s]    = b.margin_of_safety

            phase2["bridge"]              = bridge_results
            phase2["intrinsic_per_share"] = intrinsic_per_share
            phase2["margin_of_safety"]    = margin_of_safety
            print(f"  ✓ Bridge: base IV=${intrinsic_per_share.get('base',0):,.0f} "
                  f"MoS={margin_of_safety.get('base',0):+.1%}")

        except Exception as e:
            errors.append(f"EquityBridge failed: {e}")
            print(f"  ✗ Bridge failed: {e}")

    # ── Step 3: Reverse DCF ───────────────────────────────────────────────────
    try:
        from aletheia.tools.reverse_dcf import ReverseDCF
        rdcf = ReverseDCF(verbose=False)
        rdcf_kwargs = {}
        if adj["base_revenue_override"]:
            rdcf_kwargs["base_revenue_override"] = adj["base_revenue_override"]
        if adj.get("applies_cyclical_haircut"):
            rdcf_kwargs["applies_cyclical_haircut"] = True

        rdcf_result = rdcf.run(ticker, **rdcf_kwargs)
        phase2["reverse_dcf"]        = rdcf_result.to_dict()
        phase2["implied_cagr"]        = rdcf_result.implied_revenue_cagr_10y
        phase2["historical_cagr"]     = rdcf_result.historical_cagr_5y
        phase2["reverse_dcf_signal"]  = rdcf_result.signal
        phase2["reverse_dcf_reasons"] = rdcf_result.signal_reasons

        print(f"  ✓ ReverseDCF: implied={rdcf_result.implied_revenue_cagr_10y:.1%} "
              f"hist={rdcf_result.historical_cagr_5y:.1%} [{rdcf_result.signal}]")

    except Exception as e:
        errors.append(f"ReverseDCF failed: {e}")
        print(f"  ✗ ReverseDCF failed: {e}")

    # ── Step 4: Multiple Decomposition ───────────────────────────────────────
    try:
        from aletheia.tools.multiple_decomposition import MultipleDecomposition
        md = MultipleDecomposition(verbose=False)
        md_result = md.run(ticker)
        phase2["multiples"]             = md_result.to_dict()
        phase2["ev_ebitda_market"]       = md_result.market_ev_ebitda
        phase2["ev_ebitda_justified"]    = md_result.justified_ev_ebitda
        phase2["ev_ebitda_premium_pct"]  = md_result.ev_ebitda_premium_pct
        phase2["multiple_signal"]        = md_result.signal
        phase2["roic_wacc_spread"]       = md_result.roic_wacc_spread
        phase2["value_creation"]         = md_result.value_creation

        if "multiple_decomposition" not in phase2:
            phase2["multiple_decomposition"] = {}
        phase2["multiple_decomposition"]["roic"] = md_result.roic
        phase2["multiple_decomposition"]["wacc"] = phase2.get("wacc")

        print(f"  ✓ Multiples: {md_result.market_ev_ebitda:.1f}x market "
              f"vs {md_result.justified_ev_ebitda:.1f}x justified "
              f"[{md_result.signal}]")

    except Exception as e:
        errors.append(f"MultipleDecomposition failed: {e}")
        print(f"  ✗ Multiples failed: {e}")

    # ── Step 5: Summary ───────────────────────────────────────────────────────
    summary_lines = [f"=== Phase 2 Valuation: {ticker} ==="]
    if intrinsic_per_share:
        price = phase2.get("dcf", {}).get("current_price", 0)
        summary_lines += [
            f"Price: ${price:,.2f}  |  "
            f"Bear=${intrinsic_per_share.get('bear',0):,.0f}  "
            f"Base=${intrinsic_per_share.get('base',0):,.0f}  "
            f"Bull=${intrinsic_per_share.get('bull',0):,.0f}",
            f"MoS:  Bear={margin_of_safety.get('bear',0):+.1%}  "
            f"Base={margin_of_safety.get('base',0):+.1%}  "
            f"Bull={margin_of_safety.get('bull',0):+.1%}",
        ]
    if phase2.get("implied_cagr"):
        summary_lines.append(
            f"RDCF: implied={phase2['implied_cagr']:.1%} "
            f"hist={phase2.get('historical_cagr',0):.1%} "
            f"[{phase2.get('reverse_dcf_signal','?')}]"
        )
    if phase2.get("ev_ebitda_market"):
        summary_lines.append(
            f"EV/EBITDA: {phase2['ev_ebitda_market']:.1f}x market "
            f"vs {phase2.get('ev_ebitda_justified',0):.1f}x justified "
            f"({phase2.get('ev_ebitda_premium_pct',0):+.0%})"
        )
    if adj.get("wacc_penalty") or adj.get("terminal_growth_adj"):
        summary_lines.append(
            f"Adj: WACC+{adj['wacc_penalty']:.3f} "
            f"terminal{adj['terminal_growth_adj']:+.3f} "
            f"cap={adj['terminal_growth_cap']}"
        )
    if errors:
        summary_lines.append(f"Errors: {'; '.join(errors)}")

    phase2["summary"] = "\n".join(summary_lines)
    phase2["errors"]  = errors
    phase2.pop("dcf_object", None)

    print(f"  ✓ Valuation node complete. {len(errors)} errors.")

    output = {
        "phase2_valuation": phase2,
        "messages": [HumanMessage(content=(
            f"ValuationNode: {ticker} — "
            f"Base IV=${intrinsic_per_share.get('base',0):,.0f}/share "
            f"MoS={margin_of_safety.get('base',0):+.1%} "
            f"ImpliedCAGR={phase2.get('implied_cagr',0):.1%} "
            f"Multiple={phase2.get('multiple_signal','unknown')}"
        ))]
    }
    tracer.log_step("ValuationNode", state, output)
    return output
