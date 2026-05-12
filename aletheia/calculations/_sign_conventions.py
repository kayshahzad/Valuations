"""Three-tier sign-convention and range-bound constants.

The validation framework's central design decision: fields are
categorized by what is **mathematically possible**, not by what is
**typically expected**. The blanket "CapEx must be positive" rule
produces false positives on legitimate net-divestiture years and
trains the team to ignore the validator. The right design is strict
on identities (EBITDA ≥ EBIT, FCF = OpCF − CapEx) and tolerant on
values that can legitimately be negative (CapEx in divestiture
years, Net Income in losses, FCF in growth-investment periods).

Three tiers:

  Tier 1 — STRICT: fields that cannot legitimately be negative under
           any normal accounting circumstance. A negative value is a
           sign error, unit error, or wrong-field-mapping bug.

  Tier 2 — SOFT-FLAG: fields that CAN legitimately be negative but
           are usually not. Negative warrants a structured warning,
           not a refusal. Validation is via range checks on ratios
           and arithmetic identities, NOT sign rules.

  Tier 3 — NO SIGN RULE: fields that legitimately swing either sign
           often (e.g., tax rate in tax-benefit years, ROIC for
           value-destroying companies). Range checks only.

Adding a new field to the schema: classify it here FIRST, before
writing any validation code that consumes it.
"""

from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────
# Tier 1 — STRICT non-negative. Hard-fail on negative.
# ─────────────────────────────────────────────────────────────────────
TIER_1_STRICT_NONNEG = frozenset({
    "revenue",                # never negative for an operating company
    "total_assets",           # accounting identity: A = L + E with A ≥ 0
    "depreciation",           # U.S. GAAP: always positive; IFRS upward
                              # revaluations are an override-registry case
    "amortization",
    "cogs",                   # cost of goods sold
    "operating_expenses",
    "total_debt",             # gross debt; NetDebt is Tier 2
    "cash",
    "interest_expense",       # cost of debt
    "tax_paid",               # cash taxes (outflow positive convention)
    "shares_outstanding",     # strictly positive
    "shares_diluted",         # strictly positive
    "market_cap",             # strictly positive for any listed co
    "enterprise_value",       # market_cap + net_debt; should be positive
                              # except in extreme distress (override case)
    "goodwill",               # balance-sheet level non-negative
                              # (goodwill_change is Tier 2)
    "intangible_assets",      # level non-negative
})


# ─────────────────────────────────────────────────────────────────────
# Tier 2 — SOFT-FLAG. Negative is legitimate but unusual.
# Validate via range checks on ratios + arithmetic identities.
# ─────────────────────────────────────────────────────────────────────
TIER_2_SOFT_FLAG_NEGATIVE_OK = frozenset({
    "capex",                  # negative in net-divestiture years
                              # (GE breakup, Kraft portfolio rationalize)
    "net_income",             # company can lose money
    "ebit",                   # cyclical troughs / unprofitable years
    "ebitda",                 # rare but possible in distressed cases
    "fcf",                    # negative for growth-investment years
                              # (Amazon for ~a decade)
    "operating_cash_flow",    # negative for distressed / fast-growth
    "working_capital_change", # can be either direction
    "net_debt",               # NEGATIVE means net cash (AAPL, NVDA, MSFT)
    "goodwill_change",        # impairments + divestitures produce negative
    "total_equity",           # can be negative for buyback-heavy mature
                              # companies (LOW, HD historical)
    "retained_earnings",      # accumulated deficit possible
    "invested_capital",       # rare but can be negative
})


# ─────────────────────────────────────────────────────────────────────
# Tier 3 — NO SIGN RULE. Validate finiteness + range only.
# ─────────────────────────────────────────────────────────────────────
TIER_3_NO_SIGN_RULE = frozenset({
    "tax_rate",               # can be negative in tax-benefit years
                              # (deferred tax asset reversals, NOL release)
    "effective_tax_rate",
    "roic",                   # negative when company destroys value
    "roe",
    "roa",
    "gross_margin",
    "operating_margin",
    "ebit_margin",
    "ebitda_margin",
    "net_margin",
    "fcf_margin",
    "revenue_growth_rate",    # declining businesses exist
    "implied_cagr",           # reverse-DCF can imply decline
    "historical_cagr",
    "wacc",                   # positive but fluctuates widely
    "cost_of_equity",
    "cost_of_debt",
    "beta",
    "risk_free_rate",
    "terminal_growth",
})


# ─────────────────────────────────────────────────────────────────────
# Range bounds for ratio / rate validation.
# Format: (min, max). Sourced from documented analytical conventions,
# not invented at validation time. Modify only with a documented
# rationale committed alongside the change.
# ─────────────────────────────────────────────────────────────────────
RANGE_BOUNDS = {
    # Reinvestment / capital intensity
    "capex_to_revenue":               (-0.30, 0.50),   # negative = net divestitures
    "da_to_revenue":                  (0.0, 0.40),     # D&A as % of revenue
    "fcf_to_revenue":                 (-1.0, 0.60),    # growth-investment can go very negative
    "operating_cash_flow_to_revenue": (-1.0, 0.60),

    # Profitability
    "ebit_to_revenue":                (-2.0, 0.80),
    "ebitda_to_revenue":              (-1.5, 0.80),
    "net_income_to_revenue":          (-2.0, 1.5),
    "gross_margin_pct":               (-100.0, 100.0),  # decimal percentage convention varies

    # Returns
    "roic":                           (-0.50, 1.50),
    "roe":                            (-2.0, 2.0),     # negative-equity companies blow this up
    "roa":                            (-0.30, 0.80),

    # Tax
    "tax_rate":                       (-1.0, 1.0),     # hard bound
    "effective_tax_rate_normal_band": (0.0, 0.50),     # warning band, not hard

    # Discount rates
    "wacc":                           (0.02, 0.30),
    "cost_of_equity":                 (0.02, 0.30),
    "cost_of_debt":                   (0.00, 0.30),
    "risk_free_rate":                 (0.00, 0.10),
    "beta":                           (0.0, 4.0),

    # Growth
    "revenue_growth_rate_annual":     (-0.50, 2.0),    # outside = merger/divestiture
    "implied_cagr":                   (-0.50, 1.0),    # outside = model degradation
    "terminal_growth":                (-0.02, 0.05),

    # Balance sheet ratios
    "net_debt_to_assets":             (-0.50, 1.50),
    "goodwill_to_assets":             (0.0, 0.80),
    "shares_diluted_yoy_ratio":       (0.70, 1.30),    # outside = split or M&A

    # Cross-period consistency (Phase 0 anomaly A2)
    "shares_diluted_yoy_ratio_pre_split_adj": (0.70, 1.30),
}


# ─────────────────────────────────────────────────────────────────────
# Identity tolerances. Fractional (0.005 = 0.5%). Tighter for definitional
# identities (EBITDA = EBIT + D&A), looser for derived (NetDebt formulas).
# ─────────────────────────────────────────────────────────────────────
IDENTITY_TOLERANCES = {
    "ebitda_equals_ebit_plus_da":         0.005,   # 0.5% — definitional
    "fcf_equals_opcf_minus_capex":        0.005,   # definitional
    "accounting_equation_a_eq_l_plus_e":  0.005,   # A8 NEE-class bug
    "net_debt_equals_debt_minus_cash":    0.010,   # 1% — derived
    "roic_equals_nopat_over_ic":          0.010,
    "tier_2_capex_to_revenue":            0.005,
    "ttm_flow_equals_q4_sum":             0.020,   # 2% — quarterly rounding
}


# Sanity assertion at module load — alert if a field is in multiple tiers
def _assert_disjoint_tiers() -> None:
    overlap_12 = TIER_1_STRICT_NONNEG & TIER_2_SOFT_FLAG_NEGATIVE_OK
    overlap_13 = TIER_1_STRICT_NONNEG & TIER_3_NO_SIGN_RULE
    overlap_23 = TIER_2_SOFT_FLAG_NEGATIVE_OK & TIER_3_NO_SIGN_RULE
    if overlap_12 or overlap_13 or overlap_23:
        raise RuntimeError(
            "sign_conventions: tier sets must be disjoint; "
            f"overlaps: T1∩T2={overlap_12}, T1∩T3={overlap_13}, T2∩T3={overlap_23}"
        )


_assert_disjoint_tiers()
