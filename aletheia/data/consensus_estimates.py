"""
aletheia/data/consensus_estimates.py

Fetch analyst consensus growth estimates from Yahoo Finance via yfinance.
Used by the `consensus_growth` scenario to construct a Y1-5 / Y6-10 path
from real analyst expectations rather than historical extrapolation.

Yahoo provides three relevant series per ticker:
  - revenue_estimate.0y.growth   = current FY revenue growth consensus
  - revenue_estimate.+1y.growth  = next FY revenue growth consensus
  - growth_estimates.LTG         = long-term (5y) consensus, sometimes missing

Strategy when LTG is missing: linearly fade from y2 to a sector-typical 4%
over years 3-5. Y6-10 fades from the y3-5 average toward 2.5% terminal.

Caching: per-ticker daily refresh at
  valuation_data/macro/consensus_estimates/<TICKER>__<YYYY-MM-DD>.json

Stale (>7 days) caches are refetched. Network failures fall back to the
most-recent cached value if any exists.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_CACHE_DIR = Path("valuation_data/macro/consensus_estimates")
_STALE_DAYS = 7


@dataclass
class ConsensusEstimates:
    """Analyst consensus growth picture for a ticker."""
    ticker: str
    fetched_at: str  # ISO date
    y1_revenue_growth: Optional[float]   # current FY consensus, decimal
    y2_revenue_growth: Optional[float]   # next FY consensus, decimal
    ltg_consensus: Optional[float]       # long-term 5y consensus, decimal (often None)
    n_analysts: Optional[int]            # # analysts contributing to revenue est
    source: str = "yfinance"


def _cache_path(ticker: str, dt: date) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}__{dt.isoformat()}.json"


def _newest_cache(ticker: str) -> Optional[Path]:
    """Most recent cached file for ticker, regardless of age."""
    if not _CACHE_DIR.exists():
        return None
    files = sorted(_CACHE_DIR.glob(f"{ticker.upper()}__*.json"), reverse=True)
    return files[0] if files else None


def _fetch_from_yfinance(ticker: str) -> ConsensusEstimates:
    """Live fetch. Returns ConsensusEstimates with whatever fields yfinance gave us."""
    t = yf.Ticker(ticker)
    y1 = y2 = ltg = None
    n_analysts = None

    try:
        re = t.revenue_estimate
        if isinstance(re, pd.DataFrame) and not re.empty:
            if "0y" in re.index and "growth" in re.columns:
                v = re.loc["0y", "growth"]
                y1 = float(v) if pd.notna(v) else None
            if "+1y" in re.index and "growth" in re.columns:
                v = re.loc["+1y", "growth"]
                y2 = float(v) if pd.notna(v) else None
            if "0y" in re.index and "numberOfAnalysts" in re.columns:
                v = re.loc["0y", "numberOfAnalysts"]
                n_analysts = int(v) if pd.notna(v) else None
    except Exception as e:
        logger.warning("revenue_estimate fetch failed for %s: %s", ticker, e)

    try:
        ge = t.growth_estimates
        if isinstance(ge, pd.DataFrame) and not ge.empty:
            if "LTG" in ge.index and "stockTrend" in ge.columns:
                v = ge.loc["LTG", "stockTrend"]
                ltg = float(v) if pd.notna(v) else None
    except Exception as e:
        logger.warning("growth_estimates fetch failed for %s: %s", ticker, e)

    return ConsensusEstimates(
        ticker=ticker.upper(),
        fetched_at=date.today().isoformat(),
        y1_revenue_growth=y1,
        y2_revenue_growth=y2,
        ltg_consensus=ltg,
        n_analysts=n_analysts,
    )


def _enrich_with_fmp(ce: ConsensusEstimates) -> ConsensusEstimates:
    """
    Enhance a Yahoo-derived ConsensusEstimates with FMP analyst-estimates
    when LTG is missing. Yahoo no longer publishes LTG for major US tickers
    (AAPL/MSFT/LLY/COST all return None as of 2026); FMP's analyst-estimates
    endpoint provides forward consensus revenue + EPS by fiscal year.

    We compute LTG as the geometric mean of (eps_est_y2 / eps_est_y1) over
    the available forward-year estimates. Falls back to Y1→Y3 revenue CAGR
    if EPS estimates are missing.
    """
    if ce.ltg_consensus is not None:
        return ce  # Yahoo gave us LTG; no enrichment needed
    try:
        from aletheia.data import fmp_client
        if not fmp_client.has_api_key():
            return ce
        estimates = fmp_client.fetch_analyst_estimates(ce.ticker)
        if not estimates:
            return ce
    except Exception:
        return ce

    # FMP returns most-recent-first; we want forward-year estimates
    # (date strings should be future fiscal year ends).
    forward = []
    today = date.today().isoformat()
    for e in estimates:
        if not isinstance(e, dict):
            continue
        d = str(e.get("date") or "")
        if d > today:
            forward.append(e)
    forward = sorted(forward, key=lambda e: e.get("date", ""))[:5]
    if len(forward) < 2:
        return ce

    # Prefer EPS-based LTG; fall back to revenue if EPS is sparse.
    # Stable API uses `epsAvg`/`revenueAvg` (legacy v3 used `estimatedEpsAvg`
    # / `estimatedRevenueAvg`). Check both for compatibility across cached
    # files written under either schema.
    eps_series: list = []
    rev_series: list = []
    for e in forward:
        eps_v = e.get("epsAvg") if e.get("epsAvg") is not None else e.get("estimatedEpsAvg")
        rev_v = e.get("revenueAvg") if e.get("revenueAvg") is not None else e.get("estimatedRevenueAvg")
        if eps_v is not None:
            try:
                eps_series.append(float(eps_v))
            except (TypeError, ValueError):
                pass
        if rev_v is not None:
            try:
                rev_series.append(float(rev_v))
            except (TypeError, ValueError):
                pass

    ltg = None
    if len(eps_series) >= 2 and eps_series[0] > 0 and eps_series[-1] > 0:
        n_years = len(eps_series) - 1
        ltg = (eps_series[-1] / eps_series[0]) ** (1.0 / n_years) - 1.0
    elif len(rev_series) >= 2 and rev_series[0] > 0 and rev_series[-1] > 0:
        n_years = len(rev_series) - 1
        ltg = (rev_series[-1] / rev_series[0]) ** (1.0 / n_years) - 1.0

    if ltg is None:
        return ce
    return ConsensusEstimates(
        ticker=ce.ticker,
        fetched_at=ce.fetched_at,
        y1_revenue_growth=ce.y1_revenue_growth,
        y2_revenue_growth=ce.y2_revenue_growth,
        ltg_consensus=ltg,
        n_analysts=ce.n_analysts,
        source="yfinance+fmp_ltg",
    )


def get_consensus_estimates(
    ticker: str,
    force_refresh: bool = False,
) -> Optional[ConsensusEstimates]:
    """
    Return consensus estimates for `ticker`. Cache-first; refetches if cache
    is missing or older than _STALE_DAYS. On network failure with no cache,
    returns None.
    """
    today = date.today()
    cache_today = _cache_path(ticker, today)

    if cache_today.exists() and not force_refresh:
        try:
            with open(cache_today) as f:
                data = json.load(f)
            return ConsensusEstimates(**data)
        except Exception:
            pass

    # Check for not-too-stale cache
    newest = _newest_cache(ticker)
    if newest and not force_refresh:
        try:
            stem_date = newest.stem.split("__", 1)[1]
            cached_dt = datetime.strptime(stem_date, "%Y-%m-%d").date()
            if (today - cached_dt) <= timedelta(days=_STALE_DAYS):
                with open(newest) as f:
                    data = json.load(f)
                return ConsensusEstimates(**data)
        except Exception:
            pass

    # Fresh fetch
    try:
        ce = _fetch_from_yfinance(ticker)
    except Exception as e:
        logger.warning("Consensus fetch failed for %s: %s", ticker, e)
        # Fall back to any cached value if available
        if newest:
            try:
                with open(newest) as f:
                    return ConsensusEstimates(**json.load(f))
            except Exception:
                return None
        return None

    # Phase 3.5b: enrich with FMP analyst estimates for LTG when Yahoo
    # doesn't provide it (which is currently true for all major US tickers).
    ce = _enrich_with_fmp(ce)

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_today, "w") as f:
        json.dump(asdict(ce), f, indent=2, sort_keys=True)
    return ce


def construct_growth_path(
    ce: ConsensusEstimates,
    terminal_growth: float = 0.025,
) -> Dict[str, float]:
    """
    Convert consensus to a Y1-5 / Y6-10 / terminal growth path that fits
    the ScenarioOverride bounds.

    Logic:
      - Y1, Y2 from consensus directly
      - Y3-5: if LTG present, use LTG; else linear fade from y2 toward 4%
      - Y6-10: fade from Y3-5 average toward terminal_growth

    Returns a dict with keys: y1_5_avg, y6_10_avg, terminal, used_ltg, used_fallback.
    """
    y1 = ce.y1_revenue_growth
    y2 = ce.y2_revenue_growth
    ltg = ce.ltg_consensus

    y1_5: list = []
    if y1 is not None:
        y1_5.append(y1)
    if y2 is not None:
        y1_5.append(y2)

    if ltg is not None and ltg > -0.10:
        # Years 3-5 use LTG
        y1_5.extend([ltg, ltg, ltg])
        used_ltg = True
    else:
        # Linear fade from y2 toward 4% across years 3-5
        target = 0.04
        anchor = y2 if y2 is not None else (y1 if y1 is not None else target)
        for k in range(1, 4):  # years 3, 4, 5
            blend = anchor + (target - anchor) * (k / 3)
            y1_5.append(blend)
        used_ltg = False

    if not y1_5:
        return {"y1_5_avg": 0.05, "y6_10_avg": 0.04, "terminal": terminal_growth,
                "used_ltg": False, "used_fallback": True}

    y1_5_avg = sum(y1_5) / len(y1_5)

    # Y6-10: fade from Y1-5 average toward terminal
    midpoint = (y1_5_avg + terminal_growth) / 2

    return {
        "y1_5_avg": y1_5_avg,
        "y6_10_avg": midpoint,
        "terminal": terminal_growth,
        "used_ltg": used_ltg,
        "used_fallback": False,
    }
