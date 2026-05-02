"""
aletheia/workflow/graph.py

Workflow DAG. Strict separation of calc and narrative:

    librarian
        │  (loads serving_base from cleaned DB)
        ▼
    calc_node                     ← all deterministic numerical analysis
        │  writes phase2_valuation, cyclicality, operating_leverage, conviction
        ▼
    forensic                      ← pure-narrative agents below; READ-ONLY
    value_chain                     against state. They produce typed
    context                         narrative outputs (Pydantic + Literal),
    strategist                      never numbers that mutate calc inputs.
    contrarian
        ▼
    lead                          ← assembles the final report

Removed nodes:
  - fundamentalist (legacy ProForma path; calc_node + DCFEngine supersede)
  - valuation_node (folded into calc_node; the old node read agent state and
    mutated DCF inputs via compute_dcf_adjustments — anti-pattern stripped)

Architecture invariants enforced by tests:
  - tests/architecture/test_no_config_imports_in_calc_layer.py
  - tests/architecture/test_no_agent_emitted_overrides.py (added in Phase B.2)
"""

from langgraph.graph import StateGraph, END

from aletheia.state import AgentState
from aletheia.agents.librarian import librarian_agent
from aletheia.agents.calc_node import calc_node
from aletheia.agents.strategist import strategist_agent
from aletheia.agents.contrarian_v2 import contrarian_agent
from aletheia.agents.forensic import forensic_agent
from aletheia.agents.value_chain import value_chain_agent
from aletheia.agents.context import strategic_context_agent
from aletheia.agents.lead import lead_agent


def create_workflow():
    workflow = StateGraph(AgentState)

    # ── Nodes ────────────────────────────────────────────────────────────────
    workflow.add_node("librarian", librarian_agent)
    workflow.add_node("calc_node", calc_node)
    workflow.add_node("forensic", forensic_agent)
    workflow.add_node("value_chain", value_chain_agent)
    workflow.add_node("context", strategic_context_agent)
    workflow.add_node("strategist", strategist_agent)
    workflow.add_node("contrarian", contrarian_agent)
    workflow.add_node("lead", lead_agent)

    # ── DAG ──────────────────────────────────────────────────────────────────
    workflow.set_entry_point("librarian")
    workflow.add_edge("librarian",   "calc_node")
    workflow.add_edge("calc_node",   "forensic")
    workflow.add_edge("forensic",    "value_chain")
    workflow.add_edge("value_chain", "context")
    workflow.add_edge("context",     "strategist")
    workflow.add_edge("strategist",  "contrarian")
    workflow.add_edge("contrarian",  "lead")
    workflow.add_edge("lead",        END)

    return workflow.compile()
