"""
aletheia/agents/valuation_node.py

DEPRECATED. Superseded by `aletheia.agents.calc_node`.

Historically this module read forensic / value_chain / context agent state and
computed DCF adjustment kwargs (wacc_penalty, growth_decay_reduction,
base_revenue_override, terminal_growth_adj) that mutated DCFEngine inputs.
That violated the calc/agent separation: agent narrative was influencing
calc-layer math.

The new architecture runs `calc_node` BEFORE any agent, with all numerical
analysis derived deterministically from CalculationInput. Agents read calc
results from state and produce narrative — they never write back to calc.

This shim remains so that the legacy `run_valuation.py` runner doesn't break.
For new code, import `calc_node` directly:

    from aletheia.agents.calc_node import calc_node

The old `compute_dcf_adjustments(...)` helper has been removed. Any caller
that imports it should be updated to consume the deterministic outputs in
`state["phase2_valuation"]` directly.
"""

from aletheia.agents.calc_node import calc_node


def valuation_node(state: dict) -> dict:
    """Deprecated thin shim — delegates to calc_node."""
    print("---VALUATION NODE (deprecated shim) → calc_node---")
    return calc_node(state)
