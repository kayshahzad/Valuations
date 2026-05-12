"""Calculation-layer validation framework.

Public API:

    from aletheia.calculations import (
        # primitives
        _require_finite,
        _require_strict_nonneg,
        _require_range,
        _require_consistent,
        _flag_unusual,
        # error classes
        CalculationError,
        CalculationInputError,
        CalculationOutputError,
        CalculationConsistencyError,
        # constants
        TIER_1_STRICT_NONNEG,
        TIER_2_SOFT_FLAG_NEGATIVE_OK,
        TIER_3_NO_SIGN_RULE,
        RANGE_BOUNDS,
        IDENTITY_TOLERANCES,
        # override registry
        OVERRIDES,
        is_override_active,
        get_override,
        log_past_due_overrides,
        # mode resolver
        _guard_mode,
    )

Mode control: set ``ALETHEIA_GUARD_MODE`` env var to one of
``off`` / ``shadow`` / ``soft`` / ``hard``. Default ``off`` preserves
legacy behavior during rollout.
"""

from ._errors import (
    CalculationError,
    CalculationInputError,
    CalculationOutputError,
    CalculationConsistencyError,
)
from ._sign_conventions import (
    TIER_1_STRICT_NONNEG,
    TIER_2_SOFT_FLAG_NEGATIVE_OK,
    TIER_3_NO_SIGN_RULE,
    RANGE_BOUNDS,
    IDENTITY_TOLERANCES,
)
from ._overrides import (
    OVERRIDES,
    is_override_active,
    get_override,
    log_past_due_overrides,
)
from ._guards import (
    _require_finite,
    _require_strict_nonneg,
    _require_range,
    _require_consistent,
    _flag_unusual,
    _guard_mode,
)
from ._schema_contract import validate_cleaned_record_schema_contract
from ._logging import setup_guard_audit_logging, get_today_audit_path


# Auto-activate audit logging on first import. The setup is idempotent
# (won't double-attach), startup banner goes to stderr so any caller
# can see which mode is active and where violations land.
#
# Suppressed when ALETHEIA_GUARD_MODE is unset OR explicitly 'off' — no
# need to maintain an audit log for a no-op framework. Activated for
# shadow / soft / hard modes where violations actually flow.
import os as _os
if _os.environ.get("ALETHEIA_GUARD_MODE", "off").lower() != "off":
    setup_guard_audit_logging()

__all__ = [
    # primitives
    "_require_finite",
    "_require_strict_nonneg",
    "_require_range",
    "_require_consistent",
    "_flag_unusual",
    # errors
    "CalculationError",
    "CalculationInputError",
    "CalculationOutputError",
    "CalculationConsistencyError",
    # constants
    "TIER_1_STRICT_NONNEG",
    "TIER_2_SOFT_FLAG_NEGATIVE_OK",
    "TIER_3_NO_SIGN_RULE",
    "RANGE_BOUNDS",
    "IDENTITY_TOLERANCES",
    # overrides
    "OVERRIDES",
    "is_override_active",
    "get_override",
    "log_past_due_overrides",
    # mode
    "_guard_mode",
    # schema contract
    "validate_cleaned_record_schema_contract",
    # audit-log setup
    "setup_guard_audit_logging",
    "get_today_audit_path",
]
