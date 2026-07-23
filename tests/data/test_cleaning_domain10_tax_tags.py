"""Regression test for cleaning_engine domain-10 tax-rate resolution.

Background
----------
A11's tax-rate fallback chain (cash → gaap → company_fy → statutory)
is implemented in ``aletheia.calculations._tax_rate.resolve_tax_rate``.
The CLEANED step of that chain reads ``clean_CashTaxRate`` and
``clean_GAAP_TaxRate`` from the persisted CleanedRecord — both
populated by ``CleaningEngine._domain10_tax_sustainability``.

For months the cleaning engine looked up XBRL tag names
(``IncomeTaxExpenseBenefit`` / the long ``IncomeLossFromContinuing…``
name) that the canonical_transformer + tag_resolver had already
renamed to ``TaxExpense`` / ``PretaxIncome``. Result: domain-10
silently produced None for every ticker; A11 fell all the way through
to statutory 0.21 on every single probe.

This test pins the field-name mapping so the same silent breakage
can't recur. It runs domain-10 against a synthetic CleanedRecord
with the resolved tag names and asserts the cleaned rates are
computed and inside their expected bands.
"""

from __future__ import annotations

import pytest

from aletheia.data.cleaning_engine import CleaningEngine, CleanedRecord


# Synthetic NVDA-shaped record. Numbers from NVDA FY2026 actuals so
# the resulting rates land in NVDA's plausible band.
NVDA_FY2026_TAX_EXPENSE = 21_383_000_000.0
NVDA_FY2026_PRETAX_INCOME = 141_450_000_000.0
NVDA_FY2026_CASH_TAXES_PAID = 20_288_000_000.0


def _build_record(*, raw: dict) -> CleanedRecord:
    """Skinny CleanedRecord shaped for domain-10's inputs only."""
    rec = CleanedRecord(
        ticker="TEST", fiscal_year=2026,
        period_end_date="2026-01-26",
    )
    rec.raw = dict(raw)
    rec.clean = dict(raw)  # cleaning engine starts clean from raw
    return rec


def test_domain10_reads_resolved_tax_tags():
    """The fix path: tag_resolver outputs ``TaxExpense`` /
    ``PretaxIncome`` — domain-10 must read those and populate
    cleaned rates."""
    rec = _build_record(raw={
        "TaxExpense": NVDA_FY2026_TAX_EXPENSE,
        "PretaxIncome": NVDA_FY2026_PRETAX_INCOME,
        "CashTaxesPaid": NVDA_FY2026_CASH_TAXES_PAID,
        # NormalizedEBIT needed downstream of domain-10 to compute NOPAT;
        # set so the NOPAT update path also fires.
        "NormalizedEBIT": 130_387_000_000.0,
    })
    rec.clean["NormalizedEBIT"] = 130_387_000_000.0

    engine = CleaningEngine(verbose=False)
    engine._domain10_tax_sustainability(rec, prior=None)

    assert "GAAP_TaxRate" in rec.clean, (
        "domain-10 didn't populate GAAP_TaxRate from resolved tag — "
        "the silent-breakage regression"
    )
    assert "CashTaxRate" in rec.clean, (
        "domain-10 didn't populate CashTaxRate from resolved tag"
    )

    gaap = rec.clean["GAAP_TaxRate"]
    cash = rec.clean["CashTaxRate"]
    expected_gaap = NVDA_FY2026_TAX_EXPENSE / NVDA_FY2026_PRETAX_INCOME
    expected_cash = NVDA_FY2026_CASH_TAXES_PAID / NVDA_FY2026_PRETAX_INCOME

    assert gaap == pytest.approx(expected_gaap, rel=1e-9), (
        f"GAAP tax rate computed wrong: got {gaap}, expected {expected_gaap}"
    )
    assert cash == pytest.approx(expected_cash, rel=1e-9), (
        f"Cash tax rate computed wrong: got {cash}, expected {expected_cash}"
    )
    # Sanity: both rates inside NVDA's documented band.
    assert 0.10 <= gaap <= 0.20
    assert 0.10 <= cash <= 0.20


def test_domain10_missing_cash_tax_falls_to_gaap_not_zero():
    """F2 (Phase 1): a UNP-class filer whose cash-tax tags are ALL absent must
    NOT fabricate a 0% (untaxed) NOPAT. Cash rate stays None; NOPAT falls to the
    GAAP rate instead of leaving EBIT untaxed."""
    ebit = 9_850_000_000.0
    rec = _build_record(raw={
        "TaxExpense": 2_028_000_000.0,       # UNP FY-actual
        "PretaxIncome": 9_166_000_000.0,
        # cash-tax tags deliberately absent (the UNP case)
        "NormalizedEBIT": ebit,
    })
    rec.clean["NormalizedEBIT"] = ebit
    engine = CleaningEngine(verbose=False)
    engine._domain10_tax_sustainability(rec, prior=None)

    # Missing cash-tax tags -> no fabricated 0% cash rate.
    assert rec.clean.get("CashTaxRate") is None
    gaap = rec.clean["GAAP_TaxRate"]
    assert gaap == pytest.approx(2_028_000_000.0 / 9_166_000_000.0, rel=1e-9)
    # NOPAT is taxed at the GAAP rate — NOT the fabricated untaxed EBIT.
    assert rec.clean["NOPAT"] == pytest.approx(ebit * (1 - gaap), rel=1e-9)
    assert rec.clean["NOPAT"] < ebit


def test_domain10_falls_back_to_xbrl_raw_tag_names():
    """Older ingests / ADR-filer paths may bypass the canonical
    rename and present record.raw with the raw XBRL tag names.
    Domain-10 must still resolve them as a fallback."""
    rec = _build_record(raw={
        "IncomeTaxExpenseBenefit": NVDA_FY2026_TAX_EXPENSE,
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest":
            NVDA_FY2026_PRETAX_INCOME,
        "CashTaxesPaid": NVDA_FY2026_CASH_TAXES_PAID,
    })
    engine = CleaningEngine(verbose=False)
    engine._domain10_tax_sustainability(rec, prior=None)

    assert "GAAP_TaxRate" in rec.clean
    assert "CashTaxRate" in rec.clean


def test_domain10_resolved_tag_takes_precedence_over_xbrl_raw():
    """If both naming variants are present (transient post-refactor
    state), the resolved tag (``TaxExpense``) takes precedence —
    that's the canonical post-cleaning name."""
    rec = _build_record(raw={
        "TaxExpense": NVDA_FY2026_TAX_EXPENSE,
        "PretaxIncome": NVDA_FY2026_PRETAX_INCOME,
        "CashTaxesPaid": NVDA_FY2026_CASH_TAXES_PAID,
        # Decoy values under XBRL-raw names — must NOT be used.
        "IncomeTaxExpenseBenefit": 999_999_999_999.0,
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 1.0,
    })
    engine = CleaningEngine(verbose=False)
    engine._domain10_tax_sustainability(rec, prior=None)

    expected_gaap = NVDA_FY2026_TAX_EXPENSE / NVDA_FY2026_PRETAX_INCOME
    assert rec.clean["GAAP_TaxRate"] == pytest.approx(expected_gaap, rel=1e-9), (
        "decoy XBRL-raw value took precedence — the rename hasn't "
        "settled and the bug can recur"
    )


def test_domain10_returns_none_when_pretax_income_zero():
    """Zero pretax income → can't compute a rate. Domain-10 must
    not divide-by-zero and must not produce a fake rate."""
    rec = _build_record(raw={
        "TaxExpense": NVDA_FY2026_TAX_EXPENSE,
        "PretaxIncome": 0.0,
        "CashTaxesPaid": NVDA_FY2026_CASH_TAXES_PAID,
    })
    engine = CleaningEngine(verbose=False)
    engine._domain10_tax_sustainability(rec, prior=None)

    assert rec.clean.get("GAAP_TaxRate") is None
    assert rec.clean.get("CashTaxRate") is None
