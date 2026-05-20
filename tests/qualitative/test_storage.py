"""qualitative_assessments — versioning, validation, round-trip.

Step 1 of the qualitative-framework rollout adds a versioned DB store
for analytical assessments. This test pins the contract:

  1. Catalog loads cleanly (all 19 dimensions, weights sum to 1.0 per HITL
     dimension, no ID collisions).
  2. Catalog hash is stable across calls and changes when weights change.
  3. Writes to `qualitative_assessments` create new rows; latest view
     surfaces the most recent per (ticker, dimension_id).
  4. The writer rejects:
     - Unknown dimension_ids (catalog membership)
     - Source-category mismatches (HITL submission against deterministic dim)
     - Out-of-range scores (>7 or <1)
     - Narratives over 500 chars
  5. Round-trip: written assessment decodes back identically including
     sub_scores and source_payload JSON.

Tests use a tmp-path DuckDB so they don't pollute the production DB.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from aletheia.data.database import InvestmentDatabase
from aletheia.qualitative.types import AssessmentRecord, SourceCategory


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "qual_test.duckdb"
    db = InvestmentDatabase(db_path=str(path), verbose=False)
    yield db
    db.close()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _hitl_record(ticker: str, dimension_id: str, score: float, narrative: str = None) -> AssessmentRecord:
    return AssessmentRecord(
        assessment_id=str(uuid.uuid4()),
        ticker=ticker,
        dimension_id=dimension_id,
        score=score,
        sub_scores={"q1": 6.0, "q2": 5.0, "q3": 5.0, "q4": 4.0, "q5": 4.0},
        narrative=narrative,
        source_category=SourceCategory.HITL,
        source_payload={"prompts_version": "v1", "questions_answered": 5},
        assessed_at=_now_iso(),
        analyst_id="primary",
        code_git_sha="abc123",
        input_fingerprint=None,
    )


def _det_record(ticker: str, dimension_id: str, score: float) -> AssessmentRecord:
    return AssessmentRecord(
        assessment_id=str(uuid.uuid4()),
        ticker=ticker,
        dimension_id=dimension_id,
        score=score,
        sub_scores=None,
        narrative=None,
        source_category=SourceCategory.DETERMINISTIC,
        source_payload={"inputs": {"sample": 0.25}, "formula": "test_v1"},
        assessed_at=_now_iso(),
        analyst_id="system",
        code_git_sha="abc123",
        input_fingerprint="hash_xyz",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Catalog
# ─────────────────────────────────────────────────────────────────────────────

class TestCatalog:

    def test_all_dimensions_load(self):
        from config.qualitative_dimensions import DIMENSIONS
        assert len(DIMENSIONS) == 19
        # No duplicate IDs (Dict guarantees this but verifies the registry)
        assert len(set(DIMENSIONS.keys())) == 19

    def test_hitl_weights_sum_to_one(self):
        """The QualitativeDimension constructor enforces this at module
        import. If this test fails the catalog wouldn't have loaded —
        kept here as an explicit assertion for future debugging."""
        from config.qualitative_dimensions import DIMENSIONS
        for d in DIMENSIONS.values():
            if d.source_category == SourceCategory.HITL:
                total = sum(q.weight for q in d.questions)
                assert 0.99 <= total <= 1.01, f"{d.id}: weights sum to {total}"

    def test_deterministic_dimensions_have_formula(self):
        from config.qualitative_dimensions import DIMENSIONS
        for d in DIMENSIONS.values():
            if d.source_category == SourceCategory.DETERMINISTIC:
                assert d.formula_citation, f"{d.id}: missing formula_citation"

    def test_pending_data_dimensions_have_no_questions(self):
        """PENDING_DATA dimensions are slots awaiting infrastructure;
        they shouldn't render as HITL prompts even if questions are
        wired (the empty state handles that)."""
        from config.qualitative_dimensions import DIMENSIONS
        for d in DIMENSIONS.values():
            if d.source_category == SourceCategory.PENDING_DATA:
                assert not d.questions

    def test_catalog_hash_is_stable(self):
        """Two calls produce the same hash — needed for the localStorage
        draft-key invariant in Step 5."""
        from config.qualitative_dimensions import MOAT_STRENGTH
        h1 = MOAT_STRENGTH.catalog_hash()
        h2 = MOAT_STRENGTH.catalog_hash()
        assert h1 == h2

    def test_catalog_hash_changes_with_questions(self):
        """If a question is reworded, the hash changes — the localStorage
        draft for the old question structure invalidates."""
        from aletheia.qualitative.types import QualitativeDimension, SubQuestion
        d1 = QualitativeDimension(
            id="t1", category="quality", title="T",
            source_category=SourceCategory.HITL, staleness_days=180,
            questions=(SubQuestion(id="q1", text="original", weight=1.0),),
        )
        d2 = QualitativeDimension(
            id="t1", category="quality", title="T",
            source_category=SourceCategory.HITL, staleness_days=180,
            questions=(SubQuestion(id="q1", text="REWORDED", weight=1.0),),
        )
        assert d1.catalog_hash() != d2.catalog_hash()

    def test_category_composite_excludes_pending_data(self):
        """The Management category has 2 PENDING_DATA dimensions and no
        others. Its composite weights dict should be empty — the UI
        renders this as 'no composite available' rather than 'composite
        of zero things'."""
        from config.qualitative_dimensions import category_composite_weights
        assert category_composite_weights("management") == {}

    def test_category_composite_weights_sum_to_one(self):
        """For categories that do have composable members, weights
        normalize."""
        from config.qualitative_dimensions import category_composite_weights, CATEGORIES
        for cat_id, _ in CATEGORIES:
            weights = category_composite_weights(cat_id)
            if weights:
                assert abs(sum(weights.values()) - 1.0) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# Writer guardrails
# ─────────────────────────────────────────────────────────────────────────────

class TestWriterGuardrails:

    def test_rejects_unknown_dimension_id(self, db):
        bad = _hitl_record("AAPL", "completely_made_up_dimension", 5.0)
        with pytest.raises(ValueError, match="unknown dimension_id"):
            db.upsert_qualitative_assessment(bad)

    def test_rejects_source_category_mismatch(self, db):
        """ROIIC trend is DETERMINISTIC in the catalog; submitting a
        HITL record against it must fail — prevents the write path from
        accepting ill-formed assessments."""
        rec = _hitl_record("AAPL", "roiic_trend", 5.0)
        with pytest.raises(ValueError, match="source_category mismatch"):
            db.upsert_qualitative_assessment(rec)

    def test_rejects_score_above_seven(self, db):
        rec = _hitl_record("AAPL", "moat_strength", 8.5)
        with pytest.raises(ValueError, match="out of"):
            db.upsert_qualitative_assessment(rec)

    def test_rejects_score_below_one(self, db):
        rec = _hitl_record("AAPL", "moat_strength", 0.5)
        with pytest.raises(ValueError, match="out of"):
            db.upsert_qualitative_assessment(rec)

    def test_rejects_narrative_over_500_chars(self, db):
        rec = _hitl_record("AAPL", "moat_strength", 5.0, narrative="x" * 501)
        with pytest.raises(ValueError, match="500"):
            db.upsert_qualitative_assessment(rec)

    def test_pending_data_dimension_accepts_null_score(self, db):
        """PENDING_DATA dimensions legitimately have score=None — the
        slot exists, the data infrastructure doesn't yet."""
        rec = AssessmentRecord(
            assessment_id=str(uuid.uuid4()),
            ticker="AAPL",
            dimension_id="management_tenure_continuity",
            score=None,                     # legitimately null
            sub_scores=None,
            narrative=None,
            source_category=SourceCategory.PENDING_DATA,
            source_payload={"reason": "DEF 14A parser not yet built"},
            assessed_at=_now_iso(),
            analyst_id="system",
            code_git_sha=None,
            input_fingerprint=None,
        )
        db.upsert_qualitative_assessment(rec)  # should not raise


# ─────────────────────────────────────────────────────────────────────────────
# Versioning + read paths
# ─────────────────────────────────────────────────────────────────────────────

class TestVersioning:

    def test_first_write_creates_row(self, db):
        rec = _hitl_record("AAPL", "moat_strength", 6.0)
        db.upsert_qualitative_assessment(rec)
        latest = db.get_latest_assessment("AAPL", "moat_strength")
        assert latest is not None
        assert latest["score"] == 6.0
        assert latest["analyst_id"] == "primary"

    def test_later_assessment_supersedes_earlier(self, db):
        """Writes append; the view exposes the most recent. Older
        versions remain in `qualitative_assessments` for audit."""
        import time
        r1 = _hitl_record("AAPL", "moat_strength", 5.0, narrative="initial")
        db.upsert_qualitative_assessment(r1)
        time.sleep(0.01)  # ensure assessed_at differs
        r2 = _hitl_record("AAPL", "moat_strength", 6.5, narrative="re-assessed")
        db.upsert_qualitative_assessment(r2)

        latest = db.get_latest_assessment("AAPL", "moat_strength")
        assert latest["score"] == 6.5
        assert latest["narrative"] == "re-assessed"

        # History preserved
        rows = db.query(
            "SELECT score FROM qualitative_assessments "
            "WHERE ticker='AAPL' AND dimension_id='moat_strength' "
            "ORDER BY assessed_at"
        )
        assert list(rows["score"]) == [5.0, 6.5]

    def test_get_all_assessments_for_ticker(self, db):
        """Bulk read: one row per assessed dimension. Dimensions without
        any assessment are absent — the caller cross-references the
        catalog for empty states."""
        db.upsert_qualitative_assessment(_hitl_record("AAPL", "moat_strength", 6.0))
        db.upsert_qualitative_assessment(_det_record("AAPL", "roiic_trend", 7.0))
        db.upsert_qualitative_assessment(_hitl_record("NVDA", "moat_strength", 5.5))

        aapl = db.get_all_assessments_for_ticker("AAPL")
        assert set(aapl.keys()) == {"moat_strength", "roiic_trend"}
        assert aapl["moat_strength"]["score"] == 6.0
        assert aapl["roiic_trend"]["score"] == 7.0

        # NVDA's assessment doesn't bleed across tickers
        nvda = db.get_all_assessments_for_ticker("NVDA")
        assert set(nvda.keys()) == {"moat_strength"}

    def test_get_latest_returns_none_for_unassessed(self, db):
        assert db.get_latest_assessment("AAPL", "moat_strength") is None

    def test_get_latest_returns_none_for_unknown_ticker(self, db):
        assert db.get_latest_assessment("FAKETICKER", "moat_strength") is None


# ─────────────────────────────────────────────────────────────────────────────
# Round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestRoundTrip:

    def test_hitl_record_round_trips(self, db):
        original = _hitl_record(
            "AAPL", "moat_strength", 6.2, narrative="strong switching costs",
        )
        db.upsert_qualitative_assessment(original)
        read = db.get_latest_assessment("AAPL", "moat_strength")

        assert read["assessment_id"]   == original.assessment_id
        assert read["score"]           == 6.2
        assert read["narrative"]       == "strong switching costs"
        assert read["sub_scores"]      == original.sub_scores  # JSON decoded
        assert read["source_payload"]  == original.source_payload
        assert read["source_category"] == "hitl"
        assert read["analyst_id"]      == "primary"
        assert read["code_git_sha"]    == "abc123"

    def test_deterministic_record_round_trips(self, db):
        original = _det_record("AAPL", "roiic_trend", 6.0)
        db.upsert_qualitative_assessment(original)
        read = db.get_latest_assessment("AAPL", "roiic_trend")

        assert read["score"] == 6.0
        assert read["sub_scores"] is None
        assert read["narrative"] is None
        assert read["source_category"]   == "deterministic"
        assert read["input_fingerprint"] == "hash_xyz"
        assert read["source_payload"]    == original.source_payload
