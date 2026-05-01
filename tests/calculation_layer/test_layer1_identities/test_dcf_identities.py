# tests/calculation_layer/test_layer1_identities/test_dcf_identities.py

import pytest
import math
from aletheia.tools.dcf_engine import DCFEngine, ScenarioAssumptions
from aletheia.tools.testable import pure_compute_projection

class TestDCFIdentities:
    """Mathematical identities the DCF must obey for any inputs."""

    @pytest.fixture
    def mock_db_payload(self):
        # A minimal realistic payload for Approach B tests
        return {
            "base_revenue": 1000.0,
            "base_roic": 0.15,
            "base_da": 50.0,
            "base_capex": 50.0,
            "base_nwc": 0.0,
            "latest_fy": 2023,
            "forecast_years": 10
        }

    # ── Approach B: Minimal Mutation Fixtures ─────────────────────────────────

    def test_zero_growth_equals_perpetuity_value(self, mock_db_payload):
        """For zero growth, EV converges to NOPAT / WACC (Gordon stable)."""
        assumptions = ScenarioAssumptions(
            name="base",
            revenue_cagr_y1_5=0.0,
            revenue_cagr_y6_10=0.0,
            ebit_margin_current=0.20,
            ebit_margin_terminal=0.20,
            tax_rate=0.21,
            wacc=0.10,
            terminal_growth=0.0,
            base_roic=0.15,
            revenue_growth_rates=[0.0]*10,
            capex_pct_revenue=0.05,
            da_pct_revenue=0.05,
            nwc_pct_revenue=0.0
        )
        
        _, _, enterprise_value = pure_compute_projection(
            assumptions=assumptions,
            **mock_db_payload
        )
        
        # NOPAT = 1000 * 0.20 * (1 - 0.21) = 158.0
        # D&A (50) offsets CapEx (50). NWC change is 0. FCFF = NOPAT = 158.0
        # PV of constant perpetuity = FCFF / WACC = 158.0 / 0.10 = 1580.0
        expected_ev = 158.0 / 0.10
        
        # Allow 5% tolerance because the explicit period is discounted 
        # using half-year convention while the simple perpetuity doesn't.
        assert abs(enterprise_value - expected_ev) / expected_ev < 0.05, \
            f"Zero-growth EV should equal NOPAT/WACC; got {enterprise_value}"

    def test_lower_wacc_increases_value(self, mock_db_payload):
        """All else equal, lower WACC produces higher EV."""
        def get_ev(wacc_rate):
            assumptions = ScenarioAssumptions(
                name="base",
                revenue_cagr_y1_5=0.05,
                revenue_cagr_y6_10=0.05,
                ebit_margin_current=0.20,
                ebit_margin_terminal=0.20,
                tax_rate=0.21,
                wacc=wacc_rate,
                terminal_growth=0.02,
                base_roic=0.15,
                revenue_growth_rates=[0.05]*10,
                capex_pct_revenue=0.05,
                da_pct_revenue=0.05,
                nwc_pct_revenue=0.0
            )
            _, _, ev = pure_compute_projection(assumptions=assumptions, **mock_db_payload)
            return ev

        ev_high_wacc = get_ev(0.12)
        ev_low_wacc = get_ev(0.08)
        assert ev_low_wacc > ev_high_wacc

    def test_negative_growth_does_not_crash(self, mock_db_payload):
        """For declining companies, growth can be negative."""
        assumptions = ScenarioAssumptions(
            name="base",
            revenue_cagr_y1_5=-0.05,
            revenue_cagr_y6_10=-0.10,
            ebit_margin_current=0.20,
            ebit_margin_terminal=0.15,
            tax_rate=0.21,
            wacc=0.10,
            terminal_growth=-0.02,
            base_roic=0.10,
            revenue_growth_rates=[-0.05]*5 + [-0.10]*5,
            capex_pct_revenue=0.05,
            da_pct_revenue=0.05,
            nwc_pct_revenue=0.0
        )
        _, _, enterprise_value = pure_compute_projection(assumptions=assumptions, **mock_db_payload)
        assert enterprise_value > 0

    # ── Approach A: Emergent Scenario Comparisons ────────────────────────────

    def test_higher_growth_increases_value_for_positive_spread(self, make_calc_input):
        """When ROIC > WACC, higher growth must produce higher EV."""
        # AAPL is highly profitable (ROIC > WACC)
        engine = DCFEngine(verbose=False)
        try:
            result = engine.run(make_calc_input("AAPL"))
            if not result.bull or not result.base:
                pytest.skip("Could not generate valid scenarios for AAPL")
            
            assert result.bull.enterprise_value > result.base.enterprise_value, \
                "Bull scenario (higher growth) must produce higher EV than Base"
            assert result.base.enterprise_value > result.bear.enterprise_value, \
                "Base scenario must produce higher EV than Bear (lower growth)"
        except ValueError:
            pytest.skip("Live DB lacks AAPL data, skipping emergent test")

    def test_ev_decomposition_sums_to_total(self, make_calc_input):
        """sum_pv_explicit + pv_terminal must equal enterprise_value."""
        engine = DCFEngine(verbose=False)
        try:
            result = engine.run(make_calc_input("AAPL"))
            if not result.base:
                pytest.skip("No base scenario")
            
            base = result.base
            sum_pv_explicit = sum(proj.pv_fcff for proj in base.projections)
            pv_terminal = base.terminal.pv_tv
            expected_ev = sum_pv_explicit + pv_terminal
            
            assert math.isclose(base.enterprise_value, expected_ev, rel_tol=1e-9), \
                "Enterprise value must be exact sum of explicit PV and Terminal PV"
        except ValueError:
            pytest.skip("Live DB lacks AAPL data")