"""Phase A.9 tests — REIT engine (two-stage AFFO growth) + router dispatch.

Covers:
  - ``ReitEngine`` happy path (EQIX, real DB + config)
  - ``ReitEngine`` empty-state when no REIT config / incomplete inputs
  - ``ValuationRouter`` routes EQIX → ReitEngine (engine="reit")
  - The central two-stage formula is the same one DDM uses, fed AFFO/share
  - FCFF tickers still route to FcffEngine (no regression)
"""
from __future__ import annotations

import pytest

from aletheia.tools.valuation_engines.reit_engine import ReitEngine
from aletheia.tools.valuation_router import ValuationRouter
from aletheia.utils.calc_input_builder import make_calc_input


def test_eqix_routes_to_reit_engine_with_affo_iv():
    """EQIX (reit_required) routes to the REIT engine and produces a
    positive AFFO-based IV — not the misleading FCFF DCF."""
    ci = make_calc_input("EQIX")
    assert ci.classification.business_model == "reit_required"
    result = ValuationRouter().execute(ci)
    assert result.engine == "reit"
    assert result.intrinsic_per_share is not None and result.intrinsic_per_share > 0
    es = result.engine_specific or {}
    # AFFO/share drives the value; implied P/AFFO is the IV ÷ AFFO multiple.
    assert es.get("affo_per_share") and es["affo_per_share"] > 0
    assert es.get("implied_p_affo") and es["implied_p_affo"] > 0


def test_reit_engine_empty_state_without_config():
    """A ticker with no model='reit' config returns empty-state (no IV +
    warning), never a fabricated value."""
    class _Cls:
        ticker = "ZZZ"
        business_model = "reit_required"
        sector = industry = lifecycle = ""

    class _CI:
        classification = _Cls()
        df = None

    result = ReitEngine().compute_intrinsic_value(_CI())
    assert result.engine == "reit"
    assert result.intrinsic_per_share is None
    assert result.warnings and "No REIT inputs" in result.warnings[0]


def test_reit_iv_grows_with_affo_growth():
    """Mechanical sanity via the central two-stage formula on AFFO."""
    from aletheia.calculations.formulas import ddm_intrinsic_value as f
    low = f(current_dps=38.0, cost_of_equity=0.08,
            explicit_growth=0.05, explicit_years=5, terminal_growth=0.03)
    high = f(current_dps=38.0, cost_of_equity=0.08,
             explicit_growth=0.10, explicit_years=5, terminal_growth=0.03)
    assert low is not None and high is not None
    assert high > low


def test_router_still_handles_fcff_tickers():
    """No regression — FCFF tickers still go to the FCFF engine."""
    ci = make_calc_input("AAPL")
    result = ValuationRouter().execute(ci)
    assert result.engine == "fcff"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
