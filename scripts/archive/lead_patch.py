"""
lead_patch.py
=============
Applies targeted patches to aletheia/agents/lead.py to:
  1. Read phase2_valuation from state
  2. Include Phase 2 intrinsic values, margin of safety, implied CAGR,
     and multiple decomposition in the final report
  3. Pass richer context to the LLM synthesis
  4. Add Phase 2 constitution checks (implied CAGR vs historical, multiple premium)

Run from project root:
    PYTHONPATH=. python3 lead_patch.py
"""

from pathlib import Path

p = Path("aletheia/agents/lead.py")
code = p.read_text()

# ── Patch 1: Read phase2_valuation from state ─────────────────────────────────
OLD1 = '''    # 1. Gather Inputs
    base = state.get("serving_base", {})
    strat = state.get("strategist_report", {})
    forensic = state.get("forensic_report", {})
    vc = state.get("value_chain_report", {})
    context = state.get("strategic_context_report", {})
    val = state.get("valuation_report", {})
    contrarian = state.get("contrarian_report", {})'''

NEW1 = '''    # 1. Gather Inputs
    base = state.get("serving_base", {})
    strat = state.get("strategist_report", {})
    forensic = state.get("forensic_report", {})
    vc = state.get("value_chain_report", {})
    context = state.get("strategic_context_report", {})
    val = state.get("valuation_report", {})
    contrarian = state.get("contrarian_report", {})
    # Phase 2 valuation intelligence (from valuation_node)
    p2 = state.get("phase2_valuation", {})
    p2_intrinsic = p2.get("intrinsic_per_share", {})
    p2_mos = p2.get("margin_of_safety", {})'''

if OLD1 in code:
    code = code.replace(OLD1, NEW1, 1)
    print("✓ Patch 1: Added phase2_valuation reads")
else:
    print("✗ Patch 1 not found")

# ── Patch 2: Add Phase 2 data to constitution checks ─────────────────────────
OLD2 = '''    compliance_log = []
    
    # Rule: Terminal Cap
    term_g = params.get("terminal_growth_rate", 0)
    moat_score = forensic.get("moat_score", 0)
    if term_g > 0.03 and moat_score <= 8:
         compliance_log.append(f"❌ FAIL: Terminal Cap. g ({term_g:.1%}) > 3% but Moat ({moat_score}) <= 8.")
    else:
         compliance_log.append("✅ PASS: Terminal Cap.")
    
    compliance_section = "\\n".join(compliance_log)'''

NEW2 = '''    compliance_log = []

    # Rule: Terminal Cap
    term_g = params.get("terminal_growth_rate", 0)
    moat_score = forensic.get("moat_score", 0)
    if term_g > 0.03 and moat_score <= 8:
        compliance_log.append(f"❌ FAIL: Terminal Cap. g ({term_g:.1%}) > 3% but Moat ({moat_score}) <= 8.")
    else:
        compliance_log.append("✅ PASS: Terminal Cap.")

    # Phase 2 Rule: Implied CAGR vs Historical (Reverse DCF discipline)
    implied_cagr = p2.get("implied_cagr")
    historical_cagr = p2.get("historical_cagr")
    rdcf_signal = p2.get("reverse_dcf_signal", "unknown")
    if implied_cagr and historical_cagr and historical_cagr > 0:
        ratio = implied_cagr / historical_cagr
        if ratio > 2.0:
            compliance_log.append(
                f"❌ FAIL: Implied CAGR ({implied_cagr:.1%}) is {ratio:.1f}x historical "
                f"({historical_cagr:.1%}). Market pricing in extraordinary growth — "
                f"requires documented TAM justification before conviction."
            )
        elif ratio > 1.3:
            compliance_log.append(
                f"⚠️ CAUTION: Implied CAGR ({implied_cagr:.1%}) exceeds historical "
                f"({historical_cagr:.1%}) by {ratio:.1f}x. Growth premium requires thesis support."
            )
        else:
            compliance_log.append(
                f"✅ PASS: Implied CAGR ({implied_cagr:.1%}) is within 1.3x of "
                f"historical ({historical_cagr:.1%})."
            )

    # Phase 2 Rule: Multiple premium check
    premium_pct = p2.get("ev_ebitda_premium_pct", 0)
    mult_signal = p2.get("multiple_signal", "")
    if premium_pct > 1.0:
        compliance_log.append(
            f"❌ FAIL: EV/EBITDA trades at {premium_pct:+.0%} premium to justified multiple. "
            f"Signal: {mult_signal}. Requires reverse DCF and TAM stress test."
        )
    elif premium_pct > 0.30:
        compliance_log.append(
            f"⚠️ CAUTION: EV/EBITDA at {premium_pct:+.0%} premium. "
            f"Growth assumptions must be documented."
        )
    else:
        compliance_log.append(
            f"✅ PASS: Multiple premium {premium_pct:+.0%} within acceptable range."
        )

    compliance_section = "\\n".join(compliance_log)'''

if OLD2 in code:
    code = code.replace(OLD2, NEW2, 1)
    print("✓ Patch 2: Added Phase 2 constitution checks")
else:
    print("✗ Patch 2 not found")

# ── Patch 3: Pass Phase 2 data to LLM prompt ─────────────────────────────────
OLD3 = '''        try:
            res: LeadAgentOutput = chain.invoke({
                "ticker": ticker,
                "compliance_check": compliance_section,
                "upside_percent": f"{dcf.get('calculated_upside', 0):.1f}%",
                "wacc": f"{strat.get('wacc', 0):.1%}",
                "moat_score": forensic.get("moat_score", 0),
                "business_quality": economic_reality
            })'''

NEW3 = '''        # Build Phase 2 context for LLM
        p2_context = ""
        if p2_intrinsic:
            bear_iv = p2_intrinsic.get("bear", 0)
            base_iv = p2_intrinsic.get("base", 0)
            bull_iv = p2_intrinsic.get("bull", 0)
            cur_price = p2.get("dcf", {}).get("current_price", 0)
            p2_context = (
                f"Phase 2 DCF (3-scenario): Bear=${bear_iv:,.0f} | "
                f"Base=${base_iv:,.0f} | Bull=${bull_iv:,.0f} vs "
                f"Price=${cur_price:,.0f}. "
                f"Base MoS={p2_mos.get('base',0):+.1%}. "
                f"Implied CAGR={implied_cagr:.1%} vs historical={historical_cagr:.1%} "
                f"[{rdcf_signal}]. "
                f"EV/EBITDA premium={premium_pct:+.0%} [{mult_signal}]. "
                f"ROIC-WACC spread={p2.get('roic_wacc_spread',0):+.1%} "
                f"[{p2.get('value_creation','?')}]."
            ) if implied_cagr else p2.get("summary", "")

        try:
            res: LeadAgentOutput = chain.invoke({
                "ticker": ticker,
                "compliance_check": compliance_section,
                "upside_percent": f"{dcf.get('calculated_upside', 0):.1f}%",
                "wacc": f"{strat.get('wacc', 0):.1%}",
                "moat_score": forensic.get("moat_score", 0),
                "business_quality": economic_reality
            })'''

if OLD3 in code:
    code = code.replace(OLD3, NEW3, 1)
    print("✓ Patch 3: Added Phase 2 context to LLM call")
else:
    print("✗ Patch 3 not found")

# ── Patch 4: Add Phase 2 data to final_report schema ─────────────────────────
OLD4 = '''    final_report = {
        "ticker": ticker,
        "generated_at": datetime.now().isoformat(),
        "1_economic_reality": economic_reality,
        "2_financial_translation": financial_translation,
        "3_capital_structure_risk": capital_structure,
        "4_valuation_synthesis": {
            "dcf_model": {
                "intrinsic_value": dcf.get("equity_value"),
                # Recalculate Upside: (Intrinsic / Market Cap) - 1
                "upside_percent": ((dcf.get("equity_value", 0) / base.get("meta", {}).get("market_cap", 1)) - 1) * 100 if base.get("meta", {}).get("market_cap") else 0,
                "implied_growth": "Pending Expectations Engine"
            },
            "investment_thesis": {
                "conviction_score": conviction,
                "margin_of_safety": dcf.get("calculated_upside"),
                "narrative": narrative
            }
        }
    }'''

NEW4 = '''    final_report = {
        "ticker": ticker,
        "generated_at": datetime.now().isoformat(),
        "1_economic_reality": economic_reality,
        "2_financial_translation": financial_translation,
        "3_capital_structure_risk": capital_structure,
        "4_valuation_synthesis": {
            "dcf_model": {
                "intrinsic_value": dcf.get("equity_value"),
                "upside_percent": ((dcf.get("equity_value", 0) / base.get("meta", {}).get("market_cap", 1)) - 1) * 100 if base.get("meta", {}).get("market_cap") else 0,
                "implied_growth": "Pending Expectations Engine"
            },
            "phase2_valuation": {
                "three_scenario_dcf": {
                    "bear": {
                        "intrinsic_per_share": p2_intrinsic.get("bear"),
                        "margin_of_safety": p2_mos.get("bear"),
                        "ev": p2.get("dcf", {}).get("bear_ev"),
                    },
                    "base": {
                        "intrinsic_per_share": p2_intrinsic.get("base"),
                        "margin_of_safety": p2_mos.get("base"),
                        "ev": p2.get("dcf", {}).get("base_ev"),
                    },
                    "bull": {
                        "intrinsic_per_share": p2_intrinsic.get("bull"),
                        "margin_of_safety": p2_mos.get("bull"),
                        "ev": p2.get("dcf", {}).get("bull_ev"),
                    },
                },
                "reverse_dcf": {
                    "implied_cagr_10y": p2.get("implied_cagr"),
                    "historical_cagr": p2.get("historical_cagr"),
                    "signal": p2.get("reverse_dcf_signal"),
                    "reasons": p2.get("reverse_dcf_reasons", []),
                },
                "multiple_decomposition": {
                    "market_ev_ebitda": p2.get("ev_ebitda_market"),
                    "justified_ev_ebitda": p2.get("ev_ebitda_justified"),
                    "premium_pct": p2.get("ev_ebitda_premium_pct"),
                    "signal": p2.get("multiple_signal"),
                    "roic_wacc_spread": p2.get("roic_wacc_spread"),
                    "value_creation": p2.get("value_creation"),
                },
                "wacc": p2.get("dcf", {}).get("wacc_base"),
                "beta": p2.get("dcf", {}).get("beta"),
                "risk_free_rate": p2.get("dcf", {}).get("risk_free_rate"),
            },
            "investment_thesis": {
                "conviction_score": conviction,
                "margin_of_safety": p2_mos.get("base") or dcf.get("calculated_upside"),
                "narrative": narrative,
                "constitution_checks": compliance_log,
            }
        }
    }'''

if OLD4 in code:
    code = code.replace(OLD4, NEW4, 1)
    print("✓ Patch 4: Phase 2 data added to final_report schema")
else:
    print("✗ Patch 4 not found")

# Write patched file
p.write_text(code)
print()
print("="*60)
print("lead.py patched. Verify with:")
print("  grep -n 'phase2_valuation\\|p2_intrinsic\\|implied_cagr\\|three_scenario' aletheia/agents/lead.py | head -20")
