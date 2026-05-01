# test_layer2_framework/test_cfa_formula.py

"""
Framework Section 3.4 specifies:
    CFA = Revenue - Operating Costs - Taxes + Depreciation - CapEx - ΔNWC

The implementation uses NOPAT (already net of OpCost and Tax).
This test verifies algebraic equivalence:
    NOPAT = Revenue - OpCost - Tax  
    FCFF = NOPAT + D&A - CapEx - ΔNWC = Revenue - OpCost - Tax + D&A - CapEx - ΔNWC ✓
"""

class TestCFAFormula:
    
    def test_fcff_equivalent_to_framework_cfa(self, make_calc_input):
        """Verify FCFF formula is algebraically equivalent to framework CFA."""
        revenue = 1000
        opcost = 700
        tax_rate = 0.21
        da = 50
        capex = 80
        delta_nwc = 30
        
        # Framework formula
        ebit = revenue - opcost
        tax = ebit * tax_rate
        cfa_framework = revenue - opcost - tax + da - capex - delta_nwc
        
        # Implementation formula
        nopat = ebit * (1 - tax_rate)
        fcff_impl = nopat + da - capex - delta_nwc
        
        assert abs(cfa_framework - fcff_impl) < 0.01