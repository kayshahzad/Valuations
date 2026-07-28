"""
aletheia/ui/cache.py

Cached wrappers for the calc layer + scenario library so the Streamlit UI
can invoke them on every interaction without paying re-computation cost.

Design constraints:
  - Streamlit's @st.cache_data hashes inputs to detect cache hits. Pydantic
    BaseModels and frozen dataclasses don't always hash cleanly. We work
    around this by accepting strings (ticker, scenario_name) at the cache
    boundary and reconstructing rich objects internally.
  - TTL = 15 minutes: long enough that flipping tickers feels instant,
    short enough that re-cleaning the DB invalidates within a working session.

All functions here return JSON-serializable dicts (or DataFrames). The
caller reconstructs Pydantic objects when needed.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st


_TTL_SEC = 15 * 60  # 15 minutes


@st.cache_data(ttl=_TTL_SEC, show_spinner=False)
def cached_calc_df(ticker: str) -> pd.DataFrame:
    """Cached cleaned-records DataFrame for a ticker."""
    from aletheia.utils.calc_input_builder import make_calc_input
    return make_calc_input(ticker).df.copy()


@st.cache_data(ttl=_TTL_SEC, show_spinner=False)
def cached_dcf_summary(ticker: str) -> Dict[str, Any]:
    """
    Run the production DCF for the ticker and return a JSON-safe summary
    suitable for the dashboard (no Pydantic objects in the output).
    """
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.dcf_engine import DCFEngine

    calc = make_calc_input(ticker)
    try:
        result = DCFEngine(verbose=False).run(calc)
    except NotImplementedError as e:
        # Tickers classified as ddm_required / embedded_value_required /
        # routing_required (banks, insurers, asset managers) intentionally
        # raise here. Dashboard catches the sentinel and renders an
        # explanatory panel instead of the standard scenario cards.
        from config.ticker_classification import UNIVERSE
        meta = UNIVERSE.get(ticker.upper())
        return {
            "error":          "specialized_model_required",
            "ticker":         ticker.upper(),
            "business_model": meta.business_model if meta else None,
            "reason":         meta.notes if meta else None,
            "message":        str(e),
        }

    def _scenario(s, label: str) -> Optional[Dict[str, Any]]:
        if s is None:
            return None
        # Headline IV suppressed (pre-profitability / degenerate base scenario):
        # do NOT surface a fabricated per-share number for any scenario. The
        # projections are structurally meaningless when NOPAT seeds negative.
        if result.iv_suppressed:
            ips = 0.0
        else:
            ips = result.intrinsic_per_share(s.enterprise_value, result.net_debt) or 0.0
        a = s.assumptions
        upside = (
            (ips - result.current_price) / result.current_price
            if ips and result.current_price > 0 else None
        )
        return {
            "label":            label,
            "enterprise_value": float(s.enterprise_value),
            "iv_per_share":     float(ips) if ips else None,
            "upside_pct":       float(upside) if upside is not None else None,
            "wacc":             float(a.wacc),
            "terminal_growth":  float(a.terminal_growth),
            "terminal_margin":  float(a.ebit_margin_terminal),
            "y1_5_cagr":        float(a.revenue_cagr_y1_5),
            "y6_10_cagr":       float(a.revenue_cagr_y6_10),
            "capex_pct":        float(a.capex_pct_revenue),
            "tax_rate":         float(a.tax_rate),
            "implied_ev_ebitda":   float(s.implied_ev_ebitda),
            "justified_ev_ebitda": float(s.justified_ev_ebitda),
            "roic_wacc_spread":    float(s.roic_wacc_spread),
        }

    return {
        "ticker":         result.ticker,
        "fiscal_year":    int(result.fiscal_year),
        "current_price":  float(result.current_price),
        "shares_diluted": float(result.shares_diluted),
        "market_cap":     float(result.market_cap),
        "risk_free_rate": float(result.risk_free_rate),
        "beta":           float(result.beta),
        "wacc_base":      float(result.wacc_base),
        "revenue":        float(result.revenue),
        "ebitda":         float(result.ebitda),
        "ebit":           float(result.ebit),
        "roic":           float(result.roic),
        "fcf":            float(result.fcf),
        "net_debt":       float(result.net_debt),
        "bull": _scenario(result.bull, "Bull"),
        "base": _scenario(result.base, "Base"),
        "bear": _scenario(result.bear, "Bear"),
        "iv_suppressed":        bool(result.iv_suppressed),
        "iv_suppressed_reason": result.iv_suppressed_reason,
        "warnings":       list(result.warnings or []),
        "errors":         list(result.errors or []),
    }


@st.cache_data(ttl=_TTL_SEC, show_spinner=False)
def cached_valuation(ticker: str) -> Optional[Dict[str, Any]]:
    """Engine-agnostic valuation via the ValuationRouter.

    Returns the IV/MoS produced by whichever engine the ticker's
    business_model dispatches to (FCFF, rate_base, DDM, embedded_value).
    Unlike ``cached_dcf_summary`` — which is FCFF-only and stamps an
    ``error`` sentinel for non-FCFF tickers — this surface gives the
    dashboard the engine output for *every* ticker, so utilities /
    banks / insurers / managed-care render their real intrinsic value
    instead of a "no DCF available" warning.
    """
    from aletheia.tools.valuation_router import (
        UnknownBusinessModelError, ValuationRouter,
    )
    from aletheia.utils.calc_input_builder import make_calc_input

    try:
        calc = make_calc_input(ticker)
        result = ValuationRouter().execute(calc)
    except NotImplementedError as e:
        return {"error": "bypass", "message": str(e), "ticker": ticker.upper()}
    except UnknownBusinessModelError as e:
        return {"error": "unknown_business_model", "message": str(e),
                "ticker": ticker.upper()}
    except Exception as e:
        return {"error": type(e).__name__, "message": str(e),
                "ticker": ticker.upper()}

    snap = result.inputs_snapshot or {}
    decomposition = (result.engine_specific or {}).get("decomposition")

    # Bank convergent set (residual income / justified P/B / Gordon / FCFE) so the
    # dashboard can show ALL the methods, not just the routed headline — gated on
    # bank reality, so non-banks get available=False and nothing renders.
    bank_methods = None
    headline_override = None
    try:
        from aletheia.tools.bank_valuation_methods import (
            build_bank_valuation_methods, bank_headline_override,
        )
        _bm = build_bank_valuation_methods(calc, result, p2={"engine": result.engine})
        bank_methods = _bm if _bm.get("available") else None
        # Method-appropriate headline (CF-R19): swap the structurally-low DDM IV/MoS
        # for the convergent-set residual-income fair value when understatement is
        # flagged, so the dashboard headline matches the convergent set.
        headline_override = bank_headline_override(
            ddm_ips=(float(result.intrinsic_per_share)
                     if result.intrinsic_per_share is not None else None),
            price=(float(result.current_price) if result.current_price else None),
            bvm=bank_methods)
    except Exception:
        bank_methods = None

    _ips = (float(result.intrinsic_per_share)
            if result.intrinsic_per_share is not None else None)
    _mos = (float(result.margin_of_safety)
            if result.margin_of_safety is not None else None)
    if headline_override:
        _ips = headline_override["intrinsic_per_share"]
        _mos = headline_override["margin_of_safety"]

    return {
        "ticker":              result.ticker,
        "fiscal_year":         int(result.fiscal_year),
        "engine":              result.engine,
        "intrinsic_per_share": _ips,
        "equity_value":        (float(result.equity_value)
                                if result.equity_value is not None else None),
        "current_price":       (float(result.current_price)
                                if result.current_price is not None else None),
        "margin_of_safety":    _mos,
        "headline_override":   headline_override,
        "cost_of_equity":      snap.get("cost_of_equity"),
        "risk_free_rate":      snap.get("risk_free_rate"),
        "beta":                snap.get("beta"),
        "shares_diluted":      snap.get("shares_diluted"),
        "source":              snap.get("source"),
        "as_of_date":          snap.get("as_of_date"),
        "inputs_snapshot":     {k: v for k, v in snap.items()
                                if isinstance(v, (int, float, str, bool, type(None)))},
        "decomposition":       decomposition,
        "bank_valuation_methods": bank_methods,
        "notes":               result.notes,
        "warnings":            list(result.warnings or []),
    }


@st.cache_data(ttl=_TTL_SEC, show_spinner=False)
def cached_library_scenario(ticker: str, scenario_name: str) -> Optional[Dict[str, Any]]:
    """
    Run a Phase 3 library scenario by name. Returns JSON-safe summary or
    None if the scenario raises NotImplementedError or other errors.

    `scenario_name` must be one of the keys in
    `aletheia.scenarios.library.LIBRARY`.
    """
    from aletheia.scenarios.library import LIBRARY, run_scenario

    if scenario_name not in LIBRARY:
        return None
    try:
        r = run_scenario(ticker, LIBRARY[scenario_name])
    except NotImplementedError as e:
        return {"error": "stub", "message": str(e), "scenario_name": scenario_name}
    except Exception as e:
        return {"error": type(e).__name__, "message": str(e), "scenario_name": scenario_name}

    return {
        "scenario_name":  scenario_name,
        "label":          r.scenario_name,
        "rationale":      r.rationale,
        "iv_per_share":   r.base_iv,
        "current_price":  r.current_price,
        "upside_pct":     r.upside_pct,
        "wacc":           r.base_wacc,
        "terminal_growth": r.base_terminal_growth,
        "terminal_margin": r.base_terminal_margin,
        "capex_pct":      r.base_capex_pct,
        "y1_5_cagr":      r.override.revenue_growth_y1_5,
        "y6_10_cagr":     r.override.revenue_growth_y6_10,
        "notes":          r.notes,
    }


@st.cache_data(ttl=_TTL_SEC, show_spinner=False)
def cached_macro_context() -> Dict[str, Any]:
    """Current Rf, ERP — the dashboard header context strip. Must match what the
    valuation engines use, NOT the historical parquet: rf comes from the live
    10-year (market_data.get_risk_free_rate, ^TNX); erp from the Damodaran
    implied-ERP series (same source as macro.market_risk_premium). The
    historical_macro.get_risk_free_rate(date) path is ^IRX (13-week) + a parquet
    that lags — correct for point-in-time backtests, wrong for a 'current' strip."""
    from aletheia.data.market_data import get_risk_free_rate as _live_rf
    from aletheia.data.historical_macro import get_equity_risk_premium
    today = date.today()
    out: Dict[str, Any] = {
        "rf":  _live_rf(),
        "erp": get_equity_risk_premium(today),
    }
    # Layer-11 credit regime (IG/HY OAS from FRED). Fail-soft: absent → no
    # credit line on the header, never an error.
    try:
        from aletheia.data.fred_client import get_credit_regime
        regime = get_credit_regime()
        if regime is not None:
            out["credit"] = regime
    except Exception:
        pass
    return out


@st.cache_data(ttl=_TTL_SEC, show_spinner=False)
def cached_distress_screen(ticker: str) -> Optional[Dict[str, Any]]:
    """Altman distress screen (Phase 1a) as a JSON-safe dict for the Deep Dive
    capital-risk section. Fail-soft: returns {'error': ...} on failure so the
    caller can skip the block rather than break the page."""
    from dataclasses import asdict
    try:
        from aletheia.data.database import InvestmentDatabase
        from aletheia.tools.altman_screen import screen_ticker
        db = InvestmentDatabase(verbose=False)
        return asdict(screen_ticker(db, ticker))
    except Exception as e:  # never break the page on a screen failure
        return {"error": type(e).__name__, "message": str(e)}


@st.cache_data(ttl=_TTL_SEC, show_spinner=False)
def cached_classification(ticker: str) -> Dict[str, Any]:
    """Read the ticker's static classification (sector, industry, lifecycle)."""
    from config.ticker_classification import UNIVERSE
    c = UNIVERSE.get(ticker)
    if c is None:
        return {}
    return {
        "ticker":         c.ticker,
        "sector":         c.sector,
        "industry":       c.industry,
        "lifecycle":      c.lifecycle,
        "business_model": c.business_model,
        "is_ifrs_filer":  c.is_ifrs_filer,
        "notes":          c.notes,
    }


@st.cache_data(ttl=_TTL_SEC, show_spinner=False)
def cached_known_issues(ticker: str) -> List[Dict[str, Any]]:
    """Surface KNOWN_ISSUES entries for the ticker (override visibility)."""
    from config.known_issues import KNOWN_ISSUES
    issues = KNOWN_ISSUES.get(ticker, []) or []
    out: List[Dict[str, Any]] = []
    for it in issues:
        out.append({
            "issue_type":  getattr(it, "issue_type", None),
            "field":       getattr(it, "field", None),
            "description": getattr(it, "description", None),
            "workaround":  getattr(it, "workaround", None),
            "fy_min":      getattr(it, "fiscal_year_min", None),
            "fy_max":      getattr(it, "fiscal_year_max", None),
        })
    return out


@st.cache_data(ttl=_TTL_SEC, show_spinner=False)
def cached_screening_card(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Run the screening engine and return a JSON-safe summary INCLUDING an
    explicit `metrics` list (each item is a dict with name/category/value/
    threshold/signal/note). The dashboard's Top-5 ratios section consumes
    this list by metric name. ScreeningCard.to_dict() flattens metrics into
    top-level keys which is unsuitable for name-based selection.
    """
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.screening_ratios import ScreeningEngine
    except ImportError:
        return None

    try:
        calc = make_calc_input(ticker)
        engine = ScreeningEngine()
        card = engine.score(calc)
    except Exception as e:
        return {"error": type(e).__name__, "message": str(e)}

    metrics_list = []
    for m in (getattr(card, "metrics", []) or []):
        v = m.value
        if isinstance(v, float) and (v != v):  # NaN check
            v = None
        metrics_list.append({
            "name":      m.name,
            "category":  m.category,
            "authority": m.authority,
            "value":     v,
            "threshold": m.threshold,
            "signal":    m.signal,
            "note":      m.note,
        })

    return {
        "ticker":         card.ticker,
        "fiscal_year":    int(card.fiscal_year),
        "current_price":  float(card.current_price or 0),
        "market_cap_bn":  float(card.market_cap / 1e9) if card.market_cap else None,
        "passes":         int(card.passes),
        "flags":          int(card.flags),
        "fails":          int(card.fails),
        "available":      int(card.available),
        "metrics":        metrics_list,
    }
