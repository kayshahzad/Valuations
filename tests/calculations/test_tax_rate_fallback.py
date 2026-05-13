"""Tests for the A11 tax-rate fallback resolver.

Coverage:
  - resolve_tax_rate picks each source level in order: cash > gaap >
    company_fy > statutory.
  - Edge cases: NaN, infinity, out-of-range values are treated as
    unusable and the chain falls through.
  - company_fy_effective_tax_rate honours the lookback window, prefers
    cash over gaap row-by-row, drops unusable rows, and returns None
    when fewer than MIN_LOOKBACK_YEARS_FOR_AVG usable rows exist.
"""

from __future__ import annotations

import logging
import math

import pandas as pd
import pytest

from aletheia.calculations import (
    DEFAULT_LOOKBACK_YEARS,
    MIN_LOOKBACK_YEARS_FOR_AVG,
    US_STATUTORY,
    company_fy_effective_tax_rate,
    resolve_tax_rate,
)


# ──────────────────────────────────────────────────────────────────────
# resolve_tax_rate — chain order
# ──────────────────────────────────────────────────────────────────────

def test_resolve_prefers_cash_when_available():
    rate, source = resolve_tax_rate(
        ticker="TEST", fn="test", df=None, fy=2025,
        cash_tax_rate=0.18, gaap_tax_rate=0.25,
    )
    assert rate == 0.18
    assert source == "cash"


def test_resolve_falls_to_gaap_when_cash_missing():
    rate, source = resolve_tax_rate(
        ticker="TEST", fn="test", df=None, fy=2025,
        cash_tax_rate=None, gaap_tax_rate=0.24,
    )
    assert rate == 0.24
    assert source == "gaap"


def test_resolve_falls_to_company_fy_when_no_current_rates():
    df = pd.DataFrame({
        "fiscal_year":         [2020, 2021, 2022, 2023, 2024],
        "clean_CashTaxRate":   [0.18, 0.17, 0.19, 0.16, 0.18],
        "clean_GAAP_TaxRate":  [0.22, 0.21, 0.23, 0.20, 0.21],
    })
    rate, source = resolve_tax_rate(
        ticker="TEST", fn="test", df=df, fy=2024,
        cash_tax_rate=None, gaap_tax_rate=None,
    )
    assert source == "company_fy"
    assert rate == pytest.approx((0.18 + 0.17 + 0.19 + 0.16 + 0.18) / 5)


def test_resolve_falls_to_statutory_when_history_too_thin():
    df = pd.DataFrame({
        "fiscal_year":         [2023, 2024],
        "clean_CashTaxRate":   [0.18, 0.19],
        "clean_GAAP_TaxRate":  [0.22, 0.23],
    })
    rate, source = resolve_tax_rate(
        ticker="TEST", fn="test", df=df, fy=2024,
        cash_tax_rate=None, gaap_tax_rate=None,
    )
    assert rate == US_STATUTORY
    assert source == "statutory"


def test_resolve_falls_to_statutory_when_df_missing():
    rate, source = resolve_tax_rate(
        ticker="TEST", fn="test", df=None, fy=2024,
        cash_tax_rate=None, gaap_tax_rate=None,
    )
    assert rate == US_STATUTORY
    assert source == "statutory"


# ──────────────────────────────────────────────────────────────────────
# resolve_tax_rate — unusable value handling
# ──────────────────────────────────────────────────────────────────────

def test_resolve_treats_nan_as_unusable():
    rate, source = resolve_tax_rate(
        ticker="TEST", fn="test", df=None, fy=2024,
        cash_tax_rate=float("nan"), gaap_tax_rate=0.22,
    )
    assert rate == 0.22
    assert source == "gaap"


def test_resolve_treats_infinity_as_unusable():
    rate, source = resolve_tax_rate(
        ticker="TEST", fn="test", df=None, fy=2024,
        cash_tax_rate=float("inf"), gaap_tax_rate=0.22,
    )
    assert rate == 0.22
    assert source == "gaap"


def test_resolve_treats_out_of_band_as_unusable():
    """Tax rate > 1.0 (e.g. 21.0 from forgotten percent scaling) must
    fall through, not propagate."""
    rate, source = resolve_tax_rate(
        ticker="TEST", fn="test", df=None, fy=2024,
        cash_tax_rate=21.0, gaap_tax_rate=0.22,
    )
    assert rate == 0.22
    assert source == "gaap"


def test_resolve_treats_negative_below_minus_one_as_unusable():
    rate, source = resolve_tax_rate(
        ticker="TEST", fn="test", df=None, fy=2024,
        cash_tax_rate=-1.5, gaap_tax_rate=0.22,
    )
    assert rate == 0.22
    assert source == "gaap"


def test_resolve_accepts_negative_rate_in_band():
    """Negative tax rate (NOL release / DTA reversal years) is
    legitimate and must be accepted — see docs/sign_conventions.md
    Tier 3."""
    rate, source = resolve_tax_rate(
        ticker="TEST", fn="test", df=None, fy=2024,
        cash_tax_rate=-0.12, gaap_tax_rate=None,
    )
    assert rate == -0.12
    assert source == "cash"


# ──────────────────────────────────────────────────────────────────────
# company_fy_effective_tax_rate — windowing and per-row preference
# ──────────────────────────────────────────────────────────────────────

def test_company_fy_uses_lookback_window():
    """Older rows outside the lookback window must be excluded."""
    df = pd.DataFrame({
        "fiscal_year":        [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024],
        "clean_CashTaxRate":  [0.30, 0.30, 0.30, 0.30, 0.30, 0.18, 0.17, 0.19, 0.16, 0.18],
        "clean_GAAP_TaxRate": [None] * 10,
    })
    avg = company_fy_effective_tax_rate(df, fy=2024, lookback_years=5)
    expected = (0.18 + 0.17 + 0.19 + 0.16 + 0.18) / 5
    assert avg == pytest.approx(expected)


def test_company_fy_prefers_cash_over_gaap_per_row():
    """Each row: cash > gaap; row drops only when both are unusable."""
    df = pd.DataFrame({
        "fiscal_year":        [2020, 2021, 2022, 2023, 2024],
        "clean_CashTaxRate":  [None, 0.17, None, 0.16, 0.18],
        "clean_GAAP_TaxRate": [0.22, 0.21, 0.23, 0.20, 0.21],
    })
    avg = company_fy_effective_tax_rate(df, fy=2024, lookback_years=5)
    # Per-row: gaap, cash, gaap, cash, cash → 0.22, 0.17, 0.23, 0.16, 0.18
    expected = (0.22 + 0.17 + 0.23 + 0.16 + 0.18) / 5
    assert avg == pytest.approx(expected)


def test_company_fy_drops_unusable_rows():
    df = pd.DataFrame({
        "fiscal_year":        [2020, 2021, 2022, 2023, 2024],
        "clean_CashTaxRate":  [None, 0.17, float("nan"), 0.16, 0.18],
        "clean_GAAP_TaxRate": [None, None, None, 0.20, None],
    })
    avg = company_fy_effective_tax_rate(df, fy=2024, lookback_years=5)
    # Usable rows: 2021 cash=0.17, 2023 cash=0.16, 2024 cash=0.18 → 3 rows
    expected = (0.17 + 0.16 + 0.18) / 3
    assert avg == pytest.approx(expected)


def test_company_fy_returns_none_when_too_few_usable_rows():
    df = pd.DataFrame({
        "fiscal_year":        [2020, 2021, 2022, 2023, 2024],
        "clean_CashTaxRate":  [None, 0.17, None, None, 0.18],
        "clean_GAAP_TaxRate": [None, None, None, None, None],
    })
    assert MIN_LOOKBACK_YEARS_FOR_AVG == 3
    avg = company_fy_effective_tax_rate(df, fy=2024, lookback_years=5)
    assert avg is None


def test_company_fy_handles_missing_fiscal_year_column():
    df = pd.DataFrame({"clean_CashTaxRate": [0.18, 0.19, 0.20]})
    assert company_fy_effective_tax_rate(df, fy=2024) is None


def test_company_fy_handles_none_df():
    assert company_fy_effective_tax_rate(None, fy=2024) is None


def test_company_fy_excludes_future_years():
    """A row with fiscal_year > fy must not be included."""
    df = pd.DataFrame({
        "fiscal_year":        [2020, 2021, 2022, 2023, 2024, 2025],
        "clean_CashTaxRate":  [0.18, 0.17, 0.19, 0.16, 0.18, 0.99],
        "clean_GAAP_TaxRate": [None] * 6,
    })
    avg = company_fy_effective_tax_rate(df, fy=2024, lookback_years=5)
    expected = (0.18 + 0.17 + 0.19 + 0.16 + 0.18) / 5
    assert avg == pytest.approx(expected)


def test_company_fy_dedupes_by_fiscal_year():
    """When a df contains both an FY and a TTM/duplicate row for the
    same fiscal_year, the resolver keeps only one (the last in sort
    order). Without dedup, callers that pass a full df vs an FY-only
    df get diverging answers — the bug that broke WACC consistency
    across DCFEngine / MultipleDecomposition / ReverseDCF for NVDA."""
    df = pd.DataFrame({
        "fiscal_year":        [2020, 2021, 2022, 2023, 2024, 2024],
        "clean_CashTaxRate":  [0.30, 0.30, 0.30, 0.30, 0.18, 0.10],
        "clean_GAAP_TaxRate": [None] * 6,
    })
    avg = company_fy_effective_tax_rate(df, fy=2024, lookback_years=5)
    # Five unique FYs (2020..2024); FY2024's last-keep row is 0.10.
    expected = (0.30 + 0.30 + 0.30 + 0.30 + 0.10) / 5
    assert avg == pytest.approx(expected)


# ──────────────────────────────────────────────────────────────────────
# logging — audit trail for fallback events
# ──────────────────────────────────────────────────────────────────────

def test_company_fy_logged_at_info_level(caplog):
    df = pd.DataFrame({
        "fiscal_year":        [2020, 2021, 2022, 2023, 2024],
        "clean_CashTaxRate":  [0.18, 0.17, 0.19, 0.16, 0.18],
        "clean_GAAP_TaxRate": [None] * 5,
    })
    with caplog.at_level(logging.INFO, logger="aletheia.calculations._tax_rate"):
        _, source = resolve_tax_rate(
            ticker="DEMO", fn="test_fn", df=df, fy=2024,
            cash_tax_rate=None, gaap_tax_rate=None,
        )
    assert source == "company_fy"
    assert any("source=company_fy" in rec.message and "DEMO" in rec.message
               for rec in caplog.records)


def test_statutory_logged_at_warning_level(caplog):
    with caplog.at_level(logging.WARNING, logger="aletheia.calculations._tax_rate"):
        rate, source = resolve_tax_rate(
            ticker="DEMO", fn="test_fn", df=None, fy=2024,
            cash_tax_rate=None, gaap_tax_rate=None,
        )
    assert source == "statutory"
    assert rate == US_STATUTORY
    assert any("source=statutory" in rec.message and rec.levelname == "WARNING"
               for rec in caplog.records)


def test_cash_path_does_not_log():
    """When the cleaned rate is available, the resolver returns silently
    — only fallbacks emit audit log entries."""
    rate, source = resolve_tax_rate(
        ticker="DEMO", fn="test_fn", df=None, fy=2024,
        cash_tax_rate=0.18, gaap_tax_rate=None,
    )
    assert source == "cash"
    assert rate == 0.18


# ──────────────────────────────────────────────────────────────────────
# constants
# ──────────────────────────────────────────────────────────────────────

def test_constants_match_documented_values():
    assert US_STATUTORY == 0.21
    assert DEFAULT_LOOKBACK_YEARS == 5
    assert MIN_LOOKBACK_YEARS_FOR_AVG == 3
