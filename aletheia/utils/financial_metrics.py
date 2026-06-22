"""Build the ``5_financial_metrics`` block stamped onto the serving JSON.

Self-contained payload for the HTML/MD report generators — no DB
access at render time. Sourced once during Stage 4 (lead.py) from:

  - The cleaned 5-year FY history (DuckDB ``company_records_latest``)
  - The DCFResult that calc_node already produced (held on state)
  - Macro context (Rf, ERP)

The output schema is stable so consumers (report_generator, future
exporters) can rely on the shape rather than poking individual
phases of the workflow.

Schema:

    {
      "historical": [
        {"fiscal_year", "period_end_date",
         "revenue_bn", "ebitda_bn", "op_income_bn", "fcf_bn",
         "net_income_bn", "shares_diluted_m", "eps_diluted",
         "gross_margin", "ebit_margin", "ebitda_margin", "fcf_margin"},
        ... up to 5 years sorted ASC
      ],
      "ratios": {
        "profitability": {gross_margin, ebit_margin, ebitda_margin,
                          net_margin, fcf_margin, roic, roe, roa},
        "liquidity":     {current_ratio, quick_ratio, cash_ratio},
        "leverage":      {debt_to_equity, debt_to_ebitda,
                          net_debt_to_ebitda, interest_coverage},
        "valuation":     {pe, pb, ps, ev_sales, ev_ebitda,
                          fcf_yield, dividend_yield},
        "quality":       {sbc_pct_fcf, share_dilution_pct,
                          payout_ratio, accrual_ratio},
        "growth":        {revenue_cagr_1y, revenue_cagr_3y,
                          revenue_cagr_5y, ebitda_cagr_5y,
                          fcf_cagr_5y, eps_growth_ttm},
      },
      "dcf_assumptions": {
        "bull": {revenue_cagr_y1_5, revenue_cagr_y6_10, ebit_margin_terminal,
                 terminal_growth, wacc, tax_rate, capex_pct_revenue,
                 nwc_pct_revenue},
        "base": {...same shape},
        "bear": {...same shape}
      },
      "wacc_build": {
        "risk_free_rate", "beta", "equity_risk_premium",
        "cost_of_equity", "cost_of_debt_pretax", "tax_rate",
        "cost_of_debt_aftertax", "weight_equity", "weight_debt",
        "wacc",
      }
    }

All values are ``Optional[float]`` (numeric or None) so the HTML
renderer can degrade gracefully — missing ratios show "—" not crash.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _f(v: Any) -> Optional[float]:
    """Coerce to float or return None on failure / NaN."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None   # NaN check


def _pct(numer: Optional[float], denom: Optional[float]) -> Optional[float]:
    """Safe division; returns None when denom is missing/zero."""
    if numer is None or denom is None or denom == 0:
        return None
    return numer / denom


def _cagr(start: Optional[float], end: Optional[float], years: int) -> Optional[float]:
    """Compound annual growth rate. Returns None when inputs are
    invalid (missing, zero or negative start, zero years)."""
    if start is None or end is None or start <= 0 or years <= 0:
        return None
    try:
        return (end / start) ** (1.0 / years) - 1.0
    except (ZeroDivisionError, ValueError):
        return None


def _trailing_cagr(series, years: int) -> Optional[float]:
    """Year-aware trailing CAGR over (up to) ``years`` calendar years.

    ``series`` is ``[(value, fiscal_year), ...]`` ascending. We anchor on the
    latest point and pick the start point closest to ``latest_year - years``,
    then divide by the *actual* year span. This avoids the mislabeled
    "since-inception" CAGR that results from anchoring on the oldest row
    (e.g. EQIX's 1999 startup revenue would otherwise make a "5y" CAGR 61%).
    Gaps/duplicate years are tolerated because we match on year, not position.
    """
    if not series or len(series) < 2:
        return None
    end_v, end_y = series[-1]
    target = end_y - years
    start_v, start_y = min(series, key=lambda t: abs(t[1] - target))
    if start_y >= end_y:
        return None
    return _cagr(start_v, end_v, end_y - start_y)


def _historical_rows(df) -> List[Dict[str, Any]]:
    """Build 5 most-recent FY rows. Each row carries the absolutes the
    HTML table renders + a few margin ratios for color-coding."""
    if df is None or df.empty:
        return []
    fy = df[df.get("period", "FY") == "FY"].sort_values("fiscal_year").tail(5)
    rows: List[Dict[str, Any]] = []
    for _, r in fy.iterrows():
        rev = _f(r.get("clean_Revenue"))
        ebitda = _f(r.get("derived_EBITDA") or r.get("clean_EBITDA"))
        opinc = _f(r.get("raw_OperatingIncome"))
        fcf = _f(r.get("derived_FCF") or r.get("clean_FCF"))
        ni = _f(r.get("raw_NetIncome"))
        shares = _f(r.get("clean_SharesDiluted") or r.get("raw_SharesDiluted"))
        eps = _f(r.get("clean_EPS_Diluted"))
        cogs = _f(r.get("raw_COGS"))
        gross_profit = (rev - cogs) if (rev is not None and cogs is not None) else None
        rows.append({
            "fiscal_year":      int(r["fiscal_year"]),
            "period_end_date":  str(r.get("period_end_date"))[:10],
            "revenue_bn":       (rev / 1e9) if rev else None,
            "ebitda_bn":        (ebitda / 1e9) if ebitda else None,
            "op_income_bn":     (opinc / 1e9) if opinc else None,
            "fcf_bn":           (fcf / 1e9) if fcf else None,
            "net_income_bn":    (ni / 1e9) if ni else None,
            "shares_diluted_m": (shares / 1e6) if shares else None,
            "eps_diluted":      eps,
            "gross_margin":     _pct(gross_profit, rev),
            "ebit_margin":      _pct(opinc, rev),
            "ebitda_margin":    _pct(ebitda, rev),
            "fcf_margin":       _pct(fcf, rev),
        })
    return rows


def _engine_base_row(df):
    """The SAME base row the DCF engine consumes: a TTM-row merged over the
    latest FY (TTM wins on flow/ratio fields, FY fills structural gaps) when a
    TTM record exists, else the latest FY row. Mirrors
    ``dcf_engine._merge_ttm_with_fy_fallback`` so the Comprehensive Ratios
    (ROIC, EV/EBITDA, Net Debt/EBITDA) reconcile with the engine, conviction
    pillars, and multiple-decomposition instead of reading a stale FY-only row.
    """
    if df is None or df.empty:
        return None
    if "period" in df.columns:
        fy = df[df["period"] == "FY"].sort_values("fiscal_year")
        ttm = df[df["period"] == "TTM"]
    else:
        fy, ttm = df.sort_values("fiscal_year"), df.iloc[0:0]
    if fy.empty:
        return None
    latest_fy = fy.iloc[-1]
    if not ttm.empty:
        try:
            from aletheia.tools.dcf_engine import _merge_ttm_with_fy_fallback
            return _merge_ttm_with_fy_fallback(ttm.iloc[-1], latest_fy)
        except Exception:
            return latest_fy
    return latest_fy


def _compute_ratios(
    df, market_cap: Optional[float],
    current_price: Optional[float],
    is_financial: bool = False,
) -> Dict[str, Dict[str, Optional[float]]]:
    """Comprehensive ratios for the engine's base row (TTM-merged when
    available). Reuses the central formula library where possible; falls back
    to plain division when a ratio doesn't have a dedicated formula yet."""
    if df is None or df.empty:
        return {}
    # Snapshot ratios read the engine's base row (TTM-merged); multi-year
    # CAGR/trend calcs below stay on the FY-only frame.
    latest = _engine_base_row(df)
    if latest is None:
        return {}
    fy = df[df.get("period", "FY") == "FY"].sort_values("fiscal_year")

    # Two-tier field lookup: top-level DB column first, then clean_json
    # blob. The FMP/hybrid path doesn't always materialize every field
    # as a top-level column (raw_InterestExpense, raw_CurrentAssets, etc.
    # stay NULL for many filers); the underlying value still lives in
    # clean_json. Without the fallback, ratios that depend on those
    # fields would render "—" even when the data exists.
    import json as _json
    cj = {}
    raw_cj = latest.get("clean_json")
    if isinstance(raw_cj, str):
        try:
            cj = _json.loads(raw_cj) or {}
        except _json.JSONDecodeError:
            cj = {}

    def _get(*candidates):
        """Resolve the first non-None value across (column, clean_json key) pairs."""
        for c in candidates:
            v = latest.get(c) if c in latest.index else None
            if v is not None and not (isinstance(v, float) and v != v):
                return _f(v)
            v2 = cj.get(c)
            if v2 is not None:
                return _f(v2)
        return None

    rev = _f(latest.get("clean_Revenue")) or _f(cj.get("Revenue"))
    cogs = _f(latest.get("raw_COGS")) or _f(cj.get("COGS"))
    gross_profit = (rev - cogs) if (rev is not None and cogs is not None) else None
    opinc = _f(latest.get("raw_OperatingIncome")) or _f(cj.get("OperatingIncome"))
    ebitda = _f(latest.get("derived_EBITDA") or latest.get("clean_EBITDA")) or _f(cj.get("EBITDA"))
    ni = _f(latest.get("raw_NetIncome")) or _f(cj.get("NetIncome"))
    fcf = _f(latest.get("derived_FCF") or latest.get("clean_FCF")) or _f(cj.get("FCF"))
    sbc = _f(latest.get("clean_SBC")) or _f(cj.get("SBC"))
    eps = _f(latest.get("clean_EPS_Diluted")) or _f(cj.get("EPS_Diluted"))
    dps = _f(latest.get("clean_DividendsPerShare")) or _f(cj.get("DividendsPerShare"))
    payout = _f(latest.get("clean_PayoutRatio")) or _f(cj.get("PayoutRatio"))

    total_assets = _f(latest.get("raw_TotalAssets")) or _f(cj.get("TotalAssets"))
    total_equity = _f(latest.get("raw_TotalEquity")) or _f(cj.get("TotalEquity"))
    long_debt = (_f(latest.get("raw_LongTermDebt")) or _f(cj.get("LongTermDebt"))
                 or _f(cj.get("TotalDebt")))
    cash = _f(latest.get("raw_Cash")) or _f(cj.get("Cash"))
    net_debt = _f(latest.get("derived_NetDebt")) or _f(cj.get("NetDebt"))
    cur_assets = _f(latest.get("raw_CurrentAssets")) or _f(cj.get("CurrentAssets"))
    cur_liab = _f(latest.get("raw_LiabilitiesCurrent")) or _f(cj.get("LiabilitiesCurrent"))
    inv = _f(latest.get("raw_Inventory")) or _f(cj.get("Inventory"))
    interest_exp = (_f(latest.get("raw_InterestExpense"))
                    or _f(cj.get("InterestExpense")))
    shares = (_f(latest.get("clean_SharesDiluted") or latest.get("raw_SharesDiluted"))
              or _f(cj.get("SharesDiluted")))

    # EPS fallback: IFRS/ADR filers (e.g. TSM) often lack a clean per-share EPS
    # in the frame, leaving P/E blank. Derive it from net income / diluted
    # shares — both on the SAME USD/ADR-equivalent basis as price and
    # market_cap, so the ratio is consistent (no currency/basis mismatch).
    if eps is None and ni is not None and shares and shares > 0:
        eps = ni / shares

    # EV
    ev = (market_cap + (net_debt or 0)) if market_cap else None

    profitability = {
        "gross_margin":   _pct(gross_profit, rev),
        "ebit_margin":    _pct(opinc, rev),
        "ebitda_margin":  _pct(ebitda, rev),
        "net_margin":     _pct(ni, rev),
        "fcf_margin":     _pct(fcf, rev),
        "roic":           _f(latest.get("derived_ROIC")),
        "roe":            _f(latest.get("derived_ROE")),
        "roa":            _pct(ni, total_assets),
    }

    liquidity = {
        "current_ratio":  _pct(cur_assets, cur_liab),
        "quick_ratio":    _pct(
            ((cur_assets or 0) - (inv or 0)) if (cur_assets is not None) else None,
            cur_liab,
        ),
        "cash_ratio":     _pct(cash, cur_liab),
    }

    leverage = {
        "debt_to_equity":       _pct(long_debt, total_equity),
        "debt_to_ebitda":       _pct(long_debt, ebitda),
        "net_debt_to_ebitda":   _pct(net_debt, ebitda),
        "interest_coverage":    _pct(opinc, interest_exp),
    }

    valuation = {
        "pe":             _pct(current_price, eps),
        "pb":             _pct(market_cap, total_equity),
        "ps":             _pct(market_cap, rev),
        "ev_sales":       _pct(ev, rev),
        "ev_ebitda":      _pct(ev, ebitda),
        "fcf_yield":      _pct(fcf, market_cap),
        "dividend_yield": _pct(dps, current_price) if dps and current_price else None,
    }

    # SBC % of FCF — always recompute from primitives.
    # The cleaning_engine's ``clean_SBC_PctFCF`` column stores the value
    # in PERCENT form (19.26 for 19.26%), while every other ratio in
    # this helper is a decimal (0.1926). The HTML renderer multiplies
    # by 100 for display, so trusting the column produces "1925%"
    # for any ticker where it's populated (ACN was the canonical
    # case). Recomputing from raw SBC + FCF guarantees decimal units
    # and survives any future schema drift in the column.
    sbc_pct_fcf = _pct(sbc, fcf)

    # Share dilution % — same recompute-from-primitives policy.
    # ``clean_ShareDilution_Pct`` is also stored in percent form
    # by the legacy cleaning engine.
    shares_basic = _f(latest.get("raw_SharesBasic")) or _f(cj.get("SharesBasic"))
    if shares_basic and shares_basic > 0 and shares is not None:
        dilution_pct = (shares - shares_basic) / shares_basic
    else:
        dilution_pct = None

    quality = {
        "sbc_pct_fcf":        sbc_pct_fcf,
        "share_dilution_pct": dilution_pct,
        "payout_ratio":       payout,
        # Accrual ratio = (NI - OperatingCF) / TotalAssets
        # Negative = high earnings quality (cash exceeds reported earnings).
        "accrual_ratio": _pct(
            ((ni or 0) - _f(latest.get("clean_FCF") or latest.get("derived_FCF"))) if ni else None,
            total_assets,
        ),
    }

    # Growth — needs multi-year history
    revs_by_yr = [(_f(r.get("clean_Revenue")), int(r["fiscal_year"])) for _, r in fy.iterrows()]
    revs_by_yr = [(v, y) for v, y in revs_by_yr if v is not None and v > 0]
    growth = {
        "revenue_cagr_1y": (
            _cagr(revs_by_yr[-2][0], revs_by_yr[-1][0], 1)
            if len(revs_by_yr) >= 2 else None
        ),
        "revenue_cagr_3y": _trailing_cagr(revs_by_yr, 3),
        "revenue_cagr_5y": _trailing_cagr(revs_by_yr, 5),
    }
    ebitda_series = [(_f(r.get("derived_EBITDA")), int(r["fiscal_year"]))
                     for _, r in fy.iterrows()]
    ebitda_series = [(v, y) for v, y in ebitda_series if v is not None and v > 0]
    growth["ebitda_cagr_5y"] = _trailing_cagr(ebitda_series, 5)

    fcf_series = [(_f(r.get("derived_FCF")), int(r["fiscal_year"]))
                  for _, r in fy.iterrows()]
    fcf_series = [(v, y) for v, y in fcf_series if v is not None and v > 0]
    growth["fcf_cagr_5y"] = _trailing_cagr(fcf_series, 5)

    eps_series = [(_f(r.get("clean_EPS_Diluted")), int(r["fiscal_year"]))
                  for _, r in fy.iterrows()]
    eps_series = [(v, y) for v, y in eps_series if v is not None and v > 0]
    if len(eps_series) >= 2:
        growth["eps_growth_ttm"] = (
            (eps_series[-1][0] - eps_series[-2][0]) / eps_series[-2][0]
        )
    else:
        growth["eps_growth_ttm"] = None

    # Financials guard (CF-R21): null the metrics that are MEANINGLESS on a
    # deposit-funded balance sheet so the Comprehensive Ratios card stops printing
    # conclusion-inverting garbage (JPM: ROIC 4.6% "weak", EV/EBITDA −17.8×, Net-
    # Debt/EBITDA −28× from deposits-as-net-cash, interest coverage 0.8×, FCF
    # margin 49.4% from balance-sheet flows). Keep the bank-valid ones: P/E, P/B,
    # P/S, net margin, ROE, ROA, dividend yield, debt/equity, payout.
    if is_financial:
        for k in ("roic", "fcf_margin"):
            profitability[k] = None
        for k in ("current_ratio", "quick_ratio", "cash_ratio"):
            liquidity[k] = None
        for k in ("debt_to_ebitda", "net_debt_to_ebitda", "interest_coverage"):
            leverage[k] = None
        for k in ("ev_sales", "ev_ebitda", "fcf_yield"):
            valuation[k] = None
        for k in ("ebitda_cagr_5y", "fcf_cagr_5y"):
            growth[k] = None

    out = {
        "profitability": profitability,
        "liquidity":     liquidity,
        "leverage":      leverage,
        "valuation":     valuation,
        "quality":       quality,
        "growth":        growth,
    }
    if is_financial:
        out["financials_na_note"] = (
            "EV multiples, EBITDA-leverage, ROIC, and FCF ratios are omitted — "
            "they are not meaningful for a bank/insurer/lender (deposits are not "
            "debt; there is no unlevered FCF or invested-capital base). Read P/E, "
            "P/B, ROE, ROA and the bank operating metrics instead.")
    return out


def _dcf_assumptions_from_result(dcf_result) -> Dict[str, Dict[str, Optional[float]]]:
    """Extract per-scenario assumption stack from a DCFResult. Handles
    the case where dcf_result is missing or a scenario is None."""
    out: Dict[str, Dict[str, Optional[float]]] = {}
    if dcf_result is None:
        return out
    for name in ("bull", "base", "bear"):
        scenario = getattr(dcf_result, name, None)
        if scenario is None:
            continue
        a = getattr(scenario, "assumptions", None)
        if a is None:
            continue
        out[name] = {
            "revenue_cagr_y1_5":   _f(getattr(a, "revenue_cagr_y1_5", None)),
            "revenue_cagr_y6_10":  _f(getattr(a, "revenue_cagr_y6_10", None)),
            "ebit_margin_terminal": _f(getattr(a, "ebit_margin_terminal", None)),
            "terminal_growth":     _f(getattr(a, "terminal_growth", None)),
            "wacc":                _f(getattr(a, "wacc", None)),
            "tax_rate":            _f(getattr(a, "tax_rate", None)),
            "capex_pct_revenue":   _f(getattr(a, "capex_pct_revenue", None)),
            "nwc_pct_revenue":     _f(getattr(a, "nwc_pct_revenue", None)),
            "da_pct_revenue":      _f(getattr(a, "da_pct_revenue", None)),
        }
    return out


def _wacc_build_from_result(
    dcf_result,
    p2: Optional[Dict[str, Any]] = None,
    latest_fy_row=None,    # pandas.Series — latest FY row for debt + interest
) -> Dict[str, Optional[float]]:
    """Component decomposition of WACC. Pulls Rf + β + ERP from the
    DCFResult when available; computes Kd + capital weights from the
    latest FY balance-sheet row using the central cost-of-debt formula.

    Fixes the prior bug where Kd / weight_equity / weight_debt were
    hardcoded to None — which made the HTML render fall back to the
    "net-cash filer" cosmetic note even on filers with positive net
    debt (LDOS, MU, AAPL). Now: when total_debt > 0, we render the full
    Ke × We + Kd(1-t) × Wd build; only filers with truly zero or negative
    debt get the "net-cash" treatment.
    """
    p2 = p2 or {}

    rf = _f(getattr(dcf_result, "risk_free_rate", None) or p2.get("risk_free_rate"))
    beta = _f(getattr(dcf_result, "beta", None) or p2.get("beta"))
    from aletheia.calculations.macro import market_risk_premium as _mrp
    erp = _f(p2.get("equity_risk_premium") or _mrp())
    ke = (rf or 0) + (beta or 0) * (erp or 0) if (rf is not None and beta is not None) else None
    wacc = _f(getattr(dcf_result, "wacc_base", None) or p2.get("wacc"))

    # Compute Kd + capital weights from the latest FY balance sheet.
    # Falls back to clean_json when the top-level DB column is missing —
    # same two-tier lookup pattern as the ratios helper.
    kd_pretax = None
    kd_aftertax = None
    weight_equity = None
    weight_debt = None
    tax_rate_used = None

    if latest_fy_row is not None:
        import json as _json
        cj = {}
        raw_cj = latest_fy_row.get("clean_json")
        if isinstance(raw_cj, str):
            try:
                cj = _json.loads(raw_cj) or {}
            except _json.JSONDecodeError:
                cj = {}

        long_debt = (_f(latest_fy_row.get("raw_LongTermDebt"))
                     or _f(cj.get("LongTermDebt"))
                     or _f(cj.get("TotalDebt")))
        short_debt = (_f(latest_fy_row.get("raw_ShortTermDebt"))
                      or _f(cj.get("ShortTermDebt")) or 0.0)
        total_debt = (long_debt or 0.0) + (short_debt or 0.0)
        interest_exp = (_f(latest_fy_row.get("raw_InterestExpense"))
                        or _f(cj.get("InterestExpense")))
        # Tax rate: prefer GAAP from cleaning engine, else fall back to
        # statutory 21% (US corporate rate post-TCJA).
        tax_rate_used = (_f(latest_fy_row.get("clean_GAAP_TaxRate"))
                         or _f(cj.get("GAAP_TaxRate")) or 0.21)

        market_cap = _f(p2.get("market_cap"))
        if total_debt > 0 and interest_exp is not None and interest_exp > 0:
            # Effective interest rate; floored at risk-free + 50bps so
            # filers with sub-market reported rates still get a defensible
            # cost-of-debt for WACC purposes.
            kd_pretax_raw = interest_exp / total_debt
            kd_floor = (rf or 0.035) + 0.005
            kd_pretax = max(kd_pretax_raw, kd_floor)
            kd_aftertax = kd_pretax * (1.0 - tax_rate_used)

        # Capital-structure WEIGHTS use the engine's exact WACC debt figure
        # (wacc_total_debt) so this build and the §7 discount detail agree on the
        # same weights; the Kd above keeps the book LTD+STD basis.
        weight_debt_basis = getattr(dcf_result, "wacc_total_debt", None)
        if not isinstance(weight_debt_basis, (int, float)) or weight_debt_basis <= 0:
            weight_debt_basis = total_debt
        if market_cap and weight_debt_basis and weight_debt_basis > 0:
            ev_total = market_cap + weight_debt_basis
            weight_equity = market_cap / ev_total
            weight_debt = weight_debt_basis / ev_total

    return {
        "risk_free_rate":      rf,
        "beta":                beta,
        "equity_risk_premium": erp,
        "cost_of_equity":      ke,
        "wacc":                wacc,
        "cost_of_debt_pretax":   kd_pretax,
        "cost_of_debt_aftertax": kd_aftertax,
        "weight_equity":         weight_equity,
        "weight_debt":           weight_debt,
        "tax_rate":              tax_rate_used,
    }


def build_financial_metrics(
    ticker: str,
    db,                   # InvestmentDatabase
    dcf_result=None,      # DCFResult from calc_node, optional
    p2: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the ``5_financial_metrics`` block. Pure data + light
    arithmetic — no LLM, no network. Designed to be called once during
    Stage 4 report assembly (lead.py) and the result stamped onto the
    serving JSON."""
    # Use the CURRENCY-CONVERTED frame (TWD/EUR/DKK → USD) so the valuation
    # ratios — which divide a USD market_cap/price by these denominators —
    # are on ONE currency basis. ``db.get_latest`` returns the raw filing
    # currency (TSM in TWD, ASML in EUR), which silently deflated every
    # multiple by the FX rate (~31× for TWD) and inverted EV negative.
    # Margins/growth are currency-invariant, so this is safe for USD filers
    # (no conversion applied) and corrects foreign filers.
    is_financial = False
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        _ci = make_calc_input(ticker, apply_overrides=False)
        df = _ci.df
        from aletheia.calculations.sector_classification import is_financial_filer
        _cls = getattr(_ci, "classification", None)
        is_financial = is_financial_filer(
            getattr(_cls, "sector", "") or "", getattr(_cls, "industry", "") or "",
            getattr(_cls, "business_model", "") or "")
    except Exception:
        df = db.get_latest(ticker)   # fallback: raw frame (prior behavior)
    p2 = p2 or {}
    market_cap = _f(p2.get("market_cap"))
    current_price = _f(p2.get("current_price"))

    # Latest FY row for Kd / capital weights computation in WACC build.
    latest_fy_row = None
    if df is not None and not df.empty:
        fy = df[df.get("period", "FY") == "FY"].sort_values("fiscal_year")
        if not fy.empty:
            latest_fy_row = fy.iloc[-1]

    return {
        "historical":      _historical_rows(df),
        "ratios":          _compute_ratios(df, market_cap, current_price, is_financial),
        "dcf_assumptions": _dcf_assumptions_from_result(dcf_result),
        "wacc_build":      _wacc_build_from_result(dcf_result, p2, latest_fy_row),
    }


__all__ = ["build_financial_metrics"]
