"""
aletheia/tools/reality_checks.py

Plausibility checks that interrogate a DCF projection against external
constraints — economy-wide GDP, addressable market size, historical
analogs. Returns deterministic structured results that the Deep Dive view
renders as a "Reality Checks" section.

Design contract
---------------
Every check function returns a `RealityCheckResult` with:
  - severity: "info" | "caution" | "warning" | "critical" | "n_a" | "skipped"
  - headline: short label (e.g., "Year-10 revenue / US GDP: 4.2%")
  - detail: 1-3 sentences explaining what was computed and why it landed
            at this severity
  - data: dict of the actual numbers used (so the UI can render a table
          beneath the headline if useful)

Schema-mismatched tickers (banks, insurers, utilities, conglomerates) get
severity="n_a" with an explicit message about why standard-DCF-based
checks don't apply. They are NOT silently hidden.

Currently implemented
---------------------
- gdp_check: Year-10 base-case projected revenue ÷ projected US GDP at
  year 10. Severity bands: 3% caution / 5% warning / 10% critical
  (locked 2026-05-07). Tighter thresholds were considered (1.5%) but
  rejected because real companies (AAPL, MSFT, AMZN) already operate at
  1-2% of GDP without being implausible.

Future
------
- market_implied_check: Feature 2 — interpretation bands on implied vs
  historical CAGR.
- tam_check: Feature 3 — year-10 share of curated industry TAM.
- inflection_check: Feature 4 — decomposed pre/post-inflection CAGR.
- analog_check: Feature 5 — pattern-based historical analog matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


SEVERITY_RANK = {
    "skipped":  -1,
    "n_a":       0,
    "info":      1,
    "caution":   2,
    "warning":   3,
    "critical":  4,
}


@dataclass
class RealityCheckResult:
    name: str
    severity: str
    headline: str
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


# ── Schema gate ───────────────────────────────────────────────────────────

def _is_fcff_compatible(ticker: str) -> bool:
    """
    Reality checks require a standard FCFF DCF projection. Banks, insurers,
    utilities, and conglomerates have schemas where these checks aren't
    meaningful — return False so the caller can render an explicit
    "not applicable" message.
    """
    try:
        from config.ticker_classification import get_extended_universe
        cls = get_extended_universe().get(ticker.upper())
        if cls is None:
            # Unknown / freshly-added ticker — assume fcff_compatible (default).
            return True
        return cls.business_model == "fcff_compatible"
    except Exception:
        return True


def _business_model_label(ticker: str) -> str:
    """Human-readable framework name for the schema-mismatch message."""
    try:
        from config.ticker_classification import get_extended_universe
        cls = get_extended_universe().get(ticker.upper())
        if cls is None:
            return "this filer"
        bm = cls.business_model
        sector = cls.sector or "this filer"
        if bm == "ddm_required":
            return f"insurance/managed-care ({sector})"
        if bm == "embedded_value_required":
            return f"life-insurance ({sector})"
        if bm == "routing_required":
            return f"bank/utility/conglomerate ({sector})"
        if bm == "mlp_required":
            return f"midstream MLP ({sector})"
        return sector
    except Exception:
        return "this filer"


# ── Year-10 revenue projection ────────────────────────────────────────────

def project_year_n_revenue(
    base_revenue: float,
    cagr_y1_5: float,
    cagr_y6_10: float,
    years: int = 10,
) -> Optional[float]:
    """
    Project revenue forward N years using the standard two-stage growth
    schedule the DCF engine uses (Y1-5 and Y6-10 CAGRs).

    For year 10 specifically: base × (1 + g₁)⁵ × (1 + g₂)⁵.
    For shorter horizons: only as many years of g₁ apply.
    """
    if base_revenue is None or base_revenue <= 0:
        return None
    if cagr_y1_5 is None:
        return None
    cagr_y6_10 = cagr_y6_10 if cagr_y6_10 is not None else cagr_y1_5

    if years <= 5:
        return float(base_revenue * (1.0 + cagr_y1_5) ** years)
    return float(
        base_revenue
        * (1.0 + cagr_y1_5) ** 5
        * (1.0 + cagr_y6_10) ** (years - 5)
    )


# ── Feature 1: GDP comparison ────────────────────────────────────────────

def gdp_check(
    ticker: str,
    base_revenue: float,
    cagr_y1_5: float,
    cagr_y6_10: float,
    *,
    horizon_years: int = 10,
    today: Optional[date] = None,
) -> RealityCheckResult:
    """
    Project year-`horizon_years` revenue from the base case and compare to
    projected US nominal GDP at that point. Flag if the implied share of
    economic output crosses configured thresholds.

    `base_revenue` should be in USD ($, not $B) for unit consistency with
    `get_gdp_projection` (returns $B internally and we convert).
    """
    today = today or date.today()
    horizon_year = today.year + horizon_years

    # Schema gate
    if not _is_fcff_compatible(ticker):
        return RealityCheckResult(
            name="gdp",
            severity="n_a",
            headline="GDP comparison not applicable",
            detail=(
                f"This ticker is classified as {_business_model_label(ticker)}, "
                "which doesn't use the standard FCFF DCF revenue projection. "
                "GDP-share checks are only meaningful for filers whose forward "
                "revenue path comes from the standard DCF engine."
            ),
        )

    # Project revenue
    proj_rev = project_year_n_revenue(base_revenue, cagr_y1_5, cagr_y6_10, horizon_years)
    if proj_rev is None:
        return RealityCheckResult(
            name="gdp",
            severity="skipped",
            headline="GDP comparison skipped",
            detail="Insufficient projection inputs (base revenue or CAGR missing).",
        )

    # GDP at horizon — convert curated $B to $ for the ratio
    from aletheia.data.historical_macro import get_gdp_projection
    gdp_b = get_gdp_projection(horizon_year)
    if gdp_b is None:
        return RealityCheckResult(
            name="gdp",
            severity="skipped",
            headline=f"GDP projection unavailable for {horizon_year}",
            detail=(
                f"`config/gdp_projections.py` doesn't cover year {horizon_year}. "
                "Extend the projection table or shorten the horizon."
            ),
        )
    gdp = gdp_b * 1e9   # USD

    share = proj_rev / gdp

    # Threshold bands
    from config.gdp_projections import GDP_THRESHOLDS
    if share >= GDP_THRESHOLDS["critical"]:
        severity = "critical"
    elif share >= GDP_THRESHOLDS["warning"]:
        severity = "warning"
    elif share >= GDP_THRESHOLDS["caution"]:
        severity = "caution"
    else:
        severity = "info"

    headline = f"Year-{horizon_years} revenue ÷ US GDP at {horizon_year}: {share*100:.2f}%"
    if severity == "critical":
        detail = (
            f"At base-case CAGR ({cagr_y1_5*100:.1f}% y1-5, "
            f"{cagr_y6_10*100:.1f}% y6-10), year-{horizon_years} revenue reaches "
            f"${proj_rev/1e9:,.0f}B — {share*100:.1f}% of projected US GDP "
            f"(${gdp_b:,.0f}B in {horizon_year}). At or above {GDP_THRESHOLDS['critical']*100:.0f}% "
            "implies the company would be a non-trivial fraction of the economy. "
            "Almost no company in history has sustained this scale. "
            "The base-case growth assumptions almost certainly need to be revisited."
        )
    elif severity == "warning":
        detail = (
            f"Year-{horizon_years} revenue projects to ${proj_rev/1e9:,.0f}B — "
            f"{share*100:.1f}% of projected US GDP. The historical maximum for "
            "any single company is ~3% (Walmart at peak in the early 2000s). "
            "Above the historical-maximum threshold; growth assumptions warrant scepticism."
        )
    elif severity == "caution":
        detail = (
            f"Year-{horizon_years} revenue projects to ${proj_rev/1e9:,.0f}B — "
            f"{share*100:.1f}% of projected US GDP. Real companies do reach this "
            "scale (AAPL today is ~1.4% of US GDP, MSFT ~1.3%). Above the "
            "caution threshold mainly worth examining: are the growth assumptions "
            "consistent with the implicit market-share path?"
        )
    else:
        detail = (
            f"Year-{horizon_years} revenue projects to ${proj_rev/1e9:,.0f}B — "
            f"{share*100:.2f}% of projected US GDP, well within historical norms "
            "for large public companies."
        )

    return RealityCheckResult(
        name="gdp",
        severity=severity,
        headline=headline,
        detail=detail,
        data={
            "horizon_years":         horizon_years,
            "horizon_year":          horizon_year,
            "base_revenue_usd":      base_revenue,
            "cagr_y1_5":             cagr_y1_5,
            "cagr_y6_10":            cagr_y6_10,
            "projected_revenue_usd": proj_rev,
            "projected_gdp_usd":     gdp,
            "revenue_to_gdp":        share,
            "thresholds":            dict(GDP_THRESHOLDS),
        },
    )


# ── Aggregator ────────────────────────────────────────────────────────────

def run_all_checks(
    ticker: str,
    base_revenue: Optional[float],
    cagr_y1_5: Optional[float],
    cagr_y6_10: Optional[float],
) -> List[RealityCheckResult]:
    """
    Run every implemented reality check for a ticker. Future checks
    (TAM, inflection, analogs) will be added here as they ship.

    Order of precedence per check:
      1. Schema gate — if ticker is non-fcff_compatible, return n_a with
         explicit messaging (visible, not silently hidden).
      2. Input validity — if the inputs the check needs aren't available
         (e.g., no DCF assumptions saved for a pending ticker), return
         skipped.
      3. Run the check normally.
    """
    results: List[RealityCheckResult] = []

    # GDP check
    if not _is_fcff_compatible(ticker):
        # Schema gate fires regardless of whether inputs are present.
        results.append(RealityCheckResult(
            name="gdp",
            severity="n_a",
            headline="GDP comparison not applicable",
            detail=(
                f"This ticker is classified as {_business_model_label(ticker)}, "
                "which doesn't use the standard FCFF DCF revenue projection. "
                "GDP-share checks are only meaningful for filers whose forward "
                "revenue path comes from the standard DCF engine."
            ),
        ))
    elif base_revenue and cagr_y1_5 is not None:
        results.append(gdp_check(
            ticker, base_revenue, cagr_y1_5,
            cagr_y6_10 if cagr_y6_10 is not None else cagr_y1_5,
        ))
    else:
        results.append(RealityCheckResult(
            name="gdp",
            severity="skipped",
            headline="GDP check skipped",
            detail=(
                "No DCF projection available for this ticker — base revenue or "
                "growth assumptions are missing. Run the agent pipeline "
                "(▶ Run agents) on this ticker to populate."
            ),
        ))

    return results


def overall_severity(results: List[RealityCheckResult]) -> str:
    """Aggregate severity for the section header (max of all checks)."""
    if not results:
        return "info"
    return max(results, key=lambda r: SEVERITY_RANK.get(r.severity, 0)).severity
