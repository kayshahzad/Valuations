"""Degenerate-FCFF suppression: pre-profitability (negative operating income)
tickers must NOT emit a fabricated headline IV; profitable-but-conservatively-
valued tickers must be untouched.

Regression for the NET (Cloudflare) case: negative normalized operating income
seeded a meaningless NOPAT projection that produced a ~$2 intrinsic value against
a ~$262 price. The engine now suppresses the headline IV (intrinsic_per_share is
None + a reason is surfaced) rather than showing the fabricated number.
"""
import pytest

from aletheia.tools.dcf_engine import DCFEngine
from aletheia.tools.valuation_router import ValuationRouter
from aletheia.utils.calc_input_builder import make_calc_input


def _has(ticker: str) -> bool:
    try:
        ci = make_calc_input(ticker)
        return not ci.df.empty
    except Exception:
        return False


@pytest.mark.skipif(not _has("NET"), reason="NET not ingested in this DB")
def test_negative_oi_ticker_is_suppressed():
    """NET has negative normalized operating income → headline IV suppressed."""
    result = DCFEngine(verbose=False).run(make_calc_input("NET"))
    assert result.ebit <= 0, "precondition: NET should have negative/zero EBIT"
    assert result.iv_suppressed is True
    assert "operating income" in result.iv_suppressed_reason.lower()

    # Every serialization path must null the per-share value.
    d = result.to_dict()
    assert d["iv_suppressed"] is True
    assert d["base_intrinsic_per_share"] is None
    assert d["bull_intrinsic_per_share"] is None

    # Router / FcffEngine wrapper must not surface a fabricated number either.
    vr = ValuationRouter().execute(make_calc_input("NET"))
    assert vr.intrinsic_per_share is None
    assert vr.engine_specific.get("iv_suppressed") is True


@pytest.mark.parametrize("ticker", ["AAPL", "TSLA", "TXN"])
def test_profitable_tickers_not_suppressed(ticker):
    """Positive-OI names — even conservatively valued (TSLA ~5%, TXN ~16% of
    price) — keep their real IV. Suppression must be surgical, not a blanket
    'low IV' filter."""
    if not _has(ticker):
        pytest.skip(f"{ticker} not ingested")
    result = DCFEngine(verbose=False).run(make_calc_input(ticker))
    assert result.ebit > 0
    assert result.iv_suppressed is False
    assert result.to_dict()["base_intrinsic_per_share"] is not None
