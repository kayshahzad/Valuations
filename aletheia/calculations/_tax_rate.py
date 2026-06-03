"""Tax-rate fallback resolver — A11 stabilization.

The four calc-layer entry points (DCFEngine.run, ReverseDCF.run,
MultipleDecomposition.run, ScreeningEngine._compute_metrics) all
historically did:

    tax_rate = get("clean_CashTaxRate") or get("clean_GAAP_TaxRate") or 0.21

The trailing ``or 0.21`` is the A11 anomaly surface: when both cleaned
rates are missing or NaN-coerced, the function silently used the U.S.
federal statutory rate. That's wrong for foreign filers (Irish MDT
~14-16%, EUR ASML, TWD TSM) and wrong for special-situation years
(NOL release, DTA reversal, restructuring). It is also silent — no
audit-log breadcrumb pointing at the upstream data gap.

This module replaces that trailing fallback with a deterministic
fallback list (consistent with the team preference for
"fallback-list-over-overrides"):

    1. clean_CashTaxRate         (current-FY cash basis)
    2. clean_GAAP_TaxRate        (current-FY accrual basis)
    3. company_fy_effective_tax_rate(df)   (multi-year company history)
    4. US_STATUTORY = 0.21       (last-resort fallback)

Each step is logged via the standard guard audit log so the analyst
can see exactly which source produced the rate that fed into the
calc. The ``resolved_source`` is also returned so callers can attach
it to their result objects.
"""

from __future__ import annotations

import logging
import math
from typing import Optional, Tuple

import pandas as pd


log = logging.getLogger(__name__)


# Public constants
US_STATUTORY: float = 0.21
DEFAULT_LOOKBACK_YEARS: int = 5
MIN_LOOKBACK_YEARS_FOR_AVG: int = 3

# Below this, a single-year effective rate is almost certainly a one-time
# artifact (NOL release, DTA reversal, large credits, negative/benefit
# years) rather than the rate that persists in a perpetuity valuation.
# Foreign filers with genuinely low statutory rates (Irish MDT ~14-16%,
# ASML, TSM) sit ABOVE this floor, so they are not misclassified.
MIN_NORMAL_RATE: float = 0.10
# A current-FY rate this many percentage points off the multi-year mean is
# treated as an outlier year and normalized to the mean.
TAX_OUTLIER_DEVIATION: float = 0.10


def _is_usable_rate(value) -> bool:
    """A tax rate is usable when finite and inside the Tier-3 range
    bound for tax_rate. We keep this strict here (the resolver is the
    one place that gets to decide) so callers don't reimplement the
    test."""
    if value is None:
        return False
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    if math.isnan(f) or math.isinf(f):
        return False
    return -1.0 <= f <= 1.0


def company_fy_effective_tax_rate(
    df: Optional[pd.DataFrame],
    fy: int,
    lookback_years: int = DEFAULT_LOOKBACK_YEARS,
) -> Optional[float]:
    """Mean of the company's own historical effective tax rates.

    Walks the last ``lookback_years`` of FY rows up to ``fy``
    (inclusive). For each row prefers ``clean_CashTaxRate``; falls back
    to ``clean_GAAP_TaxRate``; drops rows where neither is usable.

    Returns the simple mean. Returns ``None`` when fewer than
    ``MIN_LOOKBACK_YEARS_FOR_AVG`` usable rows exist — the caller is
    expected to fall through to the next step in the chain.

    The resolver is robust to the caller's df-slicing convention: rows
    are deduplicated by ``fiscal_year`` (keeping the last occurrence in
    sort order) so passing a TTM-including df vs. an FY-only df yields
    the same answer. The four calc-layer call sites slice the df
    differently (DCFEngine uses ``df_fy``; the others pass the full
    df), and the resolved tax rate must be ticker-stable not
    call-site-stable so WACC stays consistent across the three
    valuation engines.
    """
    if df is None or "fiscal_year" not in df.columns:
        return None

    window = (
        df[df["fiscal_year"] <= fy]
        .sort_values("fiscal_year")
        .drop_duplicates(subset=["fiscal_year"], keep="last")
        .tail(lookback_years)
    )
    rates: list[float] = []
    for _, row in window.iterrows():
        cash = row.get("clean_CashTaxRate")
        gaap = row.get("clean_GAAP_TaxRate")
        chosen = cash if _is_usable_rate(cash) else gaap
        if _is_usable_rate(chosen):
            rates.append(float(chosen))

    if len(rates) < MIN_LOOKBACK_YEARS_FOR_AVG:
        return None
    return sum(rates) / len(rates)


def resolve_tax_rate(
    *,
    ticker: str,
    fn: str,
    df: Optional[pd.DataFrame],
    fy: int,
    cash_tax_rate: Optional[float],
    gaap_tax_rate: Optional[float] = None,
    lookback_years: int = DEFAULT_LOOKBACK_YEARS,
) -> Tuple[float, str]:
    """Resolve the tax rate via the A11 fallback chain.

    Returns ``(rate, source)`` where ``source`` is one of:

      - ``"cash"``       current-FY ``clean_CashTaxRate``
      - ``"gaap"``       current-FY ``clean_GAAP_TaxRate``
      - ``"company_fy"`` multi-year mean from company's own history
      - ``"statutory"``  ``US_STATUTORY`` (0.21) — last resort

    The ``cash`` and ``gaap`` inputs are passed in (rather than read
    from ``df``) because each call-site fetches them via its own
    safe-coercion path and we don't want to re-implement that here.

    The ``company_fy`` and ``statutory`` cases log via the standard
    Python logger so the audit trail captures every silent-fallback
    event without coupling this module to the guards audit log.
    """
    # Current-FY rate (cash preferred, then GAAP).
    current_rate = None
    current_source = None
    if _is_usable_rate(cash_tax_rate):
        current_rate, current_source = float(cash_tax_rate), "cash"
    elif _is_usable_rate(gaap_tax_rate):
        current_rate, current_source = float(gaap_tax_rate), "gaap"

    company_rate = company_fy_effective_tax_rate(df, fy, lookback_years)

    if current_rate is not None:
        # Normalize anomalous single-year rates. A DCF prices a perpetuity,
        # so a one-time benefit (AVGO TTM 1.8% / FY2025 −1.7%) or an outlier
        # year (AVGO FY2024 37.8%) must not become the perpetual tax rate.
        # When the current rate is below the one-time floor OR sits far from
        # the company's own multi-year mean, defer to that normalized mean.
        is_anomalous = (
            current_rate < MIN_NORMAL_RATE
            or (company_rate is not None
                and abs(current_rate - company_rate) >= TAX_OUTLIER_DEVIATION)
        )
        if is_anomalous and company_rate is not None:
            log.info(
                "tax_rate_normalize: ticker=%s fn=%s source=company_fy_normalized "
                "current=%.4f (%s) normalized=%.4f fy=%d lookback=%d "
                "reason=current_fy_rate_anomalous",
                ticker, fn, current_rate, current_source, company_rate,
                fy, lookback_years,
            )
            return company_rate, "company_fy_normalized"
        if is_anomalous and company_rate is None:
            # Anomalous current rate but too little history to normalize —
            # fall back to statutory rather than ship a one-time figure.
            log.warning(
                "tax_rate_normalize: ticker=%s fn=%s source=statutory "
                "current=%.4f (%s) reason=anomalous_current_no_history fy=%d",
                ticker, fn, current_rate, current_source, fy,
            )
            return US_STATUTORY, "statutory"
        return current_rate, current_source

    if company_rate is not None:
        log.info(
            "tax_rate_fallback: ticker=%s fn=%s source=company_fy "
            "rate=%.4f fy=%d lookback=%d "
            "reason=current_fy_cash_and_gaap_unavailable",
            ticker, fn, company_rate, fy, lookback_years,
        )
        return company_rate, "company_fy"

    log.warning(
        "tax_rate_fallback: ticker=%s fn=%s source=statutory rate=%.4f "
        "fy=%d reason=no_cleaned_rate_and_insufficient_history "
        "(< %d usable historical FY rows)",
        ticker, fn, US_STATUTORY, fy, MIN_LOOKBACK_YEARS_FOR_AVG,
    )
    return US_STATUTORY, "statutory"


__all__ = [
    "US_STATUTORY",
    "DEFAULT_LOOKBACK_YEARS",
    "MIN_LOOKBACK_YEARS_FOR_AVG",
    "company_fy_effective_tax_rate",
    "resolve_tax_rate",
]
