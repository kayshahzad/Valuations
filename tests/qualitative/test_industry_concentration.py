"""Phase D tests — industry_concentration deterministic computer.

Cross-ticker computer that scores top-N cohort share within the
ticker's sector. The MVP path uses the in-universe peer cohort, NOT
real industry HHI — these tests pin the cohort math + small-cohort
fallback (top-2 for cohorts of 2-3; top-1 for cohort of 1) + the 1-7
bucket mapping.

Peer revenues are injected via the ``peer_revenues`` kwarg so tests
don't need a populated DuckDB.
"""
from __future__ import annotations

import pandas as pd
import pytest

from aletheia.qualitative.computers.industry_concentration import (
    compute_industry_concentration,
)


def _make_df(ticker: str) -> pd.DataFrame:
    """Minimal cleaned-records DataFrame — only the ticker column
    is read by the computer (cross-ticker work happens via the
    injected ``peer_revenues``)."""
    return pd.DataFrame({"ticker": [ticker], "fiscal_year": [2025]})


# ── Bucket boundaries ──────────────────────────────────────────────


def test_score_7_for_very_concentrated_cohort():
    """Top-3 share > 80% → score 7."""
    # 4-peer cohort, top 3 = 85% / bottom = 15%. Use real universe
    # tickers (AAPL is in "Technology") so the sector lookup succeeds;
    # the injected dict drives the actual cohort math.
    peer_revenues = {
        "AAPL": 50.0, "MSFT": 25.0, "GOOGL": 10.0, "META": 15.0,
    }
    # Sorted desc: AAPL=50, MSFT=25, META=15, GOOGL=10
    # Top-3 share = (50 + 25 + 15) / 100 = 0.90 → score 7
    result = compute_industry_concentration(
        _make_df("AAPL"), peer_revenues=peer_revenues,
    )
    assert result.score == 7
    assert result.source_payload["top_n_share"] == pytest.approx(0.90)


def test_score_4_for_mid_cohort():
    """Top-3 share 30-45% → score 4."""
    # 6-peer cohort, top 3 = ~40%
    peer_revenues = {
        "A": 20.0, "B": 15.0, "C": 10.0,
        "D": 25.0, "E": 20.0, "F": 10.0,
    }
    result = compute_industry_concentration(
        _make_df("AAPL"), peer_revenues=peer_revenues,
    )
    # Total = 100, top-3 = D(25) + A(20) + E(20) = 65 → score 6
    assert result.source_payload["top_n_share"] == pytest.approx(0.65)
    assert result.score == 6


def test_score_1_for_fragmented_cohort():
    """Top-3 share < 10% → score 1. Built from many ~equal peers.
    Include AAPL so the universe lookup succeeds; other names are
    synthetic fillers driving the cohort math."""
    peer_revenues = {"AAPL": 1.0}
    peer_revenues.update({f"FILLER{i}": 1.0 for i in range(49)})
    result = compute_industry_concentration(
        _make_df("AAPL"), peer_revenues=peer_revenues,
    )
    # 50 equal peers → top-3 = 6% → score 1
    assert result.source_payload["top_n_share"] == pytest.approx(0.06)
    assert result.score == 1


# ── Small-cohort handling ──────────────────────────────────────────


def test_cohort_of_one_uses_top_1():
    """Single-ticker sector (TSLA in Auto Manufacturers) → top-1 =
    100% but the narrative flags the proxy as degenerate."""
    result = compute_industry_concentration(
        _make_df("TSLA"), peer_revenues={"TSLA": 100.0},
    )
    assert result.source_payload["cohort_size"] == 1
    assert result.source_payload["top_n_used"] == 1
    assert result.source_payload["top_n_share"] == 1.0
    assert result.score == 7   # degenerate-but-correct given inputs


def test_cohort_of_two_uses_top_2():
    """Cohort of 2-3 uses top-2 (avoid degenerate 100% from top-3)."""
    peer_revenues = {"A": 70.0, "B": 30.0}
    result = compute_industry_concentration(
        _make_df("AAPL"), peer_revenues=peer_revenues,
    )
    assert result.source_payload["cohort_size"] == 2
    assert result.source_payload["top_n_used"] == 2
    assert result.source_payload["top_n_share"] == 1.0


def test_cohort_of_three_uses_top_2():
    """3-peer cohort still uses top-2 (4 is the boundary)."""
    peer_revenues = {"A": 50.0, "B": 30.0, "C": 20.0}
    result = compute_industry_concentration(
        _make_df("AAPL"), peer_revenues=peer_revenues,
    )
    assert result.source_payload["cohort_size"] == 3
    assert result.source_payload["top_n_used"] == 2
    # Top-2 = A(50) + B(30) = 80% → score 7
    assert result.source_payload["top_n_share"] == pytest.approx(0.80)
    assert result.score == 7


def test_cohort_of_four_uses_top_3():
    """4-peer cohort switches to top-3."""
    peer_revenues = {"A": 25.0, "B": 25.0, "C": 25.0, "D": 25.0}
    result = compute_industry_concentration(
        _make_df("AAPL"), peer_revenues=peer_revenues,
    )
    assert result.source_payload["cohort_size"] == 4
    assert result.source_payload["top_n_used"] == 3
    assert result.source_payload["top_n_share"] == pytest.approx(0.75)
    assert result.score == 6


# ── Ticker rank + share in payload ─────────────────────────────────


def test_ticker_rank_and_share_populated():
    """Cohort leader gets rank=1; follower gets rank=N+. Used by
    the dashboard to interpret 'high concentration good/bad for THIS
    ticker' depending on rank."""
    peer_revenues = {"MSFT": 60.0, "AAPL": 25.0, "GOOGL": 15.0}
    result = compute_industry_concentration(
        _make_df("AAPL"), peer_revenues=peer_revenues,
    )
    assert result.source_payload["ticker_rank"] == 2
    assert result.source_payload["ticker_share"] == pytest.approx(0.25)


def test_ticker_rank_for_cohort_leader():
    peer_revenues = {"AAPL": 90.0, "ORCL": 10.0}
    result = compute_industry_concentration(
        _make_df("AAPL"), peer_revenues=peer_revenues,
    )
    assert result.source_payload["ticker_rank"] == 1
    assert result.source_payload["ticker_share"] == pytest.approx(0.90)


# ── Degenerate inputs ──────────────────────────────────────────────


def test_empty_df_returns_none():
    assert compute_industry_concentration(pd.DataFrame()) is None


def test_ticker_not_in_universe_returns_no_data():
    """An unrecognized ticker has no sector mapping — returns a
    no_data result rather than raising."""
    result = compute_industry_concentration(
        _make_df("ZZZZZ_NOT_REAL"),
        peer_revenues={"ZZZZZ_NOT_REAL": 100.0},
    )
    assert result is not None
    assert result.score is None
    assert "not_in_universe" in result.source_payload["reason"]


def test_empty_peer_cohort_returns_no_data():
    """When peer_revenues is provided but empty (real sector with
    no DB data for any peer), returns no_data."""
    result = compute_industry_concentration(
        _make_df("AAPL"), peer_revenues={},
    )
    assert result is not None
    assert result.score is None
    assert "empty_peer_cohort" in result.source_payload["reason"]


def test_zero_total_revenue_returns_no_data():
    result = compute_industry_concentration(
        _make_df("AAPL"), peer_revenues={"AAPL": 0.0, "MSFT": 0.0},
    )
    assert result is not None
    assert result.score is None
    assert "zero_total_revenue" in result.source_payload["reason"]


# ── Fingerprint stability ──────────────────────────────────────────


def test_fingerprint_stable_for_same_cohort():
    """Same peer dict → same fingerprint → idempotency guard
    skips DB writes on re-run."""
    peer_revenues = {"A": 50.0, "B": 30.0, "C": 20.0}
    r1 = compute_industry_concentration(_make_df("AAPL"), peer_revenues=peer_revenues)
    r2 = compute_industry_concentration(_make_df("AAPL"), peer_revenues=peer_revenues)
    assert r1.input_fingerprint == r2.input_fingerprint


def test_fingerprint_changes_with_revenue_change():
    """A peer's revenue changing → new fingerprint → new row written."""
    r1 = compute_industry_concentration(
        _make_df("AAPL"), peer_revenues={"A": 50.0, "B": 50.0},
    )
    r2 = compute_industry_concentration(
        _make_df("AAPL"), peer_revenues={"A": 60.0, "B": 40.0},
    )
    assert r1.input_fingerprint != r2.input_fingerprint


# ── Registry + catalog alignment ──────────────────────────────────


def test_registered_in_computers_registry():
    from aletheia.qualitative.computers import COMPUTERS
    assert "industry_concentration" in COMPUTERS


def test_catalog_is_deterministic_post_phase_d():
    """Phase D flips industry_concentration from PENDING_DATA to
    DETERMINISTIC. Catches accidental revert."""
    from config.qualitative_dimensions import DIMENSIONS
    from aletheia.qualitative.types import SourceCategory
    assert DIMENSIONS["industry_concentration"].source_category == SourceCategory.DETERMINISTIC
    # Formula citation is now required for DETERMINISTIC dims
    assert "industry_concentration_v1" in DIMENSIONS["industry_concentration"].formula_citation


def test_zero_pending_data_dims_remain():
    """After Phase D, no catalog dim should be PENDING_DATA. This is
    the architectural completion of the qualitative-tab wiring."""
    from config.qualitative_dimensions import DIMENSIONS
    from aletheia.qualitative.types import SourceCategory
    pending = [
        d for d, e in DIMENSIONS.items()
        if e.source_category == SourceCategory.PENDING_DATA
    ]
    assert pending == [], (
        f"Post-Phase-D, no dim should be PENDING_DATA. Still pending: {pending}"
    )
