"""Valuation router — single entry point for all valuation models.

Dispatches by ``business_model`` (from
``config.ticker_classification``) to the appropriate engine in
``aletheia.tools.valuation_engines``. Replaces the previous
"DCFEngine is the only engine + hard-stop NotImplementedError for
non-FCFF filers" pattern with a clean factory dispatch.

Phase A.1 ships with ONE engine wired (FCFF, via the existing
DCFEngine). Phase A.3+ adds rate-base, DDM, embedded-value engines
to the dispatch table — each is a single-line addition here once
the engine module + central formula + config entries are in place.

Architectural guarantees the router preserves:

  1. **Single dispatch surface** — calc_node calls
     ``ValuationRouter().execute(calc_input)`` and gets a
     ``ValuationResult`` back. No caller branches on business_model.

  2. **Closed-set business_models** — every value the enum can
     produce maps to an engine OR raises a clear error.
     ``test_valuation_router_completeness.py`` (Phase A.6) enforces
     completeness — adding a new business_model without registering
     an engine becomes a CI failure.

  3. **Defense in depth** — the existing ``DCFEngine.run()``
     hard-stop at line 966 stays in place. Bypassing the router
     (legacy callers that still construct DCFEngine directly)
     still gets the NotImplementedError for non-FCFF filers.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from aletheia.contracts.interfaces import CalculationInput
from aletheia.tools.valuation_engines.base import (
    ValuationEngine,
    ValuationResult,
)


# ── Dispatch table ──────────────────────────────────────────────────
#
# Maps business_model → engine factory (zero-arg callable that
# returns a fresh engine instance). Lazy import so adding a new
# engine module doesn't bloat import time for callers that only
# need FCFF.
#
# Adding a new engine:
#   1. Implement aletheia/tools/valuation_engines/<name>_engine.py
#      following the ValuationEngine Protocol
#   2. Add factory lambda below
#   3. Update the architecture-lock test to require config entries
#      for the new business_model
#
def _fcff_factory() -> ValuationEngine:
    from aletheia.tools.valuation_engines.fcff_engine import FcffEngine
    return FcffEngine()


def _rate_base_factory() -> ValuationEngine:
    from aletheia.tools.valuation_engines.rate_base_engine import RateBaseEngine
    return RateBaseEngine()


def _ddm_factory() -> ValuationEngine:
    from aletheia.tools.valuation_engines.ddm_engine import DdmEngine
    return DdmEngine()


def _embedded_value_factory() -> ValuationEngine:
    from aletheia.tools.valuation_engines.embedded_value_engine import (
        EmbeddedValueEngine,
    )
    return EmbeddedValueEngine()


def _reit_factory() -> ValuationEngine:
    from aletheia.tools.valuation_engines.reit_engine import ReitEngine
    return ReitEngine()


_DISPATCH_TABLE: Dict[str, Optional[Callable[[], ValuationEngine]]] = {
    "fcff_compatible":         _fcff_factory,         # Phase A.1
    "routing_required":        _rate_base_factory,    # Phase A.3 — RateBaseEngine
    "ddm_required":            _ddm_factory,          # Phase A.7 — DdmEngine
    "embedded_value_required": _embedded_value_factory,  # Phase A.7 — EmbeddedValueEngine
    "reit_required":           _reit_factory,         # Phase A.9 — ReitEngine (AFFO)
}


# ── Public API ──────────────────────────────────────────────────────


class ValuationRouter:
    """Dispatches a ``CalculationInput`` to the right valuation
    engine. Stateless except for an injectable dispatch override
    used by tests."""

    def __init__(
        self,
        *,
        dispatch_override: Optional[Dict[str, Callable[[], ValuationEngine]]] = None,
    ) -> None:
        # Tests inject a partial dispatch (e.g. {"routing_required":
        # fake_engine_factory}) to exercise the routing logic without
        # spinning up a real DCFEngine. Production callers leave this
        # None and get the global _DISPATCH_TABLE.
        self._dispatch = (
            dispatch_override
            if dispatch_override is not None
            else _DISPATCH_TABLE
        )

    def execute(self, calc_input: CalculationInput) -> ValuationResult:
        """Run the appropriate engine for ``calc_input`` and return
        a ``ValuationResult``. Raises:

          - ``UnknownBusinessModelError`` when classification has a
            business_model not present in the dispatch table.
          - ``EngineNotImplementedError`` when the business_model is
            known but the engine for that class isn't built yet
            (e.g. routing_required pre-Phase-A.3).

        Engine-internal failures (data missing, formula returns
        None) flow through as a ``ValuationResult`` with
        ``intrinsic_per_share=None`` + a populated ``warnings``
        list — not as exceptions.
        """
        model = calc_input.classification.business_model
        if model not in self._dispatch:
            raise UnknownBusinessModelError(
                f"No dispatch entry for business_model={model!r}. "
                f"Add it to ``_DISPATCH_TABLE`` in valuation_router.py "
                f"or fix the ticker's classification."
            )

        factory = self._dispatch[model]
        if factory is None:
            raise EngineNotImplementedError(
                f"Engine for business_model={model!r} is registered "
                f"but not yet implemented. "
                f"Ticker {calc_input.classification.ticker!r} "
                f"needs the engine in aletheia/tools/valuation_engines/ "
                f"+ the central formula + specialized inputs config."
            )

        engine = factory()
        return engine.compute_intrinsic_value(calc_input)

    def has_engine(self, business_model: str) -> bool:
        """Test-friendly accessor — used by the architecture lock
        to assert every business_model value has a wired factory."""
        return self._dispatch.get(business_model) is not None


# ── Exceptions ──────────────────────────────────────────────────────


class UnknownBusinessModelError(ValueError):
    """Raised when classification reports a business_model value
    that has no dispatch-table entry. Indicates a classification
    bug, not a missing engine — the router doesn't know how to
    handle it at all."""


class EngineNotImplementedError(NotImplementedError):
    """Raised when the dispatch table HAS an entry for the
    business_model but the engine factory is None (placeholder).
    Distinct from UnknownBusinessModelError — this one says
    'planned, not built yet' rather than 'classification typo.'"""


__all__ = [
    "ValuationRouter",
    "UnknownBusinessModelError",
    "EngineNotImplementedError",
]
