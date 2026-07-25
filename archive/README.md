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

## archive/lib/

Library modules that had zero importers anywhere in the live app (no
production import, no test, no dynamic/registry reference) as of the
2026-07-24 dead-code sweep. Verified via a repo-wide real-import-statement
scan plus a dynamic-reference (string/importlib/`__init__` re-export) check.

| Archived module | Former path | Why dead |
|---|---|---|
| `compare.py` | `aletheia/scenarios/compare.py` | Scenario-comparison helper; no callers |
| `sensitivity.py` | `aletheia/scenarios/sensitivity.py` | Tornado/sensitivity render; no callers |
| `universe_portfolio.py` | `aletheia/tools/universe_portfolio.py` | Superseded by the serving-layer universe endpoints |
| `agents_view.py` | `aletheia/ui/agents_view.py` | Old Streamlit agents panel; superseded by `deep_dive_view` |
| `config_loader.py` | `aletheia/utils/config_loader.py` | `load_valuation_config` unused; config read inline elsewhere |

## archive/scratch/

Top-level scratch / one-off scripts that predate the
`aletheia.cli.pipeline` and `aletheia.pipeline.orchestrator` CLI
entry points.

| File | Replacement |
|---|---|
| `fundamentalist_fixed.py` | `aletheia/pipeline/stage3_calculate.py` |
| `run_valuation.py` | `python -m aletheia.cli.pipeline run TICKER` |
| `check_coverage.py`, `extract_25.py`, `patch_deep_dive.py`, `query_nee.py`, `scratch_report_gen.py`, `tag_miss_audit.py`, `tag_resolver_final.py`, `validate_ui_data.py` | One-off audit / migration-patch scripts (2026-07-24 sweep); no importers, no CI wiring |

## What is NOT archived

These look unused but ARE still invoked:

- `main.py` + `aletheia/workflow/graph.py` — `api_main.py:1731` subprocesses `python3 main.py --ticker X` from the `/pipeline/run/{ticker}` legacy endpoint. The endpoint is marked for eventual removal but kept until external consumers migrate to `/pipeline/run` (orchestrator-based).
- Anything under `scripts/archive/` — that's a separate, older quarantine maintained by the scripts subsystem.
