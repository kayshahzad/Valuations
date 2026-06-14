"""CADS — Cash Available for Debt Service (Valuation-Methods plan, Phase 2).

A credit/coverage measure, NOT a valuation. In a long-only book its job is to
catch the cases where the *valuation* looks fine but the firm can't self-fund its
debt out of operations — the EQIX capex-sinkhole (EBITDA grows but the build-out
CapEx outgrows it, CADS turns negative) and ABT/EXAS-style coverage bears — the
trade-off note's "survive a 2-σ bad case", not LBO affordability.

CADS = CFO − CapEx. CFO/interest aren't in the calc frame, so we use the
EBITDA-based credit numerator that IS available and is the standard coverage
measure: **CADS = EBITDA − CapEx** (cash generation before financing/taxes).
Debt service is proxied from gross long-term debt × a default rate plus the
current portion when disclosed (the `kd`-proxy caveat applies — coverage is
directional, a screen, not a precise DSCR).

CF-R5 trigger: coverage < 1.0 (or negative CADS) fires deterministically as a
§9 failure-mode flag.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from aletheia.tools.business_analysis import net_debt_series

_DEFAULT_DEBT_RATE = 0.05   # gross-debt interest proxy (kd-proxy caveat)


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def build_cads_coverage(calc) -> Dict[str, Any]:
    """CADS (= EBITDA − CapEx) per FY + a debt-service coverage screen.

    Returns ``{available, cads_series, latest, debt_service, coverage,
    years_to_repay, trigger, trigger_reason, trend, notes}``. ``trigger`` is the
    CF-R5 deterministic flag (coverage < 1.0 or CADS < 0) destined for §9.
    """
    out: Dict[str, Any] = {"available": False}
    df = getattr(calc, "df", None)
    cols = getattr(df, "columns", [])
    if df is None or "derived_EBITDA" not in cols:
        return out

    # Per-FY CADS = EBITDA − CapEx, aligned to the shared net-debt series.
    nds = {x["year"]: x for x in net_debt_series(calc)}
    d = df
    if "period" in cols:
        d = d[d["period"] == "FY"]
    d = d.dropna(subset=["fiscal_year"]).sort_values("fiscal_year")

    series: List[Dict[str, Any]] = []
    for _, r in d.iterrows():
        y = int(r["fiscal_year"])
        eb = _f(r.get("derived_EBITDA"))
        cx = _f(r.get("derived_CapEx")) or _f(r.get("raw_CapEx"))
        if eb is None or cx is None:
            continue
        series.append({"year": y, "ebitda": eb, "capex": cx, "cads": eb - cx})
    if len(series) < 2:
        out["notes"] = "insufficient EBITDA/CapEx history"
        return out

    latest = series[-1]
    cads = latest["cads"]

    # Debt service proxy: gross LTD × default rate + current portion (if any).
    nd = nds.get(latest["year"], {})
    ltd = _f(nd.get("ltd")) or 0.0
    net_debt = _f(nd.get("net_debt"))
    last_row = d.iloc[-1]
    cur_portion = _f(last_row.get("raw_CurrentPortionLongTermDebt")) or 0.0
    debt_service = ltd * _DEFAULT_DEBT_RATE + cur_portion

    coverage = (cads / debt_service) if debt_service > 0 else float("inf")
    years_to_repay = (net_debt / cads) if (cads and cads > 0 and net_debt and net_debt > 0) else None

    # CF-R5 trigger.
    trigger = (cads < 0) or (coverage < 1.0)
    if cads < 0:
        reason = (f"CADS negative (EBITDA−CapEx = {cads/1e6:,.0f}M, FY{latest['year']}) "
                  f"— operations do not self-fund the capex base, let alone debt service "
                  f"(capex-sinkhole)")
    elif coverage < 1.0:
        reason = (f"CADS coverage {coverage:.2f}× < 1.0 — cash after capex below "
                  f"estimated debt service (~{debt_service/1e6:,.0f}M)")
    else:
        reason = None

    cads_vals = [s["cads"] for s in series[-3:]]
    deteriorating = len(cads_vals) >= 2 and cads_vals[-1] < cads_vals[0]

    out.update({
        "available": True,
        "cads_series": [(s["year"], round(s["cads"], 0)) for s in series],
        "latest": {"year": latest["year"], "cads": cads,
                   "ebitda": latest["ebitda"], "capex": latest["capex"]},
        "debt_service_est": debt_service,
        "coverage": coverage if coverage != float("inf") else None,
        "years_to_repay": years_to_repay,
        "trigger": trigger,
        "trigger_reason": reason,
        "deteriorating": deteriorating,
        "notes": ["debt service proxied from gross LTD × 5% + current portion "
                  "(kd-proxy; directional screen, not a precise DSCR)"],
    })
    return out
