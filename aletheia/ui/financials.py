"""
aletheia/ui/financials.py

Single-ticker financial detail bundle for the Streamlit Financials tab.
Pulls raw / clean / derived data from DuckDB and runs DCFEngine + supporting
calc tools live. Cacheable via Streamlit's @st.cache_data wrapper applied at
the call site (kept out of this module so it can be unit-tested without
streamlit installed).

Returns a single nested dict with all 12 sections the AAPL detail dump shows:
  identity, income_statement, balance_sheet, returns_capital, lease_items,
  fiscal_history, dcf_inputs, cagr_ladder, dcf_scenarios, projections,
  terminal_value, assumptions, plus a `bypass` field if DCF didn't run.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from aletheia.utils.calc_input_builder import make_calc_input


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_FLAG_SEV_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


def _annotate_flag_acks(ticker: str, cs: Dict[str, Any]) -> Dict[str, Any]:
    """Merge persisted current-state flag acks into a cs payload (DB read).

    Mirrors the API's `_annotate_acks` so the Financials panel shows the same
    resolution state as the Deep Dive gate. `needs_analysis` does NOT resolve."""
    try:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        try:
            acks = db.get_flag_acks(ticker)
        finally:
            db.close()
    except Exception:
        acks = {}
    unresolved_rank, unresolved_high = 0, 0
    for f in cs.get("flags", []):
        ack = acks.get(f.get("key", ""))
        resolved = bool(ack) and ack["decision"] != "needs_analysis"
        f["acknowledged"] = resolved
        f["ack"] = {
            "decision": ack["decision"], "rationale": ack.get("rationale"),
            "decided_by": ack.get("decided_by"), "decided_at": ack.get("decided_at"),
        } if ack else None
        if not resolved:
            unresolved_rank = max(unresolved_rank, _FLAG_SEV_RANK.get(f.get("severity"), 0))
            if f.get("severity") == "HIGH":
                unresolved_high += 1
    cs["unresolved_severity"] = next(
        (s for s, r in _FLAG_SEV_RANK.items() if r == unresolved_rank), "NONE")
    cs["unresolved_high"] = unresolved_high
    return cs


def _f(v: Any) -> Optional[float]:
    """Coerce to float, returning None for null/NaN."""
    if v is None:
        return None
    try:
        f = float(v)
        if np.isnan(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _parse_json_field(s: Any) -> Dict[str, Any]:
    if not s:
        return {}
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return {}


def _safe_get(row: pd.Series, *names: str) -> Optional[float]:
    """Take the first non-null value across a series of candidate column names."""
    for n in names:
        if n in row:
            v = _f(row.get(n))
            if v is not None:
                return v
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Section builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_identity(ticker: str, latest: pd.Series) -> Dict[str, Any]:
    warns = _parse_json_field(latest.get("warnings_json"))
    errs = _parse_json_field(latest.get("errors_json"))
    return {
        "ticker": ticker,
        "fiscal_year": int(latest["fiscal_year"]),
        "period_end_date": str(latest.get("period_end_date") or ""),
        "quality_score": _f(latest.get("overall_quality_score")),
        "warnings": warns,
        "errors": errs,
        "warning_count": int(latest.get("warning_count") or 0),
        "error_count": int(latest.get("error_count") or 0),
    }


def _build_income_statement(latest: pd.Series, clean_json: dict, raw_json: dict) -> Dict[str, Optional[float]]:
    return {
        "Revenue":          _safe_get(latest, "clean_Revenue", "raw_Revenue") or _f(clean_json.get("Revenue") or raw_json.get("Revenue")),
        "COGS":             _safe_get(latest, "raw_COGS") or _f(clean_json.get("COGS") or raw_json.get("COGS")),
        "GrossProfit":      None,  # computed below
        "GrossMargin_Pct":  _f(latest.get("derived_GrossMargin_Pct")),
        "RnD":              _safe_get(latest, "raw_RnD") or _f(clean_json.get("R&D") or raw_json.get("R&D")),
        "SGnA":             _f(clean_json.get("SG&A") or raw_json.get("SG&A")),
        "OperatingIncome":  _safe_get(latest, "raw_OperatingIncome", "derived_OperatingIncome") or _f(clean_json.get("OperatingIncome")),
        # Capital-IQ-aligned operating income: OpInc + impairment/restructuring
        # add-back. Source provenance is rendered as a pill in the UI.
        # Hybrid path uses discrete XBRL tags; FMP-only fallback uses
        # ``otherExpenses`` bucket gated by a 5%-of-revenue materiality test.
        "OperatingIncome_ExUnusual":        _f(latest.get("clean_OperatingIncome_ExUnusual")),
        "OperatingIncome_ExUnusual_Source": (
            latest.get("clean_OperatingIncome_ExUnusual_Source")
            if isinstance(latest.get("clean_OperatingIncome_ExUnusual_Source"), str)
            else None
        ),
        "EBIT_Margin_Pct":  _f(latest.get("derived_EBIT_Margin_Pct")),
        "EBITDA":           _safe_get(latest, "clean_EBITDA", "derived_EBITDA") or _f(clean_json.get("EBITDA")),
        "EBITDA_ExUnusual": _f(latest.get("clean_EBITDA_ExUnusual")),
        "EBITDA_Margin_Pct": _f(latest.get("derived_EBITDA_Margin_Pct")),
        "DepreciationAmortization": _safe_get(latest, "derived_Depreciation_Total") or _f(clean_json.get("Depreciation_Total")),
        "NOPAT":            _safe_get(latest, "clean_NOPAT") or _f(clean_json.get("NOPAT")),
        "NetIncome":        _safe_get(latest, "raw_NetIncome") or _f(clean_json.get("NetIncome")),
        "DilutedEPS":       _f(clean_json.get("DilutedEPS") or raw_json.get("DilutedEPS")),
        "OperatingCF":      _f(clean_json.get("OperatingCF") or raw_json.get("OperatingCF")),
        "InvestingCF":      _f(clean_json.get("InvestingCF") or raw_json.get("InvestingCF")),
        "FinancingCF":      _f(clean_json.get("FinancingCF") or raw_json.get("FinancingCF")),
        "FCF":              _safe_get(latest, "clean_FCF", "derived_FCF") or _f(clean_json.get("FCF")),
        "FCFF":             _safe_get(latest, "clean_FCFF", "derived_FCFF") or _f(clean_json.get("FCFF")),
        "FCF_Margin_Pct":   _f(latest.get("derived_FCF_Margin_Pct")),
        "CapEx":            _safe_get(latest, "raw_CapEx", "derived_CapEx") or _f(clean_json.get("CapEx")),
        "MaintenanceCapEx": _f(clean_json.get("MaintenanceCapEx")),
        "GrowthCapEx":      _f(clean_json.get("GrowthCapEx")),
    }


def _build_balance_sheet(latest: pd.Series, clean_json: dict, raw_json: dict) -> Dict[str, Optional[float]]:
    return {
        "TotalAssets":         _safe_get(latest, "raw_TotalAssets") or _f(raw_json.get("TotalAssets")),
        "Cash":                _safe_get(latest, "raw_Cash") or _f(raw_json.get("Cash")),
        "ShortTermInvestments": _f(clean_json.get("ShortTermInvestments") or raw_json.get("ShortTermInvestments")),
        "AccountsReceivable":  _f(clean_json.get("AccountsReceivable") or raw_json.get("AccountsReceivable")),
        "Inventory":           _f(clean_json.get("Inventory") or raw_json.get("Inventory")),
        "PPE_Net":             _f(clean_json.get("PPE") or raw_json.get("PPE")),
        "PPE_Gross":           _f(clean_json.get("PPE_Gross") or raw_json.get("PPE_Gross")),
        "AccumulatedDepreciation": _f(clean_json.get("AccumulatedDepreciation") or raw_json.get("AccumulatedDepreciation")),
        "TotalLiabilities":    _safe_get(latest, "raw_TotalLiabilities") or _f(raw_json.get("TotalLiabilities")),
        "LiabilitiesCurrent":  _safe_get(latest, "raw_LiabilitiesCurrent") or _f(raw_json.get("LiabilitiesCurrent")),
        "ShortTermDebt":       _f(clean_json.get("ShortTermDebt") or raw_json.get("ShortTermDebt")),
        "LongTermDebt":        _safe_get(latest, "raw_LongTermDebt") or _f(raw_json.get("LongTermDebt")),
        "AccountsPayable":     _f(clean_json.get("AccountsPayable") or raw_json.get("AccountsPayable")),
        "TotalEquity":         _safe_get(latest, "raw_TotalEquity") or _f(raw_json.get("TotalEquity")),
        "NWC":                 _f(clean_json.get("NWC")),
        "NetDebt":             _safe_get(latest, "derived_NetDebt"),
    }


def _build_returns_capital(latest: pd.Series, clean_json: dict, raw_json: dict) -> Dict[str, Optional[float]]:
    return {
        "ROIC":            _f(latest.get("derived_ROIC")),
        "ROE":             _f(latest.get("derived_ROE")),
        "InvestedCapital": _f(latest.get("derived_InvestedCapital")),
        "SharesDiluted":   _safe_get(latest, "clean_SharesDiluted", "raw_SharesDiluted") or _f(clean_json.get("SharesDiluted")),
        "SharesBasic":     _f(clean_json.get("SharesBasic") or raw_json.get("SharesBasic")),
        "SharesOutstanding": _safe_get(latest, "raw_SharesOutstanding") or _f(clean_json.get("SharesOutstanding") or raw_json.get("SharesOutstanding")),
        "Buybacks":        _f(clean_json.get("Buybacks") or raw_json.get("Buybacks")),
        "SBC":             _safe_get(latest, "clean_SBC") or _f(clean_json.get("SBC")),
        "NetBuyback_AfterSBC": _safe_get(latest, "clean_NetBuyback_AfterSBC") or _f(clean_json.get("NetBuyback_AfterSBC")),
        "SBC_PctFCF":      _safe_get(latest, "clean_SBC_PctFCF") or _f(clean_json.get("SBC_PctFCF")),
        "DilutionPct":     _safe_get(latest, "clean_ShareDilution_Pct") or _f(clean_json.get("ShareDilution_Pct")),
        "DividendsPaid":   _f(clean_json.get("DividendsPaid") or raw_json.get("DividendsPaid")),
    }


def _build_lease_items(clean_json: dict, raw_json: dict) -> Dict[str, Optional[float]]:
    op_curr  = _f(clean_json.get("LeaseLiabilityCurrent_Operating") or raw_json.get("LeaseLiabilityCurrent_Operating"))
    op_nc    = _f(clean_json.get("LeaseLiabilityNoncurrent_Operating") or raw_json.get("LeaseLiabilityNoncurrent_Operating"))
    fin_curr = _f(clean_json.get("LeaseLiabilityCurrent_Finance") or raw_json.get("LeaseLiabilityCurrent_Finance"))
    fin_nc   = _f(clean_json.get("LeaseLiabilityNoncurrent_Finance") or raw_json.get("LeaseLiabilityNoncurrent_Finance"))
    op_total = (op_curr or 0) + (op_nc or 0) if (op_curr is not None or op_nc is not None) else None
    fin_total = (fin_curr or 0) + (fin_nc or 0) if (fin_curr is not None or fin_nc is not None) else None
    return {
        "ROUAsset_Operating":  _f(clean_json.get("ROU_Asset_Operating") or raw_json.get("ROU_Asset_Operating")),
        "ROUAsset_Finance":    _f(clean_json.get("ROU_Asset_Finance") or raw_json.get("ROU_Asset_Finance")),
        "LeaseLiability_Operating_Total": op_total,
        "LeaseLiability_Finance_Total":   fin_total,
        "LeaseCost":           _f(clean_json.get("LeaseCost") or raw_json.get("LeaseCost")),
    }


def _build_freshness(latest_fy: pd.Series,
                     ttm: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Phase Q-7: surface filing freshness on the Financials hero strip.
    Picks the freshest period_end_date (TTM if ingested, else FY) and
    estimates the next expected 10-Q filing date using the SEC's
    accelerated-filer floor (~45 days post quarter-end). The estimate
    is intentionally conservative — actual filers usually beat it by
    1-3 weeks; the goal is "data isn't broken, next refresh is N days
    out", not a precise forecast."""
    import datetime

    today = datetime.date.today()

    if ttm and ttm.get("period_end_date"):
        latest_period_end = ttm["period_end_date"]
        latest_period = "TTM"
    else:
        latest_period_end = str(latest_fy.get("period_end_date") or "")[:10] or None
        latest_period = "FY"

    days_since_filing: Optional[int] = None
    next_expected_date: Optional[str] = None
    days_until_next: Optional[int] = None
    if latest_period_end:
        try:
            pe = datetime.date.fromisoformat(latest_period_end)
            days_since_filing = (today - pe).days
            # Next 10-Q quarter-end is 90 days after current period_end;
            # filing usually lands within ~45 days of THAT quarter-end.
            next_qtr_end = pe + datetime.timedelta(days=90)
            next_filing  = next_qtr_end + datetime.timedelta(days=45)
            next_expected_date = next_filing.isoformat()
            days_until_next = (next_filing - today).days
        except (TypeError, ValueError):
            pass

    return {
        "latest_period":           latest_period,           # 'TTM' or 'FY'
        "latest_period_end_date":  latest_period_end,
        "days_since_filing":       days_since_filing,
        "next_expected_date":      next_expected_date,
        "days_until_next_filing":  days_until_next,
    }


def _attach_prior_year_ttm(ticker: str, ttm_snapshot: Dict[str, Any],
                           fx_rate: float = 1.0) -> None:
    """Compute prior-year TTM revenue + net income from FMP quarterly
    statements and stamp on the snapshot. Used by the Multi-year
    history table to render YoY-TTM-vs-prior-TTM rev growth instead
    of the seasonality-trap TTM-vs-FY comparison.

    Math: latest 4 FMP quarters = current TTM (already in `ttm_snapshot`).
    Quarters [4:8] = TTM as of one year ago. Returns silently if FMP
    data is unavailable or insufficient (<8 quarters).

    Currency: FMP quarterly statements are in the filer's NATIVE currency
    (NVO → DKK), but `ttm_snapshot["Revenue"]` was already FX-converted to
    USD via the history frame. Comparing the two directly mixed currencies
    and produced a spurious ~-83% TTM growth for NVO. `fx_rate`
    (USD-per-foreign-unit; 1.0 for USD filers / no conversion) scales the
    prior-year figures into the SAME currency as the snapshot so the growth
    ratio is currency-consistent."""
    try:
        from aletheia.data import fmp_client
        quarters = fmp_client.fetch_income_statements(ticker, period="quarter")
    except Exception:
        return
    if not quarters or len(quarters) < 8:
        return
    # FMP returns most-recent first. Take quarters 4..7 for prior-year
    # TTM (skip the latest 4 which are the current TTM window).
    prior_window = quarters[4:8]
    try:
        prior_revenue = sum(float(q.get("revenue") or 0) for q in prior_window)
        prior_ni      = sum(float(q.get("netIncome") or 0) for q in prior_window)
    except (TypeError, ValueError):
        return
    if prior_revenue <= 0:
        return
    # Match the snapshot's currency (snapshot Revenue is USD-converted when
    # the filer is non-USD; fx_rate == 1.0 otherwise → no-op).
    ttm_snapshot["PriorYearRevenue"]   = prior_revenue * fx_rate
    ttm_snapshot["PriorYearNetIncome"] = prior_ni * fx_rate
    # Period_end of prior-year TTM (the 8th-most-recent quarter's date)
    prior_period_end = (prior_window[0].get("date") or "")[:10]
    if prior_period_end:
        ttm_snapshot["PriorYearPeriodEnd"] = prior_period_end


def _build_ttm_snapshot(ttm_df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Phase Q-7: extract the latest TTM record (if any) for the
    Financials hero / Multi-year-history TTM row. Returns None when no
    TTM has been ingested yet (legacy DBs / not-yet-run tickers)."""
    if ttm_df is None or ttm_df.empty:
        return None
    # Prefer the row with the most recent period_end_date; fall back to
    # max fiscal_year if dates missing.
    sortable = ttm_df.copy()
    if "period_end_date" in sortable.columns:
        sortable = sortable.sort_values(
            ["period_end_date", "fiscal_year"], na_position="first"
        )
    else:
        sortable = sortable.sort_values("fiscal_year")
    r = sortable.iloc[-1]

    return {
        "fiscal_year":         int(r["fiscal_year"]),
        "period_end_date":     str(r.get("period_end_date") or "")[:10] or None,
        "ttm_source":          _ttm_source_from_validation(r.get("fmp_validation_json")),
        "Revenue":             _f(r.get("clean_Revenue")),
        "EBIT":                _f(r.get("raw_OperatingIncome")) or _f(r.get("derived_OperatingIncome")),
        "NormEBIT":            _dcf_base_ebit(r),
        "EBITDA":              _f(r.get("derived_EBITDA")),
        "NetIncome":           _f(r.get("raw_NetIncome")),
        "TaxRate":             _f(r.get("clean_GAAP_TaxRate")),
        "CapEx":               _f(r.get("derived_CapEx")) or _f(r.get("raw_CapEx")),
        "FCF":                 _f(r.get("derived_FCF")) or _f(r.get("clean_FCF")),
        "ROIC":                _f(r.get("derived_ROIC")),
        "ROE":                 _f(r.get("derived_ROE")),
        "EBIT_Margin_Pct":     _f(r.get("derived_EBIT_Margin_Pct")),
        "FCF_Margin_Pct":      _f(r.get("derived_FCF_Margin_Pct")),
        "FMPStatus":           (r.get("fmp_validation_status") or "not_run"),
        "FMPDrifts":           _drift_summary_from_validation(r.get("fmp_validation_json")),
    }


def _ttm_source_from_validation(validation_json: Any) -> Optional[str]:
    """Pull `ttm_source` out of the persisted Gate A.TTM receipt so the
    UI can label which TTM derivation produced this row."""
    if not validation_json:
        return None
    try:
        v = json.loads(validation_json) if isinstance(validation_json, str) else validation_json
        return v.get("ttm_source") if isinstance(v, dict) else None
    except Exception:
        return None


def _drift_summary_from_validation(validation_json: Any) -> List[Dict[str, Any]]:
    """Extract the list of fields with structural_drift status from a
    persisted Gate A receipt. Used by the UI to surface non-blocking
    warnings on Multi-year history rows so analysts see WHICH metric
    drifted rather than just a generic ⚠ glyph.

    Each entry: {field, drift_pct, ours, fmp, p0, tier}. Sorted by
    abs(drift_pct) descending so the worst offender shows first."""
    if not validation_json:
        return []
    try:
        blob = (
            json.loads(validation_json)
            if isinstance(validation_json, str) else validation_json
        )
    except Exception:
        return []
    if not isinstance(blob, dict):
        return []
    fields = blob.get("fields") or {}
    drifts: List[Dict[str, Any]] = []
    for name, f in fields.items():
        if not isinstance(f, dict):
            continue
        if f.get("status") != "structural_drift":
            continue
        drift = f.get("drift_pct")
        try:
            drift_abs = abs(float(drift)) if drift is not None else 0.0
        except (TypeError, ValueError):
            drift_abs = 0.0
        drifts.append({
            "field":      name,
            "drift_pct":  drift,
            "drift_abs":  drift_abs,
            "ours":       f.get("ours"),
            "fmp":        f.get("fmp"),
            "p0":         bool(f.get("p0")),
            "tier":       f.get("tier"),
        })
    drifts.sort(key=lambda d: d["drift_abs"], reverse=True)
    return drifts


def _dcf_base_ebit(r) -> Optional[float]:
    """Base-year EBIT exactly as the DCF engine selects it (dcf_engine.py
    ~L1119): ex-unusual operating income (one-time impairments/restructuring
    stripped) when present and positive, else reported `raw_OperatingIncome`,
    else `derived_OperatingIncome`. This is the "Normalized EBIT (what DCF
    uses)" series shown in the history table — it can differ from reported
    EBIT in years with large one-time charges (e.g. LDOS FY2023)."""
    exu = _f(r.get("clean_OperatingIncome_ExUnusual"))
    if exu is not None and exu > 0:
        return exu
    return _f(r.get("raw_OperatingIncome")) or _f(r.get("derived_OperatingIncome"))


def _build_fiscal_history(history_df: pd.DataFrame) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for _, r in history_df.sort_values("fiscal_year").iterrows():
        rows.append({
            "fiscal_year":     int(r["fiscal_year"]),
            "period_end_date": str(r.get("period_end_date") or "")[:10],
            "Revenue":         _f(r.get("clean_Revenue")),
            "EBIT":            _f(r.get("raw_OperatingIncome")) or _f(r.get("derived_OperatingIncome")),
            "NormEBIT":        _dcf_base_ebit(r),
            "EBIT_Margin_Pct": _f(r.get("derived_EBIT_Margin_Pct")),
            "EBITDA":          _f(r.get("derived_EBITDA")),
            "NetIncome":       _f(r.get("raw_NetIncome")),
            "TaxRate":         _f(r.get("clean_GAAP_TaxRate")),
            "CapEx":           _f(r.get("derived_CapEx")) or _f(r.get("raw_CapEx")),
            "FCF":             _f(r.get("derived_FCF")) or _f(r.get("clean_FCF")),
            "ROIC":            _f(r.get("derived_ROIC")),
            "QualityScore":    _f(r.get("overall_quality_score")),
            "FMPStatus":       (r.get("fmp_validation_status") or "not_run"),
            "FMPDrifts":       _drift_summary_from_validation(r.get("fmp_validation_json")),
        })
    return rows


def _build_dcf_sections(dcf_dict: Dict[str, Any], dcf_obj) -> Dict[str, Any]:
    """Pulls DCF inputs, scenarios, projections, terminal value, assumptions."""
    if not dcf_dict or not dcf_obj:
        return {}

    base = dcf_obj.base
    bull = dcf_obj.bull
    bear = dcf_obj.bear

    # CAGR ladder — re-extract from the engine output if available
    cagr_ladder = []
    # The engine emits these in the verbose log; for now we surface the
    # selected y1-5 CAGR plus historical CAGR from base assumptions.
    if base and base.assumptions:
        cagr_ladder.append({
            "label": "Selected (engine choice)",
            "value_pct": base.assumptions.revenue_cagr_y1_5 * 100,
            "decay_y6_10_pct": base.assumptions.revenue_cagr_y6_10 * 100,
        })

    inputs = {
        "current_price": _f(dcf_dict.get("current_price")),
        "market_cap":    _f(dcf_dict.get("market_cap")),
        "shares_diluted": _f(dcf_dict.get("shares_diluted")),
        "risk_free_rate": _f(dcf_dict.get("risk_free_rate")),
        "beta":          _f(dcf_dict.get("beta")),
        "wacc_base":     _f(dcf_dict.get("wacc_base")),
        "tax_rate":      _f(dcf_dict.get("base_tax_rate")) or _f(base.assumptions.tax_rate if base else None),
    }

    def _scenario_summary(name: str, sc) -> Dict[str, Any]:
        if sc is None:
            return {}
        ev = sc.enterprise_value
        ips = dcf_obj.intrinsic_per_share(ev, dcf_obj.net_debt) if ev else None
        upside = (ips / dcf_obj.current_price - 1) if (ips and dcf_obj.current_price) else None
        return {
            "name": name,
            "EV": _f(ev),
            "IPS": _f(ips),
            "Upside_Pct": (upside * 100) if upside is not None else None,
            "WACC": _f(sc.assumptions.wacc),
            "TerminalGrowth": _f(sc.assumptions.terminal_growth),
            "TV_Pct_EV": _f(sc.terminal.tv_pct_of_ev),
            "ImpliedEV_EBITDA": _f(sc.implied_ev_ebitda),
            "JustifiedEV_EBITDA": _f(sc.justified_ev_ebitda),
        }

    scenarios = {
        "bull": _scenario_summary("Bull", bull),
        "base": _scenario_summary("Base", base),
        "bear": _scenario_summary("Bear", bear),
    }

    # Year-by-year projections from base case
    projections = []
    if base and base.projections:
        for p in base.projections:
            projections.append({
                "year": p.year,
                "fiscal_year": p.fiscal_year,
                "revenue": _f(p.revenue),
                "ebit": _f(p.ebit),
                "nopat": _f(p.nopat),
                "da": _f(p.da),
                "capex": _f(p.capex),
                "fcff": _f(p.fcff),
                "pv_fcff": _f(p.pv_fcff),
            })

    terminal = {}
    if base and base.terminal:
        t = base.terminal
        terminal = {
            "gordon_tv":               _f(t.gordon_tv),
            "reinvestment_tv":         _f(t.reinvestment_tv),
            "tv_used":                 _f(t.tv_used),
            "pv_tv":                   _f(t.pv_tv),
            "tv_pct_of_ev":            _f(t.tv_pct_of_ev),
            "implied_tv_ebitda_multiple": _f(t.implied_tv_ebitda_multiple),
        }

    assumptions = {}
    if base and base.assumptions:
        a = base.assumptions
        assumptions = {
            "revenue_cagr_y1_5":  _f(a.revenue_cagr_y1_5),
            "revenue_cagr_y6_10": _f(a.revenue_cagr_y6_10),
            "ebit_margin_current": _f(a.ebit_margin_current),
            "ebit_margin_terminal": _f(a.ebit_margin_terminal),
            "capex_pct_revenue":  _f(a.capex_pct_revenue),
            "da_pct_revenue":     _f(a.da_pct_revenue),
            "nwc_pct_revenue":    _f(a.nwc_pct_revenue),
            "tax_rate":           _f(a.tax_rate),
            "wacc":               _f(a.wacc),
            "terminal_growth":    _f(a.terminal_growth),
            "terminal_roic":      _f(a.terminal_roic),
            "base_roic":          _f(a.base_roic),
            "justification":      a.justification,
        }

    return {
        "inputs":      inputs,
        "scenarios":   scenarios,
        "projections": projections,
        "terminal":    terminal,
        "assumptions": assumptions,
        "cagr_ladder": cagr_ladder,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def ticker_detail(ticker: str) -> Dict[str, Any]:
    """
    Build the full Financials-tab data bundle for a ticker. Reads from DuckDB
    and runs the DCF live. Returns a dict whose Streamlit consumer can render
    each section without further computation.
    """
    from aletheia.data.database import InvestmentDatabase
    from aletheia.tools.dcf_engine import DCFEngine

    db = InvestmentDatabase(verbose=False)
    try:
        history = db.get_latest(ticker)
    finally:
        db.close()

    if history is None or history.empty:
        return {"error": f"No data for {ticker} in DB."}

    # FX: convert non-USD filers (NVO/DKK, ASML/EUR, TSM/TWD) to USD so the
    # multi-year history + hero match the USD-priced DCF. Idempotent — the same
    # frame is passed to make_calc_input below, which sees the df.attrs tag and
    # won't re-convert.
    from aletheia.data.fx import convert_financials_to_usd
    history, _fx_currency, _fx_rate = convert_financials_to_usd(history, ticker)

    # Phase Q-7 minimal: split FY rows from TTM. The income/balance/
    # returns blocks continue to be FY-based (Phase Q-5 wires TTM into
    # the calc engine; this MVP slice surfaces TTM as a separate row in
    # the Multi-year history without changing other tabs).
    if "period" in history.columns:
        fy_history  = history[history["period"] == "FY"].copy()
        ttm_history = history[history["period"] == "TTM"].copy()
    else:
        # Pre-Q-1 schema (test fixtures) — every row is FY by definition.
        fy_history  = history
        ttm_history = history.iloc[0:0]

    if fy_history.empty:
        return {"error": f"No FY data for {ticker} in DB."}

    fy_sorted = fy_history.sort_values("fiscal_year")
    latest = fy_sorted.iloc[-1]
    clean_json = _parse_json_field(latest.get("clean_json"))
    raw_json   = _parse_json_field(latest.get("raw_json"))

    # Prior-FY row (one step back). Used to populate the "Prior FY" column
    # across every section table — without this, only the five fields
    # tracked in `_build_fiscal_history` had prior data and the remaining
    # ~50 rows in the Financials tab rendered "—" for Prior FY / YoY.
    if len(fy_sorted) >= 2:
        prior_row = fy_sorted.iloc[-2]
        prior_clean_json = _parse_json_field(prior_row.get("clean_json"))
        prior_raw_json   = _parse_json_field(prior_row.get("raw_json"))
    else:
        prior_row = None
        prior_clean_json = {}
        prior_raw_json = {}

    ttm_snapshot = _build_ttm_snapshot(ttm_history)
    # Phase Q-7: enrich TTM snapshot with a prior-year TTM revenue +
    # net income for proper YoY-TTM-vs-prior-TTM display. Without this,
    # the Multi-year history TTM row's "Rev Growth" cell compares
    # against the latest full FY, which crosses different period
    # endpoints — the seasonality trap the user flagged in plan
    # review. Using FMP quarterly statements (already cached) so
    # there's no extra API call.
    if ttm_snapshot is not None:
        # Pass the FX rate so the prior-year TTM (pulled from FMP in native
        # currency) is converted to match the snapshot's USD-converted
        # Revenue — otherwise non-USD filers (NVO/DKK) get a currency-mixed,
        # wildly wrong TTM growth (~-83%).
        _attach_prior_year_ttm(ticker, ttm_snapshot, fx_rate=_fx_rate)

    # Phase 6 Step 2b: schema-contract violations from the persistence
    # boundary. Reads schema_violations_json from the latest FY row +
    # the TTM row (if present). The UI surfaces these in a Data Quality
    # panel so analysts can see what the framework flagged on this
    # ticker's records.
    def _parse_violations(s):
        # NaN-safe: legacy records (pre-Phase-6) may have null in this
        # column, which pandas surfaces as float NaN, not None/empty.
        if s is None or s == "" or s == "[]":
            return []
        if isinstance(s, float):  # NaN
            return []
        try:
            return json.loads(s)
        except Exception:
            return []
    schema_violations = {
        "fy":  _parse_violations(latest.get("schema_violations_json")),
        "ttm": _parse_violations(ttm_history.iloc[-1].get("schema_violations_json"))
                if not ttm_history.empty else [],
    }

    bundle: Dict[str, Any] = {
        "currency": {
            "reporting": _fx_currency,
            "fx_rate":   _fx_rate,
            "converted": _fx_rate != 1.0,
            # True when the filer is non-USD but the FX rate couldn't be
            # fetched — the displayed numbers are still in native currency.
            "fx_unavailable": (_fx_currency != "USD" and _fx_rate == 1.0),
        },
        "identity":         _build_identity(ticker, latest),
        "freshness":        _build_freshness(latest, ttm_snapshot),
        "ttm_snapshot":     ttm_snapshot,
        "income_statement": _build_income_statement(latest, clean_json, raw_json),
        "balance_sheet":    _build_balance_sheet(latest, clean_json, raw_json),
        "returns_capital":  _build_returns_capital(latest, clean_json, raw_json),
        "lease_items":      _build_lease_items(clean_json, raw_json),
        "prior_income_statement": (
            _build_income_statement(prior_row, prior_clean_json, prior_raw_json)
            if prior_row is not None else {}
        ),
        "prior_balance_sheet": (
            _build_balance_sheet(prior_row, prior_clean_json, prior_raw_json)
            if prior_row is not None else {}
        ),
        "prior_returns_capital": (
            _build_returns_capital(prior_row, prior_clean_json, prior_raw_json)
            if prior_row is not None else {}
        ),
        "prior_lease_items": (
            _build_lease_items(prior_clean_json, prior_raw_json)
            if prior_row is not None else {}
        ),
        "prior_fiscal_year": (int(prior_row["fiscal_year"])
                              if prior_row is not None else None),
        "prior_period_end_date": (
            str(prior_row.get("period_end_date") or "")[:10]
            if prior_row is not None else None
        ),
        "fiscal_history":   _build_fiscal_history(fy_history),
        "schema_violations": schema_violations,
        "dcf_inputs":       {},
        "dcf_scenarios":    {},
        "projections":      [],
        "terminal_value":   {},
        "assumptions":      {},
        "cagr_ladder":      [],
        "bypass":           None,
    }

    # Run DCF live
    try:
        calc_input = make_calc_input(ticker, df=history)
        engine = DCFEngine(verbose=False)
        result = engine.run(calc_input)
        dcf_dict = result.to_dict()
        sections = _build_dcf_sections(dcf_dict, result)
        bundle["dcf_inputs"]     = sections.get("inputs", {})
        bundle["dcf_scenarios"]  = sections.get("scenarios", {})
        bundle["projections"]    = sections.get("projections", [])
        bundle["terminal_value"] = sections.get("terminal", {})
        bundle["assumptions"]    = sections.get("assumptions", {})
        bundle["cagr_ladder"]    = sections.get("cagr_ladder", [])
    except NotImplementedError as e:
        bundle["bypass"] = str(e)
    except Exception as e:
        bundle["bypass"] = f"DCF failed: {type(e).__name__}: {e}"

    # Current-State Awareness (Phase 1.5): reconcile the engine's near-term
    # growth assumption against forward analyst consensus, surface flags. Does
    # not change the engine math — adds metadata + flags for the analyst.
    try:
        from aletheia.agents.current_state import build_current_state
        from aletheia.agents.current_state_events import cached_events
        _eng_y1 = (bundle.get("assumptions") or {}).get("revenue_cagr_y1_5")
        # Reused signals (Phase A/B) — sector-relative valuation from the
        # MultipleDecomposition tool + policy/regulatory context from the
        # regulatory_exposure 10-K dimension. Both deterministic / cache reads;
        # no new LLM call. Guarded so a failure leaves current_state intact.
        _md = None
        try:
            from aletheia.tools.multiple_decomposition import MultipleDecomposition
            _mdres = MultipleDecomposition(verbose=False).run(
                make_calc_input(ticker, df=history))
            _md = {
                "market_ev_ebitda":        _mdres.market_ev_ebitda,
                "sector":                  getattr(_mdres, "sector", None),
                "sector_median_ev_ebitda": _mdres.sector_median_ev_ebitda,
                "vs_sector_premium":       _mdres.vs_sector_premium,
            }
        except Exception:
            _md = None
        _reg = None
        try:
            # `db` above is already closed (line ~559); open a fresh read.
            _regdb = InvestmentDatabase(verbose=False)
            try:
                _reg = _regdb.get_latest_assessment(ticker, "regulatory_exposure")
            finally:
                _regdb.close()
        except Exception:
            _reg = None
        _cs = build_current_state(
            ticker,
            engine_y1_growth=_eng_y1,
            latest_fy=int(latest["fiscal_year"]),
            # Cache-only read — no LLM call on page load. The grounded events
            # fetch runs on an explicit refresh (fetch_events force=True).
            events=cached_events(ticker),
            multiple_decomposition=_md,
            regulatory_exposure=_reg,
        ).to_dict()
        bundle["current_state"] = _annotate_flag_acks(ticker, _cs)
    except Exception as e:
        bundle["current_state"] = {"error": f"{type(e).__name__}: {e}"}

    # Provenance: whether analyst DCF-assumption overrides are active.
    # Lets the view banner an overridden valuation so it's never mistaken
    # for the model-derived one.
    try:
        from aletheia.data.database import InvestmentDatabase
        _ovdb = InvestmentDatabase(verbose=False)
        try:
            bundle["dcf_overrides"] = _ovdb.get_dcf_overrides(ticker)
        finally:
            _ovdb.close()
    except Exception:
        bundle["dcf_overrides"] = {}

    return bundle
