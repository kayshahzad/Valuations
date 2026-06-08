"""Discount-rate detail (memo §7).

WACC drives ~15-25% of intrinsic value per 100 bps, yet the engine's treatment
is a single CAPM number clamped to [6%, 16%]. This module adds the depth a memo
needs, WITHOUT silently re-pricing the engine:

  - Component decomposition (rf, beta, ERP, Ke, weights, WACC) with sources.
  - Build-up premia: size (by market cap), country (by domicile), idiosyncratic
    (leverage/risk) → an ADJUSTED WACC shown alongside the base (not auto-applied).
  - WACC sensitivity table: IV at base ±50/100/200 bps.
  - Implied WACC: the discount rate the current market price implies.
  - A discount-rate quality score (component completeness).

IV(w) is recomputed analytically by holding the operating forecast fixed and
treating the terminal value as a perpetuity — so IV at the base WACC reproduces
the engine's enterprise value exactly, and the WACC sensitivity is faithful.
Deterministic; no LLM, no new fetch (country uses the cached FMP profile).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Size premium by market cap (USD). Conservative, CRSP-decile-style.
_SIZE_PREMIUM = [
    (10e9, 0.000),   # large/mega
    (2e9,  0.005),   # mid
    (0.5e9, 0.012),  # small
    (0.0,  0.025),   # micro
]
# Country risk premium by domicile (Damodaran-style, abridged). Developed = 0.
_DEVELOPED = {"US", "CA", "GB", "DE", "FR", "JP", "AU", "CH", "NL", "SE",
              "DK", "NO", "FI", "IE", "BE", "AT", "SG", "HK", "NZ"}
_COUNTRY_PREMIUM = {  # non-developed examples; default 0.01 when unknown EM
    "CN": 0.008, "TW": 0.006, "KR": 0.006, "IN": 0.020, "BR": 0.025,
    "MX": 0.018, "ZA": 0.030, "RU": 0.045, "TR": 0.040,
}


def _size_premium(market_cap: Optional[float]) -> float:
    if not market_cap or market_cap <= 0:
        return 0.0
    for threshold, prem in _SIZE_PREMIUM:
        if market_cap >= threshold:
            return prem
    return 0.025


def _country_premium(country: Optional[str]) -> float:
    if not country:
        return 0.0
    c = country.upper()
    if c in _DEVELOPED:
        return 0.0
    return _COUNTRY_PREMIUM.get(c, 0.010)


def _idiosyncratic_premium(result) -> tuple:
    """Additive premium from observable company risk. Returns (premium, reasons)."""
    prem, reasons = 0.0, []
    ebitda = getattr(result, "ebitda", 0) or 0
    net_debt = getattr(result, "net_debt", 0) or 0
    if ebitda > 0:
        lev = net_debt / ebitda
        if lev > 6:
            prem += 0.020; reasons.append(f"net debt/EBITDA {lev:.1f}× (>6)")
        elif lev > 4:
            prem += 0.010; reasons.append(f"net debt/EBITDA {lev:.1f}× (>4)")
    roic = getattr(result, "roic", None)
    wacc = getattr(result, "wacc_base", None)
    if isinstance(roic, (int, float)) and isinstance(wacc, (int, float)) and roic < wacc:
        prem += 0.005; reasons.append("ROIC below WACC (value-destructive)")
    return min(prem, 0.03), reasons


def _iv_at_wacc(result, w: float) -> Optional[float]:
    """Intrinsic value per share at discount rate ``w``, holding the base-case
    operating forecast fixed. Terminal value is treated as a perpetuity whose
    implied cash flow is backed out from the engine's base TV, so IV at the base
    WACC reproduces the engine exactly."""
    base = getattr(result, "base", None)
    if base is None or not base.projections:
        return None
    g = base.assumptions.terminal_growth
    wacc_base = base.assumptions.wacc
    if w <= g:  # perpetuity diverges
        return None
    n = base.projections[-1].year
    pv_explicit = sum(p.fcff / (1.0 + w) ** p.year for p in base.projections)
    # Back out the perpetuity's terminal cash flow from the engine's TV.
    tv_used = base.terminal.tv_used if base.terminal else 0.0
    cf_perp = tv_used * (wacc_base - g)        # constant, independent of w
    tv_w = cf_perp / (w - g)
    pv_tv = tv_w / (1.0 + w) ** n
    ev = pv_explicit + pv_tv
    try:
        return result.intrinsic_per_share(ev, result.net_debt)
    except Exception:
        return None


def _implied_wacc(result, price: float, g: float) -> Optional[float]:
    """Solve IV(w) == price for w (the discount rate the price implies)."""
    if not price or price <= 0:
        return None
    lo, hi = max(g + 1e-4, 0.01), 0.60

    def f(w):
        iv = _iv_at_wacc(result, w)
        return (iv - price) if iv is not None else None

    flo, fhi = f(lo), f(hi)
    if flo is None or fhi is None or flo * fhi > 0:
        return None  # no sign change in range — price outside model's IV span
    try:
        from scipy.optimize import brentq
        return float(brentq(lambda w: f(w), lo, hi, maxiter=100, xtol=1e-5))
    except Exception:
        # Bisection fallback.
        for _ in range(60):
            mid = (lo + hi) / 2
            fm = f(mid)
            if fm is None:
                return None
            if flo * fm <= 0:
                hi = mid
            else:
                lo, flo = mid, fm
        return (lo + hi) / 2


def build_wacc_analysis(
    result, *, country: Optional[str] = None,
    tax_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """Discount-rate detail from the DCF result. Returns ``{"available": False}``
    when the base scenario isn't usable."""
    out: Dict[str, Any] = {"available": False}
    base = getattr(result, "base", None)
    if base is None or not getattr(base, "projections", None):
        return out
    from aletheia.tools.dcf_engine import MARKET_RISK_PREMIUM

    rf = float(getattr(result, "risk_free_rate", 0) or 0)
    beta = float(getattr(result, "beta", 0) or 0)
    erp = MARKET_RISK_PREMIUM
    ke = rf + beta * erp
    wacc_base = float(base.assumptions.wacc)
    g = float(base.assumptions.terminal_growth)
    price = float(getattr(result, "current_price", 0) or 0)
    mktcap = float(getattr(result, "market_cap", 0) or 0)
    net_debt = float(getattr(result, "net_debt", 0) or 0)

    # Capital-structure weights — use the EXACT debt figure the engine used in
    # the WACC (exposed as wacc_total_debt) so the discount-detail weights match
    # the cost-of-capital build instead of re-deriving from net debt.
    equity = mktcap if mktcap > 0 else None
    debt = getattr(result, "wacc_total_debt", None)
    if not isinstance(debt, (int, float)) or debt <= 0:
        debt = max(net_debt, 0.0)
    wE = wD = None
    if equity:
        tot = equity + debt
        wE, wD = equity / tot, debt / tot
    # Implied Kd backed out from WACC when weights known.
    tax = tax_rate if tax_rate is not None else 0.21
    kd_implied = None
    if wE is not None and wD and wD > 0 and (1 - tax) > 0:
        kd_implied = (wacc_base - wE * ke) / (wD * (1 - tax))

    # Build-up premia → adjusted WACC (shown, NOT auto-applied to the engine).
    size_p = _size_premium(mktcap)
    country_p = _country_premium(country)
    idio_p, idio_reasons = _idiosyncratic_premium(result)
    adjusted_wacc = wacc_base + size_p + country_p + idio_p
    iv_adjusted = _iv_at_wacc(result, adjusted_wacc)
    iv_base = _iv_at_wacc(result, wacc_base)

    # Sensitivity table: IV at base ±50/100/200 bps.
    sensitivity = []
    for d in (-0.020, -0.010, -0.005, 0.0, 0.005, 0.010, 0.020):
        w = wacc_base + d
        iv = _iv_at_wacc(result, w)
        sensitivity.append({
            "wacc": round(w, 4),
            "delta_bps": int(round(d * 10000)),
            "iv": float(iv) if iv is not None else None,
            "vs_price_pct": (iv / price - 1.0) if (iv and price) else None,
            "is_base": d == 0.0,
        })

    implied = _implied_wacc(result, price, g)

    # Discount-rate quality (component completeness).
    checks = {
        "risk_free_live": rf > 0,
        "beta_computed": beta > 0,
        "market_cap_known": mktcap > 0,
        "erp_documented": True,           # flat Damodaran 4.75%
        "weights_known": wE is not None,
    }
    score = sum(1 for v in checks.values() if v)
    quality = {
        "score": score, "max": len(checks), "checks": checks,
        "beta_r2": None,                  # not currently computed
        "erp_method": "Damodaran flat consensus (4.75%)",
        "notes": "Beta R² not tracked; country premium is domicile-based "
                 "(not geo-revenue-weighted).",
    }

    out.update({
        "available": True,
        "components": {
            "risk_free_rate": rf, "beta": beta, "erp": erp,
            "cost_of_equity": ke, "cost_of_debt_implied": kd_implied,
            "tax_rate": tax, "equity_weight": wE, "debt_weight": wD,
            "wacc_base": wacc_base, "terminal_growth": g,
        },
        "premia": {
            "size": size_p, "country": country_p, "idiosyncratic": idio_p,
            "idiosyncratic_reasons": idio_reasons,
            "total": size_p + country_p + idio_p,
            "country_used": country,
        },
        "adjusted_wacc": adjusted_wacc,
        "iv_base": float(iv_base) if iv_base is not None else None,
        "iv_at_adjusted_wacc": float(iv_adjusted) if iv_adjusted is not None else None,
        "sensitivity": sensitivity,
        "implied_wacc": implied,
        "implied_vs_base_bps": (int(round((implied - wacc_base) * 10000))
                                if implied is not None else None),
        "capital_structure": {
            "equity_weight": wE, "debt_weight": wD,
            "basis": "current market weights (no target / pro-forma modeling)",
        },
        "quality": quality,
        "source": "DCF base scenario re-discounted (forecast fixed, perpetuity TV)",
    })
    return out


def compose_wacc_analysis(ticker: str) -> Optional[Dict[str, Any]]:
    """One-call composer for ticker-only surfaces (HTML report). No LLM."""
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.dcf_engine import DCFEngine
        calc = make_calc_input(ticker)
        result = DCFEngine(verbose=False).run(calc)
        country = None
        try:
            from aletheia.data import fmp_client
            prof = fmp_client.fetch_profile(ticker) or {}
            country = prof.get("country")
        except Exception:
            country = None
        return build_wacc_analysis(result, country=country)
    except Exception:
        return None


__all__ = ["build_wacc_analysis", "compose_wacc_analysis"]
