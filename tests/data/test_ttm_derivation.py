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


def test_skip_when_foreign_currency():
    """Foreign filers (ASML EUR, TSM TWD) skipped pending FX support."""
    patches = _patch_fmp(_quarterly_income(currency="EUR"),
                         _balance_latest(), _quarterly_cashflow())
    _start_patches(patches)
    try:
        r = derive_ttm_from_fmp("ASML")
    finally:
        _stop_patches(patches)
    assert r.record is None
    assert r.skip_reason == "fmp_currency_mismatch:EUR"


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
    assert "missing_required_flows" in r.skip_reason


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
