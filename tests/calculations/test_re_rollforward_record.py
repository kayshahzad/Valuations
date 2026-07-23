"""Phase 2.4 — RE roll-forward reads RetainedEarnings from the persisted record
(not raw XBRL), so it runs for every ticker instead of silently skipping 520×;
and 'skipped' is a distinct state (was_skipped), not a masked pass.
"""
from __future__ import annotations

from aletheia.calculations.identity_checks import (
    check_retained_earnings_rollforward, IdentityCheckResult,
)


class _StubLoader:
    """No XBRL / companyfacts available — forces the record path."""
    def xbrl_fact(self, *a, **k):
        return None

    def cik(self, ticker):
        return None


def _rec(fy, **raw):
    return {"ticker": "T", "fiscal_year": fy, "period": "FY",
            "clean": {}, "raw": raw}


def test_runs_from_record_without_xbrl():
    # RE_end = RE_beg + NI − Div  →  120 = 100 + 25 − 5  (closes)
    prior = _rec(2023, RetainedEarnings=100e9)
    current = _rec(2024, RetainedEarnings=120e9, NetIncome=25e9, DividendsPaid=5e9)
    res = check_retained_earnings_rollforward(prior, current, _StubLoader())
    assert not res.was_skipped                 # ran (previously would skip: no XBRL)
    assert res.passed


def test_negative_retained_earnings_not_falsy_dropped():
    # accumulated-deficit filer: RE is negative, must not fall through to XBRL.
    prior = _rec(2023, RetainedEarnings=-200e9)
    current = _rec(2024, RetainedEarnings=-180e9, NetIncome=25e9, DividendsPaid=5e9)
    res = check_retained_earnings_rollforward(prior, current, _StubLoader())
    assert not res.was_skipped
    assert res.passed                          # -180 = -200 + 25 - 5


def test_skipped_is_distinct_when_re_absent():
    prior = _rec(2023)                          # no RE anywhere, no XBRL
    current = _rec(2024, NetIncome=25e9)
    res = check_retained_earnings_rollforward(prior, current, _StubLoader())
    assert res.was_skipped                      # distinct state...
    assert res.passed                           # ...but still not a failure


def test_skipped_classmethod_sets_flag():
    r = IdentityCheckResult.skipped(
        ticker="T", fiscal_year=2024, period="FY",
        identity_name="retained_earnings_rollforward", reason="x")
    assert r.was_skipped and r.passed
