"""Stage 4 — Agent Execution.

Consumes a ``CalculationBundle`` (Stage 3 output) and produces an
``AgentBundle`` carrying the LLM-derived qualitative analysis:
qualitative_synthesis, contrarian, thesis. Plus the raw 10-K excerpt
for forensic traceability.

Boundary discipline: Stage 4 is the consumer of everything upstream
and has no further forbidden imports (per the locked decision in
docs/pipeline_contracts.md). It MAY import from ``aletheia.agents``,
``aletheia.workflow``, and ``aletheia.ui`` — though the recommended
pattern is for callers to inject an ``agent_runner`` callable so the
stage stays decoupled from any specific agent implementation.

LLM cost gating: the orchestrator's chain stops at Stage 3 by default.
Operators opt into Stage 4 explicitly via ``--auto-agents``. Each run
captures ``llm_cost_usd`` so the status registry can surface
"recompute would cost $X" prompts before re-firing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from aletheia.contracts.pipeline import AgentBundle, CalculationBundle


# ─────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────

class Stage4AgentError(Exception):
    """Stage 4 input contract violation or agent-runner failure."""


# ─────────────────────────────────────────────────────────────────────
# Agent-runner protocol
# ─────────────────────────────────────────────────────────────────────

# The agent_runner takes the upstream calc bundle + run options and
# returns a dict shaped to feed AgentBundle's sub-result fields. This
# indirection keeps Stage 4 itself free of imports from the in-flux
# agent layer; callers wire whatever runner is appropriate (existing
# LangGraph workflow, a new orchestrator, a test stub).
AgentRunner = Callable[[CalculationBundle, Dict[str, Any]], Dict[str, Any]]


# ─────────────────────────────────────────────────────────────────────
# Default runner — empty-bundle placeholder
# ─────────────────────────────────────────────────────────────────────

def _default_agent_runner(
    bundle: CalculationBundle,
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """Default agent_runner: returns empty agent sub-results. Used by
    callers that want the typed boundary without committing to a
    specific agent implementation (tests, dry-runs, status queries).

    Returns a shape-matching dict so the AgentBundle constructor
    doesn't complain about missing fields. ``llm_cost_usd`` is None
    to signal "no LLM call was made" — distinct from "LLM call made
    and cost $0" which would be 0.0.
    """
    return {
        "qualitative_synthesis": {},
        "contrarian": {},
        "thesis": {},
        "raw_10k_excerpt": None,
        "llm_cost_usd": None,
    }


# ─────────────────────────────────────────────────────────────────────
# Fingerprint
# ─────────────────────────────────────────────────────────────────────

def _compute_bundle_fingerprint(
    *,
    ticker: str,
    input_calculation_fingerprint: str,
    pipeline_version: str,
    runner_id: str,
) -> str:
    """SHA-256 over (ticker + input fingerprint + pipeline_version +
    runner identity). The runner_id distinguishes ``default-empty``
    (no agents were actually run) from a real agent runner — so a
    placeholder bundle doesn't share a fingerprint with a real run.
    """
    payload = "|".join([
        ticker,
        input_calculation_fingerprint,
        pipeline_version,
        runner_id,
    ])
    return hashlib.sha256(payload.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────

def run_stage4(
    calculation_bundle: CalculationBundle,
    *,
    pipeline_version: str,
    agent_runner: Optional[AgentRunner] = None,
    runner_id: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
) -> AgentBundle:
    """Run the qualitative-analysis agents over a Stage 3 bundle.

    Args:
        calculation_bundle: Stage 3 output. Stage 4 reads its
            ``bundle_fingerprint`` for lineage and its
            ``ticker`` / ``fiscal_year`` to pass to the runner.
        pipeline_version: Git SHA / version string stamped on the
            output bundle. Folded into the agent-bundle fingerprint
            so methodology bumps invalidate the cache.
        agent_runner: Callable that actually invokes the LLM agents.
            When None, the default runner returns an empty-shape
            bundle — used for tests, dry-runs, and the orchestrator's
            "Stage 4 placeholder" path when --auto-agents is off but
            the orchestrator still wants to surface a typed bundle.
        runner_id: Short string identifying the runner for fingerprint
            purposes. Defaults to ``"default-empty"`` for the placeholder,
            or ``"custom"`` when a runner is supplied without an id.
        options: Per-run dict passed through to the runner. Reserved
            for things like temperature, model choice, dry-run flag.

    Raises:
        Stage4AgentError: when the runner raises or returns a
            structurally-invalid payload.
    """
    if calculation_bundle is None:
        raise Stage4AgentError(
            "Stage 4 requires a CalculationBundle input — got None."
        )

    runner = agent_runner or _default_agent_runner
    resolved_runner_id = runner_id or (
        "default-empty" if agent_runner is None else "custom"
    )

    try:
        payload = runner(calculation_bundle, options or {})
    except Exception as exc:  # noqa: BLE001 — boundary defensiveness
        raise Stage4AgentError(
            f"agent_runner raised for {calculation_bundle.ticker}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    # Defensive shape check.
    required_keys = {"qualitative_synthesis", "contrarian", "thesis"}
    missing = required_keys - set(payload.keys())
    if missing:
        raise Stage4AgentError(
            f"agent_runner returned payload missing required keys: "
            f"{sorted(missing)} (got {sorted(payload.keys())})"
        )

    bundle_fp = _compute_bundle_fingerprint(
        ticker=calculation_bundle.ticker,
        input_calculation_fingerprint=calculation_bundle.bundle_fingerprint,
        pipeline_version=pipeline_version,
        runner_id=resolved_runner_id,
    )

    return AgentBundle(
        ticker=calculation_bundle.ticker,
        qualitative_synthesis=payload.get("qualitative_synthesis") or {},
        contrarian=payload.get("contrarian") or {},
        thesis=payload.get("thesis") or {},
        raw_10k_excerpt=payload.get("raw_10k_excerpt"),
        bundle_fingerprint=bundle_fp,
        input_calculation_fingerprint=calculation_bundle.bundle_fingerprint,
        computed_at=datetime.now(timezone.utc),
        pipeline_version=pipeline_version,
        llm_cost_usd=payload.get("llm_cost_usd"),
    )


__all__ = [
    "Stage4AgentError",
    "AgentRunner",
    "run_stage4",
]
