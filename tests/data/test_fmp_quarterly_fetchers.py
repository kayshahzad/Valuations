"""Phase Q-3: FMP fetchers gain a `period` arg + TTM endpoints.

Pins:

  1. Annual default unchanged — calls remain backward-compatible.
  2. period='quarter' hits the same FMP endpoint with `period=quarter`,
     and writes a separate cache file so annual + quarterly responses
     don't overwrite each other.
  3. TTM endpoints (key_metrics_ttm, ratios_ttm) return a single dict
     (not a list) — they're a snapshot, not a series.
  4. Invalid period values raise ValueError fast — silent typo bugs
     would mean we'd silently fetch the wrong cadence.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aletheia.data import fmp_client


# ── period validation ─────────────────────────────────────────────────

def test_validate_period_accepts_annual():
    assert fmp_client._validate_period("annual") == "annual"


def test_validate_period_accepts_quarter():
    assert fmp_client._validate_period("quarter") == "quarter"


def test_validate_period_rejects_typos():
    with pytest.raises(ValueError, match="must be 'annual' or 'quarter'"):
        fmp_client._validate_period("quarterly")
    with pytest.raises(ValueError):
        fmp_client._validate_period("ttm")
    with pytest.raises(ValueError):
        fmp_client._validate_period("Q1")


# ── annual default unchanged ──────────────────────────────────────────

def test_annual_default_uses_annual_endpoint_label():
    """Calling without `period` arg must hit the same cache label as
    pre-Q-3 callers expect (`income_annual` etc.)."""
    with patch("aletheia.data.fmp_client._fetch") as m:
        m.return_value = []
        fmp_client.fetch_income_statements("AAPL")
    args, kwargs = m.call_args
    assert args[1] == "income-statement"
    assert args[2] == "income_annual"
    assert kwargs["params"]["period"] == "annual"


def test_annual_explicit_matches_default():
    """period='annual' explicit and default produce identical calls."""
    with patch("aletheia.data.fmp_client._fetch") as m:
        m.return_value = []
        fmp_client.fetch_balance_sheets("AAPL", period="annual")
    args, _ = m.call_args
    assert args[2] == "balance_annual"


# ── quarterly fetch routes to separate cache + correct param ─────────

def test_quarterly_uses_distinct_cache_label():
    """period='quarter' must NOT overwrite annual cache."""
    with patch("aletheia.data.fmp_client._fetch") as m:
        m.return_value = []
        fmp_client.fetch_income_statements("AAPL", period="quarter")
    args, kwargs = m.call_args
    assert args[2] == "income_quarter"
    assert kwargs["params"]["period"] == "quarter"


def test_quarterly_works_for_all_six_statement_endpoints():
    """Every fetcher with a period dimension routes correctly."""
    fetchers = [
        (fmp_client.fetch_income_statements,    "income-statement",       "income_quarter"),
        (fmp_client.fetch_balance_sheets,       "balance-sheet-statement", "balance_quarter"),
        (fmp_client.fetch_cash_flows,           "cash-flow-statement",    "cashflow_quarter"),
        (fmp_client.fetch_ratios,               "ratios",                 "ratios_quarter"),
        (fmp_client.fetch_key_metrics,          "key-metrics",            "key_metrics_quarter"),
        (fmp_client.fetch_enterprise_values,    "enterprise-values",      "ev_quarter"),
    ]
    for fn, expected_endpoint, expected_label in fetchers:
        with patch("aletheia.data.fmp_client._fetch") as m:
            m.return_value = []
            fn("AAPL", period="quarter")
        args, _ = m.call_args
        assert args[1] == expected_endpoint, fn.__name__
        assert args[2] == expected_label, fn.__name__


def test_quarterly_invalid_period_raises_at_fetcher_call_site():
    with pytest.raises(ValueError):
        fmp_client.fetch_income_statements("AAPL", period="Q1")


# ── TTM endpoints return a single dict, not a list ───────────────────

def test_fetch_key_metrics_ttm_returns_single_dict():
    sample = [{"revenuePerShareTTM": 6.5, "marketCap": 4_300e9}]
    with patch("aletheia.data.fmp_client._fetch", return_value=sample):
        r = fmp_client.fetch_key_metrics_ttm("AAPL")
    assert isinstance(r, dict)
    assert r["revenuePerShareTTM"] == 6.5


def test_fetch_key_metrics_ttm_handles_dict_response():
    """Some FMP responses come back as a dict directly rather than a
    one-element list — both shapes flatten to the same return."""
    with patch("aletheia.data.fmp_client._fetch",
               return_value={"netIncomeTTM": 99e9}):
        r = fmp_client.fetch_key_metrics_ttm("AAPL")
    assert r == {"netIncomeTTM": 99e9}


def test_fetch_key_metrics_ttm_returns_none_on_empty_response():
    with patch("aletheia.data.fmp_client._fetch", return_value=None):
        r = fmp_client.fetch_key_metrics_ttm("AAPL")
    assert r is None


def test_fetch_ratios_ttm_returns_single_dict():
    sample = [{"peRatioTTM": 28.0, "roicTTM": 0.30}]
    with patch("aletheia.data.fmp_client._fetch", return_value=sample):
        r = fmp_client.fetch_ratios_ttm("AAPL")
    assert isinstance(r, dict)
    assert r["roicTTM"] == 0.30


def test_ttm_endpoints_use_distinct_cache_labels():
    """key_metrics_ttm and ratios_ttm must not collide with annual/quarter."""
    with patch("aletheia.data.fmp_client._fetch") as m:
        m.return_value = []
        fmp_client.fetch_key_metrics_ttm("AAPL")
    assert m.call_args[0][2] == "key_metrics_ttm"

    with patch("aletheia.data.fmp_client._fetch") as m:
        m.return_value = []
        fmp_client.fetch_ratios_ttm("AAPL")
    assert m.call_args[0][2] == "ratios_ttm"
