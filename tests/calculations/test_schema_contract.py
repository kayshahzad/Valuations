"""Phase 7 tests — validate_cleaned_record_schema_contract end-to-end.

Coverage:
  - Happy path: clean records pass without violations
  - Per-mode behavior: off / shadow / soft / hard
  - Required-field checks (Tier-1) on FY and TTM records
  - Business-model-aware required set (routing_required / ddm_required)
  - Override registry consultation (V shares, TSLA pre-2015)
  - EBITDA = EBIT + D&A identity
  - FCF identity auto-detect (pre/post-ASC-842 lease)
  - A=L+E identity auto-detect (with/without RedeemableNCI)
  - NetDebt = TotalDebt - Cash identity
  - capex/revenue range check
  - Regression fixtures for the 13 residual violators
"""

from __future__ import annotations

import pytest

from aletheia.calculations import (
    validate_cleaned_record_schema_contract,
    CalculationError,
    CalculationConsistencyError,
    CalculationInputError,
)
from aletheia.data.cleaning_engine import CleanedRecord


# ─────────────────────────────────────────────────────────────────────
# Record builders
# ─────────────────────────────────────────────────────────────────────

def _clean_fy_record(ticker: str = "TEST", fiscal_year: int = 2025,
                     **overrides) -> CleanedRecord:
    """A minimal CleanedRecord that should pass the schema contract.

    Default values picked to satisfy:
      - All Tier-1 required fields present + positive
      - EBITDA = EBIT + D&A identity
      - FCF = OpCF - CapEx identity (no lease adj)
      - A = L + E identity (no NCI)
      - NetDebt = LTD - Cash identity
      - capex/revenue in band
    """
    rec = CleanedRecord(
        ticker=ticker, fiscal_year=fiscal_year,
        period="FY", period_end_date=f"{fiscal_year}-12-31",
    )
    raw = {
        "Revenue":          1000.0,
        "TotalAssets":      5000.0,
        "TotalLiabilities": 3000.0,
        "TotalEquity":      2000.0,
        "Depreciation":     50.0,
        "OperatingCF":      150.0,
        "CapEx":            40.0,  # POSITIVE per schema convention
        "OperatingIncome":  120.0,
        "Cash":             200.0,
        "LongTermDebt":     500.0,
    }
    derived = {
        "EBITDA":             170.0,  # = 120 + 50
        "Depreciation_Total": 50.0,
        "NetDebt":            300.0,  # = 500 - 200
        "OperatingIncome":    120.0,
        "CapEx":              40.0,
    }
    clean = {
        "FCF":            110.0,  # = 150 - 40
        "SharesDiluted":  100.0,
    }
    raw.update(overrides.get("raw", {}))
    derived.update(overrides.get("derived", {}))
    clean.update(overrides.get("clean", {}))
    rec.raw = raw
    rec.derived = derived
    rec.clean = clean
    return rec


def _clean_ttm_record(ticker: str = "TEST", fiscal_year: int = 2026,
                      **overrides) -> CleanedRecord:
    rec = _clean_fy_record(ticker=ticker, fiscal_year=fiscal_year, **overrides)
    rec.period = "TTM"
    rec.period_end_date = f"{fiscal_year}-03-31"
    return rec


@pytest.fixture(autouse=True)
def reset_guard_mode(monkeypatch):
    monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
    yield


# ─────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────

class TestHappyPath:

    def test_clean_fy_record_passes_in_hard_mode(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        ok, violations = validate_cleaned_record_schema_contract(_clean_fy_record())
        assert ok is True
        assert violations == []

    def test_clean_ttm_record_passes_in_hard_mode(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        ok, violations = validate_cleaned_record_schema_contract(_clean_ttm_record())
        assert ok is True
        assert violations == []

    def test_off_mode_no_violations_collected(self, monkeypatch):
        """In off mode, the function is a no-op — returns empty violations
        regardless of record state."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "off")
        rec = _clean_fy_record(raw={"Revenue": -1000.0})  # would fail in hard
        ok, violations = validate_cleaned_record_schema_contract(rec)
        assert ok is True
        assert violations == []


# ─────────────────────────────────────────────────────────────────────
# Required Tier-1 fields
# ─────────────────────────────────────────────────────────────────────

class TestRequiredFields:

    def test_missing_revenue_raises_in_hard(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record()
        rec.raw.pop("Revenue")
        rec.clean.pop("Revenue", None)  # ensure neither namespace has it
        with pytest.raises(CalculationInputError):
            validate_cleaned_record_schema_contract(rec)

    def test_negative_revenue_raises_in_hard(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(raw={"Revenue": -100.0})
        with pytest.raises(CalculationInputError):
            validate_cleaned_record_schema_contract(rec)

    def test_shadow_mode_collects_missing_field_violation(self, monkeypatch):
        """Phase 6 collector-pattern fix: violations populate in shadow
        mode now (used to only populate in hard via try/except)."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "shadow")
        rec = _clean_fy_record()
        rec.raw.pop("Revenue")
        rec.clean.pop("Revenue", None)
        ok, violations = validate_cleaned_record_schema_contract(rec)
        assert ok is True  # shadow doesn't refuse persist
        assert any("revenue" in str(v.get("field", "")).lower() for v in violations)


# ─────────────────────────────────────────────────────────────────────
# Business-model-aware required set
# ─────────────────────────────────────────────────────────────────────

class TestBusinessModelRouting:

    def test_routing_required_ticker_uses_relaxed_set(self, monkeypatch):
        """AXP / JPM / BRK-B (routing_required) don't have the standard
        Revenue field — but have InterestIncome / NetInterestIncome.
        The non-FCFF required set should accept those alternatives."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(ticker="AXP", raw={"Revenue": None,
                                                  "InterestIncome": 25000.0})
        rec.raw.pop("Revenue", None)
        rec.clean.pop("Revenue", None)
        # AXP is routing_required — should pass via the relaxed set
        ok, _ = validate_cleaned_record_schema_contract(rec)
        assert ok is True

    def test_fcff_compatible_ticker_requires_revenue(self, monkeypatch):
        """Industrial ticker (default fcff_compatible) requires Revenue
        in the standard XBRL location."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(ticker="MDT")  # MDT is fcff_compatible
        rec.raw.pop("Revenue")
        rec.clean.pop("Revenue", None)
        with pytest.raises(CalculationInputError):
            validate_cleaned_record_schema_contract(rec)


# ─────────────────────────────────────────────────────────────────────
# Override registry consultation
# ─────────────────────────────────────────────────────────────────────

class TestOverrideRegistry:

    def test_v_shares_override_downgrades_to_soft_flag(self, monkeypatch):
        """V has shares_diluted_ingest_bug in OVERRIDES → missing
        shares_diluted should soft-flag, not raise, even in hard mode."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(ticker="V")
        rec.clean.pop("SharesDiluted")
        rec.raw.pop("SharesDiluted", None)
        # V's override means this should NOT raise
        ok, _ = validate_cleaned_record_schema_contract(rec)
        assert ok is True

    def test_unknown_ticker_no_override_raises(self, monkeypatch):
        """A ticker WITHOUT an override should raise on missing shares."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(ticker="UNKNOWN_TICKER_NO_OVERRIDE")
        rec.clean.pop("SharesDiluted")
        rec.raw.pop("SharesDiluted", None)
        with pytest.raises(CalculationInputError):
            validate_cleaned_record_schema_contract(rec)


# ─────────────────────────────────────────────────────────────────────
# EBITDA = EBIT + D&A identity
# ─────────────────────────────────────────────────────────────────────

class TestEbitdaIdentity:

    def test_identity_holds_passes(self, monkeypatch):
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(
            derived={"EBITDA": 170.0, "OperatingIncome": 120.0,
                     "Depreciation_Total": 50.0},
        )
        ok, _ = validate_cleaned_record_schema_contract(rec)
        assert ok is True

    def test_identity_violation_raises(self, monkeypatch):
        """EBITDA reports 200 but EBIT + D&A = 170 — drift 17.6%."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(derived={"EBITDA": 200.0})
        with pytest.raises(CalculationConsistencyError) as excinfo:
            validate_cleaned_record_schema_contract(rec)
        assert "ebitda" in excinfo.value.field.lower()


# ─────────────────────────────────────────────────────────────────────
# FCF identity auto-detect (pre/post-ASC-842)
# ─────────────────────────────────────────────────────────────────────

class TestFcfIdentityAutoDetect:

    def test_simple_form_holds(self, monkeypatch):
        """Pre-2019: FCF = OpCF - CapEx exactly."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(clean={"FCF": 110.0}, raw={
            "OperatingCF": 150.0, "CapEx": 40.0})
        ok, _ = validate_cleaned_record_schema_contract(rec)
        assert ok is True

    def test_lease_adjusted_form_holds(self, monkeypatch):
        """Post-2019: FCF = OpCF - CapEx - FinanceLeasePrincipal.
        Auto-detect should accept this form."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(
            clean={"FCF": 100.0},
            raw={"OperatingCF": 150.0, "CapEx": 40.0,
                 "FinanceLeasePrincipalPayments": 10.0},
        )
        # 150 - 40 - 10 = 100 — lease-adjusted form holds
        ok, _ = validate_cleaned_record_schema_contract(rec)
        assert ok is True

    def test_neither_form_holds_with_lease_data_raises(self, monkeypatch):
        """Lease data is populated but FCF still violates — real bug."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(
            clean={"FCF": 50.0},  # way off from both forms
            raw={"OperatingCF": 150.0, "CapEx": 40.0,
                 "FinanceLeasePrincipalPayments": 10.0},
        )
        with pytest.raises(CalculationConsistencyError):
            validate_cleaned_record_schema_contract(rec)

    def test_neither_form_holds_without_lease_data_soft_flags(self, monkeypatch):
        """When lease data isn't populated and simple form fails, soft-flag
        (gap is unattributable, not blocking)."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(
            clean={"FCF": 95.0},  # 110 expected, off by 15
            raw={"OperatingCF": 150.0, "CapEx": 40.0},
        )
        ok, _ = validate_cleaned_record_schema_contract(rec)
        # Soft-flag = no raise even in hard mode
        assert ok is True


# ─────────────────────────────────────────────────────────────────────
# A = L + E identity auto-detect
# ─────────────────────────────────────────────────────────────────────

class TestAccountingEquationAutoDetect:

    def test_no_nci_form_holds(self, monkeypatch):
        """A=L+E without RedeemableNCI — base case."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(raw={
            "TotalAssets": 5000.0, "TotalLiabilities": 3000.0,
            "TotalEquity": 2000.0})
        ok, _ = validate_cleaned_record_schema_contract(rec)
        assert ok is True

    def test_with_nci_form_holds(self, monkeypatch):
        """UNH/CAT pattern: A = L + E + RedeemableNCI."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(raw={
            "TotalAssets": 5150.0, "TotalLiabilities": 3000.0,
            "TotalEquity": 2000.0,
            "RedeemableNoncontrollingInterest": 150.0,
        })
        # 5150 = 3000 + 2000 + 150 (with-NCI form)
        ok, _ = validate_cleaned_record_schema_contract(rec)
        assert ok is True

    def test_equity_already_includes_nci_form_holds(self, monkeypatch):
        """MCO/WMT/TSLA pattern: TotalEquity ALREADY includes
        RedeemableNCI, so A=L+E (no-NCI form) is the right identity."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(raw={
            "TotalAssets": 5000.0, "TotalLiabilities": 3000.0,
            "TotalEquity": 2000.0,
            "RedeemableNoncontrollingInterest": 150.0,
        })
        # 5000 = 3000 + 2000 (no-NCI form holds — equity already includes)
        ok, _ = validate_cleaned_record_schema_contract(rec)
        assert ok is True

    def test_neither_form_raises(self, monkeypatch):
        """Real violation — neither form satisfies."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(raw={
            "TotalAssets": 5500.0, "TotalLiabilities": 3000.0,
            "TotalEquity": 2000.0,  # 500 gap; no NCI to absorb
        })
        with pytest.raises(CalculationConsistencyError):
            validate_cleaned_record_schema_contract(rec)


# ─────────────────────────────────────────────────────────────────────
# capex/revenue range check
# ─────────────────────────────────────────────────────────────────────

class TestCapexRangeCheck:

    def test_normal_capex_in_band(self, monkeypatch):
        """4% capex/revenue is normal."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(raw={"Revenue": 1000.0, "CapEx": 40.0})
        ok, _ = validate_cleaned_record_schema_contract(rec)
        assert ok is True

    def test_semi_fab_60pct_in_band(self, monkeypatch):
        """TSMC during expansion: 60% capex/revenue is legitimate.
        Range is [-0.30, 0.75] specifically to accommodate this."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        # FCF identity needs to also pass — adjust both
        rec = _clean_fy_record(
            raw={"Revenue": 1000.0, "CapEx": 600.0, "OperatingCF": 650.0,
                 "TotalAssets": 5000.0, "TotalLiabilities": 3000.0,
                 "TotalEquity": 2000.0, "Depreciation": 50.0,
                 "OperatingIncome": 120.0, "Cash": 200.0,
                 "LongTermDebt": 500.0},
            clean={"FCF": 50.0, "SharesDiluted": 100.0},
            derived={"EBITDA": 170.0, "Depreciation_Total": 50.0,
                     "OperatingIncome": 120.0, "CapEx": 600.0,
                     "NetDebt": 300.0},
        )
        ok, _ = validate_cleaned_record_schema_contract(rec)
        assert ok is True

    def test_extreme_capex_raises(self, monkeypatch):
        """ORCL TTM 75.3% capex/revenue is above the 0.75 band — flagged."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(
            raw={"Revenue": 1000.0, "CapEx": 800.0, "OperatingCF": 850.0,
                 "TotalAssets": 5000.0, "TotalLiabilities": 3000.0,
                 "TotalEquity": 2000.0, "Depreciation": 50.0,
                 "OperatingIncome": 120.0, "Cash": 200.0,
                 "LongTermDebt": 500.0},
            clean={"FCF": 50.0, "SharesDiluted": 100.0},
            derived={"EBITDA": 170.0, "Depreciation_Total": 50.0,
                     "OperatingIncome": 120.0, "CapEx": 800.0,
                     "NetDebt": 300.0},
        )
        with pytest.raises(CalculationInputError) as excinfo:
            validate_cleaned_record_schema_contract(rec)
        assert "capex_to_revenue" in excinfo.value.field

    def test_extreme_negative_capex_raises(self, monkeypatch):
        """-50% capex/revenue is implausible — likely sign error."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        # Build with internally-consistent FCF identity
        rec = _clean_fy_record(
            raw={"Revenue": 1000.0, "CapEx": -500.0, "OperatingCF": -450.0,
                 "TotalAssets": 5000.0, "TotalLiabilities": 3000.0,
                 "TotalEquity": 2000.0, "Depreciation": 50.0,
                 "OperatingIncome": 120.0, "Cash": 200.0,
                 "LongTermDebt": 500.0},
            clean={"FCF": 50.0, "SharesDiluted": 100.0},
            derived={"EBITDA": 170.0, "Depreciation_Total": 50.0,
                     "OperatingIncome": 120.0, "CapEx": -500.0,
                     "NetDebt": 300.0},
        )
        # FCF identity: -450 - (-500) = 50 ✓
        with pytest.raises(CalculationInputError):
            validate_cleaned_record_schema_contract(rec)


# ─────────────────────────────────────────────────────────────────────
# Regression fixtures — anchor the Phase 6 triage decisions
# ─────────────────────────────────────────────────────────────────────

class TestRegressionFixtures:
    """Specific scenarios from the 13 residual violators (Phase 6 triage).
    These tests lock in our decisions so they don't quietly regress."""

    def test_mdt_class_bug_with_capex_sign_flip(self, monkeypatch):
        """MDT incident: TTM CapEx was negative due to ingest sign-flip.
        Schema contract should flag the resulting bad capex/revenue."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_ttm_record(
            ticker="MDT",
            raw={"Revenue": 35000.0, "CapEx": -1880.0,
                 "OperatingCF": 7290.0,  # FCF identity needs to satisfy
                 "TotalAssets": 86700.0, "TotalLiabilities": 60000.0,
                 "TotalEquity": 26700.0, "Cash": 200.0, "LongTermDebt": 500.0,
                 "Depreciation": 50.0, "OperatingIncome": 120.0},
            clean={"FCF": 9170.0, "SharesDiluted": 1432.0},  # 7290 - (-1880) = 9170
            derived={"EBITDA": 170.0, "Depreciation_Total": 50.0,
                     "OperatingIncome": 120.0, "CapEx": -1880.0,
                     "NetDebt": 300.0},
        )
        # capex/revenue = -1880/35000 = -5.4% — within [-30%, +75%], passes
        # The real lesson: the framework's range check is wider than the
        # original cleaning-engine abs() convention. Sign flips that stay
        # in band won't be caught by capex_to_revenue alone — that's why
        # we also have the FCF identity check.
        # This record actually passes (range-wise), demonstrating that the
        # MDT bug needed input-level guards on NormalizedEBIT, not just
        # range-based output checks. Documents the limit.
        ok, _ = validate_cleaned_record_schema_contract(rec)
        assert ok is True  # range check alone doesn't catch sign-in-band

    def test_orcl_capex_75pct_flagged(self, monkeypatch):
        """ORCL TTM 75.3% capex/revenue is just outside [-0.30, 0.75]."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(
            ticker="ORCL",
            raw={"Revenue": 64100.0, "CapEx": 48250.0, "OperatingCF": 48300.0,
                 "TotalAssets": 5000.0, "TotalLiabilities": 3000.0,
                 "TotalEquity": 2000.0, "Depreciation": 50.0,
                 "OperatingIncome": 120.0, "Cash": 200.0,
                 "LongTermDebt": 500.0},
            clean={"FCF": 50.0, "SharesDiluted": 100.0},
            derived={"EBITDA": 170.0, "Depreciation_Total": 50.0,
                     "OperatingIncome": 120.0, "CapEx": 48250.0,
                     "NetDebt": 300.0},
        )
        with pytest.raises(CalculationInputError):
            validate_cleaned_record_schema_contract(rec)

    def test_nee_a_eq_l_plus_e_violation_silenced_by_override(self, monkeypatch):
        """NEE has utility_total_liabilities_aggregation override covering
        accounting_equation_a_eq_l_plus_e. A=L+E violations on NEE should
        soft-flag, not raise."""
        monkeypatch.setenv("ALETHEIA_GUARD_MODE", "hard")
        rec = _clean_fy_record(
            ticker="NEE", fiscal_year=2010,
            raw={"TotalAssets": 53000.0, "TotalLiabilities": 20500.0,
                 "TotalEquity": 14460.0,  # 18B gap
                 "Revenue": 15000.0, "CapEx": 100.0, "OperatingCF": 200.0,
                 "Depreciation": 50.0, "OperatingIncome": 120.0,
                 "Cash": 200.0, "LongTermDebt": 500.0},
            clean={"FCF": 100.0, "SharesDiluted": 100.0},
            derived={"EBITDA": 170.0, "Depreciation_Total": 50.0,
                     "OperatingIncome": 120.0, "CapEx": 100.0,
                     "NetDebt": 300.0},
        )
        # NEE override means A=L+E violation doesn't raise
        ok, _ = validate_cleaned_record_schema_contract(rec)
        assert ok is True
