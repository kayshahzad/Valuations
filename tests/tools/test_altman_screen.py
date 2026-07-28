"""Altman distress screen (Phase 1a) — acceptance + branch coverage.

The acceptance gate: flags a genuine deficit name, clears LOW (buyback artifact),
never emits actionable distress on a healthy name, and excludes financials.
"""
import pytest

from aletheia.tools.altman_screen import screen_distress, screen_ticker, AltmanScreen


def _base(**kw):
    """A healthy, unlevered non-financial baseline record; override per test."""
    d = dict(
        ticker="TEST", fiscal_year=2025,
        raw_TotalAssets=100.0, raw_TotalLiabilities=40.0, raw_TotalEquity=60.0,
        raw_RetainedEarnings=40.0, raw_CurrentAssets=30.0, raw_LiabilitiesCurrent=20.0,
        clean_NormalizedEBIT=15.0, clean_Revenue=90.0, derived_EBITDA=20.0,
        derived_NetDebt=-10.0, raw_LongTermDebt=5.0, raw_CurrentPortionLongTermDebt=1.0,
        raw_SharesDiluted=10.0,
    )
    d.update(kw)
    return d


_TECH = {"sector": "Technology", "industry": "Software", "business_model": "fcff_compatible"}
_FIN = {"sector": "Financials", "industry": "Banks", "business_model": "ddm_required"}
_MFG = {"sector": "Industrials", "industry": "Heavy Machinery", "business_model": "fcff_compatible"}


def test_healthy_name_scores_safe_not_actionable():
    r = screen_distress(_base(), [8.0] * 10, _TECH)
    assert r.scoreable and r.zone == "safe"
    assert r.actionable_distress is False


def test_genuine_financials_excluded():
    """Banks / lenders / card issuers / insurers / diversified financials are
    Altman-undefined."""
    for cls in (
        {"sector": "Financials", "industry": "Banks"},                    # JPM
        {"sector": "Financials", "industry": "Payments & Cards"},          # AXP (lender)
        {"sector": "Financial Services", "industry": "Financial - Credit Services"},  # SOFI
        {"sector": "Financials", "industry": "Diversified"},              # BRK-B
    ):
        r = screen_distress(_base(), [8.0] * 10, cls)
        assert not r.scoreable and "financial" in r.reason, cls


def test_specialized_but_definable_are_NOT_excluded():
    """Utilities / REITs / MLPs / payment networks / rating agencies / managed
    care route to specialized VALUATION engines but have definable Altman inputs
    — they must be scored, not excluded (the split fix)."""
    for cls in (
        {"sector": "Utilities", "industry": "Utilities"},                 # NEE
        {"sector": "Real Estate", "industry": "Data Center REIT"},        # EQIX
        {"sector": "Energy", "industry": "Oil & Gas Midstream"},          # ET
        {"sector": "Financials", "industry": "Payments"},                 # V (network, not lender)
        {"sector": "Financials", "industry": "Credit Ratings"},           # MCO
        {"sector": "Healthcare Plans", "industry": "Managed Care"},       # CNC / UNH
    ):
        r = screen_distress(_base(), [8.0] * 10, cls)
        assert r.scoreable, f"{cls} was wrongly excluded"


def test_capital_return_negative_equity_abstains():
    """LOW-class: negative book equity from buybacks -> abstain, never a score."""
    r = screen_distress(
        _base(raw_TotalEquity=-9.0, raw_RetainedEarnings=-10.0, clean_NormalizedEBIT=8.0),
        [6.0] * 12,                       # profitable history -> capital_return
        {"sector": "Consumer Discretionary", "industry": "Home Improvement Retail",
         "business_model": "fcff_compatible"},
    )
    assert not r.scoreable
    assert "capital_structure" in r.reason
    assert r.classification == "capital_return"


def test_capital_return_large_negative_re_positive_equity_abstains():
    """APIC/SBC-heavy: positive equity but |RE|/TA >= 10% from buybacks -> abstain."""
    r = screen_distress(
        _base(raw_TotalEquity=30.0, raw_RetainedEarnings=-30.0),  # RE/TA = -30%
        [10.0] * 12,
        _TECH,
    )
    assert not r.scoreable and "capital_structure" in r.reason


def test_capital_return_small_negative_re_scores_raw():
    """ORCL-class: positive equity, |RE|/TA < 10% (noise) -> score raw, not abstain."""
    r = screen_distress(
        _base(raw_RetainedEarnings=-4.0),   # RE/TA = -4%
        [10.0] * 12,
        _TECH,
    )
    assert r.scoreable and r.re_reason == "capital_return_noise"


def test_accumulated_deficit_with_coverage_is_actionable_distress():
    """SNAP-class (>=8y losses): accumulated_deficit + EBIT/TA<0 corroborates -> distress."""
    r = screen_distress(
        _base(raw_TotalAssets=15.0, raw_RetainedEarnings=-14.0, raw_TotalEquity=6.0,
              clean_NormalizedEBIT=-3.0, derived_EBITDA=-2.0,
              raw_LongTermDebt=4.0, raw_CurrentPortionLongTermDebt=0.5, derived_NetDebt=3.0),
        [-1.5] * 9,                         # 9y of losses -> accumulated_deficit
        _TECH,
    )
    assert r.scoreable and r.classification == "accumulated_deficit"
    assert r.zone == "distress" and r.corroborated is True
    assert r.actionable_distress is True


from aletheia.tools.altman_validation import VALIDATION_CASES, screen_case


@pytest.mark.parametrize("case", VALIDATION_CASES, ids=lambda c: c["ticker"])
def test_validation_universe_stays_actionable(case):
    """Live canary — real distressed names (frozen from SEC filings) that must
    ALWAYS fire actionable distress. If a regression breaks the firing path,
    these flip to safe/abstain and this test fails immediately. Includes the
    Wayfair falsifier (neg equity from deficit, coverage-corroborated) and RIVN
    (5y young-and-losing, proving the deficit-window fix)."""
    r = screen_case(case)
    assert r.scoreable, f"{case['ticker']} stopped scoring: {r.reason}"
    assert r.classification == case["expect_classification"]
    assert r.actionable_distress is True, f"{case['ticker']} no longer actionable"


def test_wayfair_corroboration_via_coverage_not_ebit():
    """Wayfair-specific: EBIT is barely positive, so corroboration must come from
    interest coverage (<2×), NOT EBIT/TA — the retail-safe path."""
    r = screen_case(VALIDATION_CASES[0])   # Wayfair
    assert r.facts["ebit_positive"] is True and r.facts["interest_coverage"] < 2.0


def test_five_year_deficit_now_scores_actionable():
    """RIVN-class (5y of losses): net cumulative losses are unambiguous, so the
    8y floor no longer gates them — scores accumulated_deficit, not abstain.
    Young-and-losing-money is exactly the target profile."""
    r = screen_distress(
        _base(raw_TotalAssets=15.0, raw_RetainedEarnings=-27.0, raw_TotalEquity=8.0,
              clean_NormalizedEBIT=-3.0, derived_EBITDA=-2.0,
              raw_LongTermDebt=4.0, raw_CurrentPortionLongTermDebt=0.5, derived_NetDebt=3.0),
        [-5.0] * 5,                         # 5y of losses -> ΣNI < 0
        _TECH,
    )
    assert r.scoreable and r.classification == "accumulated_deficit"
    assert r.actionable_distress is True


def test_two_year_deficit_below_floor_abstains():
    """Below the 3y deficit floor -> insufficient_history (not enough to trust)."""
    r = screen_distress(
        _base(raw_RetainedEarnings=-27.0, clean_NormalizedEBIT=-3.0),
        [-5.0] * 2,
        _TECH,
    )
    assert not r.scoreable and "insufficient_history" in r.reason


def test_short_history_positive_sigma_negative_equity_abstains():
    """Positive cumulative income but negative equity and <8y — can't confirm
    capital_return, so abstain (the 8y floor still gates THIS branch)."""
    r = screen_distress(
        _base(raw_TotalEquity=-2.0, raw_RetainedEarnings=-5.0, clean_NormalizedEBIT=4.0),
        [1.0] * 5,                          # ΣNI > 0, only 5y
        _TECH,
    )
    assert not r.scoreable and "insufficient_history" in r.reason
    assert r.classification == "unknown"


def test_managed_care_liquidity_cannot_corroborate():
    """Float archetype (managed care): claims-payable makes current ratio < 0.8
    structurally, so it must NOT corroborate distress. Only EBIT/TA or coverage
    can. The veto is structural, not incidental on the leverage gate."""
    _CARE = {"sector": "Healthcare Plans", "industry": "Managed Care",
             "business_model": "residual_income_required"}
    # Low current ratio + rising ST-debt (would corroborate for a normal name),
    # but EBIT positive and coverage healthy -> must NOT corroborate for float.
    r = screen_distress(
        _base(raw_CurrentAssets=15.0, raw_LiabilitiesCurrent=40.0,  # current ratio 0.375
              raw_CurrentPortionLongTermDebt=5.0, clean_NormalizedEBIT=12.0),
        [8.0] * 10, _CARE,
        interest_expense=1.0, prior_short_term_debt=1.0,   # ST-debt rising 1->5
    )
    assert r.scoreable and r.float_distorted is True
    assert r.corroborated is False        # liquidity barred, EBIT+ and coverage fine


def test_managed_care_still_corroborates_on_real_ebit_loss():
    """Structural veto is on LIQUIDITY only — a genuine operating loss (EBIT/TA<0)
    still corroborates for managed care."""
    _CARE = {"sector": "Healthcare Plans", "industry": "Managed Care",
             "business_model": "residual_income_required"}
    r = screen_distress(_base(clean_NormalizedEBIT=-5.0), [8.0] * 10, _CARE)
    assert r.scoreable and r.float_distorted is True
    assert r.corroborated is True         # EBIT/TA < 0 is a real signal, not float


def test_manufacturer_gets_advisory_z_original():
    r = screen_distress(_base(), [8.0] * 10, _MFG, price=100.0, shares=10.0)
    assert r.model_authoritative == "Z''"
    assert r.z_original is not None          # advisory cross-read populated
    assert r.z_double_prime is not None


# ── DB-backed acceptance (skips if the ticker isn't ingested) ────────────────

@pytest.fixture(scope="module")
def db():
    from aletheia.data.database import InvestmentDatabase
    return InvestmentDatabase(verbose=False)


@pytest.mark.parametrize("ticker,expect", [
    ("LOW",  "abstain_capital_structure"),
    ("AAPL", "safe_not_actionable"),
    ("JPM",  "financial"),        # genuine bank -> excluded
    ("BRK-B", "financial"),       # diversified financial -> excluded
    ("ORCL", "scoreable_not_actionable"),
    ("NEE",  "scoreable"),        # utility now evaluated (split fix), not excluded
    ("CNC",  "scoreable"),        # managed care now evaluated
    ("V",    "scoreable"),        # payment network now evaluated
])
def test_db_acceptance(db, ticker, expect):
    if db.get_latest(ticker).empty:
        pytest.skip(f"{ticker} not ingested")
    r = screen_ticker(db, ticker)
    if expect == "abstain_capital_structure":
        assert not r.scoreable and "capital_structure" in (r.reason or "")
    elif expect == "financial":
        assert not r.scoreable and "financial" in (r.reason or "")
    elif expect == "safe_not_actionable":
        assert r.scoreable and r.zone == "safe" and not r.actionable_distress
    elif expect == "scoreable_not_actionable":
        assert r.scoreable and not r.actionable_distress   # corroboration guard vetoes
    elif expect == "scoreable":
        assert r.scoreable   # evaluated, not wrongly excluded as "financial"
