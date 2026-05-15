"""Tests for the Layer-2 derivation registry.

The registry catalogues every Stage 3 derived value's inputs, formula,
methodology, alternates, and FMP divergence note. These tests verify:
  - basic lookup helpers
  - all the predicted FMP-drift rows have registry entries
  - Category-D flag is set where methodology divergence is expected
  - registry shape is well-formed (no missing required fields)
"""

from __future__ import annotations

import pytest


def test_registry_size_meets_minimum():
    """Phase-5 prediction was ≥25 entries; we expect 25-35."""
    from aletheia.calculations.derivation_registry import DERIVATIONS
    assert len(DERIVATIONS) >= 25, f"only {len(DERIVATIONS)} entries"


def test_lookup_by_name_and_label():
    from aletheia.calculations.derivation_registry import (
        lookup, lookup_by_label,
    )
    e_by_name = lookup("fcf")
    e_by_label = lookup_by_label("FCF")
    assert e_by_name is not None
    assert e_by_label is not None
    assert e_by_name is e_by_label  # same registry object


def test_lookup_returns_none_for_unknown():
    from aletheia.calculations.derivation_registry import (
        lookup, lookup_by_label,
    )
    assert lookup("NONEXISTENT_KEY") is None
    assert lookup_by_label("Bogus Label") is None


@pytest.mark.parametrize("label", [
    "FCF",
    "ROIC",
    "IV per share (base)",
    "Market P/E",
    "P/E",            # Screening P/E (Cat-D vs FMP TTM convention)
    "P/B",            # Screening P/B (Cat-D book-value definition)
    "Current EV / EBITDA",
])
def test_predicted_drift_labels_have_registry_entries(label):
    """Phase-2 validation criterion: every observed FMP drift > 5% on
    the Stage 3 panel MUST have a documented registry entry. This is
    the no-orphan-drift principle."""
    from aletheia.calculations.derivation_registry import lookup_by_label
    entry = lookup_by_label(label)
    assert entry is not None, f"{label!r} has no registry entry"
    # Every drift row needs either fmp_equivalent OR an alternates list
    # (so the analyst can read WHY the divergence exists)
    assert entry.fmp_equivalent or entry.alternates, (
        f"{label!r} has no FMP divergence note and no alternates"
    )


def test_category_d_entries_have_fmp_divergence_note():
    """Category-D = expected divergence. Must have an fmp_equivalent
    explaining WHY the value diverges from FMP."""
    from aletheia.calculations.derivation_registry import (
        category_d_entries,
    )
    for entry in category_d_entries():
        assert entry.fmp_equivalent is not None, (
            f"{entry.label}: category_d=True but no fmp_equivalent note"
        )


def test_every_entry_has_required_fields():
    """Schema check: every registry entry has non-empty
    name/label/category/formula/methodology fields."""
    from aletheia.calculations.derivation_registry import DERIVATIONS
    for e in DERIVATIONS:
        assert e.name, f"empty name in {e!r}"
        assert e.label, f"empty label in {e!r}"
        assert e.category, f"empty category in {e!r}"
        assert e.formula, f"empty formula in {e!r}"
        assert e.methodology, f"empty methodology in {e!r}"


def test_entries_by_category():
    from aletheia.calculations.derivation_registry import (
        entries_by_category,
    )
    dcf_entries = entries_by_category("DCF")
    assert len(dcf_entries) >= 5, (
        f"DCF category only has {len(dcf_entries)} entries"
    )
    cleaning_entries = entries_by_category("Cleaning")
    assert len(cleaning_entries) >= 4


def test_no_duplicate_names_or_labels():
    from aletheia.calculations.derivation_registry import DERIVATIONS
    names = [e.name for e in DERIVATIONS]
    labels = [e.label for e in DERIVATIONS]
    assert len(names) == len(set(names)), (
        f"duplicate canonical names: {[n for n in names if names.count(n) > 1]}"
    )
    assert len(labels) == len(set(labels)), (
        f"duplicate labels: {[l for l in labels if labels.count(l) > 1]}"
    )
