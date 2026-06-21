"""Single source for the CAPM equity-risk-premium input.

The market risk premium (MRP / ERP) was hardcoded at 4.75% in ~9 places. It now
resolves from Damodaran's published implied-ERP series
(``valuation_data/macro/damodaran_implied_erp.csv``) via
``historical_macro.get_equity_risk_premium`` — so the live valuation engines and
the backtest share ONE source and pick up the current value (4.23% for 2026)
instead of a stale constant. Falls back to 4.75% if the series is unavailable.

Resolved per-process: the live engines call this at import (servers restart on
deploy, and the implied ERP moves ~annually); pass ``as_of`` for a point-in-time
value (e.g. a backtest date).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

# Damodaran long-run consensus — used only if the implied-ERP series can't be read.
_FALLBACK_MRP: float = 0.0475


def market_risk_premium(as_of: Optional[date] = None) -> float:
    """Equity risk premium for CAPM, from Damodaran's implied-ERP series as of
    ``as_of`` (default today). Falls back to 4.75% if the series is unavailable."""
    try:
        from aletheia.data.historical_macro import get_equity_risk_premium
        v = get_equity_risk_premium(as_of or date.today())
        return float(v) if v is not None else _FALLBACK_MRP
    except Exception:
        return _FALLBACK_MRP


__all__ = ["market_risk_premium"]
