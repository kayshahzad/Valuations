"""Scenario bands for specialized engines.

Single-point specialized engines (residual-income, rate-base, MLP, DDM, REIT,
embedded-value) now emit a bull/bear sensitivity band by flexing ONE key driver,
so the three-scenario panel shows a real range instead of one bar.
"""

from __future__ import annotations

import pytest

from aletheia.tools.valuation_engines.base import scenario_band


# ── the shared helper ───────────────────────────────────────────────

def test_scenario_band_legs_and_mos():
    band = scenario_band(
        recompute=lambda x: x * 10.0, bear_value=9.0, bull_value=13.0,
        current_price=100.0, driver="normalized ROE", driver_unit="pp")
    assert band["driver"] == "normalized ROE"
    assert band["bear"]["intrinsic_per_share"] == pytest.approx(90.0)
    assert band["bull"]["intrinsic_per_share"] == pytest.approx(130.0)
    assert band["bear"]["margin_of_safety"] == pytest.approx(-0.10)
    assert band["bull"]["margin_of_safety"] == pytest.approx(0.30)


def test_scenario_band_none_when_uncomputable():
    assert scenario_band(
        recompute=lambda x: None, bear_value=1, bull_value=2,
        current_price=10.0, driver="x") is None


def test_scenario_band_survives_recompute_exception():
    def _boom(x):
        if x < 0:
            raise ValueError("bad")
        return x
    band = scenario_band(recompute=_boom, bear_value=-1, bull_value=5,
                         current_price=10.0, driver="x")
    assert band["bear"] is None
    assert band["bull"]["intrinsic_per_share"] == pytest.approx(5.0)


# ── each specialized engine produces a monotonic band ───────────────

@pytest.mark.parametrize("ticker,engine,driver", [
    ("CNC", "residual_income", "normalized ROE"),
    ("NEE", "rate_base", "allowed ROE"),
    ("ET", "mlp", "EV/EBITDA multiple"),
    ("JPM", "ddm", "dividend growth"),
    ("EQIX", "reit", "AFFO growth"),
    ("BRK-B", "embedded_value", "expected ROE"),
])
def test_specialized_engine_band_is_monotonic(ticker, engine, driver):
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.valuation_router import ValuationRouter
    try:
        calc = make_calc_input(ticker)
    except Exception as e:
        pytest.skip(f"{ticker} not available: {e}")

    vr = ValuationRouter().execute(calc)
    assert vr.engine == engine
    band = (vr.engine_specific or {}).get("scenario_band")
    assert band is not None, f"{ticker}: no scenario_band"
    assert band["driver"] == driver
    bear = band["bear"]["intrinsic_per_share"]
    bull = band["bull"]["intrinsic_per_share"]
    base = vr.intrinsic_per_share
    # higher driver value → higher IV (monotone), and base sits between
    assert bear < base < bull, (ticker, bear, base, bull)


# ── serving path drops the band into bear/bull slots ────────────────

def test_serving_path_populates_bear_bull():
    import api_main
    try:
        d = api_main._compute_dcf_live("CNC")
    except Exception as e:
        pytest.skip(f"CNC serving unavailable: {e}")
    assert d["bear"] is not None and d["bull"] is not None
    assert d["bear"]["intrinsic_per_share"] < d["base"]["intrinsic_per_share"]
    assert d["bull"]["intrinsic_per_share"] > d["base"]["intrinsic_per_share"]
    assert (d.get("scenario_band") or {}).get("driver") == "normalized ROE"
