"""Downside-protection layer (memo §8).

The NVO failure ("+137% MoS / CONVICTION" with no downside analysis) is the
reason this exists. It answers the questions a buy-side memo must answer before
sizing a position:

  - How much can I lose vs how much can I make? (asymmetry ratio)
  - What does the downside actually look like? (a severity ladder, not one bear)
  - Is my margin of safety big enough FOR THIS RISK LEVEL? (required-MoS by stage)
  - Given conviction + downside, how big should the position be? (sizing band)

Everything here is DETERMINISTIC — it reuses the three engine scenarios
(bull/base/bear), the multiple-decomposition output, and the lifecycle MoS
thresholds. No LLM call. The qualitative pieces a memo also wants (named
failure modes, a pre-mortem) are LLM work and ride the contrarian agent — see
``failure_modes``/``premortem`` placeholders, populated when present.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _sev_from_drawdown(pct: float) -> str:
    """Severity label from a downside % (negative = loss)."""
    if pct <= -0.50:
        return "catastrophic"
    if pct <= -0.30:
        return "severe"
    if pct <= -0.15:
        return "standard"
    return "mild"


def _required_mos(calc) -> Dict[str, Any]:
    """Required margin of safety for this ticker's lifecycle stage."""
    out: Dict[str, Any] = {}
    try:
        from config.lifecycle_thresholds import STAGE_THRESHOLDS, Stage
        lifecycle = getattr(getattr(calc, "classification", None), "lifecycle", None) or "mature"
        try:
            th = STAGE_THRESHOLDS[Stage(lifecycle)]
        except (ValueError, KeyError):
            th = STAGE_THRESHOLDS[Stage.MATURE]
        out = {"stage": lifecycle, "mos_good": float(th.mos_good),
               "mos_strong": float(th.mos_strong)}
    except Exception:
        out = {"stage": "mature", "mos_good": 0.15, "mos_strong": 0.30}
    return out


def build_downside_protection(
    calc, result, *,
    conviction_tier: Optional[str] = None,
    multiple_decomposition: Optional[Dict[str, Any]] = None,
    failure_modes: Optional[List[Dict[str, Any]]] = None,
    premortem: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the downside-protection block from the DCF result + reused inputs.

    ``result`` is a DCFResult (bull/base/bear scenarios, current_price,
    net_debt). ``calc`` supplies the lifecycle stage. Returns
    ``{"available": False}`` when there isn't enough to say anything."""
    out: Dict[str, Any] = {"available": False}
    price = getattr(result, "current_price", None)
    if not price or price <= 0:
        return out

    def _iv(scenario):
        if scenario is None:
            return None
        try:
            return result.intrinsic_per_share(
                scenario.enterprise_value, result.net_debt)
        except Exception:
            return None

    bull_iv = _iv(getattr(result, "bull", None))
    base_iv = _iv(getattr(result, "base", None))
    bear_iv = _iv(getattr(result, "bear", None))

    def _pct(iv):
        return (iv / price - 1.0) if iv else None

    bull_up = _pct(bull_iv)
    base_up = _pct(base_iv)
    bear_dn = _pct(bear_iv)

    # ── Downside ladder ─────────────────────────────────────────────────
    # Each entry is an independent anchor for "what if". The engine bear is
    # the model's own pessimistic case; the multiple-de-rating anchor is a
    # market-based floor (price if the stock compresses to its sector median
    # EV/EBITDA), which often bites before the DCF bear does.
    ladder: List[Dict[str, Any]] = []
    if bear_iv is not None:
        ladder.append({"name": "Engine bear case", "value": float(bear_iv),
                       "vs_price_pct": bear_dn, "severity": _sev_from_drawdown(bear_dn),
                       "basis": "DCF bear scenario (lower growth/margin, higher WACC)"})
    md = multiple_decomposition or {}
    mkt = md.get("market_ev_ebitda"); sect = md.get("sector_median_ev_ebitda")
    if isinstance(mkt, (int, float)) and isinstance(sect, (int, float)) and mkt > 0 and sect > 0 and mkt > sect:
        derate_price = price * (sect / mkt)
        dr_pct = derate_price / price - 1.0
        ladder.append({"name": "Sector-median de-rating", "value": float(derate_price),
                       "vs_price_pct": dr_pct, "severity": _sev_from_drawdown(dr_pct),
                       "basis": f"Multiple compresses {mkt:.1f}× → sector median {sect:.1f}×"})

    worst_pct = min([e["vs_price_pct"] for e in ladder], default=bear_dn)

    # ── Asymmetry ratio ─────────────────────────────────────────────────
    # Realistic upside (base case) vs realistic downside (worst anchor).
    asymmetry = None
    if base_up is not None and worst_pct is not None and worst_pct < 0:
        asymmetry = base_up / abs(worst_pct)
    asym_verdict = ("favorable" if (asymmetry is not None and asymmetry >= 2.0)
                    else "balanced" if (asymmetry is not None and asymmetry >= 1.0)
                    else "unfavorable" if asymmetry is not None else "n/a")

    # ── Required MoS by risk (lifecycle stage) ──────────────────────────
    req = _required_mos(calc)
    actual_mos = base_up  # base-case margin of safety
    if actual_mos is None:
        mos_verdict = "n/a"
    elif actual_mos >= req["mos_strong"]:
        mos_verdict = "meets_strong"
    elif actual_mos >= req["mos_good"]:
        mos_verdict = "meets_good"
    else:
        mos_verdict = "insufficient"

    # ── Position sizing band ────────────────────────────────────────────
    # Deterministic, transparent mapping. Start from the MoS verdict, scale by
    # asymmetry, then cap by conviction tier. A heuristic v1 — NOT portfolio
    # optimization — surfaced so the rating becomes an executable size.
    base_bands = {"meets_strong": (4.0, 6.0), "meets_good": (2.0, 4.0),
                  "insufficient": (0.0, 1.0), "n/a": (0.0, 1.0)}
    lo, hi = base_bands[mos_verdict]
    if asym_verdict == "unfavorable" or worst_pct is not None and worst_pct <= -0.50:
        lo, hi = lo * 0.5, hi * 0.5  # haircut for poor asymmetry / catastrophic tail
    tier = (conviction_tier or "").lower()
    if tier == "pass":
        lo, hi = 0.0, 0.0
    elif tier == "monitor":
        hi = min(hi, 3.0)
    elif tier == "high_conviction" and mos_verdict in ("meets_strong", "meets_good"):
        hi = min(hi + 1.0, 8.0)
    if hi <= 0:
        size_label = "0% — do not initiate"
    elif hi < 1:
        size_label = "<1% (starter only)"
    else:
        size_label = f"{lo:.0f}–{hi:.0f}% of portfolio"
    sizing = {
        "band_pct": [round(lo, 1), round(hi, 1)],
        "label": size_label,
        "basis": (f"MoS {mos_verdict.replace('_',' ')}, asymmetry {asym_verdict}"
                  + (f", tier {tier}" if tier else "")),
    }

    out.update({
        "available": True,
        "price": float(price),
        "upside_pct": bull_up,            # bull case
        "base_upside_pct": base_up,       # base case (= MoS)
        "downside_pct": bear_dn,          # engine bear
        "worst_case_pct": worst_pct,      # worst ladder anchor
        "asymmetry_ratio": asymmetry,
        "asymmetry_verdict": asym_verdict,
        "downside_scenarios": ladder,
        "required_mos": req,
        "actual_mos": actual_mos,
        "mos_verdict": mos_verdict,
        "position_sizing": sizing,
        # LLM-sourced extras (ride the contrarian call when available).
        "failure_modes": failure_modes or [],
        "premortem": premortem or None,
        "source": "DCF scenarios + multiple decomposition + lifecycle MoS (deterministic)",
    })
    return out


def compose_downside_protection(
    ticker: str, *, conviction_tier: Optional[str] = None,
    failure_modes: Optional[List[Dict[str, Any]]] = None,
    premortem: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """One-call composer (recomputes engine + multiples) for surfaces that only
    have a ticker — e.g. HTML report generation. No LLM. None on failure.

    ``failure_modes`` / ``premortem`` are the LLM-sourced extras from the
    contrarian agent (passed through when available); the deterministic core
    is computed here."""
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.dcf_engine import DCFEngine
        from aletheia.tools.multiple_decomposition import MultipleDecomposition
        calc = make_calc_input(ticker)
        result = DCFEngine(verbose=False).run(calc)
        md = None
        try:
            mdres = MultipleDecomposition(verbose=False).run(calc)
            md = {"market_ev_ebitda": mdres.market_ev_ebitda,
                  "sector_median_ev_ebitda": mdres.sector_median_ev_ebitda}
        except Exception:
            md = None
        return build_downside_protection(
            calc, result, conviction_tier=conviction_tier,
            multiple_decomposition=md,
            failure_modes=failure_modes, premortem=premortem)
    except Exception:
        return None


__all__ = ["build_downside_protection", "compose_downside_protection"]
