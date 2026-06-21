"""Phase A.2 + A.3 tests — rate-base formula, loader, engine, router.

Covers:
  - Central formula ``rate_base_equity_value`` math + edge cases
  - ``rate_base_decomposition`` payload shape
  - ``load_specialized_inputs`` reads NEE entry correctly
  - ``has_required_inputs`` gate behavior
  - ``RateBaseEngine`` happy path (NEE, real DB)
  - ``RateBaseEngine`` empty-state when inputs missing/incomplete
  - ``ValuationRouter`` routes NEE to RateBaseEngine
  - FCFF tickers still route to FcffEngine (no regression)
"""
from __future__ import annotations

import pytest

from aletheia.calculations.formulas import (
    rate_base_decomposition,
    rate_base_equity_value,
)
from aletheia.calculations.specialized_inputs import (
    get_all_specialized_tickers,
    load_specialized_inputs,
)


# ── Central formula: rate_base_equity_value ─────────────────────────


def test_rate_base_basic():
    """NEE-style inputs: $75.1B rate base, 10.95% allowed ROE,
    6.3% Ke, 7.5% explicit growth over 4 years, 2.5% terminal.
    Expected ~$265.8B based on hand calc."""
    ev = rate_base_equity_value(
        rate_base=75.1e9,
        allowed_roe=0.1095,
        cost_of_equity=0.063,
        explicit_growth=0.075,
        explicit_years=4,
        terminal_growth=0.025,
    )
    assert ev is not None
    # Allow ±5% band (rounding tolerance in hand calc)
    assert 250e9 < ev < 285e9, f"Got ${ev/1e9:.1f}B"


def test_rate_base_higher_growth_increases_value():
    """Mechanical sanity — higher growth → higher equity value."""
    low = rate_base_equity_value(
        rate_base=75e9, allowed_roe=0.10, cost_of_equity=0.07,
        explicit_growth=0.03, explicit_years=4, terminal_growth=0.025,
    )
    high = rate_base_equity_value(
        rate_base=75e9, allowed_roe=0.10, cost_of_equity=0.07,
        explicit_growth=0.08, explicit_years=4, terminal_growth=0.025,
    )
    assert high > low


def test_rate_base_higher_ke_decreases_value():
    """Mechanical sanity — higher discount rate → lower PV."""
    low_ke = rate_base_equity_value(
        rate_base=75e9, allowed_roe=0.10, cost_of_equity=0.06,
        explicit_growth=0.05, explicit_years=4, terminal_growth=0.025,
    )
    high_ke = rate_base_equity_value(
        rate_base=75e9, allowed_roe=0.10, cost_of_equity=0.10,
        explicit_growth=0.05, explicit_years=4, terminal_growth=0.025,
    )
    assert low_ke > high_ke


def test_rate_base_returns_none_on_ke_below_terminal_g():
    """Gordon divergence guard — Ke ≤ g_terminal returns None."""
    assert rate_base_equity_value(
        rate_base=75e9, allowed_roe=0.10, cost_of_equity=0.02,
        explicit_growth=0.05, explicit_years=4, terminal_growth=0.025,
    ) is None


def test_rate_base_returns_none_on_missing_inputs():
    """Missing rate_base / allowed_roe / Ke → None (don't fabricate)."""
    assert rate_base_equity_value(
        rate_base=None, allowed_roe=0.10, cost_of_equity=0.07,
        explicit_growth=0.05, explicit_years=4,
    ) is None
    assert rate_base_equity_value(
        rate_base=75e9, allowed_roe=None, cost_of_equity=0.07,
        explicit_growth=0.05, explicit_years=4,
    ) is None
    assert rate_base_equity_value(
        rate_base=75e9, allowed_roe=0.10, cost_of_equity=None,
        explicit_growth=0.05, explicit_years=4,
    ) is None


def test_rate_base_returns_none_on_zero_explicit_years():
    """Zero or negative explicit years is degenerate."""
    assert rate_base_equity_value(
        rate_base=75e9, allowed_roe=0.10, cost_of_equity=0.07,
        explicit_growth=0.05, explicit_years=0,
    ) is None


# ── Decomposition for audit/UI ─────────────────────────────────────


def test_decomposition_shape():
    d = rate_base_decomposition(
        rate_base=75.1e9, allowed_roe=0.1095, cost_of_equity=0.063,
        explicit_growth=0.075, explicit_years=4, terminal_growth=0.025,
    )
    assert d is not None
    assert "yearly" in d
    assert "terminal_value" in d
    assert "pv_terminal" in d
    assert "pv_explicit" in d
    assert "equity_value" in d
    assert "tv_share_of_equity" in d

    # 4 explicit years
    assert len(d["yearly"]) == 4
    # Each year has the right keys
    for yr in d["yearly"]:
        assert {"year", "rate_base", "earnings", "pv"} <= yr.keys()
    # Sum of PVs reconciles
    pv_explicit_sum = sum(y["pv"] for y in d["yearly"])
    assert abs(pv_explicit_sum - d["pv_explicit"]) < 1.0
    assert abs(d["pv_explicit"] + d["pv_terminal"] - d["equity_value"]) < 1.0


def test_decomposition_year_earnings_monotonic_increasing():
    """Earnings should compound positively when growth is positive."""
    d = rate_base_decomposition(
        rate_base=75e9, allowed_roe=0.10, cost_of_equity=0.07,
        explicit_growth=0.075, explicit_years=5, terminal_growth=0.025,
    )
    earnings = [y["earnings"] for y in d["yearly"]]
    assert all(earnings[i] < earnings[i + 1] for i in range(len(earnings) - 1))


# ── Loader: specialized inputs ─────────────────────────────────────


def test_loader_finds_nee_entry():
    inputs = load_specialized_inputs("NEE")
    assert inputs is not None
    assert inputs.ticker == "NEE"
    assert inputs.model == "rate_base"
    assert inputs.params["rate_base_billions"] == 75.1
    assert inputs.params["allowed_roe_pct"] == 10.95
    assert inputs.params["explicit_years"] == 4
    assert inputs.as_of_date == "2025-11-20"
    assert "Florida PSC" in inputs.source


def test_loader_returns_none_for_unknown_ticker():
    assert load_specialized_inputs("ZZZZ_NOT_REAL") is None


def test_loader_normalizes_ticker_case():
    inputs = load_specialized_inputs("nee")  # lowercase
    assert inputs is not None
    assert inputs.ticker == "NEE"


def test_has_required_inputs_for_nee():
    """NEE entry is complete — all required rate-base params filled."""
    inputs = load_specialized_inputs("NEE")
    required = (
        "rate_base_billions", "allowed_roe_pct",
        "explicit_growth_pct", "explicit_years", "terminal_growth_pct",
    )
    assert inputs.has_required_inputs(*required)


def test_has_required_inputs_false_for_placeholder():
    """JPM is a placeholder entry — required DDM params are null."""
    inputs = load_specialized_inputs("JPM")
    assert inputs is not None
    assert inputs.model == "ddm"
    assert not inputs.has_required_inputs(
        "current_dps_annualized", "payout_ratio_pct",
        "sustainable_growth_pct",
    )


def test_get_all_specialized_tickers_covers_universe():
    """The 6 routing/ddm/EV tickers in the universe must all have
    entries in the config (placeholders OK for Phase A.3)."""
    tickers = get_all_specialized_tickers()
    expected = {"NEE", "JPM", "AXP", "BRK-B", "UNH", "CNC"}
    assert expected <= tickers, (
        f"Missing config entries: {expected - tickers}"
    )


# ── RateBaseEngine ────────────────────────────────────────────────


def test_rate_base_engine_produces_iv_for_nee():
    """End-to-end: NEE through the engine yields a real IV with
    proper inputs_snapshot. Uses live market data (Rf, β) and the
    config-stored rate-base params."""
    from aletheia.tools.valuation_engines import RateBaseEngine
    from aletheia.utils.calc_input_builder import make_calc_input

    ci = make_calc_input("NEE")
    result = RateBaseEngine().compute_intrinsic_value(ci)

    assert result.engine == "rate_base"
    assert result.intrinsic_per_share is not None
    assert result.intrinsic_per_share > 50.0   # sanity floor
    assert result.intrinsic_per_share < 300.0  # sanity ceiling
    assert result.equity_value is not None
    assert result.current_price is not None    # live market snapshot
    assert result.margin_of_safety is not None

    # Inputs snapshot captures the analyst-curated config + computed Ke
    snap = result.inputs_snapshot
    assert snap["rate_base"] == 75.1e9
    assert snap["allowed_roe"] == pytest.approx(0.1095)
    assert snap["explicit_growth"] == 0.075
    assert snap["explicit_years"] == 4
    assert snap["terminal_growth"] == 0.025
    assert "Florida PSC" in snap["source"]
    # Cost of equity computed via CAPM, should be reasonable for a utility
    assert 0.04 < snap["cost_of_equity"] < 0.10


def test_rate_base_engine_decomposition_in_engine_specific():
    """Engine surfaces the year-by-year breakdown for the UI to render."""
    from aletheia.tools.valuation_engines import RateBaseEngine
    from aletheia.utils.calc_input_builder import make_calc_input

    ci = make_calc_input("NEE")
    result = RateBaseEngine().compute_intrinsic_value(ci)
    decomp = result.engine_specific["decomposition"]
    assert len(decomp["yearly"]) == 4
    assert decomp["tv_share_of_equity"] > 0.5  # TV-heavy is normal
    assert decomp["pv_explicit"] > 0


# ── Equity-ratio correction (the FPL overstatement bug) ────────────


def test_equity_ratio_scales_value_linearly():
    """Allowed ROE is earned on the equity-funded portion only, so the equity
    value scales linearly with equity_ratio (every earnings term is × ratio)."""
    full = rate_base_equity_value(
        rate_base=75.1e9, allowed_roe=0.1095, cost_of_equity=0.063,
        explicit_growth=0.075, explicit_years=4, terminal_growth=0.025,
        equity_ratio=1.0)
    partial = rate_base_equity_value(
        rate_base=75.1e9, allowed_roe=0.1095, cost_of_equity=0.063,
        explicit_growth=0.075, explicit_years=4, terminal_growth=0.025,
        equity_ratio=0.596)
    assert partial == pytest.approx(full * 0.596, rel=1e-9)


def test_equity_ratio_defaults_to_legacy_behavior():
    """Omitting equity_ratio reproduces the pre-fix (100%-equity) value, so
    configs without an equity ratio are unchanged."""
    with_default = rate_base_equity_value(
        rate_base=50e9, allowed_roe=0.10, cost_of_equity=0.07,
        explicit_growth=0.05, explicit_years=4, terminal_growth=0.025)
    explicit_one = rate_base_equity_value(
        rate_base=50e9, allowed_roe=0.10, cost_of_equity=0.07,
        explicit_growth=0.05, explicit_years=4, terminal_growth=0.025,
        equity_ratio=1.0)
    assert with_default == explicit_one


# ── Sum-of-parts segment formula + engine wiring ───────────────────


def test_segment_multiple_value_residual_and_bridge():
    from aletheia.calculations.formulas import segment_multiple_value
    seg = segment_multiple_value(
        consolidated_net_income=8.18e9,
        regulated_equity_earnings=4.90e9,
        multiple=18.0)
    assert seg["residual_earnings"] == pytest.approx(3.28e9, rel=1e-6)
    assert seg["segment_value"] == pytest.approx(3.28e9 * 18.0, rel=1e-6)


def test_segment_multiple_value_negative_residual_reported_honestly():
    from aletheia.calculations.formulas import segment_multiple_value
    seg = segment_multiple_value(
        consolidated_net_income=3.0e9,
        regulated_equity_earnings=5.0e9, multiple=18.0)
    assert seg["residual_earnings"] < 0          # holdco drag exceeds segment
    assert seg["segment_value"] < 0              # reduces SOTP, not floored


def test_nee_sum_of_parts_is_coherent():
    """NEE through the engine is now a sum-of-parts: regulated FPL leg +
    non-regulated NEER leg. The FPL leg alone must sit BELOW the whole-company
    price (the pre-fix bug had FPL-only > price), and the total = the two legs."""
    from aletheia.tools.valuation_engines import RateBaseEngine
    from aletheia.utils.calc_input_builder import make_calc_input

    result = RateBaseEngine().compute_intrinsic_value(make_calc_input("NEE"))
    sop = result.engine_specific["decomposition"]["sum_of_parts"]

    # equity-ratio applied
    assert result.inputs_snapshot["equity_ratio"] == pytest.approx(0.596)
    # FPL-only per-share is below the whole-company price (coherence)
    assert sop["regulated_per_share"] < result.current_price
    # total = regulated + segment
    assert sop["total_equity_value"] == pytest.approx(
        sop["regulated_value"] + sop["segment_value"], rel=1e-9)
    assert result.equity_value == pytest.approx(sop["total_equity_value"], rel=1e-9)
    # NEER adds value (positive residual) → total IV > FPL-only
    assert result.intrinsic_per_share > sop["regulated_per_share"]
    assert sop["segment_multiple"] == 18.0
    assert "sum-of-parts" in result.inputs_snapshot["valuation_basis"]


# ── ValuationRouter dispatch ─────────────────────────────────────


def test_router_dispatches_nee_to_rate_base_engine():
    """Phase A.3 wires routing_required → RateBaseEngine. NEE is the
    only universe ticker with that classification."""
    from aletheia.tools.valuation_router import ValuationRouter
    from aletheia.utils.calc_input_builder import make_calc_input

    ci = make_calc_input("NEE")
    result = ValuationRouter().execute(ci)
    assert result.engine == "rate_base"
    assert result.intrinsic_per_share is not None


def test_router_still_handles_fcff_tickers():
    """Regression check — FCFF dispatch unchanged after wiring
    rate-base."""
    from aletheia.tools.valuation_router import ValuationRouter
    from aletheia.utils.calc_input_builder import make_calc_input

    ci = make_calc_input("AAPL")
    result = ValuationRouter().execute(ci)
    assert result.engine == "fcff"
    assert result.intrinsic_per_share is not None


def test_router_dispatches_ddm_required_to_ddm_engine_post_a7():
    """Phase A.7 closed the architectural seam: every classification
    value now has its own engine. UNH/CNC (ddm_required) route to
    DdmEngine; CNC returns empty-state because it pays no dividend
    but the engine itself is wired."""
    from aletheia.tools.valuation_router import ValuationRouter
    from aletheia.utils.calc_input_builder import make_calc_input

    # UNH pays a dividend → real IV
    unh = ValuationRouter().execute(make_calc_input("UNH"))
    assert unh.engine == "ddm"
    assert unh.intrinsic_per_share is not None

    # CNC doesn't pay a dividend → empty-state with warning
    cnc = ValuationRouter().execute(make_calc_input("CNC"))
    assert cnc.engine == "ddm"
    assert cnc.intrinsic_per_share is None
    assert cnc.warnings


def test_post_a7_reclassified_tickers_route_correctly():
    """Phase A.7 reclassified JPM/AXP → ddm_required and BRK-B →
    embedded_value_required. Verify the new classifications route
    to the right engines (no more 'routes to rate_base + empties'
    workaround)."""
    from aletheia.tools.valuation_router import ValuationRouter
    from aletheia.utils.calc_input_builder import make_calc_input

    for ticker, expected_engine in [
        ("JPM",   "ddm"),
        ("AXP",   "ddm"),
        ("BRK-B", "embedded_value"),
    ]:
        result = ValuationRouter().execute(make_calc_input(ticker))
        assert result.engine == expected_engine, (
            f"{ticker}: expected engine {expected_engine!r}, "
            f"got {result.engine!r}"
        )
        assert result.intrinsic_per_share is not None, (
            f"{ticker}: expected non-null IV after Phase A.7, "
            f"got None"
        )
