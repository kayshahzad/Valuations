
# tests/calculation_layer/test_layer4_reference/test_msft_reference.py

import pytest
from tests.calculation_layer.reference_implementations import msft_reference

# Tolerances
IV_TOLERANCE = 0.05      # 5% tolerance on IV
WACC_TOLERANCE = 0.005   # 50bps tolerance on WACC
EPV_TOLERANCE = 0.05     # 5% tolerance on EPV
MULTIPLE_TOLERANCE = 0.10  # 10% tolerance on Liberti multiple

class TestMSFTReference:
    
    def test_wacc_matches_reference(self, make_calc_input):
        from aletheia.tools.dcf_engine import DCFEngine
        
        reference = msft_reference.compute_dcf_reference()
        actual = DCFEngine(verbose=False).run(make_calc_input("MSFT"), fiscal_year=2025)
        
        delta = abs(actual.wacc - reference["wacc"])
        assert delta < WACC_TOLERANCE, \
            f"MSFT WACC: ref={reference['wacc']:.2%}, actual={actual.wacc:.2%}, delta={delta:.3%}"
    
    def test_base_iv_within_tolerance(self, make_calc_input):
        from aletheia.tools.dcf_engine import DCFEngine
        
        reference = msft_reference.compute_dcf_reference()
        actual = DCFEngine(verbose=False).run(make_calc_input("MSFT"), fiscal_year=2025)
        
        delta_pct = abs(actual.base.enterprise_value - reference["enterprise_value"]) / \
                    reference["enterprise_value"]
        assert delta_pct < IV_TOLERANCE, \
            f"MSFT EV: ref=${reference['enterprise_value']/1e9:.0f}B, " \
            f"actual=${actual.base.enterprise_value/1e9:.0f}B, " \
            f"delta={delta_pct:.1%}"
    
    def test_epv_matches_reference(self, make_calc_input):
        from aletheia.tools.epv import compute_epv
        
        reference = msft_reference.compute_epv_reference()
        # Read inputs from DB to match what tool uses
        # ...
        actual = compute_epv(
            normalized_ebit=msft_reference.INPUTS["ebit"] * 1e6,
            tax_rate=msft_reference.INPUTS["tax_rate"],
            wacc=msft_reference.compute_wacc_reference(),
        )
        
        delta_pct = abs(actual - reference["epv_total"]) / reference["epv_total"]
        assert delta_pct < EPV_TOLERANCE
    
    def test_liberti_multiple_matches_reference(self, make_calc_input):
        # ...