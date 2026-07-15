"""Phase Q-4: TTM derivation + Gate A.TTM cross-check.

Pins:

  1. derive_ttm_from_fmp() sums the last 4 quarterly statements for
     flow items (revenue, NI, FCF, EBITDA, OpCF, CapEx, SBC) and uses
     the latest balance sheet for stock items.
  2. Period is stamped 'TTM' and the record carries
     ttm_source='fmp_derived_quarters' so the eventual SEC primary
     source-swap is observable.
  3. Skip paths: missing API key, fewer than 4 quarters, foreign-
     currency filer, missing required flow series — all degrade to
     a structured skip_reason without raising.
  4. Gate A.TTM byte-perfect-required: 0.5% drift on revenue or NI
     escalates to blocking_drift.  Drift on ROIC/ROE is informational
     (definitional tier — different formulas legitimately diverge).
"""

from __future__ import annotations

from unittest.mock import patch

from aletheia.data.ttm_derivation import derive_ttm_from_fmp
from aletheia.data.fmp_validation import validate_ttm_record


# ── Stub FMP responses ────────────────────────────────────────────────

def _quarterly_income(q_revenue=100e9, q_ni=25e9, q_op_inc=30e9,
                      q_ebitda=35e9, q_cogs=60e9, q_rnd=8e9,
                      shares=15e9, currency="USD", fy=2025):
    """Build 4 quarterly income records, most-recent-first."""
    return [
        {"date": f"{fy}-{m:02d}-30", "fiscalYear": str(fy), "period": q,
         "revenue": q_revenue, "netIncome": q_ni,
         "operatingIncome": q_op_inc, "ebitda": q_ebitda,
         "costOfRevenue": q_cogs, "researchAndDevelopmentExpenses": q_rnd,
         "interestExpense": 1e9,
         "weightedAverageShsOutDil": shares,
         "reportedCurrency": currency}
        for q, m in [("Q3", 9), ("Q2", 6), ("Q1", 3), ("Q4", 12)]
    ]


def _quarterly_cashflow(q_ocf=28e9, q_fcf=22e9, q_capex=-6e9, q_sbc=2e9):
    return [
        {"date": f"2025-{m:02d}-30",
         "operatingCashFlow": q_ocf, "freeCashFlow": q_fcf,
         "capitalExpenditure": q_capex, "stockBasedCompensation": q_sbc}
        for m in (9, 6, 3, 12)
    ]


def _balance_latest():
    return [{
        "date": "2025-09-30",
        "totalAssets": 400e9,
        "totalLiabilities": 250e9,
        "totalStockholdersEquity": 150e9,
        "cashAndCashEquivalents": 30e9,
        "longTermDebt": 80e9,
        "shortTermDebt": 10e9,
        "commonStockSharesOutstanding": 15e9,
    }]


def _km_ttm(ev=4_000e9, ev_to_sales=10.0, ev_to_ebitda=28.5,
            ev_to_fcf=45.5, roic=0.30, roe=0.65):
    """FMP /key-metrics-ttm: only EV + ratio-multiples are exposed
    (no absolute revenue/NI/FCF). ROIC + ROE land here, not on
    /ratios-ttm. Defaults imply revenue=$400B, ebitda=$140B, fcf=$88B
    via EV-multiple division — matches the quarterly-sum stub."""
    return {
        "enterpriseValueTTM":         ev,
        "evToSalesTTM":               ev_to_sales,
        "evToEBITDATTM":              ev_to_ebitda,
        "evToFreeCashFlowTTM":        ev_to_fcf,
        "returnOnInvestedCapitalTTM": roic,
        "returnOnEquityTTM":          roe,
    }


def _ratios_ttm(op_margin=0.30):
    """FMP /ratios-ttm: profit margins + ratio multiples (no ROIC/ROE)."""
    return {
        "operatingProfitMarginTTM":   op_margin,
        "effectiveTaxRateTTM":        0.21,
    }


def _patch_fmp(income, balance, cashflow, km_ttm=None, ratios_ttm=None):
    """Patch all five fmp_client fetchers in one go."""
    return [
        patch("aletheia.data.fmp_client.has_api_key", return_value=True),
        patch("aletheia.data.fmp_client.fetch_income_statements",
              return_value=income),
        patch("aletheia.data.fmp_client.fetch_balance_sheets",
              return_value=balance),
        patch("aletheia.data.fmp_client.fetch_cash_flows",
              return_value=cashflow),
        patch("aletheia.data.fmp_client.fetch_key_metrics_ttm",
              return_value=km_ttm or _km_ttm()),
        patch("aletheia.data.fmp_client.fetch_ratios_ttm",
              return_value=ratios_ttm or _ratios_ttm()),
    ]


def _start_patches(patches):
    for p in patches:
        p.start()


def _stop_patches(patches):
    for p in patches:
        p.stop()


# ── derive_ttm_from_fmp: happy path ───────────────────────────────────

def test_derive_ttm_sums_four_quarters_for_flows():
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    assert result.skip_reason is None
    rec = result.record
    assert rec is not None
    assert rec.period == "TTM"
    assert rec.ticker == "AAPL"
    # 4 × 100B = 400B
    assert rec.raw["Revenue"] == 400e9
    assert rec.raw["NetIncome"] == 100e9
    # Cash flow sums
    assert rec.clean["FCF"] == 88e9
    assert rec.derived["EBITDA"] == 140e9


def test_derive_ttm_uses_latest_balance_for_stocks():
    """Stocks (TotalAssets, Equity) come from the LATEST balance only —
    not summed.  Multiplied stocks would be a P0 bug."""
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    rec = result.record
    assert rec.raw["TotalAssets"] == 400e9
    assert rec.raw["TotalEquity"] == 150e9
    # NetDebt = LTD + STD - Cash
    assert rec.derived["NetDebt"] == 80e9 + 10e9 - 30e9


def test_derive_ttm_stamps_source_for_forensic_swap():
    """ttm_source is observable so the eventual SEC primary swap is
    forensically traceable in the receipt."""
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    assert result.record.fmp_validation["ttm_source"] == "fmp_derived_quarters"


def test_derive_ttm_computes_basic_ratios():
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    rec = result.record
    # EBIT margin = 120B / 400B = 30%
    assert abs(rec.derived["EBIT_Margin_Pct"] - 30.0) < 0.01
    # ROE = 100B / 150B
    assert abs(rec.derived["ROE"] - (100e9 / 150e9)) < 1e-6
    # FCF margin = 88B / 400B = 22%
    assert abs(rec.derived["FCF_Margin_Pct"] - 22.0) < 0.01


# ── derive_ttm_from_fmp: skip paths ───────────────────────────────────

def test_skip_when_no_api_key():
    with patch("aletheia.data.fmp_client.has_api_key", return_value=False):
        r = derive_ttm_from_fmp("AAPL")
    assert r.record is None
    assert r.skip_reason == "fmp_api_key_not_configured"


def test_skip_when_fewer_than_four_quarters():
    income_3q = _quarterly_income()[:3]
    patches = _patch_fmp(income_3q, _balance_latest(), _quarterly_cashflow())
    _start_patches(patches)
    try:
        r = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)
    assert r.record is None
    assert "insufficient_quarters" in r.skip_reason


def test_foreign_currency_converts_to_usd():
    """Foreign filers (ASML EUR, TSM TWD) get every monetary value
    multiplied by the FY-average FX rate from fx_converter. Stamps
    reported_currency + fx_converted on the record receipt so the
    conversion is auditable."""
    patches = _patch_fmp(
        _quarterly_income(currency="EUR", fy=2025),
        _balance_latest(), _quarterly_cashflow(),
    )
    _start_patches(patches)
    try:
        r = derive_ttm_from_fmp("ASML")
    finally:
        _stop_patches(patches)

    assert r.record is not None, r.skip_reason
    assert r.skip_reason is None
    # FY2025 EUR/USD = 1.0950 → 4 × 100B EUR = 400B EUR → ~438B USD
    assert abs(r.record.raw["Revenue"] - 400e9 * 1.0950) < 1e6
    # Receipt forensics
    assert r.record.fmp_validation["reported_currency"] == "EUR"
    assert r.record.fmp_validation["fx_converted"] is True


def test_usd_filer_fx_metadata_marks_conversion_disabled():
    """USD filers skip the multiplication entirely. Receipt stamps
    fx_converted=False so analysts can confirm at a glance whether
    FX touched a record."""
    patches = _patch_fmp(_quarterly_income(currency="USD"),
                         _balance_latest(), _quarterly_cashflow())
    _start_patches(patches)
    try:
        r = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)
    assert r.record.fmp_validation["reported_currency"] == "USD"
    assert r.record.fmp_validation["fx_converted"] is False


def test_skip_when_required_flow_missing():
    income_no_revenue = _quarterly_income()
    for rec in income_no_revenue:
        rec["revenue"] = None
    patches = _patch_fmp(income_no_revenue, _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        r = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)
    assert r.record is None
    # With no quarter reporting real revenue, the quarter-selection guard
    # (which drops placeholder/zero-revenue quarters) short-circuits before
    # the required-flow check — a more precise skip reason for the same
    # "revenue flow missing" condition.
    assert "insufficient_real_quarters" in r.skip_reason


# ── Gate A.TTM cross-check ────────────────────────────────────────────

def test_gate_a_ttm_revenue_via_ev_implied_byte_perfect_when_consistent():
    """EV-implied lane: revenue = enterpriseValueTTM / evToSalesTTM.
    Stub default: 4000B / 10.0 = 400B, matches the quarterly-sum
    stub's $400B revenue. Standard tier; non-blocking by design."""
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=result.fmp_key_metrics_ttm,
        fmp_ratios_ttm=result.fmp_ratios_ttm,
    )
    # No P0 fields can breach (ev_implied is non-P0; ratios are
    # definitional and informational).
    assert gate["blocking_fields"] == []
    f = gate["fields"]["revenue_ttm"]
    assert f["status"] == "byte_perfect"
    assert f["fmp"] == 400e9       # 4000B / 10.0
    assert f["source_endpoint"] == "key_metrics_ttm:ev_implied"


def test_gate_a_ttm_revenue_drift_via_ev_implied_is_informational():
    """Inject ~7% drift in EV-implied revenue (evToSalesTTM tweaked).
    Standard tier surfaces structural_drift but never blocks — this is
    a regression detector, not a gate. The byte-perfect-required gate
    on absolute flows returns when SEC quarterly parsing ships and we
    have a true second source."""
    patches = _patch_fmp(
        _quarterly_income(), _balance_latest(), _quarterly_cashflow(),
        km_ttm=_km_ttm(ev_to_sales=10.7),  # implies 374B vs ours 400B
    )
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=result.fmp_key_metrics_ttm,
        fmp_ratios_ttm=result.fmp_ratios_ttm,
    )
    f = gate["fields"]["revenue_ttm"]
    assert f["status"] == "structural_drift"
    assert f["p0"] is False
    assert "revenue_ttm" not in gate["blocking_fields"]
    assert gate["status"] != "blocking_drift"


def test_gate_a_ttm_revenue_within_acceptable_band():
    """3% drift via EV-implied → standard tier marks acceptable, not
    structural_drift; lane stays informational."""
    patches = _patch_fmp(
        _quarterly_income(), _balance_latest(), _quarterly_cashflow(),
        km_ttm=_km_ttm(ev_to_sales=10.3),  # implies 388.3B vs ours 400B (~3%)
    )
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=result.fmp_key_metrics_ttm,
        fmp_ratios_ttm=result.fmp_ratios_ttm,
    )
    f = gate["fields"]["revenue_ttm"]
    # Standard tier: <1% byte_perfect, 1–5% acceptable, >5% structural
    assert f["status"] == "acceptable"
    assert "revenue_ttm" not in gate["blocking_fields"]


def test_gate_a_ttm_roic_drift_is_informational_not_p0():
    """ROIC formulas legitimately differ between sources — definitional
    tier, never blocks."""
    patches = _patch_fmp(
        _quarterly_income(), _balance_latest(), _quarterly_cashflow(),
        # ROIC now lives on key_metrics_ttm, not ratios_ttm. Inject 0.50
        # vs our computed ~0.30 (~67% drift on definitional tier).
        km_ttm=_km_ttm(roic=0.50),
    )
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=result.fmp_key_metrics_ttm,
        fmp_ratios_ttm=result.fmp_ratios_ttm,
    )
    assert "roic_ttm" not in gate["blocking_fields"]
    assert gate["status"] in ("validated", "drift")


def test_gate_a_ttm_skipped_when_ttm_blob_missing():
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=None, fmp_ratios_ttm=None,
    )
    assert gate["status"] == "skipped"
    assert gate["skip_reason"] == "fmp_ttm_blob_unavailable"


def test_gate_a_ttm_carries_ticker_for_caller_identification():
    """Result includes ticker so a caller aggregating gates across the
    universe can route by symbol without re-tracing."""
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=result.fmp_key_metrics_ttm,
        fmp_ratios_ttm=result.fmp_ratios_ttm,
    )
    assert gate["ticker"] == "AAPL"


# ── Phase Q-6 full second-source lanes ────────────────────────────────

def test_ttm_result_exposes_latest_quarter_income_for_phase_q6():
    """The latest contributing quarter's raw FMP record must be passed
    through TTMDerivationResult so Gate A.TTM Phase Q-6 can compare
    it against /income-statement-as-reported?period=quarter."""
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    assert result.latest_quarter_income is not None
    assert result.latest_quarter_income["revenue"] == 100e9
    assert result.latest_quarter_period_end == "2025-09-30"


def test_gate_a_ttm_ev_identity_byte_perfect():
    """FMP /enterprise-values?period=quarter latest record:
       implied_NetDebt = EV - mktCap.  Stub: EV=3T, mktCap=2.94T →
       implied=60B.  Our derived NetDebt = LTD+STD-Cash = 80+10-30 = 60B.
       Match within standard tier."""
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    fmp_ev_quarter = {
        "enterpriseValue":      3_000_000_000_000,
        "marketCapitalization": 2_940_000_000_000,
    }
    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=result.fmp_key_metrics_ttm,
        fmp_ratios_ttm=result.fmp_ratios_ttm,
        fmp_ev_latest_quarter=fmp_ev_quarter,
    )
    f = gate["fields"]["net_debt_ttm_via_ev_identity"]
    assert f["fmp"] == 60_000_000_000
    assert f["status"] == "byte_perfect"
    assert f["p0"] is False
    assert "net_debt_ttm_via_ev_identity" not in gate["blocking_fields"]


def test_gate_a_ttm_ev_identity_catches_netdebt_drift():
    """If our NetDebt diverges materially from FMP's EV-implied NetDebt,
    surface as drift on the second-source lane (still non-blocking — the
    primary /key-metrics.netDebt check at Gate A on the FY record owns
    blocking; this lane is a regression detector)."""
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    # Force our derived NetDebt to diverge from the FMP EV identity
    result.record.derived["NetDebt"] = 200_000_000_000  # 233% off
    fmp_ev_quarter = {
        "enterpriseValue":      3_000_000_000_000,
        "marketCapitalization": 2_940_000_000_000,
    }
    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=result.fmp_key_metrics_ttm,
        fmp_ratios_ttm=result.fmp_ratios_ttm,
        fmp_ev_latest_quarter=fmp_ev_quarter,
    )
    f = gate["fields"]["net_debt_ttm_via_ev_identity"]
    assert f["status"] == "structural_drift"
    assert "net_debt_ttm_via_ev_identity" not in gate["blocking_fields"]


def test_gate_a_ttm_ev_identity_n_a_when_endpoint_missing():
    """Caller doesn't pass /enterprise-values blob → field stamps n_a;
    no false drift signal."""
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=result.fmp_key_metrics_ttm,
        fmp_ratios_ttm=result.fmp_ratios_ttm,
        fmp_ev_latest_quarter=None,
    )
    assert gate["fields"]["net_debt_ttm_via_ev_identity"]["status"] == "n_a"


def test_gate_a_ttm_as_reported_byte_perfect_on_latest_quarter():
    """FMP /income-statement-as-reported?period=quarter latest record
    matches our latest quarter's revenue/NI byte-perfect — both arms
    trace to the same XBRL fact."""
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    fmp_as_reported = {
        "data": {
            # FMP returns XBRL tags lowercase under `data`
            "revenues":      100e9,
            "netincomeloss": 25e9,
        },
    }
    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=result.fmp_key_metrics_ttm,
        fmp_ratios_ttm=result.fmp_ratios_ttm,
        fmp_income_as_reported_quarter=fmp_as_reported,
        latest_quarter_income=result.latest_quarter_income,
    )
    assert gate["fields"]["revenue_latest_quarter_as_reported"]["status"] == "byte_perfect"
    assert gate["fields"]["net_income_latest_quarter_as_reported"]["status"] == "byte_perfect"


def test_gate_a_ttm_as_reported_falls_back_to_alt_xbrl_tag():
    """When `Revenues` is missing, fall back to the post-ASC-606 tag."""
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    fmp_as_reported = {
        "data": {
            "revenuefromcontractwithcustomerexcludingassessedtax": 100e9,
            "netincomeloss": 25e9,
        },
    }
    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=result.fmp_key_metrics_ttm,
        fmp_ratios_ttm=result.fmp_ratios_ttm,
        fmp_income_as_reported_quarter=fmp_as_reported,
        latest_quarter_income=result.latest_quarter_income,
    )
    f = gate["fields"]["revenue_latest_quarter_as_reported"]
    assert f["status"] == "byte_perfect"
    assert f["fmp_key_resolved"] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_gate_a_ttm_as_reported_catches_quarterly_tag_mapping_bug():
    """Inject a 4% drift between our latest-quarter revenue and the
    XBRL-tagged value.  Strict tier surfaces as structural_drift but
    NOT P0 (10-Q is unaudited; flag is informational)."""
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    fmp_as_reported = {
        "data": {
            "revenues":      96e9,   # 4% below our $100B
            "netincomeloss": 25e9,
        },
    }
    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=result.fmp_key_metrics_ttm,
        fmp_ratios_ttm=result.fmp_ratios_ttm,
        fmp_income_as_reported_quarter=fmp_as_reported,
        latest_quarter_income=result.latest_quarter_income,
    )
    f = gate["fields"]["revenue_latest_quarter_as_reported"]
    assert f["status"] == "structural_drift"
    assert "revenue_latest_quarter_as_reported" not in gate["blocking_fields"]


def test_gate_a_ttm_as_reported_n_a_when_endpoint_missing():
    """Quarterly as-reported endpoint not on plan → field stamps n_a."""
    patches = _patch_fmp(_quarterly_income(), _balance_latest(),
                         _quarterly_cashflow())
    _start_patches(patches)
    try:
        result = derive_ttm_from_fmp("AAPL")
    finally:
        _stop_patches(patches)

    gate = validate_ttm_record(
        "AAPL", result.record,
        fmp_key_metrics_ttm=result.fmp_key_metrics_ttm,
        fmp_ratios_ttm=result.fmp_ratios_ttm,
        fmp_income_as_reported_quarter=None,
        latest_quarter_income=result.latest_quarter_income,
    )
    assert gate["fields"]["revenue_latest_quarter_as_reported"]["status"] == "n_a"
    assert gate["fields"]["net_income_latest_quarter_as_reported"]["status"] == "n_a"


# ── Regression: IBM-class TTM assembly bugs (2026-07) ─────────────────
# Two real FMP data hazards, both reproduced on IBM's 2026-03-31 TTM:
#   1. FMP pre-seeds a placeholder row for the current, not-yet-reported
#      quarter (revenue=0). Blindly taking income[:4] includes it and
#      drops a real quarter → every flow understated by ~one quarter.
#   2. FMP flips the sign on quarterly capitalExpenditure across quarters;
#      abs(sum) lets them cancel (IBM: +391 −391 −908 +605 → 303M), so
#      capex must be summed as sum(abs) per quarter.

def _income_row(date, fy, period, revenue, ni):
    return {"date": date, "fiscalYear": str(fy), "period": period,
            "revenue": revenue, "netIncome": ni,
            "operatingIncome": 30e9, "ebitda": 35e9,
            "costOfRevenue": 60e9, "researchAndDevelopmentExpenses": 8e9,
            "interestExpense": 1e9, "weightedAverageShsOutDil": 15e9,
            "reportedCurrency": "USD"}


def _cf_row(date, capex, ocf=28e9, fcf=22e9):
    return {"date": date, "operatingCashFlow": ocf, "freeCashFlow": fcf,
            "capitalExpenditure": capex, "stockBasedCompensation": 2e9}


def test_drops_placeholder_future_quarter():
    """A revenue=0 placeholder at the array head is skipped; the TTM sums
    the 4 most-recent REAL quarters and aligns cash-flow to them."""
    income = [
        _income_row("2026-06-30", 2026, "Q2", 0.0, 0.0),      # phantom
        _income_row("2026-03-31", 2026, "Q1", 100e9, 25e9),
        _income_row("2025-12-31", 2025, "Q4", 100e9, 25e9),
        _income_row("2025-09-30", 2025, "Q3", 100e9, 25e9),
        _income_row("2025-06-30", 2025, "Q2", 100e9, 25e9),
    ]
    cashflow = [
        _cf_row("2026-06-30", -6e9),   # phantom cash-flow carries data
        _cf_row("2026-03-31", -6e9),
        _cf_row("2025-12-31", -6e9),
        _cf_row("2025-09-30", -6e9),
        _cf_row("2025-06-30", -6e9),
    ]
    balance = [{"date": "2026-03-31", "totalAssets": 400e9,
                "totalLiabilities": 250e9, "totalStockholdersEquity": 150e9,
                "cashAndCashEquivalents": 30e9, "longTermDebt": 80e9,
                "shortTermDebt": 10e9, "commonStockSharesOutstanding": 15e9}]
    patches = _patch_fmp(income, balance, cashflow)
    _start_patches(patches)
    try:
        r = derive_ttm_from_fmp("IBM")
    finally:
        _stop_patches(patches)
    assert r.record is not None
    # 4 real quarters, NOT 3 real + 1 zero.
    assert r.record.raw["Revenue"] == 400e9
    assert r.record.raw["NetIncome"] == 100e9
    assert r.record.period_end_date == "2026-03-31"
    assert r.record.fmp_validation["ttm_quarters"] == [
        "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"]


def test_capex_sign_flips_do_not_cancel():
    """Mixed-sign quarterly capex must sum by magnitude (sum(abs)), not
    abs(sum) — otherwise IBM's +391/−391/−908/+605 collapses to ~0."""
    income = [
        _income_row("2026-03-31", 2026, "Q1", 100e9, 25e9),
        _income_row("2025-12-31", 2025, "Q4", 100e9, 25e9),
        _income_row("2025-09-30", 2025, "Q3", 100e9, 25e9),
        _income_row("2025-06-30", 2025, "Q2", 100e9, 25e9),
    ]
    # Signs alternate; abs(sum) = 0, sum(abs) = 24e9. FMP's freeCashFlow
    # field (22e9/q → 88e9) is deliberately inconsistent with OCF−CapEx
    # (120e9 − 24e9 = 96e9) so the test proves FCF is RECOMPUTED, not the
    # FMP field passed through.
    cashflow = [
        _cf_row("2026-03-31", -6e9, ocf=30e9),
        _cf_row("2025-12-31", +6e9, ocf=30e9),
        _cf_row("2025-09-30", -6e9, ocf=30e9),
        _cf_row("2025-06-30", +6e9, ocf=30e9),
    ]
    balance = [{"date": "2026-03-31", "totalAssets": 400e9,
                "totalLiabilities": 250e9, "totalStockholdersEquity": 150e9,
                "cashAndCashEquivalents": 30e9, "longTermDebt": 80e9,
                "shortTermDebt": 10e9, "commonStockSharesOutstanding": 15e9}]
    patches = _patch_fmp(income, balance, cashflow)
    _start_patches(patches)
    try:
        r = derive_ttm_from_fmp("IBM")
    finally:
        _stop_patches(patches)
    assert r.record is not None
    assert r.record.derived["CapEx"] == 24e9            # sum(abs), not abs(sum)=0
    # FCF recomputed as OCF − CapEx (120e9 − 24e9), NOT FMP's field sum (88e9).
    assert r.record.derived["FCF"] == 96e9
    recon = r.record.fmp_validation["fcf_reconciliation"]
    assert recon["fcf_computed_ocf_minus_capex"] == 96e9
    assert recon["fmp_fcf_field_sum"] == 88e9           # 4×22e9 — the wrong value we rejected
    assert recon["divergence_pct"] is not None          # mismatch surfaced on the receipt


# ── EBIT/EBITDA backfill from FMP (filer-quirk completeness) ──────────
# Filers like IBM/ACN/KO/MCO don't tag OperatingIncomeLoss in XBRL, so
# the SEC-derived TTM has OperatingIncome=None → EBITDA=None → blank
# EBIT/Norm-EBIT/EBITDA columns. backfill_ebit_ebitda_from_fmp grafts
# those two fields from the (period-aligned) FMP-derived TTM.

from aletheia.data.ttm_derivation import backfill_ebit_ebitda_from_fmp
from aletheia.data.cleaning_engine import CleanedRecord


def _sec_like_record(pe="2026-03-31", revenue=68.9e9, op_inc=None, ebitda=None):
    r = CleanedRecord(ticker="IBM", fiscal_year=2026, period="TTM",
                      period_end_date=pe)
    r.raw = {"Revenue": revenue, "OperatingIncome": op_inc}
    r.clean = {"Revenue": revenue, "EBITDA": ebitda}
    r.derived = {"OperatingIncome": op_inc, "EBITDA": ebitda}
    return r


def _fmp_like_record(pe="2026-03-31", op_inc=12.13e9, ebitda=17.63e9):
    r = CleanedRecord(ticker="IBM", fiscal_year=2026, period="TTM",
                      period_end_date=pe)
    r.raw = {"Revenue": 68.9e9, "OperatingIncome": op_inc}
    r.clean = {"EBITDA": ebitda}
    r.derived = {"OperatingIncome": op_inc, "EBITDA": ebitda}
    return r


def test_backfill_grafts_ebit_ebitda_when_sec_missing():
    sec = _sec_like_record(op_inc=None, ebitda=None)
    filled = backfill_ebit_ebitda_from_fmp(sec, _fmp_like_record())
    assert set(filled) == {"OperatingIncome", "EBITDA"}
    assert sec.raw["OperatingIncome"] == 12.13e9
    assert sec.derived["OperatingIncome"] == 12.13e9
    assert sec.clean["EBITDA"] == 17.63e9
    assert sec.derived["EBITDA"] == 17.63e9
    # margins recomputed on the PRIMARY record's own revenue
    assert abs(sec.derived["EBIT_Margin_Pct"] - (12.13e9 / 68.9e9 * 100)) < 1e-6
    assert abs(sec.derived["EBITDA_Margin_Pct"] - (17.63e9 / 68.9e9 * 100)) < 1e-6


def test_backfill_skips_on_period_mismatch():
    sec = _sec_like_record(pe="2026-06-30", op_inc=None, ebitda=None)
    filled = backfill_ebit_ebitda_from_fmp(sec, _fmp_like_record(pe="2026-03-31"))
    assert filled == []
    assert sec.raw["OperatingIncome"] is None


def test_backfill_never_overwrites_existing_sec_values():
    sec = _sec_like_record(op_inc=11.0e9, ebitda=16.0e9)
    filled = backfill_ebit_ebitda_from_fmp(sec, _fmp_like_record())
    assert filled == []
    assert sec.raw["OperatingIncome"] == 11.0e9   # untouched
    assert sec.clean["EBITDA"] == 16.0e9


def test_backfill_noop_without_fmp_record():
    sec = _sec_like_record(op_inc=None, ebitda=None)
    assert backfill_ebit_ebitda_from_fmp(sec, None) == []


# ── A=L+E reconciliation: NCI attribution (FMP parent-only equity) ────
# FMP's totalStockholdersEquity excludes noncontrolling interest, so
# A = L + E misses by the NCI amount for filers like WMT (~2.3%) / MDT
# (~0.7%). The derivation attributes a small positive residual to
# MinorityInterest so the A=L+E schema contract's NCI form holds.

def _balance(a, l, e):
    return [{"date": "2026-03-31", "totalAssets": a, "totalLiabilities": l,
             "totalStockholdersEquity": e, "cashAndCashEquivalents": 10e9,
             "longTermDebt": 20e9, "shortTermDebt": 5e9,
             "commonStockSharesOutstanding": 15e9}]


def _derive_with_balance(balance):
    patches = _patch_fmp(_quarterly_income(), balance, _quarterly_cashflow())
    _start_patches(patches)
    try:
        return derive_ttm_from_fmp("WMT")
    finally:
        _stop_patches(patches)


def test_nci_residual_attributed_to_minority_interest():
    # A=100, L=70, E=27 → residual 3 (3%) → NCI, contract's NCI form holds
    r = _derive_with_balance(_balance(100e9, 70e9, 27e9)).record
    assert r.raw["MinorityInterest"] == 3e9
    # L and E stay as-reported (NCI not folded into liabilities)
    assert r.raw["TotalLiabilities"] == 70e9
    assert r.raw["TotalEquity"] == 27e9


def test_no_nci_when_balance_reconciles():
    r = _derive_with_balance(_balance(100e9, 70e9, 30e9)).record
    assert r.raw["MinorityInterest"] is None


def test_large_residual_not_attributed_to_nci():
    # 10% gap is a real data error, not NCI — left for the contract to catch
    r = _derive_with_balance(_balance(100e9, 60e9, 30e9)).record
    assert r.raw["MinorityInterest"] is None


def test_negative_residual_not_attributed_to_nci():
    # A < L+E can't be NCI (which is positive) — left alone
    r = _derive_with_balance(_balance(100e9, 80e9, 30e9)).record
    assert r.raw["MinorityInterest"] is None
