"""
Architectural lock — prevents the calc/agent separation from regressing.

The DCF engine and other calc tools previously accepted kwargs that let agent
narrative mutate calc-layer math (wacc_penalty, growth_decay_reduction,
base_revenue_override, terminal_growth_adj, max_growth_rate, wacc_override,
terminal_growth_cap). Those kwargs were stripped from the calc layer.

This test ensures the agent layer doesn't quietly reintroduce them by emitting
the same field names in agent output dicts. If any agent module references one
of these names — as a dict key, attribute, or kwarg — the build fails.

If you genuinely need an agent to influence calc, the path is:
  - Add a typed bounded field to ScenarioOverride (Phase C), or
  - Encode the situation as a KNOWN_ISSUES entry with field/year scoping
Never via an agent-emitted override field.

Same shape as test_no_config_imports_in_calc_layer.py.
"""
import ast
from pathlib import Path

# Forbidden field names. Match the set we removed from calc tools.
FORBIDDEN_OVERRIDE_NAMES = frozenset({
    "wacc_penalty",
    "wacc_override",
    "growth_decay_reduction",
    "base_revenue_override",
    "terminal_growth_adj",
    "terminal_growth_cap",
    "max_growth_rate",
})

# Files that are exempt from the scan. Empty since the agent-consolidation
# cleanup removed `fundamentalist`, `valuation_node`, `intake`, and
# `contrarian` v1. Every remaining agent file must comply with the lock.
# Add new exempt paths only after PR review.
EXEMPT_FILES = frozenset({
    # (none currently — all agent files must comply)
})


def _find_violations(tree: ast.AST, source_path: str) -> list[str]:
    """Walk an agent module's AST looking for forbidden override names."""
    violations: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_Constant(self, node):
            # Any string literal matching a forbidden name (typical dict-key usage)
            if isinstance(node.value, str) and node.value in FORBIDDEN_OVERRIDE_NAMES:
                violations.append(
                    f"{source_path}:{node.lineno}: forbidden override name "
                    f"'{node.value}' appears as a string literal"
                )
            self.generic_visit(node)

        def visit_Attribute(self, node):
            if node.attr in FORBIDDEN_OVERRIDE_NAMES:
                violations.append(
                    f"{source_path}:{node.lineno}: forbidden override name "
                    f"'.{node.attr}' appears as an attribute access"
                )
            self.generic_visit(node)

        def visit_keyword(self, node):
            if node.arg and node.arg in FORBIDDEN_OVERRIDE_NAMES:
                violations.append(
                    f"{source_path}:{node.lineno}: forbidden override name "
                    f"'{node.arg}=' appears as a keyword argument"
                )
            self.generic_visit(node)

        def visit_Name(self, node):
            if node.id in FORBIDDEN_OVERRIDE_NAMES:
                violations.append(
                    f"{source_path}:{node.lineno}: forbidden override name "
                    f"'{node.id}' appears as an identifier"
                )
            self.generic_visit(node)

    Visitor().visit(tree)
    return violations


def test_no_agent_emitted_overrides():
    """Agent layer must not emit calc-mutating override field names."""
    repo_root = Path(__file__).resolve().parents[2]
    agents_dir = repo_root / "aletheia" / "agents"

    all_violations: list[str] = []
    for path in sorted(agents_dir.rglob("*.py")):
        rel = path.relative_to(repo_root)
        if str(rel) in EXEMPT_FILES or path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            all_violations.append(f"{rel}: parse error {e}")
            continue
        all_violations.extend(_find_violations(tree, str(rel)))

    assert not all_violations, (
        "Agent-layer override violation — these break the calc/agent separation. "
        "If you genuinely need to influence calc, add a typed ScenarioOverride "
        "or a KNOWN_ISSUES entry instead.\n  "
        + "\n  ".join(all_violations)
    )
