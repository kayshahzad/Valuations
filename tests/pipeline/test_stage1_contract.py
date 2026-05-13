"""Contract tests for Stage 1 (ingestion).

Per docs/pipeline_contracts.md "Contract testing", each stage's tests
verify:
  (a) the stage produces output matching the contract schema,
  (b) the stage rejects malformed input,
  (c) the stage's output is consumable by the next stage's input
      contract.

For Stage 1, (c) reduces to "bundle_fingerprint is deterministic and
each RawSource carries a sha256 of an on-disk payload" — Stage 2's
input is the bundle via ``input_bundle_fingerprint`` lineage.

Most tests use lightweight fakes that write synthetic payloads to a
temp directory and then call ``run_stage1`` against the real
production code path. The fakes monkey-patch the fmp_client /
edgar_client fetchers; the file-hash / fingerprint logic itself
exercises real code.

The A14 (V shares FMP fallback) resolver has dedicated tests that
exercise its FMP-cache parsing logic without hitting network.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pytest

from aletheia.contracts.pipeline import IngestedRawBundle, RawSource
from aletheia.pipeline import stage1_ingest
from aletheia.pipeline.stage1_ingest import Stage1IngestError, run_stage1


# ─────────────────────────────────────────────────────────────────────
# Test fixture — stub out network with synthetic payloads on disk
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_ingest_env(tmp_path, monkeypatch):
    """Redirects Stage 1's persistence roots into ``tmp_path`` and
    monkey-patches edgar_client / fmp_client so no network calls
    happen. Returns a dict the test can mutate to control fetcher
    behaviour (e.g., simulate an FMP failure).

    Side-effects to revert (handled by monkeypatch):
      - stage1_ingest.SEC_COMPANYFACTS_DIR / MARKET_SNAPSHOT_DIR
      - fmp_client._CACHE_DIR
      - edgar_client.SecEdgar
      - market_data.MarketDataCache.get_info / get_beta / get_risk_free_rate
      - The fmp_client fetcher functions referenced in _FMP_SOURCES
    """
    sec_dir = tmp_path / "raw" / "sec" / "companyfacts"
    fmp_dir = tmp_path / "macro" / "fmp"
    market_dir = tmp_path / "raw" / "market"
    sec_dir.mkdir(parents=True, exist_ok=True)
    fmp_dir.mkdir(parents=True, exist_ok=True)
    market_dir.mkdir(parents=True, exist_ok=True)

    from aletheia.data import fmp_client, market_data

    monkeypatch.setattr(stage1_ingest, "SEC_COMPANYFACTS_DIR", sec_dir)
    monkeypatch.setattr(stage1_ingest, "MARKET_SNAPSHOT_DIR", market_dir)
    monkeypatch.setattr(fmp_client, "_CACHE_DIR", fmp_dir)

    fake_sec_payload = {"entityName": "TestCo", "facts": {"us-gaap": {}}}

    class FakeSec:
        def __init__(self, *_, **__):
            pass

        def resolve_cik(self, ticker: str) -> Optional[str]:
            return env["sec_cik"]

        def fetch_company_facts(self, cik: str) -> Optional[Dict[str, Any]]:
            return env["sec_payload"]

    from aletheia.data import edgar_client
    monkeypatch.setattr(edgar_client, "SecEdgar", FakeSec)

    # Fake fmp_client endpoints. Each returns the payload from env so
    # individual tests can simulate per-endpoint failures.
    def _fake_fmp(label: str):
        def _impl(ticker: str, *, force_refresh: bool = False, **kw):
            return env["fmp"].get(label)
        return _impl

    # Patch the nine referenced fmp_client functions.
    monkeypatch.setattr(fmp_client, "fetch_income_statements", _fake_fmp("fmp_income"))
    monkeypatch.setattr(fmp_client, "fetch_balance_sheets", _fake_fmp("fmp_balance_sheet"))
    monkeypatch.setattr(fmp_client, "fetch_cash_flows", _fake_fmp("fmp_cashflow"))
    monkeypatch.setattr(fmp_client, "fetch_key_metrics", _fake_fmp("fmp_key_metrics"))
    monkeypatch.setattr(fmp_client, "fetch_enterprise_values", _fake_fmp("fmp_enterprise_values"))
    monkeypatch.setattr(fmp_client, "fetch_key_metrics_ttm", _fake_fmp("fmp_key_metrics_ttm"))
    monkeypatch.setattr(fmp_client, "fetch_ratios_ttm", _fake_fmp("fmp_ratios_ttm"))
    monkeypatch.setattr(fmp_client, "fetch_income_statement_as_reported_quarter",
                        _fake_fmp("fmp_income_as_reported_quarter"))
    monkeypatch.setattr(fmp_client, "fetch_profile", _fake_fmp("fmp_profile"))

    # The _FMP_SOURCES list captured the OLD function references at
    # import time. Refresh that list to point at the now-patched
    # fmp_client functions so run_stage1 picks them up.
    monkeypatch.setattr(stage1_ingest, "_FMP_SOURCES", [
        ("fmp_income",                       fmp_client.fetch_income_statements,
         "income-statement",                 "annual"),
        ("fmp_balance_sheet",                fmp_client.fetch_balance_sheets,
         "balance-sheet-statement",          "annual"),
        ("fmp_cashflow",                     fmp_client.fetch_cash_flows,
         "cash-flow-statement",              "annual"),
        ("fmp_key_metrics",                  fmp_client.fetch_key_metrics,
         "key-metrics",                      "annual"),
        ("fmp_enterprise_values",            fmp_client.fetch_enterprise_values,
         "enterprise-values",                "annual"),
        ("fmp_key_metrics_ttm",              fmp_client.fetch_key_metrics_ttm,
         "key-metrics-ttm",                  None),
        ("fmp_ratios_ttm",                   fmp_client.fetch_ratios_ttm,
         "ratios-ttm",                       None),
        ("fmp_income_as_reported_quarter",
         fmp_client.fetch_income_statement_as_reported_quarter,
         "income-statement-as-reported",     "quarter"),
        ("fmp_profile",                      fmp_client.fetch_profile,
         "profile",                          None),
    ])

    # Fake market_data.
    class FakeMarketCache:
        @classmethod
        def get_info(cls, ticker):
            return {"last_price": 100.0, "market_cap": 1e12, "shares": 1e10}

    monkeypatch.setattr(market_data, "MarketDataCache", FakeMarketCache)
    monkeypatch.setattr(market_data, "get_beta", lambda *_, **__: 1.1)
    monkeypatch.setattr(market_data, "get_risk_free_rate", lambda: 0.045)

    env = {
        "sec_cik": "0000000320",
        "sec_payload": fake_sec_payload,
        "fmp": {
            # FMP endpoints — default to a minimal payload per source.
            "fmp_income": [{"calendarYear": 2024, "revenue": 100.0}],
            "fmp_balance_sheet": [{"calendarYear": 2024, "totalAssets": 200.0}],
            "fmp_cashflow": [{"calendarYear": 2024, "operatingCashFlow": 30.0}],
            "fmp_key_metrics": [{"calendarYear": 2024, "marketCap": 1e12}],
            "fmp_enterprise_values": [{"date": "2024-12-31", "enterpriseValue": 1.1e12}],
            "fmp_key_metrics_ttm": {"marketCapTTM": 1e12},
            "fmp_ratios_ttm": {"peRatioTTM": 30.0},
            "fmp_income_as_reported_quarter": [{"period": "Q3", "revenue": 25.0}],
            "fmp_profile": {"symbol": "AAPL", "sector": "Technology"},
        },
    }

    return env


# ─────────────────────────────────────────────────────────────────────
# (a) Output schema
# ─────────────────────────────────────────────────────────────────────

def test_stage1_produces_valid_bundle(fake_ingest_env):
    bundle = run_stage1(
        "AAPL",
        pipeline_version="test-v1",
        include_market_snapshot=False,
    )
    assert isinstance(bundle, IngestedRawBundle)
    assert bundle.ticker == "AAPL"
    assert bundle.pipeline_version == "test-v1"
    assert len(bundle.bundle_fingerprint) == 64  # SHA-256 hex
    assert "sec_companyfacts" in bundle.sources
    # Every fake FMP source is present.
    fmp_keys = [k for k in bundle.sources if k.startswith("fmp_")]
    assert len(fmp_keys) >= 8  # nine FMP sources in the default set

    # Each RawSource is well-formed.
    for src in bundle.sources.values():
        assert isinstance(src, RawSource)
        assert len(src.payload_sha256) == 64
        assert src.payload_path.exists(), (
            f"source {src.source!r} references non-existent payload "
            f"path {src.payload_path}"
        )


def test_stage1_includes_classification_snapshot(fake_ingest_env):
    bundle = run_stage1("AAPL", pipeline_version="t", include_market_snapshot=False)
    snap = bundle.classification_snapshot
    assert snap["ticker"] == "AAPL"
    assert snap["sector"]
    assert "lifecycle" in snap
    assert "business_model" in snap


def test_stage1_skips_market_snapshot_when_requested(fake_ingest_env):
    bundle = run_stage1("AAPL", pipeline_version="t", include_market_snapshot=False)
    assert "market_snapshot" not in bundle.sources


def test_stage1_includes_market_snapshot_when_enabled(fake_ingest_env):
    bundle = run_stage1("AAPL", pipeline_version="t", include_market_snapshot=True)
    assert "market_snapshot" in bundle.sources
    snap_src = bundle.sources["market_snapshot"]
    payload = json.loads(snap_src.payload_path.read_text())
    assert payload["last_price"] == 100.0
    assert payload["beta"] == 1.1
    assert payload["risk_free_rate"] == 0.045


def test_stage1_honours_source_whitelist(fake_ingest_env):
    bundle = run_stage1(
        "AAPL",
        pipeline_version="t",
        sources=["sec_companyfacts", "fmp_income"],
        include_market_snapshot=False,
    )
    assert set(bundle.sources.keys()) == {"sec_companyfacts", "fmp_income"}


# ─────────────────────────────────────────────────────────────────────
# (b) Input contract enforcement
# ─────────────────────────────────────────────────────────────────────

def test_stage1_rejects_off_universe_ticker(fake_ingest_env):
    with pytest.raises(Stage1IngestError, match="not in UNIVERSE"):
        run_stage1("ZZZNOTREAL", pipeline_version="t")


def test_stage1_raises_when_sec_unavailable(fake_ingest_env, monkeypatch):
    """SEC is the required primary source; absence is a hard fail."""
    from aletheia.data import edgar_client

    class FailingSec:
        def __init__(self, *_, **__): pass
        def resolve_cik(self, ticker): return None
        def fetch_company_facts(self, cik): return None

    monkeypatch.setattr(edgar_client, "SecEdgar", FailingSec)
    with pytest.raises(Stage1IngestError, match="SEC companyfacts"):
        run_stage1("AAPL", pipeline_version="t", include_market_snapshot=False)


def test_stage1_tolerates_missing_fmp_endpoint(fake_ingest_env):
    """A missing FMP endpoint doesn't fail the bundle — the gap shows
    up as an absent source, which Stage 2 surfaces as a validation
    receipt entry."""
    fake_ingest_env["fmp"]["fmp_profile"] = None
    bundle = run_stage1("AAPL", pipeline_version="t", include_market_snapshot=False)
    assert "fmp_profile" not in bundle.sources
    # Required sources still present.
    assert "sec_companyfacts" in bundle.sources


# ─────────────────────────────────────────────────────────────────────
# Fingerprint determinism (c)
# ─────────────────────────────────────────────────────────────────────

def test_bundle_fingerprint_stable_across_runs(fake_ingest_env):
    b1 = run_stage1("AAPL", pipeline_version="vX", include_market_snapshot=False)
    b2 = run_stage1("AAPL", pipeline_version="vX", include_market_snapshot=False)
    assert b1.bundle_fingerprint == b2.bundle_fingerprint
    # Each source's sha256 also stable.
    for src_id in b1.sources:
        assert b1.sources[src_id].payload_sha256 == b2.sources[src_id].payload_sha256


def test_bundle_fingerprint_changes_with_pipeline_version(fake_ingest_env):
    b1 = run_stage1("AAPL", pipeline_version="vA", include_market_snapshot=False)
    b2 = run_stage1("AAPL", pipeline_version="vB", include_market_snapshot=False)
    assert b1.bundle_fingerprint != b2.bundle_fingerprint


def test_bundle_fingerprint_changes_when_a_source_payload_changes(fake_ingest_env):
    b1 = run_stage1(
        "AAPL", pipeline_version="v",
        include_market_snapshot=False, force_refresh=True,
    )
    # Mutate the SEC payload — same source id but different content.
    fake_ingest_env["sec_payload"] = {"entityName": "OtherCo", "facts": {"us-gaap": {}}}
    b2 = run_stage1(
        "AAPL", pipeline_version="v",
        include_market_snapshot=False, force_refresh=True,
    )
    assert b1.bundle_fingerprint != b2.bundle_fingerprint
    assert (
        b1.sources["sec_companyfacts"].payload_sha256
        != b2.sources["sec_companyfacts"].payload_sha256
    )


# ─────────────────────────────────────────────────────────────────────
# A14 — shares_diluted FMP fallback resolver
# ─────────────────────────────────────────────────────────────────────

def test_a14_resolver_prefers_diluted_share_count(monkeypatch):
    """When FMP exposes weightedAverageShsOutDil, use it directly."""
    from aletheia.data import fmp_client
    from aletheia.data.shares_diluted_resolver import resolve_shares_diluted_from_fmp

    def fake_income(ticker, period="annual", force_refresh=False):
        return [
            {"calendarYear": 2023, "weightedAverageShsOutDil": 2_050_000_000.0},
            {"calendarYear": 2024, "weightedAverageShsOutDil": 2_100_000_000.0},
        ]
    monkeypatch.setattr(fmp_client, "fetch_income_statements", fake_income)

    val, source = resolve_shares_diluted_from_fmp(ticker="V", fiscal_year=2024)
    assert val == pytest.approx(2_100_000_000.0)
    assert source == "fmp_income_statement_diluted"


def test_a14_resolver_falls_to_basic_when_diluted_missing(monkeypatch):
    from aletheia.data import fmp_client
    from aletheia.data.shares_diluted_resolver import resolve_shares_diluted_from_fmp

    def fake_income(ticker, period="annual", force_refresh=False):
        return [
            {"calendarYear": 2024, "weightedAverageShsOut": 2_080_000_000.0},
        ]
    monkeypatch.setattr(fmp_client, "fetch_income_statements", fake_income)

    val, source = resolve_shares_diluted_from_fmp(ticker="V", fiscal_year=2024)
    assert val == pytest.approx(2_080_000_000.0)
    assert source == "fmp_income_statement_basic_fallback"


def test_a14_resolver_returns_unavailable_when_no_match(monkeypatch):
    from aletheia.data import fmp_client
    from aletheia.data.shares_diluted_resolver import resolve_shares_diluted_from_fmp

    def fake_income(ticker, period="annual", force_refresh=False):
        return [
            {"calendarYear": 2020, "weightedAverageShsOutDil": 2_000_000_000.0},
        ]
    monkeypatch.setattr(fmp_client, "fetch_income_statements", fake_income)

    val, source = resolve_shares_diluted_from_fmp(ticker="V", fiscal_year=2024)
    assert val is None
    assert source == "unavailable"


def test_a14_resolver_returns_unavailable_when_fmp_empty(monkeypatch):
    from aletheia.data import fmp_client
    from aletheia.data.shares_diluted_resolver import resolve_shares_diluted_from_fmp

    def fake_income(ticker, period="annual", force_refresh=False):
        return None
    monkeypatch.setattr(fmp_client, "fetch_income_statements", fake_income)

    val, source = resolve_shares_diluted_from_fmp(ticker="V", fiscal_year=2024)
    assert val is None
    assert source == "unavailable"


def test_a14_resolver_handles_fmp_raise(monkeypatch):
    """Network errors must not propagate; resolver is defensive."""
    from aletheia.data import fmp_client
    from aletheia.data.shares_diluted_resolver import resolve_shares_diluted_from_fmp

    def fake_income(ticker, period="annual", force_refresh=False):
        raise RuntimeError("transient network failure")
    monkeypatch.setattr(fmp_client, "fetch_income_statements", fake_income)

    val, source = resolve_shares_diluted_from_fmp(ticker="V", fiscal_year=2024)
    assert val is None
    assert source == "unavailable"


def test_a14_resolver_matches_on_fiscalYear_when_calendarYear_differs(monkeypatch):
    """Some filers (V's older filings) report calendarYear differently
    from fiscalYear. The resolver must try both."""
    from aletheia.data import fmp_client
    from aletheia.data.shares_diluted_resolver import resolve_shares_diluted_from_fmp

    def fake_income(ticker, period="annual", force_refresh=False):
        return [
            {"calendarYear": "2024", "fiscalYear": "2024",
             "weightedAverageShsOutDil": 2_100_000_000.0},
        ]
    monkeypatch.setattr(fmp_client, "fetch_income_statements", fake_income)

    val, source = resolve_shares_diluted_from_fmp(ticker="V", fiscal_year=2024)
    assert val == pytest.approx(2_100_000_000.0)
    assert source == "fmp_income_statement_diluted"
