
from typing import Dict, Any, Optional

def build_dcf_model_from_proforma(
    proforma_output: Dict[str, Any],
    shares_diluted: Optional[float] = None,
    market_price: Optional[float] = None,
    intrinsic_is_per_share: bool = False
) -> Dict[str, Any]:
    """
    Deterministic mapping function that converts a ProForma output dict
    into a dcf_model dict matching the report schema.
    
    Args:
        proforma_output: The raw dictionary returned by ProFormaEngine.generate_forecast()
        shares_diluted: Optional share count for per-share intrinsic value.
        market_price: Optional market price for upside calculation.
        intrinsic_is_per_share: If True, intrinsic_value will be calculated per share.
                                Requires shares_diluted to be > 0.
                                
    Returns:
        A dictionary strictly matching the schema expected by ReportGenerator.
        Keys:
            - intrinsic_value (float or None)
            - upside_percent (float or None) 
            - assumptions_used (dict)
            - projections (list)
            - enterprise_value (float)
            - equity_value (float)
            - implied_growth (str/float, optional)
            
    Raises:
        ValueError: If essential keys are missing from proforma_output.
        ValueError: If intrinsic_is_per_share=True but shares_diluted is invalid.
        ValueError: If market_price is provided but <= 0.
    """
    
    # 1. Hard Validation of Input
    required_keys = ["enterprise_value", "equity_value", "projections", "diagnostics"]
    missing = [k for k in required_keys if k not in proforma_output]
    if missing:
        raise ValueError(f"ProForma output missing required keys: {missing}")
        
    equity_value = float(proforma_output["equity_value"])
    enterprise_value = float(proforma_output["enterprise_value"])
    projections = proforma_output["projections"]
    diagnostics = proforma_output.get("diagnostics", {})
    assumptions = diagnostics.get("assumptions_used", {})
    
    # 2. Intrinsic Value Calculation
    intrinsic_value = equity_value
    
    if intrinsic_is_per_share:
        if shares_diluted is None or shares_diluted <= 0:
            raise ValueError(f"intrinsic_is_per_share=True requires shares_diluted > 0. Got: {shares_diluted}")
        intrinsic_value = equity_value / float(shares_diluted)
    
    # 3. Upside Calculation
    upside_percent = None
    if market_price is not None:
        if market_price <= 0:
             raise ValueError(f"market_price must be > 0. Got: {market_price}")
        
        # Upside needs compare apple-to-apples (per share vs per share OR total vs total)
        # Assuming market_price is per share.
        
        if intrinsic_is_per_share:
             # Compare intrinsic per share to market price
             upside_percent = (intrinsic_value - market_price) / market_price
        elif shares_diluted and shares_diluted > 0:
             # Convert intrinsic total to per share for comparison
             intrinsic_per_share = intrinsic_value / shares_diluted
             upside_percent = (intrinsic_per_share - market_price) / market_price
        else:
             # Cannot compute upside if we have total equity value vs share price without share count
             # Unless market_price is total market cap? Usually 'market_price' implies share price.
             # If market_price is implicitly total cap, we could do (intrinsic - market) / market
             # But let's stay safe and require shares if we are comparing to share price.
             # If the user passes Total Market Cap as market_price, they should probably ensure consistency.
             # For this function, let's assume market_price is share price if shares provided, otherwise undefined behavior if mixing types.
             pass

    # 4. Construct Output
    return {
        "intrinsic_value": intrinsic_value,
        "upside_percent": upside_percent,
        "assumptions_used": assumptions,
        "projections": projections,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "implied_growth": "N/A" # or calculate if needed
    }
