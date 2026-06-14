"""Defensive fundamentals-quality gate (VSD plan Pre-flight / R5).

Two confirmed FMP feed-corruption classes have poisoned downstream math:

  * quarterly EPS garbage (memo $9.83/$9.91/$9.67 vs Schwab $3.75/$3.81/$4.20)
  * an impossible revenue 5Y CAGR (61.3% vs a true ~9%)

The historical-multiple feed (Build 2) and the value-source decomposition
(Build 4) both divide by annual EPS / EBITDA — a corrupted denominator there
would silently move the conviction tier. ``validate_fundamentals`` is a cheap,
read-only sanity gate that catches these classes BEFORE they propagate. It does
NOT fix the upstream feed (separate backlog item); it sets
``fundamentals_quality_flag`` so callers can fall back to ``available: False``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None  # drop NaN
    except (TypeError, ValueError):
        return None


def _fy_rows(df):
    """Most-recent-last FY rows as a list of dict-likes, or []."""
    if df is None or "clean_Revenue" not in getattr(df, "columns", []):
        return []
    d = df
    if "period" in d.columns:
        d = d[d["period"] == "FY"]
    d = d.sort_values("fiscal_year")
    return [r for _, r in d.iterrows()]


def validate_fundamentals(calc_input, quarterly_eps: Optional[List[float]] = None,
                          annual_eps: Optional[float] = None) -> Dict[str, Any]:
    """Sanity-check the annual fundamentals a multiple feed would divide by.

    Returns ``{fundamentals_quality_flag: bool, checks: {...}, reasons: [...]}``.
    The flag is True when any *hard* check fails — callers (Build 2/4) should
    then treat the affected feed as ``available: False`` rather than computing
    on bad data.

    Checks (only those whose inputs are present run; missing inputs are skipped,
    not failed):
      * ``eps_reconciles`` — latest-FY diluted EPS vs net income / diluted
        shares, within ±15%. Catches a corrupted annual EPS row.
      * ``revenue_cagr_plausible`` — trailing ≤5Y revenue CAGR is below an
        implausibility ceiling (a mature large-cap compounding >50%/yr for 5
        straight years is a data artifact, e.g. the 61.3% bug).
      * ``quarterly_eps_sums`` — when a trailing-4Q EPS list is supplied, it
        sums to the annual diluted EPS within ±10% (the FMP quarterly-garbage
        class). Skipped when no quarterly feed is passed.
    """
    checks: Dict[str, Optional[bool]] = {}
    reasons: List[str] = []

    df = getattr(calc_input, "df", None)
    rows = _fy_rows(df)

    # ── EPS reconciliation (annual) ──────────────────────────────────────────
    if rows:
        latest = rows[-1]
        eps = _f(latest.get("clean_EPS_Diluted"))
        ni = _f(latest.get("raw_NetIncome"))
        sh = _f(latest.get("clean_SharesDiluted")) or _f(latest.get("raw_SharesDiluted"))
        if eps is not None and ni is not None and sh and sh > 0 and abs(eps) > 1e-9:
            implied = ni / sh
            rel = abs(implied - eps) / abs(eps)
            ok = rel <= 0.15
            checks["eps_reconciles"] = ok
            if not ok:
                reasons.append(
                    f"FY{int(latest.get('fiscal_year', 0))} diluted EPS {eps:.2f} "
                    f"≠ NI/shares {implied:.2f} ({rel:.0%} gap) — annual EPS suspect"
                )
        else:
            checks["eps_reconciles"] = None

    # ── Revenue CAGR plausibility ────────────────────────────────────────────
    if len(rows) >= 2:
        revs = [(_f(r.get("clean_Revenue")), int(r["fiscal_year"])) for r in rows]
        revs = [(v, y) for v, y in revs if v is not None and v > 0]
        if len(revs) >= 2:
            end_v, end_y = revs[-1]
            target = end_y - 5
            start_v, start_y = min(revs, key=lambda t: abs(t[1] - target))
            if start_y < end_y and start_v > 0:
                cagr = (end_v / start_v) ** (1.0 / (end_y - start_y)) - 1.0
                ok = cagr <= 0.50  # >50%/yr sustained over the window = artifact
                checks["revenue_cagr_plausible"] = ok
                if not ok:
                    reasons.append(
                        f"trailing revenue CAGR {cagr:.0%} over FY{start_y}-FY{end_y} "
                        f"implausible (start ${start_v/1e6:,.0f}M likely wrong-era base)"
                    )

    # ── Quarterly EPS sum (only when a quarterly feed is supplied) ───────────
    if quarterly_eps:
        q = [v for v in (_f(x) for x in quarterly_eps) if v is not None][:4]
        ann = annual_eps
        if ann is None and rows:
            ann = _f(rows[-1].get("clean_EPS_Diluted"))
        if len(q) == 4 and ann is not None and abs(ann) > 1e-9:
            qsum = sum(q)
            rel = abs(qsum - ann) / abs(ann)
            ok = rel <= 0.10
            checks["quarterly_eps_sums"] = ok
            if not ok:
                reasons.append(
                    f"Σ trailing 4Q EPS {qsum:.2f} ≠ annual {ann:.2f} "
                    f"({rel:.0%} gap) — quarterly EPS feed suspect"
                )

    flag = any(v is False for v in checks.values())
    return {
        "fundamentals_quality_flag": flag,
        "checks": checks,
        "reasons": reasons,
    }
