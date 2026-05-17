"""Architectural lock — prevents resurrection of removed agents.

The agent-consolidation cleanup (Week 0.5 of the agent-layer refactor)
removed four agent files that were dead code or anti-patterns:

  - `fundamentalist.py`  — legacy ProForma DCF path; superseded by
                            calc_node + DCFEngine. Its `valuation_report`
                            output was unit-confused (EV labelled as
                            intrinsic_value) and produced silent vacuous
                            PASSes in lead.py's compliance checks.
  - `valuation_node.py`  — deprecated shim that delegated to calc_node.
                            Its earlier incarnation read agent state and
                            mutated DCF inputs (calc/agent separation
                            violation).
  - `intake.py`          — `intake_agent` was a no-op (returned empty
                            `serving_base`); the 270 lines of
                            EconomicRealityTranslator class definitions
                            were unreachable.
  - `contrarian.py` v1   — superseded by `contrarian_v2.py`.

This test ensures none of those files come back. If a future engineer
accidentally re-creates one (e.g. via `git revert`, refactor that
restores a deleted file, or copy-paste from another project), the build
fails loudly so the architectural decision can be re-litigated rather
than silently undone.

Same enforcement pattern as `test_no_agent_emitted_overrides.py`.
"""

from pathlib import Path


# Filenames that must NOT exist in `aletheia/agents/`. Names here are the
# agent files removed in the consolidation cleanup. Adding to this list
# only after PR review documenting why a previously-active agent is now
# permanently retired.
REMOVED_AGENT_FILES = frozenset({
    # Week 0.5 cleanup
    "fundamentalist.py",
    "valuation_node.py",
    "intake.py",
    "contrarian.py",   # v1; v2 lives in contrarian_v2.py
    # Week 1.5 consolidation — replaced by qualitative_synthesis.py
    # (single LLM call instead of three; schemas + DB helpers preserved
    # in qualitative_sections.py for the new agent to use).
    "forensic.py",
    "value_chain.py",
    "context.py",
})


def test_no_resurrected_agent_files():
    """Removed agent files must stay removed."""
    repo_root = Path(__file__).resolve().parents[2]
    agents_dir = repo_root / "aletheia" / "agents"

    resurrected = []
    for name in REMOVED_AGENT_FILES:
        if (agents_dir / name).exists():
            resurrected.append(name)

    assert not resurrected, (
        "Resurrected agent file(s) detected — these were removed in the "
        "agent-consolidation cleanup and must not return without an "
        "explicit decision documented in PR review:\n  "
        + "\n  ".join(resurrected)
    )


def test_no_imports_of_removed_agents():
    """No source file in the live codebase may import the removed agents.

    Excludes `scripts/archive/` and `tests/architecture/` (this file
    references the names in strings; that's intentional and acceptable
    because we're string-matching to detect the file names, not
    importing them)."""
    repo_root = Path(__file__).resolve().parents[2]
    forbidden_imports = (
        # Week 0.5
        "from aletheia.agents.fundamentalist",
        "from aletheia.agents.valuation_node",
        "from aletheia.agents.intake",
        # contrarian v1: can import as `from aletheia.agents.contrarian import`
        # but NOT as `from aletheia.agents.contrarian_v2 import` — guard the
        # exact pattern that targets the v1 file.
        "from aletheia.agents.contrarian import",
        "import aletheia.agents.fundamentalist",
        "import aletheia.agents.valuation_node",
        "import aletheia.agents.intake",
        # Week 1.5 — narrative-agent consolidation
        "from aletheia.agents.forensic import",
        "from aletheia.agents.value_chain import",
        "from aletheia.agents.context import",
        "import aletheia.agents.forensic",
        "import aletheia.agents.value_chain",
        "import aletheia.agents.context",
    )

    # "archive/" is the canonical quarantine for removed modules — see
    # archive/README.md. By design it contains the old imports verbatim;
    # excluding it lets us preserve historical reference without
    # tripping the no-resurrection gate.
    excluded_dirs = {"archive", "scripts/archive", "__pycache__", ".venv", ".git"}

    violations: list[str] = []
    for path in repo_root.rglob("*.py"):
        rel = str(path.relative_to(repo_root))
        if any(ex in rel for ex in excluded_dirs):
            continue
        # Skip this test file itself
        if rel == "tests/architecture/test_no_resurrected_agents.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for forbidden in forbidden_imports:
                if forbidden in stripped:
                    violations.append(f"{rel}:{line_no}: {stripped}")

    assert not violations, (
        "Forbidden import(s) of removed agents — the agent-consolidation "
        "cleanup removed these modules. Update callers to use the live "
        "agents (calc_node for valuation/intake roles; contrarian_v2 for "
        "contrarian):\n  "
        + "\n  ".join(violations)
    )
