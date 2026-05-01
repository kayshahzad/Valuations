# test_layer5_differential/test_input_sensitivity.py

class TestInputSensitivity:
    """Outputs must respond correctly to input changes."""
    
    def test_higher_revenue_increases_iv(self, make_calc_input):
        """For same EBIT margin, higher revenue → higher IV."""
        pass
    
    def test_lower_wacc_increases_iv(self, make_calc_input):
        """All else equal, lower WACC produces higher EV."""
        # ...
    
    def test_cyclicality_reduces_iv(self, make_calc_input):
        """When lifecycle='cyclical_industrial', IV must be materially lower.
        
        This is the regression test for Bug 1.8 self-canceling.
        """
        from aletheia.tools.dcf_engine import DCFEngine
        engine = DCFEngine(verbose=False)
        
        from dataclasses import replace
        # Test 1: No haircut (mature)
        calc_off = make_calc_input("CAT")
        calc_off.classification = replace(calc_off.classification, lifecycle="mature")
        result_off = engine.run(calc_off)
        
        # Test 2: With haircut (cyclical_industrial)
        calc_on = make_calc_input("CAT")
        calc_on.classification = replace(calc_on.classification, lifecycle="cyclical_industrial")
        result_on = engine.run(calc_on)
        
        # Must differ by at least 5%
        delta_pct = abs(result_on.base.enterprise_value - result_off.base.enterprise_value) / \
                    result_off.base.enterprise_value
        
        assert delta_pct > 0.05, \
            f"Cyclical haircut should change IV by >5%; got {delta_pct:.1%} (self-canceling bug)"
        
        # Haircut should LOWER IV
        assert result_on.base.enterprise_value < result_off.base.enterprise_value
    
    def test_higher_sbc_lowers_p5(self, make_calc_input):
        """Higher SBC % FCF reduces Pillar 5."""
        pass
    
    def test_stronger_roic_wacc_spread_higher_p2(self, make_calc_input):
        """Wider ROIC-WACC spread → higher P2 score."""
        # ...