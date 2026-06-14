"""SaaS analysis overlay — deterministic KPIs (plan Build B).

Subscription-economics signals a buy-side reviewer demands before underwriting
a SaaS moat, computed from the cleaned DB only (no LLM). Strictly gated by
``is_saas_company`` so non-SaaS reports are untouched. Every metric
**self-suppresses when its assumption fails** — a wrong signal is worse than a
missing one — and names its blind spots rather than fabricating.

What's deterministic in v1: owner's-earnings FCF (FCF net of SBC), SBC ratios,
magic number / sales efficiency (only when S&M is separable from SG&A),
CAC-payback proxy, gross-margin trend, max drawdown + correlation, and a
SaaS-gated cyclicality reconciliation flag.

Deferred-dependent metrics (billings, deferred-revenue trend, RPO) are emitted
as explicit ``data_gap`` entries when the column isn't in the calc frame —
ADBE's frame has no deferred-revenue field today; those activate automatically
if it gets wired, or come from the Build-C extraction (v2).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _fy(df):
    if df is None or "clean_Revenue" not in getattr(df, "columns", []):
        return []
    d = df
    if "period" in d.columns:
        d = d[d["period"] == "FY"]
    d = d.sort_values("fiscal_year")
    return [r for _, r in d.iterrows()]


def build_saas_metrics(calc_input, market_cap: Optional[float] = None,
                       price: Optional[float] = None) -> Dict[str, Any]:
    """SaaS KPI overlay. Returns ``{available: False}`` for non-SaaS names."""
    from config.ticker_classification import is_saas_company
    cls = getattr(calc_input, "classification", None)
    if not is_saas_company(cls):
        return {"available": False}

    out: Dict[str, Any] = {"available": True, "notes": [], "data_gaps": [], "flags": []}
    df = getattr(calc_input, "df", None)
    cols = list(getattr(df, "columns", []))
    rows = _fy(df)
    if len(rows) < 2:
        return {"available": False}
    latest, prior = rows[-1], rows[-2]
    ticker = getattr(cls, "ticker", "") or ""

    rev = _f(latest.get("clean_Revenue"))
    rev_prior = _f(prior.get("clean_Revenue"))

    # ── Owner's-earnings FCF = FCF less the economic cost of dilution (SBC) ──
    fcf = _f(latest.get("derived_FCF")) or _f(latest.get("clean_FCF"))
    sbc = _f(latest.get("clean_SBC"))
    if fcf is not None and sbc is not None:
        owner_fcf = fcf - sbc
        out["owners_earnings"] = {
            "fcf": fcf,
            "sbc": sbc,
            "owners_earnings_fcf": owner_fcf,
            "sbc_pct_fcf": _f(latest.get("clean_SBC_PctFCF")),
            "sbc_pct_revenue": (sbc / rev) if (rev and rev > 0) else None,
            "label": "FCF less the economic cost of dilution (SBC) — not a correction of an error",
        }
        if market_cap and market_cap > 0:
            out["owners_earnings"]["fcf_yield"] = fcf / market_cap
            out["owners_earnings"]["owners_earnings_yield"] = owner_fcf / market_cap
    else:
        out["data_gaps"].append("owners_earnings (FCF or SBC missing)")

    # ── M&A break signal (drives the billings guard) ────────────────────────
    ma_in_window = False
    try:
        from aletheia.tools.business_analysis import build_growth_decomposition
        gd = build_growth_decomposition(calc_input)
        ma_in_window = bool(gd.get("break_years")) or bool(gd.get("organic_is_upper_bound"))
    except Exception:
        gd = {}

    # ── Billings = Revenue + Δ deferred revenue (deferred-dependent) ────────
    deferred_col = next((c for c in ("raw_DeferredRevenue", "clean_DeferredRevenue")
                         if c in cols), None)
    if deferred_col is None:
        out["data_gaps"].append(
            "billings / deferred-revenue trend — no deferred-revenue column in the "
            "calc frame (not wired from filings); see Build-C extraction")
    else:
        dr_now = _f(latest.get(deferred_col))
        dr_prior = _f(prior.get(deferred_col))
        if dr_now is not None and dr_prior is not None and rev is not None:
            billings = rev + (dr_now - dr_prior)
            bill = {"billings": billings, "billings_vs_revenue": billings - rev}
            if ma_in_window:
                # Reliable guard: an acquired deferred balance shows as a phantom
                # billings spike — suppress rather than mislead.
                bill = {"billings": None,
                        "reason": "N/A — acquisitive (M&A break in window); "
                                  "deferred-balance consolidation distorts billings"}
            else:
                # Soft caution only — a low deferred/revenue ratio can mean a
                # benign billing-frequency shift, not a non-subscription model.
                if rev > 0 and (dr_now / rev) < 0.10:
                    bill["approximate"] = True
                    bill["note"] = ("approximate — low deferred/revenue ratio "
                                    "(billing-frequency confound, not necessarily mixed model)")
            out["billings"] = bill
            if not ma_in_window:
                out.setdefault("notes", []).append(
                    "billings blind spot: structurally-mixed software names without an "
                    "M&A break are under-guarded until subscription_revenue_pct (v2)")

    # ── Magic number — only when S&M is separable from SG&A ──────────────────
    sm = _f(latest.get("clean_SellingAndMarketing"))
    sm_separable = "clean_SellingAndMarketing" in cols and sm is not None and sm > 0
    if sm_separable and rev is not None and rev_prior is not None:
        d_rev = rev - rev_prior
        out["magic_number"] = {
            "value": d_rev / sm,
            "source": "Δ revenue / S&M (separable)",
        }
        gm = _f(latest.get("derived_GrossMargin_Pct"))
        gm_frac = (gm / 100.0) if (gm is not None and gm > 1.5) else gm
        if gm_frac and d_rev > 0:
            new_gp_arr = d_rev * gm_frac
            out["cac_payback"] = {
                "months": 12.0 * (sm / new_gp_arr) if new_gp_arr > 0 else None,
                "source": "S&M / net-new gross-profit ARR (proxy)",
            }
    else:
        out["magic_number"] = {
            "value": None,
            "reason": "S&M not separable from SG&A — combined SG&A substitution "
                      "would invert the signal (G&A is 30-50% of the line)",
        }
        out["cac_payback"] = {"months": None, "reason": "S&M not separable"}

    # ── Gross-margin trend ───────────────────────────────────────────────────
    gm_series = [(_f(r.get("derived_GrossMargin_Pct")), int(r["fiscal_year"]))
                 for r in rows[-5:]]
    gm_series = [(v, y) for v, y in gm_series if v is not None]
    if len(gm_series) >= 2:
        out["gross_margin_trend"] = {
            "latest": gm_series[-1][0],
            "delta_5y_pp": gm_series[-1][0] - gm_series[0][0],
            "years": [y for _, y in gm_series],
        }

    # ── Drawdown / correlation ───────────────────────────────────────────────
    try:
        from aletheia.data.market_data import get_drawdown_correlation
        dd = get_drawdown_correlation(ticker)
        out["drawdown"] = {
            "max_drawdown": dd.get("max_drawdown"),
            "corr_benchmark": dd.get("corr_benchmark"),
            "benchmark": dd.get("benchmark"),
            "note": "SPY correlation is ≈1 for large-caps (near-filler); "
                    "correlation vs the existing book is the better cut (v2)",
        }
    except Exception:
        pass

    # ── RPO data gap (MD&A-only → Build C) ──────────────────────────────────
    if not any("rpo" in c.lower() or "performance" in c.lower() for c in cols):
        out["data_gaps"].append("RPO / remaining performance obligations — MD&A-only, "
                                "needs Build-C extraction")

    # ── SaaS-gated cyclicality reconciliation flag (mirrors Build F) ─────────
    try:
        from aletheia.tools.cyclicality import calculate_z_score
        z, is_peak, applies_haircut, _avg, _ctx = calculate_z_score(calc_input)
        if is_peak and not applies_haircut:
            out["flags"].append({
                "kind": "cyclicality_reconciliation",
                "message": (f"z-score {z:+.2f} elevated by secular growth, not a cycle; "
                            f"base case is contracted recurring revenue, NOT haircut — "
                            f"IV/MoS unaffected. The 'peak / base inflated' pillar text is "
                            f"a false positive for a recurring-revenue model."),
            })
    except Exception:
        pass

    return out
