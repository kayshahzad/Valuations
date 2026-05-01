"""
Earnings Power Value (EPV) Calculator
Implements Section 16.4 of the 16-Section Framework.
"""

def compute_epv(normalized_ebit: float, tax_rate: float, wacc: float) -> float:
    """
    Computes Earnings Power Value (EPV) assuming zero future growth.
    Formula: NOPAT / WACC
    
    Args:
        normalized_ebit: Mid-cycle EBIT ($)
        tax_rate: Expected cash tax rate (decimal, e.g. 0.21)
        wacc: Weighted Average Cost of Capital (decimal, e.g. 0.08)
        
    Returns:
        float: Earnings Power Value in $
    """
    if wacc <= 0:
        raise ValueError("WACC must be strictly positive to compute EPV.")
    
    nopat = normalized_ebit * (1.0 - tax_rate)
    epv = nopat / wacc
    return epv

if __name__ == "__main__":
    # Unit Tests
    print("Running EPV tests...")
    
    # Test 1: LLY-like inputs (from Bug 1.6 definition)
    # compute_epv(25.8e9, 0.18, 0.105) ≈ 201e9
    ebit = 25.8e9
    tax = 0.18
    wacc = 0.105
    epv = compute_epv(ebit, tax, wacc)
    expected = 201.485e9
    assert abs(epv - expected) < 1e7, f"Expected ~201.485B, got {epv/1e9:.3f}B"
    print("✓ Test 1 Passed (LLY-like)")
    
    # Test 2: Zero tax
    assert compute_epv(100, 0.0, 0.10) == 1000.0
    print("✓ Test 2 Passed (Zero tax)")
    
    # Test 3: High WACC
    assert compute_epv(100, 0.20, 0.20) == 400.0
    print("✓ Test 3 Passed (High WACC)")
    
    # Test 4: WACC <= 0 raises
    try:
        compute_epv(100, 0.20, 0.0)
        assert False, "Should raise ValueError"
    except ValueError:
        print("✓ Test 4 Passed (WACC validation)")
        
    print("All EPV tests passed!")
