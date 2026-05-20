"""
config/gdp_projections.py

US nominal GDP projections used by the Reality Checks module to flag
terminal-year revenue projections that imply implausibly large fractions
of national output.

Source: CBO Long-Term Budget Outlook (most recent edition), nominal GDP
projection points. Linear interpolation handles intermediate years; the
projection is reasonably smooth so this approximation is fine.

Methodology
-----------
The CBO publishes 30-year nominal GDP projections in their Long-Term
Budget Outlook (annual; March/April release). We capture the curve at
five-year markers, which is sufficient resolution given the linear
interpolation downstream and the 10-year horizon we typically use.

The values below assume ~4% nominal GDP growth over the next decade
(real growth ~2% + inflation ~2%) tapering to ~3.5% longer-term, which
matches CBO's central projection. Actual realized values may diverge;
analyst should review at least annually.

Last reviewed: 2026-05-07 (against CBO LTBO 2025).
"""

from __future__ import annotations

from datetime import date
from typing import Dict


# Projection points: year (YYYY) → nominal GDP in USD billions ($B).
# Source: CBO LTBO 2025, nominal GDP series.
GDP_PROJECTIONS_USD_BILLIONS: Dict[int, float] = {
    2024: 28_300.0,    # Actual (BEA)
    2025: 29_700.0,    # Actual / near-actual
    2026: 30_900.0,
    2027: 32_100.0,
    2030: 35_900.0,
    2035: 43_700.0,
    2040: 53_000.0,
    2045: 64_200.0,
    2050: 77_700.0,
    2055: 93_900.0,
}

# Configurable thresholds (locked 2026-05-07): revenue ÷ GDP at the
# projection horizon. Looser thresholds chosen because companies do
# legitimately reach 2-3% of GDP at maturity (AAPL ~1.4% today, MSFT ~1.3%,
# WMT historically peaked near 3%). Flagging at <1% would train analysts to
# ignore the signal.
GDP_THRESHOLDS = {
    "caution":  0.03,   # 3% of GDP — unusual scale, examine
    "warning":  0.05,   # 5% — historical maximum (WMT peak), warrants scepticism
    "critical": 0.10,   # 10% — implies the company IS the economy
}

# Last-reviewed flag for sanity checks.
LAST_REVIEWED: date = date(2026, 5, 7)
