# tests/calculation_layer/test_layer3_data_coverage/test_data_coverage.py

import pytest
import json
from pathlib import Path
from aletheia.tools.dcf_engine import DCFEngine
from aletheia.tools.multiple_decomposition import MultipleDecomposition
from aletheia.tools.equity_bridge import EquityBridge
from aletheia.data.database import InvestmentDatabase
from tests.calculation_layer.conftest import UNIVERSE

class TestDataCoverage:
    """
    Suite 2: Data Coverage & Pipeline Audit
    Runs against the live UNIVERSE database.
    This suite is allowed to fail/skip. Its primary purpose is to generate 
    a telemetry report of missing XBRL tags or broken coverage across the universe.
    
    Dashboard Owner: Data Engineering Team (SLA: Weekly Review)
    """

    @pytest.fixture(scope="class")
    def test_tickers(self):
        # To avoid running hundreds in a unit test suite, sample the top 20
        return UNIVERSE[:20]

    def test_dcf_coverage(self, test_tickers, make_calc_input):
        """Audit which tickers have sufficient data for DCF."""
        engine = DCFEngine(verbose=False)
        success_count = 0
        failures = []
        
        for ticker in test_tickers:
            try:
                engine.run(make_calc_input(ticker))
                success_count += 1
            except Exception as e:
                failures.append({"ticker": ticker, "error": str(e)})
        
        coverage_pct = success_count / len(test_tickers)
        
        # We don't assert it must be 100%, we just log the gaps for the dashboard.
        # But we do want to fail the test if coverage drops below a critical threshold (e.g. 50%).
        report_path = Path("scratch/dcf_coverage_report.json")
        report_path.parent.mkdir(exist_ok=True)
        with open(report_path, "w") as f:
            json.dump({"coverage": coverage_pct, "failures": failures}, f, indent=2)
            
        assert coverage_pct > 0.5, \
            f"DCF coverage fell below 50% ({coverage_pct:.0%}). Check ingestion pipeline."

    def test_multiple_decomposition_coverage(self, test_tickers, make_calc_input):
        """Audit which tickers have sufficient data for Multiple Decomposition."""
        md = MultipleDecomposition(verbose=False)
        success_count = 0
        failures = []
        
        for ticker in test_tickers:
            try:
                md.run(make_calc_input(ticker))
                success_count += 1
            except Exception as e:
                failures.append({"ticker": ticker, "error": str(e)})
                
        coverage_pct = success_count / len(test_tickers)
        
        report_path = Path("scratch/md_coverage_report.json")
        report_path.parent.mkdir(exist_ok=True)
        with open(report_path, "w") as f:
            json.dump({"coverage": coverage_pct, "failures": failures}, f, indent=2)
            
        assert coverage_pct > 0.5, \
            f"Multiple Decomposition coverage fell below 50% ({coverage_pct:.0%})."

    def test_equity_bridge_coverage(self, test_tickers, make_calc_input):
        """Audit which tickers have sufficient data for Equity Bridge construction."""
        bridge = EquityBridge(verbose=False)
        success_count = 0
        failures = []
        
        for ticker in test_tickers:
            try:
                bridge.build(make_calc_input(ticker), enterprise_value=1000.0) # Synthetic EV just to test bridge data
                success_count += 1
            except Exception as e:
                failures.append({"ticker": ticker, "error": str(e)})
                
        coverage_pct = success_count / len(test_tickers)
        
        report_path = Path("scratch/bridge_coverage_report.json")
        report_path.parent.mkdir(exist_ok=True)
        with open(report_path, "w") as f:
            json.dump({"coverage": coverage_pct, "failures": failures}, f, indent=2)
            
        assert coverage_pct > 0.5, \
            f"Equity Bridge coverage fell below 50% ({coverage_pct:.0%})."
