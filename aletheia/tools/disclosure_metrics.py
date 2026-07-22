"""Secondary disclosure metrics (Phase 1) — deterministic, hybrid-sourced.

Structured signals from the XBRL/FMP secondary tags the core statements
don't surface: RPO (forward revenue), deferred-revenue billings, the
debt-maturity ladder, lease obligations, revenue disaggregation, R&D
intensity, and pension funded status. No LLM, no new fetch beyond the
cached companyfacts + the (already-wired) FMP segmentation endpoint.

Sourcing follows the hybrid-provider convention (see the memory note):
  - **Dual-source metrics** (deferred revenue, R&D) read from the cleaned
    frame, which already reflects the ticker's active provider — so the
    number matches the rest of the page.
  - **XBRL-only specialty tags** (RPO, debt-maturity ladder, lease
    obligations, pension) read via the XBRL provider's ``get_companyfact``
    escape hatch — always XBRL because FMP doesn't expose them. They
    self-suppress when the cached companyfacts lacks the tag.
  - **Revenue disaggregation** comes from FMP's segmentation endpoint
    (canonical, already wired); XBRL dimensional members are left as
    future enrichment.

Universe-wide (ungated). Every metric self-suppresses when its source is
absent — a missing tag renders "not disclosed", never fabricated. Fills
§4 (revenue quality), §6 (balance-sheet detail), §9 (forward revenue).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _f(x) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        return v if v == v else None  # drop NaN
    except (TypeError, ValueError):
        return None


def _fy_rows(df) -> List[Any]:
    """FY rows sorted oldest→newest (mirrors saas_metrics._fy)."""
    if df is None or "fiscal_year" not in getattr(df, "columns", []):
        return []
    d = df
    if "period" in d.columns:
        d = d[d["period"] == "FY"]
    d = d.sort_values("fiscal_year")
    return [r for _, r in d.iterrows()]


# ── XBRL-only specialty tags (candidate fallback lists, resolved via the
#    XBRL provider's companyfact escape hatch) ──────────────────────────────
_TAGS_RPO = ("RevenueRemainingPerformanceObligation",)
_TAGS_DEBT_LADDER = {
    "y1":         ("LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",),
    "y2":         ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo",),
    "y3":         ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree",),
    "y4":         ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour",),
    "y5":         ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive",),
    "thereafter": ("LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive",),
}
_TAGS_OP_LEASE = ("OperatingLeaseLiability",)
_TAGS_FIN_LEASE = ("FinanceLeaseLiability",)
_TAGS_PENSION = ("DefinedBenefitPlanFundedStatusOfPlan",)
# ASC-606 deferred revenue — the XBRL fallback when the cleaned frame
# doesn't carry a deferred-revenue column (it usually doesn't).
_TAGS_DEFERRED = ("ContractWithCustomerLiability",
                  "ContractWithCustomerLiabilityCurrent",
                  "DeferredRevenueCurrent", "DeferredRevenue")


def _xbrl_year(xbrl, ticker: str, tags: Tuple[str, ...],
               y: int) -> Optional[float]:
    """First non-None value across candidate tags at a specific FY."""
    for tag in tags:
        v = _f(xbrl.get_companyfact(ticker, tag, y, "FY"))
        if v is not None:
            return v
    return None


def _xbrl_latest(xbrl, ticker: str, tags: Tuple[str, ...],
                 fy: int) -> Optional[float]:
    """Most-recent value across candidate tags, tolerating a 1-year lag
    in the SEC ``fy`` attribute (re-tagging / filing timing)."""
    for y in (fy, fy - 1):
        v = _xbrl_year(xbrl, ticker, tags, y)
        if v is not None:
            return v
    return None


def _na(reason: str) -> Dict[str, Any]:
    return {"available": False, "note": reason}


def build_disclosure_metrics(calc_input) -> Dict[str, Any]:
    """Compute the secondary disclosure metrics for a ticker.

    Returns ``{"available": bool, <metric>: {...}, "provenance": {...}}``.
    ``available`` is True when at least one metric resolved. Never raises
    on missing data — each metric degrades to ``{available: False}``.
    """
    df = getattr(calc_input, "df", None)
    rows = _fy_rows(df)
    if not rows:
        return {"available": False, "note": "no FY history frame"}
    cls = getattr(calc_input, "classification", None)
    ticker = (getattr(cls, "ticker", None)
              or getattr(calc_input, "ticker", "") or "")
    latest = rows[-1]
    prior = rows[-2] if len(rows) >= 2 else None
    fy = int(latest.get("fiscal_year") or 0)
    rev = _f(latest.get("clean_Revenue")) or _f(latest.get("raw_Revenue"))

    out: Dict[str, Any] = {"available": False, "provenance": {}}
    notes: List[str] = []

    # ── XBRL-only specialty tags (always via the XBRL provider) ──────────
    xbrl = None
    try:
        from aletheia.providers.registry import get_provider
        xbrl = get_provider(name="xbrl")
    except Exception as exc:  # provider unavailable → specialty tags skip
        notes.append(f"xbrl provider unavailable: {type(exc).__name__}")

    # RPO — forward revenue (§9)
    if xbrl and ticker and fy:
        rpo = _xbrl_latest(xbrl, ticker, _TAGS_RPO, fy)
        if rpo is not None:
            cov = (rpo / rev) if rev else None
            out["rpo"] = {"available": True, "value": rpo,
                          "coverage_ratio": cov,
                          "note": "remaining performance obligations"}
            out["provenance"]["rpo"] = "xbrl"
        else:
            out["rpo"] = _na("RPO not disclosed")
    else:
        out["rpo"] = _na("no XBRL provider / fiscal year")

    # Debt-maturity ladder + wall flag (§6)
    if xbrl and ticker and fy:
        buckets: Dict[str, float] = {}
        for k, tags in _TAGS_DEBT_LADDER.items():
            v = _xbrl_latest(xbrl, ticker, tags, fy)
            if v is not None:
                buckets[k] = v
        if buckets:
            total = sum(buckets.values())
            near = buckets.get("y1", 0.0) + buckets.get("y2", 0.0)
            near_pct = (near / total) if total else None
            out["debt_maturity"] = {
                "available": True, "buckets": buckets, "total": total,
                "near_term_pct": near_pct,
                "wall_flag": bool(near_pct is not None and near_pct > 0.40),
                "note": "principal maturities by year",
            }
            out["provenance"]["debt_maturity"] = "xbrl"
        else:
            out["debt_maturity"] = _na("no maturity schedule tagged")
    else:
        out["debt_maturity"] = _na("no XBRL provider / fiscal year")

    # Lease obligations (§6)
    if xbrl and ticker and fy:
        op = _xbrl_latest(xbrl, ticker, _TAGS_OP_LEASE, fy)
        fin = _xbrl_latest(xbrl, ticker, _TAGS_FIN_LEASE, fy)
        if op is not None or fin is not None:
            total = (op or 0.0) + (fin or 0.0)
            out["leases"] = {"available": True, "operating": op,
                             "finance": fin, "total": total,
                             "note": "capitalized lease liabilities (ASC 842)"}
            out["provenance"]["leases"] = "xbrl"
        else:
            out["leases"] = _na("no lease liability tagged")
    else:
        out["leases"] = _na("no XBRL provider / fiscal year")

    # Pension funded status (§6)
    if xbrl and ticker and fy:
        pen = _xbrl_latest(xbrl, ticker, _TAGS_PENSION, fy)
        if pen is not None:
            out["pension"] = {"available": True, "funded_status": pen,
                              "underfunded": bool(pen < 0),
                              "note": "defined-benefit funded status"}
            out["provenance"]["pension"] = "xbrl"
        else:
            out["pension"] = _na("no defined-benefit plan tagged")
    else:
        out["pension"] = _na("no XBRL provider / fiscal year")

    # ── Frame-sourced (already provider-consistent) ─────────────────────
    # Deferred revenue → billings = revenue + Δ deferred (§4/§9)
    cols = list(getattr(df, "columns", []))
    deferred_col = next((c for c in ("raw_DeferredRevenue", "clean_DeferredRevenue")
                         if c in cols), None)
    dr_now = dr_prior = None
    dr_src = None
    # Frame-first (provider-consistent) …
    if deferred_col and prior is not None:
        dr_now = _f(latest.get(deferred_col))
        dr_prior = _f(prior.get(deferred_col))
        if dr_now is not None and dr_prior is not None:
            dr_src = "frame (active provider)"
    # … XBRL fallback (the frame rarely carries deferred revenue).
    if (dr_now is None or dr_prior is None) and xbrl and ticker and fy:
        dn = _xbrl_year(xbrl, ticker, _TAGS_DEFERRED, fy)
        dp = _xbrl_year(xbrl, ticker, _TAGS_DEFERRED, fy - 1)
        if dn is not None and dp is not None:
            dr_now, dr_prior, dr_src = dn, dp, "xbrl (ContractWithCustomerLiability)"
    if dr_now is not None and dr_prior is not None and rev is not None:
        billings = rev + (dr_now - dr_prior)
        out["billings"] = {"available": True, "value": billings,
                           "deferred_now": dr_now, "deferred_prior": dr_prior,
                           "vs_revenue": (billings / rev) if rev else None,
                           "note": "billings = revenue + Δ deferred revenue"}
        out["provenance"]["billings"] = dr_src
    else:
        out["billings"] = _na("deferred revenue not disclosed")

    # R&D intensity (§5/§15)
    rd = _f(latest.get("raw_RnD")) or _f(latest.get("clean_RnD"))
    if rd is not None and rev:
        out["rd_intensity"] = {"available": True, "rd": rd,
                               "pct_revenue": rd / rev,
                               "note": "R&D as % of revenue"}
        out["provenance"]["rd_intensity"] = "frame (active provider)"
    else:
        out["rd_intensity"] = _na("no R&D expense")

    # ── Revenue disaggregation — FMP segmentation (canonical) ───────────
    try:
        from aletheia.data import fmp_client
        seg = fmp_client.fetch_revenue_product_segmentation(ticker)
        mix = _latest_segment_mix(seg)
        if mix:
            out["revenue_mix"] = mix
            out["provenance"]["revenue_mix"] = "fmp segmentation"
        else:
            out["revenue_mix"] = _na("no product segmentation disclosed")
    except Exception:
        out["revenue_mix"] = _na("segmentation fetch unavailable")

    # overall availability
    metric_keys = ("rpo", "debt_maturity", "leases", "pension", "billings",
                   "rd_intensity", "revenue_mix")
    out["available"] = any(out.get(k, {}).get("available") for k in metric_keys)
    if notes:
        out["notes"] = notes
    return out


def _latest_segment_mix(seg: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Turn FMP's revenue-product-segmentation (most-recent-first, each row
    a ``{segment: revenue}`` dict) into a mix + concentration read."""
    if not seg or not isinstance(seg, list):
        return None
    row = seg[0]
    data = row.get("data") if isinstance(row, dict) else None
    if not isinstance(data, dict) or not data:
        return None
    pairs = [(k, _f(v)) for k, v in data.items() if _f(v) and _f(v) > 0]
    total = sum(v for _, v in pairs)
    if not pairs or not total:
        return None
    shares = [(k, v / total) for k, v in pairs]
    shares.sort(key=lambda x: x[1], reverse=True)
    hhi = sum(s * s for _, s in shares)  # 0–1; higher = more concentrated
    return {
        "available": True,
        "segments": [{"name": k, "revenue": v, "share": v / total} for k, v in pairs],
        "hhi": hhi,
        "top_segment": shares[0][0],
        "top_share": shares[0][1],
        "n_segments": len(pairs),
        "note": "revenue mix by product segment (HHI 0–1)",
    }


__all__ = ["build_disclosure_metrics"]
