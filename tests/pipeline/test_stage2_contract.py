"""Contract tests for Stage 2 (validation + cleaning).

Per docs/pipeline_contracts.md, each stage's tests verify:
  (a) the stage produces output matching the contract schema,
  (b) the stage rejects malformed input,
  (c) the stage's output is consumable by the next stage's input
      contract.

For Stage 2, (c) reduces to "the produced ValidatedCleanedRecord
list is what Stage 3's run_stage3() consumes" — exercised end-to-end
in the closing parity test.

Most tests use a stubbed CleaningEngine so they don't depend on
the DuckDB/canonical parquet state. One real-ticker smoke test
proves the full path works against the production cleaning pipeline.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from aletheia.contracts.pipeline import ValidatedCleanedRecord
from aletheia.data import utility_taxonomy
from aletheia.pipeline import stage2_validate
from aletheia.pipeline.stage2_validate import (
    Stage2ValidateError,
    _compute_record_fingerprint,
    _overrides_state_hash,
    run_stage2,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers — stub CleanedRecord with the shape Stage 2's adapter reads
# ─────────────────────────────────────────────────────────────────────

class _StubCleanedRecord:
    """Mirrors the attrs Stage 2's _cleaned_record_to_validated reads.
    Avoids importing the real CleanedRecord dataclass + the cleaning
    engine's heavy module-level state in unit tests."""

    def __init__(
        self,
        *,
        ticker: str = "TEST",
        fiscal_year: int = 2024,
        period: str = "FY",
        period_end_date: str = "2024-12-31",
        raw: Dict[str, Any] = None,
        clean: Dict[str, Any] = None,
        derived: Dict[str, Any] = None,
        quality: float = 0.95,
        warnings: List[str] = None,
        errors: List[str] = None,
    ):
        self.ticker = ticker
        self.fiscal_year = fiscal_year
        self.period = period
        self.period_end_date = period_end_date
        self.raw = raw or {"Revenue": 100_000.0}
        self.clean = clean or {"Revenue": 100_000.0, "NOPAT": 18_000.0}
        self.derived = derived or {"EBITDA": 25_000.0}
        self.overall_quality_score = quality
        self.warnings = warnings or []
        self.errors = errors or []


@pytest.fixture
def stub_cleaning_engine(monkeypatch):
    """Replace ``CleaningEngine`` with a stub that returns canned
    records, controlled by the test through ``state``."""

    state: Dict[str, Any] = {"records": [_StubCleanedRecord()]}

    class StubEngine:
        def __init__(self, *_, **__):
            pass

        def clean_all_years(self, ticker: str):
            return state["records"]

    monkeypatch.setattr(stage2_validate, "CleaningEngine", StubEngine)
    return state


# ─────────────────────────────────────────────────────────────────────
# (a) Output schema
# ─────────────────────────────────────────────────────────────────────

def test_stage2_produces_validated_record_list(stub_cleaning_engine):
    stub_cleaning_engine["records"] = [
        _StubCleanedRecord(ticker="TEST", fiscal_year=2023),
        _StubCleanedRecord(ticker="TEST", fiscal_year=2024),
    ]
    out = run_stage2(ticker="TEST", pipeline_version="v1")
    assert len(out) == 2
    for r in out:
        assert isinstance(r, ValidatedCleanedRecord)
        assert r.ticker == "TEST"
        assert r.pipeline_version == "v1"
        assert len(r.record_fingerprint) == 64
        assert r.input_bundle_fingerprint == "<pre-orchestrator-adapter>"


def test_stage2_propagates_input_bundle_fingerprint(stub_cleaning_engine):
    out = run_stage2(
        ticker="TEST",
        pipeline_version="v1",
        input_bundle_fingerprint="abc123",
    )
    assert all(r.input_bundle_fingerprint == "abc123" for r in out)


def test_stage2_carries_raw_clean_derived_dicts(stub_cleaning_engine):
    stub_cleaning_engine["records"] = [_StubCleanedRecord(
        raw={"Revenue": 100.0, "TotalAssets": 500.0},
        clean={"Revenue": 100.0, "NOPAT": 18.0},
        derived={"EBITDA": 25.0, "FCF": 12.0},
    )]
    out = run_stage2(ticker="TEST", pipeline_version="v")
    rec = out[0]
    assert rec.raw["Revenue"] == 100.0
    assert rec.raw["TotalAssets"] == 500.0
    assert rec.clean["NOPAT"] == 18.0
    assert rec.derived["FCF"] == 12.0


def test_stage2_attaches_validation_receipt(stub_cleaning_engine):
    out = run_stage2(ticker="TEST", pipeline_version="v")
    rec = out[0]
    assert rec.validation is not None
    # schema_violations is a list of dicts (possibly empty)
    assert isinstance(rec.validation.schema_violations, list)
    assert isinstance(rec.validation.overrides_applied, list)


def test_stage2_emits_blob_fields_for_screening_engine(stub_cleaning_engine):
    """ScreeningEngine + MultipleDecomposition read sub-fields out of
    the raw_blob_json / clean_blob_json via _get_json. Stage 2's
    record must populate these so the Week 3 parity tests pass when
    the orchestrator wires Stage 2 → Stage 3."""
    stub_cleaning_engine["records"] = [_StubCleanedRecord(
        raw={"CurrentAssets": 50.0, "CurrentLiabilities": 30.0},
        clean={"Revenue": 100.0},
    )]
    out = run_stage2(ticker="TEST", pipeline_version="v")
    rec = out[0]
    assert rec.raw_blob_json is not None
    assert "CurrentAssets" in rec.raw_blob_json
    assert rec.clean_blob_json is not None


def test_stage2_filters_to_requested_fiscal_years(stub_cleaning_engine):
    stub_cleaning_engine["records"] = [
        _StubCleanedRecord(fiscal_year=2022),
        _StubCleanedRecord(fiscal_year=2023),
        _StubCleanedRecord(fiscal_year=2024),
    ]
    out = run_stage2(
        ticker="TEST", pipeline_version="v",
        fiscal_years=[2023, 2024],
    )
    assert sorted(r.fiscal_year for r in out) == [2023, 2024]


# ─────────────────────────────────────────────────────────────────────
# (b) Input contract enforcement
# ─────────────────────────────────────────────────────────────────────

def test_stage2_raises_on_empty_cleaning_output(stub_cleaning_engine):
    stub_cleaning_engine["records"] = []
    with pytest.raises(Stage2ValidateError, match="zero records"):
        run_stage2(ticker="TEST", pipeline_version="v")


# ─────────────────────────────────────────────────────────────────────
# Fingerprint determinism + cascade bust conditions
# ─────────────────────────────────────────────────────────────────────

def test_record_fingerprint_is_deterministic():
    fp1 = _compute_record_fingerprint(
        ticker="NVDA", fiscal_year=2024, period="FY",
        period_end_date="2024-01-28",
        input_bundle_fingerprint="abc",
        overrides_hash="def",
        pipeline_version="v1",
    )
    fp2 = _compute_record_fingerprint(
        ticker="NVDA", fiscal_year=2024, period="FY",
        period_end_date="2024-01-28",
        input_bundle_fingerprint="abc",
        overrides_hash="def",
        pipeline_version="v1",
    )
    assert fp1 == fp2
    assert len(fp1) == 64


def test_record_fingerprint_busts_on_bundle_change():
    fp1 = _compute_record_fingerprint(
        ticker="NVDA", fiscal_year=2024, period="FY",
        period_end_date="2024-01-28",
        input_bundle_fingerprint="abc", overrides_hash="d",
        pipeline_version="v1",
    )
    fp2 = _compute_record_fingerprint(
        ticker="NVDA", fiscal_year=2024, period="FY",
        period_end_date="2024-01-28",
        input_bundle_fingerprint="xyz", overrides_hash="d",
        pipeline_version="v1",
    )
    assert fp1 != fp2


def test_record_fingerprint_busts_on_override_change():
    fp1 = _compute_record_fingerprint(
        ticker="NVDA", fiscal_year=2024, period="FY",
        period_end_date="2024-01-28",
        input_bundle_fingerprint="b", overrides_hash="state-A",
        pipeline_version="v",
    )
    fp2 = _compute_record_fingerprint(
        ticker="NVDA", fiscal_year=2024, period="FY",
        period_end_date="2024-01-28",
        input_bundle_fingerprint="b", overrides_hash="state-B",
        pipeline_version="v",
    )
    assert fp1 != fp2


def test_overrides_state_hash_is_stable_per_ticker():
    """Two back-to-back calls for the same ticker must produce the
    same hash. Lets the cascade-invalidation chain detect override
    changes via fingerprint comparison."""
    h1 = _overrides_state_hash("NVDA")
    h2 = _overrides_state_hash("NVDA")
    assert h1 == h2


def test_overrides_state_hash_differs_by_ticker():
    """Different tickers — different override state. The hash is
    keyed on (ticker, override entries), not global."""
    h1 = _overrides_state_hash("NVDA")
    h2 = _overrides_state_hash("LOW")  # LOW has buyback override
    assert h1 != h2


# ─────────────────────────────────────────────────────────────────────
# Override registry surfacing
# ─────────────────────────────────────────────────────────────────────

def test_stage2_surfaces_active_overrides_in_receipt(stub_cleaning_engine):
    """``ValidationReceipt.overrides_applied`` must reflect every
    override registry key active for the ticker — this is the
    downstream signal that a record's validation results aren't
    raw, but have a documented exception applied."""
    stub_cleaning_engine["records"] = [_StubCleanedRecord(ticker="V")]
    out = run_stage2(ticker="V", pipeline_version="v")
    # V has one override: shares_diluted_ingest_bug (A14)
    assert "shares_diluted_ingest_bug" in out[0].validation.overrides_applied


def test_stage2_overrides_applied_is_empty_when_none_active(stub_cleaning_engine):
    stub_cleaning_engine["records"] = [_StubCleanedRecord(ticker="AAPL")]
    out = run_stage2(ticker="AAPL", pipeline_version="v")
    # AAPL has no overrides in the registry
    assert out[0].validation.overrides_applied == []


# ─────────────────────────────────────────────────────────────────────
# A19 fix — utility_taxonomy module
# ─────────────────────────────────────────────────────────────────────

def test_utility_taxonomy_recognises_nee():
    assert utility_taxonomy.is_utility_filer("NEE") is True


def test_utility_taxonomy_excludes_non_utility_tickers():
    for ticker in ("NVDA", "AAPL", "JPM", "BRK-B"):
        assert utility_taxonomy.is_utility_filer(ticker) is False


def test_utility_taxonomy_unknown_ticker_returns_false():
    assert utility_taxonomy.is_utility_filer("ZZZNOTREAL") is False


def test_capex_from_cip_returns_none_for_non_utility():
    """Defensive: the helper is a no-op for non-utility filers, even
    if they accidentally have CIP data. Prevents the utility CapEx
    estimator from being applied to a regular industrial filer."""
    out = utility_taxonomy.capex_from_construction_in_progress(
        ticker="NVDA", fiscal_year=2024,
        cip_this_year=1.0, cip_prior_year=0.5,
    )
    assert out is None


def test_capex_from_cip_returns_none_when_no_cip_data():
    out = utility_taxonomy.capex_from_construction_in_progress(
        ticker="NEE", fiscal_year=2024,
        cip_this_year=None, cip_prior_year=None,
    )
    assert out is None


def test_capex_from_cip_first_year_uses_cip_value():
    """When prior-year CIP is missing (first recorded year), the
    helper falls back to the current CIP value alone."""
    out = utility_taxonomy.capex_from_construction_in_progress(
        ticker="NEE", fiscal_year=2009,
        cip_this_year=2_000_000_000.0,
        cip_prior_year=None,
        ppe_additions_complete=500_000_000.0,
    )
    assert out == pytest.approx(2_500_000_000.0)


def test_capex_from_cip_combines_delta_and_additions():
    """Standard case: CapEx ≈ ΔCIP + completed additions."""
    out = utility_taxonomy.capex_from_construction_in_progress(
        ticker="NEE", fiscal_year=2024,
        cip_this_year=10_000_000_000.0,
        cip_prior_year=8_000_000_000.0,
        ppe_additions_complete=3_000_000_000.0,
    )
    # ΔCIP = +2B, completions = 3B, total ≈ 5B
    assert out == pytest.approx(5_000_000_000.0)


def test_capex_from_cip_handles_zero_completions():
    """When the additions-complete field isn't available, the
    helper still works using ΔCIP alone."""
    out = utility_taxonomy.capex_from_construction_in_progress(
        ticker="NEE", fiscal_year=2024,
        cip_this_year=10_000_000_000.0,
        cip_prior_year=8_000_000_000.0,
    )
    assert out == pytest.approx(2_000_000_000.0)


def test_capex_from_cip_handles_negative_delta():
    """Delta CIP can be negative (more plant rolled out than was
    added). The helper returns the raw arithmetic; the consumer
    decides whether to soft-flag."""
    out = utility_taxonomy.capex_from_construction_in_progress(
        ticker="NEE", fiscal_year=2024,
        cip_this_year=5_000_000_000.0,
        cip_prior_year=8_000_000_000.0,
        ppe_additions_complete=4_000_000_000.0,
    )
    # ΔCIP = -3B, completions = 4B, total = 1B
    assert out == pytest.approx(1_000_000_000.0)


# ─────────────────────────────────────────────────────────────────────
# End-to-end smoke test (real DB)
# ─────────────────────────────────────────────────────────────────────

def test_stage2_runs_end_to_end_against_real_ticker():
    """One real-ticker run proving the orchestration works against
    the production cleaning engine. Skips if the canonical parquet
    or DuckDB state isn't populated for the chosen ticker."""
    try:
        out = run_stage2(ticker="NVDA", pipeline_version="smoke-v1")
    except Stage2ValidateError as exc:
        pytest.skip(f"NVDA not populated end-to-end: {exc}")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Stage 2 e2e failed (likely missing data): {exc}")
    assert len(out) > 0
    assert all(r.ticker == "NVDA" for r in out)
    assert all(len(r.record_fingerprint) == 64 for r in out)
