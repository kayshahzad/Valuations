"""Phase-1 falsy-zero fix regressions (fix-plan).

Guards the get_strict() primitive and the F3 equity fix: a missing/zero
TotalEquity must propagate as None/0 (ROE suppressed) rather than being
coerced to a fabricated $1 denominator.
"""
from aletheia.data.cleaning_engine import CleanedRecord
from aletheia.calculations.formulas import roe, invested_capital


def _rec(**clean):
    r = CleanedRecord(ticker="T", fiscal_year=2024, period_end_date=None)
    r.clean = dict(clean)
    return r


class TestGetStrict:
    def test_preserves_legitimate_zero(self):
        # The whole point: a real 0.0 is NOT coerced away.
        assert _rec(X=0.0).get_strict("X") == 0.0

    def test_clean_first_then_raw(self):
        r = CleanedRecord(ticker="T", fiscal_year=2024, period_end_date=None)
        r.clean, r.raw = {}, {"X": 5.0}
        assert r.get_strict("X") == 5.0

    def test_none_on_genuine_miss(self):
        assert _rec().get_strict("X") is None

    def test_differs_from_get_on_zero(self):
        # get() coerces a 0.0 to the fallback; get_strict() preserves it.
        r = _rec(X=0.0)
        assert r.get("X", 1.0) == 1.0
        assert r.get_strict("X") == 0.0


class TestF3EquityFabrication:
    def test_roe_none_on_missing_equity(self):
        # Was: NI / 1.0 -> astronomical ROE. Now: None.
        assert roe(net_income=5e9, total_equity=None) is None

    def test_invested_capital_none_on_missing_equity(self):
        assert invested_capital(
            total_equity=None, total_debt=1e9, cash=0.0, revenue=1e10) is None

    def test_roe_still_suppressed_on_negative_equity(self):
        # Buyback filers (HD/LOW/AZO) — behavior preserved.
        assert roe(net_income=5e9, total_equity=-2e9) is None
