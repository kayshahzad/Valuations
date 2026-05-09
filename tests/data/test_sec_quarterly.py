"""Phase Q-2: SEC-derived TTM cumulative-period math.

SEC 10-Q facts are CUMULATIVE year-to-date (a fp=Q3 record covers the
first 9 months of the FY, not just Q3 standalone). The TTM formula:

    TTM = prior_FY_annual + latest_cumulative_Q − prior_year_same_Q_cumulative

These tests pin the math against synthetic XBRL fact lists so the
source-primacy swap from FMP-derived to SEC-derived stays observable
and correct as the SEC parse path ships.
"""

from __future__ import annotations

from unittest.mock import patch

from aletheia.data import sec_quarterly as sq


def _fact(form, fy, fp, start, end, val):
    return {"form": form, "fy": fy, "fp": fp,
            "start": start, "end": end, "val": val}


def _annual(fy, end, val):
    """10-K canonical fact (12-month window ending at fy-end)."""
    return _fact("10-K", fy, "FY",
                 start=f"{int(end[:4]) - 1}-09-29", end=end, val=val)


def _quarter(fy, fp, start, end, val):
    return _fact("10-Q", fy, fp, start=start, end=end, val=val)


# ── Helper resolution ─────────────────────────────────────────────────

def test_matching_prior_year_picks_latest_end_when_multiple_match():
    """SEC re-tags prior-year comparatives with the current filing's
    fy. When two records both claim (fy=2025, fp=Q1), we want the one
    with the most recent `end` — the actual period being reported."""
    facts = [
        _quarter(2025, "Q1", "2022-09-25", "2022-12-31", val=100),  # comparative
        _quarter(2025, "Q1", "2024-09-29", "2024-12-28", val=124),  # actual
    ]
    r = sq._matching_prior_year(facts, target_fy=2026, target_fp="Q1")
    assert r["val"] == 124


def test_annual_fact_filters_to_fp_FY_then_latest_end():
    """10-K filings include 2 prior years' comparable annuals — all
    tagged with the current filing's fy. Pick the one with the most
    recent `end` to get the actual annual being reported."""
    facts = [
        _annual(2025, "2023-09-30", 383e9),
        _annual(2025, "2024-09-28", 391e9),
        _annual(2025, "2025-09-27", 416e9),
    ]
    r = sq._annual_fact(facts, fy=2025)
    assert r["val"] == 416e9


# ── TTM cumulative-period math ────────────────────────────────────────

def test_ttm_from_cumulative_q1_anchor():
    """Latest 10-Q is fp=Q1 (3 months into new FY). TTM = prior_annual +
    latest_q − prior_year_same_q. Apple-shaped numbers."""
    facts = [
        _quarter(2026, "Q1", "2025-09-28", "2025-12-27", val=143.8e9),  # latest
        _quarter(2025, "Q1", "2024-09-29", "2024-12-28", val=124.3e9),  # prior-year same
        _annual(2025, "2025-09-27", val=394.7e9),                       # prior-FY annual
    ]
    ttm, latest = sq._ttm_from_cumulative(facts)
    expected = 394.7e9 + 143.8e9 - 124.3e9   # 414.2B
    assert abs(ttm - expected) < 1.0
    assert latest["fp"] == "Q1"
    assert latest["end"] == "2025-12-27"


def test_ttm_from_cumulative_q3_anchor():
    """fp=Q3 latest → 9-month cumulative on both sides. Math invariant."""
    facts = [
        _quarter(2026, "Q3", "2025-09-28", "2026-06-30", val=320e9),  # 9 months FY26
        _quarter(2025, "Q3", "2024-09-29", "2025-06-29", val=300e9),  # 9 months FY25
        _annual(2025, "2025-09-27", val=394.7e9),
    ]
    ttm, _ = sq._ttm_from_cumulative(facts)
    expected = 394.7e9 + 320e9 - 300e9
    assert abs(ttm - expected) < 1.0


def test_ttm_from_cumulative_returns_none_when_prior_year_q_missing():
    """No prior-year same-quarter → can't run the math; caller stamps
    skip_reason rather than producing a wrong number."""
    facts = [
        _quarter(2026, "Q1", "2025-09-28", "2025-12-27", val=143.8e9),
        _annual(2025, "2025-09-27", val=394.7e9),
        # NO prior-year Q1 fact
    ]
    ttm, _ = sq._ttm_from_cumulative(facts)
    assert ttm is None


def test_ttm_from_cumulative_returns_none_when_prior_annual_missing():
    """No prior-FY 10-K → can't anchor the rolling sum."""
    facts = [
        _quarter(2026, "Q1", "2025-09-28", "2025-12-27", val=143.8e9),
        _quarter(2025, "Q1", "2024-09-29", "2024-12-28", val=124.3e9),
        # NO prior-FY 10-K
    ]
    ttm, _ = sq._ttm_from_cumulative(facts)
    assert ttm is None


# ── End-to-end derive_ttm_from_sec ────────────────────────────────────

def test_derive_ttm_from_sec_skipped_when_no_companyfacts():
    with patch("aletheia.data.sec_quarterly._load_facts", return_value=None):
        r = sq.derive_ttm_from_sec("FAKE")
    assert r.record is None
    assert r.skip_reason == "sec_companyfacts_not_cached"


def test_derive_ttm_from_sec_skipped_when_revenue_tag_unresolved():
    """Companyfacts JSON exists but no Revenue tag in any fallback —
    skip cleanly with a structured reason."""
    fake_facts = {"facts": {"us-gaap": {}}}
    with patch("aletheia.data.sec_quarterly._load_facts",
               return_value=fake_facts):
        r = sq.derive_ttm_from_sec("FAKE")
    assert r.record is None
    assert r.skip_reason == "sec_revenue_tag_unresolved"


def test_extract_standalone_quarters_subtracts_cumulative_chain():
    """Q1 standalone = Q1 cum; Q2 = Q2 cum - Q1 cum; Q3 = Q3 cum - Q2 cum.
    Apple-shaped numbers across one fiscal year."""
    facts = [
        _quarter(2025, "Q1", "2024-09-29", "2024-12-28", val=124e9),
        _quarter(2025, "Q2", "2024-09-29", "2025-03-29", val=219e9),
        _quarter(2025, "Q3", "2024-09-29", "2025-06-28", val=313e9),
    ]
    out = sq.extract_standalone_quarters(facts)
    by_fp = {q["fp"]: q for q in out if q["fy"] == 2025}
    assert abs(by_fp["Q1"]["val_standalone"] - 124e9) < 1
    assert abs(by_fp["Q2"]["val_standalone"] - (219e9 - 124e9)) < 1
    assert abs(by_fp["Q3"]["val_standalone"] - (313e9 - 219e9)) < 1


def test_extract_standalone_quarters_returns_most_recent_first():
    facts = [
        _quarter(2025, "Q1", "2024-09-29", "2024-12-28", val=124e9),
        _quarter(2026, "Q1", "2025-09-28", "2025-12-27", val=143e9),
        _quarter(2025, "Q2", "2024-09-29", "2025-03-29", val=219e9),
    ]
    out = sq.extract_standalone_quarters(facts)
    # Sorted by (fy, fp) descending → 2026 Q1 first, then 2025 Q2, then 2025 Q1
    assert out[0]["fy"] == 2026
    assert out[0]["fp"] == "Q1"


def test_extract_standalone_quarters_handles_missing_prior_for_q2():
    """If Q1 cumulative is missing, Q2 standalone returns None rather
    than silently producing the cumulative value as if it were standalone."""
    facts = [
        _quarter(2025, "Q2", "2024-09-29", "2025-03-29", val=219e9),
    ]
    out = sq.extract_standalone_quarters(facts)
    assert out[0]["val_standalone"] is None


def test_derive_ttm_from_sec_stamps_source_primacy():
    """Synthetic Apple-shaped facts → record carries
    ttm_source='sec_derived_quarters' so the FMP→SEC swap is
    forensically observable."""
    fake_facts = {
        "facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {"USD": [
                    _quarter(2026, "Q1", "2025-09-28", "2025-12-27", val=143.8e9),
                    _quarter(2025, "Q1", "2024-09-29", "2024-12-28", val=124.3e9),
                    _annual(2025, "2025-09-27", val=394.7e9),
                ]},
            },
            "NetIncomeLoss": {
                "units": {"USD": [
                    _quarter(2026, "Q1", "2025-09-28", "2025-12-27", val=42e9),
                    _quarter(2025, "Q1", "2024-09-29", "2024-12-28", val=36e9),
                    _annual(2025, "2025-09-27", val=99e9),
                ]},
            },
        }},
    }
    with patch("aletheia.data.sec_quarterly._load_facts",
               return_value=fake_facts):
        r = sq.derive_ttm_from_sec("AAPL")
    assert r.record is not None
    assert r.record.period == "TTM"
    assert r.record.fmp_validation["ttm_source"] == "sec_derived_quarters"
    # Revenue: 394.7 + 143.8 - 124.3 = 414.2B
    assert abs(r.record.raw["Revenue"] - 414.2e9) < 1e6
    # NI: 99 + 42 - 36 = 105B
    assert abs(r.record.raw["NetIncome"] - 105e9) < 1e6
