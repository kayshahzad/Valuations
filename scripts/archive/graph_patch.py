"""
graph_patch.py
==============
Patches aletheia/workflow/graph.py to:
  1. Import valuation_node and contrarian_v2
  2. Add valuation_node between fundamentalist and contrarian
  3. Replace contrarian with contrarian_v2

Also creates aletheia/state.py update if needed.

Run from project root:
    PYTHONPATH=. python3 graph_patch.py
"""

from pathlib import Path

# ── Read graph.py ─────────────────────────────────────────────────────────────
graph_path = Path("aletheia/workflow/graph.py")
if not graph_path.exists():
    print(f"✗ {graph_path} not found. Please show contents with: cat aletheia/workflow/graph.py")
    exit(1)

code = graph_path.read_text()
print(f"Read {graph_path} ({len(code)} chars)")
print()
print("Current content:")
print(code)
print()

# ── Detect import section and add new imports ────────────────────────────────
import_candidates = [
    ("from aletheia.agents.fundamentalist import fundamentalist_agent",
     "from aletheia.agents.fundamentalist import fundamentalist_agent\nfrom aletheia.agents.valuation_node import valuation_node\nfrom aletheia.agents.contrarian_v2 import contrarian_agent as contrarian_agent_v2"),
    ("from aletheia.agents.contrarian import contrarian_agent",
     "from aletheia.agents.contrarian_v2 import contrarian_agent"),
]

patched = False
for old_import, new_import in import_candidates:
    if old_import in code:
        code = code.replace(old_import, new_import, 1)
        print(f"✓ Import patched: {old_import[:60]}...")
        patched = True

if not patched:
    print("⚠ Could not auto-patch imports — adding manually at top of file")
    new_imports = """from aletheia.agents.valuation_node import valuation_node
from aletheia.agents.contrarian_v2 import contrarian_agent
"""
    # Add after first import block
    lines = code.split('\n')
    last_import_idx = 0
    for i, line in enumerate(lines):
        if line.startswith('from ') or line.startswith('import '):
            last_import_idx = i
    lines.insert(last_import_idx + 1, new_imports)
    code = '\n'.join(lines)
    print("✓ Imports added after last import line")

# ── Add valuation_node between fundamentalist and contrarian ──────────────────
# Try common LangGraph patterns

edge_patterns = [
    # Pattern: add_edge from fundamentalist to contrarian
    ('graph.add_edge("fundamentalist_agent", "contrarian_agent")',
     'graph.add_edge("fundamentalist_agent", "valuation_node")\ngraph.add_edge("valuation_node", "contrarian_agent")'),
    # Pattern: add_node style
    ('graph.add_node("contrarian_agent", contrarian_agent)',
     'graph.add_node("valuation_node", valuation_node)\ngraph.add_node("contrarian_agent", contrarian_agent)'),
    # Sequential patterns
    ('fundamentalist_agent\ncontrarian_agent',
     'fundamentalist_agent\nvaluation_node\ncontrarian_agent'),
]

node_patched = False
for old, new in edge_patterns:
    if old in code:
        code = code.replace(old, new, 1)
        print(f"✓ Graph edge patched: inserted valuation_node")
        node_patched = True
        break

if not node_patched:
    print()
    print("⚠ Could not auto-detect graph edge pattern.")
    print("  Please manually add to graph.py:")
    print()
    print('  # Add node')
    print('  graph.add_node("valuation_node", valuation_node)')
    print()
    print('  # Change edge: fundamentalist → valuation_node → contrarian')
    print('  # Replace: graph.add_edge("fundamentalist_agent", "contrarian_agent")')
    print('  # With:')
    print('  graph.add_edge("fundamentalist_agent", "valuation_node")')
    print('  graph.add_edge("valuation_node", "contrarian_agent")')

graph_path.write_text(code)
print()
print("graph.py updated. Final content:")
print("-" * 60)
print(graph_path.read_text())

# ── Update aletheia/state.py if it exists ─────────────────────────────────────
state_candidates = [
    Path("aletheia/state.py"),
    Path("state.py"),
]

for state_path in state_candidates:
    if state_path.exists():
        state_code = state_path.read_text()
        print(f"\nFound {state_path}:")
        print(state_code)

        # Add phase2_valuation to TypedDict if not already there
        if "phase2_valuation" not in state_code:
            # Find the TypedDict class and add the field
            for pattern in [
                "contrarian_report: ",
                "final_report: ",
                "messages: ",
            ]:
                if pattern in state_code:
                    # Add phase2_valuation after contrarian_report
                    state_code = state_code.replace(
                        pattern,
                        f"phase2_valuation: dict\n    {pattern}",
                        1
                    )
                    state_path.write_text(state_code)
                    print(f"✓ Added phase2_valuation to {state_path}")
                    break
        else:
            print(f"✓ phase2_valuation already in {state_path}")
        break
