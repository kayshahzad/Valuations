"""Verifies the compat-wrapper deprecation contract for
``aletheia.workflow.graph.create_workflow``.

Per decision #3 in docs/pipeline_contracts.md, the legacy entry point
stays in place but emits a ``DeprecationWarning`` on every call. The
function must continue to return a usable LangGraph object until the
6-month deprecation window closes — only the warning is new.
"""

from __future__ import annotations

import warnings

import pytest


def test_create_workflow_emits_deprecation_warning():
    """Each call emits a DeprecationWarning that mentions the new
    orchestrator path so the operator can migrate without going
    spelunking through docs."""
    from aletheia.workflow.graph import create_workflow

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        create_workflow()

    deprecation_warnings = [
        w for w in captured if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecation_warnings, (
        "create_workflow() must emit a DeprecationWarning; got "
        f"{[type(w.message).__name__ for w in captured]}"
    )
    # The message must point migrators at the new entry point. Don't
    # over-specify wording — just verify the breadcrumbs are present.
    msg = str(deprecation_warnings[0].message)
    assert "aletheia.pipeline" in msg
    assert "Orchestrator" in msg or "pipeline run" in msg


def test_create_workflow_still_returns_a_runnable_graph():
    """Compat is real compat: the function continues to return a
    LangGraph object during the deprecation window. We don't run
    the graph (that needs a populated state + agent layer), just
    confirm the object is constructible."""
    from aletheia.workflow.graph import create_workflow

    with warnings.catch_warnings():
        # Silence the deprecation warning in this test so it doesn't
        # pollute the test report — the *other* test already asserts
        # the warning fires.
        warnings.simplefilter("ignore", DeprecationWarning)
        graph = create_workflow()

    assert graph is not None
    # LangGraph compiled graphs expose .invoke() / .stream() — checking
    # that the object has at least one of them protects against an
    # accidental refactor that returns the wrong type.
    assert hasattr(graph, "invoke") or hasattr(graph, "stream"), (
        f"create_workflow() returned {type(graph).__name__!r}; expected "
        "a compiled LangGraph object exposing .invoke or .stream"
    )
