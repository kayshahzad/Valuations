from dataclasses import dataclass
import pandas as pd
from typing import List, Dict, Literal, Optional, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


# ─────────────────────────────────────────────────────────────────────────────
# Phase C — Typed scenario overrides
#
# Agents (forensic, value_chain, context) may PROPOSE alternate scenarios
# alongside their narrative. Each scenario is a typed bundle of bounded
# overrides applied to a fresh DCFEngine run; the calc layer still does all
# math. Architectural protection:
#   - Only listed fields can be overridden — NEVER WACC, ROIC, tax_rate,
#     beta, kwargs, or arbitrary calc inputs
#   - Hard bounds enforced in __post_init__ (validators); out-of-range
#     values raise immediately rather than silently clipping
#   - frozen=True; adding a field requires schema PR review
#   - Each scenario carries `proposed_by` and `rationale` for full
#     provenance, surfaced in contrarian/lead narrative and Streamlit
# ─────────────────────────────────────────────────────────────────────────────

class ScenarioOverride(BaseModel):
    """A typed alternate-scenario hypothesis proposed by a narrative agent."""
    model_config = ConfigDict(frozen=True)

    name: str = Field(
        description="Short label for the scenario, e.g. 'AI capex peak' or "
                    "'Services-led re-acceleration'.",
        min_length=3,
        max_length=80,
    )
    scenario_type: Literal["bull", "bear", "base_alternative"] = Field(
        description="Direction of the override relative to base case."
    )
    proposed_by: Literal["forensic", "value_chain", "context"] = Field(
        description="Which agent proposed this scenario."
    )
    rationale: str = Field(
        description="Brief justification — why this scenario is worth modelling.",
        min_length=10,
        max_length=600,
    )

    # Bounded override fields. Each is optional; only the ones provided are
    # applied to the cloned ValuationProfile. Bounds are intentionally tight
    # to prevent agent hallucinations from producing extreme valuations.
    revenue_growth_y1_5: Optional[float] = Field(
        default=None,
        description="Revenue CAGR years 1-5. Bounded [0.0, 0.45]."
    )
    revenue_growth_y6_10: Optional[float] = Field(
        default=None,
        description="Revenue CAGR years 6-10. Bounded [0.0, 0.25]."
    )
    terminal_margin_decay: Optional[float] = Field(
        default=None,
        description="Terminal EBIT margin / current margin ratio. "
                    "Bounded [0.5, 1.0] (terminal margin can compress, not expand)."
    )
    terminal_growth: Optional[float] = Field(
        default=None,
        description="Terminal (perpetual) growth rate. Bounded [0.0, 0.04]."
    )
    base_revenue_normalization: Optional[float] = Field(
        default=None,
        description="Override the base-year revenue (USD, absolute). Used for "
                    "cyclical-peak normalization. Must be > 0.",
    )

    @field_validator("revenue_growth_y1_5")
    @classmethod
    def _check_y1_5(cls, v):
        if v is not None and not (0.0 <= v <= 0.45):
            raise ValueError(f"revenue_growth_y1_5 must be in [0.0, 0.45], got {v}")
        return v

    @field_validator("revenue_growth_y6_10")
    @classmethod
    def _check_y6_10(cls, v):
        if v is not None and not (0.0 <= v <= 0.25):
            raise ValueError(f"revenue_growth_y6_10 must be in [0.0, 0.25], got {v}")
        return v

    @field_validator("terminal_margin_decay")
    @classmethod
    def _check_margin_decay(cls, v):
        if v is not None and not (0.5 <= v <= 1.0):
            raise ValueError(f"terminal_margin_decay must be in [0.5, 1.0], got {v}")
        return v

    @field_validator("terminal_growth")
    @classmethod
    def _check_terminal_growth(cls, v):
        if v is not None and not (0.0 <= v <= 0.04):
            raise ValueError(f"terminal_growth must be in [0.0, 0.04], got {v}")
        return v

    @field_validator("base_revenue_normalization")
    @classmethod
    def _check_base_rev(cls, v):
        if v is not None and not (v > 0):
            raise ValueError(f"base_revenue_normalization must be > 0, got {v}")
        return v

    def applies_any_override(self) -> bool:
        """True if at least one override field is set."""
        return any(
            getattr(self, f) is not None
            for f in (
                "revenue_growth_y1_5",
                "revenue_growth_y6_10",
                "terminal_margin_decay",
                "terminal_growth",
                "base_revenue_normalization",
            )
        )

