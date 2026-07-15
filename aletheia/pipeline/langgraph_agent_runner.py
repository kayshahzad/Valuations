"""LangGraph-backed agent runner for Stage 4.

Conforms to the ``AgentRunner`` protocol declared in
``aletheia.pipeline.stage4_agents``. Bridges the typed-contract Stage 4
to the existing LangGraph workflow (``aletheia.workflow.graph``) so the
orchestrator's "Run Stage 4 (LLM)" path actually invokes the agents
instead of falling through to the empty ``_default_agent_runner``.

Background — why this wrapper exists:
  - The CLI used to be ``python3 main.py --ticker T`` which ran the
    LangGraph workflow end-to-end and wrote ``valuation_data/serving/
    latest/{T}_report.json`` via ``lead_agent``.
  - The new typed-contract orchestrator (``aletheia.pipeline``) took
    over Stages 1-3 but left Stage 4 plugged into a placeholder
    ``_default_agent_runner`` that returns empty dicts. That made the
    "Run Stage 4 LLM" button a 1-second no-op even though
    ``pipeline_status`` reported ``ok``.
  - This module finishes the migration: a runner that delegates to the
    same LangGraph workflow ``main.py`` invoked, so the LLM-bearing path
    actually executes when the orchestrator drives Stage 4.

The runner re-runs ``librarian`` + ``calc_node`` inside the workflow
(rather than threading the orchestrator's Stage 3 ``CalculationBundle``
into ``AgentState``). That re-run is intentional:

  - ``librarian`` fetches the 10-K narrative text the qualitative-
    synthesis agent reads — Stages 1-3 don't capture filing text.
  - ``calc_node`` populates ``state["phase2_valuation"]`` in the exact
    shape downstream agents expect. Rebuilding that shape from the
    typed ``CalculationBundle`` would re-implement state plumbing the
    workflow already encodes correctly.

Cost: ~30-60s of duplicated calc work per ticker. Acceptable because
LLM calls (Gemini × 3 agents) dominate Stage 4 runtime by an order of
magnitude. When the agent layer is rewritten to read directly from the
typed bundle, this runner can drop the librarian/calc_node prefix.
"""
from __future__ import annotations

from typing import Any, Dict

from aletheia.contracts.pipeline import CalculationBundle


def langgraph_agent_runner(
    calculation_bundle: CalculationBundle,
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """Invoke the LangGraph workflow for ``calculation_bundle.ticker``.

    Returns a dict shaped for the ``AgentBundle`` constructor:
    ``qualitative_synthesis``, ``contrarian``, ``thesis``, plus optional
    ``raw_10k_excerpt`` and ``llm_cost_usd``. Side-effects of the
    workflow (``valuation_data/serving/latest/{T}_report.json`` written
    by ``lead_agent``, ``agent_runs`` DB row upserted) are what the UI
    actually consumes — this return value is the typed audit trail.
    """
    # Import inside the function so importing this module is cheap and
    # doesn't trigger the LangGraph DeprecationWarning at module load.
    from aletheia.workflow.graph import create_workflow
    from aletheia.eval.tracing import init_langsmith, trace_config

    ticker = calculation_bundle.ticker

    # Turn on LangSmith tracing if a key is configured (no-op otherwise).
    # Every LangChain agent inside the graph is then auto-instrumented;
    # trace_config labels the run so it's filterable by ticker.
    init_langsmith()

    initial_state: Dict[str, Any] = {
        "ticker":             ticker,
        "messages":           [],
        "financial_data":     {},
        "valuation_report":   {},
        "strategist_report":  {},
        "contrarian_report":  {},
        "final_report":       "",
    }

    app = create_workflow()
    final_state = app.invoke(initial_state, config=trace_config(ticker))

    # The qualitative_synthesis agent writes three legacy state keys
    # (forensic / value_chain / strategic_context) so existing
    # consumers don't break. We bundle them into a single dict for the
    # AgentBundle's ``qualitative_synthesis`` field.
    qualitative = {
        "forensic":          final_state.get("forensic_report") or {},
        "value_chain":       final_state.get("value_chain_report") or {},
        "strategic_context": final_state.get("strategic_context_report") or {},
    }

    return {
        "qualitative_synthesis": qualitative,
        "contrarian":            final_state.get("contrarian_report") or {},
        "thesis":                final_state.get("thesis_synthesis") or {},
        # 10-K excerpt the librarian fetched; useful for the audit
        # trail. Stored on the bundle, not the serving JSON.
        "raw_10k_excerpt":       final_state.get("ten_k_text"),
        # The current agents don't yet self-report LLM spend; left None
        # to signal "real run, cost unmeasured" (distinct from the
        # default placeholder's None which means "no LLM call made").
        "llm_cost_usd":          None,
    }


__all__ = ["langgraph_agent_runner"]
