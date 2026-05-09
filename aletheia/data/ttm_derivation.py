"""Phase Q-4: derive a trailing-twelve-month CleanedRecord from FMP.

Sums FMP's last four quarterly statements (income / cash-flow) for
flow items, takes the latest quarter's balance sheet for stock items,
and computes the standard ratios inline so the TTM record has the same
shape as an FY record. Persists to DB with `period='TTM'`.

In the redefined MVP (per the locked plan):
  - This is the *primary* TTM source. Phase Q-2 (deferred) will add an
    SEC-derived TTM and Gate A.TTM byte-perfect cross-check.
  - Until then, the receipt stamps `ttm_source="fmp_derived_quarters"`
    so the source-primacy swap when SEC quarterly lands is observable.

Cross-check inside MVP: our quarterly-sum TTM is compared to FMP's
pre-computed `/key-metrics-ttm` and `/ratios-ttm`. Both arms are FMP
internally, so this catches FMP-internal inconsistencies (rare) — not
a substitute for the eventual SEC vs FMP gate, but a useful structural
sanity check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from aletheia.data import fmp_client
from aletheia.data.cleaning_engine import CleanedRecord


def _f(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_or_none(records: List[Dict[str, Any]], key: str) -> Optional[float]:
    """Sum `key` across records. Returns None if any value is missing —
    we don't silently substitute zero because that'd mask data gaps."""
    vals: List[float] = []
    for r in records:
        v = _f(r.get(key))
        if v is None:
            return None
        vals.append(v)
    return sum(vals) if vals else None


@dataclass
class TTMDerivationResult:
    """Wrapper around the produced CleanedRecord plus the FMP TTM blobs
    used by Gate A.TTM. `latest_quarter_income` is the raw FMP
    quarterly income record for the most recent contributing quarter —
    Gate A.TTM (Phase Q-6 full) cross-checks this against FMP's
    as-reported XBRL endpoint to verify our quarterly source data
    aligns with the actual filed numbers."""
    record: Optional[CleanedRecord]
    fmp_key_metrics_ttm: Optional[Dict[str, Any]]
    fmp_ratios_ttm: Optional[Dict[str, Any]]
    skip_reason: Optional[str]
    latest_quarter_income: Optional[Dict[str, Any]] = None
    latest_quarter_period_end: Optional[str] = None


def derive_ttm_from_fmp(ticker: str) -> TTMDerivationResult:
    """Build a TTM CleanedRecord by summing FMP's last four quarterly
    statements. Returns a result wrapper; `record` is None if any
    required series is missing.

    Source-primacy: the produced record carries `period='TTM'` and is
    flagged via the `fmp_validation` block as the FMP-derived TTM. When
    SEC quarterly parsing ships (deferred Phase Q-2), the new SEC-
    derived TTM will become primary and this output will move to a
    cross-check role.
    """
    if not fmp_client.has_api_key():
        return TTMDerivationResult(None, None, None, "fmp_api_key_not_configured")

    try:
        income_q   = fmp_client.fetch_income_statements(ticker, period="quarter")
        balance_q  = fmp_client.fetch_balance_sheets(ticker, period="quarter")
        cashflow_q = fmp_client.fetch_cash_flows(ticker, period="quarter")
        km_ttm     = fmp_client.fetch_key_metrics_ttm(ticker)
        ratios_ttm = fmp_client.fetch_ratios_ttm(ticker)
    except Exception as exc:
        return TTMDerivationResult(
            None, None, None, f"fmp_network_error:{type(exc).__name__}"
        )

    if not (income_q and balance_q and cashflow_q):
        return TTMDerivationResult(None, km_ttm, ratios_ttm, "fmp_quarterly_unavailable")
    if len(income_q) < 4 or len(cashflow_q) < 4:
        return TTMDerivationResult(
            None, km_ttm, ratios_ttm,
            f"insufficient_quarters:income={len(income_q)},cashflow={len(cashflow_q)}",
        )

    # FMP returns most-recent first; take the latest 4 quarters
    income_last4   = income_q[:4]
    cashflow_last4 = cashflow_q[:4]
    balance_latest = balance_q[0]

    # Currency check — match Gate A's policy: foreign-reporting filers
    # are skipped pending Phase 6 FX support.
    fmp_ccy = (income_last4[0].get("reportedCurrency") or "").upper()
    if fmp_ccy and fmp_ccy != "USD":
        return TTMDerivationResult(
            None, km_ttm, ratios_ttm, f"fmp_currency_mismatch:{fmp_ccy}"
        )

    # ── Flow items (TTM = sum of last four quarters) ────────────────────
    revenue          = _sum_or_none(income_last4,   "revenue")
    net_income       = _sum_or_none(income_last4,   "netIncome")
    operating_income = _sum_or_none(income_last4,   "operatingIncome")
    ebitda           = _sum_or_none(income_last4,   "ebitda")
    cogs             = _sum_or_none(income_last4,   "costOfRevenue")
    rnd              = _sum_or_none(income_last4,   "researchAndDevelopmentExpenses")
    interest_expense = _sum_or_none(income_last4,   "interestExpense")
    operating_cf     = _sum_or_none(cashflow_last4, "operatingCashFlow")
    fcf              = _sum_or_none(cashflow_last4, "freeCashFlow")
    capex            = _sum_or_none(cashflow_last4, "capitalExpenditure")
    sbc              = _sum_or_none(cashflow_last4, "stockBasedCompensation")
    # D&A is required by the DCF engine. Sum across quarters; some FMP
    # responses only expose it on the income statement.
    da_total         = (
        _sum_or_none(cashflow_last4, "depreciationAndAmortization")
        or _sum_or_none(income_last4, "depreciationAndAmortization")
    )

    if revenue is None or net_income is None:
        return TTMDerivationResult(
            None, km_ttm, ratios_ttm, "missing_required_flows:revenue_or_net_income",
        )

    # ── Stock items (latest balance sheet) ──────────────────────────────
    total_assets       = _f(balance_latest.get("totalAssets"))
    total_liabilities  = _f(balance_latest.get("totalLiabilities"))
    total_equity       = _f(balance_latest.get("totalStockholdersEquity"))
    cash               = _f(balance_latest.get("cashAndCashEquivalents"))
    long_term_debt     = _f(balance_latest.get("longTermDebt"))
    short_term_debt    = _f(balance_latest.get("shortTermDebt"))
    shares_diluted     = _f(income_last4[0].get("weightedAverageShsOutDil"))
    shares_outstanding = _f(balance_latest.get("commonStockSharesOutstanding"))

    # NetDebt: enterprise-value definition (gross debt minus cash)
    if long_term_debt is not None or short_term_debt is not None:
        net_debt = (long_term_debt or 0.0) + (short_term_debt or 0.0) - (cash or 0.0)
    else:
        net_debt = None

    # ── Ratios computed inline (same formulas as cleaning_engine) ───────
    gross_margin_pct = (
        ((revenue - cogs) / revenue) * 100.0
        if cogs is not None and revenue else None
    )
    ebit_margin_pct = (
        (operating_income / revenue) * 100.0
        if operating_income is not None and revenue else None
    )
    ebitda_margin_pct = (
        (ebitda / revenue) * 100.0
        if ebitda is not None and revenue else None
    )
    fcf_margin_pct = (
        (fcf / revenue) * 100.0
        if fcf is not None and revenue else None
    )

    # ROE: NetIncome / TotalEquity (decimal, suppressed on negative equity)
    roe = (
        (net_income / total_equity)
        if total_equity is not None and total_equity > 0 else None
    )

    # ROIC: NOPAT / InvestedCapital. NOPAT ≈ EBIT × (1 - effective tax).
    # Effective tax from FMP TTM ratios when available; fallback 25%.
    eff_tax = None
    if ratios_ttm:
        eff_tax = _f(ratios_ttm.get("effectiveTaxRateTTM"))
    if eff_tax is None or not (0.0 <= eff_tax <= 0.6):
        eff_tax = 0.25

    invested_capital = None
    if total_equity is not None and (long_term_debt or short_term_debt) is not None:
        invested_capital = (
            (total_equity or 0.0)
            + (long_term_debt or 0.0)
            + (short_term_debt or 0.0)
            - (cash or 0.0)
        )
    roic = None
    if operating_income is not None and invested_capital and invested_capital > 0:
        nopat = operating_income * (1.0 - eff_tax)
        roic = nopat / invested_capital

    # ── Derive period_end_date + fiscal_year from latest quarter ────────
    period_end_date = (balance_latest.get("date") or income_last4[0].get("date") or "")[:10] or None
    fy_str = str(income_last4[0].get("fiscalYear") or income_last4[0].get("calendarYear") or "")
    fiscal_year = int(fy_str) if fy_str.isdigit() else 0

    # ── Build the CleanedRecord with period='TTM' ───────────────────────
    record = CleanedRecord(
        ticker=ticker.upper(),
        fiscal_year=fiscal_year,
        period="TTM",
        period_end_date=period_end_date,
    )
    record.raw = {
        "Revenue":             revenue,
        "NetIncome":           net_income,
        "OperatingIncome":     operating_income,
        "TotalAssets":         total_assets,
        "TotalLiabilities":    total_liabilities,
        "TotalEquity":         total_equity,
        "Cash":                cash,
        "LongTermDebt":        long_term_debt,
        "ShortTermDebt":       short_term_debt,
        "CapEx":               capex,
        "OperatingCF":         operating_cf,
        "COGS":                cogs,
        "R&D":                 rnd,
        "InterestExpense":     interest_expense,
        "SharesDiluted":       shares_diluted,
        "SharesOutstanding":   shares_outstanding,
    }
    record.clean = {
        "Revenue":      revenue,
        "FCF":          fcf,
        "EBITDA":       ebitda,
        "SBC":          sbc,
        "SharesDiluted": shares_diluted,
    }
    record.derived = {
        "EBITDA":             ebitda,
        "OperatingIncome":    operating_income,
        "CapEx":              capex,
        "FCF":                fcf,
        "ROIC":               roic,
        "ROE":                roe,
        "NetDebt":            net_debt,
        "InvestedCapital":    invested_capital,
        "GrossMargin_Pct":    gross_margin_pct,
        "EBIT_Margin_Pct":    ebit_margin_pct,
        "EBITDA_Margin_Pct":  ebitda_margin_pct,
        "FCF_Margin_Pct":     fcf_margin_pct,
        # Required by DCFEngine — sum of quarterly D&A. Falls back to
        # latest-FY value via the merged-row resolver in dcf_engine.run()
        # if FMP doesn't expose D&A on quarterly statements.
        "Depreciation_Total": da_total,
    }
    record.overall_quality_score = 1.0  # provisional; Gate A.TTM updates it

    # Stamp source-primacy on the record's fmp_validation block for the
    # Gate D receipt and forensics. Real Gate A.TTM cross-check happens
    # in `validate_ttm_against_fmp` (called separately so the validator
    # can decide blocking vs informational).
    record.fmp_validation = {
        "status":      "validated",  # provisional; tightened by Gate A.TTM
        "ttm_source":  "fmp_derived_quarters",
        "fields":      {},
    }

    # Latest contributing quarter — passed through so Gate A.TTM can
    # cross-check our quarterly source against FMP's as-reported XBRL
    # endpoint (Phase Q-6 full).
    latest_quarter_income = income_last4[0] if income_last4 else None
    latest_quarter_period_end = (
        (latest_quarter_income or {}).get("date") or ""
    )[:10] or None

    return TTMDerivationResult(
        record=record,
        fmp_key_metrics_ttm=km_ttm,
        fmp_ratios_ttm=ratios_ttm,
        skip_reason=None,
        latest_quarter_income=latest_quarter_income,
        latest_quarter_period_end=latest_quarter_period_end,
    )
