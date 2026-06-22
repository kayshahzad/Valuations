"""Bank operating metrics — read from SEC XBRL companyfacts.

The bank income statement (NII, provisions, non-interest income/expense, deposits,
loans) isn't in the industrial cleaned frame; bank_metrics reads it straight from
companyfacts and derives the KPIs. CET1/RWA are NOT in XBRL → reported as a gap,
not faked.
"""

from __future__ import annotations

import pytest


def test_jpm_bank_metrics_tie_to_actuals():
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.bank_metrics import build_bank_metrics
    try:
        calc = make_calc_input("JPM")
    except Exception as e:
        pytest.skip(f"JPM unavailable: {e}")

    m = build_bank_metrics(calc, shares=2.7937e9)
    if not m.get("available"):
        pytest.skip(f"JPM companyfacts not cached: {m.get('notes')}")
    k = m["kpis"]
    # JPM actuals: net revenue ~$180B, ROA ~1.3%, efficiency ~55%, tBVPS ~$110
    assert 150e9 < k["net_revenue"] < 220e9
    assert 0.008 < k["roa"] < 0.020
    assert 0.40 < k["efficiency_ratio"] < 0.70
    assert 90 < k["tangible_bvps"] < 130
    assert k["net_interest_income"] is not None and k["provisions"] is not None
    assert k["deposits"] > 1e12 and k["loans"] > 5e11   # trillions/hundreds of billions
    # regulatory capital is gated, not faked
    assert "CET1 ratio" in (m["capital_adequacy_gap"]["missing"])


def test_gate_excludes_non_banks():
    # is_bank_for_display gate: V/MCO (Financials but fcff) and AAPL get nothing.
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.bank_metrics import build_bank_metrics
    for t in ("V", "AAPL"):
        try:
            calc = make_calc_input(t)
        except Exception:
            continue
        assert build_bank_metrics(calc, shares=1e9).get("available") is not True


def test_sofi_is_a_bank_metrics_filer():
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.bank_metrics import build_bank_metrics
    try:
        calc = make_calc_input("SOFI")
    except Exception as e:
        pytest.skip(f"SOFI unavailable: {e}")
    m = build_bank_metrics(calc, shares=1.378e9)
    if not m.get("available"):
        pytest.skip(f"SOFI companyfacts not cached: {m.get('notes')}")
    # SOFI IS a bank — has NII; net revenue ties to the ~$3.6B Capital IQ figure
    assert m["kpis"]["net_interest_income"] is not None
    assert 2e9 < m["kpis"]["net_revenue"] < 6e9
