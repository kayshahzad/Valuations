# tests/calculation_layer/test_layer1_identities/test_equity_bridge_identities.py

import pytest
import math
from aletheia.tools.testable import pure_equity_bridge_math

class TestEquityBridgeIdentities:
    
    def test_sum_of_items_equals_equity_minus_ev(self, make_calc_input):
        """Algebraic identity: sum of bridge items = equity - EV."""
        ev = 1000.0
        equity_value, cash_analysis, items = pure_equity_bridge_math(
            enterprise_value=ev,
            revenue=500.0,
            gross_cash=100.0,
            short_term_investments=0.0,
            long_term_debt=200.0,
            short_term_debt=50.0,
            minority_interest=0.0,
            pension_deficit=0.0,
            lease_debt=0.0,
            jva_income=0.0,
        )
        sum_items = sum(item.value for item in items)
        
        # Identity: Equity = EV + Sum(Items)
        # Note: EV is value of operating assets. Cash is added (+), Debt is subtracted (-).
        assert math.isclose(equity_value, ev + sum_items, rel_tol=1e-9), \
            f"Equity ({equity_value}) != EV ({ev}) + Items ({sum_items})"
    
    def test_negative_equity_not_possible(self, make_calc_input):
        """If debt massively exceeds EV and cash, equity might be negative algebraically. 
           In practice, intrinsic value floors at 0. But the pure bridge algebra allows negative."""
        ev = 100.0
        equity_value, _, _ = pure_equity_bridge_math(
            enterprise_value=ev,
            revenue=500.0,
            gross_cash=10.0,
            short_term_investments=0.0,
            long_term_debt=1000.0,  # massive debt
            short_term_debt=0.0,
            minority_interest=0.0,
            pension_deficit=0.0,
            lease_debt=0.0,
            jva_income=0.0,
        )
        # The algebra just adds EV and items.
        assert equity_value < 0, "Pure algebraic bridge allows negative equity"
    
    def test_cash_haircuts_strictly_reduce_cash(self, make_calc_input):
        """Net cash after haircuts must be ≤ gross cash."""
        gross = 100.0
        _, cash_analysis, _ = pure_equity_bridge_math(
            enterprise_value=1000.0,
            revenue=500.0, # 2% WC haircut = 10.0
            gross_cash=gross,
            short_term_investments=0.0,
            long_term_debt=0.0,
            short_term_debt=0.0,
            minority_interest=0.0,
            pension_deficit=0.0,
            lease_debt=0.0,
            jva_income=0.0,
            wc_pct=0.02,
            overseas_cash_fraction=0.6,
            repatriation_tax=0.15 # 0.6 * 0.15 = 0.09 * 100 = 9.0
        )
        
        assert cash_analysis.net_accessible_cash <= gross, "Net cash must be <= gross cash"
        
        # Expected: 100 - 10 (wc) - 9 (repatriation) = 81
        assert math.isclose(cash_analysis.net_accessible_cash, 81.0, rel_tol=1e-9)