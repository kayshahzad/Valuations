"""Valuation-engine package — one engine per filer class.

Each filer class in the universe (``business_model`` enum value in
``config.ticker_classification``) maps to an engine module:

  ``fcff_compatible``         → ``fcff_engine.FcffEngine``
  ``routing_required``        → ``rate_base_engine.RateBaseEngine``    (Phase A.3)
  ``ddm_required``            → ``ddm_engine.DdmEngine``               (Phase A.7)
  ``embedded_value_required`` → ``embedded_value_engine.EmbeddedValueEngine``  (Phase A.7)

All engines implement the same ``ValuationEngine`` Protocol declared
in ``base.py``: ``compute_intrinsic_value(calc_input) -> ValuationResult``.
The shared result type lets downstream consumers (lead.py, the
thesis synthesizer, the Deep Dive UI) treat every engine
identically — read ``intrinsic_per_share`` + ``margin_of_safety``,
ignore engine-specific structures.

The ``ValuationRouter`` (``aletheia/tools/valuation_router.py``)
dispatches by ``business_model`` and is the public entry point — no
caller should instantiate an engine directly except for tests.

Centralization-lock invariant: engines call into
``aletheia.calculations.formulas`` for all numeric primitives
(cost_of_equity, cost_of_debt, rate_base_equity_value, ...). They
NEVER redefine a formula locally — the Phase 5 architecture lock
(``tests/architecture/test_single_formula_source.py``) makes that a
build failure.
"""

from aletheia.tools.valuation_engines.base import (
    ValuationEngine,
    ValuationResult,
)
from aletheia.tools.valuation_engines.ddm_engine import DdmEngine
from aletheia.tools.valuation_engines.embedded_value_engine import (
    EmbeddedValueEngine,
)
from aletheia.tools.valuation_engines.fcff_engine import FcffEngine
from aletheia.tools.valuation_engines.mlp_engine import MlpEngine
from aletheia.tools.valuation_engines.rate_base_engine import RateBaseEngine
from aletheia.tools.valuation_engines.residual_income_engine import ResidualIncomeEngine


__all__ = [
    "ValuationEngine", "ValuationResult",
    "FcffEngine", "RateBaseEngine", "DdmEngine", "EmbeddedValueEngine",
    "MlpEngine", "ResidualIncomeEngine",
]
