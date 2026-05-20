"""DCF engine + reverse-DCF latency baseline.

Measures the cost of `_compute_dcf_live` end-to-end across a representative
mix of tickers — including ones that exercise different code paths
(growth-compounder, mature, schema-mismatch). Reports p50/p95/p99 to stdout.

Latency budget: p95 < 500ms. Above that, the dashboard feels laggy on
ticker switches and the Step 1c LRU cache should land before merge.

Not a hard pass/fail test — pytest will print the histogram and pass. Wire
up an explicit assertion only if a future run regresses sharply.
"""

from __future__ import annotations

import statistics
import time

import pytest


# Representative mix:
#   AAPL     — growth_compounder_software, original 25-ticker set
#   NVDA     — secular_hyper_growth, post-fix scenario test
#   ACN      — mature, recently-added (no agent run)
#   KO       — mature, recently-added (no agent run)
#   AXP      — routing_required, expect engine raise
#   JPM      — fcff_compatible but bank-style schema; engine usually raises
TICKERS = ["AAPL", "NVDA", "ACN", "KO", "AXP", "JPM"]


@pytest.mark.skipif(
    not __import__("pathlib").Path("valuation_data/database/investment.duckdb").exists(),
    reason="DuckDB not present in this environment",
)
def test_dcf_engine_latency_baseline():
    """
    Separates cold-cache from warm-cache latency because they reflect
    different user-visible behaviors:

    - **Cold call per ticker** (first viewing in a fresh API process):
      dominated by external market_data fetch (yfinance: risk-free rate +
      beta). Internally cached for the rest of the process lifetime.
    - **Warm call** (every subsequent request to the same or any other
      ticker that shares the cached market data): pure DCFEngine compute,
      typically <50ms.

    The dashboard's actual experience is overwhelmingly warm — cold
    happens once per ticker per process restart, then never again. The
    Step 1c LRU cache decision should hinge on warm p95, not on
    cold-influenced mixed p95.
    """
    from api_main import _compute_dcf_live
    from fastapi import HTTPException

    cold_samples_ms: list[float] = []
    warm_samples_ms: list[float] = []
    per_ticker: dict[str, list[float]] = {}
    failures: list[str] = []

    for ticker in TICKERS:
        for i in range(3):
            t0 = time.perf_counter()
            try:
                _compute_dcf_live(ticker)
            except HTTPException as e:
                if e.status_code not in (404, 422):
                    failures.append(f"{ticker}: {e.status_code} {e.detail}")
            elapsed = (time.perf_counter() - t0) * 1000.0
            (cold_samples_ms if i == 0 else warm_samples_ms).append(elapsed)
            per_ticker.setdefault(ticker, []).append(elapsed)

    def stats(samples):
        if not samples:
            return None, None, None
        s = sorted(samples)
        p50 = statistics.median(s)
        p95 = s[int(len(s) * 0.95)] if len(s) >= 20 else s[-1]
        p99 = s[int(len(s) * 0.99)] if len(s) >= 100 else s[-1]
        return p50, p95, p99

    cold_p50, cold_p95, cold_p99 = stats(cold_samples_ms)
    warm_p50, warm_p95, warm_p99 = stats(warm_samples_ms)

    print()
    print(f"DCF latency — {len(TICKERS)} tickers × 3 calls each:")
    print(f"  cold (1st call/ticker, n={len(cold_samples_ms)}): "
          f"p50={cold_p50:.0f}ms p95={cold_p95:.0f}ms p99={cold_p99:.0f}ms")
    print(f"  warm (2nd+ calls,    n={len(warm_samples_ms)}): "
          f"p50={warm_p50:.0f}ms p95={warm_p95:.0f}ms p99={warm_p99:.0f}ms")
    print()
    print("Per-ticker (cold | warm | warm):")
    for t, samples in per_ticker.items():
        print(f"  {t:8s} {' | '.join(f'{s:6.0f}ms' for s in samples)}")

    if failures:
        print()
        print(f"Unexpected failures: {failures}")

    # Hard budget on warm p95 — that's what users feel during dashboard
    # navigation. Cold spike is one-time per process, acceptable up to ~2s.
    if warm_p95 > 500:
        pytest.fail(
            f"Warm-cache latency budget exceeded: warm p95={warm_p95:.0f}ms "
            f"> 500ms target. Add the LRU cache (Step 1c) or profile "
            f"DCFEngine.run."
        )
    if cold_p95 > 2500:
        pytest.fail(
            f"Cold-cache latency unreasonably high: cold p95={cold_p95:.0f}ms "
            f"> 2500ms. Likely yfinance/market_data is being called from "
            f"the request path without any caching — investigate."
        )
