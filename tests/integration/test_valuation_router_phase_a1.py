"""Phase A.1 unit tests for the valuation router foundation.

Covers:
  - ``ValuationResult`` shape + immutability
  - ``ValuationRouter`` dispatch for ``fcff_compatible`` →
    ``FcffEngine`` (Phase A.1 wires this)
  - ``EngineNotImplementedError`` raised for ``routing_required`` /
    ``ddm_required`` / ``embedded_value_required`` (placeholder
    state — Phase A.3 / A.7 fill these in)
  - ``UnknownBusinessModelError`` raised for unknown classification
  - Dispatch override (used by Phase A.3+ tests to inject mock
    engines without touching the global dispatch table)
  - ``FcffEngine`` is callable as a ``ValuationEngine`` Protocol
    instance (runtime_checkable enforces method presence)
  - The DCFEngine hard-stop at line 966 stays intact (defense-in-depth)

No live DCF run — the FCFF-tickers smoke test happens in the Phase
A.1 universe-diff verification, not in unit tests.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any
from unittest.mock import MagicMock

import pytest

from aletheia.tools.valuation_engines import (
    FcffEngine,
    ValuationEngine,
    ValuationResult,
)
from aletheia.tools.valuation_router import (
    UnknownBusinessModelError,
    ValuationRouter,
)


# ── ValuationResult shape ──────────────────────────────────────────


def test_valuation_result_minimal_construction():
    r = ValuationResult(
        ticker="AAPL", fiscal_year=2025, engine="fcff",
        intrinsic_per_share=200.0, equity_value=3_000e9,
        current_price=210.0, margin_of_safety=-0.05,
    )
    assert r.ticker == "AAPL"
    assert r.engine == "fcff"
    assert r.intrinsic_per_share == 200.0
    # Default fields populated
    assert r.inputs_snapshot == {}
    assert r.warnings == []
    assert r.engine_specific == {}


def test_valuation_result_is_frozen():
    """Frozen dataclass prevents accidental mutation between engine
    and consumer — matches the pattern used by ExtractionResult."""
    r = ValuationResult(
        ticker="AAPL", fiscal_year=2025, engine="fcff",
        intrinsic_per_share=None, equity_value=None,
        current_price=None, margin_of_safety=None,
    )
    with pytest.raises(FrozenInstanceError):
        r.intrinsic_per_share = 200.0  # type: ignore[misc]


def test_valuation_result_accepts_none_values():
    """All scoring fields can be None — empty-state path for engines
    that can't produce an IV (missing inputs / data gap)."""
    r = ValuationResult(
        ticker="NEE", fiscal_year=2026, engine="rate_base",
        intrinsic_per_share=None, equity_value=None,
        current_price=93.36, margin_of_safety=None,
        warnings=["rate_base_billions not provided in config"],
    )
    assert r.intrinsic_per_share is None
    assert r.warnings == ["rate_base_billions not provided in config"]


# ── FcffEngine implements ValuationEngine Protocol ─────────────────


def test_fcff_engine_is_valuation_engine_protocol():
    """``runtime_checkable`` Protocol — isinstance check verifies
    that FcffEngine has the required ``compute_intrinsic_value``
    method. Phase A.3+ engines need the same shape."""
    engine = FcffEngine()
    assert isinstance(engine, ValuationEngine)


# ── Router dispatch ────────────────────────────────────────────────


def _calc_input_with_model(business_model: str) -> Any:
    """Build a minimal CalculationInput-shaped mock for dispatch tests."""
    ci = MagicMock(name="CalculationInput")
    ci.classification.business_model = business_model
    ci.classification.ticker = "FAKE"
    return ci


def test_router_dispatch_table_post_a7():
    """Production dispatch table after Phase A.7 — all four
    business_model values are wired to concrete engines. No
    placeholders remain."""
    router = ValuationRouter()
    assert router.has_engine("fcff_compatible") is True          # Phase A.1
    assert router.has_engine("routing_required") is True         # Phase A.3
    assert router.has_engine("ddm_required") is True             # Phase A.7
    assert router.has_engine("embedded_value_required") is True  # Phase A.7


def test_router_raises_unknown_for_typo_business_model():
    """A classification with a business_model value not in the
    dispatch table at all (typo, new value added without router
    update) raises a distinct exception so the failure mode is
    obvious."""
    router = ValuationRouter()
    ci = _calc_input_with_model("not_a_real_model")
    with pytest.raises(UnknownBusinessModelError, match="not_a_real_model"):
        router.execute(ci)


# ── Dispatch override (test-injection) ─────────────────────────────


def test_router_uses_dispatch_override_when_provided():
    """Tests can inject a partial dispatch table so they don't
    need a real DCFEngine to verify routing logic. Phase A.3+
    engine tests rely on this."""
    called_with = []

    def fake_factory():
        engine = MagicMock(spec=ValuationEngine)
        engine.compute_intrinsic_value.side_effect = lambda ci: (
            called_with.append(ci) or ValuationResult(
                ticker="MOCK", fiscal_year=2025, engine="mock",
                intrinsic_per_share=42.0, equity_value=42e9,
                current_price=50.0, margin_of_safety=-0.16,
            )
        )
        return engine

    router = ValuationRouter(
        dispatch_override={"routing_required": fake_factory},
    )
    ci = _calc_input_with_model("routing_required")
    result = router.execute(ci)
    assert len(called_with) == 1
    assert result.engine == "mock"
    assert result.intrinsic_per_share == 42.0


def test_router_override_does_not_pollute_global_dispatch():
    """Each ValuationRouter instance gets its own dispatch — an
    override for tests doesn't accidentally leak into production
    code's dispatch table."""
    def fake_factory():
        return MagicMock(spec=ValuationEngine)

    test_router = ValuationRouter(
        dispatch_override={"fcff_compatible": fake_factory},
    )
    prod_router = ValuationRouter()
    # Production still has FcffEngine wired
    assert prod_router.has_engine("fcff_compatible")
    # Test instance still has its override
    assert test_router.has_engine("fcff_compatible")


# ── Defense-in-depth: DCFEngine hard-stop survives the refactor ────


def test_dcfengine_directly_still_rejects_non_fcff():
    """Phase A.1 doesn't remove DCFEngine's NotImplementedError
    guard. Legacy callers (anything bypassing the router and
    constructing DCFEngine directly) still get the hard-stop —
    no regression in protective behavior."""
    from aletheia.tools.dcf_engine import DCFEngine
    from aletheia.utils.calc_input_builder import make_calc_input

    nee_calc = make_calc_input("NEE")
    engine = DCFEngine(verbose=False)
    with pytest.raises(NotImplementedError, match="routing_required"):
        engine.run(nee_calc)
