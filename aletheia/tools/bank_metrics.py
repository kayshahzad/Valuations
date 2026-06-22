"""Bank operating metrics — the bank income statement + KPIs, read straight from
SEC XBRL companyfacts.

A bank's economics live in line items the industrial cleaned-frame doesn't carry
(net interest income, provisions, non-interest income/expense, deposits, loans).
Those ARE in the SEC XBRL (verified: JPM NII $25.4B, deposits $2.68T, ...), but
they're not in the DB schema's fixed ``raw_*`` columns, so this reads them
directly from companyfacts via the resolver's robust annual-period matching
(``_extract_value``) and derives the operating KPIs:

  NIM            = net interest income / average earning assets (≈ loans+securities; total assets proxy)
  efficiency     = non-interest expense / (net interest income + non-interest income)
  provision rate = provision for credit losses / loans
  ROA            = net income / average total assets
  tangible BVPS  = (equity − goodwill) / shares

GATED (NOT faked): CET1 / Tier-1 / RWA / NPL are NOT in structured XBRL — only
the regulatory minimums are; the actual ratios live in MD&A text. Reported as
``capital_adequacy_gap`` so the panel shows what's missing rather than inventing it.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# Exact us-gaap XBRL tags (verified present on JPM companyfacts).
_BANK_TAGS = {
    "net_interest_income": ["InterestIncomeExpenseNet",
                            "InterestIncomeExpenseAfterProvisionForLoanLoss"],
    "interest_income":     ["InterestIncomeOperating",
                            "InterestAndDividendIncomeOperating"],
    "interest_expense":    ["InterestExpense"],
    "provisions":          ["ProvisionForLoanLeaseAndOtherLosses",
                            "ProvisionForLoanAndLeaseLosses"],
    "noninterest_income":  ["NoninterestIncome"],
    "noninterest_expense": ["NoninterestExpense"],
    "deposits":            ["Deposits", "InterestBearingDepositLiabilities"],
    "loans":               ["LoansAndLeasesReceivableNetReportedAmount",
                            "FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss"],
    "goodwill":            ["Goodwill"],
    "net_income":          ["NetIncomeLoss", "ProfitLoss"],
    "total_assets":        ["Assets"],
    "total_equity":        ["StockholdersEquity",
                            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
}


def _extract_bank_facts(ticker: str, fiscal_years: List[int]) -> Dict[int, Dict[str, float]]:
    """{fiscal_year: {concept: value}} from companyfacts, via the resolver's
    annual-period matcher. Empty when companyfacts aren't cached."""
    from aletheia.data.tag_resolver import TagResolver
    r = TagResolver()
    raw = r._load_raw_facts(ticker)
    if not raw:
        return {}
    facts = raw.get("facts", {})
    combined = {}
    combined.update(facts.get("dei", {}))
    combined.update(facts.get("ifrs-full", {}))
    combined.update(facts.get("us-gaap", {}))

    out: Dict[int, Dict[str, float]] = {}
    for fy in fiscal_years:
        row: Dict[str, float] = {}
        for concept, tags in _BANK_TAGS.items():
            for tag in tags:
                if tag in combined:
                    val, _ = r._extract_value(combined[tag], fy, tag_name=tag)
                    if val is not None and val != 0:
                        row[concept] = float(val)
                        break
        if row:
            out[fy] = row
    return out


def build_bank_metrics(calc, shares: Optional[float] = None) -> dict:
    """Bank income statement + operating KPIs from companyfacts. Gated on the
    financial-filer predicate; non-banks get ``available: False``."""
    out = {"available": False}
    cls = getattr(calc, "classification", None)
    from aletheia.calculations.sector_classification import is_bank_for_display
    if not is_bank_for_display(getattr(cls, "sector", ""), getattr(cls, "business_model", "")):
        out["notes"] = "not a bank/specialized-financial filer"
        return out

    ticker = getattr(cls, "ticker", "") or ""
    df = getattr(calc, "df", None)
    if df is None or df.empty or "fiscal_year" not in df.columns:
        out["notes"] = "no frame"
        return out
    years = sorted(int(y) for y in df["fiscal_year"].dropna().unique())[-6:]

    facts = _extract_bank_facts(ticker, years)
    if not facts:
        out["notes"] = ("no bank facts in companyfacts (not re-ingested, or not a "
                        "structured-XBRL bank filer)")
        return out

    def _v(fy, k):
        return (facts.get(fy) or {}).get(k)

    latest = max(facts)
    prev = max((y for y in facts if y < latest), default=None)

    def _avg(k):  # 2-point average for flow/stock ratios (ROA, NIM)
        a, b = _v(latest, k), (_v(prev, k) if prev else None)
        return (a + b) / 2.0 if (a is not None and b is not None) else a

    nii = _v(latest, "net_interest_income")
    nonii = _v(latest, "noninterest_income")
    nonie = _v(latest, "noninterest_expense")
    prov = _v(latest, "provisions")
    loans = _v(latest, "loans")
    assets_avg = _avg("total_assets")
    ni = _v(latest, "net_income")
    equity = _v(latest, "total_equity")
    goodwill = _v(latest, "goodwill")
    deposits = _v(latest, "deposits")

    net_revenue = (nii or 0) + (nonii or 0) if (nii is not None or nonii is not None) else None

    kpis = {
        "net_interest_income": nii,
        "noninterest_income": nonii,
        "noninterest_expense": nonie,
        "net_revenue": net_revenue,
        "provisions": prov,
        "deposits": deposits,
        "loans": loans,
        # NIM ≈ NII / avg earning assets (loans+securities); total assets is the
        # available proxy (understates NIM — flagged).
        "nim_on_assets": (nii / assets_avg) if (nii and assets_avg) else None,
        "efficiency_ratio": (nonie / net_revenue) if (nonie and net_revenue) else None,
        "provision_rate": (prov / loans) if (prov is not None and loans) else None,
        "roa": (ni / assets_avg) if (ni and assets_avg) else None,
        "loan_to_deposit": (loans / deposits) if (loans and deposits) else None,
        "tangible_bvps": (((equity - (goodwill or 0)) / shares)
                          if (equity and shares) else None),
        "bvps": (equity / shares) if (equity and shares) else None,
        "deposit_growth": ((deposits / _v(prev, "deposits") - 1.0)
                           if (deposits and prev and _v(prev, "deposits")) else None),
        "loan_growth": ((loans / _v(prev, "loans") - 1.0)
                        if (loans and prev and _v(prev, "loans")) else None),
    }

    out.update({
        "available": True,
        "source": "SEC XBRL companyfacts (direct read)",
        "fiscal_year": latest,
        "kpis": kpis,
        "by_year": facts,
        "capital_adequacy_gap": {
            "missing": ["CET1 ratio", "Tier-1 ratio", "risk-weighted assets",
                        "NPL ratio", "loan-loss coverage"],
            "reason": "regulatory-capital ratios are not in structured XBRL "
                      "(only the minimums are) — they live in MD&A text; needs "
                      "MD&A/LLM extraction or a third-party feed.",
        },
        "notes": ["NIM uses total assets as the earning-asset proxy (understates "
                  "true NIM). Efficiency = non-int expense / (NII + non-int income)."],
    })
    return out


__all__ = ["build_bank_metrics"]
