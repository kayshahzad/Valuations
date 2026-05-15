"""Tests for Layer-1 tier-C enforcement at the Stage 2 → 3 boundary.

The tier-C classifier separates truly-invalid states (A ≠ L + E,
missing Tier-1 field) from identity drifts (EBITDA, net_debt, FCF).
Stage 3 refuses to run on records with tier-C violations; tier-W
violations flow through to the accounting_identities audit.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ─────────────────────────────────────────────────────────────────────
# Classifier unit tests
# ─────────────────────────────────────────────────────────────────────

def test_tier_c_classifier_identifies_balance_sheet_equation():
    from aletheia.calculations._schema_contract import is_tier_c_violation
    v = {
        "category": "CalculationConsistencyError",
        "field": "accounting_equation_a_eq_l_plus_e",
        "message": "...",
    }
    assert is_tier_c_violation(v) is True


def test_tier_c_classifier_identifies_missing_required_field():
    from aletheia.calculations._schema_contract import is_tier_c_violation
    v = {
        "category": "MissingRequiredFieldError",
        "field": "revenue",
        "message": "...",
    }
    assert is_tier_c_violation(v) is True


@pytest.mark.parametrize("field", [
    "ebitda_equals_ebit_plus_da",
    "net_debt_equals_debt_minus_cash",
    "fcf_equals_opcf_minus_capex",
    "capex_to_revenue",
])
def test_tier_c_classifier_treats_identity_drifts_as_tier_w(field):
    """Identity drifts should NOT block Stage 3 — they're informational
    diagnostics that flow through to the accounting_identities audit."""
    from aletheia.calculations._schema_contract import is_tier_c_violation
    v = {
        "category": "CalculationConsistencyError",
        "field": field,
        "message": "...",
    }
    assert is_tier_c_violation(v) is False


def test_extract_tier_c_violations_filters_correctly():
    from aletheia.calculations._schema_contract import (
        extract_tier_c_violations,
    )
    violations = [
        {"category": "CalculationConsistencyError",
         "field": "accounting_equation_a_eq_l_plus_e", "message": "x"},
        {"category": "CalculationConsistencyError",
         "field": "ebitda_equals_ebit_plus_da", "message": "x"},
        {"category": "MissingRequiredFieldError",
         "field": "revenue", "message": "x"},
        {"category": "CalculationConsistencyError",
         "field": "net_debt_equals_debt_minus_cash", "message": "x"},
    ]
    tier_c = extract_tier_c_violations(violations)
    assert len(tier_c) == 2
    fields = {v["field"] for v in tier_c}
    assert fields == {"accounting_equation_a_eq_l_plus_e", "revenue"}


def test_extract_tier_c_violations_handles_empty_input():
    from aletheia.calculations._schema_contract import (
        extract_tier_c_violations,
    )
    assert extract_tier_c_violations([]) == []
    assert extract_tier_c_violations(None) == []


# ─────────────────────────────────────────────────────────────────────
# Stage 3 boundary enforcement
# ─────────────────────────────────────────────────────────────────────

def _make_record_with_violation(
    ticker: str = "NVDA",
    fiscal_year: int = 2024,
    tier_c_field: str = "accounting_equation_a_eq_l_plus_e",
):
    """Construct a record carrying one tier-C violation. NVDA is a
    universe-known ticker so classification resolves without issue."""
    from aletheia.contracts.pipeline import (
        ValidatedCleanedRecord, ValidationReceipt,
    )
    return ValidatedCleanedRecord(
        ticker=ticker,
        fiscal_year=fiscal_year,
        period="FY",
        period_end_date=f"{fiscal_year}-12-31",
        raw={"Revenue": 100_000.0},
        clean={"Revenue": 100_000.0, "NormalizedEBIT": 20_000.0},
        derived={"EBITDA": 25_000.0, "FCF": 18_000.0},
        overall_quality_score=0.95,
        cleaning_warnings=[],
        blocking_errors=[],
        validation=ValidationReceipt(
            schema_violations=[{
                "category": "CalculationConsistencyError",
                "field": tier_c_field,
                "value": "x", "expected": "y",
                "message": f"synthetic tier-C: {tier_c_field}",
            }],
        ),
        record_fingerprint="fp-test",
        input_bundle_fingerprint="bundle-test",
        cleaned_at=datetime.now(timezone.utc),
        pipeline_version="test-tier-c",
    )


def test_stage3_blocks_on_balance_sheet_equation_violation():
    """A ≠ L + E is a truly invalid state — Stage 3 must refuse."""
    from aletheia.pipeline.stage3_calculate import (
        run_stage3, Stage3InputError,
    )
    record = _make_record_with_violation(
        tier_c_field="accounting_equation_a_eq_l_plus_e",
    )
    with pytest.raises(Stage3InputError, match="tier-C"):
        run_stage3([record], pipeline_version="test")


def test_stage3_blocks_on_missing_required_field():
    """Missing Tier-1 required field is a truly invalid state."""
    from aletheia.pipeline.stage3_calculate import (
        run_stage3, Stage3InputError,
    )
    from aletheia.contracts.pipeline import (
        ValidatedCleanedRecord, ValidationReceipt,
    )
    record = ValidatedCleanedRecord(
        ticker="NVDA", fiscal_year=2024, period="FY",
        period_end_date="2024-12-31",
        raw={}, clean={}, derived={},
        overall_quality_score=0.0,
        cleaning_warnings=[], blocking_errors=[],
        validation=ValidationReceipt(
            schema_violations=[{
                "category": "MissingRequiredFieldError",
                "field": "revenue",
                "value": None, "expected": "non-null",
                "message": "synthetic missing-required",
            }],
        ),
        record_fingerprint="fp-test",
        input_bundle_fingerprint="bundle-test",
        cleaned_at=datetime.now(timezone.utc),
        pipeline_version="test-tier-c",
    )
    with pytest.raises(Stage3InputError, match="tier-C"):
        run_stage3([record], pipeline_version="test")


def test_stage3_allows_tier_w_identity_drifts():
    """Identity drift (EBITDA, net_debt, FCF) should NOT block. The
    record flows through to the calc engines; warnings surface via
    the accounting_identities audit."""
    from aletheia.pipeline.stage3_calculate import (
        run_stage3, Stage3InputError,
    )
    record = _make_record_with_violation(
        tier_c_field="ebitda_equals_ebit_plus_da",  # tier-W
    )
    # Should not raise Stage3InputError (might raise other errors due to
    # the synthetic minimal record, but NOT the tier-C block).
    try:
        run_stage3([record], pipeline_version="test")
    except Stage3InputError as e:
        if "tier-C" in str(e):
            pytest.fail(
                f"Stage 3 incorrectly blocked on tier-W violation: {e}"
            )
        # Other Stage3InputErrors (e.g. unknown ticker) are fine for
        # this test; we only care that tier-W doesn't trigger the
        # tier-C gate.
    except Exception:
        # Calc engines may raise on the synthetic minimal record; not
        # our concern — we only care that the tier-C gate didn't fire.
        pass


def test_stage3_error_message_includes_override_path():
    """The block message must direct the analyst to the OVERRIDES path
    so they know how to add a waiver."""
    from aletheia.pipeline.stage3_calculate import (
        run_stage3, Stage3InputError,
    )
    record = _make_record_with_violation()
    with pytest.raises(Stage3InputError) as exc_info:
        run_stage3([record], pipeline_version="test")
    assert "_overrides" in str(exc_info.value).lower() or "override" in str(exc_info.value).lower()
