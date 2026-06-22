"""Industrial-metric refusal on financials (CF-R21).

ROIC−WACC value creation, EV/EBITDA, Net-Debt/EBITDA, interest coverage, FCF
margin/yield are all meaningless on a deposit-funded balance sheet (JPM: ROIC
4.6% "weak", EV/EBITDA −17.8×, Net-Debt/EBITDA −28× from deposits-as-net-cash).
They're conclusion-inverting, so the display modules refuse them for financials
while keeping the bank-valid ratios (P/E, P/B, ROE, ROA).
"""

from __future__ import annotations

import pytest


def test_multiple_decomposition_refuses_financials():
    pytest.importorskip("pandas")
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.multiple_decomposition import MultipleDecomposition
    try:
        r = MultipleDecomposition().run(make_calc_input("JPM"))
    except Exception as e:
        pytest.skip(f"JPM unavailable: {e}")
    assert r.applicable is False
    assert r.signal == "not_applicable"
    assert r.value_creation == ""          # no "destroying" / "weak" verdict
    d = r.to_dict()
    assert d["applicable"] is False and d.get("warnings")


def test_multiple_decomposition_keeps_industrials():
    pytest.importorskip("pandas")
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.multiple_decomposition import MultipleDecomposition
    try:
        r = MultipleDecomposition().run(make_calc_input("AAPL"))
    except Exception as e:
        pytest.skip(f"AAPL unavailable: {e}")
    assert r.applicable is True
    assert r.value_creation in ("creating", "destroying", "neutral")


def test_comprehensive_ratios_suppress_industrial_metrics_for_financials():
    pytest.importorskip("pandas")
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.utils.financial_metrics import _compute_ratios
    try:
        df = make_calc_input("JPM").df
    except Exception as e:
        pytest.skip(f"JPM unavailable: {e}")
    r = _compute_ratios(df, market_cap=9e11, current_price=329.0, is_financial=True)
    # meaningless → nulled
    assert r["profitability"]["roic"] is None
    assert r["profitability"]["fcf_margin"] is None
    assert r["leverage"]["net_debt_to_ebitda"] is None
    assert r["leverage"]["interest_coverage"] is None
    assert r["valuation"]["ev_ebitda"] is None
    assert r["valuation"]["fcf_yield"] is None
    # bank-valid → kept
    assert r["valuation"]["pe"] is not None
    assert r["valuation"]["pb"] is not None
    assert r["profitability"]["roe"] is not None
    assert "financials_na_note" in r

    # industrials (is_financial=False) keep everything
    r2 = _compute_ratios(df, market_cap=9e11, current_price=329.0, is_financial=False)
    assert r2["profitability"]["roic"] is not None or r2["profitability"]["roe"] is not None
    assert "financials_na_note" not in r2
