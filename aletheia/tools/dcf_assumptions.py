"""
aletheia/tools/dcf_assumptions.py

Deterministic logic to construct initial DCF assumptions from historical data.
Extracted from fundamentalist.py to ensure testability and isolation.
"""

import pandas as pd
import json
from pathlib import Path
from typing import Dict, Any

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

def build_base_assumptions(row: pd.Series, clean_data: Dict[str, Any], dcf_config: Dict[str, Any], wacc: float, archetypes: Dict[str, Any]) -> tuple[Dict[str, float], float]:
    """
    Constructs base assumptions for Pro Forma projection.
    Applies margin calibration based on archetype rules.
    
    Returns:
        (assumptions_dict, net_debt)
    """
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
    sector = row.get("sector", "General") if "sector" in row else "General"
    archetype_key = "Asset_Heavy" # Default
    
    if sector in ["Financial Services", "Insurance"]:
        archetype_key = "Float_Heavy"
    else:
        # Determine Knowledge vs Asset Heavy
        gross_margin = row.get("derived_GrossMargin_Pct", 0.0)
        if not pd.isna(gross_margin) and gross_margin > 50.0:
             archetype_key = "Knowledge_Heavy"
             
    archetype_data = archetypes.get(archetype_key, {})
    
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
        assumptions["cogs_percent_sales"] += margin_gap
        assumptions["calibration_adjustment"] = margin_gap
    # --------------------------
    
    net_debt = row.get("derived_NetDebt", 0.0)
    if pd.isna(net_debt): net_debt = 0.0
    
    return assumptions, net_debt
