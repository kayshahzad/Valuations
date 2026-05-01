"""
tests/calculation_layer/test_process_ticker_stateless.py

Ensures the decoupling boundary between Calculation (process_ticker) and 
Storage (InvestmentDatabase) does not silently decay.

It intentionally monkey-patches InvestmentDatabase to raise an exception upon instantiation.
If process_ticker relies on the database, this test will fail, indicating a violation
of the architecture.
"""

import pytest
import os
import tempfile
import pandas as pd
from unittest.mock import patch

from aletheia.data.database import process_ticker, CalculationOutput
from aletheia.data.cleaning_engine import CleaningEngine, CleanedRecord
from aletheia.data.quantitative_screens import QuantitativeScreens

@pytest.fixture
def mock_canonical_dir():
    with tempfile.TemporaryDirectory() as d:
        # Create a dummy parquet file for AAPL
        df = pd.DataFrame([
            {"ticker": "AAPL", "fy": 2023, "standard_tag": "Revenue", "value": 1000.0},
            {"ticker": "AAPL", "fy": 2023, "standard_tag": "NetIncome", "value": 200.0},
        ])
        df.to_parquet(os.path.join(d, "AAPL.parquet"))
        yield d

@patch("aletheia.data.database.InvestmentDatabase.__init__", side_effect=RuntimeError("ILLEGAL DB INSTANTIATION in process_ticker"))
def test_process_ticker_is_stateless(mock_db_init, mock_canonical_dir):
    """
    Asserts that process_ticker can run to completion strictly in memory,
    without ever instantiating or communicating with the database.
    """
    engine = CleaningEngine(canonical_dir=mock_canonical_dir, verbose=False)
    screens = QuantitativeScreens(verbose=False)

    # Note: process_ticker should not require InvestmentDatabase at all
    output = process_ticker(
        ticker="AAPL",
        fiscal_year=2023,
        engine=engine,
        screens=screens
    )

    assert isinstance(output, CalculationOutput)
    # The output might be a validation_failed due to missing mock data,
    # but the key is that it completes WITHOUT hitting the mocked DB exception.
    assert output.ticker == "AAPL"
    assert output.fiscal_year == 2023
    assert not mock_db_init.called, "process_ticker violated decoupling by instantiating InvestmentDatabase"
