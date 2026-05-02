from dataclasses import dataclass
import pandas as pd
from typing import List, Dict, Optional, Any

from config.ticker_classification import TickerClassification
from config.known_issues import KnownIssue

@dataclass(frozen=True)
class ValuationProfile:
    growth_rate: float
    terminal_growth: float
    forecast_years: int
    terminal_margin_decay: float
    decay_bull: float = 0.90
    decay_base: float = 0.85
    decay_bear: float = 0.70
    terminal_growth_cap: Optional[float] = 0.04
    bear_growth_haircut: float = -0.50
    bear_margin_compression: float = -0.10
    bear_wacc_adjustment: float = 0.015
    bull_growth_haircut: float = 0.25
    bull_margin_compression: float = 0.10
    bull_wacc_adjustment: float = -0.005
    max_historical_cagr: float = 0.50

@dataclass
class UniverseSnapshot:
    tickers: List[str]
    classifications: Dict[str, TickerClassification]

@dataclass
class CalculationInput:
    df: pd.DataFrame
    classification: TickerClassification
    known_issues: List[KnownIssue]
    valuation_profile: ValuationProfile
    lifecycle_thresholds: Any = None

