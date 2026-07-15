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


def _sum_abs_or_none(records: List[Dict[str, Any]], key: str) -> Optional[float]:
    """Sum the MAGNITUDE of `key` across records (abs per quarter, then
    sum). FMP reports some flow items — notably ``capitalExpenditure`` —
    with an inconsistent sign across quarters for the same filer (e.g.
    IBM: +391M, −391M, −908M, +605M). Summing raw and then abs() lets the
    signs cancel (→ 303M instead of ~2.3B). Taking abs() per quarter first
    is the correct aggregation for a magnitude-convention field."""
    vals: List[float] = []
    for r in records:
        v = _f(r.get(key))
        if v is None:
            return None
        vals.append(abs(v))
    return sum(vals) if vals else None


def _q_date(record: Dict[str, Any]) -> str:
    """Period-end date (YYYY-MM-DD) for a quarterly FMP statement row."""
    return (record.get("date") or "")[:10]


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

    # ── Select the 4 most-recent COMPLETE quarters ──────────────────────
    # FMP returns most-recent first, but pre-seeds a row for the current,
    # not-yet-reported quarter with revenue=0 / netIncome=0 (e.g. IBM on
    # 2026-07-14 carries an empty 2026-06-30 Q2 row). Blindly taking
    # ``[:4]`` includes that placeholder and drops a real quarter — the
    # TTM then sums 3 real quarters + 1 zero, understating every flow by
    # ~one quarter. Filter to quarters that report real revenue, then
    # align income / cash-flow / balance to that SAME set of period-end
    # dates so every line item covers identical quarters.
    real_income = [r for r in income_q if (_f(r.get("revenue")) or 0.0) > 0.0]
    if len(real_income) < 4:
        return TTMDerivationResult(
            None, km_ttm, ratios_ttm,
            f"insufficient_real_quarters:income_with_revenue={len(real_income)}",
        )
    income_last4 = real_income[:4]
    target_dates = [_q_date(r) for r in income_last4]

    cashflow_by_date = {_q_date(r): r for r in cashflow_q}
    cashflow_last4 = [cashflow_by_date[d] for d in target_dates if d in cashflow_by_date]
    if len(cashflow_last4) < 4:
        return TTMDerivationResult(
            None, km_ttm, ratios_ttm,
            f"cashflow_misaligned:matched={len(cashflow_last4)}/4 for {target_dates}",
        )

    # Balance sheet: the latest quarter aligned to the TTM's end date
    # (target_dates[0]), not FMP's array head, which may be the phantom.
    balance_by_date = {_q_date(r): r for r in balance_q}
    balance_latest = balance_by_date.get(target_dates[0], balance_q[0])

    # Currency conversion — foreign-reporting filers (ASML EUR, TSM TWD,
    # etc.) get every monetary value multiplied by the FY-average FX
    # rate from `aletheia.data.fx_converter`. Shares + ratios are
    # currency-invariant and pass through untouched.
    fmp_ccy = (income_last4[0].get("reportedCurrency") or "").upper()
    fy_str  = str(income_last4[0].get("fiscalYear") or income_last4[0].get("calendarYear") or "")
    fy_for_fx = int(fy_str) if fy_str.isdigit() else 0

    def _fx(value: Optional[float]) -> Optional[float]:
        """Convert value to USD using FY-average FX. None pass-through."""
        if value is None:
            return None
        if not fmp_ccy or fmp_ccy == "USD":
            return value
        from aletheia.data.fx_converter import convert_to_usd
        return convert_to_usd(value, fmp_ccy, fy_for_fx)

    # ── Flow items (TTM = sum of last four quarters, then FX) ───────────
    revenue          = _fx(_sum_or_none(income_last4,   "revenue"))
    net_income       = _fx(_sum_or_none(income_last4,   "netIncome"))
    operating_income = _fx(_sum_or_none(income_last4,   "operatingIncome"))
    ebitda           = _fx(_sum_or_none(income_last4,   "ebitda"))
    cogs             = _fx(_sum_or_none(income_last4,   "costOfRevenue"))
    rnd              = _fx(_sum_or_none(income_last4,   "researchAndDevelopmentExpenses"))
    interest_expense = _fx(_sum_or_none(income_last4,   "interestExpense"))
    operating_cf     = _fx(_sum_or_none(cashflow_last4, "operatingCashFlow"))
    # FMP reports capitalExpenditure as a (nominally negative) cash
    # outflow but flips the sign inconsistently across quarters. Schema
    # convention stores it POSITIVE (magnitude). Sum abs() PER QUARTER so
    # sign flips can't cancel (see _sum_abs_or_none). FY cleaning engine
    # applies abs() at cleaning_engine.py:1381 / :1672.
    capex            = _fx(_sum_abs_or_none(cashflow_last4, "capitalExpenditure"))
    # FCF: recompute as OCF − CapEx rather than trusting FMP's summed
    # ``freeCashFlow`` field, which inherits the same sign/placeholder
    # inconsistencies (and would put FCF > EBITDA). Keep FMP's sum for a
    # reconciliation flag; fall back to it only if OCF or CapEx is absent.
    fmp_fcf_field    = _fx(_sum_or_none(cashflow_last4, "freeCashFlow"))
    if operating_cf is not None and capex is not None:
        fcf = operating_cf - capex
    else:
        fcf = fmp_fcf_field
    sbc              = _fx(_sum_or_none(cashflow_last4, "stockBasedCompensation"))
    # D&A is required by the DCF engine. Sum across quarters; some FMP
    # responses only expose it on the income statement.
    da_total         = _fx(
        _sum_or_none(cashflow_last4, "depreciationAndAmortization")
        or _sum_or_none(income_last4, "depreciationAndAmortization")
    )

    if revenue is None or net_income is None:
        return TTMDerivationResult(
            None, km_ttm, ratios_ttm, "missing_required_flows:revenue_or_net_income",
        )

    # ── Stock items (latest balance sheet, then FX) ─────────────────────
    total_assets       = _fx(_f(balance_latest.get("totalAssets")))
    total_liabilities  = _fx(_f(balance_latest.get("totalLiabilities")))
    total_equity       = _fx(_f(balance_latest.get("totalStockholdersEquity")))
    cash               = _fx(_f(balance_latest.get("cashAndCashEquivalents")))
    long_term_debt     = _fx(_f(balance_latest.get("longTermDebt")))
    short_term_debt    = _fx(_f(balance_latest.get("shortTermDebt")))
    # Shares + ratios are unit-invariant; no FX needed.
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
    # FCF reconciliation: computed (OCF−CapEx) vs FMP's summed field.
    # A large divergence usually means FMP's freeCashFlow field carried a
    # sign flip or a phantom quarter — worth surfacing on the receipt.
    fcf_divergence_pct = None
    if fcf and fmp_fcf_field is not None:
        fcf_divergence_pct = round((fmp_fcf_field - fcf) / abs(fcf) * 100.0, 1)

    record.fmp_validation = {
        "status":      "validated",  # provisional; tightened by Gate A.TTM
        "ttm_source":  "fmp_derived_quarters",
        "reported_currency": fmp_ccy or "USD",
        "fx_converted":      bool(fmp_ccy and fmp_ccy != "USD"),
        # Forensics: exactly which 4 quarter-end dates fed this TTM, so a
        # dropped/placeholder quarter is auditable from the receipt.
        "ttm_quarters":      target_dates,
        "fcf_reconciliation": {
            "fcf_computed_ocf_minus_capex": fcf,
            "fmp_fcf_field_sum":            fmp_fcf_field,
            "divergence_pct":               fcf_divergence_pct,
        },
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


def backfill_ebit_ebitda_from_fmp(
    primary: Optional[CleanedRecord],
    fmp_record: Optional[CleanedRecord],
) -> List[str]:
    """Graft OperatingIncome (EBIT) + EBITDA from the FMP-derived TTM onto
    a primary (SEC-derived) record that couldn't resolve them.

    Motivation: some filers don't tag ``OperatingIncomeLoss`` in XBRL
    (IBM, ACN, KO, MCO), so the SEC path returns ``OperatingIncome=None``
    → ``EBITDA=None`` → the Multi-year-history EBIT / Norm-EBIT / EBITDA
    columns render blank on the TTM row. Rather than reconstruct EBIT from
    pretax+interest and EBITDA from cash-flow D&A — which understates EBITDA
    for filers with large intangible amortization (IBM: ~$14.8B vs the
    correct ~$17.6B) — reuse FMP's own operatingIncome / ebitda summed over
    the same quarters.

    Guards:
      - only fills fields the primary is MISSING (never overwrites SEC data);
      - only when both records cover the SAME period-end (else the TTM
        windows differ and the graft would be inconsistent);
      - margins are recomputed on the PRIMARY record's own revenue.

    Mutates ``primary`` in place. Returns the list of fields backfilled
    (empty when nothing was grafted) so the caller can stamp provenance
    after Gate A.TTM overwrites ``fmp_validation``.
    """
    if primary is None or fmp_record is None:
        return []
    if (primary.period_end_date or "") != (fmp_record.period_end_date or ""):
        return []

    rev = _f(primary.raw.get("Revenue")) or _f(primary.clean.get("Revenue"))
    filled: List[str] = []

    have_ebit = (
        primary.raw.get("OperatingIncome") is not None
        or primary.derived.get("OperatingIncome") is not None
    )
    fmp_ebit = _f(fmp_record.raw.get("OperatingIncome"))
    if not have_ebit and fmp_ebit is not None:
        primary.raw["OperatingIncome"] = fmp_ebit
        primary.derived["OperatingIncome"] = fmp_ebit
        if rev:
            primary.derived["EBIT_Margin_Pct"] = fmp_ebit / rev * 100.0
        filled.append("OperatingIncome")

    have_ebitda = (
        primary.derived.get("EBITDA") is not None
        or primary.clean.get("EBITDA") is not None
    )
    fmp_ebitda = _f(fmp_record.clean.get("EBITDA"))
    if fmp_ebitda is None:
        fmp_ebitda = _f(fmp_record.derived.get("EBITDA"))
    if not have_ebitda and fmp_ebitda is not None:
        primary.derived["EBITDA"] = fmp_ebitda
        primary.clean["EBITDA"] = fmp_ebitda
        if rev:
            primary.derived["EBITDA_Margin_Pct"] = fmp_ebitda / rev * 100.0
        filled.append("EBITDA")

    return filled
