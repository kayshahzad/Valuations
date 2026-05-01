# tests/calculation_layer/test_layer1_identities/test_liberti_identities.py

import pytest
import math
from aletheia.tools.testable import pure_compute_justified_multiple

class TestLibertiIdentities:
    """Identities for the Liberti EV/EBITDA formula."""
    
    def test_zero_growth_simplifies(self, make_calc_input):
        """For g=0: EV/EBITDA = NOPAT / (EBITDA × WACC)."""
        result, _ = pure_compute_justified_multiple(
            nopat=158.0, 
            ebitda=200.0, 
            roic=0.20, 
            wacc=0.10, 
            g_terminal=0.0
        )
        expected = 158.0 / (200.0 * 0.10)  # = 7.9
        assert math.isclose(result, expected, rel_tol=1e-9)
    
    def test_roic_equals_wacc_growth_has_no_value(self, make_calc_input):
        """When ROIC = WACC, growth doesn't increase the multiple."""
        no_growth, _ = pure_compute_justified_multiple(
            nopat=100.0, ebitda=120.0, roic=0.10, wacc=0.10, g_terminal=0.0)
        
        with_growth, _ = pure_compute_justified_multiple(
            nopat=100.0, ebitda=120.0, roic=0.10, wacc=0.10, g_terminal=0.03)
        
        # When ROIC = WACC, the penalty in cash conversion (1 - g/ROIC) exactly offsets 
        # the premium in the denominator (WACC - g). The multiple should be identical.
        assert math.isclose(no_growth, with_growth, rel_tol=1e-9)
    
    def test_higher_roic_higher_multiple(self, make_calc_input):
        """Higher ROIC must produce higher justified multiple, all else equal."""
        low_roic, _ = pure_compute_justified_multiple(
            nopat=100.0, ebitda=120.0, roic=0.10, wacc=0.08, g_terminal=0.03)
        
        high_roic, _ = pure_compute_justified_multiple(
            nopat=100.0, ebitda=120.0, roic=0.20, wacc=0.08, g_terminal=0.03)
            
        assert high_roic > low_roic, "Higher ROIC must yield higher multiple"