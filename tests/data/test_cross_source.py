"""3.3 / 3.4 — expanded SEC canonical tags + cross-source agreement.

3.4 widened CANONICAL_TAGS from the 8 bottom-line totals to 16 (adding
gross profit, operating income, R&D, capex, D&A, inventory, AR, current
assets). 3.3 compares our cleaned value to the authoritative SEC fact per field.
"""
from __future__ import annotations

import pytest

from aletheia.data.sec_xbrl_validator import (
    CANONICAL_TAGS, build_cross_source_agreement, lookup_xbrl,
)

_EXPANDED = ("GrossProfit", "OperatingIncome", "ResearchAndDevelopment",
             "CapEx", "Depreciation", "Inventory", "AccountsReceivable",
             "CurrentAssets")


def test_expanded_canonical_tags_present():
    for f in _EXPANDED:
        assert f in CANONICAL_TAGS, f"{f} missing from CANONICAL_TAGS (3.4)"
    # non-USD-unit fields stay deferred (documented)
    assert "EPSDiluted" not in CANONICAL_TAGS
    assert "SharesDiluted" not in CANONICAL_TAGS


def _aapl_cached() -> bool:
    return lookup_xbrl("AAPL", "Revenue", 2024) is not None


@pytest.mark.skipif(not _aapl_cached(), reason="AAPL companyfacts not cached")
def test_cross_source_agreement_flags():
    rev = lookup_xbrl("AAPL", "Revenue", 2024).value
    # Revenue matches the filing exactly; GrossProfit deliberately way off.
    our = {"Revenue": rev, "GrossProfit": 1.0}
    xs = build_cross_source_agreement("AAPL", 2024, our)

    assert xs["Revenue"]["flag"] == "validated"
    assert xs["Revenue"]["sec_tag"]                       # authoritative tag recorded
    assert xs["GrossProfit"]["flag"] == "drift"          # 1.0 vs the real value
    # a field we didn't supply is ours_missing, not fabricated
    assert xs["OperatingIncome"]["flag"] == "ours_missing"


@pytest.mark.skipif(not _aapl_cached(), reason="AAPL companyfacts not cached")
def test_both_absent_field_omitted():
    xs = build_cross_source_agreement("AAPL", 1990, {})   # pre-history: no SEC, no ours
    assert xs == {}


# ── duration-concept selection: annual, not quarterly (the ACN bug) ──
from aletheia.data.sec_xbrl_validator import _pick_fy_fact  # noqa: E402


def test_pick_fy_fact_prefers_annual_over_quarter():
    # A 10-K carries both the 12-month annual and the 3-month Q4 under fp=FY with
    # the same end (ACN). Must pick the annual, not the quarter.
    facts = [
        {"fp": "FY", "form": "10-K", "start": "2024-09-01", "end": "2025-08-31",
         "val": 69.7e9, "filed": "2025-10-01"},                       # 12-month
        {"fp": "FY", "form": "10-K", "start": "2025-06-01", "end": "2025-08-31",
         "val": 16.7e9, "filed": "2025-10-01"},                       # 3-month Q4
    ]
    assert _pick_fy_fact(facts, 2025)["val"] == 69.7e9


def test_pick_fy_fact_instant_concept_unaffected():
    # Balance-sheet fact: no `start` (instant) — must still resolve.
    facts = [{"fp": "FY", "form": "10-K", "end": "2025-08-31",
              "val": 65e9, "filed": "2025-10-01"}]
    assert _pick_fy_fact(facts, 2025)["val"] == 65e9
