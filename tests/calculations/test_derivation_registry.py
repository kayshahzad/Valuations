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


def test_trace_value_returns_none_for_unknown_entry():
    from aletheia.calculations.derivation_registry import trace_value
    assert trace_value("nonexistent_name", {}) is None


def test_trace_value_resolves_inputs_from_dcf_subbundle():
    """trace_value should find DCF-side inputs (NOPAT, EBIT, wacc) when
    they live in the bundle's dcf sub-dict."""
    from aletheia.calculations.derivation_registry import trace_value
    bundle = {
        "dcf": {
            "nopat":   75_932_000_000.0,
            "ebit":    83_280_000_000.0,
            "roic":    0.311,
            "wacc_base": 0.0984,
            "fcf":     46_109_000_000.0,
        },
    }
    trace = trace_value("nopat", bundle)
    assert trace is not None
    assert trace["result"] == 75_932_000_000.0
    # Inputs declared in registry: ["NormalizedEBIT", "tax_rate"]. The
    # alias map should resolve "NormalizedEBIT" → dcf.ebit.
    input_dict = dict(trace["input_values"])
    assert input_dict.get("NormalizedEBIT") == 83_280_000_000.0


def test_trace_value_marks_unresolvable_inputs_as_upstream():
    """When an input doesn't appear in any bundle sub-dict, the trace
    marks it as '<from upstream>' rather than failing."""
    from aletheia.calculations.derivation_registry import trace_value
    bundle = {"dcf": {"fcf": 46_109_000_000.0}}  # missing OperatingCF / CapEx
    trace = trace_value("fcf", bundle)
    assert trace is not None
    assert trace["result"] == 46_109_000_000.0
    input_dict = dict(trace["input_values"])
    # Neither OperatingCF nor CapEx_Total in bundle — both should be
    # marked as upstream placeholder
    assert input_dict.get("OperatingCF") == "<from upstream>"
    assert input_dict.get("CapEx_Total") == "<from upstream>"


def test_trace_value_carries_methodology_and_category_d():
    """The trace surfaces the registry entry's methodology + category_d
    flag so the UI can render the right chips."""
    from aletheia.calculations.derivation_registry import trace_value
    trace = trace_value("fcf", {"dcf": {"fcf": 46_109_000_000.0}})
    assert trace is not None
    assert "Damodaran" in trace["methodology"]
    assert trace["category_d"] is True
    assert trace["fmp_equivalent"]  # FCF has documented FMP divergence


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
