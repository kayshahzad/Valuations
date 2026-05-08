"""Architectural lock — thesis_synthesizer is integration, not analysis.

The thesis_synthesizer agent's role is to STRUCTURE upstream signals
into an investment thesis — not to produce parallel analysis. To prevent
drift toward "let's just have thesis_synthesizer also check the 10-K /
search the web / query the DB," this test scans the agent's imports
and rejects any data-fetching primitive at module load time.

If a future change genuinely needs new data, the path is:
  - Add the data to an upstream agent (calc_node, qualitative_synthesis,
    contrarian_v2) and surface it as a structured field
  - Cite the new field in thesis_synthesizer's `cited_signals`

Never by adding direct DB / search / web access to thesis_synthesizer
itself.

Pairs with the schema-level `cited_signals` enforcement and the
runtime vocabulary-regex check inside the agent. The three layers
together operationalize "we want integration discipline."
"""

import ast
from pathlib import Path


# Imports that data-analysis agents use but thesis_synthesizer must not.
# Match any of: a full module path, a name imported from a module, or
# a top-level module name.
FORBIDDEN_IMPORT_FRAGMENTS = (
    # Web search
    "DuckDuckGoSearchRun",
    "search_sentiment",
    "duckduckgo_search",
    "langchain_community.tools",
    # Direct DB access (must read from upstream state, not query)
    "InvestmentDatabase",
    "duckdb",
    "aletheia.data.database",
    # External market data
    "yfinance",
    "aletheia.data.market_data",
    # 10-K text (must read from state['raw_10k_text'] if needed, but the
    # agent's role does not require it — it consumes pre-extracted
    # qualitative signals from qualitative_synthesis instead)
    "edgar",
    "aletheia.tools.search",
    # Calc-layer engines (thesis cites their outputs, doesn't run them)
    "DCFEngine",
    "ReverseDCF",
    "MultipleDecomposition",
    "ConvictionScorer",
    "calculate_z_score",
)


def _module_imports(path: Path) -> set[str]:
    """Return every imported name (module path + names) in a Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            seen.add(module)
            for alias in node.names:
                seen.add(f"{module}.{alias.name}")
                seen.add(alias.name)
    return seen


def test_thesis_synthesizer_does_not_import_data_fetchers():
    """thesis_synthesizer.py must not import any data-fetching primitive."""
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / "aletheia" / "agents" / "thesis_synthesizer.py"
    assert target.exists(), f"thesis_synthesizer.py not found at {target}"

    imports = _module_imports(target)

    violations: list[str] = []
    for fragment in FORBIDDEN_IMPORT_FRAGMENTS:
        for imp in imports:
            if fragment in imp:
                violations.append(f"{fragment!r} appears in import {imp!r}")

    assert not violations, (
        "thesis_synthesizer is INTEGRATION-ONLY — it must consume upstream "
        "signals via state, not produce parallel analysis. Adding a data-"
        "fetching primitive defeats the architectural separation. "
        "If new data is needed, surface it from an upstream agent and cite "
        "it in cited_signals.\n  "
        + "\n  ".join(violations)
    )


def test_thesis_synthesizer_uses_state_only_for_inputs():
    """Spot-check: the agent function signature should accept a state dict
    and read all inputs from it, not from external sources."""
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / "aletheia" / "agents" / "thesis_synthesizer.py"
    src = target.read_text(encoding="utf-8")

    # The agent function must exist and take a single `state` parameter
    assert "def thesis_synthesizer_agent(state" in src, (
        "thesis_synthesizer_agent must accept a `state` dict as its first arg"
    )

    # The summary helpers must read from `state.get(...)` — they're the
    # canonical input-extraction pattern. Verifies no external data fetch.
    expected_state_reads = [
        "state.get(\"phase2_valuation\")",
        "state.get(\"forensic_report\")",
        "state.get(\"value_chain_report\")",
        "state.get(\"strategic_context_report\")",
        "state.get(\"contrarian_report\")",
        "state.get(\"scenario_results\")",
        "state.get(\"conviction\")",
        # Dashboard projection populated by dashboard_fetch_node — added
        # in the week-6 wiring change. The synthesizer itself never
        # touches the DB; it consumes the projected state only.
        "state.get(\"qualitative_dashboard\")",
    ]
    missing = [r for r in expected_state_reads if r not in src]
    assert not missing, (
        "thesis_synthesizer must consume upstream signals via state. "
        f"Missing state reads: {missing}"
    )
