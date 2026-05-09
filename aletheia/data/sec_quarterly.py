"""Phase Q-2 MVP: derive a TTM CleanedRecord from SEC XBRL 10-Q facts.

SEC reports 10-Q facts CUMULATIVELY year-to-date — a `fp=Q3` entry is
the first 9 months of the fiscal year, not just Q3 standalone. The TTM
formula leveraging this:

    TTM = prior_FY_annual + latest_cumulative_Q − prior_year_same_Q_cumulative

Worked example (AAPL, 10-Q filed Dec 2025):
    latest:       fy=2026, fp=Q1, end=2025-12-27, val=143.8B (3 months)
    prior FY-1:   fy=2025, fp=Q1, end=2024-12-28, val=124.3B (3 months)
    annual FY-1:  fy=2025 10-K, end=2025-09-27,  val=394.7B (12 months)
    TTM revenue = 394.7B + 143.8B − 124.3B = 414.2B

Compared to the FMP-derived path:
  - SEC is the authoritative source. Both arms of Gate A.TTM (SEC-summed
    vs FMP /key-metrics-ttm) now trace to independent computations on
    the same XBRL facts. Drift > 0.5% is a real bug, not a methodology
    gap. The byte_perfect_required tier on Gate A.TTM regains its teeth.
  - When SEC quarterly is missing (foreign filer, mid-quarter window
    where 10-Q hasn't filed yet, etc.), caller falls back to
    `derive_ttm_from_fmp()` and the receipt stamps
    `ttm_source='fmp_derived_quarters'` instead.

The module is read-only against the cached XBRL companyfacts JSON. No
live SEC fetches; that already happens during the standard ingest.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aletheia.data.cleaning_engine import CleanedRecord
from config.tag_mappings import FIELD_MAPPINGS


_RAW_DIR = Path("valuation_data/raw/sec/companyfacts")


# Tags we sum from 10-Q (cumulative-YTD shape). Stocks (TotalAssets,
# Cash, Equity, etc.) are point-in-time and use the latest 10-Q
# instant fact, NOT the cumulative formula.
_FLOW_FIELDS = (
    "Revenue", "NetIncome", "OperatingIncome",
    "OperatingCF", "CapEx",
)
_STOCK_FIELDS = (
    "TotalAssets", "TotalLiabilities", "TotalEquity",
    "Cash", "LongTermDebt",
)
# Standalone-3-month tags (no cumulative wrap). Diluted shares are a
# weighted average within the period; we take the latest 10-Q's value.
_INSTANT_FIELDS = ("SharesDiluted",)


def _cik_for(ticker: str) -> Optional[str]:
    """Reuse the tag_resolver's CIK lookup so we don't duplicate the
    SEC ticker → CIK mapping."""
    from aletheia.data.tag_resolver import TagResolver
    tr = TagResolver()
    return tr._get_cik(ticker.upper())


def _load_facts(ticker: str) -> Optional[Dict[str, Any]]:
    cik = _cik_for(ticker)
    if not cik:
        return None
    path = _RAW_DIR / f"CIK{cik}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _facts_for_tag(facts: Dict[str, Any], tag: str) -> List[Dict[str, Any]]:
    """All USD-unit facts under us-gaap.<tag> across every form."""
    return (
        facts.get("facts", {})
             .get("us-gaap", {})
             .get(tag, {})
             .get("units", {})
             .get("USD", [])
    )


def _resolve_field(
    facts: Dict[str, Any], canonical_name: str, form: str,
) -> Optional[List[Dict[str, Any]]]:
    """Walk the FIELD_MAPPINGS fallback list for `canonical_name` and
    return the FULL fact list for the first tag that has at least one
    record of `form`. Mirrors the cleaning engine's tag-fallback
    resolution policy.

    Important: returns ALL facts under the matched tag, not just those
    matching `form`. Downstream needs both 10-Q (cumulative period
    values) and 10-K (annual anchor) under the same tag for the TTM
    formula to work."""
    rules = FIELD_MAPPINGS.get(canonical_name, {})
    for tag in rules.get("default", []):
        candidates = _facts_for_tag(facts, tag)
        if not candidates:
            continue
        if any(f.get("form") == form for f in candidates):
            return candidates
    return None


def _period_months(fact: Dict[str, Any]) -> Optional[int]:
    """Return the period length in months for a 10-Q fact, derived
    from start/end dates. Returns None for instant facts (no `start`).

    Quarterly cumulative shapes:
      Q1 → 3 months
      Q2 → 6 months
      Q3 → 9 months
    Annual (10-K) → 12 months."""
    start = fact.get("start")
    end = fact.get("end")
    if not (start and end):
        return None
    try:
        s = datetime.date.fromisoformat(start)
        e = datetime.date.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    return round((e - s).days / 30.4)


def _latest_quarterly(
    facts_list: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Pick the most recent 10-Q fact, breaking ties by `end`."""
    tenq = [f for f in facts_list if f.get("form") in ("10-Q", "10-Q/A")]
    if not tenq:
        return None
    tenq.sort(key=lambda f: f.get("end", ""), reverse=True)
    return tenq[0]


def _matching_prior_year(
    facts_list: List[Dict[str, Any]],
    target_fy: int,
    target_fp: str,
) -> Optional[Dict[str, Any]]:
    """Find the same (fp) cumulative fact one fiscal year earlier.

    SEC re-tags prior-period comparatives with the current filing's
    fy attribute — so a single (fy, fp) tuple can match multiple
    records. Pick the one whose `end` date is most recent: that's the
    actual period being reported, not a comparative."""
    matches = [
        f for f in facts_list
        if (f.get("form") in ("10-Q", "10-Q/A")
            and f.get("fy") == target_fy - 1
            and f.get("fp") == target_fp)
    ]
    if not matches:
        return None
    matches.sort(key=lambda f: f.get("end", ""), reverse=True)
    return matches[0]


def _annual_fact(
    facts_list: List[Dict[str, Any]], fy: int,
) -> Optional[Dict[str, Any]]:
    """Pick the 10-K fact for fiscal year `fy`. SEC re-tags prior-year
    comparatives with the current filing's fy, so we can't trust fy
    alone — filter to fp=FY (the canonical annual marker) and pick the
    most recent `end` date among matches."""
    matches = [
        f for f in facts_list
        if (f.get("form") in ("10-K", "10-K/A")
            and f.get("fy") == fy
            and f.get("fp") == "FY")
    ]
    if matches:
        matches.sort(key=lambda f: f.get("end", ""), reverse=True)
        return matches[0]
    # Loose fallback: any 10-K fact for that fy, latest end first
    fallbacks = [
        f for f in facts_list
        if f.get("form") in ("10-K", "10-K/A") and f.get("fy") == fy
    ]
    if not fallbacks:
        return None
    fallbacks.sort(key=lambda f: f.get("end", ""), reverse=True)
    return fallbacks[0]


def _ttm_from_cumulative(
    facts_list: List[Dict[str, Any]],
) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
    """Apply the TTM formula on a flow tag's fact list.
    Returns (ttm_value, latest_q_fact_used) so caller can stamp
    period_end_date and (fy, fp) in the result."""
    latest_q = _latest_quarterly(facts_list)
    if latest_q is None:
        return None, None

    target_fy = latest_q.get("fy")
    target_fp = latest_q.get("fp")
    if not (target_fy and target_fp):
        return None, latest_q

    prior_q = _matching_prior_year(facts_list, target_fy, target_fp)
    prior_annual = _annual_fact(facts_list, target_fy - 1)

    if not (prior_q and prior_annual):
        return None, latest_q

    try:
        ttm = (
            float(prior_annual["val"])
            + float(latest_q["val"])
            - float(prior_q["val"])
        )
    except (TypeError, ValueError, KeyError):
        return None, latest_q
    return ttm, latest_q


def _instant_value(
    facts: Dict[str, Any], canonical_name: str,
) -> Optional[float]:
    """For balance-sheet stocks (TotalAssets, Cash, etc.), pick the
    most recent 10-Q instant fact."""
    rules = FIELD_MAPPINGS.get(canonical_name, {})
    for tag in rules.get("default", []):
        all_facts = _facts_for_tag(facts, tag)
        if not all_facts:
            continue
        instants = [
            f for f in all_facts
            if f.get("end") and not f.get("start")
            and f.get("form") in ("10-Q", "10-Q/A", "10-K", "10-K/A")
        ]
        if not instants:
            continue
        instants.sort(key=lambda f: f.get("end", ""), reverse=True)
        try:
            return float(instants[0]["val"])
        except (TypeError, ValueError, KeyError):
            continue
    return None


@dataclass
class SECTTMResult:
    record: Optional[CleanedRecord]
    skip_reason: Optional[str]
    latest_quarter_fact: Optional[Dict[str, Any]] = None


def derive_ttm_from_sec(ticker: str) -> SECTTMResult:
    """Build a TTM CleanedRecord from cached SEC XBRL 10-Q facts.
    Returns SECTTMResult with skip_reason set when:
      - companyfacts JSON not on disk
      - latest 10-Q facts incomplete (no prior-year same-quarter or
        no prior-FY 10-K to anchor the math)
      - required flow fields (revenue / NI) can't be resolved

    On success the record carries period='TTM' and
    fmp_validation['ttm_source']='sec_derived_quarters'."""
    facts = _load_facts(ticker)
    if facts is None:
        return SECTTMResult(None, "sec_companyfacts_not_cached")

    revenue_facts = _resolve_field(facts, "Revenue", "10-Q")
    if not revenue_facts:
        return SECTTMResult(None, "sec_revenue_tag_unresolved")
    revenue, latest_q = _ttm_from_cumulative(revenue_facts)
    if revenue is None:
        return SECTTMResult(
            None,
            "sec_ttm_math_unresolved:missing_prior_year_or_fy_anchor",
            latest_quarter_fact=latest_q,
        )
    if latest_q is None:
        return SECTTMResult(None, "sec_no_latest_quarter_fact")

    target_fy = latest_q.get("fy")
    period_end = latest_q.get("end")

    def _flow_ttm(canonical: str) -> Optional[float]:
        f_list = _resolve_field(facts, canonical, "10-Q")
        if not f_list:
            return None
        ttm, _ = _ttm_from_cumulative(f_list)
        return ttm

    net_income = _flow_ttm("NetIncome")
    if net_income is None:
        return SECTTMResult(
            None, "sec_net_income_ttm_unresolved",
            latest_quarter_fact=latest_q,
        )

    operating_income = _flow_ttm("OperatingIncome")
    operating_cf     = _flow_ttm("OperatingCF")
    capex            = _flow_ttm("CapEx")  # FMP returns negative; SEC reports positive payments
    # Make CapEx sign-convention consistent with FMP's sign (negative).
    if capex is not None and capex > 0:
        capex = -abs(capex)

    # Stocks — latest 10-Q instant facts
    total_assets       = _instant_value(facts, "TotalAssets")
    total_liabilities  = _instant_value(facts, "TotalLiabilities")
    total_equity       = _instant_value(facts, "TotalEquity")
    cash               = _instant_value(facts, "Cash")
    long_term_debt     = _instant_value(facts, "LongTermDebt")

    # FCF = OpCF - CapEx (CapEx already negative on flow side)
    fcf = (
        (operating_cf + capex)
        if (operating_cf is not None and capex is not None)
        else None
    )

    # NetDebt
    net_debt = None
    if long_term_debt is not None or cash is not None:
        net_debt = (long_term_debt or 0.0) - (cash or 0.0)

    # Margins (decimals) — keep parity with FMP-derived shape (ours are percent)
    ebit_margin_pct = (
        (operating_income / revenue) * 100.0
        if operating_income is not None and revenue else None
    )
    fcf_margin_pct = (
        (fcf / revenue) * 100.0
        if fcf is not None and revenue else None
    )

    # ROE: NetIncome / TotalEquity
    roe = (
        (net_income / total_equity)
        if total_equity is not None and total_equity > 0 else None
    )

    record = CleanedRecord(
        ticker=ticker.upper(),
        fiscal_year=int(target_fy or 0),
        period="TTM",
        period_end_date=period_end,
    )
    record.raw = {
        "Revenue":          revenue,
        "NetIncome":        net_income,
        "OperatingIncome":  operating_income,
        "OperatingCF":      operating_cf,
        "CapEx":            capex,
        "TotalAssets":      total_assets,
        "TotalLiabilities": total_liabilities,
        "TotalEquity":      total_equity,
        "Cash":             cash,
        "LongTermDebt":     long_term_debt,
    }
    record.clean = {
        "Revenue": revenue,
        "FCF":     fcf,
    }
    record.derived = {
        "OperatingIncome":   operating_income,
        "CapEx":             capex,
        "FCF":               fcf,
        "NetDebt":           net_debt,
        "ROE":               roe,
        "EBIT_Margin_Pct":   ebit_margin_pct,
        "FCF_Margin_Pct":    fcf_margin_pct,
    }
    record.fmp_validation = {
        "status":      "validated",   # provisional; tightened by Gate A.TTM
        "ttm_source":  "sec_derived_quarters",
        "fields":      {},
    }
    return SECTTMResult(record=record, skip_reason=None,
                        latest_quarter_fact=latest_q)
