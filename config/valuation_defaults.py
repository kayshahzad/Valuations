from dataclasses import dataclass
from typing import Dict

@dataclass(frozen=True)
class LifecycleDefaults:
    growth_rate: float
    terminal_growth: float
    forecast_years: int
    terminal_margin_decay: float

LIFECYCLE_PROFILES: Dict[str, LifecycleDefaults] = {
    "secular_hyper_growth":   LifecycleDefaults(0.35, 0.05, 15, 0.10),
    "hyper_growth":           LifecycleDefaults(0.25, 0.05, 15, 0.10),
    "high_growth_compounder": LifecycleDefaults(0.18, 0.04, 10, 0.15),
    "growth_compounder":      LifecycleDefaults(0.135, 0.035, 10, 0.20),
    "mature":                 LifecycleDefaults(0.05, 0.025, 10, 0.30),
    "cyclical_industrial":    LifecycleDefaults(0.04, 0.025, 10, 0.50),
}

@dataclass(frozen=True)
class ScenarioAdjustments:
    growth_haircut: float
    margin_compression: float
    wacc_adjustment: float

BEAR_ADJUSTMENTS = ScenarioAdjustments(-0.50, -0.10, +0.015)
BULL_ADJUSTMENTS = ScenarioAdjustments(+0.25, +0.10, -0.005)
