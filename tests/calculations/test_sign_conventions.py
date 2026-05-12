"""Phase 7 tests — sign-convention tier definitions and range bounds.

These tests defend the structural invariants of the framework's tier
system:
  - The three tier sets must be DISJOINT (no field in multiple tiers).
  - Tier-1 fields must be reasonable (revenue, assets, etc. — non-negative
    is a real accounting invariant).
  - Range bounds must be sensibly ordered (min < max).
"""

from __future__ import annotations

import pytest

from aletheia.calculations import (
    RANGE_BOUNDS,
    TIER_1_STRICT_NONNEG,
    TIER_2_SOFT_FLAG_NEGATIVE_OK,
    TIER_3_NO_SIGN_RULE,
    IDENTITY_TOLERANCES,
)


class TestTierDisjointness:

    def test_tier1_tier2_disjoint(self):
        assert not (TIER_1_STRICT_NONNEG & TIER_2_SOFT_FLAG_NEGATIVE_OK), (
            "Tier-1 and Tier-2 must be disjoint — a field cannot be both "
            "strict-nonneg AND legitimately-negative-OK"
        )

    def test_tier1_tier3_disjoint(self):
        assert not (TIER_1_STRICT_NONNEG & TIER_3_NO_SIGN_RULE)

    def test_tier2_tier3_disjoint(self):
        assert not (TIER_2_SOFT_FLAG_NEGATIVE_OK & TIER_3_NO_SIGN_RULE)


class TestTier1Membership:
    """Tier-1 fields must be mathematically non-negative (anchor cases)."""

    def test_revenue_in_tier1(self):
        assert "revenue" in TIER_1_STRICT_NONNEG

    def test_total_assets_in_tier1(self):
        assert "total_assets" in TIER_1_STRICT_NONNEG

    def test_shares_outstanding_in_tier1(self):
        assert "shares_outstanding" in TIER_1_STRICT_NONNEG

    def test_market_cap_in_tier1(self):
        assert "market_cap" in TIER_1_STRICT_NONNEG

    def test_depreciation_in_tier1(self):
        """U.S. GAAP depreciation is always positive."""
        assert "depreciation" in TIER_1_STRICT_NONNEG


class TestTier2Membership:
    """Tier-2 fields CAN legitimately be negative (e.g., CapEx in
    net-divestiture years, FCF for growth-investment companies)."""

    def test_capex_in_tier2(self):
        """The MDT-incident root cause: capex was wrongly assumed nonneg.
        Tier-2 framing accepts legitimate negatives via range checks."""
        assert "capex" in TIER_2_SOFT_FLAG_NEGATIVE_OK

    def test_fcf_in_tier2(self):
        """AMZN ran negative FCF for ~a decade — Tier-2 accepts it."""
        assert "fcf" in TIER_2_SOFT_FLAG_NEGATIVE_OK

    def test_net_debt_in_tier2(self):
        """Cash-rich tech companies have net cash (negative net debt).
        Hard-failing on negative would reject AAPL/NVDA/MSFT."""
        assert "net_debt" in TIER_2_SOFT_FLAG_NEGATIVE_OK

    def test_net_income_in_tier2(self):
        assert "net_income" in TIER_2_SOFT_FLAG_NEGATIVE_OK

    def test_total_equity_in_tier2(self):
        """Buyback-heavy mature companies (LOW, HD) can show negative equity."""
        assert "total_equity" in TIER_2_SOFT_FLAG_NEGATIVE_OK


class TestTier3Membership:
    """Tier-3 fields swing either sign frequently — only range checks."""

    def test_tax_rate_in_tier3(self):
        """Tax rate can be negative in tax-benefit years (NOL reversal)."""
        assert "tax_rate" in TIER_3_NO_SIGN_RULE

    def test_roic_in_tier3(self):
        """Loss-making companies have negative ROIC."""
        assert "roic" in TIER_3_NO_SIGN_RULE

    def test_implied_cagr_in_tier3(self):
        """Reverse-DCF can imply decline for distressed names."""
        assert "implied_cagr" in TIER_3_NO_SIGN_RULE

    def test_wacc_in_tier3(self):
        assert "wacc" in TIER_3_NO_SIGN_RULE


class TestRangeBounds:
    """Sanity checks on RANGE_BOUNDS — min < max, values reasonable."""

    def test_all_bounds_well_ordered(self):
        for key, (lo, hi) in RANGE_BOUNDS.items():
            assert lo < hi, f"RANGE_BOUNDS[{key!r}] = ({lo}, {hi}) — min must be < max"

    def test_capex_to_revenue_accepts_semi_fab(self):
        """TSMC during expansion runs 40-55% capex/revenue — must be in band."""
        lo, hi = RANGE_BOUNDS["capex_to_revenue"]
        assert lo <= -0.30 and hi >= 0.55, (
            f"capex_to_revenue {(lo, hi)} too tight for semiconductor-fab norms"
        )

    def test_tax_rate_accepts_tax_benefit_years(self):
        """Tax rate can be negative in tax-benefit years."""
        lo, hi = RANGE_BOUNDS["tax_rate"]
        assert lo <= -0.5, f"tax_rate min {lo} doesn't allow tax-benefit years"

    def test_implied_cagr_accepts_decline(self):
        """Reverse-DCF should accept market pricing in decline."""
        lo, hi = RANGE_BOUNDS["implied_cagr"]
        assert lo < 0, f"implied_cagr min {lo} excludes decline-pricing scenarios"

    def test_wacc_excludes_zero_and_below(self):
        """WACC must be positive (cost of capital)."""
        lo, hi = RANGE_BOUNDS["wacc"]
        assert lo > 0, f"wacc min {lo} should be positive"
        assert hi <= 0.50, f"wacc max {hi} should be ≤ 50%"


class TestIdentityTolerances:
    """Identity tolerances should be tight on definitional identities,
    looser on derived."""

    def test_all_tolerances_in_reasonable_band(self):
        for key, tol in IDENTITY_TOLERANCES.items():
            assert 0 < tol <= 0.05, (
                f"IDENTITY_TOLERANCES[{key!r}] = {tol} — should be in (0, 5%]"
            )

    def test_definitional_identities_are_tight(self):
        """EBITDA = EBIT + D&A is definitional — tight tolerance."""
        assert IDENTITY_TOLERANCES["ebitda_equals_ebit_plus_da"] <= 0.01

    def test_accounting_equation_is_tight(self):
        """A=L+E is the most fundamental accounting identity."""
        assert IDENTITY_TOLERANCES["accounting_equation_a_eq_l_plus_e"] <= 0.01
