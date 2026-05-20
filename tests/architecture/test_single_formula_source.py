"""Architecture lock — prevents formula re-fragmentation.

The centralization refactor (Phases 1-4) consolidated 27 financial
formulas into ``aletheia/calculations/formulas/``. Future drift would
re-introduce the kind of cross-provider bugs that motivated the
refactor (e.g. GOOGL ROIC FMP=21.44% vs XBRL=12.00% caused by
``invested_capital`` having two different implementations).

This test enforces two rules at the AST level:

  **Rule 1 — single definition site.** Any function whose name
  appears in ``aletheia.calculations.formulas.__init__.__all__`` may
  ONLY be defined inside the ``aletheia/calculations/formulas/``
  package. Redefining one of these names anywhere else in the live
  codebase triggers the test.

  **Rule 2 — public-surface-only imports.** Outside the formulas
  package, callers must import from the package root
  (``from aletheia.calculations.formulas import X``), not from
  submodules. This keeps the public surface explicit and prevents
  callers from depending on internal module layout.

Same enforcement pattern as ``test_no_resurrected_agents.py``. The
test is fast (pure file scan + ast parse, no execution) and offers no
escape hatch — adding to the central package is the only way to add a
formula.
"""

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FORMULAS_PACKAGE = REPO_ROOT / "aletheia" / "calculations" / "formulas"

# Directories that are NOT part of the live codebase — quarantined or
# tooling. Same exclusion convention as test_no_resurrected_agents.
_EXCLUDED_DIRS = {
    "archive",
    "scripts/archive",
    "__pycache__",
    ".venv",
    ".git",
    # Tests themselves get a pass — fixtures may need to define
    # local helpers that share names with central formulas without
    # implying production behavior.
    "tests",
}


def _load_central_formula_names() -> set[str]:
    """Read ``__all__`` from the formulas package's ``__init__.py``
    and return the canonical formula-name set."""
    init_path = FORMULAS_PACKAGE / "__init__.py"
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, ast.List):
                        names = set()
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                names.add(elt.value)
                        return names
    raise AssertionError(
        "Could not parse __all__ from formulas/__init__.py — the "
        "architecture lock can't run without it."
    )


def _walk_live_python_files():
    """Yield every ``.py`` file in the live codebase, skipping
    excluded directories and the formulas package itself."""
    for path in REPO_ROOT.rglob("*.py"):
        rel = str(path.relative_to(REPO_ROOT))
        if any(ex in rel for ex in _EXCLUDED_DIRS):
            continue
        # Formulas package itself: allowed to define the names
        if str(path).startswith(str(FORMULAS_PACKAGE)):
            continue
        yield path


def test_no_formula_redefinition_outside_central_package():
    """Rule 1 — every formula in ``__all__`` may be defined only
    inside ``aletheia/calculations/formulas/``. AST-walks every live
    .py file and asserts no module-level ``def <formula_name>``
    matches the central list outside the package.

    Only **top-level** function definitions are checked. Class
    methods and ``@property`` accessors that happen to share a name
    (e.g. ``DCFResult.wacc`` returning the pre-computed ``wacc_base``
    attribute) are descriptors, not formula implementations — they
    don't fragment the source of truth.
    """
    central_names = _load_central_formula_names()
    violations: list[str] = []

    for path in _walk_live_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            # Skip files we can't parse — they have other problems
            continue

        # Inspect only module-level (top-level) function definitions.
        # Skipping nested defs catches the architectural concern (a
        # parallel formula implementation) while leaving methods +
        # accessors alone.
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in central_names:
                    rel = path.relative_to(REPO_ROOT)
                    violations.append(
                        f"{rel}:{node.lineno}: def {node.name}(...) "
                        f"— move to aletheia/calculations/formulas/ "
                        f"or rename"
                    )

    assert not violations, (
        "Architecture violation: financial-formula names redefined "
        "outside the central formulas package. The centralization "
        "refactor requires all 27 formulas to live in exactly one "
        "place — adding new definitions of these names anywhere else "
        "would silently fragment the source of truth.\n\n"
        + "\n".join(violations)
    )


def test_no_submodule_imports_outside_central_package():
    """Rule 2 — callers outside the package must import from the
    package root (``from aletheia.calculations.formulas import X``),
    not from submodules (``from aletheia.calculations.formulas.ratios
    import roic``). Keeps the public surface explicit so future
    reorganization of the internal module layout doesn't break
    callers."""
    violations: list[str] = []
    forbidden_prefix = "aletheia.calculations.formulas."

    for path in _walk_live_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                # Catch ``import aletheia.calculations.formulas.ratios``
                for alias in node.names:
                    if alias.name.startswith(forbidden_prefix):
                        rel = path.relative_to(REPO_ROOT)
                        violations.append(
                            f"{rel}:{node.lineno}: import {alias.name}"
                        )
                continue

            if module and module.startswith(forbidden_prefix):
                rel = path.relative_to(REPO_ROOT)
                violations.append(
                    f"{rel}:{node.lineno}: from {module} import ..."
                )

    assert not violations, (
        "Architecture violation: submodule imports of the central "
        "formulas package detected. Callers must import from the "
        "package root so the public __all__ surface stays canonical.\n\n"
        "Wrong:  from aletheia.calculations.formulas.ratios import roic\n"
        "Right:  from aletheia.calculations.formulas import roic\n\n"
        + "\n".join(violations)
    )
