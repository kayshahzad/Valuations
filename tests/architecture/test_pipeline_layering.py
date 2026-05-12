"""Architecture lock test for pipeline stage layering.

Enforces the dependency direction across the four pipeline stages:

    Stage 1 (ingest)    →  cannot import from validation, calc, agents
    Stage 2 (validate)  →  cannot import from calc, agents
    Stage 3 (calculate) →  cannot import from agents
    Stage 4 (agents)    →  no further restriction

This is the structural enforcement of the schema-contract boundary
discussed in docs/pipeline_contracts.md. Without this test, validation
logic eventually creeps into ingestion code, calc logic creeps into
validation, etc. — the team's discipline alone isn't enough at scale,
the import graph needs teeth.

Currently the pipeline stage modules don't exist yet (Weeks 2-5 of the
refactor create them). Tests skip until the corresponding stage module
lands. Once a stage module exists, its test enforces immediately.

Test naming convention: ``test_<stage>_does_not_import_from_<forbidden>``
so failures point straight at the violated boundary.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────────
# Forbidden-import map
# ─────────────────────────────────────────────────────────────────────
# For each stage module, the namespaces it MUST NOT import from.
# Match is by `startswith` against the imported module path, so
# `aletheia.tools` blocks anything under aletheia.tools.*
FORBIDDEN: dict[str, list[str]] = {
    "aletheia.pipeline.stage1_ingest": [
        # Stage 1 is pure raw-data fetch. No cleaning, no validation,
        # no calc, no agents. Its output is the IngestedRawBundle —
        # bytes + URLs + timestamps, that's it.
        "aletheia.calculations",
        "aletheia.data.cleaning_engine",
        "aletheia.data.ingestion_validator",
        "aletheia.tools.dcf_engine",
        "aletheia.tools.reverse_dcf",
        "aletheia.tools.multiple_decomposition",
        "aletheia.tools.screening_ratios",
        "aletheia.tools.moat_fingerprint",
        "aletheia.tools.forensic_metrics",
        "aletheia.tools.cyclicality",
        "aletheia.agents",
        "aletheia.workflow",
    ],
    "aletheia.pipeline.stage2_validate": [
        # Stage 2 owns cleaning_engine, calculations (the framework
        # primitives + schema_contract), ingestion_validator, fmp_validation.
        # It must NOT import calc-layer tools or agents.
        "aletheia.tools.dcf_engine",
        "aletheia.tools.reverse_dcf",
        "aletheia.tools.multiple_decomposition",
        "aletheia.tools.screening_ratios",
        "aletheia.tools.moat_fingerprint",
        "aletheia.tools.forensic_metrics",
        "aletheia.tools.cyclicality",
        "aletheia.agents",
        "aletheia.workflow",
    ],
    "aletheia.pipeline.stage3_calculate": [
        # Stage 3 is deterministic financial calc. No LLM agents,
        # no workflow graph, no UI.
        "aletheia.agents",
        "aletheia.workflow",
        "aletheia.ui",
    ],
    "aletheia.pipeline.stage4_agents": [
        # Stage 4 has no further forbidden imports — it's the consumer
        # of everything upstream. The empty list is intentional and
        # locked in as the explicit "no constraints" decision so
        # nobody adds restrictions silently.
    ],
}


# ─────────────────────────────────────────────────────────────────────
# Implementation
# ─────────────────────────────────────────────────────────────────────

def _module_imports(module_path: Path) -> set[str]:
    """Return every fully-qualified module name imported by `module_path`.

    Walks the AST so we catch imports inside conditionals, functions,
    and lazy-import patterns. Returns the top-level path of each import
    (e.g. ``aletheia.calculations._guards`` is recorded as such, not
    collapsed to ``aletheia.calculations``).
    """
    tree = ast.parse(module_path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.add(node.module)
    return out


def _resolve_module_files(module: str) -> list[Path]:
    """A stage module can be a single file (`stage1_ingest.py`) or a
    package (`stage1_ingest/__init__.py` + submodules). Walk both."""
    base = Path(module.replace(".", "/"))
    single_file = base.with_suffix(".py")
    package_init = base / "__init__.py"

    paths: list[Path] = []
    if single_file.exists():
        paths.append(single_file)
    if package_init.exists():
        paths.append(package_init)
        # Include submodules so e.g. stage1_ingest/sec.py is also checked
        for sub in base.rglob("*.py"):
            if sub != package_init:
                paths.append(sub)
    return paths


@pytest.mark.parametrize(
    "stage_module,forbidden_list",
    list(FORBIDDEN.items()),
    ids=lambda v: v if isinstance(v, str) else "list",
)
def test_stage_does_not_import_from_forbidden_namespaces(
    stage_module: str, forbidden_list: list[str],
) -> None:
    """Each stage module must not import from any forbidden namespace.

    Until the stage module is created (Weeks 2-5 of the refactor), this
    test skips. Once the module lands, the test enforces immediately.
    """
    files = _resolve_module_files(stage_module)
    if not files:
        pytest.skip(
            f"{stage_module} not yet created; this test enforces once "
            "the corresponding refactor week ships."
        )

    all_imports: set[str] = set()
    for f in files:
        all_imports |= _module_imports(f)

    for forbidden in forbidden_list:
        violators = sorted(imp for imp in all_imports if imp.startswith(forbidden))
        assert not violators, (
            f"\n  {stage_module}\n  imports from forbidden namespace "
            f"{forbidden!r}.\n  Specific violators:\n    "
            + "\n    ".join(violators)
            + f"\n  This violates the pipeline layering decision in "
              f"docs/pipeline_contracts.md.\n  Move the affected logic "
              f"to the appropriate stage, or refactor to call across "
              f"the contract boundary."
        )


def test_pipeline_contracts_module_is_pure_data() -> None:
    """The shared contracts module must not import from any stage,
    cleaning_engine, or calc-layer code — it's the dependency-free
    boundary type system that every stage depends on.

    If contracts.pipeline.py started pulling in calc-layer modules, the
    contracts would couple to internals and the boundary would erode."""
    contract_path = Path("aletheia/contracts/pipeline.py")
    if not contract_path.exists():
        pytest.skip("contracts/pipeline.py not yet created")

    forbidden = [
        "aletheia.calculations",
        "aletheia.data.cleaning_engine",
        "aletheia.tools",
        "aletheia.agents",
        "aletheia.workflow",
        "aletheia.pipeline",
    ]
    imports = _module_imports(contract_path)
    for ns in forbidden:
        violators = sorted(imp for imp in imports if imp.startswith(ns))
        assert not violators, (
            f"aletheia/contracts/pipeline.py imports from {ns!r}: {violators}. "
            "Contracts are the dependency-free type layer; they cannot "
            "depend on the modules they describe."
        )
