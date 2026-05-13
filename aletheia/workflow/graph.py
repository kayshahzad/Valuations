"""
aletheia/workflow/graph.py

.. deprecated:: 2026-05-13
   The LangGraph workflow is superseded by the typed-contract pipeline
   in ``aletheia.pipeline`` (Stages 1-4 + ``Orchestrator``). New callers
   should use ``aletheia pipeline run <TICKER>`` (CLI) or
   ``Orchestrator().run(ticker, ...)`` (programmatic). This module
   stays in place as a compat wrapper until the in-flight agent-layer
   rewrite migrates every caller off it. See
   ``docs/pipeline_contracts.md`` decision #3 for the 6-month
   deprecation timeline and ``docs/pipeline_operations.md`` for the
   migration cookbook.

Each call to ``create_workflow()`` emits a ``DeprecationWarning``;
callers that consume ``aletheia.workflow.graph`` directly will see
the warning at construction time and can plan migration accordingly.
The legacy DAG below is preserved verbatim — replacing the
narrative-side agent execution is not in this refactor's scope.

----- Original module docs (preserved for migration reference) -----

Workflow DAG. Strict separation of calc and narrative:

    librarian                     ← fetches 10-K text from SEC EDGAR
        │
        ▼
    calc_node                     ← all deterministic numerical analysis
        │  writes phase2_valuation, cyclicality, conviction
        ▼
    qualitative_synthesis         ← consolidated narrative (one LLM call,
        │                          one 10-K read). Writes the three legacy
        │                          state keys (forensic_report,
        │                          value_chain_report, strategic_context_
        │                          report) so downstream consumers don't
        │                          need to change.
        ▼
    scenario_eval                 ← evaluates 0-3 agent-proposed scenarios
        ▼                          via DCFEngine
    strategist
        ▼
    contrarian (v2)               ← bias detection + bear case + sentiment
        │                          (web search dropped after week-1.5 A/B)
        ▼
    dashboard_fetch               ← projects the qualitative-dashboard
        │                          state for the ticker (per-dim status,
        │                          category composites, coverage bucket)
        │                          into state["qualitative_dashboard"].
        │                          Does the DB read that thesis_synthesizer
        │                          itself is AST-locked from doing.
        ▼
    thesis_synthesizer            ← INTEGRATION layer. Cites upstream
        │                          signals via cited_signals; produces
        │                          structured ThesisSynthesis (bull /
        │                          bear / base case + decision conditions).
        │                          AST-locked: no data-fetching primitives.
        ▼
    lead                          ← assembles the final report

Removed nodes:
  - fundamentalist (legacy ProForma path; calc_node + DCFEngine supersede)
  - valuation_node (folded into calc_node)
  - intake (no-op; class defs unreachable)
  - contrarian v1 (superseded by contrarian_v2)
  - forensic + value_chain + context  (week-1.5 consolidation —
    replaced by qualitative_synthesis after side-by-side quality gate
    confirmed depth preserved on all 9 sample tickers)

Architecture invariants enforced by tests:
  - tests/architecture/test_no_config_imports_in_calc_layer.py
  - tests/architecture/test_no_agent_emitted_overrides.py
  - tests/architecture/test_no_resurrected_agents.py
"""

import warnings

from langgraph.graph import StateGraph, END

from aletheia.state import AgentState
from aletheia.agents.librarian import librarian_agent
from aletheia.agents.calc_node import calc_node
from aletheia.agents.qualitative_synthesis import qualitative_synthesis_agent
from aletheia.agents.scenario_eval_node import scenario_eval_node
from aletheia.agents.strategist import strategist_agent
from aletheia.agents.contrarian_v2 import contrarian_agent
from aletheia.agents.dashboard_fetch import dashboard_fetch_node
from aletheia.agents.thesis_synthesizer import thesis_synthesizer_agent
from aletheia.agents.lead import lead_agent


_DEPRECATION_MESSAGE = (
    "aletheia.workflow.graph.create_workflow() is deprecated; use the "
    "typed-contract pipeline at aletheia.pipeline.Orchestrator (or the "
    "`aletheia pipeline run` CLI) instead. See docs/pipeline_contracts.md "
    "decision #3 and docs/pipeline_operations.md for the migration "
    "cookbook. The 6-month deprecation window starts on the first "
    "release that ships this warning; the function is removed at the "
    "end of the window."
)


def create_workflow():
    warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
    workflow = StateGraph(AgentState)

    # ── Nodes ────────────────────────────────────────────────────────────────
    workflow.add_node("librarian",             librarian_agent)
    workflow.add_node("calc_node",             calc_node)
    workflow.add_node("qualitative_synthesis", qualitative_synthesis_agent)
    workflow.add_node("scenario_eval",         scenario_eval_node)
    workflow.add_node("strategist",            strategist_agent)
    workflow.add_node("contrarian",            contrarian_agent)
    workflow.add_node("dashboard_fetch",       dashboard_fetch_node)
    workflow.add_node("thesis_synthesizer",    thesis_synthesizer_agent)
    workflow.add_node("lead",                  lead_agent)

    # ── DAG ──────────────────────────────────────────────────────────────────
    # dashboard_fetch projects qualitative-dashboard state for the ticker
    # so thesis_synthesizer can cite analyst-structured judgment alongside
    # calc_node and other agent outputs without violating its AST lock.
    workflow.set_entry_point("librarian")
    workflow.add_edge("librarian",             "calc_node")
    workflow.add_edge("calc_node",             "qualitative_synthesis")
    workflow.add_edge("qualitative_synthesis", "scenario_eval")
    workflow.add_edge("scenario_eval",         "strategist")
    workflow.add_edge("strategist",            "contrarian")
    workflow.add_edge("contrarian",            "dashboard_fetch")
    workflow.add_edge("dashboard_fetch",       "thesis_synthesizer")
    workflow.add_edge("thesis_synthesizer",    "lead")
    workflow.add_edge("lead",                  END)

    return workflow.compile()
