"""FMP validation core — pure-function machinery for cross-checking
cleaned/derived values against Financial Modeling Prep.

Extracted from `scripts/validate_fmp.py` so the same comparison primitives
can be reused by:
  - `scripts/validate_fmp.py` (CLI + Markdown report — preserved)
  - `aletheia/data/fmp_validation.py` (Gate orchestrators — A/B/D)
  - UI consumers (quality_report, validation_badge, add_ticker_pipeline)

This module is import-safe (no I/O at import time) and depends only on
`fmp_client` for FMP fetches + `make_calc_input` for our cleaned data.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


# ────────────────────────────────────────────────────────────────────────
# Field maps — FMP key ↔ Aletheia key. Tuple shapes documented inline.
# ────────────────────────────────────────────────────────────────────────

INCOME_FIELDS = [
    # (label, fmp_key, our_key)
    ("Revenue",              "revenue",                                 "Revenue"),
    ("COGS",                 "costOfRevenue",                           "COGS"),
    ("R&D",                  "researchAndDevelopmentExpenses",          "R&D"),
    ("SG&A",                 "sellingGeneralAndAdministrativeExpenses", "SG&A"),
    ("Operating Income",     "operatingIncome",                         "OperatingIncome"),
    ("EBITDA",               "ebitda",                                  "_derived_EBITDA"),
    ("Net Income",           "netIncome",                               "NetIncome"),
    ("Diluted EPS",          "epsDiluted",                              "DilutedEPS"),
    ("Diluted Shares",       "weightedAverageShsOutDil",                "SharesDiluted"),
]

BALANCE_FIELDS = [
    ("Cash",                  "cashAndCashEquivalents",   "Cash"),
    ("Short-Term Investments", "shortTermInvestments",    "ShortTermInvestments"),
    ("Accounts Receivable",   "netReceivables",           "AccountsReceivable"),
    ("Inventory",             "inventory",                "Inventory"),
    ("Current Assets",        "totalCurrentAssets",       "CurrentAssets"),
    ("PPE Net",               "propertyPlantEquipmentNet", "PPE"),
    ("Goodwill",              "goodwill",                 "Goodwill"),
    ("Total Assets",          "totalAssets",              "TotalAssets"),
    ("Accounts Payable",      "accountPayables",          "AccountsPayable"),
    ("Short-Term Debt",       "shortTermDebt",            "ShortTermDebt"),
    ("Current Liabilities",   "totalCurrentLiabilities",  "LiabilitiesCurrent"),
    ("Long-Term Debt",        "longTermDebt",             "LongTermDebt"),
    ("Total Liabilities",     "totalLiabilities",         "TotalLiabilities"),
    ("Total Equity",          "totalStockholdersEquity",  "TotalEquity"),
]

CASHFLOW_FIELDS = [
    # FMP encodes CapEx, Dividends, Buybacks as negative outflows;
    # we store as positive magnitudes — comparison uses use_abs=True.
    ("Operating CF",   "operatingCashFlow",      "OperatingCF"),
    ("CapEx",          "capitalExpenditure",     "CapEx"),
    ("Free Cash Flow", "freeCashFlow",           "_derived_FCF"),
    ("Dividends Paid", "commonDividendsPaid",    "DividendsPaid"),
    ("Buybacks",       "commonStockRepurchased", "Buybacks"),
]

# Derived ratios + key metrics. (label, fmp_key, fmp_endpoint, our_column, fmp_scale)
DERIVED_FIELDS = [
    ("Gross Margin %",       "grossProfitMargin",      "ratios",      "derived_GrossMargin_Pct",     100.0),
    ("EBIT Margin %",        "operatingProfitMargin",  "ratios",      "derived_EBIT_Margin_Pct",     100.0),
    ("EBITDA Margin %",      "ebitdaMargin",           "ratios",      "derived_EBITDA_Margin_Pct",   100.0),
    ("ROIC",                 "returnOnInvestedCapital", "key_metrics", "derived_ROIC",                1.0),
    ("ROE",                  "returnOnEquity",         "key_metrics", "derived_ROE",                  1.0),
    ("Invested Capital",     "investedCapital",        "key_metrics", "derived_InvestedCapital",      1.0),
]

# Screening-engine ratios.
# (display_label, screening_metric_name, fmp_key, fmp_endpoint, fmp_scale)
SCREENING_FIELDS = [
    ("P/E Ratio",         "P/E Ratio",             "priceToEarningsRatio",     "ratios",      1.0),
    ("P/B Ratio",         "P/B Ratio",             "priceToBookRatio",         "ratios",      1.0),
    ("EV/EBITDA",         "EV/EBITDA (clean)",     "evToEBITDA",               "key_metrics", 1.0),
    ("EV/FCF",            "EV/FCF",                "evToFreeCashFlow",         "key_metrics", 1.0),
    ("Debt-to-Equity",    "Debt-to-Equity",        "debtToEquityRatio",        "ratios",      1.0),
    ("Interest Coverage", "Interest Coverage",     "interestCoverageRatio",    "ratios",      1.0),
    ("Current Ratio",     "Current Ratio",         "currentRatio",             "ratios",      1.0),
    ("Net Debt / EBITDA", "Net Debt / EBITDA",     "netDebtToEBITDA",          "key_metrics", 1.0),
    ("Dividend Yield %",  "Dividend Yield %",      "dividendYieldPercentage",  "ratios",      1.0),
    ("EV ($B)",           "EV (Billions)",         "enterpriseValue",          "key_metrics", 1.0e-9),
    ("Market Cap ($B)",   "Market Cap (Billions)", "marketCap",                "key_metrics", 1.0e-9),
]


# ────────────────────────────────────────────────────────────────────────
# Tolerance bands (decimal). Used by both legacy validate_ticker and
# the new gate orchestrators. Gate-specific tiers can override.
# ────────────────────────────────────────────────────────────────────────

TOL_OK    = 0.01   # ✓ <1%   byte-perfect
TOL_NEAR  = 0.05   # ≈ 1-5%  acceptable
# Above TOL_NEAR → ✗ structural drift


# ────────────────────────────────────────────────────────────────────────
# Comparison primitives — pure functions
# ────────────────────────────────────────────────────────────────────────

def _our_value(row: Any, raw_json: Dict[str, Any], key: str) -> Optional[float]:
    """Lookup our value from row/raw_json. `_derived_X` reads from the
    derived_X column on the row."""
    if key.startswith("_derived_"):
        col = "derived_" + key.replace("_derived_", "")
        v = row.get(col) if hasattr(row, "get") else None
        return float(v) if v is not None else None
    v = raw_json.get(key)
    return float(v) if v is not None else None


def _drift_label(
    ours: Optional[float], theirs: Optional[float]
) -> Tuple[str, Optional[float]]:
    """Compute drift % and signal label.

    None-vs-zero is treated as ✓: when one source reports an explicit
    zero (e.g., goodwill=0 because the issuer has no goodwill) and the
    other source has no tag at all, both are semantically "no value
    here".

    Returns (flag, drift_fraction). Flag is one of:
      "✓"            byte-perfect (drift < TOL_OK or both null)
      "≈"            acceptable    (TOL_OK ≤ drift < TOL_NEAR)
      "✗"            structural    (drift ≥ TOL_NEAR)
      "—"            both null
      "ours_missing" we lack a value FMP has
      "fmp_missing"  FMP lacks a value we have (legitimate when ours=0)
    """
    if ours is None and theirs is None:
        return "—", None
    if ours is None:
        return ("✓", 0.0) if (theirs is not None and abs(theirs) < 1e-6) else ("ours_missing", None)
    if theirs is None:
        return ("✓", 0.0) if abs(ours) < 1e-6 else ("fmp_missing", None)
    if abs(theirs) < 1e-6:
        if abs(ours) < 1e-6:
            return "✓", 0.0
        return "✗", float("inf")
    drift = (ours - theirs) / abs(theirs)
    flag = "✓" if abs(drift) < TOL_OK else ("≈" if abs(drift) < TOL_NEAR else "✗")
    return flag, drift


def _abs_sign(v: Optional[float]) -> Optional[float]:
    """FMP encodes CapEx, Dividends, Buybacks as negative outflows; we
    may store as positive magnitudes. Compare on absolute values for
    those fields."""
    return abs(v) if v is not None else None


# ────────────────────────────────────────────────────────────────────────
# Per-statement comparison
# ────────────────────────────────────────────────────────────────────────

def _compare_statement(
    fields: List[Tuple[str, str, str]],
    fmp_record: Dict[str, Any],
    our_row: Any,
    raw_json: Dict[str, Any],
    use_abs: bool = False,
) -> List[Dict[str, Any]]:
    rows = []
    for label, fmp_key, our_key in fields:
        fmp_val = fmp_record.get(fmp_key)
        if fmp_val is not None:
            try:
                fmp_val = float(fmp_val)
            except (TypeError, ValueError):
                fmp_val = None
        our_val = _our_value(our_row, raw_json, our_key)
        if use_abs:
            fmp_val = _abs_sign(fmp_val)
            our_val = _abs_sign(our_val)
        flag, drift = _drift_label(our_val, fmp_val)
        rows.append({
            "label": label,
            "fmp":   fmp_val,
            "ours":  our_val,
            "drift": drift,
            "flag":  flag,
        })
    return rows


def _compare_derived(
    ticker: str, fy: int, our_row: Any
) -> List[Dict[str, Any]]:
    """Compare derived ratios + key metrics. Suppresses ROIC + Invested
    Capital for non-fcff_compatible business models (banks, insurers,
    utilities) where standard formulas don't apply."""
    from aletheia.data import fmp_client
    try:
        from config.ticker_classification import UNIVERSE
        bm = UNIVERSE.get(ticker.upper())
        is_standard_business = (
            bm is not None and bm.business_model == "fcff_compatible"
        )
    except Exception:
        is_standard_business = True

    fmp_ratios = fmp_client.fetch_ratios(ticker) or []
    fmp_km     = fmp_client.fetch_key_metrics(ticker) or []
    ratios_rec = fmp_client.get_for_fiscal_year(fmp_ratios, fy) or {}
    km_rec     = fmp_client.get_for_fiscal_year(fmp_km, fy) or {}

    SCHEMA_SENSITIVE = {"derived_ROIC", "derived_InvestedCapital"}

    rows = []
    for label, fmp_key, endpoint, our_col, scale in DERIVED_FIELDS:
        if not is_standard_business and our_col in SCHEMA_SENSITIVE:
            rows.append({
                "label": label, "fmp": None, "ours": None,
                "drift": None, "flag": "n/a (schema)",
            })
            continue
        rec = ratios_rec if endpoint == "ratios" else km_rec
        fmp_raw = rec.get(fmp_key)
        try:
            fmp_val = float(fmp_raw) * scale if fmp_raw is not None else None
        except (TypeError, ValueError):
            fmp_val = None
        try:
            our_val = float(our_row.get(our_col)) if our_row.get(our_col) is not None else None
        except (TypeError, ValueError):
            our_val = None
        flag, drift = _drift_label(our_val, fmp_val)
        rows.append({
            "label": label, "fmp": fmp_val, "ours": our_val,
            "drift": drift, "flag":  flag,
        })
    return rows


def _compare_screening(
    ticker: str, fy: int, calc: Any
) -> List[Dict[str, Any]]:
    """Compare screening-engine outputs to FMP equivalents. Validates the
    full screening pipeline that conviction scoring + UI consume."""
    from aletheia.data import fmp_client
    fmp_ratios = fmp_client.fetch_ratios(ticker) or []
    fmp_km     = fmp_client.fetch_key_metrics(ticker) or []
    ratios_rec = fmp_client.get_for_fiscal_year(fmp_ratios, fy) or {}
    km_rec     = fmp_client.get_for_fiscal_year(fmp_km, fy) or {}

    try:
        from aletheia.tools.screening_ratios import ScreeningEngine
        card = ScreeningEngine().score(calc)
    except Exception:
        return [
            {"label": label, "fmp": None, "ours": None, "drift": None, "flag": "ours_missing"}
            for label, *_ in SCREENING_FIELDS
        ]

    metrics_by_name = {
        m.name: m.value for m in (card.metrics or []) if m.value is not None
    }

    rows = []
    for label, screen_name, fmp_key, endpoint, scale in SCREENING_FIELDS:
        rec = ratios_rec if endpoint == "ratios" else km_rec
        fmp_raw = rec.get(fmp_key)
        try:
            fmp_val = float(fmp_raw) * scale if fmp_raw is not None else None
        except (TypeError, ValueError):
            fmp_val = None
        ours_raw = metrics_by_name.get(screen_name)
        try:
            ours_val = float(ours_raw) if ours_raw is not None else None
            if isinstance(ours_val, float) and ours_val != ours_val:  # NaN
                ours_val = None
        except (TypeError, ValueError):
            ours_val = None
        flag, drift = _drift_label(ours_val, fmp_val)
        rows.append({
            "label": label, "fmp": fmp_val, "ours": ours_val,
            "drift": drift, "flag": flag,
        })
    return rows


# ────────────────────────────────────────────────────────────────────────
# Top-level: validate one ticker. Library entry — UI consumers + scripts
# ────────────────────────────────────────────────────────────────────────

def validate_ticker(
    ticker: str, fy: Optional[int] = None
) -> Dict[str, Any]:
    """Run full FMP validation for `ticker` × `fy` (defaults to latest).

    Returns a dict with keys:
      ticker, fiscal_year, income, balance, cashflow, derived, screening
    OR an error dict {ticker, error, status?} when a fail-soft path
    triggers (no FMP key, currency mismatch, missing FY, etc.).

    This signature is preserved byte-perfect from the original
    `scripts/validate_fmp.py` so existing UI consumers (quality_report,
    validation_badge, add_ticker_pipeline) keep working unchanged.
    """
    from aletheia.data import fmp_client
    from aletheia.utils.calc_input_builder import make_calc_input

    calc = make_calc_input(ticker)
    df = calc.df
    if df.empty:
        return {"ticker": ticker, "error": "no cleaned data in DB"}
    if fy is None:
        fy = int(df["fiscal_year"].max())
    matched = df[df["fiscal_year"] == fy]
    if matched.empty:
        return {"ticker": ticker, "error": f"no FY{fy} record"}
    our_row = matched.iloc[0]
    raw_json = json.loads(our_row.get("raw_json") or "{}")

    fmp_inc = fmp_client.fetch_income_statements(ticker)
    fmp_bs  = fmp_client.fetch_balance_sheets(ticker)
    fmp_cf  = fmp_client.fetch_cash_flows(ticker)
    if fmp_inc is None or fmp_bs is None or fmp_cf is None:
        status = fmp_client.probe_subscription(ticker)
        if status == "restricted":
            err = "FMP subscription-restricted (HTTP 402: ticker not in free tier)"
        elif status == "quota_exhausted":
            err = "FMP daily quota exhausted (HTTP 429); retry after UTC midnight"
        elif status == "auth_error":
            err = "FMP auth error (check FMP_API_KEY)"
        elif status == "no_key":
            err = "FMP_API_KEY not set"
        elif status == "network_error":
            err = "FMP network error"
        else:
            err = "FMP fetch returned partial/no data"
        return {"ticker": ticker, "error": err, "status": status}

    inc = fmp_client.get_for_fiscal_year(fmp_inc, fy)
    bs  = fmp_client.get_for_fiscal_year(fmp_bs, fy)
    cf  = fmp_client.get_for_fiscal_year(fmp_cf, fy)
    if not (inc and bs and cf):
        return {"ticker": ticker, "error": f"FMP missing FY{fy} statement"}

    fmp_ccy = (inc.get("reportedCurrency") or "").upper()
    if fmp_ccy and fmp_ccy != "USD":
        return {
            "ticker": ticker,
            "error": (f"FMP reports in {fmp_ccy}; cleaned data is in USD "
                      f"(foreign filer — skip FMP comparison)"),
            "status": "currency_mismatch",
        }

    derived = _compare_derived(ticker, fy, our_row)
    screening = _compare_screening(ticker, fy, calc)

    return {
        "ticker": ticker,
        "fiscal_year": fy,
        "income":   _compare_statement(INCOME_FIELDS,  inc, our_row, raw_json),
        "balance":  _compare_statement(BALANCE_FIELDS, bs,  our_row, raw_json),
        "cashflow": _compare_statement(CASHFLOW_FIELDS, cf, our_row, raw_json, use_abs=True),
        "derived":  derived,
        "screening": screening,
    }


__all__ = [
    "INCOME_FIELDS", "BALANCE_FIELDS", "CASHFLOW_FIELDS",
    "DERIVED_FIELDS", "SCREENING_FIELDS",
    "TOL_OK", "TOL_NEAR",
    "_our_value", "_drift_label", "_abs_sign",
    "_compare_statement", "_compare_derived", "_compare_screening",
    "validate_ticker",
]
