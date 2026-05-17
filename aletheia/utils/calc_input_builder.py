"""
aletheia/utils/calc_input_builder.py

Single helper for assembling a `CalculationInput` from a ticker. Used by both
the test fixtures and the production agent layer (calc_node, context,
etc.) so all calc-tool callers go through the same construction path.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from aletheia.contracts.interfaces import CalculationInput, ValuationProfile
from config.ticker_classification import get_extended_universe
from config.known_issues import KNOWN_ISSUES
from config.valuation_defaults import (
    LIFECYCLE_PROFILES,
    TERMINAL_GROWTH_CAP_BY_LIFECYCLE,
)
from config.lifecycle_thresholds import STAGE_THRESHOLDS, Stage


def make_calc_input(ticker: str, df: Optional[pd.DataFrame] = None) -> CalculationInput:
    """
    Build a CalculationInput for `ticker`. If `df` is omitted, loads the
    cleaned multi-year history from DuckDB.

    Raises if the ticker isn't in the extended UNIVERSE (curated +
    runtime classifications) — calc tools require classification
    metadata to dispatch sector/lifecycle behavior correctly.
    """
    classification = get_extended_universe().get(ticker)
    if classification is None:
        raise ValueError(
            f"Ticker {ticker!r} not in extended UNIVERSE — add it to "
            f"config/ticker_classification.py or via the Add Ticker flow "
            f"before calling calc tools."
        )

    issues = KNOWN_ISSUES.get(ticker, [])
    lifecycle = classification.lifecycle or "mature"
    profile_cfg = LIFECYCLE_PROFILES.get(lifecycle, LIFECYCLE_PROFILES["mature"])
    # Terminal growth cap is per-lifecycle (sub-profile differentiation).
    # Software/cloud gets 5.5%, pharma 5%, consumer 4%, mature 3.5%.
    tg_cap = TERMINAL_GROWTH_CAP_BY_LIFECYCLE.get(lifecycle, 0.04)
    vp = ValuationProfile(
        growth_rate=profile_cfg.growth_rate,
        terminal_growth=profile_cfg.terminal_growth,
        forecast_years=profile_cfg.forecast_years,
        terminal_margin_decay=profile_cfg.terminal_margin_decay,
        terminal_growth_cap=tg_cap,
    )

    try:
        stage = Stage(lifecycle)
        thresholds = STAGE_THRESHOLDS[stage]
    except (ValueError, KeyError):
        thresholds = STAGE_THRESHOLDS[Stage.GROWTH_COMPOUNDER]

    if df is None:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        try:
            df = db.get_latest(ticker)
        finally:
            db.close()

    # Phase Q-1+ schemas carry a `period` column. The DCF engine
    # (Phase Q-5) selects TTM as the base row when present and falls
    # back to the latest FY row otherwise. We pass both shapes through;
    # filtering happens inside the engine where the base-row selection
    # is split from the FY-only historical CAGR computation.

    return CalculationInput(
        df=df,
        classification=classification,
        known_issues=issues,
        valuation_profile=vp,
        lifecycle_thresholds=thresholds,
    )
