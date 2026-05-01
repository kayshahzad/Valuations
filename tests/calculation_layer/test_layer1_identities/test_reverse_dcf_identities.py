# tests/calculation_layer/test_layer1_identities/test_reverse_dcf_identities.py

import pytest
import math
from aletheia.tools.testable import pure_reverse_dcf_math

class TestReverseDCFIdentities:
    
    def test_higher_ev_requires_higher_growth(self, make_calc_input):
        """All else equal, a higher current EV must imply a higher embedded growth rate."""
        low_ev = 1000.0
        high_ev = 2000.0
        
        cagr_low = pure_reverse_dcf_math(
            current_ev=low_ev, base_revenue=100.0, ebit_margin=0.20, wacc=0.10
        )
        
        cagr_high = pure_reverse_dcf_math(
            current_ev=high_ev, base_revenue=100.0, ebit_margin=0.20, wacc=0.10
        )
        
        assert cagr_high > cagr_low, "Higher EV must imply higher growth"

    def test_higher_wacc_requires_higher_growth(self, make_calc_input):
        """All else equal, a higher discount rate means higher growth is needed to justify the same EV."""
        cagr_low_wacc = pure_reverse_dcf_math(
            current_ev=1000.0, base_revenue=100.0, ebit_margin=0.20, wacc=0.08
        )
        
        cagr_high_wacc = pure_reverse_dcf_math(
            current_ev=1000.0, base_revenue=100.0, ebit_margin=0.20, wacc=0.12
        )
        
        assert cagr_high_wacc > cagr_low_wacc, "Higher WACC requires higher growth to maintain EV"
