# archive/

Quarantine for code that's been removed from the live application but
kept on disk for historical reference. Nothing here is imported or
executed by the running app — `tests/architecture/test_no_resurrected_agents.py`
enforces that none of the legacy agent imports reappear.

## archive/agents/

Seven legacy LangGraph agents superseded by the consolidated thesis-
synthesis pipeline. Removed from `aletheia/agents/` because their
functions are now covered by:

| Archived agent | Superseded by |
|---|---|
| `context.py` | Folded into `qualitative_synthesis_agent` |
| `contrarian.py` | Replaced by `contrarian_v2.py` |
| `forensic.py` | Folded into `qualitative_synthesis_agent` |
| `fundamentalist.py` | Replaced by `calc_node.py` (deterministic) + `thesis_synthesizer_agent.py` (narrative) |
| `intake.py` | Replaced by `librarian.py` |
| `valuation_node.py` | Folded into `calc_node.py` |
| `value_chain.py` | Folded into `qualitative_synthesis_agent` |

## archive/scratch/

Top-level scratch / one-off scripts that predate the
`aletheia.cli.pipeline` and `aletheia.pipeline.orchestrator` CLI
entry points.

| File | Replacement |
|---|---|
| `fundamentalist_fixed.py` | `aletheia/pipeline/stage3_calculate.py` |
| `run_valuation.py` | `python -m aletheia.cli.pipeline run TICKER` |

## What is NOT archived

These look unused but ARE still invoked:

- `main.py` + `aletheia/workflow/graph.py` — `api_main.py:1731` subprocesses `python3 main.py --ticker X` from the `/pipeline/run/{ticker}` legacy endpoint. The endpoint is marked for eventual removal but kept until external consumers migrate to `/pipeline/run` (orchestrator-based).
- Anything under `scripts/archive/` — that's a separate, older quarantine maintained by the scripts subsystem.
