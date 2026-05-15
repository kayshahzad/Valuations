"""Schema-contract assertion for CleanedRecord persistence.

The enforcing boundary the framework hangs on. Before any record is
written to DuckDB, this function walks the full set of schema invariants
(signs + arithmetic identities + ranges) and refuses to persist records
that violate them. Violations surface as ingest errors, NOT as silently-
propagated calc-layer errors three call sites downstream.

Two record shapes:

  FY  — built by ``cleaning_engine`` from annual 10-K. Has the full
        normalization stack: clean_NormalizedEBIT, clean_NOPAT,
        clean_CashTaxRate. Identities must hold across all of them.

  TTM — built by ``derive_ttm_from_sec`` or ``derive_ttm_from_fmp``.
        Does NOT (yet) populate the FY-only normalization fields
        (A4/A5 in the anomaly catalog). The contract is correspondingly
        relaxed; the schema-required set is smaller.

Mode behavior (via ``ALETHEIA_GUARD_MODE``):

  off    : function is a no-op. Default during initial rollout.
  shadow : violations logged; ``persist_ok`` returned True regardless.
  soft   : violations logged + surfaced in receipt; persist_ok True.
  hard   : violations cause ``CalculationError`` to be raised; the
           caller must catch it and decide whether to skip persist.

Returns:
  ``(persist_ok: bool, violations: list[dict])``

  In off/shadow/soft → persist_ok is always True (the assertion is
  observational, not blocking).
  In hard → raises before returning; the caller never sees False.
  An explicit ``False`` value is reserved for callers that want to
  enforce themselves without going to hard mode globally (e.g., a
  legacy-data backfill that quarantines violators rather than raising).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ._errors import CalculationError
from ._guards import (
    _flag_unusual,
    _guard_mode,
    _require_consistent,
    _require_finite,
    _require_range,
    _require_strict_nonneg,
)
from ._sign_conventions import IDENTITY_TOLERANCES, RANGE_BOUNDS
from ._overrides import OVERRIDES


def _collect_violation(check_callable, *args, **kwargs):
    """Run a guard check at force-hard semantics to collect any
    violation regardless of the system's actual mode. Returns the
    CalculationError instance if the check failed, None otherwise.

    Why: in shadow/soft mode, the framework primitives log but don't
    raise, so a naive try/except in the schema-contract function never
    sees the violation and can't collect it for persistence. Forcing
    hard-mode semantics locally captures every violation; the caller
    decides what to do with it (log + persist + optionally re-raise).

    The hard-mode ERROR log emitted by the primitive is suppressed
    during the collection call so callers see exactly one structured
    log entry per violation, with the real mode in the receipt.
    """
    import logging
    guard_logger = logging.getLogger("aletheia.calculations._guards")
    old_level = guard_logger.level
    guard_logger.setLevel(logging.CRITICAL + 1)
    try:
        kwargs["mode_override"] = "hard"
        check_callable(*args, **kwargs)
        return None
    except CalculationError as exc:
        return exc
    finally:
        guard_logger.setLevel(old_level)


def _emit_collected(
    exc: CalculationError, mode: str, violations: list,
) -> None:
    """Append a collected violation to the list AND emit a structured
    log at the system's real mode (not the forced-hard mode used for
    collection)."""
    import logging
    record = exc.to_receipt()
    record["mode"] = mode
    violations.append(record)
    guard_logger = logging.getLogger("aletheia.calculations._guards")
    if mode == "hard":
        guard_logger.error("calc_guard_violation %s", record)
    else:
        guard_logger.warning("calc_guard_violation %s", record)


def _override_covers_field(ticker: str, field: str) -> bool:
    """True if any active override for this ticker lists `field` in its
    fields scope. Used by the schema contract to downgrade missing-field
    errors to soft-flags when the gap is a documented edge case."""
    for override_key, record in OVERRIDES.get(ticker, {}).items():
        if field in (record.get("fields") or []):
            return True
    return False


# Business models that bypass the FCFF DCF entirely (banks, asset
# managers, insurers, payment networks that don't fit the industrial
# schema). For these, the schema-contract requires a much smaller set
# (just revenue-or-interest-income + total_assets); the rest of the
# industrial schema is irrelevant because the calc layer routes them
# to specialized engines or skips them.
_BUSINESS_MODELS_NON_FCFF = frozenset({
    "ddm_required",            # UNH, CNC
    "embedded_value_required", # life insurance (none in current universe)
    "routing_required",        # AXP, JPM, BRK-B
})


def _resolve_business_model(ticker: str) -> str:
    """Look up the ticker's business_model from ticker_classification.
    Returns 'fcff_compatible' as default (most permissive industrial)."""
    try:
        from config.ticker_classification import get_extended_universe
        u = get_extended_universe()
        c = u.get(ticker)
        if c is None:
            return "fcff_compatible"
        return c.business_model or "fcff_compatible"
    except Exception:
        return "fcff_compatible"


# ─────────────────────────────────────────────────────────────────────
# Required-field sets per record period
# ─────────────────────────────────────────────────────────────────────

# Tier-1 strict-nonneg fields the record MUST carry. Keys are the
# canonical field names (matching TIER_1_STRICT_NONNEG); values are
# the (namespace, key) location on the CleanedRecord — checked in
# order, first non-None wins.
_FY_REQUIRED_TIER1 = {
    "revenue":      [("raw", "Revenue"), ("clean", "Revenue")],
    "total_assets": [("raw", "TotalAssets")],
    "shares_diluted": [("clean", "SharesDiluted"),
                       ("raw", "SharesDiluted")],
    # depreciation intentionally NOT required at the schema layer:
    # XBRL filers split D&A across multiple tags (GOOGL FY2015-2020
    # parsed by cleaning engine inconsistently), and downstream calc
    # functions (DCFEngine, reverse_dcf) already validate their own
    # D&A requirement. Making this a persistence-time requirement
    # would mass-reject historical rows that the calc layer can still
    # consume via fallback paths.
}

_TTM_REQUIRED_TIER1 = {
    "revenue":      [("raw", "Revenue"), ("clean", "Revenue")],
    "total_assets": [("raw", "TotalAssets")],
}

# Relaxed set for non-FCFF business models (routing_required,
# ddm_required, embedded_value_required). These tickers bypass the
# industrial DCF and don't necessarily carry the standard "Revenue"
# tag (financials use NetInterestIncome + NonInterestIncome). The
# canonical field names match TIER_1_STRICT_NONNEG; the path list
# accepts whichever XBRL location actually carries the value.
_NON_FCFF_REQUIRED_TIER1 = {
    "revenue":      [
        ("raw", "Revenue"),
        ("clean", "Revenue"),
        ("raw", "InterestIncome"),
        ("raw", "NetInterestIncome"),
        ("raw", "TotalRevenue"),
    ],
    "total_assets": [("raw", "TotalAssets")],
}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _read_field(record: Any, paths: List[Tuple[str, str]]) -> Optional[float]:
    """Walk (namespace, key) paths; return first non-None numeric value."""
    for ns, key in paths:
        bag = getattr(record, ns, None)
        if not bag:
            continue
        v = bag.get(key)
        if v is None:
            continue
        try:
            fv = float(v)
            if fv != fv:  # NaN check without importing math
                continue
            return fv
        except (TypeError, ValueError):
            continue
    return None


def _safe_record_field(record: Any, ns: str, key: str) -> Optional[float]:
    """Single-path read with NaN filtering."""
    bag = getattr(record, ns, None)
    if not bag:
        return None
    v = bag.get(key)
    if v is None:
        return None
    try:
        fv = float(v)
        if fv != fv:
            return None
        return fv
    except (TypeError, ValueError):
        return None


def _record_period(record: Any) -> str:
    return getattr(record, "period", "FY") or "FY"


def _record_ticker(record: Any) -> str:
    return getattr(record, "ticker", "?") or "?"


def _record_fy(record: Any) -> int:
    return int(getattr(record, "fiscal_year", 0) or 0)


# ─────────────────────────────────────────────────────────────────────
# The contract
# ─────────────────────────────────────────────────────────────────────

def validate_cleaned_record_schema_contract(
    record: Any,
    *,
    mode_override: Optional[str] = None,
) -> Tuple[bool, List[Dict[str, Any]]]:
    """Walk every invariant a persisted record must satisfy.

    See module docstring for mode behavior and return contract.
    """
    mode = _guard_mode(mode_override)
    violations: List[Dict[str, Any]] = []

    if mode == "off":
        return True, violations

    ticker = _record_ticker(record)
    period = _record_period(record)
    fn = f"schema_contract({period})"

    # Business-model-aware required-field selection. Non-FCFF business
    # models (financials, asset managers, insurers) don't fit the
    # industrial schema; they have their own much smaller required set.
    # TTM rows always use the smaller TTM-required set regardless of
    # business model (TTM normalization is shipped only for fcff_compatible).
    business_model = _resolve_business_model(ticker)
    if period == "TTM":
        required_tier1 = _TTM_REQUIRED_TIER1
    elif business_model in _BUSINESS_MODELS_NON_FCFF:
        required_tier1 = _NON_FCFF_REQUIRED_TIER1
    else:
        required_tier1 = _FY_REQUIRED_TIER1

    # ── (1) Required Tier-1 fields exist + are non-negative ─────────
    for canonical, paths in required_tier1.items():
        v = _read_field(record, paths)
        if v is None:
            # Check override registry: a documented edge case (e.g. TSLA
            # pre-2015 shares_diluted historical gap, V shares ingest bug)
            # downgrades the missing-field error to a soft-flag.
            if _override_covers_field(ticker, canonical):
                _flag_unusual(
                    value=None, field_name=canonical,
                    ticker=ticker, fn=fn,
                    note=(f"required field missing but override registry "
                          f"covers it for {ticker}; soft-flagged not raised"),
                    mode_override=mode_override,
                )
                continue
            exc = _collect_violation(_require_finite, None, canonical,
                                     ticker=ticker, fn=fn)
            if exc:
                _emit_collected(exc, mode, violations)
                if mode == "hard":
                    raise exc
        else:
            exc = _collect_violation(_require_strict_nonneg, v, canonical,
                                     ticker=ticker, fn=fn)
            if exc:
                _emit_collected(exc, mode, violations)
                if mode == "hard":
                    raise exc

    # ── (2) Arithmetic identities (when components are present) ─────
    revenue   = _read_field(record, [("raw", "Revenue"), ("clean", "Revenue")])
    ebit      = _safe_record_field(record, "derived", "OperatingIncome") \
                 or _safe_record_field(record, "raw", "OperatingIncome")
    ebitda    = _safe_record_field(record, "derived", "EBITDA")
    da_total  = _safe_record_field(record, "derived", "Depreciation_Total") \
                 or _safe_record_field(record, "raw", "Depreciation")
    op_cf     = _safe_record_field(record, "raw", "OperatingCF") \
                 or _safe_record_field(record, "clean", "OperatingCF")
    capex     = _safe_record_field(record, "raw", "CapEx") \
                 or _safe_record_field(record, "derived", "CapEx")
    fcf       = _safe_record_field(record, "clean", "FCF") \
                 or _safe_record_field(record, "derived", "FCF")
    total_assets = _safe_record_field(record, "raw", "TotalAssets")
    total_liab   = _safe_record_field(record, "raw", "TotalLiabilities")
    total_equity = _safe_record_field(record, "raw", "TotalEquity")
    net_debt   = _safe_record_field(record, "derived", "NetDebt")
    long_debt  = _safe_record_field(record, "raw", "LongTermDebt")
    cash       = _safe_record_field(record, "raw", "Cash")

    # EBITDA = EBIT + D&A (definitional)
    if ebitda is not None and ebit is not None and da_total is not None:
        exc = _collect_violation(
            _require_consistent,
            actual=ebitda, expected=(ebit + da_total),
            tolerance_pct=IDENTITY_TOLERANCES["ebitda_equals_ebit_plus_da"],
            identity_name="ebitda_equals_ebit_plus_da",
            ticker=ticker, fn=fn,
        )
        if exc:
            _emit_collected(exc, mode, violations)
            if mode == "hard":
                raise exc

    # FCF identity. Try TWO forms because of the ASC 842 transition
    # (2019): pre-2019, filers reported clean_FCF = OpCF - CapEx with
    # finance-lease principal NOT subtracted; post-2019, the company-
    # reported FCF subtracts FinanceLeasePrincipalPayments. Auto-detect
    # which form the filer used by trying both and accepting the closer.
    if fcf is not None and op_cf is not None and capex is not None:
        fin_lease_principal = _safe_record_field(
            record, "raw", "FinanceLeasePrincipalPayments")
        financing_obligation = _safe_record_field(
            record, "raw", "FinancingObligationPrincipalPayments")

        expected_simple = op_cf - capex
        expected_with_lease = expected_simple
        if fin_lease_principal is not None:
            expected_with_lease -= fin_lease_principal
        if financing_obligation is not None:
            expected_with_lease -= financing_obligation

        tol = IDENTITY_TOLERANCES["fcf_equals_opcf_minus_capex"]

        def _within(actual_v: float, expected_v: float) -> bool:
            if abs(expected_v) < 1e-9:
                return abs(actual_v - expected_v) <= 1.0
            return abs(actual_v - expected_v) / abs(expected_v) <= tol

        simple_ok = _within(fcf, expected_simple)
        lease_ok = _within(fcf, expected_with_lease)

        if simple_ok or lease_ok:
            # At least one form holds — identity is satisfied.
            pass
        elif fin_lease_principal is None and financing_obligation is None:
            # Neither form holds and we have no lease data to attribute
            # the gap to. Soft-flag (gap is visible but not blocking;
            # ingest may be missing the lease term).
            _flag_unusual(
                value=fcf, field_name="fcf",
                ticker=ticker, fn=fn,
                note=(
                    f"FCF gap vs (OpCF-CapEx) is ${(fcf - expected_simple)/1e9:.2f}B. "
                    "FinanceLeasePrincipalPayments not populated on this row; "
                    "cannot enforce strict identity until ingest captures the "
                    "lease term."
                ),
                mode_override=mode_override,
            )
        else:
            # Both forms fail AND lease data is populated — this is a
            # real consistency violation. Emit via the stricter form.
            exc = _collect_violation(
                _require_consistent,
                actual=fcf, expected=expected_with_lease,
                tolerance_pct=tol,
                identity_name="fcf_equals_opcf_minus_capex_minus_lease",
                ticker=ticker, fn=fn,
            )
            if exc:
                _emit_collected(exc, mode, violations)
                if mode == "hard":
                    raise exc

    # Accounting equation: A = L + E (+ RedeemableNCI depending on filer).
    # The cleaning engine's TotalEquity already includes regular
    # MinorityInterest (verified on MCO base case). RedeemableNoncontrolling
    # Interest is reported HETEROGENEOUSLY across filers:
    #   - UNH/CAT-recent/TSM: TotalEquity excludes RedeemableNCI, so the
    #     identity must add it on the right side
    #   - MCO/WMT/TSLA: TotalEquity already INCLUDES RedeemableNCI, so
    #     adding it again double-counts and produces a -RedeemableNCI gap
    # Try both forms and accept whichever satisfies tolerance — same
    # auto-detection pattern as the FCF/lease-principal identity.
    if (total_assets is not None and total_liab is not None
            and total_equity is not None):
        redeemable_nci = _safe_record_field(
            record, "raw", "RedeemableNoncontrollingInterest") or 0.0

        tol = IDENTITY_TOLERANCES["accounting_equation_a_eq_l_plus_e"]
        expected_no_nci = total_liab + total_equity
        expected_with_nci = expected_no_nci + redeemable_nci

        def _within(actual_v: float, expected_v: float) -> bool:
            if abs(expected_v) < 1e-9:
                return abs(actual_v - expected_v) <= 1.0
            return abs(actual_v - expected_v) / abs(expected_v) <= tol

        if _within(total_assets, expected_with_nci) or _within(total_assets, expected_no_nci):
            pass  # at least one form holds
        elif _override_covers_field(ticker, "accounting_equation_a_eq_l_plus_e"):
            # Override registry covers this ticker's accounting-equation
            # identity (typically utility-filer XBRL aggregation gaps).
            # Soft-flag with full context so the violation is visible in
            # receipts but doesn't block persist / raise in hard mode.
            err_no = abs(total_assets - expected_no_nci)
            _flag_unusual(
                value=total_assets, field_name="accounting_equation_a_eq_l_plus_e",
                ticker=ticker, fn=fn,
                note=(f"A=L+E gap=${err_no/1e9:.2f}B covered by override "
                      f"registry for {ticker}; soft-flagged not raised"),
                mode_override=mode_override,
            )
        else:
            # Neither form satisfies — real violation. Emit via the
            # closer of the two forms for the most informative error.
            err_with = abs(total_assets - expected_with_nci)
            err_no = abs(total_assets - expected_no_nci)
            chosen_expected = (expected_with_nci if err_with < err_no
                               else expected_no_nci)
            exc = _collect_violation(
                _require_consistent,
                actual=total_assets, expected=chosen_expected,
                tolerance_pct=tol,
                identity_name="accounting_equation_a_eq_l_plus_e",
                ticker=ticker, fn=fn,
            )
            if exc:
                _emit_collected(exc, mode, violations)
                if mode == "hard":
                    raise exc

    # NetDebt = TotalDebt - Cash (derived; looser tolerance)
    if (net_debt is not None and long_debt is not None and cash is not None):
        exc = _collect_violation(
            _require_consistent,
            actual=net_debt, expected=(long_debt - cash),
            tolerance_pct=IDENTITY_TOLERANCES["net_debt_equals_debt_minus_cash"],
            identity_name="net_debt_equals_debt_minus_cash",
            ticker=ticker, fn=fn,
        )
        if exc:
            _emit_collected(exc, mode, violations)
            if mode == "hard":
                raise exc

    # ── (3) Range checks on critical ratios ─────────────────────────
    if revenue and revenue > 0:
        if capex is not None:
            cmin, cmax = RANGE_BOUNDS["capex_to_revenue"]
            exc = _collect_violation(
                _require_range,
                capex / revenue, min=cmin, max=cmax,
                field_name="capex_to_revenue", ticker=ticker, fn=fn,
                note=("negative beyond bound → sign error; "
                      "above upper bound → unit error or wrong-tag "
                      "(semis can run 0.50-0.75 during expansion)"),
            )
            if exc:
                _emit_collected(exc, mode, violations)
                if mode == "hard":
                    raise exc

    return True, violations


# ─────────────────────────────────────────────────────────────────────
# Tier-C classifier — Layer 1 enforcement at Stage 2 → 3 boundary
# ─────────────────────────────────────────────────────────────────────

# Identity fields whose violation constitutes a truly invalid state
# (financial statements cannot internally contradict). These hard-block
# Stage 3 at the boundary. Everything else is a Tier-W warning that
# flows through to the accounting_identities audit.
_TIER_C_FIELDS = frozenset({
    "accounting_equation_a_eq_l_plus_e",
    # Missing Tier-1 required fields surface as category=MissingRequiredFieldError
    # and are tested by category below, not field name.
})

_TIER_C_CATEGORIES = frozenset({
    "MissingRequiredFieldError",
})


def is_tier_c_violation(violation: Dict[str, Any]) -> bool:
    """Classify a schema_contract violation as Tier C (critical, hard-
    block Stage 3) or Tier W (warning, surface but allow).

    Tier C = truly invalid states (e.g. A ≠ L + E, required-field
    missing). Stage 3 refuses to compute on a record carrying one
    because the math downstream becomes meaningless — you can't DCF
    a balance sheet that doesn't balance.

    Tier W = identity drift signal. The record itself is still
    consumable downstream; these flow through to the
    accounting_identities Stage 3 audit with documented exception
    categories per Phase 3.

    Returns True for Tier C, False for Tier W.
    """
    if violation.get("category") in _TIER_C_CATEGORIES:
        return True
    if violation.get("field") in _TIER_C_FIELDS:
        return True
    return False


def extract_tier_c_violations(
    schema_violations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Filter a violations list to only Tier-C (critical) entries.

    Convenience wrapper for ``run_stage3``'s pre-flight gate. When the
    returned list is non-empty, Stage 3 raises ``Stage3InputError``
    rather than computing on broken inputs.
    """
    return [v for v in (schema_violations or []) if is_tier_c_violation(v)]
