# test_layer5_differential/test_lifecycle_routing.py

import pytest

class TestLifecycleRouting:
    """Verify lifecycle stage drives appropriate calculation behavior."""
    
    @pytest.mark.skip(reason="Not implemented yet")
    def test_pharma_does_not_get_cyclical_haircut(self, make_calc_input):
        """For LLY (pharma), industry classification must say non-cyclical."""
        # ...
    
    @pytest.mark.skip(reason="Not implemented yet")
    def test_industrial_can_get_cyclical_haircut(self, make_calc_input):
        """For CAT (industrial), industry classification allows cyclical."""
        # ...
    
    @pytest.mark.skip(reason="Not implemented yet")
    def test_bank_uses_roe_in_conviction(self, make_calc_input):
        """For JPM, P2 reasoning uses ROE not ROIC-WACC."""
        from aletheia.tools.conviction_scorer import ConvictionScorer
        result = ConvictionScorer().score_from_state("JPM", synthetic_jpm_state())
        
        roic_mention = any("ROIC" in r for r in result.p2_health.reasons)
        roe_mention = any("ROE" in r for r in result.p2_health.reasons)
        
        assert roe_mention and not roic_mention, \
            "Bank should use ROE in P2, not ROIC"