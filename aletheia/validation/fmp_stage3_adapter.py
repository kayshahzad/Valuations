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
    # Per-share metrics straight from FMP. The legacy ``epsdiluted`` key
    # (all-lowercase) never matched FMP's actual camelCase response,
    # silently producing NaN — fixed here.
    "eps": "EPS_Basic",
    "epsDiluted": "EPS_Diluted",
    # FMP's catch-all "other" line. For tickers with material one-time
    # items (goodwill impairment, restructuring, settlement) this can
    # equal the Capital-IQ ``Asset Writedown`` line; for other years it
    # may be net of offsetting credits. Exposed here so analysts can
    # see the gap between FMP's ``operatingIncome`` and Capital IQ's
    # ex-unusual view rather than guessing.
    "otherExpenses": "OtherOperatingItems",
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
    # Non-redeemable noncontrolling interest in consolidated subsidiaries.
    # Sits at the equity tier of A=L+E but FMP's ``totalStockholdersEquity``
    # is parent-only and excludes it. Without this mapping, the
    # schema-contract A=L+E identity emits chronic ~0.3-0.6% gaps for
    # multinational filers (APH, ACN, KO, JNJ, etc.) where consolidated
    # subsidiaries have material minority partners. Schema contract uses
    # this to construct a third "expected_with_minority_interest" form.
    "minorityInterest": "MinorityInterest",
    # Mezzanine / temporary equity (redeemable convertible preferred) sitting
    # between liabilities and permanent equity. FMP exposes the full carrying
    # amount here even when SEC companyfacts drops it (company extension tags
    # are excluded from companyfacts). Canonical case: CELH carries PepsiCo's
    # 2022 convertible preferred — $824M FY2022-2024, growing to $1.76B at
    # FY2025 year-end (Alani Nu acquisition consideration), where the XBRL
    # TemporaryEquityCarryingAmountAttributableToParent tag stops reporting.
    # Schema contract's A=L+E uses it as the redeemable-equity term; the
    # multi-form auto-detection ignores it harmlessly for filers whose
    # totalStockholdersEquity already includes permanent preferred.
    "preferredStock": "TemporaryEquityCarryingAmount",
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
    # DAL FY2023+ populates ``commonDividendsPaid`` ($128M/$321M/$440M)
    # while ``dividendsPaid`` returns None. Listed first so the broader
    # ``dividendsPaid`` total can supersede when both fields are
    # populated (``_apply_map`` lets later mappings overwrite earlier
    # ones). Tickers where only the common-stock line populates fall
    # back to that value.
    "commonDividendsPaid": "DividendsPaid",
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


# XBRL specialty tags the hybrid provider injects when available. Sum
# of any non-None values = the ex-impairment add-back for OpInc/EBITDA.
# Order doesn't matter; we just sum magnitudes. Adding new tags here
# (e.g., AssetWritedown, ImpairmentOfLongLivedAssetsHeldForUse) expands
# coverage without touching the derivation logic.
_IMPAIRMENT_TAGS = (
    "AssetImpairmentCharges",
    "GoodwillImpairmentLoss",
    "IntangibleAssetImpairmentCharge",
    "ImpairmentOfLongLivedAssetsHeldForUse",
    "RestructuringCharges",
)

# Provenance codes — ValidatedCleanedRecord.clean is typed
# Dict[str, Optional[float]] so the source label can't ride in the
# record dict as a string. Numeric code goes in; the DB-upsert
# translates back to a human label via the reverse map below.
_SOURCE_CODES: Dict[str, float] = {
    "xbrl_discrete_tags":        1.0,
    "fmp_other_expenses_bucket": 2.0,
}
SOURCE_LABELS: Dict[float, str] = {v: k for k, v in _SOURCE_CODES.items()}


_FMP_FALLBACK_MAX_PCT_OF_REVENUE = 0.05


def _ex_impairment_addback(rec: Dict[str, Any]):
    """Decide how much to ADD BACK to OperatingIncome / EBITDA to
    strip one-time impairment / restructuring. Returns
    ``(amount, source)`` or ``(None, None)`` when no signal is
    available.

    Preference order:
      1. ``xbrl_discrete_tags`` — sum of explicit XBRL impairment tags
         supplied by the hybrid provider. Most precise; aligns with
         Capital IQ's "Asset Writedown" line.
      2. ``fmp_other_expenses_bucket`` — FMP's ``otherExpenses`` field,
         gated by a materiality threshold (default 5% of revenue).
         FMP's ``otherExpenses`` is a kitchen sink: for retail filers
         like Macy's it happens to be the goodwill writedown ($966M /
         4% of revenue ≈ Capital IQ's $957M Asset Writedown). For tech
         filers like Amazon it's the rest of operating costs ($99B /
         15% of revenue), and applying it as an impairment add-back
         produces fantasy ex-unusual numbers. The threshold filters
         out the structural-cost case while keeping the impairment
         case. Threshold is symmetric — large negative otherExpenses
         (net credits) also fail the test.
      3. None — leave OperatingIncome / EBITDA as reported.
    """
    discrete = [
        abs(rec[k]) for k in _IMPAIRMENT_TAGS
        if rec.get(k) is not None
    ]
    if discrete:
        return sum(discrete), "xbrl_discrete_tags"
    other = rec.get("OtherOperatingItems")
    revenue = rec.get("Revenue")
    if (other is not None and revenue and revenue > 0
            and abs(other) / revenue <= _FMP_FALLBACK_MAX_PCT_OF_REVENUE):
        return other, "fmp_other_expenses_bucket"
    return None, None


def _compute_derived(rec: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Compute the cleaning-engine-equivalent derived fields that
    DCF / Multiple Decomposition / Screening read.

    DCFEngine requires (will raise without): ``Depreciation_Total``,
    ``CapEx``. With fallbacks: ``NOPAT``, ``ROIC``, ``FCF``, ``NetDebt``,
    ``InvestedCapital``, ``GAAP_TaxRate``. Computing them from FMP raw
    inputs unblocks the engines to produce real output for parity.
    """
    # Centralized formula functions — Phase 1-3 of the centralization
    # refactor. All numeric derivations in this function now flow
    # through these functions; both the FMP path (this file) and the
    # XBRL path (cleaning_engine) call the same code. See
    # docs/methodology_changes/ for each phase's methodology memo.
    from aletheia.calculations.formulas import (
        nopat as _nopat,
        invested_capital as _invested_capital,
        roic as _roic,
        fcf as _fcf,
        fcff as _fcff,
        gross_debt as _gross_debt,
        liquid_assets as _liquid_assets,
        net_debt as _net_debt,
        ebitda as _ebitda,
        gross_margin_pct as _gross_margin_pct,
        ebit_margin_pct as _ebit_margin_pct,
        ebitda_margin_pct as _ebitda_margin_pct,
        fcf_margin_pct as _fcf_margin_pct,
        roe as _roe,
    )

    derived: Dict[str, Optional[float]] = {}
    op_inc = rec.get("OperatingIncome")
    pretax = rec.get("PretaxIncome")
    tax_expense = rec.get("TaxExpense")
    net_income = rec.get("NetIncome")
    # Revenue is needed early for the centralized invested-capital
    # formula (5%-of-revenue floor + excess-cash netting at 2% of
    # revenue). The duplicate read further down is preserved for the
    # margin block to keep that block independently readable.
    revenue = rec.get("Revenue")
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
    if ebitda is None:
        # Central synthesis: EBITDA = OperatingIncome + Depreciation_Total
        ebitda = _ebitda(operating_income=op_inc, depreciation_total=da)
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

    # NOPAT — uses GAAP rate when available, statutory 21% as fallback.
    # Tax-rate resolution stays here because FMP doesn't have access
    # to the full _tax_rate resolver's fallback ladder (which reads
    # historical cash tax from the DB).
    tax_rate = derived.get("GAAP_TaxRate")
    if tax_rate is None:
        tax_rate = 0.21
    nopat_val = _nopat(operating_income=op_inc, tax_rate=tax_rate)
    if nopat_val is not None:
        derived["NOPAT"] = nopat_val

    # NormalizedEBIT — simple proxy in the absence of non-recurring
    # adjustments. The cleaning engine refines this with one-off
    # backouts; FMP doesn't expose those, so we equal OperatingIncome.
    if op_inc is not None:
        derived["NormalizedEBIT"] = op_inc

    # FCF — central formula.
    fcf_val = _fcf(operating_cf=op_cf, capex=capex)
    if fcf_val is not None:
        derived["FCF"] = fcf_val

    # FCFF — Phase 2 canonicalization. FMP exposes
    # ``changeInWorkingCapital`` directly so the full CFA-textbook
    # formula (NOPAT + D&A − CapEx − ΔNWC) is now feasible on this
    # path. Previously aliased to FCF, which understated the firm-
    # level cash generation by ΔNWC magnitude.
    delta_nwc = rec.get("ChangeInWorkingCapital")
    fcff_val = _fcff(
        nopat=nopat_val,
        depreciation=da,
        capex=capex,
        delta_nwc=delta_nwc,
    )
    if fcff_val is not None:
        derived["FCFF"] = fcff_val
    elif fcf_val is not None:
        # Fallback to FCF when D&A or NOPAT inputs missing — preserves
        # the prior FCF-shaped value rather than dropping the field.
        derived["FCFF"] = fcf_val

    # Net debt — central formula. Phase 2 canonicalization includes
    # current LT-debt portion + finance leases + LT investments,
    # bringing FMP path in line with cleaning_engine's EV-aligned
    # definition. FMP doesn't decompose finance-lease current/non-
    # current; uses the consolidated total when available.
    current_lt = rec.get("CurrentPortionLongTermDebt") or 0.0
    fl_total = rec.get("FinanceLeaseLiability_Total") or 0.0
    lt_inv = rec.get("LongTermInvestments") or 0.0
    gd = _gross_debt(
        long_term_debt=rec.get("LongTermDebt"),
        short_term_debt=rec.get("ShortTermDebt"),
        current_portion_lt_debt=current_lt,
        finance_lease_total=fl_total,
    )
    la = _liquid_assets(
        cash=cash,
        short_term_investments=st_inv,
        long_term_investments=lt_inv,
    )
    nd_val = _net_debt(gross_debt=gd, liquid_assets=la)
    if nd_val is not None:
        derived["NetDebt"] = nd_val

    # Invested capital — central formula, ExcessCash netting + 5%
    # revenue floor.
    ic_val = _invested_capital(
        total_equity=total_equity,
        total_debt=total_debt,
        cash=cash,
        revenue=revenue,
    )
    if ic_val is not None:
        derived["InvestedCapital"] = ic_val

    # ROIC = NOPAT / InvestedCapital. Central formula returns None
    # when IC <= 0, replacing the local "abs(ic) > 1e3" guard.
    roic_val = _roic(nopat=nopat_val, invested_capital=ic_val)
    if roic_val is not None:
        derived["ROIC"] = roic_val

    # EBITDA_ExcludingSBC — treats SBC as a real expense (Buffett view).
    if ebitda is not None:
        derived["EBITDA_ExcludingSBC"] = ebitda - sbc

    # Net income passthrough on derived as a stable resolver target.
    if net_income is not None:
        derived["NetIncome"] = net_income

    # ── Margin-percent fields read by ScreeningEngine + FMP Compare ──
    # ScreeningEngine reads `derived_EBIT_Margin_Pct`,
    # `derived_FCF_Margin_Pct`, `derived_GrossMargin_Pct` as percent
    # values (not fractions — multiply by 100). FMP Compare view also
    # reads `derived_EBITDA_Margin_Pct` for its EBITDA Margin row.
    # All four margins now flow through the central formula module
    # (Phase 3 mechanical consolidation; identical formulas).
    gross_profit = rec.get("GrossProfit")
    fcf_for_margin = derived.get("FCF")
    ebit_m = _ebit_margin_pct(ebit=op_inc, revenue=revenue)
    if ebit_m is not None:
        derived["EBIT_Margin_Pct"] = ebit_m
    ebitda_m = _ebitda_margin_pct(ebitda=ebitda, revenue=revenue)
    if ebitda_m is not None:
        derived["EBITDA_Margin_Pct"] = ebitda_m
    fcf_m = _fcf_margin_pct(fcf=fcf_for_margin, revenue=revenue)
    if fcf_m is not None:
        derived["FCF_Margin_Pct"] = fcf_m
    gross_m = _gross_margin_pct(gross_profit=gross_profit, revenue=revenue)
    if gross_m is not None:
        derived["GrossMargin_Pct"] = gross_m

    # ROE — central formula. Returns None on non-positive equity,
    # matching the cleaning_engine's suppression behavior for
    # aggressive-buyback filers with negative book equity.
    roe_val = _roe(net_income=net_income, total_equity=total_equity)
    if roe_val is not None:
        derived["ROE"] = roe_val

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

    # NetBuyback_AfterSBC — the buyback_discipline qualitative computer
    # reads this column. Only the XBRL cleaning_engine populated it
    # historically; under the FMP provider it was always NaN, so every
    # FMP-sourced ticker rendered "buyback data unavailable." Compute it
    # here whenever both fields are present in the raw FMP payload —
    # abs(Buybacks) flips FMP's outflow-negative convention to magnitude,
    # SBC is already positive (cash-flow addback). When either field is
    # missing entirely, leave NaN so the computer's data-quality threshold
    # can still distinguish "no data" from "true zero."
    if "Buybacks" in rec and "SBC" in rec:
        buybacks_magnitude = abs(rec["Buybacks"] or 0.0)
        sbc_value = rec["SBC"] or 0.0
        rec["NetBuyback_AfterSBC"] = buybacks_magnitude - sbc_value

    # OperatingIncome / EBITDA ex-unusual items — fallback chain:
    #   1. Discrete XBRL impairment tags (preferred): hybrid provider
    #      enriches the record with AssetImpairmentCharges,
    #      GoodwillImpairmentLoss, IntangibleAssetImpairmentCharge,
    #      RestructuringCharges. When present, sum them — that's the
    #      precise Capital-IQ "Asset Writedown" add-back. Source:
    #      "xbrl_discrete_tags".
    #   2. FMP's catch-all ``otherExpenses`` (fallback): bundles the
    #      same items into a single kitchen-sink line, sometimes with
    #      offsetting credits. Works for Macy's FY2023 (pure $957M
    #      goodwill) but produces nonsense for Macy's FY2025 (-$1B
    #      net credit because of an offsetting gain). Source:
    #      "fmp_other_expenses_bucket".
    # Provenance is stored on the record so the UI can show analysts
    # which path produced the ex-unusual number.
    addback, source = _ex_impairment_addback(rec)
    if op_inc is not None and addback is not None:
        rec["OperatingIncome_ExUnusual"] = op_inc + addback
        # ValidatedCleanedRecord.clean is typed Dict[str, Optional[float]],
        # so provenance is stored as a numeric code here; the DB upsert
        # translates back to a human label via _SOURCE_LABELS.
        rec["OperatingIncome_ExUnusual_Source_Code"] = _SOURCE_CODES[source]
        if ebitda is not None:
            rec["EBITDA_ExUnusual"] = ebitda + addback

    # Per-share metrics — FMP exposes EPS directly; DPS and payout
    # ratio compute from existing fields. abs() on dividends flips FMP's
    # negative outflow convention to a magnitude consistent with the
    # external Capital-IQ/S&P presentation.
    dps_source = rec.get("DividendsPaid")
    shares_dil = rec.get("SharesDiluted")
    if dps_source is not None and shares_dil and shares_dil > 0:
        rec["DividendsPerShare"] = abs(dps_source) / shares_dil
    if dps_source is not None and net_income and net_income > 0:
        rec["PayoutRatio"] = abs(dps_source) / net_income

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
