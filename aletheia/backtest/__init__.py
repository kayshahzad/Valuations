"""
aletheia/backtest

Minimal backtest harness for evaluating predictive edge of the calc-layer
signals (DCF intrinsic value, conviction score, ROIC-WACC spread) at
historical points in time, against a price-momentum baseline.

The point-in-time loader filters SEC companyfacts JSON by `filed` date so
the engine only sees data that was publicly available at the as-of date —
this is the central correctness property; without it the backtest produces
fake edge from lookahead bias.
"""

from aletheia.backtest.signal_generator import BacktestSignal, generate_signal_at_date
from aletheia.backtest.point_in_time import load_point_in_time, PointInTimeLoadResult
from aletheia.backtest.outcome_tracker import get_price_at, compute_forward_return
from aletheia.backtest.harness import run_backtest
from aletheia.backtest.calibration import (
    signal_calibration_table,
    fundamental_vs_momentum_comparison,
    staleness_calibration,
    calibrate_signal,
    multi_signal_comparison,
    signal_correlation_matrix,
)

__all__ = [
    "BacktestSignal",
    "generate_signal_at_date",
    "load_point_in_time",
    "PointInTimeLoadResult",
    "get_price_at",
    "compute_forward_return",
    "run_backtest",
    "signal_calibration_table",
    "fundamental_vs_momentum_comparison",
    "staleness_calibration",
    "calibrate_signal",
    "multi_signal_comparison",
    "signal_correlation_matrix",
]
