"""FRED credit-regime series — IG / HY option-adjusted spreads (Layer 11).

Uses FRED's *keyless* CSV endpoint (fredgraph.csv) — one call per series, no
API key, no CUSIP mapping, no cleaning. Cached to
``valuation_data/macro/fred/<series>.json`` with a 1-day TTL. Fail-soft: any
network/parse error returns ``None`` so the dashboard degrades to "no credit
line" rather than erroring.

Two series:
  BAMLC0A0CM   — ICE BofA US Corporate (investment-grade) Index OAS, %
  BAMLH0A0HYM2 — ICE BofA US High Yield Index OAS, %

The regime read is a *percentile of the full history*, not a hardcoded band, so
"near historic tights" is self-calibrating: a low HY percentile means spreads
are tighter than most of the observed record — credit is underwriting default
cheaply, and any "financial resilience" score is being graded on an easy curve.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CACHE_DIR = Path("valuation_data/macro/fred")
_TTL_SECONDS = 24 * 3600          # daily series → 1-day cache
_TIMEOUT = 20

IG_SERIES = "BAMLC0A0CM"
HY_SERIES = "BAMLH0A0HYM2"

# HY-OAS history-percentile thresholds for the regime label. Percentile = share
# of historical observations at or below the current level; a *low* percentile
# means unusually tight spreads.
_TIGHT_PCTL = 20.0
_STRESS_PCTL = 80.0


def _cache_path(series: str) -> Path:
    return _CACHE_DIR / f"{series}.json"


def _load_cache(series: str) -> Optional[Dict[str, Any]]:
    p = _cache_path(series)
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text())
        if time.time() - float(blob.get("_fetched", 0)) < _TTL_SECONDS:
            return blob
    except Exception:
        return None
    return None


def _save_cache(series: str, blob: Dict[str, Any]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        blob = {**blob, "_fetched": time.time()}
        _cache_path(series).write_text(json.dumps(blob))
    except Exception as e:  # cache is best-effort
        logger.debug("FRED cache write failed for %s: %s", series, e)


def _http_get_csv(series: str) -> Optional[str]:
    """GET the keyless fredgraph CSV. Uses ``requests`` (bundled certifi) so it
    works on macOS dev boxes where bare urllib fails cert verification."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    try:
        import requests
        r = requests.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.warning("FRED fetch failed for %s: %s", series, e)
        return None


def _parse_series(csv_text: str) -> Optional[Dict[str, Any]]:
    """Parse fredgraph CSV → {latest, as_of, percentile}. Percentile is the
    share of all valid historical values at or below the latest."""
    rows = csv_text.strip().splitlines()
    vals = []
    latest = None
    as_of = None
    for row in rows[1:]:                       # skip header
        parts = row.split(",")
        if len(parts) != 2 or parts[1] in (".", ""):
            continue
        try:
            v = float(parts[1])
        except ValueError:
            continue
        vals.append(v)
        latest, as_of = v, parts[0]            # last valid row wins
    if latest is None or not vals:
        return None
    at_or_below = sum(1 for v in vals if v <= latest)
    percentile = round(100.0 * at_or_below / len(vals), 1)
    return {"latest": latest, "as_of": as_of, "percentile": percentile,
            "n_obs": len(vals)}


def _series(series: str) -> Optional[Dict[str, Any]]:
    cached = _load_cache(series)
    if cached is not None:
        return cached
    csv_text = _http_get_csv(series)
    if csv_text is None:
        # fall back to any stale cache before giving up
        p = _cache_path(series)
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                return None
        return None
    parsed = _parse_series(csv_text)
    if parsed is not None:
        _save_cache(series, parsed)
    return parsed


def get_credit_regime() -> Optional[Dict[str, Any]]:
    """Market-wide credit regime from IG + HY OAS. Returns None if FRED is
    unreachable and no cache exists (dashboard then shows no credit line).

    Keys: ig_oas, hy_oas (%, decimal-of-percent i.e. 2.79 == 2.79%), hy_pctile,
    ig_pctile, regime ('tight'|'normal'|'stressed'), as_of, caveat (str|None).
    """
    ig = _series(IG_SERIES)
    hy = _series(HY_SERIES)
    if hy is None:                              # HY is the regime driver
        return None

    hy_oas = hy["latest"]
    hy_pctile = hy["percentile"]
    ig_oas = ig["latest"] if ig else None
    ig_pctile = ig["percentile"] if ig else None

    if hy_pctile <= _TIGHT_PCTL:
        regime = "tight"
        caveat = (
            f"Credit at {hy_pctile:.0f}th-percentile tights (HY OAS {hy_oas:.2f}%) — "
            "near-zero default pricing; financial-resilience scores are graded on "
            "an easy curve."
        )
    elif hy_pctile >= _STRESS_PCTL:
        regime = "stressed"
        caveat = (
            f"Credit stress: HY OAS {hy_oas:.2f}% at {hy_pctile:.0f}th percentile — "
            "financing is tightening; refinancing risk is repricing."
        )
    else:
        regime = "normal"
        caveat = None

    return {
        "ig_oas": ig_oas, "hy_oas": hy_oas,
        "ig_pctile": ig_pctile, "hy_pctile": hy_pctile,
        "regime": regime, "as_of": hy.get("as_of"), "caveat": caveat,
    }


__all__ = ["get_credit_regime", "IG_SERIES", "HY_SERIES"]
