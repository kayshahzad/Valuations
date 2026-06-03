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
    # byte_perfect_required: 0.5%/0.5% — collapsed bands for cases where
    # both sides MUST agree (Gate A.TTM cross-checks our quarterly-sum
    # against FMP's pre-computed TTM; both arms draw from the same
    # quarterly facts so any drift > 0.5% is a P0 bug, not a flag).
    "byte_perfect_required": (0.005, 0.005),
}


def _classify_drift(drift_pct: Optional[float], tier: str) -> str:
    """Returns 'byte_perfect' | 'acceptable' | 'structural_drift' | 'n_a'.

    `drift_pct` is the signed fraction. Compare against `tier`'s bands.
    `sanity_only` tier always returns byte_perfect (drift bands are ∞);
    only useful for surfacing values, never blocking.
    """
    if drift_pct is None:
        return "n_a"
    if tier == "sanity_only":
        return "byte_perfect"
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
    # ROIC + ROE: definitional tier because invested-capital + tax-rate
    # definitions legitimately drift between sources. Non-blocking; the
    # leverage is isolating whether a numerator or denominator bug is
    # the source of any flagged composite drift.
    ("roic",                  "ratios",         ["returnOnInvestedCapital", "roic"],     "definitional", False, False),
    ("roe",                   "ratios",         ["returnOnEquity"],                      "definitional", False, False),
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
    "roic":                ("derived_ROIC", 1.0),               # both sides decimal, no scale
    "roe":                 ("derived_ROE",  1.0),
}


# ── Gate A — as-reported lane (XBRL tag-level cross-check) ─────────────
#
# Compares our raw_X values (pulled directly from SEC EDGAR via our own
# ingest) against the same XBRL tag values FMP saw on /income-statement-
# as-reported. If both sides agree, we know our SEC ingest and FMP's
# saw the same filing — a tag-mapping bug on either side would surface
# here before it can fool the higher-level checks.
#
# (our_db_col, [xbrl_tag fallback list], tier)
# Tier `strict` for these — they're literally the filed numbers, no
# normalization gap. Drift here is a high-confidence signal of a bug.
_GATE_A_AS_REPORTED_FIELDS: List[Tuple[str, str, List[str], str]] = [
    ("revenue_as_reported",      "clean_Revenue",
     ["Revenues",
      "RevenueFromContractWithCustomerExcludingAssessedTax",
      "SalesRevenueNet"],
     "strict"),
    ("net_income_as_reported",   "raw_NetIncome",
     ["NetIncomeLoss"],
     "strict"),
    ("total_assets_as_reported", "raw_TotalAssets",
     ["Assets"],
     "strict"),
]


def _add_as_reported_checks(
    record: Any,
    ticker: str,
    fy: int,
    fields: Dict[str, Dict[str, Any]],
) -> None:
    """Mutate `fields` to append the XBRL-tag-level cross-checks.
    Best-effort: if the as-reported endpoint is unavailable or the FY
    record can't be located, fields are stamped n_a — never blocks.
    Skipped silently when no FMP key is configured (already gated
    upstream)."""
    try:
        from aletheia.data import fmp_client
        ar_records = fmp_client.fetch_income_statement_as_reported(ticker)
    except Exception:
        ar_records = None

    ar_fy: Dict[str, Any] = {}
    if ar_records:
        match = fmp_client.get_for_fiscal_year(ar_records, fy)
        if isinstance(match, dict):
            ar_fy = match

    for label, our_col, xbrl_tags, tier in _GATE_A_AS_REPORTED_FIELDS:
        ours = record.get(our_col) if hasattr(record, "get") else None
        try:
            ours = float(ours) if ours is not None else None
        except (TypeError, ValueError):
            ours = None

        # Try each XBRL tag in fallback order — first non-None wins.
        fmp_val: Optional[float] = None
        tag_used: Optional[str] = None
        for tag in xbrl_tags:
            v = ar_fy.get(tag)
            if v is None:
                continue
            try:
                fmp_val = float(v)
                tag_used = tag
                break
            except (TypeError, ValueError):
                continue

        _, drift = _drift_label(ours, fmp_val)
        fields[label] = {
            "ours":             ours,
            "fmp":              fmp_val,
            "drift_pct":        drift,
            "tier":             tier,
            "blocking":         False,  # informational; primary check owns the gate
            "status":           _classify_drift(drift, tier),
            "source_endpoint":  "income_as_reported",
            "fmp_key_resolved": tag_used or xbrl_tags[0],
        }


def _add_ev_derived_checks(
    record: Any,
    fmp_data: Dict[str, Any],
    fields: Dict[str, Dict[str, Any]],
) -> None:
    """Mutate `fields` to append two derived second-source checks pulled
    from FMP's /enterprise-values endpoint:

      `net_debt_via_ev_identity` — fmp.enterpriseValue − fmp.marketCap
        gives an implied NetDebt independent of /key-metrics.netDebt;
        compared against our derived_NetDebt.

      `shares_outstanding_eop` — fmp.numberOfShares (end-of-period)
        compared against raw_SharesDiluted (weighted-average). Drift
        is expected and tier=definitional reflects that.

    Both fields are non-blocking (False). When EV data is missing they
    record status=n_a so callers can see why."""
    ev = (fmp_data or {}).get("enterprise_values") or {}

    # NetDebt via EV identity
    fmp_ev = ev.get("enterpriseValue")
    fmp_mc = ev.get("marketCapitalization") or ev.get("marketCap")
    fmp_implied_nd: Optional[float] = None
    if fmp_ev is not None and fmp_mc is not None:
        try:
            fmp_implied_nd = float(fmp_ev) - float(fmp_mc)
        except (TypeError, ValueError):
            fmp_implied_nd = None
    ours_nd = _our_value_for_gate(record, "net_debt")
    _, d_nd = _drift_label(ours_nd, fmp_implied_nd)
    fields["net_debt_via_ev_identity"] = {
        "ours":            ours_nd,
        "fmp":             fmp_implied_nd,
        "drift_pct":       d_nd,
        "tier":            "standard",
        "blocking":        False,
        "status":          _classify_drift(d_nd, "standard"),
        "source_endpoint": "enterprise_values",
        "fmp_key_resolved": "enterpriseValue-marketCapitalization",
    }

    # End-of-period shares vs our weighted-average diluted
    fmp_shares_eop = ev.get("numberOfShares")
    ours_shares = _our_value_for_gate(record, "shares_diluted")
    _, d_sh = _drift_label(ours_shares, fmp_shares_eop)
    fields["shares_outstanding_eop"] = {
        "ours":            ours_shares,
        "fmp":             fmp_shares_eop,
        "drift_pct":       d_sh,
        "tier":            "definitional",
        "blocking":        False,
        "status":          _classify_drift(d_sh, "definitional"),
        "source_endpoint": "enterprise_values",
        "fmp_key_resolved": "numberOfShares",
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
        ev  = fmp_client.fetch_enterprise_values(ticker)
    except Exception as exc:
        return None, f"fmp_network_error:{type(exc).__name__}"

    # `ev` is allowed to be None on legacy plans; missing EV degrades the
    # EV-identity / shares-EOP checks to n_a but doesn't skip Gate A.
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
    ev_fy  = fmp_client.get_for_fiscal_year(ev, fy) if ev else None
    if not (inc_fy and bs_fy and cf_fy):
        return None, f"fmp_missing_fy{fy}"

    # Currency check: foreign filers (ASML EUR, TSM TWD) skipped pending
    # Phase 6 FX support.
    fmp_ccy = (inc_fy.get("reportedCurrency") or "").upper()
    if fmp_ccy and fmp_ccy != "USD":
        return None, f"fmp_currency_mismatch:{fmp_ccy}"

    return ({
        "income":            inc_fy,
        "balance":           bs_fy,
        "cashflow":          cf_fy,
        "key_metrics":       km_fy or {},
        "ratios":            rt_fy or {},
        "enterprise_values": ev_fy or {},
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
      is_latest_fy: when True, blocking-tier drift escalates to
        `blocking_drift` (caller raises IngestionValidationFailure).
        When False, the same drift is downgraded to `drift` —
        recorded for visibility but never blocks ingestion of a
        historical row that may have been re-normalized by FMP.

    Returns: result dict per the §7 schema.

    Does NOT raise on its own. The caller checks status and decides
    whether to discard the record (raise IngestionValidationFailure).
    """
    base = {
        "status":                "validated",
        "skip_reason":           None,
        "fiscal_year_validated": fiscal_year,
        "is_latest_fy":          is_latest_fy,
        "fetched_at":            _now_iso(),
        "fields":                {},
        "blocking_fields":       [],
    }

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

        # FMP self-consistency guard for EBITDA. EBITDA = EBIT + D&A, so
        # FMP's own ``ebitda`` can never be below its ``operatingIncome``
        # (D&A ≥ 0). When it is, FMP mis-tagged the income statement — seen
        # on CELH FY2025 (Alani Nu acquisition year): FMP ebitda=$203M <
        # FMP operatingIncome=$469M, while our EBITDA=$498M=EBIT+D&A is
        # internally consistent. FMP's ebitda is not a trustworthy blocking
        # reference here, so demote the drift to non-blocking — an FMP data
        # bug must not drop an internally-consistent record of ours.
        fmp_unreliable: Optional[str] = None
        if field_name == "ebitda" and fmp is not None:
            fmp_opinc = _fmp_value_for_gate(
                fmp_data, "income", ["operatingIncome"], False)
            if fmp_opinc is not None and fmp < fmp_opinc - 1.0:
                is_blocking_violation = False
                fmp_unreliable = (
                    f"FMP ebitda={fmp:.0f} < FMP operatingIncome={fmp_opinc:.0f} "
                    "(EBITDA<EBIT impossible); FMP value untrustworthy — "
                    "drift recorded but not blocking"
                )

        fields[field_name] = {
            "ours":            ours,
            "fmp":             fmp,
            "drift_pct":       drift_pct,
            "tier":            tier,
            "blocking":        blocking,
            "status":          status,
            "source_endpoint": endpoint,
            "fmp_key_resolved": fmp_keys[0],
            "fmp_unreliable":  fmp_unreliable,
        }
        if is_violation:
            n_drift += 1
        if is_blocking_violation:
            n_blocking += 1
            blocking_fields.append(field_name)

    # ── EV-derived second-source checks ─────────────────────────────────
    # These cross-check our values against an independent FMP endpoint
    # (/enterprise-values), so a regression that fooled the primary gate
    # because both sides drew from the same FMP source still gets caught.
    _add_ev_derived_checks(record, fmp_data, fields)

    # ── As-reported XBRL-tag-level cross-check ──────────────────────────
    # Strict-tier comparison of our raw_X values against the same XBRL
    # tags FMP saw on /income-statement-as-reported. Catches tag-mapping
    # bugs that would slip past FMP's normalized fields.
    _add_as_reported_checks(record, ticker, fiscal_year, fields)

    # Historical FYs: drift is recorded but never blocks ingestion.
    if n_blocking > 0 and is_latest_fy:
        result_status = "blocking_drift"
    elif n_blocking > 0 or n_drift > 0:
        result_status = "drift"
    else:
        result_status = "validated"

    return {
        **base,
        "status":          result_status,
        "fields":          fields,
        "blocking_fields": blocking_fields if is_latest_fy else [],
    }


# ────────────────────────────────────────────────────────────────────────
# Gate A.TTM — byte-perfect TTM cross-check (Phase Q-4)
# ────────────────────────────────────────────────────────────────────────
#
# Compares our quarterly-summed TTM (`derive_ttm_from_fmp` output) against
# FMP's pre-computed TTM blob (`/key-metrics-ttm` + `/ratios-ttm`). Both
# arms draw from the same underlying quarterly facts, so any drift > 0.5%
# on revenue or NI is a P0 bug — either FMP's quarterlies don't sum to
# their own TTM, or our summation is wrong. There's no third
# possibility, so the tier is `byte_perfect_required` (collapsed bands).
#
# Per-share TTM fields (revenuePerShareTTM etc.) are converted to
# absolute values using FMP's reported diluted shares so units align.
#
# Returns the same result shape as Gate A so downstream consumers
# (Gate D receipt, UI glyph) treat it uniformly.

# (label, our_path, fmp_source, fmp_key, tier, p0, scale)
# fmp_source = "km" → fetched key-metrics-ttm dict
# fmp_source = "ratios" → fetched ratios-ttm dict
# fmp_source = "ev_implied" → derived from km dict via EV-multiple identity
# `scale` is multiplied into our value before comparing — used when our
# field is stored in different units than FMP's (e.g. ours percent,
# FMP decimal).
#
# Live-data finding (Phase Q-6 hardening): FMP's /key-metrics-ttm does
# NOT expose absolute revenue/NI/FCF (no `revenuePerShareTTM`-style
# fields). Only `enterpriseValueTTM` + the `evToXTTM` ratios are
# absolute; everything else is per-ratio. So the absolute-flow lanes
# now use EV-implied math: revenue_ttm = enterpriseValueTTM / evToSalesTTM.
# Tier drops from byte_perfect_required to standard because EV-implied
# arithmetic legitimately drifts from a quarterly-sum (the EV used for
# the ratio is a different snapshot). The byte-perfect gate on absolute
# flows will return when SEC quarterly parsing ships (Phase Q-2) and
# we have a true second source.
#
# ROIC + ROE: live keys revealed FMP exposes these on /key-metrics-ttm,
# NOT /ratios-ttm. EBIT margin stays on /ratios-ttm but needs a 0.01
# scale (ours stored as percent, FMP as decimal).
_GATE_A_TTM_FIELDS: List[Tuple[str, str, str, str, str, bool, float]] = [
    # Absolute flow items via EV-implied math — second-source check
    # against FMP-internal consistency (EV × ratio reconstructions).
    ("revenue_ttm",     "raw.Revenue",     "ev_implied", "evToSalesTTM",          "standard", False, 1.0),
    ("ebitda_ttm",      "derived.EBITDA",  "ev_implied", "evToEBITDATTM",         "standard", False, 1.0),
    ("fcf_ttm",         "clean.FCF",       "ev_implied", "evToFreeCashFlowTTM",   "standard", False, 1.0),
    # Ratios: definitional tier — formulas legitimately differ (effective
    # tax rate, invested-capital definition). Stamped for visibility.
    ("roic_ttm",        "derived.ROIC",    "km",     "returnOnInvestedCapitalTTM", "definitional", False, 1.0),
    ("roe_ttm",         "derived.ROE",     "km",     "returnOnEquityTTM",          "definitional", False, 1.0),
    # EBIT margin: ours stored as percent, FMP as decimal → scale=0.01.
    ("ebit_margin_ttm", "derived.EBIT_Margin_Pct", "ratios", "operatingProfitMarginTTM", "definitional", False, 0.01),
]


def _resolve_record_path(record: Any, path: str) -> Optional[float]:
    """Walk `record.raw.X`, `record.clean.X`, or `record.derived.X`."""
    if record is None:
        return None
    bucket, _, key = path.partition(".")
    if not key:
        return None
    container = getattr(record, bucket, None)
    if container is None:
        return None
    val = container.get(key) if hasattr(container, "get") else None
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def validate_ttm_record(
    ticker: str,
    record: Any,
    *,
    fmp_key_metrics_ttm: Optional[Dict[str, Any]] = None,
    fmp_ratios_ttm: Optional[Dict[str, Any]] = None,
    fmp_ev_latest_quarter: Optional[Dict[str, Any]] = None,
    fmp_income_as_reported_quarter: Optional[Dict[str, Any]] = None,
    latest_quarter_income: Optional[Dict[str, Any]] = None,
    sec_quarterly_revenue: Optional[List[Dict[str, Any]]] = None,
    sec_quarterly_net_income: Optional[List[Dict[str, Any]]] = None,
    fmp_quarterly_income: Optional[List[Dict[str, Any]]] = None,
    prior_fy_net_debt: Optional[float] = None,
    prior_fy_total_assets: Optional[float] = None,
) -> Dict[str, Any]:
    """Gate A.TTM — cross-check our derived TTM against FMP's TTM blob
    plus two second-source lanes added in Phase Q-6 full:

      EV-identity NetDebt: pulls FMP's latest quarterly /enterprise-values
        (independent of /key-metrics.netDebt) and compares the implied
        NetDebt = EV − marketCap against our TTM record's derived NetDebt.

      As-reported XBRL: pulls /income-statement-as-reported?period=quarter
        for the latest contributing quarter and verifies our quarterly
        source data (latest_quarter_income) matches the raw filed XBRL
        tags. Catches tag-mapping regressions on the freshest filing.

    `record` is the CleanedRecord returned by `derive_ttm_from_fmp`.
    `fmp_*` blobs come from the same TTMDerivationResult / a parallel
    fetch in the ingestion entrypoint.

    Result status:
      - validated:      all fields within tier band
      - drift:          one or more non-P0 fields outside band
      - blocking_drift: a P0 field outside the byte_perfect_required
                        band — indicates FMP quarterlies don't sum to
                        their own TTM, or our summation is wrong
      - skipped:        FMP TTM blob unavailable
    """
    # Carry forensic fields from the derivation's receipt: ttm_source
    # (FMP vs SEC), reported_currency (USD/EUR/TWD/etc.), and
    # fx_converted (True for foreign filers post-FX). Without this the
    # receipt would silently lose currency provenance when validate
    # rebuilds it.
    src_receipt = getattr(record, "fmp_validation", {}) if record else {}
    base = {
        "ticker":            ticker.upper(),
        "status":            "validated",
        "skip_reason":       None,
        "ttm_source":        src_receipt.get("ttm_source"),
        "reported_currency": src_receipt.get("reported_currency"),
        "fx_converted":      src_receipt.get("fx_converted"),
        "fetched_at":        _now_iso(),
        "fields":            {},
        "blocking_fields":   [],
    }

    if record is None:
        return {**base, "status": "skipped", "skip_reason": "no_ttm_record"}

    if not (fmp_key_metrics_ttm or fmp_ratios_ttm):
        return {**base, "status": "skipped", "skip_reason": "fmp_ttm_blob_unavailable"}

    # `enterpriseValueTTM` is needed for the ev_implied lane; pull once
    # at the top so we don't re-extract per field.
    ev_ttm = None
    if fmp_key_metrics_ttm:
        try:
            ev_ttm = float(fmp_key_metrics_ttm.get("enterpriseValueTTM"))
        except (TypeError, ValueError):
            ev_ttm = None

    fields: Dict[str, Dict[str, Any]] = {}
    blocking_fields: List[str] = []
    n_drift = 0
    n_p0_breach = 0

    for label, our_path, fmp_source, fmp_key, tier, p0, scale in _GATE_A_TTM_FIELDS:
        ours_raw = _resolve_record_path(record, our_path)
        ours = ours_raw * scale if (ours_raw is not None) else None

        # Pull FMP side
        if fmp_source == "km":
            blob = fmp_key_metrics_ttm or {}
            endpoint = "key_metrics_ttm"
        elif fmp_source == "ratios":
            blob = fmp_ratios_ttm or {}
            endpoint = "ratios_ttm"
        elif fmp_source == "ev_implied":
            blob = fmp_key_metrics_ttm or {}
            endpoint = "key_metrics_ttm:ev_implied"
        else:
            blob = {}
            endpoint = "unknown"

        fmp_raw_val = blob.get(fmp_key)
        try:
            fmp_raw_val = float(fmp_raw_val) if fmp_raw_val is not None else None
        except (TypeError, ValueError):
            fmp_raw_val = None

        # Convert FMP side to absolute units when source requires it.
        # `ev_implied`: divide enterpriseValueTTM by the ratio (e.g.
        # evToSalesTTM = EV / Revenue, so Revenue = EV / evToSalesTTM).
        if fmp_source == "ev_implied":
            if fmp_raw_val and ev_ttm and fmp_raw_val != 0:
                fmp_value: Optional[float] = ev_ttm / fmp_raw_val
            else:
                fmp_value = None
        else:
            fmp_value = fmp_raw_val

        _, drift_pct = _drift_label(ours, fmp_value)
        status = _classify_drift(drift_pct, tier)
        is_violation = (status == "structural_drift")
        is_p0_breach = (p0 and is_violation)

        fields[label] = {
            "ours":             ours,
            "fmp":              fmp_value,
            "drift_pct":        drift_pct,
            "tier":             tier,
            "p0":               p0,
            "status":           status,
            "source_endpoint":  endpoint,
            "fmp_key_resolved": fmp_key,
        }
        if is_violation:
            n_drift += 1
        if is_p0_breach:
            n_p0_breach += 1
            blocking_fields.append(label)

    # ── Phase Q-6 full: second-source TTM lanes ────────────────────────
    # Both lanes are non-P0 by design (the primary check at /key-
    # metrics.netDebt + Gate A.TTM revenue/NI fields owns the gate);
    # these append fields for forensics and add to drift count only.
    nd_drift = _add_ttm_ev_identity_check(
        record, fmp_ev_latest_quarter, fields,
    )
    ar_drift = _add_ttm_as_reported_check(
        latest_quarter_income, fmp_income_as_reported_quarter, fields,
    )
    # Phase Q-6 multi-year quarterly: when SEC is the primary source,
    # cross-check each contributing quarter's standalone value against
    # FMP's `period=quarter` records. Catches per-quarter drifts that
    # would offset and disappear in the TTM rollup. Non-blocking; the
    # primary lane on revenue/NI owns the gate.
    qc_drift = _add_ttm_quarterly_consistency_check(
        sec_quarterly_revenue, sec_quarterly_net_income,
        fmp_quarterly_income, fields,
    )
    # Period-over-period balance-sheet sanity check. Catches cases like
    # ABT's $20B Q1 2026 debt issuance that BOTH our cleaning engine
    # AND FMP correctly captured but neither flag would catch on its
    # own (because they cross-check sources, not period-over-period
    # changes within a single source). Non-blocking — legitimate M&A
    # trips this intentionally; the analyst should cross-check against
    # an 8-K but the data isn't wrong.
    pop_drift = _add_ttm_period_over_period_check(
        record, prior_fy_net_debt, prior_fy_total_assets, fields,
    )
    n_drift += nd_drift + ar_drift + qc_drift + pop_drift

    if n_p0_breach > 0:
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


# ── Phase Q-6 full helpers ─────────────────────────────────────────────

def _add_ttm_period_over_period_check(
    record: Any,
    prior_fy_net_debt: Optional[float],
    prior_fy_total_assets: Optional[float],
    fields: Dict[str, Dict[str, Any]],
) -> int:
    """Period-over-period balance-sheet sanity check. Flags as
    `structural_drift` when |ΔNetDebt|/prior_FY_TotalAssets > 20% — a
    threshold tuned to catch material corporate actions (large debt
    issuance / refinance / paydown / acquisition funding) that warrant
    analyst review. 5–20% is the `acceptable` (monitor) band.

    Non-blocking by design. Legitimate M&A trips this intentionally;
    the analyst should cross-check against an 8-K. The data isn't
    wrong — the SHAPE of the change is unusual and worth a manual
    look. ABT's Q1 2026 was the case study ($20B debt issuance,
    25% of total assets — would have fired the warning).

    Returns the drift count contribution (0 or 1)."""
    label = "period_over_period_netdebt_jump"
    ttm_nd = _resolve_record_path(record, "derived.NetDebt")

    if (ttm_nd is None or prior_fy_net_debt is None
            or prior_fy_total_assets is None
            or prior_fy_total_assets <= 0):
        fields[label] = {
            "ours":             ttm_nd,
            "fmp":              prior_fy_net_debt,
            "drift_pct":        None,
            "tier":             "definitional",
            "p0":               False,
            "status":           "n_a",
            "source_endpoint":  "internal:period_over_period",
            "fmp_key_resolved": "prior_fy_net_debt|total_assets",
        }
        return 0

    delta = float(ttm_nd) - float(prior_fy_net_debt)
    delta_pct_assets = abs(delta) / float(prior_fy_total_assets)
    # Anything above 20% of total assets is a structural change worth
    # flagging (M&A, large issuance/refi). Below 5% is routine; 5-20% is
    # "monitor." Threshold tuned on ABT Q1 2026 ($20B issuance ≈ 23% of
    # total assets) — we want that to fire.
    status = (
        "structural_drift" if delta_pct_assets > 0.20
        else ("acceptable" if delta_pct_assets > 0.05 else "byte_perfect")
    )
    fields[label] = {
        "ours":             ttm_nd,
        "fmp":              prior_fy_net_debt,   # the comparison anchor
        "drift_pct":        delta / float(prior_fy_total_assets),
        "tier":             "definitional",
        "p0":               False,
        "status":           status,
        "source_endpoint":  "internal:period_over_period",
        "fmp_key_resolved": "ΔNetDebt / prior_FY_TotalAssets",
    }
    return 1 if status == "structural_drift" else 0


def _add_ttm_ev_identity_check(
    record: Any,
    fmp_ev: Optional[Dict[str, Any]],
    fields: Dict[str, Dict[str, Any]],
) -> int:
    """Append `net_debt_ttm_via_ev_identity` field. Returns the drift
    count contribution. Non-blocking by design — primary check at
    /key-metrics.netDebt owns the gate; this lane is a second-source
    regression detector."""
    ours_nd = _resolve_record_path(record, "derived.NetDebt")
    if not fmp_ev:
        fields["net_debt_ttm_via_ev_identity"] = {
            "ours":             ours_nd,
            "fmp":              None,
            "drift_pct":        None,
            "tier":             "standard",
            "p0":               False,
            "status":           "n_a",
            "source_endpoint":  "enterprise_values_quarter",
            "fmp_key_resolved": "enterpriseValue-marketCapitalization",
        }
        return 0

    ev = fmp_ev.get("enterpriseValue")
    mc = fmp_ev.get("marketCapitalization") or fmp_ev.get("marketCap")
    fmp_implied_nd: Optional[float] = None
    if ev is not None and mc is not None:
        try:
            fmp_implied_nd = float(ev) - float(mc)
        except (TypeError, ValueError):
            fmp_implied_nd = None

    _, drift = _drift_label(ours_nd, fmp_implied_nd)
    status = _classify_drift(drift, "standard")
    fields["net_debt_ttm_via_ev_identity"] = {
        "ours":             ours_nd,
        "fmp":              fmp_implied_nd,
        "drift_pct":        drift,
        "tier":             "standard",
        "p0":               False,
        "status":           status,
        "source_endpoint":  "enterprise_values_quarter",
        "fmp_key_resolved": "enterpriseValue-marketCapitalization",
    }
    return 1 if status == "structural_drift" else 0


# (label, our_key_in_latest_quarter_income, xbrl_tags fallback list, tier)
# Live-data finding (Phase Q-6 hardening): FMP returns the as-reported
# tags under `record["data"]` (nested), keyed in **lowercase** (e.g.
# `revenuefromcontractwithcustomerexcludingassessedtax`, NOT the
# CamelCase XBRL concept). The lookup helper handles both shapes.
_TTM_AS_REPORTED_FIELDS: List[Tuple[str, str, List[str], str]] = [
    ("revenue_latest_quarter_as_reported", "revenue",
     ["RevenueFromContractWithCustomerExcludingAssessedTax",
      "Revenues",
      "SalesRevenueNet"],
     "strict"),
    ("net_income_latest_quarter_as_reported", "netIncome",
     ["NetIncomeLoss"],
     "strict"),
]


def _lookup_as_reported_tag(
    fmp_record: Dict[str, Any], xbrl_tag: str,
) -> Optional[float]:
    """FMP /income-statement-as-reported returns tags under record['data']
    in lowercase. Try both nested-lower and flat-Camel for backward
    compatibility with future endpoint shape changes."""
    data_block = fmp_record.get("data") if isinstance(fmp_record, dict) else None
    candidates = [xbrl_tag, xbrl_tag.lower()]
    for blob in (data_block, fmp_record):
        if not isinstance(blob, dict):
            continue
        for key in candidates:
            v = blob.get(key)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _add_ttm_quarterly_consistency_check(
    sec_quarterly_revenue:    Optional[List[Dict[str, Any]]],
    sec_quarterly_net_income: Optional[List[Dict[str, Any]]],
    fmp_quarterly_income:     Optional[List[Dict[str, Any]]],
    fields: Dict[str, Dict[str, Any]],
) -> int:
    """Phase Q-6: per-quarter cross-check between SEC standalone
    quarters (derived from cumulative XBRL via subtraction) and FMP's
    `period=quarter` income statements. Match by period_end_date.

    Stamps up to 8 fields:
      revenue_q_<period_end>     (per quarter, max 4)
      net_income_q_<period_end>  (per quarter, max 4)

    Standard tier; non-blocking.  When SEC is FMP-primary (no SEC
    standalones available), all fields stamp n_a — caller can skip
    rendering this lane in UIs that want to hide noise."""
    n_drift = 0
    have_sec = bool(sec_quarterly_revenue or sec_quarterly_net_income)
    have_fmp = bool(fmp_quarterly_income)

    if not (have_sec and have_fmp):
        # Stamp a sentinel summary so the lane is visible as n_a in
        # the receipt rather than silently absent.
        fields["per_quarter_consistency"] = {
            "ours":             None,
            "fmp":              None,
            "drift_pct":        None,
            "tier":             "standard",
            "p0":               False,
            "status":           "n_a",
            "source_endpoint":  "income_statement_quarter:vs_sec_standalones",
            "fmp_key_resolved": None,
        }
        return 0

    fmp_by_end: Dict[str, Dict[str, Any]] = {}
    for rec in fmp_quarterly_income or []:
        end = (rec.get("date") or "")[:10]
        if end:
            fmp_by_end[end] = rec

    def _emit(label: str, ours_v: Any, fmp_v: Any, end: str) -> int:
        try:
            ours_f = float(ours_v) if ours_v is not None else None
        except (TypeError, ValueError):
            ours_f = None
        try:
            fmp_f = float(fmp_v) if fmp_v is not None else None
        except (TypeError, ValueError):
            fmp_f = None
        _, drift = _drift_label(ours_f, fmp_f)
        status = _classify_drift(drift, "standard")
        fields[label] = {
            "ours":             ours_f,
            "fmp":              fmp_f,
            "drift_pct":        drift,
            "tier":             "standard",
            "p0":               False,
            "status":           status,
            "source_endpoint":  "income_statement_quarter",
            "fmp_key_resolved": f"end={end}",
        }
        return 1 if status == "structural_drift" else 0

    # Cap to the 4 most recent SEC standalones per metric — anything
    # older isn't part of the active TTM window.
    for metric_key, fmp_key, sec_list in (
        ("revenue",     "revenue",   sec_quarterly_revenue or []),
        ("net_income",  "netIncome", sec_quarterly_net_income or []),
    ):
        for q in sec_list[:4]:
            end = (q.get("end") or "")[:10]
            sec_v = q.get("val_standalone")
            fmp_rec = fmp_by_end.get(end)
            if sec_v is None or fmp_rec is None:
                # Skip silently — gap is informational, not an error
                continue
            n_drift += _emit(
                f"{metric_key}_q_{end}", sec_v, fmp_rec.get(fmp_key), end,
            )
    return n_drift


def _add_ttm_as_reported_check(
    latest_quarter_income: Optional[Dict[str, Any]],
    fmp_as_reported: Optional[Dict[str, Any]],
    fields: Dict[str, Dict[str, Any]],
) -> int:
    """Append as-reported XBRL fields for the latest contributing
    quarter. Returns the drift count contribution. Strict tier; non-
    blocking — 10-Q is unaudited and FMP normalization may legit
    drift, so a flag here is informational."""
    n_drift = 0
    if not latest_quarter_income or not fmp_as_reported:
        for label, _, _, tier in _TTM_AS_REPORTED_FIELDS:
            fields[label] = {
                "ours":             None,
                "fmp":              None,
                "drift_pct":        None,
                "tier":             tier,
                "p0":               False,
                "status":           "n_a",
                "source_endpoint":  "income_as_reported_quarter",
                "fmp_key_resolved": None,
            }
        return 0

    for label, our_key, xbrl_tags, tier in _TTM_AS_REPORTED_FIELDS:
        ours_v = latest_quarter_income.get(our_key)
        try:
            ours_v = float(ours_v) if ours_v is not None else None
        except (TypeError, ValueError):
            ours_v = None

        fmp_v: Optional[float] = None
        tag_used: Optional[str] = None
        for tag in xbrl_tags:
            v = _lookup_as_reported_tag(fmp_as_reported, tag)
            if v is not None:
                fmp_v = v
                tag_used = tag
                break

        _, drift = _drift_label(ours_v, fmp_v)
        status = _classify_drift(drift, tier)
        fields[label] = {
            "ours":             ours_v,
            "fmp":              fmp_v,
            "drift_pct":        drift,
            "tier":             tier,
            "p0":               False,
            "status":           status,
            "source_endpoint":  "income_as_reported_quarter",
            "fmp_key_resolved": tag_used or xbrl_tags[0],
        }
        if status == "structural_drift":
            n_drift += 1

    return n_drift


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
    # Beta tier `sanity_only`: drift checks always pass; the value is
    # stamped for forensic visibility but never blocks. Rationale: FMP's
    # /profile.beta is itself unreliable on defensive names (audit found
    # JNJ at 0.26, MRK at 0.20 vs Bloomberg ~0.5 — likely the same
    # COVID-anomaly regression artifact our raw beta hit pre-sector-floor).
    # Gate B's job for beta is to surface our value alongside FMP's; the
    # downstream WACC chain is already validated via current_price +
    # market_cap which ARE truth values.
    ("beta",                       ["dcf.beta", "beta"],
                                                   "profile",     ["beta"],                                       "sanity_only",  False),
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
    # Second-source price + market-cap checks. /profile is FMP's "live"
    # quote; /historical-price-eod and /historical-market-capitalization
    # give us a daily series — comparing against the most recent close
    # catches stale /profile quotes and lets us bound the actual snapshot
    # drift. Standard tier; non-blocking (price-timing always has some
    # drift even between two FMP sources).
    ("price_eod_recent",           ["dcf.current_price"],
                                                   "price_eod",   ["close"],                                      "standard",     False),
    ("market_cap_eod_recent",      ["dcf.market_cap"],
                                                   "mcap_eod",    ["marketCap"],                                  "standard",     False),
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
    """Fetch live `/profile` plus cached `/key-metrics`, `/ratios`, and
    the most-recent EOD price + market-cap records (second-source check
    on the live /profile quote)."""
    from aletheia.data import fmp_client
    if not fmp_client.has_api_key():
        return None, "fmp_api_key_not_configured"

    try:
        profile = fmp_client.fetch_profile(ticker)
        km      = fmp_client.fetch_key_metrics(ticker)
        ratios  = fmp_client.fetch_ratios(ticker)
        prices  = fmp_client.fetch_historical_prices(ticker)
        mcaps   = fmp_client.fetch_historical_market_cap(ticker)
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

    # Most recent EOD record from each daily series. Series come back
    # most-recent-first; first element is the closest close to today.
    price_rec = (prices[0] if prices else {}) or {}
    mcap_rec  = (mcaps[0] if mcaps else {}) or {}

    return ({
        "profile":     profile,
        "key_metrics": km_rec,
        "ratios":      rt_rec,
        "price_eod":   price_rec,
        "mcap_eod":    mcap_rec,
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
