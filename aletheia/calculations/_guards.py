"""Validation primitives for the calculation layer.

Public surface:

  _require_finite(value, field_name, *, ticker, fn)
      Hard-fail on None / NaN / inf. Always-on; no mode gate (a non-
      finite input is never legitimate).

  _require_strict_nonneg(value, field_name, *, ticker, fn)
      Tier-1 sign check. The field name MUST be in TIER_1_STRICT_NONNEG;
      using this on a Tier-2/3 field is a coding bug.

  _flag_unusual(value, field_name, *, ticker, fn, note)
      Tier-2 soft signal. Logs structured warning, never raises. Used
      for legitimate-but-noteworthy values (negative CapEx in a
      divestiture year, etc.).

  _require_range(value, *, min, max, field_name, ticker, fn, note="")
      Range check. Works for ratios, rates, and absolute values. The
      most powerful single primitive: catches sign errors, unit errors,
      and wrong-field-mapping all at once.

  _require_consistent(actual, expected, *, tolerance_pct, identity_name,
                      ticker, fn)
      Arithmetic identity check. The most reliable bug-catcher because
      it encodes what MUST be true (FCF = OpCF − CapEx) rather than
      what is typically true.

Rollback architecture — the ``_guard_mode()`` function reads
``ALETHEIA_GUARD_MODE`` env var on each call (so flipping the kill
switch takes effect without restart):

  - ``off``    : guards are no-ops. Default — preserves legacy behavior.
  - ``shadow`` : guards log structured warnings only, never raise.
  - ``soft``   : guards log + emit a structured receipt (UI surfaces it);
                 computation proceeds. Use when the UI has the
                 "unavailable" affordance ready.
  - ``hard``   : guards raise CalculationError. Use when the system is
                 ready to refuse degraded inputs in production.

Per-function override: pass ``mode_override="hard"`` (or ``"soft"``)
to elevate a specific function's enforcement level without flipping
the global env var. Used for trusted functions that should always
refuse while the rest of the system is in shadow.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Optional

from ._errors import (
    CalculationError,
    CalculationInputError,
    CalculationConsistencyError,
)
from ._sign_conventions import TIER_1_STRICT_NONNEG

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Mode resolution
# ─────────────────────────────────────────────────────────────────────

_VALID_MODES = {"off", "shadow", "soft", "hard"}
_ENV_VAR = "ALETHEIA_GUARD_MODE"


def _guard_mode(override: Optional[str] = None) -> str:
    """Resolve the active guard mode.

    Precedence: explicit ``override`` arg > ``ALETHEIA_GUARD_MODE``
    env var > default ``"off"``. Reads env on every call so a kill-
    switch flip takes effect immediately without process restart.
    """
    if override is not None:
        if override not in _VALID_MODES:
            raise ValueError(
                f"mode_override={override!r} invalid; expected one of "
                f"{sorted(_VALID_MODES)}"
            )
        return override
    env = os.environ.get(_ENV_VAR, "off").strip().lower()
    return env if env in _VALID_MODES else "off"


def _should_raise(mode: str) -> bool:
    """True when the configured mode causes guards to raise."""
    return mode == "hard"


def _should_emit(mode: str) -> bool:
    """True when the mode logs and/or surfaces violations.

    'off' is a no-op (legacy behavior preserved). 'shadow', 'soft',
    'hard' all emit structured information; 'hard' additionally
    raises.
    """
    return mode in {"shadow", "soft", "hard"}


# ─────────────────────────────────────────────────────────────────────
# Structured emission
# ─────────────────────────────────────────────────────────────────────

def _emit_violation(
    error_cls: type,
    message: str,
    *,
    ticker: str,
    fn: str,
    field: Optional[str] = None,
    value: Any = None,
    expected: Optional[str] = None,
    mode: str,
) -> None:
    """Common emit path. In shadow/soft → log; in hard → raise."""
    err = error_cls(
        message, ticker=ticker, fn=fn, field=field,
        value=value, expected=expected,
    )
    # Log the structured receipt at WARNING (shadow/soft) or ERROR (hard).
    record = err.to_receipt()
    record["mode"] = mode
    if mode == "hard":
        logger.error("calc_guard_violation %s", record)
        raise err
    else:
        logger.warning("calc_guard_violation %s", record)


def _structured_warn(
    *,
    ticker: str,
    fn: str,
    field: str,
    value: Any,
    category: str,
    note: str,
    mode: str,
) -> None:
    """Tier-2 soft signal. Never raises regardless of mode (Tier 2 is
    by definition non-blocking). Logged so it shows up in audits."""
    if not _should_emit(mode):
        return
    logger.warning(
        "calc_guard_soft_flag %s",
        {
            "ticker":   ticker, "fn": fn,
            "field":    field, "value": value,
            "category": category, "note": note,
            "mode":     mode,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Primitives
# ─────────────────────────────────────────────────────────────────────

def _require_finite(
    value: Any,
    field_name: str,
    *,
    ticker: str,
    fn: str,
    mode_override: Optional[str] = None,
) -> None:
    """Hard-fail on None / NaN / inf / non-numeric.

    Always-on conceptually: a non-finite value is never legitimate input
    to a calc function. Honors the mode gate (in 'off' it's a no-op for
    backwards compat during shadow-mode rollout).
    """
    mode = _guard_mode(mode_override)
    if mode == "off":
        return

    if value is None:
        _emit_violation(
            CalculationInputError,
            "value is None",
            ticker=ticker, fn=fn, field=field_name,
            value=None, expected="finite number",
            mode=mode,
        )
        return
    if not isinstance(value, (int, float)):
        _emit_violation(
            CalculationInputError,
            f"value is not numeric (type={type(value).__name__})",
            ticker=ticker, fn=fn, field=field_name,
            value=value, expected="int or float",
            mode=mode,
        )
        return
    if isinstance(value, float) and not math.isfinite(value):
        _emit_violation(
            CalculationInputError,
            f"value is not finite ({value!r})",
            ticker=ticker, fn=fn, field=field_name,
            value=value, expected="finite (not NaN/inf)",
            mode=mode,
        )


def _require_strict_nonneg(
    value: Any,
    field_name: str,
    *,
    ticker: str,
    fn: str,
    mode_override: Optional[str] = None,
) -> None:
    """Tier-1 strict non-negative check.

    Fails (per mode) when value < 0 OR not finite. The field name MUST
    be classified as Tier 1 in ``_sign_conventions.TIER_1_STRICT_NONNEG``;
    calling this primitive on a Tier-2 or Tier-3 field is a coding bug
    and ValueError raises unconditionally (no mode gating — this is a
    bug in the caller, not the data).
    """
    if field_name not in TIER_1_STRICT_NONNEG:
        raise ValueError(
            f"_require_strict_nonneg called on {field_name!r}, which is "
            f"NOT in TIER_1_STRICT_NONNEG. This is a bug in the calling "
            f"code — use _require_finite + _require_range instead. Tier-2 "
            f"fields can legitimately be negative."
        )

    _require_finite(value, field_name, ticker=ticker, fn=fn,
                    mode_override=mode_override)

    mode = _guard_mode(mode_override)
    if mode == "off":
        return

    # _require_finite may have already raised in hard mode; if it didn't,
    # value is a number and we can sign-check.
    if isinstance(value, (int, float)) and value < 0:
        _emit_violation(
            CalculationInputError,
            f"Tier-1 strict-nonneg field is negative ({value})",
            ticker=ticker, fn=fn, field=field_name,
            value=value, expected=">= 0",
            mode=mode,
        )


def _flag_unusual(
    value: Any,
    field_name: str,
    *,
    ticker: str,
    fn: str,
    note: str,
    mode_override: Optional[str] = None,
) -> None:
    """Tier-2 soft signal — log, never raise.

    Use for legitimate-but-noteworthy values (negative CapEx in a
    divestiture year, negative FCF in a growth-investment year). The
    log entry lets us measure how often each Tier-2 case fires across
    the universe; high-frequency cases indicate the rule may be too
    sensitive.
    """
    mode = _guard_mode(mode_override)
    _structured_warn(
        ticker=ticker, fn=fn, field=field_name, value=value,
        category="tier2_soft_flag", note=note, mode=mode,
    )


def _require_range(
    value: Any,
    *,
    min: float,
    max: float,
    field_name: str,
    ticker: str,
    fn: str,
    note: str = "",
    mode_override: Optional[str] = None,
) -> None:
    """Universal range check. Works for ratios, rates, absolute values.

    The most powerful single primitive: catches sign errors, unit
    errors, and wrong-field-mapping errors simultaneously. Use this on
    Tier-2 fields where sign rules would false-positive, AND on output
    sanity bounds (implied CAGR, WACC, etc.).
    """
    _require_finite(value, field_name, ticker=ticker, fn=fn,
                    mode_override=mode_override)

    mode = _guard_mode(mode_override)
    if mode == "off":
        return

    if isinstance(value, (int, float)) and (value < min or value > max):
        suffix = f" {note}" if note else ""
        _emit_violation(
            CalculationInputError,
            f"out of range [{min}, {max}] (got {value}).{suffix}",
            ticker=ticker, fn=fn, field=field_name,
            value=value, expected=f"[{min}, {max}]",
            mode=mode,
        )


def _require_consistent(
    actual: Any,
    expected: Any,
    *,
    tolerance_pct: float = 0.005,
    identity_name: str,
    ticker: str,
    fn: str,
    mode_override: Optional[str] = None,
) -> None:
    """Arithmetic identity check.

    The most reliable bug-catcher because identities encode what MUST
    be true rather than what is typically true. Examples:
      - EBITDA = EBIT + D&A (definitional)
      - FCF = OpCF - CapEx (definitional)
      - TotalAssets = TotalLiabilities + TotalEquity (accounting eq)
      - NetDebt = TotalDebt - Cash (derived; looser tolerance)

    tolerance_pct is fractional (0.005 = 0.5%). If |expected| is tiny
    (near zero), falls back to absolute tolerance of 1.0 (one unit of
    the currency or count) to avoid false-positive division-by-zero.
    """
    _require_finite(actual, f"{identity_name}_actual",
                    ticker=ticker, fn=fn, mode_override=mode_override)
    _require_finite(expected, f"{identity_name}_expected",
                    ticker=ticker, fn=fn, mode_override=mode_override)

    mode = _guard_mode(mode_override)
    if mode == "off":
        return

    actual_f = float(actual)
    expected_f = float(expected)

    if abs(expected_f) < 1e-9:
        # Near-zero expected: use absolute tolerance to avoid /0
        abs_diff = abs(actual_f - expected_f)
        if abs_diff > 1.0:
            _emit_violation(
                CalculationConsistencyError,
                f"identity {identity_name}: actual={actual_f}, "
                f"expected={expected_f}, abs_diff={abs_diff} > 1.0",
                ticker=ticker, fn=fn, field=identity_name,
                value=actual_f, expected=f"{expected_f} (abs_tol=1.0)",
                mode=mode,
            )
        return

    rel_diff = abs(actual_f - expected_f) / abs(expected_f)
    if rel_diff > tolerance_pct:
        _emit_violation(
            CalculationConsistencyError,
            f"identity {identity_name}: actual={actual_f}, "
            f"expected={expected_f}, rel_diff={rel_diff:.4f} > "
            f"tolerance={tolerance_pct}",
            ticker=ticker, fn=fn, field=identity_name,
            value=actual_f,
            expected=f"{expected_f} (within {tolerance_pct*100:.2f}%)",
            mode=mode,
        )


# Re-export the error hierarchy from a single import surface
__all__ = [
    "CalculationError",
    "CalculationInputError",
    "CalculationConsistencyError",
    "_require_finite",
    "_require_strict_nonneg",
    "_flag_unusual",
    "_require_range",
    "_require_consistent",
    "_guard_mode",
]
