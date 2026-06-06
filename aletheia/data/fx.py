"""FX conversion — convert non-USD filer financials to USD.

Foreign filers report in their home currency (Novo Nordisk DKK, ASML EUR,
TSM TWD), but they trade in the US as ADRs priced in USD. The DCF, multiple
decomposition, reverse-DCF and screening all compare intrinsic values /
multiples against that USD price, so the financials MUST be converted to USD
first — otherwise a DKK intrinsic value compared to a USD price produces a
nonsensical margin of safety (NVO: +1,967% before this layer).

Conversion is applied at the calc-input boundary (make_calc_input) and the
financials-display builder. It is idempotent: a converted DataFrame is tagged
via ``df.attrs`` so it is never double-converted when the same frame flows
through multiple call sites.

Approach: a single current spot rate is applied to every monetary column.
That's exactly right for the DCF (which values against today's price) and a
reasonable approximation for the historical display. Per-year period-end
rates would be more precise for the multi-year history but require a
historical FX series — a future refinement.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Optional, Tuple

import pandas as pd

from aletheia.data.market_data import get_fx_to_usd

_FMP_DIR = "valuation_data/macro/fmp"
_ATTR_FLAG = "fx_converted_to_usd"

# Monetary columns live under these cleaned-record namespaces.
_MONETARY_PREFIXES = ("raw_", "clean_", "derived_")
# Substrings that mark a prefixed column as NON-currency (counts, ratios,
# percentages, rates, scores) — these must NOT be scaled by the FX rate.
_NON_MONETARY = (
    "shares", "_pct", "pct", "taxrate", "rate", "roic", "roe", "margin",
    "dilution", "domain_score", "beneish", "sloan", "quality", "_source",
    "_flagged", "z_score", "ratio", "payout",
)
# Currency columns that don't carry a namespace prefix but must convert.
_FORCE_MONETARY = {"epv", "epv_per_share"}


def get_reporting_currency(ticker: str) -> str:
    """Reporting currency for a ticker, from the FMP income cache
    (``reportedCurrency``). Defaults to ``USD`` when unknown."""
    for pat in (f"{ticker.upper()}__income_annual.json",
                f"{ticker.upper()}__income_quarter.json"):
        p = os.path.join(_FMP_DIR, pat)
        if os.path.exists(p):
            try:
                d = json.load(open(p))
                rows = d if isinstance(d, list) else d.get("data", [])
                for r in rows:
                    c = r.get("reportedCurrency")
                    if c:
                        return str(c).upper()
            except Exception:
                pass
    return "USD"


def _is_monetary(col: str) -> bool:
    if col in _FORCE_MONETARY:
        return True
    if not col.startswith(_MONETARY_PREFIXES):
        return False
    low = col.lower()
    return not any(s in low for s in _NON_MONETARY)


def convert_financials_to_usd(
    df: pd.DataFrame, ticker: str,
) -> Tuple[pd.DataFrame, str, float]:
    """Return ``(df_usd, currency, rate)``.

    Scales every monetary column by the current spot FX rate when the ticker
    reports in a non-USD currency. Idempotent — a frame already converted (or
    one that reports in USD, or one where the FX rate is unavailable) is
    returned unchanged. ``rate`` is USD-per-foreign-unit (1.0 = no conversion).
    """
    if df is None or getattr(df, "empty", True):
        return df, "USD", 1.0
    if df.attrs.get(_ATTR_FLAG):
        return df, df.attrs.get("fx_currency", "USD"), df.attrs.get("fx_rate", 1.0)

    currency = get_reporting_currency(ticker)
    if currency == "USD":
        df.attrs[_ATTR_FLAG] = True
        df.attrs["fx_currency"] = "USD"
        df.attrs["fx_rate"] = 1.0
        return df, "USD", 1.0

    rate = get_fx_to_usd(currency)
    if not rate or rate == 1.0:
        # Conversion unavailable — leave native and DON'T mark converted, so
        # the caller can flag "FX unavailable" rather than trust the numbers.
        return df, currency, 1.0

    out = df.copy()
    for col in out.columns:
        if _is_monetary(col):
            out[col] = pd.to_numeric(out[col], errors="coerce") * rate
    out.attrs[_ATTR_FLAG] = True
    out.attrs["fx_currency"] = currency
    out.attrs["fx_rate"] = rate
    return out, currency, rate


__all__ = [
    "get_reporting_currency",
    "convert_financials_to_usd",
]
