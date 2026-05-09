"""FMP Compare tab — side-by-side ticker-level comparison.

Renders every line item we ingest (raw, derived, ratios, multiples,
market) alongside FMP's value for the same period, with drift % and
a status glyph. Pulls our data from DuckDB and FMP from the cached
fmp_client blobs (no live fetches; whatever's on disk is what we see).

Two sections per category:
  - **Latest FY** — last 10-K's annual figures
  - **TTM** — trailing-twelve-month roll-up (when ingested)

Status glyph is the same `_classify_drift` taxonomy as the validation
gates: ✓ byte_perfect, ≈ acceptable, ✗ structural drift, — n/a.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from aletheia.data import fmp_client
from aletheia.data.database import InvestmentDatabase
from aletheia.data.fmp_validation_core import _drift_label


# ── Drift classification + glyph ──────────────────────────────────────

_TIER_BANDS = {
    "strict":       (0.005, 0.02),
    "standard":     (0.01,  0.05),
    "definitional": (0.05,  0.25),
}


def _status(drift_pct: Optional[float], tier: str) -> str:
    if drift_pct is None:
        return "—"
    bp, accept = _TIER_BANDS.get(tier, _TIER_BANDS["standard"])
    abs_d = abs(drift_pct)
    if abs_d < bp:
        return "✓"
    if abs_d < accept:
        return "≈"
    return "✗"


# ── Field formatters ──────────────────────────────────────────────────

def _is_missing(v: Any) -> bool:
    """DuckDB returns numpy.nan for null DOUBLE columns; treat both
    None and nan as missing. Strings/zero-valued numbers pass through."""
    if v is None:
        return True
    try:
        import math
        return isinstance(v, float) and math.isnan(v)
    except Exception:
        return False


def _money_b(v: Optional[float]) -> str:
    if _is_missing(v):
        return "—"
    return f"${v / 1e9:,.2f}B"


def _money_m(v: Optional[float]) -> str:
    if _is_missing(v):
        return "—"
    return f"${v / 1e6:,.1f}M"


def _money_per_share(v: Optional[float]) -> str:
    if _is_missing(v):
        return "—"
    return f"${v:,.2f}"


def _pct(v: Optional[float]) -> str:
    if _is_missing(v):
        return "—"
    return f"{v * 100:,.2f}%"


def _ratio(v: Optional[float]) -> str:
    if _is_missing(v):
        return "—"
    return f"{v:,.2f}×"


def _scalar(v: Optional[float]) -> str:
    if _is_missing(v):
        return "—"
    return f"{v:,.4f}"


def _shares(v: Optional[float]) -> str:
    if _is_missing(v):
        return "—"
    return f"{v / 1e9:,.2f}B"


# ── Side-by-side row builder ──────────────────────────────────────────

# Each row is a (label, our_value, fmp_value, formatter, tier, our_scale)
# `our_scale` lets us normalize when our side is stored in different
# units (e.g., percent vs decimal — see EBIT_Margin).
_RowSpec = Tuple[str, Optional[float], Optional[float], Callable, str, float]


def _build_compare_table(rows: List[_RowSpec]) -> pd.DataFrame:
    """Build the side-by-side DataFrame with drift columns."""
    out = []
    for label, ours, fmp, fmt, tier, scale in rows:
        # DuckDB returns nan for null DOUBLE — coerce to None before
        # arithmetic so drift % doesn't render as +nan%.
        ours_clean = None if _is_missing(ours) else ours
        fmp_clean  = None if _is_missing(fmp)  else fmp
        ours_normalized = ours_clean * scale if (ours_clean is not None) else None
        _, drift = _drift_label(ours_normalized, fmp_clean)
        out.append({
            "Field":  label,
            "Ours":   fmt(ours_normalized),
            "FMP":    fmt(fmp_clean),
            "Drift":  (f"{drift * 100:+.2f}%"
                       if isinstance(drift, (int, float)) and not _is_missing(drift)
                       else "—"),
            "":       _status(drift if not _is_missing(drift) else None, tier),
        })
    return pd.DataFrame(out)


def _render_table(rows: List[_RowSpec]) -> None:
    df = _build_compare_table(rows)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Field":  st.column_config.TextColumn("Field",  width="medium"),
            "Ours":   st.column_config.TextColumn("Ours",   width="small"),
            "FMP":    st.column_config.TextColumn("FMP",    width="small"),
            "Drift":  st.column_config.TextColumn("Drift",  width="small"),
            "":       st.column_config.TextColumn("",       width="small",
                       help="✓ byte-perfect (<1% strict / <5% standard) · ≈ acceptable · ✗ structural drift"),
        },
    )


# ── Data loaders ──────────────────────────────────────────────────────

def _load_local(ticker: str) -> Tuple[Optional[Dict], Optional[Dict]]:
    """Pull the latest FY row + the TTM row (if any) from DuckDB."""
    db = InvestmentDatabase(verbose=False)
    try:
        df = db.query(
            "SELECT * FROM company_records_latest WHERE ticker=? "
            "ORDER BY period_end_date DESC",
            [ticker.upper()],
        )
    finally:
        db.close()
    if df is None or df.empty:
        return None, None

    fy_df  = df[df["period"] == "FY"] if "period" in df.columns else df
    ttm_df = df[df["period"] == "TTM"] if "period" in df.columns else df.iloc[0:0]

    fy_row  = fy_df.iloc[0].to_dict()  if not fy_df.empty  else None
    ttm_row = ttm_df.iloc[0].to_dict() if not ttm_df.empty else None
    return fy_row, ttm_row


def _load_fmp(ticker: str, target_fy: Optional[int]) -> Dict[str, Any]:
    """Pull FMP blobs for the comparison. All cached; no live fetches."""
    out: Dict[str, Any] = {}
    try:
        inc = fmp_client.fetch_income_statements(ticker, period="annual")
        bs  = fmp_client.fetch_balance_sheets(ticker, period="annual")
        cf  = fmp_client.fetch_cash_flows(ticker, period="annual")
        km  = fmp_client.fetch_key_metrics(ticker, period="annual")
        rt  = fmp_client.fetch_ratios(ticker, period="annual")
        ev  = fmp_client.fetch_enterprise_values(ticker, period="annual")
    except Exception:
        inc = bs = cf = km = rt = ev = None

    def _pick(records, fy):
        if not records or fy is None:
            return {}
        return fmp_client.get_for_fiscal_year(records, fy) or {}

    out["fy"] = {
        "income":      _pick(inc, target_fy),
        "balance":     _pick(bs,  target_fy),
        "cashflow":    _pick(cf,  target_fy),
        "key_metrics": _pick(km,  target_fy),
        "ratios":      _pick(rt,  target_fy),
        "ev":          _pick(ev,  target_fy),
    }
    out["ttm"] = {
        "key_metrics": fmp_client.fetch_key_metrics_ttm(ticker) or {},
        "ratios":      fmp_client.fetch_ratios_ttm(ticker) or {},
    }
    out["profile"] = fmp_client.fetch_profile(ticker) or {}
    return out


# ── Section builders (FY) ─────────────────────────────────────────────

def _income_rows_fy(local: Dict, fmp: Dict) -> List[_RowSpec]:
    L = local or {}
    F = fmp or {}
    return [
        ("Revenue",            L.get("clean_Revenue"),         F.get("revenue"),         _money_b, "strict",       1.0),
        ("COGS",               L.get("raw_COGS"),              F.get("costOfRevenue"),   _money_b, "standard",     1.0),
        ("Gross Profit",       (L.get("clean_Revenue") or 0) - (L.get("raw_COGS") or 0)
                                if L.get("clean_Revenue") and L.get("raw_COGS") else None,
                                F.get("grossProfit"),          _money_b, "standard",     1.0),
        ("R&D",                L.get("raw_RnD"),               F.get("researchAndDevelopmentExpenses"),
                                                                                          _money_b, "standard",     1.0),
        ("Operating Income",   L.get("raw_OperatingIncome") or L.get("derived_OperatingIncome"),
                                F.get("operatingIncome"),      _money_b, "standard",     1.0),
        ("EBITDA",             L.get("derived_EBITDA"),        F.get("ebitda"),          _money_b, "definitional", 1.0),
        ("Net Income",         L.get("raw_NetIncome"),         F.get("netIncome"),       _money_b, "strict",       1.0),
        ("Shares Diluted",     L.get("raw_SharesDiluted"),     F.get("weightedAverageShsOutDil"),
                                                                                          _shares,  "strict",       1.0),
    ]


def _balance_rows_fy(local: Dict, fmp: Dict) -> List[_RowSpec]:
    L = local or {}
    F = fmp or {}
    return [
        ("Total Assets",       L.get("raw_TotalAssets"),       F.get("totalAssets"),         _money_b, "strict",   1.0),
        ("Total Liabilities",  L.get("raw_TotalLiabilities"),  F.get("totalLiabilities"),    _money_b, "standard", 1.0),
        ("Total Equity",       L.get("raw_TotalEquity"),       F.get("totalStockholdersEquity"),
                                                                                              _money_b, "standard", 1.0),
        ("Cash",               L.get("raw_Cash"),              F.get("cashAndCashEquivalents"),
                                                                                              _money_b, "strict",   1.0),
        ("Long-Term Debt",     L.get("raw_LongTermDebt"),      F.get("longTermDebt"),        _money_b, "standard", 1.0),
        ("Liabilities Current", L.get("raw_LiabilitiesCurrent"), F.get("totalCurrentLiabilities"),
                                                                                              _money_b, "standard", 1.0),
    ]


def _cashflow_rows_fy(local: Dict, fmp: Dict, raw_json: Dict) -> List[_RowSpec]:
    L = local or {}
    F = fmp or {}
    R = raw_json or {}
    # CapEx sign convention: FMP follows the cash-flow-statement
    # convention (outflow as negative); our raw_CapEx is the positive
    # `payments to acquire PPE` magnitude. Normalize both to the
    # outflow-as-negative shape so the analyst sees the same row a
    # CFO would see in a 10-K and the drift compares like-to-like.
    our_capex = L.get("derived_CapEx") or L.get("raw_CapEx")
    if our_capex is not None and our_capex > 0:
        our_capex = -our_capex
    fmp_capex = F.get("capitalExpenditure")
    if fmp_capex is not None and fmp_capex > 0:
        fmp_capex = -fmp_capex
    return [
        ("Operating Cash Flow", R.get("OperatingCF"),         F.get("operatingCashFlow"), _money_b, "strict",   1.0),
        ("Free Cash Flow",      L.get("derived_FCF") or L.get("clean_FCF"),
                                                              F.get("freeCashFlow"),      _money_b, "standard", 1.0),
        ("CapEx",               our_capex,                    fmp_capex,                  _money_b, "standard", 1.0),
        ("SBC",                 L.get("clean_SBC"),           F.get("stockBasedCompensation"),
                                                                                          _money_b, "standard", 1.0),
    ]


def _ratios_rows_fy(
    local: Dict, fmp_ratios: Dict, fmp_km: Dict, fmp_income: Dict, fmp_cashflow: Dict,
) -> List[_RowSpec]:
    L = local or {}
    R = fmp_ratios or {}
    K = fmp_km or {}
    I = fmp_income or {}
    C = fmp_cashflow or {}
    # Our margin fields are stored as percent; FMP as decimal.
    # Use scale=0.01 to bring our percent → decimal for comparison.
    #
    # Tier choices:
    #   - Gross / EBIT margin: standard (1%/5%) — these trace to single
    #     well-defined line items; drift > 5% genuinely is a bug.
    #   - EBITDA margin: definitional (5%/25%) — D&A composition varies
    #     between sources (FMP includes ASC-842 right-of-use lease
    #     amortization + acquisition intangible amortization that
    #     aren't always in the standard XBRL Depreciation tags).
    #   - FCF margin: now compared like-for-like; FMP's FCF / FMP's
    #     revenue. Drift mostly comes from CapEx classification.
    #   - ROE / ROIC: definitional — formulas legitimately differ
    #     (ROIC especially: Liberti's Equity+LTD-Cash denominator vs
    #     FMP's TotalDebt+Equity).
    fmp_fcf_margin = (
        C.get("freeCashFlow") / I.get("revenue")
        if (C.get("freeCashFlow") and I.get("revenue")) else None
    )
    return [
        ("Gross Margin",       L.get("derived_GrossMargin_Pct"),  R.get("grossProfitMargin"),
                                                                                          _pct,    "standard",     0.01),
        ("EBIT Margin",        L.get("derived_EBIT_Margin_Pct"),  R.get("operatingProfitMargin"),
                                                                                          _pct,    "standard",     0.01),
        ("EBITDA Margin",      L.get("derived_EBITDA_Margin_Pct"), R.get("ebitdaMargin") or R.get("ebitMargin"),
                                                                                          _pct,    "definitional", 0.01),
        ("FCF Margin",         L.get("derived_FCF_Margin_Pct"),   fmp_fcf_margin,         _pct,    "standard",     0.01),
        ("ROE",                L.get("derived_ROE"),              R.get("returnOnEquity") or K.get("returnOnEquity"),
                                                                                          _pct,    "definitional", 1.0),
        ("ROIC",               L.get("derived_ROIC"),             R.get("returnOnInvestedCapital") or K.get("returnOnInvestedCapital"),
                                                                                          _pct,    "definitional", 1.0),
    ]


def _multiples_rows_fy(
    local: Dict, dcf: Dict,
    fmp_ratios_ttm: Dict, fmp_km_ttm: Dict,
    ttm_local: Optional[Dict] = None,
) -> List[_RowSpec]:
    """Multiples are price-dependent and aren't stored on
    `company_records`. Compute our side from the live DCFEngine
    output (`dcf` is the cached_dcf_summary dict). When the DCF can't
    run for a ticker (specialized-model classification — UNH, V,
    banks), our side stays None and the row renders FMP-only.

    Two snapshot-alignment rules:
      1. FMP's annual `/ratios` and `/enterprise-values` are FY-end-
         priced — we compare against `/ratios-ttm` and `/key-metrics-ttm`
         which use current price + TTM earnings.
      2. Our numerators (revenue, NI, EBITDA) prefer TTM-record values
         when available, falling back to FY only when TTM is absent.
         Mixing current-priced market cap with stale FY earnings
         produces phantom drift (NVDA's FY EBITDA $133B vs TTM $144B
         was making EV/EBITDA show +8% drift even though EV and EBITDA
         themselves matched FMP byte-perfect)."""
    L = local or {}
    T = ttm_local or {}
    Rt = fmp_ratios_ttm or {}
    Kt = fmp_km_ttm or {}
    D = dcf or {}

    is_specialized = isinstance(D, dict) and D.get("error") == "specialized_model_required"

    def _prefer_ttm(ttm_key: str, fy_key: str) -> Optional[float]:
        """TTM value when present + finite; otherwise FY."""
        v = T.get(ttm_key) if T else None
        if v is not None and not _is_missing(v):
            return v
        return L.get(fy_key)

    market_cap = (D.get("market_cap") or 0) if not is_specialized else None
    revenue     = _prefer_ttm("clean_Revenue",   "clean_Revenue")
    net_income  = _prefer_ttm("raw_NetIncome",   "raw_NetIncome")
    ebitda      = _prefer_ttm("derived_EBITDA",  "derived_EBITDA")
    net_debt    = _prefer_ttm("derived_NetDebt", "derived_NetDebt")
    # Book equity is a balance-sheet snapshot — TTM record carries the
    # latest 10-Q balance, FY carries the 10-K balance. TTM is fresher
    # when present.
    book_equity = _prefer_ttm("raw_TotalEquity", "raw_TotalEquity")

    our_pe       = (market_cap / net_income)   if (market_cap and net_income and net_income > 0) else None
    our_pb       = (market_cap / book_equity)  if (market_cap and book_equity and book_equity > 0) else None
    our_ps       = (market_cap / revenue)      if (market_cap and revenue) else None
    our_ev       = (market_cap + (net_debt or 0)) if market_cap else None
    our_ev_ebitda = (our_ev / ebitda) if (our_ev and ebitda) else None

    return [
        ("P/E",              our_pe,        Rt.get("priceToEarningsRatioTTM"),  _ratio,  "standard", 1.0),
        ("EV/EBITDA",        our_ev_ebitda, Kt.get("evToEBITDATTM"),            _ratio,  "standard", 1.0),
        ("P/B",              our_pb,        Rt.get("priceToBookRatioTTM"),      _ratio,  "standard", 1.0),
        ("P/S",              our_ps,        Rt.get("priceToSalesRatioTTM"),     _ratio,  "standard", 1.0),
        ("Enterprise Value", our_ev,        Kt.get("enterpriseValueTTM"),       _money_b, "standard", 1.0),
        ("Net Debt",         L.get("derived_NetDebt"), None,                    _money_b, "standard", 1.0),
    ]


# ── Section builders (TTM) ────────────────────────────────────────────

def _ttm_rows(local: Dict, fmp_km_ttm: Dict, fmp_ratios_ttm: Dict) -> List[_RowSpec]:
    L = local or {}
    K = fmp_km_ttm or {}
    R = fmp_ratios_ttm or {}
    # FMP TTM exposes per-share + EV-multiples; absolute revenue/NI
    # come via EV-implied math (see Gate A.TTM design).
    ev_ttm = K.get("enterpriseValueTTM")
    ev_to_sales = K.get("evToSalesTTM")
    ev_to_ebitda = K.get("evToEBITDATTM")
    ev_to_fcf = K.get("evToFreeCashFlowTTM")
    market_cap = K.get("marketCap")
    pe_ttm = R.get("priceToEarningsRatioTTM")
    fmp_revenue_ttm = (ev_ttm / ev_to_sales) if (ev_ttm and ev_to_sales) else None
    fmp_ebitda_ttm  = (ev_ttm / ev_to_ebitda) if (ev_ttm and ev_to_ebitda) else None
    fmp_fcf_ttm     = (ev_ttm / ev_to_fcf) if (ev_ttm and ev_to_fcf) else None
    # Net income via market_cap / P/E TTM (FMP /key-metrics-ttm doesn't
    # expose absolute NI directly).
    fmp_ni_ttm = (market_cap / pe_ttm) if (market_cap and pe_ttm) else None

    return [
        ("Revenue (TTM)",       L.get("clean_Revenue"),         fmp_revenue_ttm,
                                                                                          _money_b, "standard", 1.0),
        ("Net Income (TTM)",    L.get("raw_NetIncome"),         fmp_ni_ttm,
                                                                                          _money_b, "standard", 1.0),
        ("EBITDA (TTM)",        L.get("derived_EBITDA"),        fmp_ebitda_ttm,
                                                                                          _money_b, "standard", 1.0),
        ("FCF (TTM)",           L.get("derived_FCF"),           fmp_fcf_ttm,
                                                                                          _money_b, "standard", 1.0),
        ("ROE (TTM)",           L.get("derived_ROE"),           K.get("returnOnEquityTTM"),
                                                                                          _pct,     "definitional", 1.0),
        ("ROIC (TTM)",          L.get("derived_ROIC"),          K.get("returnOnInvestedCapitalTTM"),
                                                                                          _pct,     "definitional", 1.0),
        ("EBIT Margin (TTM)",   L.get("derived_EBIT_Margin_Pct"), R.get("operatingProfitMarginTTM"),
                                                                                          _pct,     "standard", 0.01),
    ]


def _market_rows(
    profile: Dict, fmp_ev: Dict, fmp_km_ttm: Dict,
    dcf: Dict, local_fy: Dict,
) -> List[_RowSpec]:
    """Live market snapshot. Our side comes from the DCF engine output
    (current_price, market_cap, beta, shares_diluted), with EV computed
    as market_cap + derived_NetDebt.

    FMP side: prefers TTM-anchored values (current price + current
    market cap + current EV) over the FY-end-priced annual snapshot.
    Shares outstanding falls back to `/enterprise-values.numberOfShares`
    when /profile doesn't expose it."""
    P = profile or {}
    E = fmp_ev or {}
    K = fmp_km_ttm or {}
    D = dcf or {}
    L = local_fy or {}

    is_specialized = isinstance(D, dict) and D.get("error") == "specialized_model_required"

    our_price        = (D.get("current_price") if not is_specialized else None)
    our_market_cap   = (D.get("market_cap")    if not is_specialized else None)
    our_shares       = (D.get("shares_diluted") if not is_specialized else None)
    our_beta         = (D.get("beta")          if not is_specialized else None)
    our_net_debt     = L.get("derived_NetDebt")
    our_ev = (
        (our_market_cap + (our_net_debt or 0.0))
        if our_market_cap is not None else None
    )

    # FMP shares fallback chain — /profile sometimes returns None,
    # /enterprise-values exposes numberOfShares as the period-end count.
    fmp_shares = P.get("sharesOutstanding") or E.get("numberOfShares")

    return [
        ("Current Price",      our_price,      P.get("price"),                  _money_per_share, "strict",       1.0),
        ("Market Cap",         our_market_cap, P.get("mktCap") or P.get("marketCap") or K.get("marketCap"),
                                                                                _money_b,         "standard",     1.0),
        ("Enterprise Value",   our_ev,         K.get("enterpriseValueTTM"),     _money_b,         "standard",     1.0),
        ("Beta",               our_beta,       P.get("beta"),                   _scalar,          "definitional", 1.0),
        ("Shares Outstanding", our_shares,     fmp_shares,                      _shares,          "strict",       1.0),
        ("FMP DCF",            None,           P.get("dcf"),                    _money_per_share, "definitional", 1.0),
    ]


# ── Top-level renderer ────────────────────────────────────────────────

def render_fmp_compare_view(ticker: str) -> None:
    """Side-by-side comparison of every line item we ingest vs FMP."""
    if not ticker:
        st.info("Select a ticker from the sidebar to compare.")
        return

    fy_local, ttm_local = _load_local(ticker)
    if fy_local is None and ttm_local is None:
        st.error(f"No cleaned data for {ticker} in the database.")
        return

    target_fy = int(fy_local["fiscal_year"]) if fy_local else None
    fmp_blobs = _load_fmp(ticker, target_fy)

    # DCF summary for our-side multiples (P/E, EV/EBITDA, etc.). The
    # cached version is shared with the rest of the dashboard; for
    # specialized-model tickers (UNH, CNC, banks, V) the call returns
    # an `error` sentinel and our multiples render as None per the
    # locked DCF-NA policy.
    try:
        from aletheia.ui.cache import cached_dcf_summary
        dcf_summary = cached_dcf_summary(ticker)
    except Exception:
        dcf_summary = {}

    st.markdown(f"## FMP Compare — {ticker.upper()}")
    if target_fy:
        period_end = (
            str(fy_local.get("period_end_date") or "")[:10] if fy_local else "—"
        )
        st.caption(
            f"Comparison anchored on FY{target_fy} (ended {period_end}). "
            f"Local data from `company_records_latest`, FMP from cached "
            f"endpoints (run `python scripts/refresh_sec_cache.py --all && "
            f"python scripts/ingest_ttm.py --all` to refresh)."
        )

    raw_json = {}
    if fy_local and fy_local.get("raw_json"):
        try:
            raw_json = json.loads(fy_local["raw_json"])
        except Exception:
            raw_json = {}

    # ── Latest FY ────────────────────────────────────────────────────
    if fy_local:
        st.markdown(f"### Latest FY — FY{target_fy}")
        st.markdown("##### Income statement")
        _render_table(_income_rows_fy(fy_local, fmp_blobs["fy"]["income"]))
        st.markdown("##### Balance sheet")
        _render_table(_balance_rows_fy(fy_local, fmp_blobs["fy"]["balance"]))
        st.markdown("##### Cash flow")
        _render_table(_cashflow_rows_fy(fy_local, fmp_blobs["fy"]["cashflow"], raw_json))
        st.markdown("##### Margins + returns")
        _render_table(_ratios_rows_fy(
            fy_local, fmp_blobs["fy"]["ratios"], fmp_blobs["fy"]["key_metrics"],
            fmp_blobs["fy"]["income"], fmp_blobs["fy"]["cashflow"],
        ))
        st.caption(
            "_Methodology drift expected on EBITDA Margin and ROIC._  "
            "FMP's EBITDA includes ASC-842 right-of-use lease amortization "
            "and acquisition-related intangible amortization that aren't "
            "always captured under the standard XBRL `DepreciationAndAmortization` "
            "tag (asset-heavy services firms show this most). FMP's ROIC "
            "uses `(NetIncome + InterestExpense×(1-tax)) / (TotalDebt + Equity)` "
            "vs our Liberti-style `NOPAT / (Equity + LongTermDebt − Cash)` — "
            "both correct, different denominators."
        )
        st.markdown("##### Multiples + capital structure")
        _render_table(_multiples_rows_fy(
            fy_local, dcf_summary,
            fmp_blobs["ttm"]["ratios"], fmp_blobs["ttm"]["key_metrics"],
            ttm_local=ttm_local,
        ))
        st.caption(
            "_FMP multiples sourced from `/ratios-ttm` and `/key-metrics-ttm` "
            "(current-price + TTM-earnings basis), like-for-like with our "
            "current-price values. FMP's annual `/ratios` would be FY-end-priced, "
            "which produces ~30% drift on every multiple when the stock has moved._"
        )
        if isinstance(dcf_summary, dict) and dcf_summary.get("error") == "specialized_model_required":
            # The cache stamps `message` with the NotImplementedError
            # text, which already explains the cause (specialized
            # business model OR a known-issues bypass like V's missing
            # diluted-share XBRL). Surface that verbatim — more
            # accurate than relabeling.
            msg = dcf_summary.get("message") or "DCF unavailable"
            st.caption(
                f"_Multiples not computed locally for {ticker.upper()}: "
                f"{msg}. FMP values shown for reference only._"
            )

    # ── TTM ──────────────────────────────────────────────────────────
    if ttm_local:
        ttm_period_end = str(ttm_local.get("period_end_date") or "")[:10]
        ttm_source = "—"
        try:
            ttm_blob = json.loads(ttm_local.get("fmp_validation_json") or "{}")
            ttm_source = ttm_blob.get("ttm_source") or "—"
        except Exception:
            pass
        st.markdown(
            f"### TTM — ended {ttm_period_end}  ·  source: `{ttm_source}`"
        )
        ttm_rows = _ttm_rows(
            ttm_local, fmp_blobs["ttm"]["key_metrics"], fmp_blobs["ttm"]["ratios"],
        )
        _render_table(ttm_rows)
        # Surface filer-specific XBRL gaps. Some filers (LLY, others
        # with non-standard pharma/insurance income statements) don't
        # publish OperatingIncomeLoss or PaymentsToAcquireProperty
        # under the standard us-gaap tags, so the SEC TTM derivation
        # leaves the dependent fields empty. The latest 10-K's value
        # is shown in the FY section above for reference.
        gap_labels = [
            label for label, ours, fmp, *_ in ttm_rows
            if (ours is None or _is_missing(ours)) and not _is_missing(fmp)
        ]
        if gap_labels and ttm_source == "sec_derived_quarters":
            st.caption(
                f"_TTM gaps for {ticker.upper()} ({', '.join(gap_labels)}): "
                f"this filer doesn't publish the standard XBRL tags our "
                f"SEC-quarterly derivation needs. Check the FY section "
                f"above for the latest annual values._"
            )
    else:
        st.info(
            "No TTM record on file. Run `python scripts/ingest_ttm.py "
            f"--ticker {ticker.upper()}` to ingest the trailing twelve "
            "months for this ticker."
        )

    # ── Market snapshot ──────────────────────────────────────────────
    st.markdown("### Market snapshot (live FMP /profile)")
    _render_table(_market_rows(
        fmp_blobs.get("profile"), fmp_blobs["fy"]["ev"],
        fmp_blobs["ttm"]["key_metrics"], dcf_summary, fy_local,
    ))

    # ── Legend ───────────────────────────────────────────────────────
    st.caption(
        "✓ byte-perfect (within strict/standard tier)  ·  "
        "≈ acceptable (within 5% / 25% per tier)  ·  "
        "✗ structural drift (outside band)  ·  "
        "— field not exposed on one side"
    )
