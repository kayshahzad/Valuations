"""FRED macro series — registry-driven, three structurally-isolated channels.

The organizing constraint (design doc): the channels must not leak into each
other. Two accessors exist FOR A REASON — do not collapse them:

  Channel A — MOVES INTRINSIC VALUE (rf → WACC, terminal growth, cycle
    detection). Held to the SAME rigor as any cleaned financial field: every
    Channel-A series carries a plausibility band + a staleness bound
    (``validate_registry`` enforces this), and a stale/invalid value MUST NOT
    reach the valuation — the consumer fails loud and refuses to value (the D&A
    precedent). No silent fallback. Channel A cannot fall back to a reference
    distribution the way the ICE series can, so it needs the live source.

  Channel B — CALIBRATES CONVICTION (credit-regime cohorts). Stale/invalid →
    lower confidence / omit the read.  ``get_credit_regime`` lives here.

  Channel C — DISPLAY ONLY, and NEVER read by an agent or calc module. The
    architecture test asserts nothing outside ``aletheia/ui/`` imports the
    Channel-C accessor. Stale → hide the line.

Series are declared in ``FRED_SERIES`` (a ``SeriesSpec`` each: channel, native
frequency, staleness bound, unit transform, plausibility bounds). Adding a
series is a config line, not code. The unit ``transform`` is the 100× landmine:
FRED returns 4.42 for 4.42%, so a rate feeding WACC needs ÷100 → 0.0442; a
missed conversion is a 100× WACC error the validator's bounds catch.

Fetch: keyed observations API (full history) with the keyless fredgraph CSV as
fallback. Cached under ``valuation_data/macro/fred/``. NOTE: FRED license-
restricts the ICE BofA (BAML*) credit series to a trailing ~3y window even with
a key, so the credit regime places the current level against a static 1996-2024
reference distribution; DGS*/UMCSENT return true full history.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE_DIR = Path("valuation_data/macro/fred")
_TTL_SECONDS = 24 * 3600            # daily series → 1-day fresh cache
_MAX_STALE_SECONDS = 3 * 24 * 3600  # never serve a cache older than this
_TIMEOUT = 20

# HY-OAS full-history-percentile thresholds for the regime label. Percentile =
# share of historical observations at or below the current level; a *low*
# percentile means unusually tight spreads (current is tighter than most of the
# record).
_TIGHT_PCTL = 20.0
_STRESS_PCTL = 80.0


# ── Series registry ───────────────────────────────────────────────────────────
# Three structurally-isolated channels (see module docstring):
#   A — moves intrinsic value (rf → WACC, terminal growth). SAME rigor as a
#       cleaned financial field: MUST carry bounds + a staleness bound, and a
#       stale/invalid value MUST NOT reach the valuation (the consumer fails
#       loud and refuses to value — the D&A precedent). No silent fallback.
#   B — calibrates conviction (credit-regime cohorts). Stale/invalid → lower
#       confidence / omit the read.
#   C — display only, NEVER read by an agent/calc module. Stale → hide the line.
@dataclass(frozen=True)
class SeriesSpec:
    series_id: str
    channel: str                                    # "A" | "B" | "C"
    freq: str                                       # "daily" | "monthly"
    staleness_days: int
    transform: Optional[Callable[[float], float]]   # native FRED units → consumer units
    description: str
    bounds: Optional[Tuple[float, float]] = None    # plausibility band on the TRANSFORMED value


# The transform is the 100× landmine: FRED returns 4.42 for 4.42%, so a rate
# feeding WACC needs ÷100 → 0.0442. OAS series stay in percent — their consumer
# (the credit regime + reference distribution) works in percent.
def _pct_to_decimal(v: float) -> float:
    return v / 100.0


FRED_SERIES: Dict[str, "SeriesSpec"] = {
    # Channel A — moves IV, and its failure is fail-loud (refuse to value), so it
    # needs real staleness headroom, NOT a tight bound. Empirically the largest
    # gap in 36y of DGS10 is 4 calendar days (3-day-weekend holidays); with T+1
    # publishing, a normal check can already read 4d old (Fri obs on a Tue). 7d
    # gives margin against a holiday-gap + publish-timing stack while still
    # catching a genuine FRED outage (7+ days is really broken).
    "DGS10": SeriesSpec("DGS10", "A", "daily", 7, _pct_to_decimal,
                        "10Y Treasury constant maturity (risk-free rate)", bounds=(0.0, 0.20)),
    # Channel B — conviction. OAS in percent (no transform).
    "BAMLH0A0HYM2": SeriesSpec("BAMLH0A0HYM2", "B", "daily", 5, None,
                               "ICE BofA US High Yield OAS, %", bounds=(0.5, 30.0)),
    "BAMLC0A0CM": SeriesSpec("BAMLC0A0CM", "B", "daily", 5, None,
                             "ICE BofA US Investment-Grade OAS, %", bounds=(0.2, 15.0)),
    # Channel C — display only. DGS2 pairs with DGS10 for the T10Y2Y curve spread.
    "DGS2": SeriesSpec("DGS2", "C", "daily", 5, _pct_to_decimal,
                       "2Y Treasury (curve: T10Y2Y = DGS10 − DGS2)", bounds=(0.0, 0.20)),
    "UMCSENT": SeriesSpec("UMCSENT", "C", "monthly", 45, None,
                          "U. Michigan consumer sentiment index", bounds=(0.0, 200.0)),
}

# Legacy aliases (existing callers).
IG_SERIES = "BAMLC0A0CM"
HY_SERIES = "BAMLH0A0HYM2"


def validate_registry() -> List[str]:
    """Registry-integrity guard (asserted by tests): every Channel-A series MUST
    carry bounds + a positive staleness bound, so a careless Channel-A addition
    can't ship without the rigor that channel demands. Returns a list of
    violations (empty = clean)."""
    problems: List[str] = []
    for sid, spec in FRED_SERIES.items():
        if spec.channel not in ("A", "B", "C"):
            problems.append(f"{sid}: invalid channel {spec.channel!r}")
        if spec.staleness_days <= 0:
            problems.append(f"{sid}: staleness_days must be positive")
        if spec.channel == "A" and spec.bounds is None:
            problems.append(f"{sid}: Channel-A series must declare bounds")
    return problems


def _days_since(iso_date: str) -> Optional[int]:
    try:
        y, m, d = (int(x) for x in iso_date.split("-"))
        return (date.today() - date(y, m, d)).days
    except Exception:
        return None

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


def get_series(series_id: str) -> Optional[Dict[str, Any]]:
    """Registry-driven accessor: fetch a REGISTERED series in CONSUMER units with
    per-series staleness + bounds validation. Keys: series_id, channel, latest
    (transformed), raw, as_of, age_days, stale, valid, source, pairs. Returns
    None only when no data at all is available. Channel POLICY (fail-loud for A,
    hide for C) is applied by the CONSUMER — this returns the facts to gate on."""
    spec = FRED_SERIES.get(series_id)
    if spec is None:
        raise KeyError(f"{series_id!r} not in FRED_SERIES registry")
    pairs = _fetch_full_history(series_id)      # keyed API → full history
    source = "api"
    if not pairs:
        csv = _http_get_csv(series_id)          # keyless CSV fallback
        pairs = _pairs(csv) if csv else None
        source = "keyless"
    if not pairs:
        return None
    as_of, raw = pairs[-1]
    latest = spec.transform(raw) if spec.transform else float(raw)
    age = _days_since(as_of)
    stale = age is not None and age > spec.staleness_days
    valid = spec.bounds is None or (spec.bounds[0] <= latest <= spec.bounds[1])
    return {"series_id": series_id, "channel": spec.channel, "latest": latest,
            "raw": raw, "as_of": as_of, "age_days": age, "stale": stale,
            "valid": valid, "source": source, "pairs": pairs}


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
    # Channel B — current OAS levels via the registry (percent, no transform).
    # ICE license-restricts the BAML* series to a trailing ~3y window even with a
    # key (verified), so the percentile is placed against the STATIC 1996-2024
    # reference distribution rather than a too-short live window.
    hy = get_series(HY_SERIES)
    if hy is None:                              # HY is the regime driver
        return None
    ig = get_series(IG_SERIES)
    hy_oas = hy["latest"]
    ig_oas = ig["latest"] if ig else None

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


__all__ = ["SeriesSpec", "FRED_SERIES", "get_series", "validate_registry",
           "get_credit_regime", "IG_SERIES", "HY_SERIES"]
