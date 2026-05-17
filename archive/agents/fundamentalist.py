import json
from langchain_core.messages import HumanMessage
from aletheia.utils.tracing import tracer


def fundamentalist_agent(state):
    """
    The Fundamentalist: Valuation Engine.

    Legacy ProForma path. Superseded by `valuation_node` which calls DCFEngine
    directly. Kept in the workflow for backwards compatibility with the
    `valuation_report` state field consumed by `lead_agent`. Skips silently if
    the ProForma module is unavailable.
    """
    print("---FUNDAMENTALIST AGENT (Served)---")

    try:
        from aletheia.tools.pro_forma import ProFormaEngine
        from aletheia.tools.dcf_assumptions import (
            load_archetype_templates, build_base_assumptions
        )
    except ImportError as e:
        print(f"  ⊘ ProForma path unavailable ({e}); valuation_node handles DCF.")
        return {
            "valuation_report": {},
            "messages": [HumanMessage(content="Fundamentalist: skipped (ProForma not installed)")]
        }
    
    ticker = state.get("ticker", "UNKNOWN")
    
    # 1. Fetch from Database
    from aletheia.data.database import InvestmentDatabase
    from aletheia.data.market_data import get_current_price, get_market_cap, get_shares_outstanding
    import pandas as pd
    
    db = InvestmentDatabase()
    try:
        record_df = db.get_latest(ticker)
        if record_df.empty:
            return {"messages": [HumanMessage(content=f"Fundamentalist: No DB data for {ticker}")]}
        # Always use LATEST fiscal year — iloc[0] is oldest
        row = record_df[record_df["fiscal_year"] == record_df["fiscal_year"].max()].iloc[0]
        clean_data = json.loads(row["clean_json"])
    finally:
        db.close()
        
    if row.get("overall_quality_score", 1.0) < 0.8:
        print(f"⛔ CRITICAL: DB Quality Score is {row.get('overall_quality_score')}. Valuation may be invalid.")

    # 2. Fetch Live Market Data
    market_price = get_current_price(ticker)
    market_cap = get_market_cap(ticker)
    
    shares_diluted = None
    if market_cap and market_price and market_price > 0:
        shares_diluted = market_cap / market_price
    
    # 3. Inputs from Strategist
    strategist = state.get("strategist_report", {})
    # WACC lives at capital_stack.wacc — not top-level
    wacc = (
        strategist.get("capital_stack", {}).get("wacc")
        or strategist.get("wacc")  # legacy fallback
        or 0.09
    )
    
    # 4. Construct Logic
    dcf_config = state.get("dcf_config") or {}
    
    archetypes = load_archetype_templates()
    assumptions, net_debt = build_base_assumptions(row, clean_data, dcf_config, wacc, archetypes)
        
    revenue = row.get("clean_Revenue", 1.0)
    if not revenue or pd.isna(revenue) or revenue == 0:
        revenue = 1.0
        
    base_financials = {
        "net_sales": revenue if revenue != 1.0 else 0.0,
        "cogs": clean_data.get("COGS", 0.0),
        "interest_expense": 0.0,
        "net_debt": net_debt
    }
    
    # 5. Pro Forma Execution
    try:
        pf_engine = ProFormaEngine(base_financials, assumptions)
        result = pf_engine.generate_forecast()
        
        # --- DCF Synthesis Mapping ---
        from aletheia.utils.dcf_synthesis import build_dcf_model_from_proforma
        
        dcf_model = build_dcf_model_from_proforma(
            result,
            shares_diluted=shares_diluted,
            market_price=market_price,
            intrinsic_is_per_share=True
        )
        
        upside = dcf_model.get('upside_percent')
        upside_str = f"{upside:.1%}" if upside is not None else "N/A"
        print(f"Fundamentalist: Valued {ticker}. Upside: {upside_str}")
        
        output = {
            "valuation_report": { 
                 "dcf_result": result, 
                 "dcf_model": dcf_model,
                 "assumptions_used": assumptions,
                 "base_financials": base_financials,
                 "calculated_upside": upside
            },
            "messages": [HumanMessage(content=f"Fundamentalist: DCF Completed. EV: ${dcf_model['enterprise_value']:,.0f}")]
        }
        tracer.log_step("Fundamentalist", state, output)
        return output
        
    except Exception as e:
        print(f"Fundamentalist Error: {e}")
        return {"messages": [HumanMessage(content=f"Fundamentalist Failed: {e}")]}
