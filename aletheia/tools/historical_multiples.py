"""Historical own-multiple feed (VSD plan Build 2 / gap G3).

The value-source decomposition's ``mult_contrib`` reports two target multiples:
the *justified* (NorthWestern/creation) multiple — already computed elsewhere —
and the company's own *historical* (reversion) multiple, built here. We take a
year-end price per fiscal year, divide by that year's cleaned EPS / EBITDA, and
median over the trailing ≤5 fiscal years.

REIT handling (decision #4): EV/EBITDA and P/E are GAAP-distorted and
meaningless for REITs (the EQIX lesson), so we do not compute them at all for
``reit_required`` names; P/AFFO would require an AFFO time series we don't carry
(AFFO is a single curated config input), so REITs return ``available: False``
and the decomposition falls back to the justified read.

Input gate (R5): before dividing, ``validate_fundamentals`` must pass; a
flagged feed returns ``available: False`` rather than computing on bad data.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional

from aletheia.data.fundamentals_validation import validate_fundamentals


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _year_end_prices(ticker: str, years: List[int]):
    """Map each fiscal year → that year's last available close. Fetches once;
    returns {} on any failure (callers degrade to available: False)."""
    try:
        import yfinance as yf
        span = max(years) - min(years) + 2
        hist = yf.Ticker(ticker.upper()).history(period=f"{max(span, 6)}y")["Close"]
        if hist is None or len(hist) == 0:
            return {}
        out: Dict[int, float] = {}
        for y in years:
            # last close on/before Dec-31 of the fiscal year
            sub = hist[hist.index.year <= y]
            if len(sub) > 0:
                out[y] = float(sub.iloc[-1])
        return out
    except Exception:
        return {}


def build_historical_multiples(calc_input) -> Dict[str, Any]:
    """Trailing ≤5Y median of the company's own P/E and EV/EBITDA (non-REIT).

    Returns ``{pe_5y_avg, ev_ebitda_5y_avg, p_affo_5y_avg, years_used,
    source, available}``. ``available`` is False for REITs, for flagged
    fundamentals (R5), or when prices/denominators can't be assembled.
    """
    out: Dict[str, Any] = {
        "available": False, "pe_5y_avg": None, "ev_ebitda_5y_avg": None,
        "p_affo_5y_avg": None, "years_used": [], "source": None,
    }

    cls = getattr(calc_input, "classification", None)
    business_model = getattr(cls, "business_model", None)
    ticker = getattr(cls, "ticker", None) or ""

    # REIT branch (decision #4): GAAP P/E & EV/EBITDA meaningless; no AFFO
    # time series → nothing computable here. Fall back to justified read.
    if business_model == "reit_required":
        out["source"] = "REIT: P/E & EV/EBITDA skipped (GAAP-distorted); no AFFO series"
        return out

    # Input gate (R5).
    vf = validate_fundamentals(calc_input)
    if vf.get("fundamentals_quality_flag"):
        out["source"] = "fundamentals_quality_flag set — " + "; ".join(vf.get("reasons", []))
        return out

    df = getattr(calc_input, "df", None)
    if df is None or "clean_Revenue" not in getattr(df, "columns", []):
        return out
    d = df
    if "period" in d.columns:
        d = d[d["period"] == "FY"]
    d = d.sort_values("fiscal_year").tail(5)
    rows = [r for _, r in d.iterrows()]
    if len(rows) < 3:
        out["source"] = "insufficient FY history (<3)"
        return out

    years = [int(r["fiscal_year"]) for r in rows]
    prices = _year_end_prices(ticker, years)
    if not prices:
        out["source"] = "year-end prices unavailable"
        return out

    pe_vals: List[float] = []
    ev_ebitda_vals: List[float] = []
    used: List[int] = []
    for r in rows:
        y = int(r["fiscal_year"])
        px = prices.get(y)
        if px is None:
            continue
        eps = _f(r.get("clean_EPS_Diluted"))
        ebitda = _f(r.get("derived_EBITDA")) or _f(r.get("clean_EBITDA"))
        shares = _f(r.get("clean_SharesDiluted")) or _f(r.get("raw_SharesDiluted"))
        net_debt = _f(r.get("derived_NetDebt")) or 0.0
        if eps is not None and eps > 0:
            pe_vals.append(px / eps)
        if ebitda is not None and ebitda > 0 and shares and shares > 0:
            ev = px * shares + net_debt
            ev_ebitda_vals.append(ev / ebitda)
        used.append(y)

    if not pe_vals and not ev_ebitda_vals:
        out["source"] = "no usable year-end denominators"
        return out

    out.update({
        "available": True,
        "pe_5y_avg": statistics.median(pe_vals) if pe_vals else None,
        "ev_ebitda_5y_avg": statistics.median(ev_ebitda_vals) if ev_ebitda_vals else None,
        "years_used": used,
        "source": f"median of {len(used)} FY year-end multiples (own history)",
    })
    return out
