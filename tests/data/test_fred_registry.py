"""FRED series registry (Phase 0) — network-free structural guards.

These enforce the channel contract so a careless series addition can't ship:
Channel A (moves IV) must carry bounds + staleness, transforms must normalize
units (the 100x landmine), and the registry must be internally consistent.
"""
import pytest

from aletheia.data import fred_client as f


def test_registry_is_internally_consistent():
    assert f.validate_registry() == [], f.validate_registry()


def test_channel_a_series_carry_bounds_and_staleness():
    """The inverse guard: a Channel-A series added without a plausibility band or
    a positive staleness bound must fail here — it moves IV and cannot ship
    without the rigor of a cleaned field."""
    a = [s for s in f.FRED_SERIES.values() if s.channel == "A"]
    assert a, "expected at least one Channel-A series (DGS10)"
    for spec in a:
        assert spec.bounds is not None, f"{spec.series_id}: Channel A needs bounds"
        assert spec.staleness_days > 0, f"{spec.series_id}: needs staleness bound"


def test_transform_normalizes_rate_percent_to_decimal():
    """The 100x landmine: FRED returns 4.42 for 4.42%; a rate feeding WACC must
    become 0.0442. And the declared bounds must be on the TRANSFORMED value."""
    dgs10 = f.FRED_SERIES["DGS10"]
    assert dgs10.transform(4.42) == pytest.approx(0.0442)
    lo, hi = dgs10.bounds
    assert lo < dgs10.transform(4.42) < hi          # bounds in decimal space
    assert hi <= 1.0                                # decimal, not percent (would be ~20)


def test_oas_series_stay_in_percent():
    """OAS consumers (credit regime + reference distribution) work in percent, so
    those series carry no transform and percent-scale bounds."""
    hy = f.FRED_SERIES["BAMLH0A0HYM2"]
    assert hy.transform is None
    assert hy.channel == "B" and hy.bounds[1] > 1.0   # percent scale (e.g. up to 30)


def test_staleness_bounds_match_frequency():
    """5 days for daily (covers a long holiday weekend), 45 for monthly."""
    for spec in f.FRED_SERIES.values():
        if spec.freq == "daily":
            assert spec.staleness_days >= 5
        elif spec.freq == "monthly":
            assert spec.staleness_days >= 30


def test_channel_c_is_display_scoped():
    """Every Channel-C series is display-only — documented so the architecture
    test (Phase 2) can assert no calc/agent module imports the C accessor."""
    c = [s.series_id for s in f.FRED_SERIES.values() if s.channel == "C"]
    assert "UMCSENT" in c and "DGS2" in c
