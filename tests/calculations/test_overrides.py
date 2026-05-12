"""Phase 7 tests — per-ticker override registry.

Coverage:
  - Registry is well-formed at import (validate-at-import behavior)
  - Each entry has mandatory reason / created_date / review_by_date
  - is_override_active() and get_override() lookups behave correctly
  - log_past_due_overrides() identifies entries past their review date
  - Registry stays small (the framework's design assumption — overrides
    should be the exception, not the norm)
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from aletheia.calculations import (
    OVERRIDES,
    is_override_active,
    get_override,
    log_past_due_overrides,
)
from aletheia.calculations._overrides import (
    _REGISTRY_SIZE_WARNING_THRESHOLD,
    _validate_registry_at_import,
)


class TestRegistrySchema:
    """Every entry must carry the mandatory fields."""

    def test_validate_registry_passes_at_import(self):
        """The validation that runs at module import must pass —
        no malformed entries in the shipped registry."""
        _validate_registry_at_import()

    def test_every_entry_has_reason(self):
        for ticker, entries in OVERRIDES.items():
            for key, rec in entries.items():
                assert rec.get("reason"), (
                    f"OVERRIDES[{ticker!r}][{key!r}] missing 'reason'"
                )

    def test_every_entry_has_created_date(self):
        for ticker, entries in OVERRIDES.items():
            for key, rec in entries.items():
                assert rec.get("created_date"), (
                    f"OVERRIDES[{ticker!r}][{key!r}] missing 'created_date'"
                )

    def test_every_entry_has_review_by_date(self):
        for ticker, entries in OVERRIDES.items():
            for key, rec in entries.items():
                assert rec.get("review_by_date"), (
                    f"OVERRIDES[{ticker!r}][{key!r}] missing 'review_by_date'"
                )

    def test_dates_are_iso_parseable(self):
        for ticker, entries in OVERRIDES.items():
            for key, rec in entries.items():
                for field in ("created_date", "review_by_date"):
                    date.fromisoformat(rec[field])

    def test_reason_is_substantive(self):
        """No one-word 'reasons' that don't explain anything."""
        for ticker, entries in OVERRIDES.items():
            for key, rec in entries.items():
                reason = rec.get("reason", "")
                assert len(reason) >= 30, (
                    f"OVERRIDES[{ticker!r}][{key!r}] reason too short "
                    f"({len(reason)} chars): {reason!r}"
                )


class TestLookups:

    def test_known_ticker_returns_true(self):
        """MDT has a tax_rate_normal_range override (Phase 0 seed)."""
        assert is_override_active("MDT", "tax_rate_normal_range") is True

    def test_unknown_ticker_returns_false(self):
        assert is_override_active("XXX_NOT_IN_REGISTRY", "any_key") is False

    def test_unknown_key_returns_false(self):
        assert is_override_active("MDT", "no_such_override") is False

    def test_get_override_returns_dict(self):
        rec = get_override("MDT", "tax_rate_normal_range")
        assert rec is not None
        assert "reason" in rec
        assert "Irish" in rec["reason"]  # international filer rationale

    def test_get_override_returns_none_when_missing(self):
        assert get_override("XXX", "key") is None


class TestRegistrySize:
    """Design principle: registry stays small. >20 entries is a signal
    the validation rules are too strict, not that the universe has lots
    of edge cases."""

    def test_total_entries_under_threshold(self):
        total = sum(len(v) for v in OVERRIDES.values())
        assert total <= _REGISTRY_SIZE_WARNING_THRESHOLD, (
            f"Registry has {total} entries — exceeds the {_REGISTRY_SIZE_WARNING_THRESHOLD}"
            " threshold. Validation rules are too strict; recalibrate "
            "instead of accumulating more exceptions."
        )

    def test_threshold_is_documented(self):
        """The threshold constant exists and is reasonable."""
        assert 10 <= _REGISTRY_SIZE_WARNING_THRESHOLD <= 50


class TestPastDueReview:
    """Past-due reviews should be visible — entries with review_by_date
    in the past indicate the framework rules haven't been revisited."""

    def test_log_past_due_returns_count(self):
        """The function returns an int count; useful for monitoring."""
        n = log_past_due_overrides()
        assert isinstance(n, int)
        assert n >= 0

    def test_no_entries_past_due_at_landing_time(self):
        """When this test was written, no entries should be past due —
        every entry has a forward-looking review_by_date."""
        today = date.today()
        past_due = []
        for ticker, entries in OVERRIDES.items():
            for key, rec in entries.items():
                review = date.fromisoformat(rec["review_by_date"])
                if review < today:
                    past_due.append((ticker, key, review.isoformat()))
        assert not past_due, (
            f"{len(past_due)} overrides past their review_by_date: {past_due}. "
            "Either revisit the underlying validation rule or refresh the "
            "review_by_date with a new rationale."
        )


class TestSpecificOverrides:
    """Anchor specific overrides from the Phase 0 seed + Phase 6 triage."""

    def test_mdt_tax_rate_override(self):
        """MDT is Irish-domiciled with ~14-16% effective rate."""
        rec = get_override("MDT", "tax_rate_normal_range")
        assert rec is not None
        assert "tax_rate" in (rec.get("fields") or [])

    def test_v_shares_override_short_review(self):
        """V shares ingest bug should have a SHORT review_by to force
        the resolver fix onto the schedule."""
        rec = get_override("V", "shares_diluted_ingest_bug")
        assert rec is not None
        review = date.fromisoformat(rec["review_by_date"])
        # Must be within 12 months — short pressure-valve review
        assert review <= date.today() + timedelta(days=365)

    def test_nee_capex_utility_override(self):
        """NEE uses non-standard utility XBRL tags for CapEx."""
        rec = get_override("NEE", "utility_capex_xbrl")
        assert rec is not None
        assert "capex" in (rec.get("fields") or [])

    def test_low_negative_equity_override(self):
        """LOW has multi-year negative equity from aggressive buybacks."""
        rec = get_override("LOW", "negative_total_equity")
        assert rec is not None
