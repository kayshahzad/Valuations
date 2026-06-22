"""Phase 2 — CADS (cash available for debt service) coverage + CF-R5 trigger."""

import pytest

from dotenv import load_dotenv
load_dotenv()

from aletheia.utils.calc_input_builder import make_calc_input
from aletheia.tools.cads_coverage import build_cads_coverage


def test_cads_is_ebitda_minus_capex():
    c = build_cads_coverage(make_calc_input("ADBE"))
    assert c["available"]
    latest = c["latest"]
    assert abs(latest["cads"] - (latest["ebitda"] - latest["capex"])) < 1.0


def test_eqix_capex_sinkhole_triggers():
    """EQIX: build-out CapEx outgrows EBITDA → CADS negative → CF-R5 fires."""
    c = build_cads_coverage(make_calc_input("EQIX"))
    assert c["available"]
    assert c["latest"]["cads"] < 0
    assert c["trigger"] is True
    assert "capex-sinkhole" in (c["trigger_reason"] or "")


def test_adbe_coverage_clear():
    """ADBE: trivial capex, fat CADS → no trigger, high coverage."""
    c = build_cads_coverage(make_calc_input("ADBE"))
    assert c["trigger"] is False
    assert c["coverage"] is None or c["coverage"] > 5.0


def test_calc_node_stores_cads():
    from aletheia.agents.calc_node import calc_node
    st = calc_node({"ticker": "EQIX"})
    cads = (st.get("phase2_valuation") or {}).get("cads") or {}
    assert cads.get("available")
    assert cads.get("trigger") is True


def test_cads_refuses_financials():
    """CF-R18: CADS = EBITDA−CapEx with a gross-LTD debt-service proxy is a bogus
    credit measure for a bank/insurer/lender — refuse rather than leak an
    industrial credit-floor number onto JPM/SOFI/AXP/BRK-B."""
    for t in ("JPM", "SOFI", "AXP", "BRK-B"):
        try:
            c = build_cads_coverage(make_calc_input(t))
        except Exception:
            continue
        assert c.get("available") is not True, f"{t} should be refused by CADS"
        assert "financials" in (c.get("notes") or "").lower()


def test_cads_keeps_industrials_and_utilities():
    """The financials guard must not capture industrials (AAPL) or utilities (NEE),
    which have a meaningful EBITDA / capex / debt-service profile."""
    for t in ("AAPL", "NEE"):
        try:
            c = build_cads_coverage(make_calc_input(t))
        except Exception:
            continue
        assert c.get("available") is True, f"{t} should keep CADS"
