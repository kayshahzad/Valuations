
import unittest
from aletheia.utils.dcf_synthesis import build_dcf_model_from_proforma

class TestDCFMapping(unittest.TestCase):
    def setUp(self):
        # Mock ProForma output based on typical trace content
        self.proforma_output = {
            "enterprise_value": 1000.0,
            "equity_value": 800.0,
            "projections": [{"year": 1, "fcff": 100}, {"year": 2, "fcff": 110}, {"year": 3, "fcff": 120}, {"year": 4, "fcff": 130}, {"year": 5, "fcff": 140}],
            "diagnostics": {
                "assumptions_used": {
                    "wacc": 0.09,
                    "tax_rate": 0.21,
                    "terminal_growth_rate": 0.03
                }
            }
        }

    def test_basic_mapping(self):
        result = build_dcf_model_from_proforma(self.proforma_output)
        
        self.assertEqual(result["intrinsic_value"], 800.0)
        self.assertEqual(result["assumptions_used"]["wacc"], 0.09)
        self.assertEqual(len(result["projections"]), 5)
        self.assertIsNone(result["upside_percent"]) # No market price implies no upside calc by default

    def test_per_share_mapping(self):
        result = build_dcf_model_from_proforma(
            self.proforma_output,
            shares_diluted=100.0, 
            intrinsic_is_per_share=True
        )
        # Intrinsic = 800 / 100 = 8.0
        self.assertEqual(result["intrinsic_value"], 8.0)
        
    def test_upside_calculation(self):
        # Case 1: Total vs Total (if shares not provided, but market price is given - behavior undefined in my impl currently? 
        # Wait, my implementation requires shares or per-share mode for upside if we assume market_price is per share.
        # Let's test the "shares provided" path which is standard.)
        
        # Market Price = 5.0, Intrinsic per share = 8.0 -> Upside = (8-5)/5 = 0.60 (60%)
        result = build_dcf_model_from_proforma(
            self.proforma_output,
            shares_diluted=100.0,
            market_price=5.0,
            intrinsic_is_per_share=True
        )
        self.assertEqual(result["upside_percent"], 0.60)
        
    def test_validation_gates(self):
        # Missing keys
        bad_output = {"foo": "bar"}
        with self.assertRaises(ValueError):
            build_dcf_model_from_proforma(bad_output)
            
        # Invalid shares
        with self.assertRaises(ValueError):
             build_dcf_model_from_proforma(self.proforma_output, shares_diluted=0, intrinsic_is_per_share=True)
             
        # Invalid market price
        with self.assertRaises(ValueError):
            build_dcf_model_from_proforma(self.proforma_output, market_price=-10.0)

if __name__ == '__main__':
    unittest.main()
