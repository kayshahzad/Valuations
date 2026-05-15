"""Tests for the Calculation Framework view.

The view surfaces L1+L2+L3 + exception categories pulled directly
from the calc-layer modules. Tests verify:
  - module imports cleanly
  - the catalogs are non-empty (sanity check the references)
  - render_calculation_framework doesn't crash on the mocked Streamlit
"""

from __future__ import annotations

from typing import Any, Dict, List


class _StMock:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.session_state: Dict[str, Any] = {}

    def __getattr__(self, name):
        if name == "session_state":
            return self.__dict__["session_state"]

        def _capture(*args, **kwargs):
            self.calls.append({"fn": name, "args": args, "kwargs": kwargs})
            if name == "columns":
                n = args[0] if args else 1
                if isinstance(n, list):
                    n = len(n)
                return [_StMock() for _ in range(n)]
            if name == "tabs":
                tabs = args[0] if args else []
                class _Ctx:
                    def __enter__(self_inner): return _StMock()
                    def __exit__(self_inner, *a): return False
                return [_Ctx() for _ in tabs]
            if name == "expander":
                class _Ctx:
                    def __enter__(self_inner): return _StMock()
                    def __exit__(self_inner, *a): return False
                return _Ctx()
            if name == "button":
                return False
            if name == "multiselect":
                return kwargs.get("default", [])
            return None
        return _capture


def _install_mock(monkeypatch) -> _StMock:
    mock = _StMock()
    import aletheia.ui.calculation_framework_view as mod
    monkeypatch.setattr(mod, "st", mock)
    return mock


def test_l1_identities_catalog_non_empty():
    from aletheia.ui.calculation_framework_view import _L1_IDENTITIES
    assert len(_L1_IDENTITIES) >= 7  # 7 foundational identities


def test_l1_identity_entries_have_required_fields():
    """Every L1 entry must have id/name/formula/enforcement/rationale."""
    from aletheia.ui.calculation_framework_view import _L1_IDENTITIES
    for entry in _L1_IDENTITIES:
        for field in ("id", "name", "formula", "enforcement", "rationale"):
            assert entry.get(field), (
                f"L1 entry missing {field}: {entry}"
            )


def test_l1_identity_ids_match_tolerance_thresholds():
    """Every L1 id should resolve to a tolerance value in the live
    TOLERANCE_THRESHOLDS dict. Catches drift between the docs catalog
    and the actual identity_checks module."""
    from aletheia.ui.calculation_framework_view import _L1_IDENTITIES
    from aletheia.calculations.identity_checks import TOLERANCE_THRESHOLDS
    for entry in _L1_IDENTITIES:
        assert entry["id"] in TOLERANCE_THRESHOLDS, (
            f"L1 catalog id {entry['id']!r} has no tolerance entry"
        )


def test_exception_categories_catalog_non_empty():
    from aletheia.ui.calculation_framework_view import _EXCEPTION_CATEGORIES
    assert len(_EXCEPTION_CATEGORIES) >= 30


def test_exception_categories_have_required_fields():
    from aletheia.ui.calculation_framework_view import _EXCEPTION_CATEGORIES
    for c in _EXCEPTION_CATEGORIES:
        for field in ("category", "identity", "semantic"):
            assert c.get(field), f"Exception entry missing {field}: {c}"


def test_render_does_not_crash(monkeypatch):
    """The view must render without raising on every section."""
    mock = _install_mock(monkeypatch)
    from aletheia.ui.calculation_framework_view import (
        render_calculation_framework,
    )
    render_calculation_framework()
    # Confirm it rendered multiple sections (each calls markdown / metric)
    markdowns = [c for c in mock.calls if c["fn"] == "markdown"]
    assert len(markdowns) >= 10, (
        f"render produced only {len(markdowns)} markdown calls"
    )


def test_render_does_not_crash_on_empty_filters(monkeypatch):
    """Verify the view handles the no-filter (default) case cleanly."""
    mock = _install_mock(monkeypatch)
    from aletheia.ui.calculation_framework_view import (
        render_calculation_framework,
    )
    # multiselect returns [] when default=[]; the render code must
    # treat that as "show everything" without crashing.
    render_calculation_framework()
    # No filter dataframes should still produce table rows.
    dataframes = [c for c in mock.calls if c["fn"] == "dataframe"]
    assert dataframes, "expected at least one dataframe render"
    # Every dataframe should be non-empty (catalogs have entries).
    for d in dataframes:
        rows = d["args"][0]
        if isinstance(rows, list):
            assert rows, "dataframe rendered with empty rows"
