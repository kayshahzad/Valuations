"""
aletheia/data/fmp_client.py

FinancialModelingPrep API client for data validation. Pulls SEC-derived
financial statements + ratios + estimates and caches per-ticker JSON locally
at valuation_data/macro/fmp/<TICKER>__<endpoint>.json with a 7-day TTL.

Usage requires FMP_API_KEY in the environment (or .env loaded into env).
Free tier (no credit card) gives 250 calls/day — enough for full-universe
quarterly validation + ad-hoc lookups.

API docs: https://site.financialmodelingprep.com/developer/docs/

Endpoints exposed:
  - fetch_income_statements(ticker)    annual income statements
  - fetch_balance_sheets(ticker)       annual balance sheets
  - fetch_cash_flows(ticker)           annual cash flow statements
  - fetch_ratios(ticker)               annual ratios
  - fetch_key_metrics(ticker)          annual key metrics (EV, FCF/share, etc.)
  - fetch_enterprise_values(ticker)    annual enterprise value series
  - fetch_analyst_estimates(ticker)    forward consensus revenue/EPS estimates
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# FMP deprecated /api/v3/ for accounts created after Aug 31, 2025. The
# current "stable" API uses /stable/<endpoint>?symbol=<TICKER>&... — no
# /api/ prefix and ticker passed as query param, not in path.
_BASE_STABLE = "https://financialmodelingprep.com/stable"
_CACHE_DIR = Path("valuation_data/macro/fmp")
_STALE_DAYS = 7
_TIMEOUT = 30
_RETRIES_429 = 3
_BACKOFF = 5  # seconds


def _api_key() -> Optional[str]:
    """Read FMP_API_KEY from env. Returns None if not set; callers fail-soft."""
    key = os.environ.get("FMP_API_KEY")
    if not key:
        # Try to load from .env if present (lightweight; no python-dotenv dep)
        env_path = Path(".env")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.strip().startswith("FMP_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("'\"")
                    break
    return key or None


def _cache_path(ticker: str, endpoint: str) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}__{endpoint}.json"


def _load_cache(ticker: str, endpoint: str, allow_stale: bool = False) -> Optional[Any]:
    """
    Read cache. Default: respect TTL (return None if older than `_STALE_DAYS`).
    With `allow_stale=True`, return whatever is on disk regardless of age —
    used as the fallback path when live fetch fails.
    """
    path = _cache_path(ticker, endpoint)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if not allow_stale:
            cached_at = datetime.fromisoformat(data.get("_cached_at", "1970-01-01"))
            if datetime.now() - cached_at > timedelta(days=_STALE_DAYS):
                return None
        return data.get("data")
    except Exception:
        return None


# In-process flag: once we've hit "Limit Reach" once in this run, stop attempting
# further live calls. They will all fail the same way and burn time.
_quota_exhausted: bool = False


def is_quota_exhausted() -> bool:
    return _quota_exhausted


def reset_quota_flag() -> None:
    """Reset the in-process quota flag — useful if the caller knows quota
    has reset (e.g., a long-running daemon crossing UTC midnight)."""
    global _quota_exhausted
    _quota_exhausted = False


def _save_cache(ticker: str, endpoint: str, data: Any) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(ticker, endpoint)
    with open(path, "w") as f:
        json.dump({
            "_cached_at": datetime.now().isoformat(),
            "ticker":     ticker.upper(),
            "endpoint":   endpoint,
            "data":       data,
        }, f, indent=2, default=str)


def _http_get(url: str) -> Optional[Any]:
    """GET with rate-limit-aware retry. Returns parsed JSON or None on failure.

    The free tier has *two* 429-shaped failures:
      - Burst rate-limit (transient) — body is short, retry-with-backoff works.
      - Daily quota exhausted (persistent until UTC midnight) — body contains
        "Limit Reach". Retrying just burns time. We log distinctly, set the
        in-process `_quota_exhausted` flag so subsequent calls skip live HTTP
        entirely, and bail.
    """
    global _quota_exhausted
    if _quota_exhausted:
        # Subsequent calls in the same run after quota was exhausted: don't
        # bother hitting the network.
        return None

    for attempt in range(_RETRIES_429 + 1):
        try:
            r = requests.get(url, timeout=_TIMEOUT)
        except requests.RequestException as e:
            logger.warning("FMP request failed: %s", e)
            return None
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                logger.warning("FMP returned non-JSON for %s", url)
                return None
        if r.status_code == 429:
            body = r.text or ""
            if "Limit Reach" in body or "upgrade your plan" in body:
                logger.error(
                    "FMP daily quota exhausted (HTTP 429). Subsequent calls "
                    "in this run will skip live HTTP and use stale cache only. "
                    "Quota resets ~UTC midnight. URL=%s", url[:80]
                )
                _quota_exhausted = True
                return None
            wait = _BACKOFF * (attempt + 1)
            logger.warning("FMP 429 rate-limited; sleeping %ds (attempt %d)", wait, attempt + 1)
            time.sleep(wait)
            continue
        if r.status_code in (401, 403):
            logger.error("FMP authentication failed (%d). Check FMP_API_KEY.", r.status_code)
            return None
        if r.status_code == 402:
            # "Special Endpoint" — symbol is not in current subscription tier.
            # Fail-soft: fall through with a clear log so callers can distinguish
            # subscription gating from network/auth failures.
            body = (r.text or "")[:200]
            logger.warning("FMP 402 (subscription-restricted) for %s: %s", url[:80], body)
            return None
        logger.warning("FMP returned %d for %s", r.status_code, url[:80])
        return None
    return None


def _fetch(ticker: str, endpoint_name: str, endpoint_label: str,
           params: Optional[Dict[str, str]] = None,
           force_refresh: bool = False) -> Optional[Any]:
    """
    Common fetch path: cache-first, then HTTP, then write cache.
    `endpoint_name` is the path segment after /stable/, e.g. "income-statement".
    The stable API takes the ticker as `?symbol=...` query param.
    """
    if not force_refresh:
        cached = _load_cache(ticker, endpoint_label)
        if cached is not None:
            return cached
    key = _api_key()
    if not key:
        logger.error("FMP_API_KEY not set; cannot fetch %s for %s", endpoint_label, ticker)
        # Even without an API key, stale cache may be useful.
        return _load_cache(ticker, endpoint_label, allow_stale=True)
    p = dict(params or {})
    p["symbol"] = ticker.upper()
    p["apikey"] = key
    qs = "&".join(f"{k}={v}" for k, v in p.items())
    url = f"{_BASE_STABLE}/{endpoint_name}?{qs}"
    data = _http_get(url)
    if data is None:
        # Live failed (network / 429-quota / 402-restricted / other). Fall
        # back to whatever is on disk regardless of TTL — stale data beats
        # no data, and the failure mode is logged distinctly upstream.
        stale = _load_cache(ticker, endpoint_label, allow_stale=True)
        if stale is not None:
            logger.info(
                "FMP %s/%s: live fetch failed; using stale cache.",
                ticker, endpoint_label,
            )
        return stale
    if isinstance(data, dict) and "Error Message" in data:
        logger.warning("FMP error for %s/%s: %s", ticker, endpoint_label, data["Error Message"])
        return _load_cache(ticker, endpoint_label, allow_stale=True)
    _save_cache(ticker, endpoint_label, data)
    return data


# ────────────────────────────────────────────────────────────────────────
# Public endpoint functions
# ────────────────────────────────────────────────────────────────────────

def fetch_income_statements(ticker: str, force_refresh: bool = False) -> Optional[List[Dict[str, Any]]]:
    """Annual income statements; FMP returns most-recent-first."""
    return _fetch(ticker, "income-statement", "income_annual",
                  params={"period": "annual", "limit": "30"},
                  force_refresh=force_refresh)


def fetch_balance_sheets(ticker: str, force_refresh: bool = False) -> Optional[List[Dict[str, Any]]]:
    return _fetch(ticker, "balance-sheet-statement", "balance_annual",
                  params={"period": "annual", "limit": "30"},
                  force_refresh=force_refresh)


def fetch_cash_flows(ticker: str, force_refresh: bool = False) -> Optional[List[Dict[str, Any]]]:
    return _fetch(ticker, "cash-flow-statement", "cashflow_annual",
                  params={"period": "annual", "limit": "30"},
                  force_refresh=force_refresh)


def fetch_ratios(ticker: str, force_refresh: bool = False) -> Optional[List[Dict[str, Any]]]:
    return _fetch(ticker, "ratios", "ratios_annual",
                  params={"period": "annual", "limit": "30"},
                  force_refresh=force_refresh)


def fetch_key_metrics(ticker: str, force_refresh: bool = False) -> Optional[List[Dict[str, Any]]]:
    return _fetch(ticker, "key-metrics", "key_metrics_annual",
                  params={"period": "annual", "limit": "30"},
                  force_refresh=force_refresh)


def fetch_enterprise_values(ticker: str, force_refresh: bool = False) -> Optional[List[Dict[str, Any]]]:
    return _fetch(ticker, "enterprise-values", "ev_annual",
                  params={"period": "annual", "limit": "30"},
                  force_refresh=force_refresh)


def fetch_analyst_estimates(ticker: str, force_refresh: bool = False) -> Optional[List[Dict[str, Any]]]:
    """Forward consensus revenue + EPS by fiscal year. 5y horizon typical."""
    return _fetch(ticker, "analyst-estimates", "analyst_estimates",
                  params={"period": "annual"},
                  force_refresh=force_refresh)


def fetch_profile(ticker: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """
    Company profile — sector, industry, country, currency, exchange, ISIN,
    description. Used to auto-classify newly added tickers (deriving the
    appropriate business_model + is_ifrs_filer flag).

    FMP returns a single-element list; we flatten to the dict for convenience.
    """
    raw = _fetch(ticker, "profile", "profile",
                 params={},
                 force_refresh=force_refresh)
    if isinstance(raw, list) and raw:
        return raw[0]
    if isinstance(raw, dict):
        return raw
    return None


# ────────────────────────────────────────────────────────────────────────
# Convenience helpers
# ────────────────────────────────────────────────────────────────────────

def has_api_key() -> bool:
    """Return True iff FMP_API_KEY is configured."""
    return _api_key() is not None


def probe_subscription(ticker: str) -> str:
    """
    Quick check: is `ticker` accessible on the current subscription tier?
    Returns one of: "ok", "restricted", "quota_exhausted", "auth_error",
    "no_key", "network_error". Does not write cache. Used by validators to
    distinguish "no data" causes.
    """
    key = _api_key()
    if not key:
        return "no_key"
    url = f"{_BASE_STABLE}/income-statement?period=annual&limit=5&symbol={ticker.upper()}&apikey={key}"
    try:
        r = requests.get(url, timeout=_TIMEOUT)
    except requests.RequestException:
        return "network_error"
    if r.status_code == 200:
        return "ok"
    if r.status_code == 402:
        return "restricted"
    if r.status_code == 429:
        body = r.text or ""
        if "Limit Reach" in body or "upgrade your plan" in body:
            return "quota_exhausted"
        return "network_error"
    if r.status_code in (401, 403):
        return "auth_error"
    return "network_error"


def get_for_fiscal_year(records: List[Dict[str, Any]], fy: int) -> Optional[Dict[str, Any]]:
    """
    Pick the record matching the given fiscal year. The stable API uses
    `fiscalYear` (string). Older v3 responses used `calendarYear`. Falls
    back to scanning `date` (period end) on mismatch.
    """
    if not records:
        return None
    fy_str = str(fy)
    for r in records:
        if str(r.get("fiscalYear")) == fy_str or str(r.get("calendarYear")) == fy_str:
            return r
    # Fallback: match on year extracted from date
    for r in records:
        d = r.get("date") or ""
        if d[:4] == fy_str:
            return r
    return None
