"""FMP Validation Gates — orchestrators for the 4 validation hook points.

Sits on top of `fmp_validation_core` (pure-function comparison primitives)
and wraps them into gate-specific entry points called from elsewhere in
the pipeline:

  Gate A   validate_ingestion_record(ticker, fy, db_row, raw_json)
           → called from cleaning_engine.CleaningEngine.clean()
           → BLOCKS DB write on >5% drift on critical line items
           → discard, log, raise IngestionValidationFailure

  Gate B   validate_calc_output(ticker, phase2_valuation)
           → called from calc_node.calc_node() before return
           → STAMPS state["_calc_validation"]; never aborts
           → Gate F catches systematic blocking_drift across the universe

  Gate D   build_receipt_block(state)
           → called from lead.ServingReportWriter.save_report()
           → aggregates Gate A + Gate B results from state
           → adds final_assembly cross-check
           → returns the `_validation` block for the serving JSON

  Gate F   aggregate_universe(serving_dir)
           → called from scripts/regen_universe.sh step 6
           → reads every report's `_validation` block
           → applies threshold matrix; exits non-zero on FAIL

Failure modes are uniform: any FMP unavailability → status="skipped"
with a `skip_reason`. Validator never propagates an exception unless the
caller asks it to (Gate A is the only gate that raises, by design).
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from aletheia.data.fmp_validation_core import (
    _drift_label,
    _abs_sign,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# Tiers and policy
# ────────────────────────────────────────────────────────────────────────
# Drift bands per locked Open Question matrix (user accepted defaults):
#   strict       — <0.5%   /  0.5-2%   /  >2%      (revenue, NI, totalAssets)
#   standard     — <1%     /  1-5%     /  >5%      (most line items + ratios)
#   definitional — <5%     /  5-25%    /  >25%     (ROIC, IC, EBITDA, beta)
#   sanity_only  — flag only when outside hard bounds, never blocking
#
# Gate A blocking set (locked): revenue, netIncome, totalAssets, ebitda,
# fcf, netDebt above their `standard` band → BLOCK.

_TIER_BANDS = {
    "strict":       (0.005, 0.02),
    "standard":     (0.01,  0.05),
    "definitional": (0.05,  0.25),
    # sanity_only: drift checks always pass (∞ bands). Only useful for
    # fields where we want the value comparison stamped but never blocked
    # — e.g. FMP's `dcf` intrinsic-value estimate vs ours (different
    # methodologies; band-based comparison would over-flag).
    "sanity_only":  (float("inf"), float("inf")),
}


def _classify_drift(drift_pct: Optional[float], tier: str) -> str:
    """Returns 'byte_perfect' | 'acceptable' | 'structural_drift' | 'n_a'.

    `drift_pct` is the signed fraction. Compare against `tier`'s bands.
    """
    if drift_pct is None:
        return "n_a"
    bp_band, accept_band = _TIER_BANDS.get(tier, _TIER_BANDS["standard"])
    abs_d = abs(drift_pct)
    if abs_d < bp_band:
        return "byte_perfect"
    if abs_d < accept_band:
        return "acceptable"
    return "structural_drift"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ────────────────────────────────────────────────────────────────────────
# Gate A — ingestion-time validation
# ────────────────────────────────────────────────────────────────────────
#
# Checks 13 fields per record, blocks DB write when any of the 6
# blocking-set fields exceed standard-tier band. Latest-FY-only.
#
# Result shape (subset of the schema in §7 of the plan):
#
#   {
#     "status":  "validated" | "skipped" | "drift" | "blocking_drift",
#     "skip_reason":  None | str,
#     "fiscal_year_validated": int,
#     "fetched_at":   ISO8601,
#     "fields":       {field_name: {our, fmp, drift_pct, tier, blocking,
#                                    status, source_endpoint}},
#     "blocking_fields": [str],   # subset of `fields` with blocking=True+drift
#   }
#

# Field resolutions for Gate A. (our_key, fmp_endpoint, fmp_keys[], tier, blocking, use_abs)
# Fallback list per Q5 from prior plan: try each FMP key in order. Use_abs
# applies to FMP fields where signs differ (CapEx, Buybacks).
_GATE_A_FIELDS: List[Tuple[str, str, List[str], str, bool, bool]] = [
    # field_name,             endpoint,         fmp_keys (fallback),                     tier,           blocking, use_abs
    ("revenue",               "income",         ["revenue", "totalRevenue"],             "strict",       True,  False),
    ("net_income",            "income",         ["netIncome"],                           "strict",       True,  False),
    ("ebitda",                "income",         ["ebitda"],                              "definitional", True,  False),  # def-tier b/c SBC differences
    ("operating_income",      "income",         ["operatingIncome"],                     "standard",     False, False),
    ("shares_diluted",        "income",         ["weightedAverageShsOutDil"],            "strict",       False, False),
    ("total_assets",          "balance",        ["totalAssets"],                         "strict",       True,  False),
    ("total_liabilities",     "balance",        ["totalLiabilities"],                    "standard",     False, False),
    ("total_equity",          "balance",        ["totalStockholdersEquity"],             "standard",     False, False),
    ("cash",                  "balance",        ["cashAndCashEquivalents"],              "strict",       False, False),
    ("operating_cash_flow",   "cashflow",       ["operatingCashFlow"],                   "strict",       False, False),
    ("fcf",                   "cashflow",       ["freeCashFlow"],                        "standard",     True,  False),
    # net_debt: derived in cleaning_engine; FMP exposes via /key-metrics
    ("net_debt",              "key_metrics",    ["netDebt"],                             "standard",     True,  False),
    # ratios from /ratios + /key-metrics — derived-side comparisons
    ("gross_margin_pct",      "ratios",         ["grossProfitMargin"],                   "standard",     False, False),  # FMP returns decimal; our scaling handled in resolver
    ("ebit_margin_pct",       "ratios",         ["operatingProfitMargin"],               "standard",     False, False),
]

# Map our_key → cleaning_engine column name (or formula for derived).
# Multiplier applied to FMP value to align units (e.g. FMP returns
# margins as decimals 0.469, we store percent 46.9 → fmp_scale=100).
_OUR_KEY_TO_DB_COL: Dict[str, Tuple[str, float]] = {
    "revenue":             ("clean_Revenue", 1.0),
    "net_income":          ("raw_NetIncome", 1.0),
    "ebitda":              ("derived_EBITDA", 1.0),
    "operating_income":    ("raw_OperatingIncome", 1.0),
    "shares_diluted":      ("raw_SharesDiluted", 1.0),
    "total_assets":        ("raw_TotalAssets", 1.0),
    "total_liabilities":   ("raw_TotalLiabilities", 1.0),
    "total_equity":        ("raw_TotalEquity", 1.0),
    "cash":                ("raw_Cash", 1.0),
    "operating_cash_flow": ("raw_OperatingCF", 1.0),
    "fcf":                 ("derived_FCF", 1.0),
    "net_debt":            ("derived_NetDebt", 1.0),
    "gross_margin_pct":    ("derived_GrossMargin_Pct", 0.01),  # FMP decimal, ours percent → divide our_pct by 100 to compare
    "ebit_margin_pct":     ("derived_EBIT_Margin_Pct", 0.01),
}


class IngestionValidationFailure(Exception):
    """Raised by Gate A when blocking drift is detected. Caller catches
    in cleaning_engine and skips the DB write for this record."""

    def __init__(self, ticker: str, fiscal_year: int,
                 result: Dict[str, Any]) -> None:
        self.ticker = ticker
        self.fiscal_year = fiscal_year
        self.result = result
        blocking = result.get("blocking_fields", [])
        super().__init__(
            f"Gate A: {ticker} FY{fiscal_year} blocked by FMP drift on "
            f"{', '.join(blocking)}"
        )


def _fetch_fmp_for_gate_a(
    ticker: str, fy: int
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Fetch all FMP endpoints needed for Gate A. Returns (endpoint_data,
    skip_reason). Skip-reason set when any fail-soft path triggers."""
    from aletheia.data import fmp_client

    if not fmp_client.has_api_key():
        return None, "fmp_api_key_not_configured"

    try:
        inc = fmp_client.fetch_income_statements(ticker)
        bs  = fmp_client.fetch_balance_sheets(ticker)
        cf  = fmp_client.fetch_cash_flows(ticker)
        km  = fmp_client.fetch_key_metrics(ticker)
        ratios = fmp_client.fetch_ratios(ticker)
    except Exception as exc:
        return None, f"fmp_network_error:{type(exc).__name__}"

    if any(x is None for x in (inc, bs, cf, km, ratios)):
        status = fmp_client.probe_subscription(ticker)
        skip_map = {
            "restricted":       "fmp_subscription_restricted",
            "quota_exhausted":  "fmp_quota_exhausted",
            "auth_error":       "fmp_auth_error",
            "no_key":           "fmp_api_key_not_configured",
            "network_error":    "fmp_network_error",
        }
        return None, skip_map.get(status, "fmp_unavailable")

    inc_fy = fmp_client.get_for_fiscal_year(inc, fy)
    bs_fy  = fmp_client.get_for_fiscal_year(bs,  fy)
    cf_fy  = fmp_client.get_for_fiscal_year(cf,  fy)
    km_fy  = fmp_client.get_for_fiscal_year(km,  fy)
    rt_fy  = fmp_client.get_for_fiscal_year(ratios, fy)
    if not (inc_fy and bs_fy and cf_fy):
        return None, f"fmp_missing_fy{fy}"

    # Currency check: foreign filers (ASML EUR, TSM TWD) skipped pending
    # Phase 6 FX support.
    fmp_ccy = (inc_fy.get("reportedCurrency") or "").upper()
    if fmp_ccy and fmp_ccy != "USD":
        return None, f"fmp_currency_mismatch:{fmp_ccy}"

    return ({
        "income":      inc_fy,
        "balance":     bs_fy,
        "cashflow":    cf_fy,
        "key_metrics": km_fy or {},
        "ratios":      rt_fy or {},
    }, None)


def _our_value_for_gate(record: Any, our_key: str) -> Optional[float]:
    """Pull our-side value for a Gate A field. `record` is a dict-like
    (CleanedRecord proxy) keyed by db column name."""
    db_col, scale = _OUR_KEY_TO_DB_COL.get(our_key, (None, 1.0))
    if db_col is None:
        return None
    val = record.get(db_col) if hasattr(record, "get") else None
    if val is None:
        return None
    try:
        return float(val) * scale
    except (TypeError, ValueError):
        return None


def _fmp_value_for_gate(
    fmp_data: Dict[str, Any], endpoint: str, fmp_keys: List[str], use_abs: bool
) -> Optional[float]:
    """Pull FMP-side value, trying each fmp_key in fallback order."""
    rec = fmp_data.get(endpoint, {})
    for key in fmp_keys:
        v = rec.get(key)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        return _abs_sign(v) if use_abs else v
    return None


def validate_ingestion_record(
    ticker: str,
    fiscal_year: int,
    record: Any,
    *,
    is_latest_fy: bool = True,
) -> Dict[str, Any]:
    """Gate A — validate one cleaned record against FMP.

    Args:
      ticker: upper-case
      fiscal_year: int
      record: dict-like keyed by cleaning_engine column names
      is_latest_fy: True only for the most recent FY ingest (per locked
        multi-FY policy: skip older FYs to cap API cost)

    Returns: result dict per the §7 schema.

    Does NOT raise on its own. The caller checks status and decides
    whether to discard the record (raise IngestionValidationFailure).
    """
    base = {
        "status":                "validated",
        "skip_reason":           None,
        "fiscal_year_validated": fiscal_year,
        "fetched_at":            _now_iso(),
        "fields":                {},
        "blocking_fields":       [],
    }

    if not is_latest_fy:
        return {**base, "status": "skipped", "skip_reason": "not_latest_fy"}

    fmp_data, skip_reason = _fetch_fmp_for_gate_a(ticker, fiscal_year)
    if fmp_data is None:
        return {**base, "status": "skipped", "skip_reason": skip_reason}

    fields: Dict[str, Dict[str, Any]] = {}
    blocking_fields: List[str] = []
    n_drift = 0
    n_blocking = 0

    for field_name, endpoint, fmp_keys, tier, blocking, use_abs in _GATE_A_FIELDS:
        ours = _our_value_for_gate(record, field_name)
        fmp  = _fmp_value_for_gate(fmp_data, endpoint, fmp_keys, use_abs)
        flag, drift_pct = _drift_label(ours, fmp)
        status = _classify_drift(drift_pct, tier)
        is_violation = (status == "structural_drift")
        is_blocking_violation = (blocking and is_violation)

        fields[field_name] = {
            "ours":            ours,
            "fmp":             fmp,
            "drift_pct":       drift_pct,
            "tier":            tier,
            "blocking":        blocking,
            "status":          status,
            "source_endpoint": endpoint,
            "fmp_key_resolved": fmp_keys[0],   # which key actually matched (caller can refine)
        }
        if is_violation:
            n_drift += 1
        if is_blocking_violation:
            n_blocking += 1
            blocking_fields.append(field_name)

    if n_blocking > 0:
        result_status = "blocking_drift"
    elif n_drift > 0:
        result_status = "drift"
    else:
        result_status = "validated"

    return {
        **base,
        "status":          result_status,
        "fields":          fields,
        "blocking_fields": blocking_fields,
    }


# ────────────────────────────────────────────────────────────────────────
# Gate B — calc_node output validation (stamp-not-abort)
# ────────────────────────────────────────────────────────────────────────
#
# Validates the live-computed phase2_valuation dict against FMP's market
# snapshot before agents consume it. Stamp-not-abort semantic per locked
# Q2: blocking-tier drift gets recorded but the calc node returns
# normally; agents see state["_calc_validation"] and Gate F (universe)
# is what fails the regen.
#
# Cost: 1 live FMP call per ticker (`/profile`). Other endpoints reused
# from Gate A's cache for the same ticker.

# (human_label, phase2_path_fallback_list, fmp_endpoint, fmp_keys, tier, blocking)
# Paths are tried in order — first non-None wins. Lets Gate B work
# whether it sees the live calc_node state shape OR the lead-processed
# serving-JSON shape:
#   beta     — live: phase2.dcf.beta;          serving: phase2.beta
#   market_cap — live: phase2.dcf.market_cap (DCFResult.to_dict);  serving: not promoted
#   ev_ebitda  — live: phase2.multiples.market_ev_ebitda OR phase2.ev_ebitda_market;
#                serving: phase2.multiple_decomposition.market_ev_ebitda
#   p_e       — calc_node doesn't compute it directly; FMP-only reference
_GATE_B_FIELDS: List[Tuple[str, List[str], str, List[str], str, bool]] = [
    ("beta",                       ["dcf.beta", "beta"],
                                                   "profile",     ["beta"],                                       "definitional", True),
    ("current_price",              ["dcf.current_price"],
                                                   "profile",     ["price"],                                      "strict",       True),
    ("market_cap",                 ["dcf.market_cap"],
                                                   "profile",     ["mktCap", "marketCap"],                        "standard",     True),
    ("market_ev_ebitda",           ["multiples.market_ev_ebitda",
                                     "multiple_decomposition.market_ev_ebitda",
                                     "ev_ebitda_market"],
                                                   "key_metrics", ["evToEBITDA", "enterpriseValueOverEBITDA"],    "standard",     False),
    ("market_p_e",                 ["multiples.market_p_e",
                                     "multiple_decomposition.market_p_e",
                                     "dcf.market_p_e"],
                                                   "ratios",      ["priceToEarningsRatio", "priceEarningsRatio"], "standard",     False),
    ("intrinsic_per_share_base",   ["three_scenario_dcf.base.intrinsic_per_share",
                                     "dcf.base_intrinsic_per_share"],
                                                   "profile",     ["dcf"],                                        "sanity_only",  False),
]


def _resolve_phase2_path(phase2: Dict[str, Any], path: str) -> Optional[float]:
    """Walk a dotted path through nested dicts; return float or None."""
    cur: Any = phase2
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    try:
        return float(cur)
    except (TypeError, ValueError):
        return None


def _resolve_phase2_paths(
    phase2: Dict[str, Any], paths: List[str]
) -> Optional[float]:
    """Try each path in order; return the first non-None value."""
    for p in paths:
        v = _resolve_phase2_path(phase2, p)
        if v is not None:
            return v
    return None


def _fetch_fmp_for_gate_b(
    ticker: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Fetch live `/profile` plus cached `/key-metrics` and `/ratios`."""
    from aletheia.data import fmp_client
    if not fmp_client.has_api_key():
        return None, "fmp_api_key_not_configured"

    try:
        profile = fmp_client.fetch_profile(ticker)
        km      = fmp_client.fetch_key_metrics(ticker)
        ratios  = fmp_client.fetch_ratios(ticker)
    except Exception as exc:
        return None, f"fmp_network_error:{type(exc).__name__}"

    if profile is None:
        status = fmp_client.probe_subscription(ticker)
        skip_map = {
            "restricted":      "fmp_subscription_restricted",
            "quota_exhausted": "fmp_quota_exhausted",
            "auth_error":      "fmp_auth_error",
            "no_key":          "fmp_api_key_not_configured",
            "network_error":   "fmp_network_error",
        }
        return None, skip_map.get(status, "fmp_unavailable")

    # `fetch_profile` returns a list with one element typically; normalize.
    if isinstance(profile, list):
        profile = profile[0] if profile else {}

    # Latest-FY snapshot from /key-metrics and /ratios (first element is
    # most recent in FMP's response). These are warn-only fields, so
    # missing data degrades to skipped without blocking.
    km_rec = (km[0] if km else {}) or {}
    rt_rec = (ratios[0] if ratios else {}) or {}

    return ({
        "profile":     profile,
        "key_metrics": km_rec,
        "ratios":      rt_rec,
    }, None)


def validate_calc_output(
    ticker: str,
    phase2: Dict[str, Any],
) -> Dict[str, Any]:
    """Gate B — validate calc_node's phase2_valuation against FMP.

    Stamp-not-abort: blocking_drift sets status but doesn't raise. Agents
    proceed; Gate F handles the aggregate universe-level failure.
    """
    base = {
        "status":          "validated",
        "skip_reason":     None,
        "fetched_at":      _now_iso(),
        "fields":          {},
        "blocking_fields": [],
    }

    fmp_data, skip_reason = _fetch_fmp_for_gate_b(ticker)
    if fmp_data is None:
        return {**base, "status": "skipped", "skip_reason": skip_reason}

    fields: Dict[str, Dict[str, Any]] = {}
    blocking_fields: List[str] = []
    n_drift = 0
    n_blocking = 0

    for label, phase2_paths, endpoint, fmp_keys, tier, blocking in _GATE_B_FIELDS:
        ours = _resolve_phase2_paths(phase2, phase2_paths)
        # FMP value
        rec = fmp_data.get(endpoint, {})
        fmp = None
        for key in fmp_keys:
            v = rec.get(key)
            if v is not None:
                try:
                    fmp = float(v)
                    break
                except (TypeError, ValueError):
                    continue

        flag, drift_pct = _drift_label(ours, fmp)
        status = _classify_drift(drift_pct, tier)
        is_violation = (status == "structural_drift")
        is_blocking_violation = (blocking and is_violation)

        fields[label] = {
            "ours":            ours,
            "fmp":             fmp,
            "drift_pct":       drift_pct,
            "tier":            tier,
            "blocking":        blocking,
            "status":          status,
            "source_endpoint": endpoint,
            "phase2_paths":    phase2_paths,
        }
        if is_violation:
            n_drift += 1
        if is_blocking_violation:
            n_blocking += 1
            blocking_fields.append(label)

    if n_blocking > 0:
        result_status = "blocking_drift"
    elif n_drift > 0:
        result_status = "drift"
    else:
        result_status = "validated"

    return {
        **base,
        "status":          result_status,
        "fields":          fields,
        "blocking_fields": blocking_fields,
    }


# ────────────────────────────────────────────────────────────────────────
# Gate D — receipt stamp at lead.save_report
# ────────────────────────────────────────────────────────────────────────
#
# Aggregates the per-gate validation results into a single `_validation`
# block on the serving JSON. The block has 3 sub-blocks:
#
#   ingestion       — Gate A's stamp (loaded from DuckDB by the caller)
#   calc            — Gate B's stamp (passed via state["_calc_validation"])
#   final_assembly  — final cross-checks done at receipt time:
#                       - numeric fidelity in the assembled narrative
#                       - scenario IPS monotonicity (bear ≤ base ≤ bull)
#
# No new FMP calls. Pure aggregation + assembled-content cross-checks.

import re as _re

_DOLLAR_RE = _re.compile(r"\$([0-9]+(?:\.[0-9]+)?)")
_PCT_RE    = _re.compile(r"([\-\+]?[0-9]+(?:\.[0-9]+)?)\s*%")


def _check_scenario_monotonicity(serving_report: Dict[str, Any]) -> Dict[str, Any]:
    """bear.IPS ≤ base.IPS ≤ bull.IPS. Warn-only; some legitimate
    inversions exist (e.g. base assumes write-down, bear doesn't)."""
    p2 = (serving_report.get("4_valuation_synthesis") or {}).get("phase2_valuation") or {}
    dcf3 = p2.get("three_scenario_dcf") or {}
    bear_ips = (dcf3.get("bear") or {}).get("intrinsic_per_share")
    base_ips = (dcf3.get("base") or {}).get("intrinsic_per_share")
    bull_ips = (dcf3.get("bull") or {}).get("intrinsic_per_share")
    if None in (bear_ips, base_ips, bull_ips):
        return {"ok": None, "values": {"bear": bear_ips, "base": base_ips, "bull": bull_ips},
                "reason": "incomplete_scenarios"}
    monotonic = (bear_ips <= base_ips <= bull_ips)
    return {
        "ok": bool(monotonic),
        "values": {"bear": float(bear_ips), "base": float(base_ips), "bull": float(bull_ips)},
    }


def _check_narrative_fidelity(serving_report: Dict[str, Any]) -> Dict[str, Any]:
    """Scan the lead's deterministic narrative for numeric mentions and
    cross-check against cited phase2 values. Reuses the same drift bands
    the synthesizer's _check_numeric_fidelity uses."""
    val = serving_report.get("4_valuation_synthesis") or {}
    narr = (val.get("investment_thesis") or {}).get("narrative") or ""
    p2 = val.get("phase2_valuation") or {}
    rdcf = p2.get("reverse_dcf") or {}
    impl = rdcf.get("implied_cagr_10y")
    hist = rdcf.get("historical_cagr") or rdcf.get("historical_cagr_5y")

    violations: List[str] = []

    # implied CAGR mentions
    for m in _re.finditer(
        r"implied\s+(?:10[\-\s]?year\s+)?(?:revenue\s+)?cagr\s+(?:of\s+)?"
        r"([\-\+]?[0-9]+(?:\.[0-9]+)?)\s*%",
        narr,
        flags=_re.IGNORECASE,
    ):
        try:
            pct = float(m.group(1)) / 100
        except ValueError:
            continue
        if impl is not None and abs(pct - impl) > 0.01:
            violations.append(
                f"narrative says implied CAGR {pct*100:.1f}% but cited "
                f"value is {impl*100:.1f}%"
            )

    # historical CAGR mentions
    for m in _re.finditer(
        r"historical\s+(?:cagr|growth|rate)s?\s+(?:of\s+)?"
        r"([\-\+]?[0-9]+(?:\.[0-9]+)?)\s*%",
        narr,
        flags=_re.IGNORECASE,
    ):
        try:
            pct = float(m.group(1)) / 100
        except ValueError:
            continue
        if hist is not None and abs(pct - hist) > 0.01:
            violations.append(
                f"narrative says historical {pct*100:.1f}% but cited "
                f"value is {hist*100:.1f}%"
            )

    return {
        "violations":     len(violations),
        "details":        violations[:10],
    }


def build_receipt_block(
    ticker: str,
    fiscal_year: Optional[int],
    state: Dict[str, Any],
    serving_report: Dict[str, Any],
    ingestion_receipt: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Gate D — assemble the `_validation` block for the serving JSON.

    Args:
      ticker:           upper-case
      fiscal_year:      latest FY for the ticker
      state:            LangGraph state at lead-write time (for Gate B receipt)
      serving_report:   the assembled report dict (final_assembly checks
                        scan its narrative + scenarios)
      ingestion_receipt: Gate A receipt loaded from DuckDB by caller.
                        Pass {} when not available; we'll show as skipped.

    No new FMP calls. Returns the `_validation` block; caller stamps it
    onto serving_report.
    """
    ing = ingestion_receipt or {"status": "skipped", "skip_reason": "not_loaded_from_db"}
    calc = state.get("_calc_validation") or {"status": "skipped", "skip_reason": "not_run"}

    # Final-assembly checks
    fidelity = _check_narrative_fidelity(serving_report)
    monotonicity = _check_scenario_monotonicity(serving_report)

    # final_assembly overall status: blocking_drift if any narrative-fidelity
    # violation; drift if monotonicity broken; otherwise validated.
    if fidelity["violations"] > 0:
        final_status = "blocking_drift"
    elif monotonicity.get("ok") is False:
        final_status = "drift"
    else:
        final_status = "validated"

    return {
        "schema_version": 2,
        "ticker":         ticker,
        "fiscal_year":    fiscal_year,
        "stamped_at":     _now_iso(),
        "ingestion":      ing,
        "calc":           calc,
        "final_assembly": {
            "status":            final_status,
            "checks": {
                "numeric_fidelity":       fidelity,
                "scenario_monotonicity":  monotonicity,
            },
        },
        "summary": {
            "ingestion_status":      ing.get("status", "n/a"),
            "calc_status":           calc.get("status", "n/a"),
            "final_assembly_status": final_status,
            "any_blocking": (
                ing.get("status") == "blocking_drift"
                or calc.get("status") == "blocking_drift"
                or final_status == "blocking_drift"
            ),
        },
    }


__all__ = [
    "IngestionValidationFailure",
    "validate_ingestion_record",
    "validate_calc_output",
    "build_receipt_block",
]
