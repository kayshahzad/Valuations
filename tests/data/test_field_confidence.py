"""Phase 3.5 — composite per-field confidence score."""
from __future__ import annotations

from aletheia.data.field_confidence import (
    field_confidence, build_confidence_map, summarize,
)


def _lvl(**kw):
    return field_confidence(**kw)[0]


class TestFieldConfidence:
    def test_reported_and_sec_validated_is_high(self):
        lvl, score, _ = field_confidence(provenance="raw", cross_source_flag="validated")
        assert lvl == "high" and score == 100

    def test_reported_single_source_is_medium(self):
        # reported but no authoritative SEC check → medium, not high
        assert _lvl(provenance="raw", cross_source_flag="sec_missing") == "medium"

    def test_derived_unverified_is_low(self):
        assert _lvl(provenance="derived", cross_source_flag=None) == "low"

    def test_sec_disagreement_is_suspect(self):
        # the AAPL-AR case: reported but SEC drifts >5% → suspect, not high
        assert _lvl(provenance="raw", cross_source_flag="drift") == "suspect"

    def test_fabricated_dominates_everything(self):
        # even if it would otherwise validate, a substituted constant is fabricated
        lvl, score, _ = field_confidence(
            provenance="raw", cross_source_flag="validated", fallback_applied=True)
        assert lvl == "fabricated" and score < 20

    def test_missing_value(self):
        assert _lvl(provenance="missing", cross_source_flag=None) == "missing"
        assert _lvl(provenance="raw", cross_source_flag="ours_missing") == "missing"

    def test_identity_violation_is_suspect(self):
        assert _lvl(provenance="raw", cross_source_flag="sec_missing",
                    identity_closed=False) == "suspect"


class TestBuildAndSummarize:
    def test_map_and_summary(self):
        prov = {"Revenue": "raw", "EBITDA": "derived", "NOPAT": "raw",
                "AR": "raw", "Equity": "raw"}
        xsrc = {"Revenue": {"flag": "validated"}, "EBITDA": {"flag": "sec_missing"},
                "AR": {"flag": "drift"}}
        cmap = build_confidence_map(
            list(prov), provenance=prov, cross_source=xsrc,
            fabricated_fields={"NOPAT"})
        assert cmap["Revenue"]["level"] == "high"
        assert cmap["AR"]["level"] == "suspect"
        assert cmap["NOPAT"]["level"] == "fabricated"

        s = summarize(cmap)
        assert s["n_fields"] == 5
        assert set(s["needs_attention"]) == {"AR", "NOPAT"}
        assert s["mean_score"] is not None
