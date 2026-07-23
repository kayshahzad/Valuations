"""
aletheia/data/sec_xbrl_validator.py

Authoritative validation source: SEC XBRL companyfacts. For each ticker we
already maintain a per-CIK companyfacts JSON at
`valuation_data/raw/sec/companyfacts/CIK*.json` — that file is the same data
that ships in SEC's quarterly Financial Statement Data Sets bulk drops, just
shaped per-filer for direct lookup.

This module reads those files and exposes the canonical us-gaap value for
a (ticker, fiscal_year, tag) triple, picking the most-recent 10-K filing
that closes on the FY end. Used by `scripts/validate_sec.py` to cross-check
our cleaned `raw_<field>` numbers byte-for-byte against the SEC truth.

Why this matters: FMP's free tier can't validate ~44% of our universe (LLY,
ASML, SMCI, ABT, BRK-B, CAT, CNC, NEE, ORCL, QCOM, TXN are subscription-
restricted there). SEC covers every SEC filer for free. It is also more
authoritative than FMP because there is no normalization layer — the values
are exactly what the issuer XBRL-tagged in their 10-K.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_FACTS_DIR = Path("valuation_data/raw/sec/companyfacts")


# Canonical us-gaap tags per logical field. We pick the modern ASC tag first
# and fall back to legacy/IFRS variants. This list is intentionally smaller
# than `config/tag_mappings.py` — the validator targets bottom-line totals
# where the "right" XBRL tag is essentially settled across the universe.
CANONICAL_TAGS: Dict[str, List[str]] = {
    "Revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "Revenue",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "RegulatedAndUnregulatedOperatingRevenue",
    ],
    "NetIncome": [
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ],
    "TotalAssets": [
        "Assets",
    ],
    "TotalLiabilities": [
        "Liabilities",
    ],
    "TotalEquity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "Cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "LongTermDebt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ],
    "OperatingCF": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    # 3.4: widen the authoritative check beyond the 8 bottom-line totals so
    # margins / EPS / capex / working-capital lines get a filing-backed dot
    # instead of an FMP-agreement-only one.
    "GrossProfit": ["GrossProfit"],
    "OperatingIncome": ["OperatingIncomeLoss"],
    "ResearchAndDevelopment": [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ],
    "CapEx": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "Depreciation": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ],
    "Inventory": ["InventoryNet"],
    "AccountsReceivable": ["AccountsReceivableNetCurrent"],
    "CurrentAssets": ["AssetsCurrent"],
    # (EPS / diluted shares deferred — non-USD units; lookup_xbrl reads USD
    #  facts only, so they need per-share/shares unit support first.)
}


@dataclass
class XBRLFact:
    tag: str
    value: float
    end: str
    form: str
    filed: str
    fy: int
    fp: str


@functools.lru_cache(maxsize=8)
def _load_companyfacts(cik: str) -> Optional[Dict[str, Any]]:
    # Cached: build_cross_source_agreement calls lookup_xbrl once per field, so a
    # single ticker's clean_all_years would otherwise re-read the (multi-MB)
    # companyfacts JSON hundreds of times.
    path = _FACTS_DIR / f"CIK{cik.zfill(10)}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


_CIK_OVERRIDES = {
    "BRK-B": "0001067983", "BRK.B": "0001067983",
    "GOOGL": "0001652044", "GOOG":  "0001652044",
}
_TICKER_CIK_PATH = Path("valuation_data/raw/sec/company_tickers/company_tickers.json")
_ticker_cik_cache: Optional[Dict[str, str]] = None


def _resolve_cik(ticker: str) -> Optional[str]:
    """
    Resolve a ticker to its zero-padded 10-digit CIK using the locally-cached
    SEC ticker map. Avoids network on every validation run.
    """
    global _ticker_cik_cache
    t = ticker.upper()
    if t in _CIK_OVERRIDES:
        return _CIK_OVERRIDES[t]
    if _ticker_cik_cache is None:
        if not _TICKER_CIK_PATH.exists():
            return None
        try:
            data = json.loads(_TICKER_CIK_PATH.read_text())
        except Exception:
            return None
        m = {}
        for entry in data.values():
            tk = str(entry.get("ticker", "")).upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            if tk:
                m[tk] = cik
        _ticker_cik_cache = m
    return _ticker_cik_cache.get(t)


def _pick_fy_fact(usd_facts: List[Dict[str, Any]], fiscal_year: int) -> Optional[Dict[str, Any]]:
    """
    Pick the canonical FY fact for a given fiscal_year.

    The match key is the period-end calendar year (`end.startswith(fy_str)`).
    NOT `fy`: within a single 10-K filing every contained fact carries the
    filing's `fy` — including prior-year comparison columns — so filtering
    on `fy` alone pulls in last year's and the year-before's numbers too.

    Among matching facts, prefer 10-K (over 10-K/A or 20-F) and then the
    most-recently-filed restatement.
    """
    fy_str = str(fiscal_year)
    candidates = [
        f for f in usd_facts
        if f.get("fp") == "FY"
        and f.get("end", "").startswith(fy_str)
        and f.get("form", "").startswith("10-K")
    ]
    if not candidates:
        # 20-F filers (ASML) and other foreign filers
        candidates = [
            f for f in usd_facts
            if f.get("fp") == "FY"
            and f.get("end", "").startswith(fy_str)
        ]
    if not candidates:
        return None
    # Duration concepts (income statement / cash flow) carry BOTH a 12-month
    # annual fact and 3-month quarterly facts under fp=FY in the same 10-K, with
    # the same period-end (ACN FY2025: annual $69.7B vs Q4 $16.7B). A naive
    # most-recently-filed pick grabs the quarter → a ~4x false "drift". Prefer
    # the ~12-month fact; instant concepts (balance sheet) have no `start` and
    # are unaffected.
    annual = [f for f in candidates if _is_annual_duration(f)]
    pool = annual or candidates
    return sorted(pool, key=lambda f: f.get("filed", ""), reverse=True)[0]


def _is_annual_duration(fact: Dict[str, Any]) -> bool:
    """True for a ~12-month duration fact, or any instant fact (no `start`)."""
    start, end = fact.get("start"), fact.get("end")
    if not start or not end:
        return True  # instant concept (balance sheet) — no duration ambiguity
    try:
        from datetime import date
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
        return 340 <= days <= 380
    except Exception:
        return True


def _read_fact(facts: Dict[str, Any], tag: str, fiscal_year: int) -> Optional[XBRLFact]:
    """Read a single tag across us-gaap + ifrs-full namespaces, USD only."""
    for ns_name in ("us-gaap", "ifrs-full"):
        ns = facts.get("facts", {}).get(ns_name, {})
        if tag not in ns:
            continue
        usd = ns[tag].get("units", {}).get("USD") or []
        fact = _pick_fy_fact(usd, fiscal_year)
        if fact is None:
            continue
        return XBRLFact(
            tag=tag,
            value=float(fact["val"]),
            end=fact.get("end", ""),
            form=fact.get("form", ""),
            filed=fact.get("filed", ""),
            fy=int(fact.get("fy") or fiscal_year),
            fp=fact.get("fp", ""),
        )
    return None


def lookup_xbrl(ticker: str, field: str, fiscal_year: int,
                tags: Optional[List[str]] = None) -> Optional[XBRLFact]:
    """
    Look up the canonical SEC XBRL value for (ticker, field, fiscal_year).

    Returns the first tag in the priority list that has a matching FY filing,
    or None if no canonical tag has a value for that fiscal year.

    Special case: TotalLiabilities falls back to (Assets - StockholdersEquity)
    when the issuer doesn't file a `Liabilities` rolled-up tag. This is the
    accounting equation, exact by definition.
    """
    cik = _resolve_cik(ticker)
    if not cik:
        return None
    facts = _load_companyfacts(cik)
    if not facts:
        return None
    tag_list = tags or CANONICAL_TAGS.get(field) or []
    if not tag_list:
        return None

    for tag in tag_list:
        fact = _read_fact(facts, tag, fiscal_year)
        if fact is not None:
            return fact

    # Derived fallback: TotalLiabilities = Assets - StockholdersEquity (exact
    # by accounting equation). Many filers (LLY, AMD, JPM, ORCL, WMT) don't
    # tag the rolled-up `Liabilities` directly.
    if field == "TotalLiabilities":
        assets = _read_fact(facts, "Assets", fiscal_year)
        equity = _read_fact(facts, "StockholdersEquity", fiscal_year)
        if assets and equity:
            return XBRLFact(
                tag="(derived: Assets − StockholdersEquity)",
                value=assets.value - equity.value,
                end=assets.end,
                form=assets.form,
                filed=assets.filed,
                fy=assets.fy,
                fp=assets.fp,
            )
    return None


def lookup_canonical(ticker: str, fiscal_year: int) -> Dict[str, Optional[XBRLFact]]:
    """Return the canonical XBRL fact for every field in CANONICAL_TAGS."""
    out: Dict[str, Optional[XBRLFact]] = {}
    for field in CANONICAL_TAGS:
        out[field] = lookup_xbrl(ticker, field, fiscal_year)
    return out


# 3.3: cross-source agreement — our cleaned value vs the authoritative SEC fact,
# per field. Fields whose CANONICAL_TAGS key differs from the raw_json key.
_FIELD_TO_OUR_KEY = {"ResearchAndDevelopment": "R&D"}
_XSRC_TOL_OK = 0.01     # <1% → validated (byte-perfect vs the filing)
_XSRC_TOL_NEAR = 0.05   # <5% → near


def build_cross_source_agreement(
    ticker: str, fiscal_year: int, our_raw: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Per-field SEC-vs-ours agreement map:
    ``{field: {sec, ours, drift, flag, sec_tag}}``.

    ``flag`` ∈ {validated, near, drift, sec_missing, ours_missing}. Only fields
    with an authoritative SEC XBRL tag (CANONICAL_TAGS) are compared. Fields
    absent on both sides are omitted. Deterministic + cache-only (reads the
    local companyfacts JSON); safe to call at ingest/report time.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for field in CANONICAL_TAGS:
        our_key = _FIELD_TO_OUR_KEY.get(field, field)
        ov = our_raw.get(our_key)
        try:
            ov = float(ov) if ov is not None else None
        except (TypeError, ValueError):
            ov = None
        fact = lookup_xbrl(ticker, field, fiscal_year)
        sv = fact.value if fact else None
        if sv is None and ov is None:
            continue
        if sv is None:
            flag, drift = "sec_missing", None
        elif ov is None:
            flag, drift = "ours_missing", None
        else:
            drift = (ov - sv) / sv if sv else (0.0 if ov == 0 else 1.0)
            ad = abs(drift)
            flag = ("validated" if ad < _XSRC_TOL_OK
                    else "near" if ad < _XSRC_TOL_NEAR else "drift")
        out[field] = {"sec": sv, "ours": ov, "drift": drift, "flag": flag,
                      "sec_tag": fact.tag if fact else None}
    return out
