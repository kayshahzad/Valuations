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


def test_financial_excluded():
    r = screen_distress(_base(), [8.0] * 10, _FIN)
    assert not r.scoreable and "non_fcff" in r.reason


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


def test_wayfair_real_data_fires_actionable_distress():
    """Live falsifier, FROZEN from Wayfair's SEC filings (FY2024). Negative book
    equity from ACCUMULATED DEFICIT (12y of losses, no buybacks) -> scores and
    fires actionable distress. The mirror of LOW (negative equity from buybacks
    -> abstains): same symptom, opposite correct verdict, proving the
    ΣNI > 3·|RE| discriminator on real data. Corroboration comes from interest
    coverage (0.1×), NOT EBIT/TA — EBIT is barely positive — which is exactly the
    retail-safe path (negative WC can't corroborate distress in internet retail)."""
    latest = dict(
        ticker="W", fiscal_year=2024,
        raw_TotalAssets=2.87e9, raw_TotalLiabilities=5.71e9,
        raw_RetainedEarnings=-4.93e9, raw_TotalEquity=-2.84e9,
        raw_CurrentAssets=1.568e9, raw_LiabilitiesCurrent=2.055e9,
        clean_NormalizedEBIT=0.02e9,
        raw_LongTermDebt=2.931e9, raw_CurrentPortionLongTermDebt=0.039e9,
    )
    ni_history = [-0.414e9] * 12          # 12y of losses, sum ~ -4.97B
    cls = {"ticker": "W", "sector": "Consumer Discretionary",
           "industry": "Internet Retail", "business_model": "fcff_compatible"}
    r = screen_distress(latest, ni_history, cls, interest_expense=0.17e9)
    assert r.scoreable and r.classification == "accumulated_deficit"
    assert r.zone == "distress"
    assert r.leverage_gated and r.corroborated
    assert r.actionable_distress is True
    # corroboration must come from coverage, not EBIT/TA (EBIT is positive here)
    assert r.facts["ebit_positive"] is True and r.facts["interest_coverage"] < 2.0


def test_short_history_negative_term_abstains_insufficient():
    """RIVN-class (5y history): can't classify a negative RE -> insufficient_history."""
    r = screen_distress(
        _base(raw_RetainedEarnings=-27.0, clean_NormalizedEBIT=-3.0),
        [-5.0] * 5,                         # only 5y coverage
        _TECH,
    )
    assert not r.scoreable and "insufficient_history" in r.reason
    assert r.classification == "unknown"


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
    ("JPM",  "financial"),
    ("ORCL", "scoreable_not_actionable"),
])
def test_db_acceptance(db, ticker, expect):
    if db.get_latest(ticker).empty:
        pytest.skip(f"{ticker} not ingested")
    r = screen_ticker(db, ticker)
    if expect == "abstain_capital_structure":
        assert not r.scoreable and "capital_structure" in (r.reason or "")
    elif expect == "financial":
        assert not r.scoreable and "non_fcff" in (r.reason or "")
    elif expect == "safe_not_actionable":
        assert r.scoreable and r.zone == "safe" and not r.actionable_distress
    elif expect == "scoreable_not_actionable":
        assert r.scoreable and not r.actionable_distress   # corroboration guard vetoes
