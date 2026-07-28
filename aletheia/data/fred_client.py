"""FRED credit-regime series — IG / HY option-adjusted spreads (Layer 11).

Uses FRED's *keyless* CSV endpoint (fredgraph.csv) — one call per series, no
API key, no CUSIP mapping, no cleaning. Cached to
``valuation_data/macro/fred/<series>.json`` with a 1-day TTL. Fail-soft: any
network/parse error returns ``None`` so the dashboard degrades to "no credit
line" rather than erroring — but bounded: a stale cache older than
``_MAX_STALE_SECONDS`` is discarded rather than displayed, so we never show a
week-old spread during the exact market stress when the number matters most.

Two series:
  BAMLC0A0CM   — ICE BofA US Corporate (investment-grade) Index OAS, %
  BAMLH0A0HYM2 — ICE BofA US High Yield Index OAS, %

Regime read is a *history percentile*, stated with an explicit direction
("tighter than X% of history since YYYY") so it can't be misread. We compute
BOTH a full-history and a trailing-10-year percentile: when they diverge
materially, the divergence is itself informative — part of the tightness is
compositional (today's HY index carries more BB / less CCC than the 2000-2010
index), not pure risk appetite, and the caveat says so rather than overclaiming.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE_DIR = Path("valuation_data/macro/fred")
_TTL_SECONDS = 24 * 3600            # daily series → 1-day fresh cache
_MAX_STALE_SECONDS = 3 * 24 * 3600  # never serve a cache older than this
_TIMEOUT = 20

IG_SERIES = "BAMLC0A0CM"
HY_SERIES = "BAMLH0A0HYM2"

# HY-OAS full-history-percentile thresholds for the regime label. Percentile =
# share of historical observations at or below the current level; a *low*
# percentile means unusually tight spreads (current is tighter than most of the
# record).
_TIGHT_PCTL = 20.0
_STRESS_PCTL = 80.0

# --- Long-run reference distribution -----------------------------------------
# FRED's keyless CSV license-restricts ICE BofA index series (BAML*) to a
# trailing ~3-year window, which is too short for a meaningful percentile (it
# misses 2008 / 2016 / 2020). We fetch the *current level* live, but place it
# against a STATIC published long-run distribution (ICE BofA OAS, monthly,
# 1996-12 .. 2024) so "tighter than X% of history" spans decades, not 3 years.
# (percentile, OAS %) breakpoints; regime/percentile come from interpolation.
# To upgrade to a live full-history percentile, set FRED_API_KEY and swap in the
# observations API (this reference is the keyless fallback).
_REF_START = "1996"
_REF_END = "2024"
_LONGRUN_REF = {
    "HY": [(0, 2.3), (10, 3.3), (25, 3.9), (50, 4.9), (75, 6.3), (90, 8.6), (100, 21.8)],
    "IG": [(0, 0.5), (10, 0.9), (25, 1.1), (50, 1.4), (75, 1.9), (90, 2.4), (100, 6.6)],
}


def _ref_percentile(kind: str, level: float) -> Optional[float]:
    """Interpolate ``level``'s percentile within the long-run reference."""
    bp = _LONGRUN_REF.get(kind)
    if not bp:
        return None
    if level <= bp[0][1]:
        return 0.0
    if level >= bp[-1][1]:
        return 100.0
    for (p0, l0), (p1, l1) in zip(bp, bp[1:]):
        if l0 <= level <= l1 and l1 != l0:
            return round(p0 + (p1 - p0) * (level - l0) / (l1 - l0), 0)
    return 50.0


def _cache_path(series: str) -> Path:
    return _CACHE_DIR / f"{series}.json"


def _read_cache_raw(series: str) -> Optional[Dict[str, Any]]:
    p = _cache_path(series)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _load_fresh_cache(series: str) -> Optional[Dict[str, Any]]:
    blob = _read_cache_raw(series)
    if blob and time.time() - float(blob.get("_fetched", 0)) < _TTL_SECONDS:
        return blob
    return None


def _load_stale_cache(series: str) -> Optional[Dict[str, Any]]:
    """Fallback when a live fetch fails — but bounded: refuse to serve a cache
    older than _MAX_STALE_SECONDS so a FRED outage hides the line instead of
    silently showing a days-old spread."""
    blob = _read_cache_raw(series)
    if blob and time.time() - float(blob.get("_fetched", 0)) < _MAX_STALE_SECONDS:
        return blob
    return None


def _save_cache(series: str, blob: Dict[str, Any]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(series).write_text(json.dumps({**blob, "_fetched": time.time()}))
    except Exception as e:  # cache is best-effort
        logger.debug("FRED cache write failed for %s: %s", series, e)


def _http_get_csv(series: str) -> Optional[str]:
    """GET the keyless fredgraph CSV. Uses ``requests`` (bundled certifi) so it
    works on macOS dev boxes where bare urllib fails cert verification.

    NOTE: for the ICE BofA (BAML*) series this endpoint license-restricts output
    to a trailing ~3-year window — enough for the current level, not for a
    full-history percentile. The keyed observations API (below) has no such cap.
    """
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    try:
        import requests
        r = requests.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.warning("FRED fetch failed for %s: %s", series, e)
        return None


def _api_key() -> Optional[str]:
    """FRED_API_KEY from env, falling back to a .env line (no dotenv dep).
    Returns None when unset → callers fall back to the keyless + reference path.
    """
    key = os.environ.get("FRED_API_KEY")
    if not key:
        env_path = Path(".env")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.strip().startswith("FRED_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("'\"")
                    break
    return key or None


def _fetch_full_history(series: str) -> Optional[List[Tuple[str, float]]]:
    """Full-history (date, value) via the keyed observations API — no window
    cap, so real percentiles back to the series start (~1996). Returns None if
    no key or the call fails (caller then uses the keyless level + reference)."""
    key = _api_key()
    if not key:
        return None
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series}&api_key={key}&file_type=json"
        "&observation_start=1990-01-01"
    )
    try:
        import requests
        r = requests.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
        obs = r.json().get("observations", [])
    except Exception as e:
        logger.warning("FRED API fetch failed for %s: %s", series, e)
        return None
    pairs: List[Tuple[str, float]] = []
    for o in obs:
        v = o.get("value")
        if v in (".", "", None):
            continue
        try:
            pairs.append((o["date"], float(v)))
        except (ValueError, KeyError):
            continue
    return pairs or None


def _live_percentiles(pairs: List[Tuple[str, float]]) -> Optional[Dict[str, Any]]:
    """Latest level + full-history and trailing-10y percentiles from real history."""
    if not pairs:
        return None
    as_of, latest = pairs[-1]
    vals = [v for _, v in pairs]
    pctile_full = round(100.0 * sum(1 for v in vals if v <= latest) / len(vals), 1)
    try:
        cutoff = f"{int(as_of[:4]) - 10}{as_of[4:]}"
    except ValueError:
        cutoff = ""
    vals_10y = [v for d, v in pairs if d >= cutoff]
    pctile_10y = (round(100.0 * sum(1 for v in vals_10y if v <= latest) / len(vals_10y), 1)
                  if vals_10y else None)
    return {"latest": latest, "as_of": as_of, "start_year": pairs[0][0][:4],
            "pctile_full": pctile_full, "pctile_10y": pctile_10y,
            "n_obs": len(vals), "source": "api"}


def _pairs(csv_text: str) -> List[Tuple[str, float]]:
    """(date, value) for every VALID row. ICE BofA series report '.' on
    holidays/non-trading days instead of omitting the row — those are skipped
    explicitly, and the last valid observation is used, so a missing print
    never raises and never masquerades as data."""
    out: List[Tuple[str, float]] = []
    for row in csv_text.strip().splitlines()[1:]:   # skip header
        parts = row.split(",")
        if len(parts) != 2 or parts[1] in (".", ""):
            continue
        try:
            out.append((parts[0], float(parts[1])))
        except ValueError:
            continue
    return out


def _parse_series(csv_text: str) -> Optional[Dict[str, Any]]:
    """Latest valid observation + the window the live (keyless) series spans.
    Percentiles come from the long-run reference in get_credit_regime, not from
    this ~3-year window — see _LONGRUN_REF."""
    pairs = _pairs(csv_text)
    if not pairs:
        return None
    as_of, latest = pairs[-1]
    return {"latest": latest, "as_of": as_of,
            "start_year": pairs[0][0][:4], "n_obs": len(pairs)}


def _series(series: str) -> Optional[Dict[str, Any]]:
    fresh = _load_fresh_cache(series)
    if fresh is not None:
        return fresh
    # Prefer the keyed observations API (real full history → live percentiles).
    hist = _fetch_full_history(series)
    if hist:
        parsed = _live_percentiles(hist)
        if parsed is not None:
            _save_cache(series, parsed)
            return parsed
    # No key / API failure → keyless CSV gives the current level only.
    csv_text = _http_get_csv(series)
    if csv_text is None:
        return _load_stale_cache(series)        # bounded stale fallback
    parsed = _parse_series(csv_text)
    if parsed is not None:
        parsed["source"] = "keyless"
        _save_cache(series, parsed)
    return parsed


def get_credit_regime() -> Optional[Dict[str, Any]]:
    """Market-wide credit regime from IG + HY OAS. Returns None if FRED is
    unreachable and no sufficiently-fresh cache exists (dashboard then shows no
    credit line).

    With FRED_API_KEY set, the percentile/regime come from REAL live full
    history (~1996-present) plus a trailing-10y percentile, and a divergence
    between the two is surfaced as a composition/regime hint. Without a key the
    keyless CSV gives only a ~3y window, so we fall back to placing the live
    level against the static 1996-2024 reference distribution.

    Keys: ig_oas, hy_oas (%), hy_pctile, hy_pctile_10y, ig_pctile, basis
    ('live 1996-present' | '1996-2024 reference'), regime, position, as_of,
    caveat.
    """
    ig = _series(IG_SERIES)
    hy = _series(HY_SERIES)
    if hy is None:                              # HY is the regime driver
        return None

    hy_oas = hy["latest"]
    ig_oas = ig["latest"] if ig else None

    # Use LIVE percentiles only when the series actually carries enough history.
    # The FRED API key returns full history for most series, but ICE BofA
    # license-restricts the BAML* credit series to a trailing ~3y window even
    # WITH a key (verified) — too short for a meaningful percentile, so those
    # fall through to the static reference distribution below.
    def _span_years(s: Dict[str, Any]) -> int:
        try:
            return int(s["as_of"][:4]) - int(s["start_year"])
        except Exception:
            return 0

    live = (
        hy.get("source") == "api"
        and hy.get("pctile_full") is not None
        and _span_years(hy) >= 10
    )

    if live:
        # Real full-history percentile — direction stated explicitly.
        pctile = hy["pctile_full"]
        pctile_10y = hy.get("pctile_10y")
        start = hy.get("start_year", "1996")
        basis = f"live {start}-present"
        ig_pctile = ig.get("pctile_full") if (ig and ig.get("source") == "api") else None
        window_phrase = f"history since {start}"
        # 10y-vs-full divergence → composition/regime hint (don't overclaim).
        diverge_note = (
            f" (vs {pctile_10y:.0f}th over 10y — divergence is part composition, "
            "part regime)"
            if (pctile_10y is not None and abs(pctile - pctile_10y) >= 20) else ""
        )
    else:
        # Keyless: place the live level against the static reference.
        pctile = _ref_percentile("HY", hy_oas)
        if pctile is None:
            return None
        pctile_10y = None
        ig_pctile = _ref_percentile("IG", ig_oas) if ig_oas is not None else None
        basis = f"{_REF_START}-{_REF_END} reference"
        window_phrase = f"{_REF_START}-{_REF_END} history"
        diverge_note = ""

    if pctile <= _TIGHT_PCTL:
        regime = "tight"
        position = f"tighter than {100 - pctile:.0f}% of {window_phrase}"
        caveat = (
            f"HY OAS {hy_oas:.2f}% — {position}{diverge_note}. Some tightness is "
            "compositional (today's HY index is higher-quality than pre-2010), so "
            "read as cheap credit / easy curve, not zero risk — financial-resilience "
            "scores are being graded generously."
        )
    elif pctile >= _STRESS_PCTL:
        regime = "stressed"
        position = f"wider than {pctile:.0f}% of {window_phrase}"
        caveat = (
            f"HY OAS {hy_oas:.2f}% — {position}{diverge_note}; financing is "
            "tightening and refinancing risk is repricing."
        )
    else:
        regime = "normal"
        position = f"mid-range ({pctile:.0f}th pctile of {window_phrase})"
        caveat = None

    return {
        "ig_oas": ig_oas, "hy_oas": hy_oas,
        "hy_pctile": pctile, "hy_pctile_10y": pctile_10y, "ig_pctile": ig_pctile,
        "basis": basis, "regime": regime, "position": position,
        "as_of": hy.get("as_of"), "caveat": caveat,
    }


__all__ = ["get_credit_regime", "IG_SERIES", "HY_SERIES"]
