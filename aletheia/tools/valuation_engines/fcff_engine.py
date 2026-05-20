"""FCFF engine — thin wrapper around the existing ``DCFEngine``.

Phase A.1 of the valuation-router refactor. Adapts ``DCFEngine.run()``
output (``DCFResult``) into the shared ``ValuationResult`` shape so
the router + downstream consumers can stay engine-agnostic.

ZERO behavior change for FCFF-compatible tickers: the same DCFEngine
runs, the same bull/base/bear scenarios get computed, the same
projections + terminal value + WACC math fires. This wrapper only
reshapes the output.

The full ``DCFResult`` is preserved under
``engine_specific["dcf_result"]`` so legacy consumers that need the
rich scenario detail can opt in. New consumers should read the
top-level ``ValuationResult`` fields (``intrinsic_per_share``,
``equity_value``, ``margin_of_safety``).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from aletheia.contracts.interfaces import CalculationInput
from aletheia.tools.dcf_engine import DCFEngine, DCFResult
from aletheia.tools.valuation_engines.base import ValuationResult


class FcffEngine:
    """FCFF-DCF valuation for ``fcff_compatible`` tickers.

    Composition over inheritance — instantiates an internal
    ``DCFEngine``, runs it, projects the output. The DCFEngine
    instance is not exposed; consumers that need its internals
    should pull them from ``ValuationResult.engine_specific``.
    """

    def __init__(self, *, verbose: bool = False) -> None:
        self._dcf = DCFEngine(verbose=verbose)

    def compute_intrinsic_value(
        self, calc_input: CalculationInput,
    ) -> ValuationResult:
        result: DCFResult = self._dcf.run(calc_input)

        ticker = result.ticker
        fy = result.fiscal_year

        # IPS isn't stored on ScenarioResult — DCFResult exposes
        # ``intrinsic_per_share(ev, net_debt)`` which computes it on
        # demand from the scenario's EV plus the firm's net debt.
        # Same pattern the existing serving-report writer uses.
        base = result.base
        if base is not None:
            ev = base.enterprise_value
            intrinsic = result.intrinsic_per_share(ev, result.net_debt)
            equity = result.ev_to_equity(ev, result.net_debt) if ev else None
            mos = result.upside(intrinsic) if intrinsic else None
        else:
            intrinsic = equity = mos = None

        # Scenarios snapshot — surface bull/bear alongside base so the
        # Deep Dive can render the spread without re-running the engine.
        scenarios = {
            name: _scenario_summary(result, getattr(result, name))
            for name in ("bull", "base", "bear")
            if getattr(result, name) is not None
        }

        return ValuationResult(
            ticker=ticker,
            fiscal_year=fy,
            engine="fcff",
            intrinsic_per_share=intrinsic,
            equity_value=equity,
            current_price=result.current_price or None,
            margin_of_safety=mos,
            inputs_snapshot={
                "wacc_base":     result.wacc_base,
                "risk_free_rate": result.risk_free_rate,
                "beta":          result.beta,
                "tax_rate_source": result.tax_rate_source,
                "shares_diluted": result.shares_diluted,
                "net_debt":      result.net_debt,
                "base_period":   result.base_period,
            },
            notes="Standard FCFF DCF (bull / base / bear); see engine_specific.scenarios for projections.",
            warnings=list(result.warnings or []) + list(result.errors or []),
            engine_specific={
                "dcf_result": result,   # full DCFResult for legacy consumers
                "scenarios":  scenarios,
                "confidence": result.confidence,
            },
        )


def _scenario_summary(
    dcf_result: DCFResult, scenario: Any,
) -> Optional[Dict[str, Any]]:
    """Pull the key fields from one ScenarioResult into a flat dict
    the UI can render without importing the scenario type.

    IPS + equity_value + MoS aren't stored on ScenarioResult — they
    compute from (EV, net_debt) via DCFResult helpers. We materialize
    them here so the UI doesn't need a DCFResult reference."""
    if scenario is None:
        return None
    ev = getattr(scenario, "enterprise_value", None)
    ips = dcf_result.intrinsic_per_share(ev, dcf_result.net_debt) if ev else None
    equity = (
        dcf_result.ev_to_equity(ev, dcf_result.net_debt)
        if ev is not None else None
    )
    mos = dcf_result.upside(ips) if ips else None
    return {
        "intrinsic_per_share": ips,
        "equity_value":        equity,
        "margin_of_safety":    mos,
        "enterprise_value":    ev,
        "wacc":                getattr(getattr(scenario, "assumptions", None),
                                       "wacc", None),
    }


__all__ = ["FcffEngine"]
