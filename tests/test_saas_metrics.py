"""SaaS analysis overlay — isolation + deterministic KPIs (plan Builds A, B)."""

import pytest

from dotenv import load_dotenv
load_dotenv()

from config.ticker_classification import is_saas_company, get_extended_universe
from aletheia.utils.calc_input_builder import make_calc_input
from aletheia.tools.saas_metrics import build_saas_metrics


# ── Build A: the isolation gate ──────────────────────────────────────────────
def test_is_saas_company_gate():
    u = get_extended_universe()
    assert is_saas_company(u.get("ADBE")) is True
    assert is_saas_company(u.get("MSFT")) is True
    assert is_saas_company(u.get("EQIX")) is False   # REIT
    assert is_saas_company(u.get("JPM")) is False    # bank
    assert is_saas_company(u.get("AAPL")) is False   # hardware
    assert is_saas_company(None) is False


# ── Build B: deterministic KPIs for ADBE ─────────────────────────────────────
@pytest.fixture(scope="module")
def adbe():
    return build_saas_metrics(make_calc_input("ADBE"), market_cap=2.0e11)


def test_adbe_overlay_available(adbe):
    assert adbe["available"] is True


def test_owners_earnings_is_fcf_minus_sbc(adbe):
    oe = adbe["owners_earnings"]
    assert abs(oe["owners_earnings_fcf"] - (oe["fcf"] - oe["sbc"])) < 1.0
    # precise label — NOT framed as a correction of an error
    assert "economic cost of dilution" in oe["label"]
    assert "correction" not in oe["label"].lower() or "not a correction" in oe["label"].lower()
    # owner's-earnings yield < fcf yield (SBC is a real cost)
    assert oe["owners_earnings_yield"] < oe["fcf_yield"]


def test_magic_number_computed_when_sm_separable(adbe):
    # ADBE has clean_SellingAndMarketing → magic number is a real value
    assert adbe["magic_number"]["value"] is not None


def test_billings_suppressed_for_acquisitive_or_gap(adbe):
    # ADBE: no deferred column AND M&A in window → billings must NOT be a number
    bl = adbe.get("billings")
    assert bl is None or (isinstance(bl, dict) and bl.get("billings") is None)


def test_cyclicality_reconciliation_flag_present(adbe):
    kinds = [f.get("kind") for f in adbe.get("flags", [])]
    assert "cyclicality_reconciliation" in kinds


# ── Build B: isolation — non-SaaS short-circuits ─────────────────────────────
@pytest.mark.parametrize("ticker", ["EQIX", "JPM"])
def test_non_saas_short_circuits(ticker):
    out = build_saas_metrics(make_calc_input(ticker))
    assert out == {"available": False}


def test_magic_number_suppressed_when_sm_not_separable():
    """Synthetic frame with only combined SG&A → magic number must be None,
    never a fabricated value from the SG&A substitution."""
    import pandas as pd

    class _Cls:
        ticker, sector, industry = "FAKE", "Technology", "Software"
        lifecycle, business_model = "growth_compounder_software", "fcff_compatible"

    class _CI:
        classification = _Cls()
        df = pd.DataFrame([
            {"period": "FY", "fiscal_year": 2024, "clean_Revenue": 1000.0,
             "clean_SGA_Combined": 400.0, "derived_FCF": 200.0, "clean_SBC": 50.0,
             "derived_GrossMargin_Pct": 80.0},
            {"period": "FY", "fiscal_year": 2025, "clean_Revenue": 1200.0,
             "clean_SGA_Combined": 460.0, "derived_FCF": 240.0, "clean_SBC": 60.0,
             "derived_GrossMargin_Pct": 81.0},
        ])

    out = build_saas_metrics(_CI())
    assert out["available"] is True
    assert out["magic_number"]["value"] is None
    assert "not separable" in out["magic_number"]["reason"]
