"""FMP → ``ValidatedCleanedRecord`` adapter.

Pulls FMP income / balance sheet / cash flow / key metrics for a ticker
and produces a list of ``ValidatedCleanedRecord`` ready for Stage 3
consumption. Used by ``run_stage3_isolated`` to validate the calc layer
against an independent data source.

What is NOT validated by this path:
  - SEC XBRL ingestion / Stage 1
  - cleaning_engine field harmonisation / Stage 2
  - cross-source agreement gates
Stage 3 sees only FMP's view of the company.

Field mapping ``_FMP_FIELD_MAP`` is the authority on which FMP keys map
to which canonical names. Sign conventions follow our canonical:
  * CapEx stored as the magnitude (abs of FMP's negative value)
  * InvestingCF / FinancingCF kept at FMP's reported sign
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aletheia.contracts.pipeline import (
    ValidatedCleanedRecord,
    ValidationReceipt,
)
from aletheia.data import fmp_client


# Income statement fields: FMP key → canonical name.
_INCOME_MAP: Dict[str, str] = {
    "revenue": "Revenue",
    "costOfRevenue": "COGS",
    "grossProfit": "GrossProfit",
    "operatingExpenses": "OperatingExpenses",
    "operatingIncome": "OperatingIncome",
    "netIncome": "NetIncome",
    "ebitda": "EBITDA",
    "interestExpense": "InterestExpense",
    "incomeTaxExpense": "TaxExpense",
    "incomeBeforeTax": "PretaxIncome",
    "researchAndDevelopmentExpenses": "R&D",
    "generalAndAdministrativeExpenses": "GeneralAndAdministrative",
    "sellingAndMarketingExpenses": "SellingAndMarketing",
    "sellingGeneralAndAdministrativeExpenses": "SGA_Combined",
    "depreciationAndAmortization": "Depreciation_Total_Aggregate",
    "weightedAverageShsOut": "SharesBasic",
    "weightedAverageShsOutDil": "SharesDiluted",
    "epsdiluted": "DilutedEPS",
}

# Balance sheet fields.
_BALANCE_MAP: Dict[str, str] = {
    "cashAndCashEquivalents": "Cash",
    "shortTermInvestments": "ShortTermInvestments",
    "longTermInvestments": "LongTermInvestments",
    "netReceivables": "AccountsReceivable",
    "inventory": "Inventory",
    "totalCurrentAssets": "CurrentAssets",
    "propertyPlantEquipmentNet": "PPE",
    "totalAssets": "TotalAssets",
    "accountPayables": "AccountsPayable",
    "shortTermDebt": "ShortTermDebt",
    "longTermDebt": "LongTermDebt",
    "totalCurrentLiabilities": "LiabilitiesCurrent",
    "totalLiabilities": "TotalLiabilities",
    "totalStockholdersEquity": "TotalEquity",
    "totalDebt": "TotalDebt",
    "totalEquity": "TotalEquity_All",
    "commonStock": "CommonStock",
    "retainedEarnings": "RetainedEarnings",
}

# Cash flow fields. Note: ``depreciationAndAmortization`` in FMP cash
# flow is the same number as `Depreciation_Total` our engines look for
# (D&A from the indirect-method CF). Stored under that canonical name so
# DCFEngine's ``get_with_provenance("Depreciation_Total")`` resolves.
_CASHFLOW_MAP: Dict[str, str] = {
    "operatingCashFlow": "OperatingCF",
    "netCashUsedForInvestingActivites": "InvestingCF",  # FMP typo "Activites"
    "netCashProvidedByOperatingActivities": "OperatingCF",
    "netCashProvidedByInvestingActivities": "InvestingCF",
    "netCashUsedProvidedByFinancingActivities": "FinancingCF",
    "netCashProvidedByFinancingActivities": "FinancingCF",
    "capitalExpenditure": "CapEx_signed",  # FMP signs negative; we convert
    "dividendsPaid": "DividendsPaid",
    "stockBasedCompensation": "SBC",
    "commonStockRepurchased": "Buybacks",
    "depreciationAndAmortization": "Depreciation_Total",
    "freeCashFlow": "FCF",
    "changeInWorkingCapital": "ChangeInWorkingCapital",
}


def _coerce(v: Any) -> Optional[float]:
    """Coerce FMP values to float; FMP sometimes serialises as string."""
    if v is None or v == "" or v == "None":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _apply_map(
    source: Dict[str, Any], mapping: Dict[str, str], target: Dict[str, Any],
) -> None:
    """Apply one FMP→canonical mapping into ``target`` dict. Skip values
    that don't coerce. Later mappings can supersede earlier ones (the
    cashflow map has both `operatingCashFlow` and
    `netCashProvidedByOperatingActivities` — whichever FMP returned).
    """
    for fmp_key, canonical_key in mapping.items():
        if fmp_key not in source:
            continue
        v = _coerce(source[fmp_key])
        if v is None:
            continue
        target[canonical_key] = v


def _normalize_signs(rec: Dict[str, Any]) -> None:
    """Apply our canonical sign conventions.

    CapEx: FMP signs negative (outflow); our canon stores as positive
    magnitude (engines call ``abs(capex)`` anyway, but the identity
    checks read raw signs — so we normalise here).
    """
    if "CapEx_signed" in rec:
        rec["CapEx"] = abs(rec.pop("CapEx_signed"))


def _compute_derived(rec: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Compute the cleaning-engine-equivalent derived fields that
    DCF / Multiple Decomposition / Screening read.

    DCFEngine requires (will raise without): ``Depreciation_Total``,
    ``CapEx``. With fallbacks: ``NOPAT``, ``ROIC``, ``FCF``, ``NetDebt``,
    ``InvestedCapital``, ``GAAP_TaxRate``. Computing them from FMP raw
    inputs unblocks the engines to produce real output for parity.
    """
    derived: Dict[str, Optional[float]] = {}
    op_inc = rec.get("OperatingIncome")
    pretax = rec.get("PretaxIncome")
    tax_expense = rec.get("TaxExpense")
    net_income = rec.get("NetIncome")
    cash = rec.get("Cash") or 0.0
    st_inv = rec.get("ShortTermInvestments") or 0.0
    total_debt = rec.get("TotalDebt")
    if total_debt is None:
        st_debt = rec.get("ShortTermDebt") or 0.0
        lt_debt = rec.get("LongTermDebt") or 0.0
        total_debt = st_debt + lt_debt if (st_debt or lt_debt) else None
    total_equity = rec.get("TotalEquity")
    op_cf = rec.get("OperatingCF")
    capex = rec.get("CapEx") or 0.0
    sbc = rec.get("SBC") or 0.0
    da = rec.get("Depreciation_Total")
    ebitda = rec.get("EBITDA")
    if ebitda is None and op_inc is not None and da is not None:
        ebitda = op_inc + da
    # Always mirror EBITDA into derived so ``derived_EBITDA`` column
    # populates regardless of whether FMP supplied the value directly
    # or we synthesized it from OpInc + D&A. Same FMP-adapter → DB
    # column mismatch pattern as Depreciation_Total: the DB schema
    # has ``derived_EBITDA`` but no ``raw_EBITDA``; the fmp_compare
    # view reads ``L.get("derived_EBITDA")`` which would otherwise
    # render "—".
    if ebitda is not None:
        derived["EBITDA"] = ebitda

    # GAAP tax rate from current-period income statement. Falls back
    # to None when pretax is near zero (loss years).
    if pretax and abs(pretax) > 1e-3 and tax_expense is not None:
        gaap_rate = tax_expense / pretax
        # Clamp to [-0.5, 0.6] to drop credit-year outliers.
        if -0.5 <= gaap_rate <= 0.6:
            derived["GAAP_TaxRate"] = gaap_rate

    # NOPAT = OperatingIncome × (1 − tax_rate). Use GAAP when sensible,
    # statutory 21% otherwise.
    if op_inc is not None:
        rate = derived.get("GAAP_TaxRate")
        if rate is None:
            rate = 0.21
        derived["NOPAT"] = op_inc * (1.0 - rate)

    # NormalizedEBIT — simple proxy in the absence of non-recurring
    # adjustments. The cleaning engine refines this with one-off
    # backouts; FMP doesn't expose those, so we equal OperatingIncome.
    if op_inc is not None:
        derived["NormalizedEBIT"] = op_inc

    # FCF from cash flow direct (avoids DCFEngine's None fallback).
    if op_cf is not None:
        derived["FCF"] = op_cf - abs(capex)

    # Net debt: total debt minus cash and short-term liquid investments.
    if total_debt is not None:
        derived["NetDebt"] = total_debt - cash - st_inv

    # Invested capital: book equity + interest-bearing debt − excess cash.
    if total_equity is not None and total_debt is not None:
        derived["InvestedCapital"] = total_equity + total_debt - cash

    # ROIC = NOPAT / InvestedCapital. Skip when denominator near zero.
    nopat = derived.get("NOPAT")
    ic = derived.get("InvestedCapital")
    if nopat is not None and ic and abs(ic) > 1e3:
        derived["ROIC"] = nopat / ic

    # EBITDA_ExcludingSBC — treats SBC as a real expense (Buffett view).
    if ebitda is not None:
        derived["EBITDA_ExcludingSBC"] = ebitda - sbc

    # Net income passthrough on derived as a stable resolver target.
    if net_income is not None:
        derived["NetIncome"] = net_income

    # ── Margin-percent fields read by ScreeningEngine ──────────────
    # ScreeningEngine reads `derived_EBIT_Margin_Pct`,
    # `derived_FCF_Margin_Pct`, `derived_GrossMargin_Pct` as percent
    # values (not fractions — multiply by 100).
    revenue = rec.get("Revenue")
    gross_profit = rec.get("GrossProfit")
    fcf = derived.get("FCF")
    if revenue and revenue > 0:
        if op_inc is not None:
            derived["EBIT_Margin_Pct"] = op_inc / revenue * 100.0
        if fcf is not None:
            derived["FCF_Margin_Pct"] = fcf / revenue * 100.0
        if gross_profit is not None:
            derived["GrossMargin_Pct"] = gross_profit / revenue * 100.0

    # ROE = NetIncome / TotalEquity. ScreeningEngine reads
    # ``derived_ROE`` as a decimal fraction.
    if net_income is not None and total_equity and abs(total_equity) > 1e3:
        derived["ROE"] = net_income / total_equity

    # ── Schema-aligned mirror to derived dict ──────────────────────
    # The DB has ``derived_*`` columns for several fields the cleaning_
    # engine emits in its derived dict. FMP records put the values in
    # ``raw`` (under FMP-friendly names) which don't always have a
    # matching ``raw_*`` column. Without an explicit derived mirror,
    # ``derived_X`` lands as NaN even though we have the value — which
    # breaks every view that reads ``derived_X`` directly (fmp_compare
    # EBITDA row, DCFEngine D&A lookup, etc.).
    #
    # Same pattern + same fix as Depreciation_Total: write into
    # ``derived`` so the post-persist DB row carries the value in
    # the column downstream code expects.
    if da is not None:
        derived["Depreciation_Total"] = da
    if op_inc is not None:
        derived["OperatingIncome"] = op_inc
    capex_pos = rec.get("CapEx")
    if capex_pos is not None:
        derived["CapEx"] = capex_pos

    # ── Mirror cleaning-engine fields into the clean dict ─────────
    # ReverseDCF reads ``clean_NormalizedEBIT``, ``clean_NOPAT``, and
    # ``clean_GAAP_TaxRate`` / ``clean_CashTaxRate`` DIRECTLY by column
    # (no ``derived_*`` fallback), and refuses to compute if any are
    # NaN. Copy the derived values back onto ``rec`` so they land in
    # the clean namespace too. ScreeningEngine and DCFEngine also
    # benefit (they read clean first, then fall back to derived).
    for k in ("NormalizedEBIT", "NOPAT", "GAAP_TaxRate"):
        if k in derived and k not in rec:
            rec[k] = derived[k]
    # CashTaxRate: FMP doesn't expose CashTaxesPaid; fall back to the
    # GAAP rate so the resolver's _is_usable_rate doesn't trip on None.
    if "GAAP_TaxRate" in derived and "CashTaxRate" not in rec:
        rec["CashTaxRate"] = derived["GAAP_TaxRate"]
    # Depreciation alias the cleaning engine uses (clean_Depreciation,
    # without the _Total suffix) — RDCF reads this for D&A pct.
    if "Depreciation_Total" in rec and "Depreciation" not in rec:
        rec["Depreciation"] = rec["Depreciation_Total"]

    return derived


def _build_record_dict(
    income: Dict[str, Any],
    balance: Dict[str, Any],
    cashflow: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge income / balance / cashflow into one flat dict in our
    canonical names. Returned dict is used for both ``raw`` and
    ``clean`` namespaces (FMP data has no separate raw vs cleaned
    flavour — it's already cleaned by FMP).

    raw/clean dicts must be Dict[str, Optional[float]] per the
    ValidatedCleanedRecord contract — period_end_date lives at the
    record level, not inside the blobs.
    """
    out: Dict[str, Any] = {}
    _apply_map(income, _INCOME_MAP, out)
    _apply_map(balance, _BALANCE_MAP, out)
    _apply_map(cashflow, _CASHFLOW_MAP, out)
    _normalize_signs(out)

    # Compute total debt when not directly provided.
    if "TotalDebt" not in out:
        st = out.get("ShortTermDebt") or 0.0
        lt = out.get("LongTermDebt") or 0.0
        if st or lt:
            out["TotalDebt"] = st + lt

    return out


def _index_by_year(records: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """FMP returns most-recent-first. Index by fiscal year for joining."""
    out: Dict[int, Dict[str, Any]] = {}
    for r in records:
        fy = r.get("calendarYear") or r.get("fiscalYear")
        try:
            fy_int = int(fy)
        except (TypeError, ValueError):
            continue
        out[fy_int] = r
    return out


# Income-statement fields that are FLOWS (sum across quarters for TTM).
# All non-listed income fields (margins, EPS, share count) are taken
# from the latest quarter rather than summed.
_INCOME_FLOW_KEYS: List[str] = [
    "revenue", "costOfRevenue", "grossProfit", "operatingExpenses",
    "operatingIncome", "netIncome", "ebitda", "interestExpense",
    "incomeTaxExpense", "incomeBeforeTax",
    "researchAndDevelopmentExpenses",
    "generalAndAdministrativeExpenses",
    "sellingAndMarketingExpenses",
    "sellingGeneralAndAdministrativeExpenses",
    "depreciationAndAmortization",
]

# Cash-flow-statement fields are FLOWS (sum across quarters for TTM).
_CASHFLOW_FLOW_KEYS: List[str] = [
    "operatingCashFlow",
    "netCashUsedForInvestingActivites",
    "netCashProvidedByOperatingActivities",
    "netCashProvidedByInvestingActivities",
    "netCashUsedProvidedByFinancingActivities",
    "netCashProvidedByFinancingActivities",
    "capitalExpenditure",
    "dividendsPaid",
    "stockBasedCompensation",
    "commonStockRepurchased",
    "depreciationAndAmortization",
    "freeCashFlow",
    "changeInWorkingCapital",
]


def _sum_quarters(quarters: List[Dict[str, Any]], key: str) -> Optional[float]:
    """Sum ``key`` across all quarters. Returns None if any quarter is
    missing the value — silent-zero substitution would hide data gaps."""
    vals: List[float] = []
    for q in quarters:
        v = _coerce(q.get(key))
        if v is None:
            return None
        vals.append(v)
    return sum(vals) if vals else None


def _build_ttm_pseudo_record(
    income_last4: List[Dict[str, Any]],
    balance_latest: Dict[str, Any],
    cashflow_last4: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Synthesise a TTM income / cashflow record by summing the last 4
    quarters; balance sheet is the latest quarter's snapshot (stock
    items don't sum). Returns a single FMP-flavored dict that mimics
    an annual FMP statement so the existing ``_apply_map`` pipeline
    can ingest it without changes."""
    ttm_income: Dict[str, Any] = {}
    for k in _INCOME_FLOW_KEYS:
        v = _sum_quarters(income_last4, k)
        if v is not None:
            ttm_income[k] = v
    # Non-flow income fields: take latest quarter's value (margins,
    # EPS, shares — these aren't sums).
    latest_q = income_last4[0]
    for k in ("weightedAverageShsOut", "weightedAverageShsOutDil",
              "epsdiluted"):
        if k in latest_q:
            ttm_income[k] = latest_q[k]

    ttm_cashflow: Dict[str, Any] = {}
    for k in _CASHFLOW_FLOW_KEYS:
        v = _sum_quarters(cashflow_last4, k)
        if v is not None:
            ttm_cashflow[k] = v

    # Balance sheet — latest quarter's reported values pass through.
    return {
        "income": ttm_income,
        "balance": balance_latest,
        "cashflow": ttm_cashflow,
    }


def build_validated_records(
    ticker: str, *, force_refresh: bool = False,
) -> List[ValidatedCleanedRecord]:
    """Pull FMP statements and produce one ``ValidatedCleanedRecord``
    per available fiscal year. Years where any of the three statements
    is missing are skipped (rollforward checks need all three).

    The latest record carries a current-market-cap stamp from FMP's
    TTM key-metrics so DCF / Multiple Decomposition can resolve price
    multiples. Earlier years keep MarketCap=None (FMP's historical
    market-cap endpoint exists but isn't wired here yet).
    """
    income_list = fmp_client.fetch_income_statements(
        ticker, period="annual", force_refresh=force_refresh,
    ) or []
    balance_list = fmp_client.fetch_balance_sheets(
        ticker, period="annual", force_refresh=force_refresh,
    ) or []
    cashflow_list = fmp_client.fetch_cash_flows(
        ticker, period="annual", force_refresh=force_refresh,
    ) or []
    keymet_ttm = fmp_client.fetch_key_metrics_ttm(
        ticker, force_refresh=force_refresh,
    ) or {}
    current_market_cap = _coerce(
        keymet_ttm.get("marketCap")
        or keymet_ttm.get("marketCapTTM"),
    )

    # Quarterly endpoints power the TTM record. We pull them up front so
    # we can append a `period="TTM"` ValidatedCleanedRecord after the FY
    # rows. DCFEngine / RDCF prefer TTM as the anchor period when one is
    # present in df, matching the regular pipeline's behaviour.
    income_q_list = fmp_client.fetch_income_statements(
        ticker, period="quarter", force_refresh=force_refresh,
    ) or []
    balance_q_list = fmp_client.fetch_balance_sheets(
        ticker, period="quarter", force_refresh=force_refresh,
    ) or []
    cashflow_q_list = fmp_client.fetch_cash_flows(
        ticker, period="quarter", force_refresh=force_refresh,
    ) or []

    income = _index_by_year(income_list)
    balance = _index_by_year(balance_list)
    cashflow = _index_by_year(cashflow_list)

    common_years = sorted(set(income) & set(balance) & set(cashflow))
    if not common_years:
        return []

    out: List[ValidatedCleanedRecord] = []
    latest_year = common_years[-1]
    for fy in common_years:
        inc = income[fy]
        bal = balance[fy]
        cf = cashflow[fy]
        period_end = (
            inc.get("date") or bal.get("date") or cf.get("date") or
            f"{fy}-12-31"
        )
        rec_dict = _build_record_dict(inc, bal, cf)

        # Stamp current market cap only on the latest record. DCFEngine
        # reads MarketCap from the latest row (TTM-first, FY fallback).
        if fy == latest_year and current_market_cap is not None:
            rec_dict["MarketCap"] = current_market_cap

        derived = _compute_derived(rec_dict)

        # ScreeningEngine + MultipleDecomposition read sub-fields out of
        # the raw_json / clean_json blob columns via ``_get_json``.
        # Serialising the canonical dict into both blobs makes
        # CurrentAssets / ShortTermDebt / InterestExpense / etc.
        # available to the engines without further wiring.
        blob = json.dumps(rec_dict)

        rec = ValidatedCleanedRecord(
            ticker=ticker,
            fiscal_year=fy,
            period="FY",
            period_end_date=period_end,
            raw=dict(rec_dict),
            clean=dict(rec_dict),
            derived=derived,
            raw_blob_json=blob,
            clean_blob_json=blob,
            overall_quality_score=1.0,
            cleaning_warnings=[],
            blocking_errors=[],
            validation=ValidationReceipt(
                schema_violations=[],
                fmp_validation={"source": "fmp_isolated_validation"},
                cross_source_agreement={},
                overrides_applied=[],
            ),
            record_fingerprint=f"fmp-iso-{ticker}-{fy}",
            input_bundle_fingerprint="fmp-iso",
            cleaned_at=datetime.now(timezone.utc),
            pipeline_version="fmp-iso-validation",
        )
        out.append(rec)

    # ── TTM record (last 4 quarters → period="TTM") ─────────────
    # DCFEngine + RDCF prefer TTM as anchor when one is present. Without
    # this, our FY2025 record gets used as anchor and the parity report
    # against FMP TTM ratios collects a structural FY-vs-TTM drift on
    # every flow ratio. Mirrors aletheia.data.ttm_derivation.
    if (
        len(income_q_list) >= 4
        and len(cashflow_q_list) >= 4
        and balance_q_list
    ):
        income_last4 = income_q_list[:4]
        cashflow_last4 = cashflow_q_list[:4]
        balance_latest = balance_q_list[0]
        ttm = _build_ttm_pseudo_record(
            income_last4, balance_latest, cashflow_last4,
        )
        ttm_rec_dict = _build_record_dict(
            ttm["income"], ttm["balance"], ttm["cashflow"],
        )
        # CapEx came through summed and absolute already because
        # ``_normalize_signs`` runs inside ``_build_record_dict``.
        if current_market_cap is not None:
            ttm_rec_dict["MarketCap"] = current_market_cap
        ttm_derived = _compute_derived(ttm_rec_dict)
        ttm_blob = json.dumps(ttm_rec_dict)
        ttm_period_end = (
            balance_latest.get("date") or income_last4[0].get("date") or ""
        )[:10]
        ttm_fy_raw = (
            income_last4[0].get("fiscalYear")
            or income_last4[0].get("calendarYear")
        )
        try:
            ttm_fy = int(ttm_fy_raw) if ttm_fy_raw is not None else latest_year
        except (TypeError, ValueError):
            ttm_fy = latest_year
        out.append(ValidatedCleanedRecord(
            ticker=ticker,
            fiscal_year=ttm_fy,
            period="TTM",
            period_end_date=ttm_period_end or f"{ttm_fy}-12-31",
            raw=dict(ttm_rec_dict),
            clean=dict(ttm_rec_dict),
            derived=ttm_derived,
            raw_blob_json=ttm_blob,
            clean_blob_json=ttm_blob,
            overall_quality_score=1.0,
            cleaning_warnings=[],
            blocking_errors=[],
            validation=ValidationReceipt(
                schema_violations=[],
                fmp_validation={
                    "source": "fmp_isolated_validation",
                    "ttm_source": "fmp_quarterly_sum",
                },
                cross_source_agreement={},
                overrides_applied=[],
            ),
            record_fingerprint=f"fmp-iso-{ticker}-TTM",
            input_bundle_fingerprint="fmp-iso",
            cleaned_at=datetime.now(timezone.utc),
            pipeline_version="fmp-iso-validation",
        ))

    return out


def coverage_report(
    records: List[ValidatedCleanedRecord],
) -> Dict[str, Any]:
    """Quick statistics about the adapter's output — how many years
    were produced, which canonical fields are populated, which are
    missing. Surfaced in the UI so the analyst sees what data the
    isolated Stage 3 is actually operating on.
    """
    if not records:
        return {"n_years": 0, "field_coverage": {}}
    # Field coverage: fraction of years that have a value for each key.
    all_keys: set = set()
    for r in records:
        all_keys.update(r.clean.keys())
    field_cov: Dict[str, int] = {}
    for k in all_keys:
        n = sum(1 for r in records if r.clean.get(k) is not None)
        field_cov[k] = n
    years = sorted(r.fiscal_year for r in records)
    return {
        "n_years": len(records),
        "fy_range": (years[0], years[-1]) if years else (None, None),
        "field_coverage": dict(sorted(field_cov.items())),
    }
