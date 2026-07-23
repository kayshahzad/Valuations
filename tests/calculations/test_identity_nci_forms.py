"""Phase 2.1 — balance-sheet audit tries the same 4 NCI closure forms as the
schema contract (I3). A record that closes only via MinorityInterest must pass
the audit, not be flagged as a residual."""
from __future__ import annotations

from aletheia.calculations.identity_checks import check_balance_sheet_equation


def _rec(**raw):
    return {"ticker": "T", "fiscal_year": 2024, "period": "FY",
            "clean": {}, "raw": raw}


def test_closes_via_minority_interest():
    # A − (L+E) = 5B (5% gap, above tolerance + materiality floor); closes only
    # once permanent NCI (MinorityInterest) is included — the contract's 3rd form.
    r = _rec(TotalAssets=100e9, TotalLiabilities=70e9, TotalEquity=25e9,
             MinorityInterest=5e9)
    res = check_balance_sheet_equation(r)
    assert res.passed
    assert res.exception_category == "nci_inclusion_required"


def test_closes_via_redeemable_nci_still_works():
    r = _rec(TotalAssets=100e9, TotalLiabilities=70e9, TotalEquity=25e9,
             RedeemableNoncontrollingInterest=5e9)
    res = check_balance_sheet_equation(r)
    assert res.passed


def test_no_spurious_close_without_any_nci():
    # A real 5% gap with no NCI available must still be flagged, not closed.
    r = _rec(TotalAssets=100e9, TotalLiabilities=70e9, TotalEquity=25e9)
    res = check_balance_sheet_equation(r)
    assert not res.passed
    assert res.exception_category is not None


def test_clean_balance_passes_plainly():
    r = _rec(TotalAssets=100e9, TotalLiabilities=70e9, TotalEquity=30e9)
    res = check_balance_sheet_equation(r)
    assert res.passed
    assert res.exception_category is None       # no NCI needed
