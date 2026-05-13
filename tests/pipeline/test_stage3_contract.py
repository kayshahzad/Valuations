"""Contract tests for Stage 3 (calculation).

Per docs/pipeline_contracts.md "Contract testing", each stage's tests
verify:
  (a) the stage produces output matching the contract schema,
  (b) the stage rejects malformed input,
  (c) the stage's output is consumable by the next stage's input
      contract.

Stage 3 inputs are ``List[ValidatedCleanedRecord]``; output is
``CalculationBundle``. The next stage (4 — agents) consumes the
bundle via ``input_calculation_fingerprint`` lineage, so (c) reduces
to "bundle_fingerprint is present and deterministic."

Most behavioural tests use a synthetic two-year minimal record set so
they run sub-second and don't touch DuckDB / market data — the
``calculation_layer/`` test suite is where real-ticker parity is
exercised.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import pytest

from aletheia.contracts.pipeline import (
    CalculationBundle,
    ValidatedCleanedRecord,
    ValidationReceipt,
)
from aletheia.pipeline.stage3_calculate import (
    Stage3InputError,
    run_stage3,
    _records_to_dataframe,
    _compute_bundle_fingerprint,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

def _make_record(
    ticker: str = "NVDA",
    fiscal_year: int = 2024,
    period: str = "FY",
    record_fingerprint: str = "fp-test",
) -> ValidatedCleanedRecord:
    """Minimal record. Calc engines may fail on it, but it's enough to
    exercise the contract surface (input validation, fingerprinting,
    DataFrame shape)."""
    return ValidatedCleanedRecord(
        ticker=ticker,
        fiscal_year=fiscal_year,
        period=period,
        period_end_date=f"{fiscal_year}-12-31",
        raw={"Revenue": 100_000.0, "CapEx": -5_000.0},
        clean={"Revenue": 100_000.0, "NormalizedEBIT": 20_000.0},
        derived={"EBITDA": 25_000.0, "FCF": 18_000.0},
        overall_quality_score=0.95,
        cleaning_warnings=[],
        blocking_errors=[],
        validation=ValidationReceipt(),
        record_fingerprint=record_fingerprint,
        input_bundle_fingerprint="bundle-test",
        cleaned_at=datetime.now(timezone.utc),
        pipeline_version="test-version",
    )


# ─────────────────────────────────────────────────────────────────────
# (b) Input contract enforcement
# ─────────────────────────────────────────────────────────────────────

def test_stage3_rejects_empty_records():
    with pytest.raises(Stage3InputError, match="empty records list"):
        run_stage3([], pipeline_version="test")


def test_stage3_rejects_mixed_ticker_records():
    records = [_make_record("NVDA"), _make_record("AAPL")]
    with pytest.raises(Stage3InputError, match="multiple tickers"):
        run_stage3(records, pipeline_version="test")


def test_stage3_rejects_unknown_ticker():
    """An off-universe ticker without explicit classification must be
    rejected — calc tools dispatch on sector/lifecycle metadata."""
    records = [_make_record("ZZZNOTREAL")]
    with pytest.raises(Stage3InputError, match="No classification"):
        run_stage3(records, pipeline_version="test")


# ─────────────────────────────────────────────────────────────────────
# DataFrame adapter
# ─────────────────────────────────────────────────────────────────────

def test_records_to_dataframe_preserves_prefixes():
    """Stage 3's existing calc engines key on raw_/clean_/derived_
    column prefixes. The adapter must restore those from the record's
    namespaced dicts."""
    records = [_make_record("NVDA", 2023), _make_record("NVDA", 2024)]
    df = _records_to_dataframe(records)
    assert len(df) == 2
    assert {"ticker", "fiscal_year", "period", "period_end_date"}.issubset(df.columns)
    assert "raw_Revenue" in df.columns
    assert "clean_Revenue" in df.columns
    assert "clean_NormalizedEBIT" in df.columns
    assert "derived_EBITDA" in df.columns
    assert "derived_FCF" in df.columns
    assert df.loc[df["fiscal_year"] == 2024, "raw_Revenue"].iloc[0] == 100_000.0


def test_records_to_dataframe_handles_disjoint_field_sets():
    """Records can have different fields per year (e.g., a new derived
    metric added mid-history). The resulting DataFrame must accommodate
    by leaving the missing column as NaN, not raising."""
    r1 = _make_record("NVDA", 2023)
    r2 = _make_record("NVDA", 2024)
    r2.clean["NewField"] = 42.0
    df = _records_to_dataframe([r1, r2])
    assert "clean_NewField" in df.columns
    # The 2023 row didn't carry this field — pandas fills NaN.
    fy2023_val = df.loc[df["fiscal_year"] == 2023, "clean_NewField"].iloc[0]
    fy2024_val = df.loc[df["fiscal_year"] == 2024, "clean_NewField"].iloc[0]
    assert fy2024_val == 42.0
    import math
    assert math.isnan(fy2023_val)


# ─────────────────────────────────────────────────────────────────────
# Fingerprint determinism
# ─────────────────────────────────────────────────────────────────────

def test_bundle_fingerprint_is_deterministic():
    fp1 = _compute_bundle_fingerprint(
        ticker="NVDA",
        fiscal_year=2024,
        base_period="TTM",
        input_record_fingerprint="abc",
        pipeline_version="v1",
    )
    fp2 = _compute_bundle_fingerprint(
        ticker="NVDA",
        fiscal_year=2024,
        base_period="TTM",
        input_record_fingerprint="abc",
        pipeline_version="v1",
    )
    assert fp1 == fp2
    # SHA-256 hex digest length.
    assert len(fp1) == 64


def test_bundle_fingerprint_changes_on_input_fingerprint():
    fp1 = _compute_bundle_fingerprint(
        ticker="NVDA", fiscal_year=2024, base_period="TTM",
        input_record_fingerprint="abc", pipeline_version="v1",
    )
    fp2 = _compute_bundle_fingerprint(
        ticker="NVDA", fiscal_year=2024, base_period="TTM",
        input_record_fingerprint="xyz", pipeline_version="v1",
    )
    assert fp1 != fp2


def test_bundle_fingerprint_changes_on_pipeline_version():
    fp1 = _compute_bundle_fingerprint(
        ticker="NVDA", fiscal_year=2024, base_period="TTM",
        input_record_fingerprint="abc", pipeline_version="v1",
    )
    fp2 = _compute_bundle_fingerprint(
        ticker="NVDA", fiscal_year=2024, base_period="TTM",
        input_record_fingerprint="abc", pipeline_version="v2",
    )
    assert fp1 != fp2


# ─────────────────────────────────────────────────────────────────────
# (a) Output schema — full end-to-end with real DB data
# ─────────────────────────────────────────────────────────────────────

def _load_real_records(ticker: str, pipeline_version: str = "test"):
    """Lazy DB import so tests that don't need DB stay fast."""
    from aletheia.cli.calc import load_records
    try:
        return load_records(ticker, pipeline_version)
    except Exception as exc:
        pytest.skip(f"DB not populated for {ticker}: {exc}")


@pytest.mark.parametrize("ticker", ["NVDA", "AAPL", "MSFT"])
def test_stage3_produces_valid_bundle_for_real_ticker(ticker):
    """End-to-end: real DuckDB records → run_stage3 → typed bundle.

    Skips if DB isn't populated for the ticker (e.g., on a fresh
    checkout). When DB is populated, exercises (a) output schema
    validity and (c) downstream consumability.
    """
    records = _load_real_records(ticker, pipeline_version="test-v1")
    bundle = run_stage3(records, pipeline_version="test-v1")

    assert isinstance(bundle, CalculationBundle)
    assert bundle.ticker == ticker
    assert bundle.pipeline_version == "test-v1"
    assert bundle.base_period in ("FY", "TTM")
    assert len(bundle.bundle_fingerprint) == 64
    assert bundle.input_record_fingerprint  # lineage pointer is set

    # The bundle must serialise to JSON cleanly (Stage 4's input is
    # the bundle via the input_calculation_fingerprint pointer, but
    # the orchestrator persists the bundle so JSON-serialisability is
    # the proof of "consumable downstream").
    j = bundle.model_dump_json()
    assert "bundle_fingerprint" in j


def test_stage3_fingerprint_stable_across_runs():
    """Two back-to-back runs against the same DB state must produce
    identical bundle_fingerprints — the cache-hit signal for Week 6's
    orchestrator."""
    records = _load_real_records("NVDA", pipeline_version="stable-v1")
    b1 = run_stage3(records, pipeline_version="stable-v1")
    b2 = run_stage3(records, pipeline_version="stable-v1")
    assert b1.bundle_fingerprint == b2.bundle_fingerprint
    assert b1.input_record_fingerprint == b2.input_record_fingerprint


def test_stage3_fingerprint_changes_with_pipeline_version():
    """Pipeline-version bump must bust the cache."""
    records_v1 = _load_real_records("NVDA", pipeline_version="ver-A")
    records_v2 = _load_real_records("NVDA", pipeline_version="ver-B")
    b1 = run_stage3(records_v1, pipeline_version="ver-A")
    b2 = run_stage3(records_v2, pipeline_version="ver-B")
    assert b1.bundle_fingerprint != b2.bundle_fingerprint
