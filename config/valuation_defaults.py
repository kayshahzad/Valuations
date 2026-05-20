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
    # Terminal growth is the *perpetual* rate after the explicit forecast
    # period ends. Even hyper-growth businesses must converge to GDP-like
    # rates in the truly-long run — a company growing perpetually faster
    # than GDP would eventually become the entire economy. Long-run nominal
    # US GDP growth is ~4% (2-2.5% real + 2% inflation target), so terminal
    # growth above 4% is economically unsound regardless of profile.
    #
    # Hyper-growth optimism is expressed through the EXPLICIT forecast
    # period (Y1-5/Y6-10 CAGR, margins, longer fade) — NOT through perpetual
    # super-GDP terminal growth. Profile defaults below all respect the
    # MAX_TERMINAL_G = 0.04 ceiling.
    "secular_hyper_growth":         LifecycleDefaults(0.35,  0.04,  15, 0.10),  # was 0.05; capped at GDP perpetuity
    "hyper_growth":                 LifecycleDefaults(0.25,  0.04,  15, 0.10),  # was 0.05; same reason
    "high_growth_compounder":       LifecycleDefaults(0.18,  0.04,  10, 0.10),  # was 0.045
    "growth_compounder":            LifecycleDefaults(0.135, 0.035, 10, 0.20),  # already ≤ 4%
    "growth_compounder_software":   LifecycleDefaults(0.135, 0.04,  10, 0.05),  # was 0.05; cloud/AI tailwind expressed via Y1-5
    "growth_compounder_consumer":   LifecycleDefaults(0.10,  0.035, 10, 0.15),  # retail steady-state
    "growth_compounder_pharma":     LifecycleDefaults(0.12,  0.04,  10, 0.10),  # sticky margins
    "mature":                       LifecycleDefaults(0.05,  0.030, 10, 0.30),
    "cyclical_industrial":          LifecycleDefaults(0.04,  0.025, 10, 0.50),
}


# Per-lifecycle hard ceiling on terminal growth in any scenario. Threaded
# into ValuationProfile via make_calc_input. Caps respect the global
# MAX_TERMINAL_G = 4% — terminal growth above long-run GDP is economically
# unsound (perpetual super-GDP growth means the company eventually becomes
# the entire economy). High-growth profiles express their optimism through
# explicit-period assumptions (Y1-5 growth, margins, WACC), not by raising
# the perpetual terminal-growth ceiling.
TERMINAL_GROWTH_CAP_BY_LIFECYCLE: Dict[str, float] = {
    "secular_hyper_growth":       0.04,   # was 0.06
    "hyper_growth":               0.04,   # was 0.055
    "high_growth_compounder":     0.04,   # was 0.055
    "growth_compounder":          0.04,
    "growth_compounder_software": 0.04,   # was 0.055
    "growth_compounder_consumer": 0.04,
    "growth_compounder_pharma":   0.04,   # was 0.05
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
