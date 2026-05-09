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


def _km_ttm(rev_per_share=400/15, ni_per_share=100/15, fcf_per_share=88/15):
    return {
        "revenuePerShareTTM":     rev_per_share,
        "netIncomePerShareTTM":   ni_per_share,
        "freeCashFlowPerShareTTM": fcf_per_share,
    }


def _ratios_ttm(roic=0.30, roe=0.65, op_margin=0.30):
    return {
        "returnOnInvestedCapitalTTM": roic,
        "returnOnEquityTTM":          roe,
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

def test_gate_a_ttm_byte_perfect_when_quarters_sum_to_fmp_ttm():
    """Our $400B TTM revenue == 4 × $100B quarters == FMP per-share × shares
    ($26.67/sh × 15B sh = $400B). All P0 fields byte_perfect; never
    blocking even if a definitional-tier field (ROIC) drifts."""
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
    # P0 fields agree byte-perfect; nothing should be in blocking_fields
    assert gate["blocking_fields"] == []
    assert gate["fields"]["revenue_ttm"]["status"] == "byte_perfect"
    assert gate["fields"]["net_income_ttm"]["status"] == "byte_perfect"
    assert gate["fields"]["fcf_ttm"]["status"] == "byte_perfect"


def test_gate_a_ttm_blocks_on_revenue_drift_above_half_pct():
    """Inject 1% drift in FMP's TTM revenue.  Both arms are FMP — drift
    means FMP-internal inconsistency.  P0 escalates to blocking_drift."""
    patches = _patch_fmp(
        _quarterly_income(), _balance_latest(), _quarterly_cashflow(),
        km_ttm=_km_ttm(rev_per_share=(400e9 * 1.01) / 15e9),  # 1% drift
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
    assert gate["status"] == "blocking_drift"
    assert "revenue_ttm" in gate["blocking_fields"]
    assert gate["fields"]["revenue_ttm"]["p0"] is True


def test_gate_a_ttm_within_half_pct_passes():
    """0.3% drift is inside the 0.5% byte_perfect_required band — the
    revenue field stays byte_perfect and is NOT in blocking_fields,
    even if a definitional-tier field independently drifts."""
    patches = _patch_fmp(
        _quarterly_income(), _balance_latest(), _quarterly_cashflow(),
        km_ttm=_km_ttm(rev_per_share=(400e9 * 1.003) / 15e9),  # 0.3%
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
    assert gate["fields"]["revenue_ttm"]["status"] == "byte_perfect"
    assert "revenue_ttm" not in gate["blocking_fields"]


def test_gate_a_ttm_roic_drift_is_informational_not_p0():
    """ROIC formulas legitimately differ between sources — definitional
    tier, never blocks."""
    patches = _patch_fmp(
        _quarterly_income(), _balance_latest(), _quarterly_cashflow(),
        ratios_ttm=_ratios_ttm(roic=0.50),  # we computed ~0.30; large diff
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
