import pytest
from aletheia.tools.dcf_engine import DCFEngine

class TestUniverseValuation:
    """Regression suite for the Phase 6 architectural arc. Ensures specific universe-wide behaviors hold."""

    def test_unh_bypass_ddm_required(self, make_calc_input):
        """UNH must cleanly raise a NotImplementedError due to ddm_required."""
        engine = DCFEngine(verbose=False)
        try:
            calc_input = make_calc_input("UNH")
            engine.run(calc_input)
            pytest.fail("UNH should have raised NotImplementedError")
        except NotImplementedError as e:
            assert "requires specialized model (ddm_required)" in str(e) or "requires DDM" in str(e)

    def test_nee_jpm_bypass_routing_required(self, make_calc_input):
        """NEE and JPM must cleanly raise a NotImplementedError due to routing_required."""
        engine = DCFEngine(verbose=False)
        for ticker in ["NEE", "JPM", "BRK-B"]:
            try:
                calc_input = make_calc_input(ticker)
                engine.run(calc_input)
                pytest.fail(f"{ticker} should have raised NotImplementedError")
            except NotImplementedError as e:
                assert "requires specialized model (routing_required)" in str(e)

    def test_tsla_gordon_to_liberti_fallback(self, make_calc_input):
        """TSLA's bear TV must not be negative; it must fall back to Gordon TV."""
        engine = DCFEngine(verbose=False)
        tsla_result = engine.run(make_calc_input("TSLA"))
        base_tv = tsla_result.base.terminal.tv_used
        bear_tv = tsla_result.bear.terminal.tv_used
        
        assert base_tv > 0, "TSLA base TV should be positive"
        assert bear_tv > 0, "TSLA bear TV should not be negative (Gordon TV fallback must have triggered)"
        assert tsla_result.intrinsic_per_share(tsla_result.base.enterprise_value, tsla_result.net_debt) > 0

    def test_nvda_secular_hyper_growth_horizon(self, make_calc_input):
        """NVDA must project exactly 15 explicit forecast years."""
        engine = DCFEngine(verbose=False)
        nvda_result = engine.run(make_calc_input("NVDA"))
        
        forecast_years = len(nvda_result.base.projections)
        assert forecast_years == 15, f"NVDA should have 15y forecast, got {forecast_years}"

    def test_cat_cyclical_haircut(self, make_calc_input):
        """CAT must run the cyclical haircut logic without crashing and produce a valid EV."""
        engine = DCFEngine(verbose=False)
        cat_result = engine.run(make_calc_input("CAT"))
        
        assert cat_result.base.enterprise_value > 0

    def test_smci_flag_low_confidence(self, make_calc_input):
        """SMCI must properly attach the earnings_quality warning flag from KNOWN_ISSUES to the result."""
        engine = DCFEngine(verbose=False)
        smci_result = engine.run(make_calc_input("SMCI"))
        
        assert "Earnings quality concerns from SEC investigation" in smci_result.base.metadata.get("data_quality_warnings", "")
        assert any("Earnings quality concerns" in warning for warning in smci_result.warnings)
