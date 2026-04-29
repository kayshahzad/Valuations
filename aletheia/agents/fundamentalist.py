from pathlib import Path
import json
from datetime import datetime
from typing import Dict, Any
from langchain_core.messages import HumanMessage
from aletheia.tools.pro_forma import ProFormaEngine
from aletheia.utils.config import load_config
from aletheia.utils.tracing import tracer

def load_archetype_templates() -> Dict[str, Any]:
    try:
        path = Path("aletheia/config/archetype_templates.json")
        if path.exists():
            with open(path, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load archetype templates: {e}")
    return {}

def apply_archetype_overrides(assumptions: Dict[str, Any], archetype_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply overrides from Archetype Template.
    """
    overrides = assumptions.copy()
    
    # Map WC Floor
    if "wc_change_percent_sales" in archetype_data:
        overrides["wc_change_percent_sales"] = archetype_data["wc_change_percent_sales"]
        
    # Validation Model Hint (Optional logging or logic switch)
    if "valuation_model" in archetype_data:
        overrides["_valuation_model_hint"] = archetype_data["valuation_model"]
        
    return overrides

def fundamentalist_agent(state):
    """
    The Fundamentalist: Valuation Engine.
    Consumes InvestmentDatabase (Phase 1) + Strategist (WACC) to produce DCF.
    """
    print("---FUNDAMENTALIST AGENT (Served)---")
    
    ticker = state.get("ticker", "UNKNOWN")
    
    # 1. Fetch from Database
    from aletheia.data.database import InvestmentDatabase
    from aletheia.tools.finance import get_financial_metrics
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
    live_metrics = get_financial_metrics(ticker)
    market_price = live_metrics.get("current_price")
    market_cap = live_metrics.get("market_cap")
    
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
    
    revenue = row.get("clean_Revenue", 1.0)
    if not revenue or pd.isna(revenue) or revenue == 0:
        revenue = 1.0
        
    sga = clean_data.get("SG&A", 0.0)
    da = clean_data.get("Depreciation", 0.0)
    capex = clean_data.get("CapEx_Total", 0.0)
    
    tax_rate = row.get("clean_CashTaxRate", 0.21)
    if pd.isna(tax_rate):
        tax_rate = 0.21
        
    assumptions = {
        "revenue_growth_initial": dcf_config.get("revenue_growth_initial", 0.10),
        "revenue_growth_decay": 0.02,
        "terminal_growth_rate": 0.03,
        "sga_percent_sales": sga / revenue,
        "da_percent_sales": da / revenue,
        "tax_rate": tax_rate,
        "capex_percent_sales": capex / revenue,
        "wacc": wacc
    }
    
    # --- Archetype Logic Bridge ---
    archetypes = load_archetype_templates()
    sector = live_metrics.get("sector", "General")
    archetype_key = "Asset_Heavy" # Default
    
    if sector in ["Financial Services", "Insurance"]:
        archetype_key = "Float_Heavy"
    else:
        # Determine Knowledge vs Asset Heavy
        gross_margin = row.get("derived_GrossMargin_Pct", 0.0)
        if not pd.isna(gross_margin) and gross_margin > 50.0:
             archetype_key = "Knowledge_Heavy"
             
    archetype_data = archetypes.get(archetype_key, {})
    print(f"Fundamentalist: Applying Archetype '{archetype_key}'")
    
    # Apply Overrides
    assumptions = apply_archetype_overrides(assumptions, archetype_data)
    
    # Sector Specific Cost Mapping
    if archetype_key == "Float_Heavy":
        assumptions["cogs_percent_sales"] = 0.85 # Fallback MLR
    else:
        gm = row.get("derived_GrossMargin_Pct", 50.0)
        if pd.isna(gm): gm = 50.0
        assumptions["cogs_percent_sales"] = 1.0 - (gm / 100.0)

    # --- Margin Calibration ---
    implied_margin = 1.0 - assumptions["cogs_percent_sales"] - assumptions["sga_percent_sales"]
    actual_ebit = row.get("clean_NormalizedEBIT", 0.0)
    if pd.isna(actual_ebit): actual_ebit = 0.0
    actual_margin = actual_ebit / revenue
    
    margin_gap = implied_margin - actual_margin
    
    if margin_gap > 0.01:
        print(f"⚠️ Fundamentalist: Margin Mismatch detected. Implied {implied_margin:.1%} vs Actual {actual_margin:.1%}. Adjusting...")
        assumptions["cogs_percent_sales"] += margin_gap
        assumptions["calibration_adjustment"] = margin_gap
    # --------------------------
    
    net_debt = row.get("derived_NetDebt", 0.0)
    if pd.isna(net_debt): net_debt = 0.0
        
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
