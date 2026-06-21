"""Capital-structure stability flag (Valuation-Methods plan, Phase 0b).

Selects the discount-rate formula family by reading the firm's **net-debt/EBITDA
trajectory** (CF-R6) — EBITDA-denominated, already in-frame, uncontaminated by
equity-price swings — disambiguated by the capital-return cross-flag (CF-R10).

The hard case is discretionary re-leveraging off a strong base (ADBE: re-levers
off net cash while buying back stock). A naive "trend up → non-stationary →
Family B" mislabels it; CF-R10 catches that returning-capital-off-a-net-cash-base
is reversible discretion, not a debt-service commitment → stays Family A.

Net-vs-gross disambiguation (logged caveat): `derived_NetDebt` swings on the cash
balance, so a buyback-funded cash drawdown reads as "re-leveraging" even with flat
gross debt. We therefore track BOTH net-debt/EBITDA and gross LTD/EBITDA and only
escalate to a committed/structural read when gross debt is also rising.

v1 is backward-only (CF-R9): `forward_signal` is wired but inert; the maturity-wall
forward override is deferred to the v2 extraction.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from aletheia.tools.business_analysis import net_debt_series, build_buyback_funding

# Provisional thresholds (CF-R6) — calibrated on the two seed labels (ADBE→A,
# EQIX→its REIT class); hardened by T2. The CF-R10 cross-flag does most of the
# separation, so these are deliberately loose.
_TREND_FLAT = 0.50          # |Δ net-debt/EBITDA| over the window below this → "stable"
_NET_CASH = 0.0             # net-debt/EBITDA below this → net-cash base
_LOW_LEVERAGE = 1.5         # net-debt/EBITDA below this → discretionary headroom
_EBITDA_FALL = 0.90         # latest EBITDA below this × earliest → falling denominator


def _trend(points: List[Tuple[int, float]]) -> Tuple[float, float]:
    """(total delta, per-year slope) of a (year, value) series."""
    if len(points) < 2:
        return 0.0, 0.0
    delta = points[-1][1] - points[0][1]
    span = points[-1][0] - points[0][0]
    return delta, (delta / span if span else 0.0)


def build_capital_structure_flag(calc, market_cap: Optional[float] = None) -> Dict[str, Any]:
    """Classify capital-structure stability → rate family.

    Returns ``{available, rate_family, klass, net_debt_to_ebitda, trend,
    gross_trend, capital_return, forward_signal, notes}``. ``rate_family`` is
    'A' (constant D/V / rebalancing) or 'B' (constant D / fixed schedule),
    consumed atomically by ``discount_rates.resolve_family`` (CF-R8).
    """
    out: Dict[str, Any] = {"available": False, "forward_signal": None}

    # Financials guard (fail-safe): for banks/insurers EBITDA is not a
    # meaningful leverage denominator, so net-debt/EBITDA is garbage (JPM
    # computed −30×). Refuse rather than emit a number that would render on the
    # report and could silently mis-route a Family selection. REITs
    # (reit_required) are NOT financials — they keep a meaningful flag.
    cls = getattr(calc, "classification", None)
    sector = (getattr(cls, "sector", "") or "")
    business_model = (getattr(cls, "business_model", "") or "")
    if sector == "Financials" or business_model in ("ddm_required", "embedded_value_required"):
        out["notes"] = ("N/A — financials: EBITDA is not a meaningful leverage "
                        "denominator for banks/insurers; net-debt/EBITDA refused")
        return out

    series = net_debt_series(calc)
    ndte = [(x["year"], x["net_debt"] / x["ebitda"])
            for x in series
            if x["net_debt"] is not None and x["ebitda"] and x["ebitda"] > 0]
    if len(ndte) < 3:
        out["notes"] = "insufficient net-debt/EBITDA history (<3 FY)"
        return out

    # Sanity backstop (sector-agnostic): an implausibly large |net-debt/EBITDA|
    # means EBITDA isn't a meaningful denominator (financial-like balance sheet
    # that slipped the sector check) — refuse rather than classify off garbage.
    if abs(ndte[-1][1]) > 8.0:
        out["notes"] = (f"N/A — |net-debt/EBITDA| {ndte[-1][1]:.1f}× implausible; "
                        f"EBITDA not a meaningful leverage denominator")
        return out

    gross = [(x["year"], x["ltd"] / x["ebitda"])
             for x in series if x["ltd"] and x["ebitda"] and x["ebitda"] > 0]
    ebitda_vals = [x["ebitda"] for x in series if x["ebitda"]]

    ndte_first, ndte_last = ndte[0][1], ndte[-1][1]
    delta, slope = _trend(ndte)
    gross_delta, _ = _trend(gross) if len(gross) >= 2 else (None, None)
    ebitda_falling = (len(ebitda_vals) >= 2
                      and ebitda_vals[-1] < ebitda_vals[0] * _EBITDA_FALL)

    # Capital-return cross-flag (CF-R10) — reuses build_buyback_funding.
    bbf = build_buyback_funding(calc, market_cap=market_cap)
    returning_capital = (bbf.get("funding") in ("fcf_funded", "mixed")
                         or (bbf.get("positive_buyback_years") or 0) > 0)
    debt_funded_share = float(bbf.get("debt_funded_share") or 0.0)
    net_cash_base = ndte_last < _NET_CASH
    discretionary_headroom = ndte_last < _LOW_LEVERAGE

    notes: List[str] = []

    # ── Trajectory class ────────────────────────────────────────────────────
    if abs(delta) < _TREND_FLAT:
        klass = "stable"
    elif delta > 0:
        klass = "re-leveraging"
    else:
        klass = "deleveraging"

    # ── Map to rate family (CF-R6 + CF-R10) ─────────────────────────────────
    if klass == "stable":
        rate_family = "A"
        notes.append(f"net-debt/EBITDA flat ({ndte_first:.2f}→{ndte_last:.2f}) → constant-D/V")
    elif klass == "deleveraging":
        rate_family = "A"
        notes.append(f"deleveraging ({ndte_first:.2f}→{ndte_last:.2f}) — rebalancing, Family A")
    else:  # re-leveraging
        gross_rising = (gross_delta is not None and gross_delta > 0)
        committed = (debt_funded_share > 0.5) or (not returning_capital and gross_rising) \
            or ebitda_falling
        if (net_cash_base or discretionary_headroom) and returning_capital and not committed:
            rate_family = "A"
            klass = "re-leveraging (discretionary)"
            notes.append(
                f"re-leveraging ({ndte_first:.2f}→{ndte_last:.2f}) but off a "
                f"{'net-cash' if net_cash_base else 'low-leverage'} base while "
                f"returning capital → discretionary/reversible, stays Family A (CF-R10)")
        else:
            rate_family = "B"
            klass = "re-leveraging (committed)"
            reasons = []
            if debt_funded_share > 0.5:
                reasons.append(f"debt-funded returns ({debt_funded_share:.0%})")
            if not returning_capital:
                reasons.append("no capital-return offset")
            if ebitda_falling:
                reasons.append("falling EBITDA denominator")
            if gross_rising:
                reasons.append("gross LTD also rising")
            notes.append("committed balance-sheet drift → Family B: " + "; ".join(reasons or ["structural"]))

    # ── Unstable escalation (falling-EBITDA structural drift) ───────────────
    if ebitda_falling and klass not in ("stable",):
        notes.append("EBITDA denominator falling — flag 'unstable' for review")
        out["unstable"] = True

    if gross_delta is not None and delta > 0 and gross_delta <= 0:
        notes.append("net-debt rise is cash-driven (gross LTD flat/falling) — "
                     "not structural re-leveraging")

    out.update({
        "available": True,
        "rate_family": rate_family,
        "klass": klass,
        "net_debt_to_ebitda": {"first": ndte_first, "last": ndte_last,
                               "delta": delta, "slope_per_yr": slope,
                               "series": [(y, round(v, 3)) for y, v in ndte]},
        "gross_ltd_to_ebitda_delta": gross_delta,
        "ebitda_falling": ebitda_falling,
        "capital_return": {"funding": bbf.get("funding"),
                           "returning_capital": returning_capital,
                           "debt_funded_share": debt_funded_share},
        "forward_signal": None,   # CF-R9 v1: inert placeholder
        "notes": notes,
        "threshold_provisional": True,
    })
    return out
