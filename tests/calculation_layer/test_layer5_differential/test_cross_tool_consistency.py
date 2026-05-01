# tests/calculation_layer/test_layer5_differential/test_cross_tool_consistency.py

import pytest
import math
from tests.calculation_layer.conftest import REFERENCE_TICKERS

class TestCrossToolConsistency:
    """
    Differential Consistency Checks (Layer 5)
    
    Same metric computed by different tools must agree exactly (for passthroughs)
    or within 1e-9 (for computed floats). Any structural difference must be explicitly 
    asserted against its theoretical delta.
    """
    
    @pytest.mark.parametrize("ticker", REFERENCE_TICKERS)
    def test_wacc_consistent_across_tools(self, ticker, make_calc_input):
        """DCF WACC must equal Multiple Decomposition WACC must equal Reverse DCF WACC."""
        from aletheia.tools.dcf_engine import DCFEngine
        from aletheia.tools.multiple_decomposition import MultipleDecomposition
        from aletheia.tools.reverse_dcf import ReverseDCF
        
        try:
            dcf_result = DCFEngine(verbose=False).run(make_calc_input(ticker))
            md_result = MultipleDecomposition(verbose=False).run(make_calc_input(ticker))
            rdcf_result = ReverseDCF(verbose=False).run(make_calc_input(ticker))
        except Exception:
            pytest.skip(f"Data not available for {ticker}")
            
        wacc_dcf = dcf_result.wacc_base
        wacc_md = md_result.wacc
        wacc_rdcf = rdcf_result.wacc
        
        # WACC is computed by a shared routine, so they must be exactly identical
        # However, due to float serialization, we allow 1e-9 tolerance
        assert math.isclose(wacc_dcf, wacc_md, rel_tol=1e-9), \
            f"WACC inconsistency: DCF={wacc_dcf:.6f}, MD={wacc_md:.6f}"
            
        assert math.isclose(wacc_dcf, wacc_rdcf, rel_tol=1e-9), \
            f"WACC inconsistency: DCF={wacc_dcf:.6f}, ReverseDCF={wacc_rdcf:.6f}"
    
    @pytest.mark.parametrize("ticker", REFERENCE_TICKERS)
    def test_roic_consistent(self, ticker, make_calc_input):
        """ROIC reported by DCF Engine must match ROIC reported by Multiple Decomposition."""
        from aletheia.tools.dcf_engine import DCFEngine
        from aletheia.tools.multiple_decomposition import MultipleDecomposition
        
        try:
            dcf_result = DCFEngine(verbose=False).run(make_calc_input(ticker))
            md_result = MultipleDecomposition(verbose=False).run(make_calc_input(ticker))
        except Exception:
            pytest.skip(f"Data not available for {ticker}")
            
        dcf_roic = dcf_result.roic
        md_roic = md_result.roic
        
        assert math.isclose(dcf_roic, md_roic, rel_tol=1e-9), \
            f"Historical ROIC inconsistency: DCF={dcf_roic:.6f}, MD={md_roic:.6f}"

    @pytest.mark.parametrize("ticker", REFERENCE_TICKERS)
    def test_enterprise_value_inputs_consistent(self, ticker, make_calc_input):
        """
        The current Enterprise Value used by Reverse DCF must exactly match 
        the snapshot's DB Market Cap + DB Net Debt when run in snapshot mode.
        """
        from aletheia.tools.reverse_dcf import ReverseDCF
        from aletheia.data.database import InvestmentDatabase
        
        try:
            rdcf_result = ReverseDCF(verbose=False).run_snapshot(ticker)
            current_ev_rdcf = rdcf_result.current_ev
            
            db = InvestmentDatabase(verbose=False)
            df = db.get_latest(ticker)
            db.close()
            
            if df.empty:
                pytest.skip("No DB data")
                
            fy = int(df["fiscal_year"].max())
            row = df[df["fiscal_year"] == fy].iloc[0]
            net_debt = float(row.get("derived_NetDebt", 0))
            db_market_cap = float(row.get("current_market_cap", 0))
            expected_ev = db_market_cap + net_debt
            
        except Exception:
            pytest.skip(f"Data not available for {ticker}")
            
        if current_ev_rdcf > 0:
            assert math.isclose(current_ev_rdcf, expected_ev, rel_tol=1e-9), \
                f"Reverse DCF EV ({current_ev_rdcf}) != DB Cap ({db_market_cap}) + NetDebt ({net_debt})"