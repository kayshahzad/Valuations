# tests/calculation_layer/test_layer1_identities/test_epv_identities.py

import pytest
import math
from aletheia.tools.epv import compute_epv

class TestEPVIdentities:
    """Algebraic identities for Earnings Power Value."""

    def test_epv_equals_nopat_over_wacc(self, make_calc_input):
        """EPV is mathematically identical to NOPAT / WACC."""
        ebit = 100.0
        tax = 0.20
        wacc = 0.10
        
        epv = compute_epv(ebit, tax, wacc)
        expected_nopat = ebit * (1 - tax)
        expected_epv = expected_nopat / wacc
        
        assert math.isclose(epv, expected_epv, rel_tol=1e-9)

    def test_higher_wacc_lower_epv(self, make_calc_input):
        """All else equal, a higher discount rate implies lower EPV."""
        epv_low_wacc = compute_epv(100.0, 0.20, 0.08)
        epv_high_wacc = compute_epv(100.0, 0.20, 0.12)
        assert epv_low_wacc > epv_high_wacc

    def test_zero_tax_epv_equals_ebit_over_wacc(self, make_calc_input):
        """If tax is 0, EPV is simply EBIT / WACC."""
        ebit = 100.0
        wacc = 0.10
        epv = compute_epv(ebit, 0.0, wacc)
        assert math.isclose(epv, ebit / wacc, rel_tol=1e-9)
