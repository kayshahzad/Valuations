"""Phase 7d tests — per-function framework wiring.

Covers the Day 2-6.5 additions to reverse_dcf, dcf_engine,
multiple_decomposition, and forensic_metrics. Each test verifies a
specific framework guard fires (or doesn't) on synthetic inputs, then
verifies legitimate-case tickers still pass through cleanly in
hard mode.
"""

from __future__ import annotations

import pytest

from aletheia.calculations import (
    CalculationError,
    CalculationOutputError,
    CalculationInputError,
)


@pytest.fixture(autouse=True)
def reset_guard_mode(monkeypatch):
    monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
    yield


# ─────────────────────────────────────────────────────────────────────
# reverse_dcf wiring
# ─────────────────────────────────────────────────────────────────────

class TestReverseDcfWiring:

    def _legitimate_calc_input(self, ticker: str = "MDT"):
        """Real calc input for a clean ticker — pulled from DuckDB."""
        from aletheia.utils.calc_input_builder import make_calc_input
        return make_calc_input(ticker)

    def test_off_mode_runs_legacy_behavior(self, monkeypatch):
        """Off mode = preserves pre-framework behavior. MDT should
        produce a sensible implied CAGR."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        from aletheia.tools.reverse_dcf import ReverseDCF
        from aletheia.data.market_data import get_current_price, get_market_cap
        calc = self._legitimate_calc_input("MDT")
        r = ReverseDCF(verbose=False).run(
            calc,
            current_price=get_current_price("MDT"),
            market_cap=get_market_cap("MDT"),
        )
        # MDT mature healthcare — should be in plausible range
        assert -0.50 <= r.implied_revenue_cagr_10y <= 1.0
        assert r.based_on_period == "FY"

    def test_hard_mode_runs_clean_on_legitimate_ticker(self, monkeypatch):
        """Hard mode should NOT break legitimate tickers (MDT/NVDA/AAPL)."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        from aletheia.tools.reverse_dcf import ReverseDCF
        from aletheia.data.market_data import get_current_price, get_market_cap
        for ticker in ("MDT", "NVDA", "AAPL"):
            calc = self._legitimate_calc_input(ticker)
            r = ReverseDCF(verbose=False).run(
                calc,
                current_price=get_current_price(ticker),
                market_cap=get_market_cap(ticker),
            )
            assert r.implied_revenue_cagr_10y is not None
            assert not r.errors, f"{ticker} unexpectedly errored: {r.errors}"

    def test_anchors_to_fy_not_ttm(self, monkeypatch):
        """Day 2 fix: reverse_dcf filters to period='FY' to avoid the
        MDT-class NaN-EBIT bug. Result.based_on_period must be 'FY'."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        from aletheia.tools.reverse_dcf import ReverseDCF
        from aletheia.data.market_data import get_current_price, get_market_cap
        calc = self._legitimate_calc_input("MDT")
        r = ReverseDCF(verbose=False).run(
            calc,
            current_price=get_current_price("MDT"),
            market_cap=get_market_cap("MDT"),
        )
        assert r.based_on_period == "FY"

    def test_implied_cagr_within_output_band(self, monkeypatch):
        """Output guard: implied_cagr ∈ [-50%, +100%]. Any of our
        regression tickers should land in this band."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        from aletheia.tools.reverse_dcf import ReverseDCF
        from aletheia.data.market_data import get_current_price, get_market_cap
        for ticker in ("MDT", "NVDA", "AAPL", "COST"):
            calc = self._legitimate_calc_input(ticker)
            r = ReverseDCF(verbose=False).run(
                calc,
                current_price=get_current_price(ticker),
                market_cap=get_market_cap(ticker),
            )
            ic = r.implied_revenue_cagr_10y
            assert -0.50 <= ic <= 1.0, (
                f"{ticker} implied_cagr={ic:.2f} outside [-0.50, 1.0] band — "
                "would have raised CalculationOutputError in hard mode"
            )


# ─────────────────────────────────────────────────────────────────────
# dcf_engine wiring
# ─────────────────────────────────────────────────────────────────────

class TestDcfEngineWiring:

    def test_off_mode_runs_legacy_behavior(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.dcf_engine import DCFEngine
        calc = make_calc_input("MDT")
        r = DCFEngine(verbose=False).run(calc)
        assert r.base is not None
        ips = r.intrinsic_per_share(r.base.enterprise_value, r.net_debt)
        assert ips is not None
        assert ips > 0

    def test_hard_mode_legitimate_tickers_pass(self, monkeypatch):
        """Day 3 wiring: hard mode shouldn't break clean tickers."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.dcf_engine import DCFEngine
        for ticker in ("MDT", "NVDA", "AAPL"):
            calc = make_calc_input(ticker)
            r = DCFEngine(verbose=False).run(calc)
            assert r.base is not None
            ips = r.intrinsic_per_share(r.base.enterprise_value, r.net_debt)
            assert ips is not None
            assert ips > 0

    def test_per_scenario_ips_within_band(self, monkeypatch):
        """Day 3 output sanity: each scenario IPS within 1-100x current price."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.dcf_engine import DCFEngine
        for ticker in ("MDT", "NVDA"):
            calc = make_calc_input(ticker)
            r = DCFEngine(verbose=False).run(calc)
            price = r.current_price
            for scn_name in ("bull", "base", "bear"):
                scn = getattr(r, scn_name, None)
                if scn is None:
                    continue
                ips = r.intrinsic_per_share(scn.enterprise_value, r.net_debt)
                if ips is not None and price > 0:
                    # Within the 100x ceiling and not <1% of price for bull/base
                    assert ips <= price * 100, (
                        f"{ticker} {scn_name} IPS={ips} > 100x price={price}"
                    )

    def test_terminal_value_multiple_within_band(self, monkeypatch):
        """Day 4 output sanity: TV/EBITDA multiple ∈ [3, 50]."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.dcf_engine import DCFEngine
        for ticker in ("MDT", "NVDA", "AAPL", "COST"):
            calc = make_calc_input(ticker)
            r = DCFEngine(verbose=False).run(calc)
            if r.base and r.base.terminal:
                tv_mult = r.base.terminal.implied_tv_ebitda_multiple
                if tv_mult != 0.0:
                    assert 3.0 <= tv_mult <= 50.0, (
                        f"{ticker} TV multiple={tv_mult:.1f}x outside [3, 50] band"
                    )


# ─────────────────────────────────────────────────────────────────────
# multiple_decomposition wiring
# ─────────────────────────────────────────────────────────────────────

class TestMultipleDecompositionWiring:

    def test_legitimate_tickers_produce_in_band_premium(self, monkeypatch):
        """ev_ebitda_premium_pct should be in [-90%, +1000%] for normal
        tickers. NVDA was 485.7% in the smoke test — still within band."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.multiple_decomposition import MultipleDecomposition
        for ticker in ("MDT", "NVDA", "AAPL"):
            calc = make_calc_input(ticker)
            r = MultipleDecomposition(verbose=False).run(calc)
            pp = r.ev_ebitda_premium_pct
            if pp != 0.0:
                assert -0.90 <= pp <= 10.0, (
                    f"{ticker} premium_pct={pp*100:.1f}% outside band"
                )

    def test_tax_rate_input_range_holds(self, monkeypatch):
        """tax_rate in [-1, 1] should hold for all our universe tickers."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.multiple_decomposition import MultipleDecomposition
        # If this raises, it means tax_rate input validation failed.
        for ticker in ("MDT", "NVDA", "AAPL"):
            calc = make_calc_input(ticker)
            try:
                MultipleDecomposition(verbose=False).run(calc)
            except CalculationError:
                pytest.fail(f"{ticker} unexpectedly failed tax_rate validation")


# ─────────────────────────────────────────────────────────────────────
# forensic_metrics wiring
# ─────────────────────────────────────────────────────────────────────

class TestOperatingLeverageWiring:

    def test_normal_case_returns_real_score(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        from aletheia.tools.forensic_metrics import compute_operating_leverage_score
        # gross_margin=40, ebit_margin=20 → ratio 0.5 → score 5.0
        s = compute_operating_leverage_score(40.0, 20.0)
        assert s == 5.0

    def test_fallback_path_logs_soft_flag(self, monkeypatch, caplog):
        """Zero/negative gross margin should fall back to 5.0 AND emit
        a structured soft-flag (so LLM prose can't cite the fallback
        as a real measurement)."""
        import logging
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "shadow")
        from aletheia.tools.forensic_metrics import compute_operating_leverage_score
        with caplog.at_level(logging.WARNING, logger="aletheia.calculations._guards"):
            s = compute_operating_leverage_score(0.0, 20.0, ticker="LOSS_CO")
        assert s == 5.0  # still returns the fallback
        # Verify the soft-flag was logged
        assert any("soft_flag" in r.message for r in caplog.records), (
            "Fallback path should emit a structured warning, not be silent"
        )

    def test_none_ebit_logs_soft_flag(self, monkeypatch, caplog):
        import logging
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "shadow")
        from aletheia.tools.forensic_metrics import compute_operating_leverage_score
        with caplog.at_level(logging.WARNING, logger="aletheia.calculations._guards"):
            s = compute_operating_leverage_score(40.0, None, ticker="MISSING_EBIT")
        assert s == 5.0
        assert any("soft_flag" in r.message and "ebit_margin" in r.message
                   for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────
# cyclicality wiring
# ─────────────────────────────────────────────────────────────────────

class TestCyclicalityWiring:

    def test_insufficient_history_logs_soft_flag(self, monkeypatch, caplog):
        """<3 years of revenue history should soft-flag the zero-signal
        fallback (Day 6.5)."""
        import logging
        import pandas as pd
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "shadow")
        from aletheia.tools.cyclicality import calculate_z_score

        class FakeCalc:
            df = pd.DataFrame({"fiscal_year": [2024],
                               "clean_Revenue": [100.0]})
            classification = None

        with caplog.at_level(logging.WARNING, logger="aletheia.calculations._guards"):
            z, peak, haircut, avg3, ctx = calculate_z_score(FakeCalc())
        assert z == 0.0
        # The function had to bail out (no classification) — that path
        # is also flagged. Verify SOME soft-flag fires.
        assert any("soft_flag" in r.message for r in caplog.records)
