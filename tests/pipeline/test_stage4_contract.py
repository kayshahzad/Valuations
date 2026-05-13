"""Contract tests for Stage 4 (agent execution).

Per docs/pipeline_contracts.md, each stage's tests verify:
  (a) output schema (valid AgentBundle, lineage pointer, fingerprint),
  (b) input contract rejection (None bundle, malformed runner output),
  (c) output consumable by downstream consumers (bundle is JSON-
      serialisable, fingerprint is deterministic).

Stage 4 is intentionally a thin typed boundary: the actual agent
work is injected via ``agent_runner``. Tests use a synthetic runner
so they don't depend on the in-flux LLM agent layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

import pytest

from aletheia.contracts.pipeline import AgentBundle, CalculationBundle
from aletheia.pipeline.stage4_agents import (
    Stage4AgentError,
    run_stage4,
    _compute_bundle_fingerprint,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

def _make_calc_bundle(
    ticker: str = "NVDA",
    fingerprint: str = "f" * 64,
    fiscal_year: int = 2024,
) -> CalculationBundle:
    return CalculationBundle(
        ticker=ticker,
        fiscal_year=fiscal_year,
        base_period="FY",
        dcf={"ticker": ticker, "wacc_base": 0.10},
        reverse_dcf={},
        multiple_decomposition={},
        screening={},
        moat_fingerprint={},
        cyclicality={},
        scenarios=[],
        capital_structure={},
        reality_checks={},
        schema_violations=[],
        bundle_fingerprint=fingerprint,
        input_record_fingerprint="r" * 64,
        computed_at=datetime.now(timezone.utc),
        pipeline_version="test-v1",
    )


def _real_runner(
    bundle: CalculationBundle, options: Dict[str, Any],
) -> Dict[str, Any]:
    """Synthetic runner that produces a 'real-looking' agent payload
    so we can test the non-empty path."""
    return {
        "qualitative_synthesis": {
            "forensic_report": {"score": 7.5},
            "value_chain_report": {"score": 6.0},
            "strategic_context_report": {"score": 8.0},
        },
        "contrarian": {"bear_case": "lorem ipsum", "sentiment": "neutral"},
        "thesis": {
            "thesis_statement": "test",
            "bull": {"cited_signals": []},
            "base": {"cited_signals": []},
            "bear": {"cited_signals": []},
        },
        "raw_10k_excerpt": "First 100 chars of 10-K...",
        "llm_cost_usd": 1.23,
    }


# ─────────────────────────────────────────────────────────────────────
# (a) Output schema — default-empty runner
# ─────────────────────────────────────────────────────────────────────

def test_stage4_produces_valid_agent_bundle_with_default_runner():
    """When no runner is supplied, the default-empty runner fires.
    Bundle is structurally valid even though sub-results are empty —
    useful for dry runs and the orchestrator's "Stage 4 stub" path."""
    calc = _make_calc_bundle()
    bundle = run_stage4(calc, pipeline_version="v")

    assert isinstance(bundle, AgentBundle)
    assert bundle.ticker == "NVDA"
    assert bundle.pipeline_version == "v"
    assert len(bundle.bundle_fingerprint) == 64
    assert bundle.input_calculation_fingerprint == calc.bundle_fingerprint
    assert bundle.qualitative_synthesis == {}
    assert bundle.contrarian == {}
    assert bundle.thesis == {}
    assert bundle.raw_10k_excerpt is None
    # The default runner explicitly returns None for cost — distinct
    # from "ran and cost $0" which would be 0.0.
    assert bundle.llm_cost_usd is None


def test_stage4_produces_valid_agent_bundle_with_custom_runner():
    calc = _make_calc_bundle()
    bundle = run_stage4(
        calc, pipeline_version="v",
        agent_runner=_real_runner, runner_id="test-runner",
    )
    assert bundle.qualitative_synthesis["forensic_report"]["score"] == 7.5
    assert bundle.contrarian["sentiment"] == "neutral"
    assert bundle.thesis["thesis_statement"] == "test"
    assert bundle.raw_10k_excerpt == "First 100 chars of 10-K..."
    assert bundle.llm_cost_usd == 1.23


def test_stage4_bundle_is_json_serialisable():
    """Stage 4's bundle gets persisted; round-tripping through JSON
    is the proof of downstream consumability."""
    bundle = run_stage4(
        _make_calc_bundle(),
        pipeline_version="v",
        agent_runner=_real_runner,
    )
    j = bundle.model_dump_json()
    assert "bundle_fingerprint" in j
    assert "qualitative_synthesis" in j


# ─────────────────────────────────────────────────────────────────────
# (b) Input contract enforcement
# ─────────────────────────────────────────────────────────────────────

def test_stage4_rejects_none_bundle():
    with pytest.raises(Stage4AgentError, match="requires a CalculationBundle"):
        run_stage4(None, pipeline_version="v")  # type: ignore[arg-type]


def test_stage4_rejects_runner_payload_missing_required_keys():
    def _malformed_runner(bundle, options):
        return {"thesis": {}}  # missing qualitative_synthesis, contrarian
    with pytest.raises(Stage4AgentError, match="missing required keys"):
        run_stage4(
            _make_calc_bundle(),
            pipeline_version="v",
            agent_runner=_malformed_runner,
        )


def test_stage4_wraps_runner_exceptions():
    def _raising_runner(bundle, options):
        raise RuntimeError("downstream API down")
    with pytest.raises(Stage4AgentError, match="agent_runner raised"):
        run_stage4(
            _make_calc_bundle(),
            pipeline_version="v",
            agent_runner=_raising_runner,
        )


# ─────────────────────────────────────────────────────────────────────
# Fingerprint determinism + bust conditions
# ─────────────────────────────────────────────────────────────────────

def test_bundle_fingerprint_is_deterministic():
    fp1 = _compute_bundle_fingerprint(
        ticker="NVDA",
        input_calculation_fingerprint="abc",
        pipeline_version="v",
        runner_id="r1",
    )
    fp2 = _compute_bundle_fingerprint(
        ticker="NVDA",
        input_calculation_fingerprint="abc",
        pipeline_version="v",
        runner_id="r1",
    )
    assert fp1 == fp2
    assert len(fp1) == 64


def test_bundle_fingerprint_changes_with_input():
    fp1 = _compute_bundle_fingerprint(
        ticker="NVDA", input_calculation_fingerprint="abc",
        pipeline_version="v", runner_id="r",
    )
    fp2 = _compute_bundle_fingerprint(
        ticker="NVDA", input_calculation_fingerprint="xyz",
        pipeline_version="v", runner_id="r",
    )
    assert fp1 != fp2


def test_bundle_fingerprint_distinguishes_default_runner_from_real():
    """The default-empty runner must produce a different fingerprint
    than a real runner — even on the same inputs — so placeholder
    bundles don't cache-collide with real ones."""
    calc = _make_calc_bundle()
    placeholder = run_stage4(calc, pipeline_version="v")
    real = run_stage4(
        calc, pipeline_version="v",
        agent_runner=_real_runner, runner_id="real",
    )
    assert placeholder.bundle_fingerprint != real.bundle_fingerprint


def test_bundle_fingerprint_changes_with_pipeline_version():
    calc = _make_calc_bundle()
    b1 = run_stage4(calc, pipeline_version="vA")
    b2 = run_stage4(calc, pipeline_version="vB")
    assert b1.bundle_fingerprint != b2.bundle_fingerprint
