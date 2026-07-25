"""Value Source Decomposition (VSD plan Build 4 / spec §3).

Attributes a position's expected annualized total return across the four Liberti
value engines — Operating, Financial, Multiple — plus a Governance modifier, so
conviction can be scaled by the *durability* of the return source, not just its
magnitude. Pure/deterministic (no LLM). Consumes signals Aletheia already
computes; the only new feeds are the β-band (Build 1) and the historical
multiple (Build 2).

Units invariant (R10): all three contributors are annualized fractional returns
(per-year, decimal). organic_cagr is annual; dividend/net-buyback yields are
annual; mult_contrib is annualized via the h-th root. Never feed a cumulative
figure into a slot.

Key modeling resolutions:
  * R9  — fin_contrib has NO leverage/tax slice (already priced into the
          WACC-driven justified multiple); net_buyback_yield is AfterSBC, so no
          separate dilution term either (no double-count).
  * R11 — op_contrib is share-neutral: organic_cagr already embeds share
          dynamics, so share_gain_pp is a reported note only, never summed.
  * R15 — the justified multiple is computed by calling the rate-parameterized
          central formula twice (gspc β WACC and sector β WACC).
  * "Conservative" mult_contrib (decision #2 / R1) = the MINIMUM signed
          re-rating across {justified@gspc, justified@sector, historical} — the
          least multiple-credit / most de-rating-risk. This correctly leaves a
          *cheap* name (ADBE: current ≈ justified) with a tiny multiple share
          while flagging a *premium* name (EQIX: current P/AFFO ≫ growth-
          normalized reference). Taking max|·| would wrongly penalize cheapness.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from aletheia.tools.business_analysis import (
    build_growth_decomposition,
    operating_leverage,
    build_buyback_funding,
)
from aletheia.tools.historical_multiples import build_historical_multiples

_NARRATIVE_BIASES = {"narrative_premium", "growth_extrapolation", "fomo"}


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _dividend_yield(calc_input, price: Optional[float]) -> Optional[float]:
    """Most-recent FY DPS / current price."""
    df = getattr(calc_input, "df", None)
    if df is None or not price or price <= 0:
        return None
    if "clean_DividendsPerShare" not in getattr(df, "columns", []):
        return None
    d = df
    if "period" in d.columns:
        d = d[d["period"] == "FY"]
    dps = [v for v in (_f(x) for x in d.sort_values("fiscal_year")["clean_DividendsPerShare"].tolist())
           if v is not None]
    if not dps:
        return None
    return max(dps[-1], 0.0) / price


def _latest_fy_with_data(calc_input, col: str = "clean_Revenue") -> Optional[int]:
    """Most-recent FY whose core operating field is actually populated.

    The operating engine derives ``organic_cagr`` from the FY revenue series;
    if the latest *populated* FY is years behind the current period, that growth
    rate is stale. Returns None when the column/frame is unavailable.
    """
    df = getattr(calc_input, "df", None)
    if df is None or "period" not in getattr(df, "columns", []):
        return None
    d = df[df["period"] == "FY"]
    if col not in getattr(d, "columns", []) or d.empty:
        return None
    valid = d[d[col].notna()]
    if valid.empty:
        return None
    try:
        return int(valid["fiscal_year"].max())
    except (TypeError, ValueError):
        return None


def _extract_contrarian_bias(state: Optional[dict]) -> Optional[str]:
    """Pull a normalized contrarian bias tag (for the R4 narrative override).
    Available only after the contrarian/bear agent has run; None at the
    deterministic calc-node stage (the share-ceiling backstop still binds)."""
    if not state:
        return None
    for path in (("contrarian_report", "bias_detected"),
                 ("strategist_report", "bias_detected"),
                 ("phase2_valuation", "contrarian_bias")):
        node = state
        ok = True
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                ok = False
                break
        if ok and isinstance(node, str):
            tag = node.strip().lower().replace(" ", "_").replace("/", "_")
            for b in _NARRATIVE_BIASES:
                if b in tag:
                    return b
    return None


def build_value_source_decomposition(calc_input, dcf_result, p2: Optional[dict] = None,
                                     state: Optional[dict] = None,
                                     horizon_years: int = 3) -> Dict[str, Any]:
    """Return the 100%-stacked engine attribution + governance modifier.

    See module docstring for the units invariant and modeling resolutions.
    Returns ``{available: False, ...}`` when inputs are too thin to attribute.
    """
    out: Dict[str, Any] = {"available": False}
    h = max(int(horizon_years or 3), 1)
    p2 = p2 or {}

    cls = getattr(calc_input, "classification", None)
    business_model = getattr(cls, "business_model", None)
    is_reit = business_model == "reit_required"

    # Price/market-cap: from the DCF result for FCFF names; from p2 for
    # specialized engines (REIT/DDM) where dcf_result is None (DCF bypassed).
    _p2dcf = p2.get("dcf") or {}
    price = (_f(getattr(dcf_result, "current_price", None))
             or _f(p2.get("current_price")) or _f(_p2dcf.get("current_price")))
    mcap = (_f(getattr(dcf_result, "market_cap", None))
            or _f(p2.get("market_cap")) or _f(_p2dcf.get("market_cap")))
    if mcap is None and price and is_reit:
        sh = _f((p2.get("specialized_inputs") or {}).get("shares_diluted"))
        if sh:
            mcap = price * sh
    notes: List[str] = []

    # ── stale-annual-data guard (R18) ────────────────────────────────────────
    # The non-REIT operating engine derives organic_cagr from the FY revenue
    # series. If the latest FY *with data* is years behind the current (TTM /
    # latest) period — e.g. a foreign filer whose 20-F annuals never ingested
    # while the TTM feed did (NVO: FY2021-2025 all NaN, TTM current at ~2.6×
    # the last real FY) — organic_cagr is computed off a stale window, op_contrib
    # is badly understated, and the multiple's share inflates into a FALSE
    # de-rating read. Refuse rather than silently emit a misleading -X%/yr bar.
    # REITs take growth from p2 specialized_inputs (not the FY series), so the
    # guard is scoped to the non-REIT path.
    if not is_reit:
        df = getattr(calc_input, "df", None)
        latest_data_fy = _latest_fy_with_data(calc_input)
        ref_fy = None
        if df is not None and "fiscal_year" in getattr(df, "columns", []):
            try:
                ref_fy = int(df["fiscal_year"].max())
            except (TypeError, ValueError):
                ref_fy = None
        if latest_data_fy is not None and ref_fy is not None:
            gap = ref_fy - latest_data_fy
            # gap of 1 is normal (TTM sits ~1 year ahead of the last 10-K).
            # gap >= 3 is never a mere filing lag — it is a genuine data hole.
            if gap >= 3:
                out["source"] = (
                    f"stale annual data — latest FY with financials is "
                    f"{latest_data_fy}, but the current period is {ref_fy} "
                    f"({gap}y gap). The operating engine would run on a stale "
                    "growth window, so the return decomposition is suppressed to "
                    "avoid a misleading read. Common for foreign filers whose "
                    "annual statements did not ingest — re-ingest the missing FYs."
                )
                out["stale_annual_data"] = True
                out["annual_data_gap_years"] = gap
                out["latest_annual_fy"] = latest_data_fy
                out["current_period_fy"] = ref_fy
                return out
            if gap == 2:
                notes.append(
                    f"annual data 2y stale (latest FY {latest_data_fy} vs current "
                    f"{ref_fy}) — organic growth may be understated"
                )

    # ── op_contrib (R2 clamp + R11 share-neutral) ────────────────────────────
    share_gain_pp = None
    if is_reit:
        si = p2.get("specialized_inputs") or {}
        organic = _f(si.get("explicit_growth"))          # AFFO/share growth
        lev_factor = 1.0                                  # no leverage amp for REITs (decision #4)
        op_source = "AFFO/share growth (REIT, decision #4)"
    else:
        gd = build_growth_decomposition(calc_input)
        ol = operating_leverage(calc_input)
        organic = _f(gd.get("organic_cagr"))
        share_gain_pp = _f(gd.get("share_gain_pp"))       # R11: reported note only
        inc_m = _f(ol.get("incremental_margin"))
        cur_m = _f(ol.get("current_margin"))
        if cur_m is not None and cur_m > 0.02 and inc_m is not None and inc_m > 0:
            lev_factor = min(max(inc_m / cur_m, 0.5), 2.0)
            if lev_factor in (0.5, 2.0):
                notes.append(f"op leverage factor hit rail ({lev_factor})")
        else:
            lev_factor = 1.0
            notes.append("op leverage factor = 1.0 (margin degenerate)")
        op_source = "organic_cagr × clamp(incremental/current margin, 0.5..2.0)"
    op_contrib = max((organic or 0.0) * lev_factor, 0.0)

    # ── fin_contrib (R9: dividend + net-buyback AfterSBC; no leverage slice,
    #    no separate dilution term) ────────────────────────────────────────────
    div_yield = _dividend_yield(calc_input, price)
    bbf = build_buyback_funding(calc_input, market_cap=mcap)
    nb_yield = _f(bbf.get("net_buyback_yield")) or 0.0
    fin_contrib = (div_yield or 0.0) + nb_yield

    # ── mult_contrib (R1 β-band + historical; R15 central formula) ───────────
    candidates: Dict[str, float] = {}
    cur_mult_label = None
    if is_reit:
        si = p2.get("specialized_inputs") or {}
        affo = _f(si.get("current_affo_per_share"))
        ke = _f(si.get("cost_of_equity"))
        gt = _f(si.get("terminal_growth"))
        if affo and affo > 0 and price and ke is not None and gt is not None and ke > gt:
            cur_paffo = price / affo
            # Growth-normalized reference: what the REIT is worth as a stable
            # perpetuity at terminal growth (no AI/buildout premium). The gap
            # vs the current multiple IS the narrative/growth premium. Using the
            # model's own two-stage justified P/AFFO would be circular (it bakes
            # in the elevated explicit growth).
            ref_paffo = 1.0 / (ke - gt)
            candidates["reit_terminal_ref"] = (ref_paffo / cur_paffo) ** (1.0 / h) - 1.0
            cur_mult_label = f"P/AFFO {cur_paffo:.1f}× vs growth-normalized {ref_paffo:.1f}×"
    else:
        ebitda = _f(getattr(dcf_result, "ebitda", None))
        net_debt = _f(getattr(dcf_result, "net_debt", None)) or 0.0
        nopat = _f(getattr(dcf_result, "nopat", None))
        roic = _f(getattr(dcf_result, "roic", None))
        base = getattr(dcf_result, "base", None)
        g = _f(getattr(getattr(base, "assumptions", None), "terminal_growth", None)) or 0.025
        cur_ev_ebitda = (mcap + net_debt) / ebitda if (mcap and ebitda and ebitda > 0) else None
        if cur_ev_ebitda:
            cur_mult_label = f"EV/EBITDA {cur_ev_ebitda:.1f}×"
            wg = _f(getattr(dcf_result, "wacc_base", None))
            beta_sector = _f(getattr(dcf_result, "beta_sector", None))
            from aletheia.tools.dcf_engine import wacc_at_beta
            from aletheia.calculations.formulas import justified_ev_ebitda
            rates = {"justified@gspc": wg}
            if beta_sector is not None and wg is not None:
                rates["justified@sector"] = wacc_at_beta(dcf_result, beta_sector)
            for label, rate in rates.items():
                if rate is None or nopat is None or roic is None or ebitda is None:
                    continue
                j = justified_ev_ebitda(nopat=nopat, ebitda=ebitda, roic=roic,
                                        wacc=rate, terminal_growth=g)
                if j and j > 0:
                    candidates[label] = (j / cur_ev_ebitda) ** (1.0 / h) - 1.0
            hm = build_historical_multiples(calc_input)
            if hm.get("available") and hm.get("ev_ebitda_5y_avg"):
                candidates["historical"] = (hm["ev_ebitda_5y_avg"] / cur_ev_ebitda) ** (1.0 / h) - 1.0

    # Conservative = minimum signed re-rating (least multiple credit / most
    # de-rating risk). Cheap names → small |mult|; premium names → large |mult|.
    gate_mult = min(candidates.values()) if candidates else 0.0

    # R17 — multiple-anchor divergence. The 2-3 re-rating anchors (justified@gspc,
    # justified@sector, historical) can disagree on magnitude or sign. We use the
    # conservative min-signed end (above), but we SURFACE the spread + a signed
    # direction so a large multiple_share on a *de-rating* contribution is not
    # misread as a positive return (the TSM case: all anchors say compress, gate
    # −28%/yr, yet the 51% share read as "+51%" to a fast eye). Same class as the
    # CF-R10 disambiguator — a rule for what to surface when inputs disagree.
    _cands = list(candidates.values())
    mult_direction = ("de-rating" if gate_mult < 0
                      else "re-rating" if gate_mult > 0 else "flat")
    mult_anchor_divergence = None
    if len(_cands) >= 2:
        _spread = max(_cands) - min(_cands)
        _sign_disagree = (max(_cands) > 0 and min(_cands) < 0)
        mult_anchor_divergence = {
            "anchors": {k: round(v, 4) for k, v in candidates.items()},
            "spread_pp": round(_spread, 4),
            "sign_disagreement": _sign_disagree,
            "selected_conservative": round(gate_mult, 4),
            "direction": mult_direction,
            # "wide" when anchors disagree on sign or span >10pp/yr — the band
            # should be read as uncertain, not a point estimate.
            "wide": bool(_sign_disagree or _spread > 0.10),
        }

    # ── gov_modifier (R3 primary debt-funding; R14 secondary M&A) ────────────
    debt_funded_share = _f(bbf.get("debt_funded_share")) or 0.0
    value_destructive_ma = False
    if not is_reit:   # GAAP ROIC distorted for REITs (decision #4) → skip M&A read
        roic = _f(getattr(dcf_result, "roic", None))
        wacc = _f(getattr(dcf_result, "wacc_base", None))
        # `gd` is computed above in the non-REIT op_contrib branch.
        if gd.get("break_years") and roic is not None and wacc is not None and roic < wacc:
            value_destructive_ma = True
    gov_modifier = 0
    if debt_funded_share > 0.5 or value_destructive_ma:
        gov_modifier = -1
    elif (not is_reit and bbf.get("funding") == "fcf_funded"):
        roic = _f(getattr(dcf_result, "roic", None))
        wacc = _f(getattr(dcf_result, "wacc_base", None))
        if roic is not None and wacc is not None and roic > wacc + 0.02:
            gov_modifier = +1

    # ── normalize (shares clamped non-negative; raw contributions reported) ──
    op_pos = max(op_contrib, 0.0)
    fin_pos = max(fin_contrib, 0.0)
    mult_abs = abs(gate_mult)
    denom = op_pos + fin_pos + mult_abs
    if denom <= 0:
        out["source"] = "no attributable return (all contributors ~0)"
        return out
    if fin_contrib < 0:
        notes.append(f"financial engine is a net drag ({fin_contrib:+.2%}; dilution)")

    out.update({
        "available": True,
        "operating_share": op_pos / denom,
        "financial_share": fin_pos / denom,
        "multiple_share": mult_abs / denom,
        "gov_modifier": gov_modifier,
        "op_contrib": op_contrib,
        "fin_contrib": fin_contrib,
        "mult_contrib_gate": gate_mult,
        "mult_contrib_candidates": {k: round(v, 4) for k, v in candidates.items()},
        "mult_direction": mult_direction,                 # R17: signed direction
        "mult_anchor_divergence": mult_anchor_divergence,  # R17: anchor spread
        "lev_factor": lev_factor,
        "share_gain_pp_note": share_gain_pp,            # R11: NOT summed
        "dividend_yield": div_yield,
        "net_buyback_yield": nb_yield,
        "buyback_funding": bbf.get("funding"),
        "debt_funded_share": debt_funded_share,
        "value_destructive_ma": value_destructive_ma,
        "current_multiple": cur_mult_label,
        "horizon_years": h,
        "contrarian_bias": _extract_contrarian_bias(state),
        "is_reit": is_reit,
        "op_source": op_source,
        "notes": notes,
        "honest_flag": ("Triangulation of return CHARACTER, not a precise P&L "
                        "attribution; engine partition is a modeling choice."),
    })
    return out
