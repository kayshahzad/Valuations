"""
validation_rules.py

Defines the Data Validation Framework (Layers 1-3).
- Layer 1: Presence checks
- Layer 2: Field-level validation (signs, bounds)
- Layer 3: Mathematical identity checks (e.g., Gross Profit)
"""

REQUIRED_FIELDS = {
    "default": [
        "Revenue", "NetIncome", 
        "TotalAssets", "TotalEquity",
        "OperatingCF", "CapEx"
    ],
    "bank": [
        "Revenue", "NetIncome", "TotalAssets", "TotalEquity",
        "OperatingCF"
    ],
    "utility": [
        "Revenue", "NetIncome",
        "TotalAssets", "TotalEquity",
        "OperatingCF"
    ]
}

def check_layer1_presence(record: dict, industry: str) -> list:
    """Returns a list of missing required fields."""
    required = REQUIRED_FIELDS.get(industry, REQUIRED_FIELDS["default"])
    missing = []
    for field in required:
        if record.get(field) is None:
            missing.append(field)
    return missing

def check_layer2_field_level(record: dict, industry: str) -> list:
    """Returns a list of field-level logic violations."""
    violations = []
    
    # Revenue should generally be positive
    if record.get("Revenue") is not None and record["Revenue"] < 0:
        violations.append("Revenue is negative")
        
    # Assets should be positive
    if record.get("TotalAssets") is not None and record["TotalAssets"] < 0:
        violations.append("TotalAssets is negative")
        
    return violations

def check_layer3_identities(record: dict, industry: str) -> list:
    """Returns a list of identity violations (e.g. Accounting Equation)."""
    violations = []
    tolerance = 0.05 # 5% tolerance to account for obscure mezzanine equity
    
    assets = record.get("TotalAssets")
    liabilities = record.get("TotalLiabilities")
    equity = record.get("TotalEquity")
    # Mezzanine / temporary equity sits between liabilities and permanent
    # equity (ASC 480-10-S99) and belongs on the right side of A=L+E.
    # Two variants: redeemable NCI (subsidiary minority) and parent-
    # attributable redeemable convertible preferred. CELH carries PepsiCo's
    # 2022 Series A convertible preferred under the latter ($824M FY2022-
    # 2024, growing to $1.76B at FY2025), in neither TotalLiabilities nor
    # parent-only TotalEquity.
    mezzanine = (record.get("RedeemableNoncontrollingInterest") or 0.0) \
        + (record.get("TemporaryEquityCarryingAmount") or 0.0)

    # 1. Accounting Equation: Assets = Liabilities + Equity + Mezzanine
    if assets is not None and liabilities is not None and equity is not None:
        computed_assets = liabilities + equity + mezzanine
        if abs(assets - computed_assets) / max(abs(assets), 1) > tolerance:
            violations.append(f"Accounting Equation failed: Assets {assets} != L+E+M {computed_assets}")
            
    # Add other identities here as needed...
            
    return violations
