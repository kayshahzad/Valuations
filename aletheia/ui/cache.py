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
    result = DCFEngine(verbose=False).run(calc)

    def _scenario(s, label: str) -> Optional[Dict[str, Any]]:
        if s is None:
            return None
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
        "warnings":       list(result.warnings or []),
        "errors":         list(result.errors or []),
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
def cached_macro_context() -> Dict[str, float]:
    """Current Rf, ERP — used for the dashboard header context strip."""
    from aletheia.data.historical_macro import (
        get_risk_free_rate, get_equity_risk_premium,
    )
    today = date.today()
    return {
        "rf":  get_risk_free_rate(today),
        "erp": get_equity_risk_premium(today),
    }


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
