"""Scenario ordering at the API boundary: bull > base > bear for every
fcff-compatible ticker with cleaned data.

Catches regressions of the bull/base/bear inversion bug at the API layer
(not just at DCFEngine unit-test layer). If a future change to the engine
re-introduces an asymmetric cap, this test surfaces it the moment any
ticker's response goes out of order.

The test was specifically motivated by reports stuck on pre-fix snapshots:
the old `/dcf` endpoint read those snapshots verbatim, so engine fixes
were invisible until each ticker's agents were re-run. Under DB-as-truth
the API recomputes live, and this test validates that the fix is now
universally visible.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from api_main import app
    return TestClient(app)


def _universe_tickers():
    """All tickers the API can value via DCFEngine, drawn from the live
    universe endpoint."""
    from api_main import _ticker_universe_union
    return _ticker_universe_union()


@pytest.mark.skipif(
    not __import__("pathlib").Path("valuation_data/database/investment.duckdb").exists(),
    reason="DuckDB not present",
)
def test_bull_gt_base_gt_bear_for_all_valuable_tickers(client):
    """For every ticker that returns 200, intrinsic_per_share must satisfy
    bull > base > bear. This is the Phase 6 invariant that the recent cap
    refactor (terminal_g + Y1-5 + Y6-10 + margin + WACC uniform-cap pattern)
    was meant to preserve."""

    failures: list[tuple[str, dict]] = []
    valued = 0

    for ticker in _universe_tickers():
        r = client.get(f"/ticker/{ticker}/dcf")
        if r.status_code != 200:
            # 404 / 422 are valid outcomes for never-ingested or
            # schema-mismatch filers. We only check ordering on tickers
            # the engine can actually value.
            continue
        data = r.json()
        bull = (data.get("bull") or {}).get("intrinsic_per_share")
        base = (data.get("base") or {}).get("intrinsic_per_share")
        bear = (data.get("bear") or {}).get("intrinsic_per_share")

        if None in (bull, base, bear):
            continue

        valued += 1
        if not (bull > base > bear):
            failures.append((ticker, {"bull": bull, "base": base, "bear": bear}))

    # Sanity: we must have actually valued a meaningful number of tickers,
    # otherwise the test is vacuously passing.
    assert valued >= 10, (
        f"Only {valued} tickers returned valid scenarios — test is "
        f"too lenient. DB or DCFEngine likely broken."
    )

    assert not failures, (
        f"{len(failures)} ticker(s) failed bull > base > bear ordering at "
        f"the API boundary:\n" +
        "\n".join(f"  {t}: {ips}" for t, ips in failures)
    )
