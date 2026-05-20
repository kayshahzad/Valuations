"""Shared types for the valuation-engine package.

``ValuationEngine`` is the Protocol every engine implements.
``ValuationResult`` is the shared return type — minimum fields every
engine populates (intrinsic_per_share, equity_value, MoS), plus an
optional ``engine_specific`` dict for richer per-engine output (FCFF
scenarios, rate-base parameters, DDM payout-ratio breakdown, etc.).

Design intent — three properties downstream consumers can rely on
regardless of which engine ran:

  1. **Same shape**: ``result.intrinsic_per_share`` and
     ``result.margin_of_safety`` are present on every result, even if
     null. The serving report writer, thesis synthesizer, and Deep
     Dive UI read these fields without branching on engine type.

  2. **Provenance trail**: ``engine`` field carries the model name
     ("fcff" / "rate_base" / "ddm"); ``inputs_snapshot`` captures
     the specific inputs the engine consumed (rate base, allowed
     ROE, source citation for analyst inputs). Audit-grade.

  3. **Honest gaps**: missing data → ``intrinsic_per_share = None``
     plus a warning. The engine never invents a value to fill a gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from aletheia.contracts.interfaces import CalculationInput


@dataclass(frozen=True)
class ValuationResult:
    """Universal shape returned by every valuation engine.

    The thinness is intentional — pinning a small canonical surface
    lets the router + downstream consumers stay engine-agnostic.
    Engine-specific richness goes in ``engine_specific``, which is
    an opaque dict the engine owns and other consumers shouldn't
    introspect except to display.
    """

    # Identity
    ticker: str
    fiscal_year: int
    engine: str                                    # "fcff" / "rate_base" / "ddm" / "embedded_value"

    # Core valuation outputs (every engine populates these or sets to None)
    intrinsic_per_share: Optional[float]
    equity_value: Optional[float]
    current_price: Optional[float]
    margin_of_safety: Optional[float]              # (IV - price) / price; > 0 = undervalued

    # Provenance + audit
    inputs_snapshot: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    warnings: List[str] = field(default_factory=list)

    # Engine-specific (FCFF scenarios, rate-base parameters, DDM
    # payout breakdown). Consumers may render these in detail panels
    # but should not require any specific keys to be present.
    engine_specific: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ValuationEngine(Protocol):
    """Engine interface — one method, ``compute_intrinsic_value``.

    Engines accept a ``CalcInput`` (the existing pipeline-shared
    type carrying the cleaned DataFrame + classification +
    valuation_profile) and return a ``ValuationResult``. Failures
    inside the engine propagate as exceptions; the router catches
    them at the boundary and emits a ``ValuationResult`` with
    ``intrinsic_per_share=None`` + a populated ``warnings`` list.
    """

    def compute_intrinsic_value(self, calc_input: CalculationInput) -> ValuationResult: ...


__all__ = ["ValuationEngine", "ValuationResult"]
