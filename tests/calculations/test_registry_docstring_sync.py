"""Registry ↔ docstring sync — keeps the documentation layer aligned
with the implementation.

The derivation_registry is the methodology source of truth for
analysts (what does this number MEAN, what convention, what
divergences are documented). The central formulas package is the
runtime source of truth (what does this code COMPUTE). They live in
separate files and could drift over time.

This test asserts: for every name in
``aletheia.calculations.formulas.__all__`` that ALSO has a registry
entry under the same name, the registry's ``formula`` field must
match the function's docstring first line (whitespace-normalized).

The test deliberately does NOT require every centralized function to
have a registry entry. Some primitives (``gross_debt``,
``liquid_assets``, helpers like ``cash_conversion_ratio``) don't need
analyst-facing methodology docs; they're construction details. The
test catches drift only where the doc-implementation relationship is
explicit — i.e. where a registry entry already exists.

Adding new registry entries is encouraged but not enforced. Pairing
this with the architecture-lock test
(``test_single_formula_source.py``) gives complete protection: lock
prevents fragmentation; sync prevents documentation drift.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = (
    REPO_ROOT / "aletheia" / "calculations" / "derivation_registry.py"
)


def _normalize(text: str) -> str:
    """Strip whitespace + collapse internal spaces. Lets the test
    accept ``"a + b"`` and ``"a+b"`` as the same formula."""
    return re.sub(r"\s+", " ", text.strip())


def _registry_formulas() -> dict:
    """Parse the registry file via AST and return
    ``{entry_name: formula_string}`` for every entry that has a
    string-literal ``formula`` field."""
    tree = ast.parse(REGISTRY_PATH.read_text(encoding="utf-8"))
    entries = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "DerivationEntry":
            continue
        name = None
        formula = None
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                name = kw.value.value
            elif kw.arg == "formula" and isinstance(kw.value, ast.Constant):
                formula = kw.value.value
        if name and formula:
            entries[name] = formula
    return entries


def _central_functions() -> dict:
    """Return ``{name: function}`` for every symbol in the central
    formulas package's ``__all__``."""
    from aletheia.calculations import formulas
    return {
        name: getattr(formulas, name)
        for name in formulas.__all__
    }


def test_registry_formula_matches_docstring_first_line():
    """For every central function that has a registry entry under
    the same name, the registry's ``formula`` field must match the
    function's docstring first line (whitespace-normalized).

    A mismatch usually means one side was updated without the other —
    either the implementation changed and the registry wasn't, or a
    new convention was documented in the registry but the code still
    runs the old formula.
    """
    registry = _registry_formulas()
    central = _central_functions()

    overlap = set(registry.keys()) & set(central.keys())
    assert overlap, (
        "No overlap between registry entries and central formula "
        "names — either the registry is empty or the central package "
        "was renamed. The sync test can't run."
    )

    mismatches = []
    for name in sorted(overlap):
        fn = central[name]
        doc = inspect.getdoc(fn) or ""
        if not doc:
            mismatches.append(
                f"  {name}: function has no docstring (registry "
                f"formula: {registry[name]!r})"
            )
            continue
        first_line = doc.splitlines()[0]
        if _normalize(first_line) != _normalize(registry[name]):
            mismatches.append(
                f"  {name}:\n"
                f"    registry  : {registry[name]}\n"
                f"    docstring : {first_line}"
            )

    assert not mismatches, (
        "Registry ↔ docstring drift detected. The registry's "
        "``formula`` field must match the function's docstring "
        "first line so analysts and engineers see the same "
        "definition. Update whichever side is wrong:\n\n"
        + "\n".join(mismatches)
    )
