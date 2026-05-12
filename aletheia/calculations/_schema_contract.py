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

from ._errors import (
    CalculationConsistencyError,
    CalculationError,
    CalculationInputError,
)
from ._guards import (
    _guard_mode,
    _require_consistent,
    _require_finite,
    _require_range,
    _require_strict_nonneg,
)
from ._sign_conventions import IDENTITY_TOLERANCES


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
    # depreciation is required for ratio math but often null at raw
    # layer (XBRL filers split it across multiple tags); cleaning
    # engine derives a total in derived.Depreciation_Total
    "depreciation": [("derived", "Depreciation_Total"),
                     ("clean", "Depreciation_Total"),
                     ("raw", "Depreciation")],
    "shares_diluted": [("clean", "SharesDiluted"),
                       ("raw", "SharesDiluted")],
}

_TTM_REQUIRED_TIER1 = {
    "revenue":      [("raw", "Revenue"), ("clean", "Revenue")],
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
    fy = _record_fy(record)
    fn = f"schema_contract({period})"

    required_tier1 = (
        _TTM_REQUIRED_TIER1 if period == "TTM" else _FY_REQUIRED_TIER1
    )

    # ── (1) Required Tier-1 fields exist + are non-negative ─────────
    for canonical, paths in required_tier1.items():
        v = _read_field(record, paths)
        if v is None:
            violations.append({
                "category": "missing_required_field",
                "field": canonical, "period": period,
                "fy": fy,
            })
            try:
                _require_finite(None, canonical, ticker=ticker, fn=fn,
                                mode_override=mode_override)
            except CalculationError:
                if mode == "hard":
                    raise
        else:
            try:
                _require_strict_nonneg(v, canonical, ticker=ticker, fn=fn,
                                       mode_override=mode_override)
            except CalculationError as exc:
                violations.append(exc.to_receipt())
                if mode == "hard":
                    raise

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
        try:
            _require_consistent(
                actual=ebitda, expected=(ebit + da_total),
                tolerance_pct=IDENTITY_TOLERANCES["ebitda_equals_ebit_plus_da"],
                identity_name="ebitda_equals_ebit_plus_da",
                ticker=ticker, fn=fn, mode_override=mode_override,
            )
        except CalculationConsistencyError as exc:
            violations.append(exc.to_receipt())
            if mode == "hard":
                raise

    # FCF = OpCF - CapEx (definitional; CapEx is positive per schema)
    if fcf is not None and op_cf is not None and capex is not None:
        try:
            _require_consistent(
                actual=fcf, expected=(op_cf - capex),
                tolerance_pct=IDENTITY_TOLERANCES["fcf_equals_opcf_minus_capex"],
                identity_name="fcf_equals_opcf_minus_capex",
                ticker=ticker, fn=fn, mode_override=mode_override,
            )
        except CalculationConsistencyError as exc:
            violations.append(exc.to_receipt())
            if mode == "hard":
                raise

    # Accounting equation: A = L + E (NEE-class catch)
    if (total_assets is not None and total_liab is not None
            and total_equity is not None):
        try:
            _require_consistent(
                actual=total_assets, expected=(total_liab + total_equity),
                tolerance_pct=IDENTITY_TOLERANCES["accounting_equation_a_eq_l_plus_e"],
                identity_name="accounting_equation_a_eq_l_plus_e",
                ticker=ticker, fn=fn, mode_override=mode_override,
            )
        except CalculationConsistencyError as exc:
            violations.append(exc.to_receipt())
            if mode == "hard":
                raise

    # NetDebt = TotalDebt - Cash (derived; looser tolerance)
    if (net_debt is not None and long_debt is not None and cash is not None):
        try:
            _require_consistent(
                actual=net_debt, expected=(long_debt - cash),
                tolerance_pct=IDENTITY_TOLERANCES["net_debt_equals_debt_minus_cash"],
                identity_name="net_debt_equals_debt_minus_cash",
                ticker=ticker, fn=fn, mode_override=mode_override,
            )
        except CalculationConsistencyError as exc:
            violations.append(exc.to_receipt())
            if mode == "hard":
                raise

    # ── (3) Range checks on critical ratios ─────────────────────────
    if revenue and revenue > 0:
        if capex is not None:
            try:
                _require_range(
                    capex / revenue, min=-0.30, max=0.50,
                    field_name="capex_to_revenue", ticker=ticker, fn=fn,
                    note="negative→divestitures (legitimate); "
                         ">0.50→sign or unit error",
                    mode_override=mode_override,
                )
            except CalculationInputError as exc:
                violations.append(exc.to_receipt())
                if mode == "hard":
                    raise

    return True, violations
