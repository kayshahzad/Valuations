"""Impairment add-back dedupe (fmp_stage3_adapter).

XBRL impairment tags form an umbrella/child hierarchy — AssetImpairmentCharges
typically subsumes GoodwillImpairmentLoss / IntangibleAssetImpairmentCharge /
ImpairmentOfLongLivedAssetsHeldForUse. Naively summing all tags double-counts
when a filer tags the same impairment at both levels — this is what corrupted
CNC's FY2025 ex-unusual numbers (a ~$7B impairment summed to ~$14B). The
add-back must be MAX(umbrella, Σ children) + restructuring.
"""

from __future__ import annotations

import pytest

from aletheia.validation.fmp_stage3_adapter import (
    _asset_impairment_addback,
    _ex_impairment_addback,
)


def test_umbrella_and_children_not_double_counted():
    # The CNC FY2025 shape: a single impairment tagged at BOTH the umbrella and
    # the component level. Must resolve to the true ~$7B, not the ~$14B sum.
    rec = {
        "AssetImpairmentCharges": 7.0e9,
        "GoodwillImpairmentLoss": 5.0e9,
        "IntangibleAssetImpairmentCharge": 2.0e9,
    }
    assert _asset_impairment_addback(rec) == pytest.approx(7.0e9)


def test_children_only_are_summed():
    # No umbrella tag → the specific components are the only signal; sum them.
    rec = {"GoodwillImpairmentLoss": 5.0e9, "IntangibleAssetImpairmentCharge": 2.0e9}
    assert _asset_impairment_addback(rec) == pytest.approx(7.0e9)


def test_umbrella_only():
    assert _asset_impairment_addback({"AssetImpairmentCharges": 7.0e9}) == pytest.approx(7.0e9)


def test_restructuring_is_additive():
    # Restructuring is a genuinely separate charge — added on top of the
    # (deduped) asset-impairment bucket.
    rec = {"AssetImpairmentCharges": 7.0e9, "RestructuringCharges": 1.0e9}
    assert _asset_impairment_addback(rec) == pytest.approx(8.0e9)


def test_children_exceeding_umbrella_use_the_larger():
    # Defensive: if the components sum above a smaller umbrella (unusual tagging),
    # MAX keeps the larger so we never UNDER-count a real charge.
    rec = {
        "AssetImpairmentCharges": 3.0e9,
        "GoodwillImpairmentLoss": 4.0e9,
        "ImpairmentOfLongLivedAssetsHeldForUse": 1.0e9,
    }
    assert _asset_impairment_addback(rec) == pytest.approx(5.0e9)


def test_no_impairment_tags_returns_none():
    assert _asset_impairment_addback({"Revenue": 100.0e9}) is None


def test_abs_magnitudes_handled():
    # Tags may arrive with either sign; the add-back is a magnitude.
    rec = {"GoodwillImpairmentLoss": -5.0e9, "RestructuringCharges": -1.0e9}
    assert _asset_impairment_addback(rec) == pytest.approx(6.0e9)


def test_ex_impairment_addback_prefers_deduped_discrete_over_fmp_bucket():
    # CNC-like: huge otherExpenses bucket present, but discrete tags win and are
    # deduped — so the add-back is the true ~$7B, not the bucket and not ~$14B.
    rec = {
        "AssetImpairmentCharges": 7.0e9,
        "GoodwillImpairmentLoss": 5.0e9,
        "IntangibleAssetImpairmentCharge": 2.0e9,
        "OtherOperatingItems": 18.0e9,
        "Revenue": 195.0e9,
    }
    addback, source = _ex_impairment_addback(rec)
    assert source == "xbrl_discrete_tags"
    assert addback == pytest.approx(7.0e9)
