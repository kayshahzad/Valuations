from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class LifecycleDefaults:
    growth_rate: float
    terminal_growth: float
    forecast_years: int
    terminal_margin_decay: float


# Lifecycle-specific DCF projection defaults. The growth_compounder bucket
# was originally a single profile applied to MSFT/AAPL/GOOGL/COST/LLY/WMT;
# v3 calibration splits it into software / consumer / pharma sub-profiles
# because their long-run economics differ materially:
#   - Software/cloud: secular tailwind justifies above-GDP terminal growth;
#     services-mix expansion offsets the natural margin decay.
#   - Consumer (retail): no secular tailwind, modest margin improvement.
#   - Pharma: structural demand tailwind, margins are sticky once products
#     have moats.
# The original `growth_compounder` key is retained for backward compatibility
# with any caller that hasn't migrated to a sub-profile.
LIFECYCLE_PROFILES: Dict[str, LifecycleDefaults] = {
    "secular_hyper_growth":         LifecycleDefaults(0.35,  0.05,  15, 0.10),
    "hyper_growth":                 LifecycleDefaults(0.25,  0.05,  15, 0.10),
    "high_growth_compounder":       LifecycleDefaults(0.18,  0.045, 10, 0.10),  # was 0.04 / 0.15
    "growth_compounder":            LifecycleDefaults(0.135, 0.035, 10, 0.20),  # legacy fallback
    "growth_compounder_software":   LifecycleDefaults(0.135, 0.05,  10, 0.05),  # cloud/AI tailwind
    "growth_compounder_consumer":   LifecycleDefaults(0.10,  0.035, 10, 0.15),  # retail steady-state
    "growth_compounder_pharma":     LifecycleDefaults(0.12,  0.04,  10, 0.10),  # sticky margins
    "mature":                       LifecycleDefaults(0.05,  0.030, 10, 0.30),  # was 0.025
    "cyclical_industrial":          LifecycleDefaults(0.04,  0.025, 10, 0.50),
}


# Per-lifecycle hard ceiling on terminal growth in any scenario. Threaded
# into ValuationProfile via make_calc_input so the DCF can't accidentally
# project software at perpetual 6% (which would imply real growth above
# developed-economy long-run rates). Software gets 5.5% (raised from 4%);
# pharma 5%; consumer stays at 4%; mature drops to 3.5%.
TERMINAL_GROWTH_CAP_BY_LIFECYCLE: Dict[str, float] = {
    "secular_hyper_growth":       0.06,
    "hyper_growth":               0.055,
    "high_growth_compounder":     0.055,
    "growth_compounder":          0.04,
    "growth_compounder_software": 0.055,  # raised from 0.04
    "growth_compounder_consumer": 0.04,
    "growth_compounder_pharma":   0.05,
    "mature":                     0.035,
    "cyclical_industrial":        0.035,
}


@dataclass(frozen=True)
class ScenarioAdjustments:
    growth_haircut: float
    margin_compression: float
    wacc_adjustment: float


BEAR_ADJUSTMENTS = ScenarioAdjustments(-0.50, -0.10, +0.015)
BULL_ADJUSTMENTS = ScenarioAdjustments(+0.25, +0.10, -0.005)
