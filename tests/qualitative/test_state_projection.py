"""Pure-function tests for state_projection.

Exercises every coverage state (zero / low / medium / high) and the
status-resolution rules without touching DuckDB. Records are built by
hand to exercise the projector's branches.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from config.qualitative_dimensions import (
    DIMENSIONS,
    CATEGORIES,
    category_composite_weights,
)
from aletheia.qualitative.state_projection import (
    coverage_summary,
    dashboard_state_fingerprint,
    project_categories,
    project_dimensions,
    status_for,
)
from aletheia.qualitative.types import SourceCategory


# ── Helpers ──────────────────────────────────────────────────────────────

_FIXED_GIT_SHA = "fixedsha000"


def _record(score: float, age_days: int = 0,
            git_sha: Optional[str] = _FIXED_GIT_SHA,
            narrative: str = "narrative text") -> Dict[str, Any]:
    """Build a fake assessment record `age_days` old."""
    assessed_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=age_days)
    return {
        "score":        score,
        "narrative":    narrative,
        "assessed_at":  assessed_at.isoformat(),
        "code_git_sha": git_sha,
    }


def _records_for(*pairs) -> Dict[str, Dict[str, Any]]:
    """Build {dim_id: record} from (dim_id, score, age_days?) tuples."""
    out = {}
    for p in pairs:
        if len(p) == 2:
            dim_id, score = p
            age = 0
        else:
            dim_id, score, age = p
        out[dim_id] = _record(score, age_days=age)
    return out


# ── status_for ───────────────────────────────────────────────────────────

def test_status_pending_data_overrides_record():
    dim = DIMENSIONS["industry_concentration"]
    assert dim.source_category == SourceCategory.PENDING_DATA
    # Even with a fake record present, PENDING_DATA wins
    rec = _record(5.0, age_days=0)
    assert status_for(dim, rec, _FIXED_GIT_SHA) == "pending_data"


def test_status_not_assessed_when_no_record():
    dim = DIMENSIONS["moat_strength"]
    assert status_for(dim, None, _FIXED_GIT_SHA) == "not_assessed"


def test_status_assessed_when_fresh():
    dim = DIMENSIONS["moat_strength"]
    rec = _record(6.2, age_days=10)
    assert status_for(dim, rec, _FIXED_GIT_SHA) == "assessed"


def test_status_stale_by_age():
    dim = DIMENSIONS["moat_strength"]   # staleness_days=365
    rec = _record(6.2, age_days=400)
    assert status_for(dim, rec, _FIXED_GIT_SHA) == "stale"


def test_status_stale_by_git_sha_for_deterministic():
    dim = DIMENSIONS["roiic_trend"]   # DETERMINISTIC
    rec = _record(7.0, age_days=10, git_sha="oldsha999")
    assert status_for(dim, rec, _FIXED_GIT_SHA) == "stale"


def test_status_hitl_git_sha_does_not_force_stale():
    """HITL submissions stamp git_sha but a code change unrelated to the
    catalog shouldn't flag them stale (rationale documented in api_main)."""
    dim = DIMENSIONS["moat_strength"]   # HITL
    rec = _record(6.2, age_days=10, git_sha="oldsha999")
    assert status_for(dim, rec, _FIXED_GIT_SHA) == "assessed"


# ── project_dimensions ───────────────────────────────────────────────────

def test_project_dimensions_emits_one_row_per_catalog_dim():
    proj = project_dimensions(DIMENSIONS, {}, _FIXED_GIT_SHA)
    assert set(proj.keys()) == set(DIMENSIONS.keys())
    assert len(proj) == 19


def test_project_dimensions_marks_pending_data_not_citable():
    proj = project_dimensions(DIMENSIONS, {}, _FIXED_GIT_SHA)
    assert proj["industry_concentration"]["status"] == "pending_data"
    assert proj["industry_concentration"]["citable"] is False
    # Tenure & alignment also pending_data
    assert proj["management_tenure_continuity"]["citable"] is False
    assert proj["management_alignment"]["citable"] is False


def test_project_dimensions_marks_assessed_citable():
    records = _records_for(
        ("moat_strength", 6.2, 10),
        ("roiic_trend",   7.0,  5),
    )
    proj = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    assert proj["moat_strength"]["citable"] is True
    assert proj["moat_strength"]["score"] == 6.2
    assert proj["roiic_trend"]["citable"] is True
    # Untouched dim is not citable
    assert proj["pricing_power"]["citable"] is False


def test_project_dimensions_marks_stale_still_citable():
    records = _records_for(("moat_strength", 6.2, 400))   # past 365d
    proj = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    assert proj["moat_strength"]["status"] == "stale"
    assert proj["moat_strength"]["citable"] is True


# ── project_categories ───────────────────────────────────────────────────

def test_project_categories_no_records_all_composite_none():
    proj_d = project_dimensions(DIMENSIONS, {}, _FIXED_GIT_SHA)
    proj_c = project_categories(DIMENSIONS, CATEGORIES,
                                category_composite_weights, proj_d)
    for cat in proj_c.values():
        assert cat["composite_score"] is None
        assert cat["status"] == "not_assessed"


def test_project_categories_partial_renormalizes():
    """Quality has 5 members with equal weight 0.2. With moat_strength
    and roiic_trend assessed, renormalized weights become 0.5 each and
    composite is the simple average of the two scores."""
    records = _records_for(
        ("moat_strength", 6.0, 10),
        ("roiic_trend",   8.0,  5),
    )
    proj_d = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    proj_c = project_categories(DIMENSIONS, CATEGORIES,
                                category_composite_weights, proj_d)
    quality = proj_c["quality"]
    assert quality["n_assessed"] == 2
    assert quality["n_total"] == 5      # 5 quality dims, no PENDING_DATA in quality
    assert quality["composite_score"] == 7.0   # (6+8)/2
    contributing_ids = {c["dimension_id"] for c in quality["contributing"]}
    assert contributing_ids == {"moat_strength", "roiic_trend"}


def test_project_categories_stale_member_flags_composite_stale():
    """Locked Q2: composite freshness = worst-of contributing."""
    records = _records_for(
        ("moat_strength", 6.0,  10),    # fresh
        ("roiic_trend",   8.0, 800),    # stale (staleness_days=730)
    )
    proj_d = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    proj_c = project_categories(DIMENSIONS, CATEGORIES,
                                category_composite_weights, proj_d)
    quality = proj_c["quality"]
    assert quality["composite_score"] == 7.0
    assert quality["status"] == "stale"
    assert "roiic_trend" in quality["stale_contributors"]


def test_project_categories_management_pending_only_returns_zero_total():
    """Management has both members PENDING_DATA → n_total=0, no composite
    even with imagined assessments (which can't happen anyway since
    PENDING_DATA dims aren't user-fillable)."""
    proj_d = project_dimensions(DIMENSIONS, {}, _FIXED_GIT_SHA)
    proj_c = project_categories(DIMENSIONS, CATEGORIES,
                                category_composite_weights, proj_d)
    mgmt = proj_c["management"]
    assert mgmt["n_total"] == 0
    assert mgmt["composite_score"] is None


# ── coverage_summary ─────────────────────────────────────────────────────

def test_coverage_zero_when_no_records():
    proj_d = project_dimensions(DIMENSIONS, {}, _FIXED_GIT_SHA)
    cov = coverage_summary(proj_d)
    assert cov["coverage_state"] == "zero"
    assert cov["n_assessed"] == 0
    # 19 catalog dims minus 3 PENDING_DATA = 16 assessable
    assert cov["n_assessable"] == 16
    assert cov["n_pending"] == 3
    assert cov["citable_dim_paths"] == []


def test_coverage_low_with_4_deterministic():
    """Mirrors the actual NVDA / COST coverage state today."""
    records = _records_for(
        ("roiic_trend",         7.0, 5),
        ("buyback_discipline",  6.0, 5),
        ("dividend_policy",     5.5, 5),
        ("cyclicality",         3.0, 5),
    )
    proj_d = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    cov = coverage_summary(proj_d)
    assert cov["coverage_state"] == "low"
    assert cov["n_assessed"] == 4


def test_coverage_medium_at_8_assessed():
    records = _records_for(
        *((d, 5.0, 5) for d in [
            "moat_strength", "roiic_trend", "pricing_power",
            "capital_allocation_track_record", "buyback_discipline",
            "dividend_policy", "market_position", "cyclicality",
        ])
    )
    proj_d = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    cov = coverage_summary(proj_d)
    assert cov["coverage_state"] == "medium"
    assert cov["n_assessed"] == 8


def test_coverage_high_at_12_assessed():
    citable_ids = [
        d for d in DIMENSIONS
        if DIMENSIONS[d].source_category != SourceCategory.PENDING_DATA
    ][:12]
    records = _records_for(*((d, 5.0, 5) for d in citable_ids))
    proj_d = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    cov = coverage_summary(proj_d)
    assert cov["coverage_state"] == "high"
    assert cov["n_assessed"] == 12


def test_coverage_citable_dim_paths_format():
    records = _records_for(("moat_strength", 6.0, 10))
    proj_d = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    cov = coverage_summary(proj_d)
    assert "qualitative.moat_strength" in cov["citable_dim_paths"]


def test_coverage_stale_paths_listed_separately():
    records = _records_for(
        ("moat_strength", 6.0, 10),    # fresh
        ("roiic_trend",   8.0, 800),   # stale
    )
    proj_d = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    cov = coverage_summary(proj_d)
    # Both still cited (stale dims are citable, just flagged)
    assert "qualitative.moat_strength" in cov["citable_dim_paths"]
    assert "qualitative.roiic_trend" in cov["citable_dim_paths"]
    # But stale_paths only contains the stale one
    assert cov["stale_paths"] == ["qualitative.roiic_trend"]


# ── dashboard_state_fingerprint ──────────────────────────────────────────

def test_fingerprint_stable_for_identical_input():
    records = _records_for(("moat_strength", 6.0, 10))
    proj_a = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    proj_b = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    assert dashboard_state_fingerprint(proj_a) == dashboard_state_fingerprint(proj_b)


def test_fingerprint_changes_when_score_changes():
    records_a = _records_for(("moat_strength", 6.0, 10))
    records_b = _records_for(("moat_strength", 6.5, 10))
    proj_a = project_dimensions(DIMENSIONS, records_a, _FIXED_GIT_SHA)
    proj_b = project_dimensions(DIMENSIONS, records_b, _FIXED_GIT_SHA)
    assert dashboard_state_fingerprint(proj_a) != dashboard_state_fingerprint(proj_b)


def test_fingerprint_changes_when_narrative_changes():
    rec_a = _record(6.0, age_days=10, narrative="cuda lock-in")
    rec_b = _record(6.0, age_days=10, narrative="rewritten narrative")
    proj_a = project_dimensions(DIMENSIONS, {"moat_strength": rec_a}, _FIXED_GIT_SHA)
    proj_b = project_dimensions(DIMENSIONS, {"moat_strength": rec_b}, _FIXED_GIT_SHA)
    assert dashboard_state_fingerprint(proj_a) != dashboard_state_fingerprint(proj_b)


def test_fingerprint_changes_when_dim_added():
    """Adding a new assessment must shift the fingerprint."""
    records_a = _records_for(("moat_strength", 6.0, 10))
    records_b = _records_for(("moat_strength", 6.0, 10),
                             ("roiic_trend",   7.0,  5))
    proj_a = project_dimensions(DIMENSIONS, records_a, _FIXED_GIT_SHA)
    proj_b = project_dimensions(DIMENSIONS, records_b, _FIXED_GIT_SHA)
    assert dashboard_state_fingerprint(proj_a) != dashboard_state_fingerprint(proj_b)


def test_fingerprint_excludes_not_assessed_and_pending():
    """Only citable dims contribute. Empty-state changes don't shift fp."""
    records = _records_for(("moat_strength", 6.0, 10))
    proj = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    # All NOT_ASSESSED and PENDING_DATA dims are present in the projection
    # but they shouldn't influence the fingerprint
    fp_with_full_projection = dashboard_state_fingerprint(proj)
    # Now manually project with only the moat record visible
    minimal_proj = {"moat_strength": proj["moat_strength"]}
    fp_minimal = dashboard_state_fingerprint(minimal_proj)
    assert fp_with_full_projection == fp_minimal


def test_fingerprint_zero_coverage_is_stable_empty_hash():
    proj = project_dimensions(DIMENSIONS, {}, _FIXED_GIT_SHA)
    fp1 = dashboard_state_fingerprint(proj)
    fp2 = dashboard_state_fingerprint({})
    assert fp1 == fp2
    assert isinstance(fp1, str) and len(fp1) == 16


def test_fingerprint_format_is_16_char_hex():
    records = _records_for(("moat_strength", 6.0, 10))
    proj = project_dimensions(DIMENSIONS, records, _FIXED_GIT_SHA)
    fp = dashboard_state_fingerprint(proj)
    assert len(fp) == 16
    int(fp, 16)   # must be valid hex
