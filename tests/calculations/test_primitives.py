"""Phase 7 tests — framework primitives (_require_finite, _require_strict_
nonneg, _require_range, _require_consistent, _flag_unusual).

Coverage matrix:
  - Off mode: every primitive is a no-op (preserves legacy behavior)
  - Shadow mode: primitives log a structured warning, never raise
  - Soft mode: primitives log + surface, never raise
  - Hard mode: primitives raise the appropriate error class

Plus coding-bug guard: calling _require_strict_nonneg on a Tier-2 field
raises ValueError unconditionally (caller bug, not data bug).
"""

from __future__ import annotations

import logging
import math
import os

import pytest

from aletheia.calculations import (
    _flag_unusual,
    _require_consistent,
    _require_finite,
    _require_range,
    _require_strict_nonneg,
    CalculationConsistencyError,
    CalculationInputError,
)


@pytest.fixture(autouse=True)
def reset_guard_mode(monkeypatch):
    """Default each test to OFF so individual tests opt into the mode they need."""
    monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
    yield


# ─────────────────────────────────────────────────────────────────────
# _require_finite
# ─────────────────────────────────────────────────────────────────────

class TestRequireFinite:

    def test_off_mode_accepts_nan(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        _require_finite(float("nan"), "test", ticker="X", fn="t")  # no raise

    def test_off_mode_accepts_none(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        _require_finite(None, "test", ticker="X", fn="t")

    def test_shadow_mode_logs_nan(self, monkeypatch, caplog):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "shadow")
        with caplog.at_level(logging.WARNING, logger="aletheia.calculations._guards"):
            _require_finite(float("nan"), "rev", ticker="X", fn="t")
        assert any("calc_guard_violation" in r.message for r in caplog.records)

    def test_hard_mode_raises_on_nan(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        with pytest.raises(CalculationInputError) as excinfo:
            _require_finite(float("nan"), "test_field", ticker="MDT", fn="reverse_dcf")
        assert excinfo.value.field == "test_field"
        assert excinfo.value.ticker == "MDT"

    def test_hard_mode_raises_on_none(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        with pytest.raises(CalculationInputError):
            _require_finite(None, "rev", ticker="X", fn="t")

    def test_hard_mode_raises_on_inf(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        with pytest.raises(CalculationInputError):
            _require_finite(math.inf, "rev", ticker="X", fn="t")

    def test_hard_mode_raises_on_non_numeric(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        with pytest.raises(CalculationInputError):
            _require_finite("not_a_number", "rev", ticker="X", fn="t")

    def test_hard_mode_accepts_valid(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        _require_finite(100.0, "rev", ticker="X", fn="t")
        _require_finite(0, "rev", ticker="X", fn="t")
        _require_finite(-50.5, "rev", ticker="X", fn="t")

    def test_mode_override_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        with pytest.raises(CalculationInputError):
            _require_finite(None, "rev", ticker="X", fn="t",
                            mode_override="hard")


# ─────────────────────────────────────────────────────────────────────
# _require_strict_nonneg
# ─────────────────────────────────────────────────────────────────────

class TestRequireStrictNonneg:

    def test_hard_mode_raises_on_negative_revenue(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        with pytest.raises(CalculationInputError) as excinfo:
            _require_strict_nonneg(-100, "revenue", ticker="X", fn="t")
        assert excinfo.value.field == "revenue"
        assert "negative" in str(excinfo.value).lower()

    def test_hard_mode_accepts_zero(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        _require_strict_nonneg(0.0, "revenue", ticker="X", fn="t")

    def test_hard_mode_accepts_positive(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        _require_strict_nonneg(1_000_000_000.0, "revenue", ticker="X", fn="t")

    def test_wrong_tier_raises_value_error_in_any_mode(self, monkeypatch):
        """Coding-bug guard: calling on a Tier-2 field is always ValueError,
        regardless of mode. capex is Tier 2 (can legitimately be negative)."""
        for mode in ("off", "shadow", "soft", "hard"):
            monkeypatch.setenv("ALETHEIA_GUARD_MODE", mode)
            with pytest.raises(ValueError) as excinfo:
                _require_strict_nonneg(-100, "capex", ticker="X", fn="t")
            assert "TIER_1_STRICT_NONNEG" in str(excinfo.value)

    def test_wrong_tier_on_tier3_raises_value_error(self, monkeypatch):
        """tax_rate is Tier 3 — strict-nonneg is a wrong-tier coding bug."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        with pytest.raises(ValueError):
            _require_strict_nonneg(0.21, "tax_rate", ticker="X", fn="t")


# ─────────────────────────────────────────────────────────────────────
# _require_range
# ─────────────────────────────────────────────────────────────────────

class TestRequireRange:

    def test_hard_mode_accepts_within_range(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        _require_range(0.21, min=-1.0, max=1.0,
                       field_name="tax_rate", ticker="X", fn="t")

    def test_hard_mode_accepts_at_boundaries(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        _require_range(-1.0, min=-1.0, max=1.0,
                       field_name="tax_rate", ticker="X", fn="t")
        _require_range(1.0, min=-1.0, max=1.0,
                       field_name="tax_rate", ticker="X", fn="t")

    def test_hard_mode_raises_above_max(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        with pytest.raises(CalculationInputError) as excinfo:
            _require_range(1.5, min=-1.0, max=1.0,
                           field_name="tax_rate", ticker="X", fn="t")
        assert excinfo.value.field == "tax_rate"
        assert "out of range" in str(excinfo.value).lower()

    def test_hard_mode_raises_below_min(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        with pytest.raises(CalculationInputError):
            _require_range(-1.5, min=-1.0, max=1.0,
                           field_name="tax_rate", ticker="X", fn="t")

    def test_hard_mode_raises_on_nan(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        with pytest.raises(CalculationInputError):
            _require_range(float("nan"), min=-1.0, max=1.0,
                           field_name="tax_rate", ticker="X", fn="t")

    def test_capex_range_accepts_legitimate_negative(self, monkeypatch):
        """Tier-2 capex/revenue can be negative (divestiture years).
        Range [-0.30, 0.75] accepts -10% as legitimate."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        _require_range(-0.10, min=-0.30, max=0.75,
                       field_name="capex_to_revenue", ticker="GE", fn="t")

    def test_capex_range_rejects_extreme_negative(self, monkeypatch):
        """-50% capex/revenue is implausible — likely sign error."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        with pytest.raises(CalculationInputError):
            _require_range(-0.50, min=-0.30, max=0.75,
                           field_name="capex_to_revenue", ticker="X", fn="t")


# ─────────────────────────────────────────────────────────────────────
# _require_consistent (arithmetic identity)
# ─────────────────────────────────────────────────────────────────────

class TestRequireConsistent:

    def test_identity_holds_within_tolerance(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        # EBITDA = EBIT + D&A, EBIT=100, D&A=20, EBITDA=120 (exact)
        _require_consistent(
            actual=120.0, expected=(100.0 + 20.0),
            tolerance_pct=0.005,
            identity_name="ebitda_equals_ebit_plus_da",
            ticker="X", fn="t",
        )

    def test_identity_holds_with_small_drift(self, monkeypatch):
        """0.3% drift is within the 0.5% tolerance."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        _require_consistent(
            actual=120.36, expected=120.0,  # 0.3% over
            tolerance_pct=0.005,
            identity_name="ebitda_equals_ebit_plus_da",
            ticker="X", fn="t",
        )

    def test_identity_violation_raises(self, monkeypatch):
        """1% drift exceeds 0.5% tolerance — raise."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        with pytest.raises(CalculationConsistencyError) as excinfo:
            _require_consistent(
                actual=121.2, expected=120.0,  # 1% drift
                tolerance_pct=0.005,
                identity_name="ebitda_equals_ebit_plus_da",
                ticker="X", fn="t",
            )
        assert excinfo.value.field == "ebitda_equals_ebit_plus_da"
        assert "rel_diff" in str(excinfo.value)

    def test_near_zero_expected_uses_absolute_tolerance(self, monkeypatch):
        """When |expected| < 1e-9, use absolute tolerance of 1.0."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        # actual=0.5, expected=0 — within absolute tolerance
        _require_consistent(
            actual=0.5, expected=0.0,
            tolerance_pct=0.005,
            identity_name="some_identity",
            ticker="X", fn="t",
        )
        # actual=2.0, expected=0 — exceeds 1.0 absolute
        with pytest.raises(CalculationConsistencyError):
            _require_consistent(
                actual=2.0, expected=0.0,
                tolerance_pct=0.005,
                identity_name="some_identity",
                ticker="X", fn="t",
            )

    def test_shadow_mode_logs_does_not_raise(self, monkeypatch, caplog):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "shadow")
        with caplog.at_level(logging.WARNING, logger="aletheia.calculations._guards"):
            _require_consistent(
                actual=200.0, expected=100.0,  # 100% drift
                tolerance_pct=0.005,
                identity_name="some_identity",
                ticker="X", fn="t",
            )
        assert any("identity" in r.message.lower() for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────
# _flag_unusual (Tier-2 soft signal)
# ─────────────────────────────────────────────────────────────────────

class TestFlagUnusual:

    def test_never_raises_in_any_mode(self, monkeypatch):
        """Tier-2 soft-flag is always non-blocking, regardless of mode."""
        for mode in ("off", "shadow", "soft", "hard"):
            monkeypatch.setenv("ALETHEIA_GUARD_MODE", mode)
            _flag_unusual(
                value=-1e9, field_name="capex",
                ticker="GE", fn="t",
                note="net divestiture year — legitimate negative",
            )

    def test_logs_in_shadow_mode(self, monkeypatch, caplog):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "shadow")
        with caplog.at_level(logging.WARNING, logger="aletheia.calculations._guards"):
            _flag_unusual(
                value=-1e9, field_name="capex",
                ticker="GE", fn="t",
                note="net divestiture year",
            )
        assert any("soft_flag" in r.message for r in caplog.records)

    def test_off_mode_no_log(self, monkeypatch, caplog):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        with caplog.at_level(logging.WARNING, logger="aletheia.calculations._guards"):
            _flag_unusual(
                value=-1e9, field_name="capex",
                ticker="GE", fn="t", note="test",
            )
        # off mode should not emit anything
        assert not any("soft_flag" in r.message for r in caplog.records)
