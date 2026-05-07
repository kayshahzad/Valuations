"""
aletheia/backtest/point_in_time.py

Point-in-time data loader. Given (ticker, as_of_date), returns the cleaned
multi-year history that would have been knowable at as_of_date — only
filings whose `filed` date <= as_of_date are visible.

This is the central correctness property of the backtest. Without it, the
engine "predicts" using restated XBRL values that weren't available at
prediction time, manufacturing fake edge.

Strategy:
  1. Read the original CIK companyfacts JSON
  2. Walk every concept's units array; drop any entry with `filed > as_of_date`
  3. Write the filtered JSON to a temp directory along with company_tickers.json
  4. Build a fresh TagResolver pointed at the temp dir, monkey-patch the
     cleaning_engine module-level singleton
  5. Run CleaningEngine(canonical_dir=<nonexistent>) so canonical Parquet
     is bypassed and everything flows through the filtered raw facts
  6. Return the resulting CleanedRecord list plus metadata about the
     most-recent filing that was actually used (latest `filed` date in the
     filtered set, capped to <= as_of_date)
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Tuple

from aletheia.data import cleaning_engine as cleaning_engine_module
from aletheia.data.cleaning_engine import CleanedRecord, CleaningEngine
from aletheia.data.tag_resolver import TagResolver


@dataclass(frozen=True)
class PointInTimeLoadResult:
    """Result of a point-in-time cleaning run."""
    ticker: str
    as_of_date: date
    records: List[CleanedRecord]
    fiscal_year_used: int          # latest FY in the records
    filing_date_used: date          # `filed` date of that FY's annual filing
    raw_facts_count: int           # how many fact entries survived the filter


def _resolve_cik(ticker: str, raw_dir: Path) -> Optional[str]:
    """Look up a 10-digit zero-padded CIK from company_tickers.json."""
    overrides = {"BRK-B": "0001067983", "BRK.B": "0001067983",
                 "GOOGL": "0001652044", "GOOG": "0001652044"}
    if ticker in overrides:
        return overrides[ticker]
    cik_path = raw_dir / "company_tickers" / "company_tickers.json"
    if not cik_path.exists():
        return None
    with open(cik_path) as f:
        data = json.load(f)
    for _, v in data.items():
        if v["ticker"].upper() == ticker.upper():
            return str(v["cik_str"]).zfill(10)
    return None


def _filter_facts_by_filing_date(
    raw_data: dict, as_of_date: date,
) -> Tuple[dict, int, Optional[date]]:
    """
    Walk facts and prune any unit entry where `filed` > as_of_date.
    Concepts with no surviving entries are dropped. Returns
    (filtered_facts, total_surviving_count, latest_filed_date).
    """
    out = {"cik": raw_data.get("cik"), "entityName": raw_data.get("entityName"),
           "facts": {}}
    surviving = 0
    latest_filed: Optional[date] = None
    cutoff_str = as_of_date.isoformat()

    for ns, ns_facts in raw_data.get("facts", {}).items():
        out["facts"][ns] = {}
        for tag, concept in ns_facts.items():
            units_out = {}
            for unit_type, units in concept.get("units", {}).items():
                kept = []
                for u in units:
                    filed = u.get("filed")
                    if not filed:
                        continue
                    if filed > cutoff_str:
                        continue
                    kept.append(u)
                    surviving += 1
                    try:
                        d = datetime.strptime(filed, "%Y-%m-%d").date()
                        if latest_filed is None or d > latest_filed:
                            latest_filed = d
                    except (ValueError, TypeError):
                        pass
                if kept:
                    units_out[unit_type] = kept
            if units_out:
                out["facts"][ns][tag] = {
                    "label": concept.get("label"),
                    "description": concept.get("description"),
                    "units": units_out,
                }
        if not out["facts"][ns]:
            del out["facts"][ns]
    return out, surviving, latest_filed


def _latest_annual_filing_date(
    filtered_data: dict, fiscal_year: int,
) -> Optional[date]:
    """
    Find the `filed` date of the 10-K/20-F/40-F filing for the given fiscal
    year (the "primary" annual filing). We use the latest `filed` date across
    Revenue facts where fy=fiscal_year and form is annual — that's effectively
    when this fiscal year's data was first publicly disclosed.
    """
    facts = filtered_data.get("facts", {})
    candidates: List[date] = []
    for ns, ns_facts in facts.items():
        for tag, concept in ns_facts.items():
            if tag not in ("Revenues", "Revenue", "RevenueFromContractWithCustomerExcludingAssessedTax",
                           "SalesRevenueNet", "SalesRevenueGoodsNet"):
                continue
            for unit_type, units in concept.get("units", {}).items():
                for u in units:
                    if u.get("form") not in ("10-K", "20-F", "40-F"):
                        continue
                    if u.get("fy") != fiscal_year:
                        continue
                    filed = u.get("filed")
                    if not filed:
                        continue
                    try:
                        candidates.append(
                            datetime.strptime(filed, "%Y-%m-%d").date()
                        )
                    except (ValueError, TypeError):
                        continue
    if not candidates:
        return None
    return min(candidates)  # earliest filing date for this FY = original 10-K


def load_point_in_time(
    ticker: str,
    as_of_date: date,
    canonical_dir: str = "valuation_data/canonical/financials",
    raw_dir: str = "valuation_data/raw/sec",
    verbose: bool = False,
) -> Optional[PointInTimeLoadResult]:
    """
    Run the cleaning pipeline using only filings publicly available at
    `as_of_date`. Returns None if insufficient data (no annual filings
    before that date).
    """
    raw_dir_p = Path(raw_dir)
    cik = _resolve_cik(ticker, raw_dir_p)
    if not cik:
        return None
    src_facts = raw_dir_p / "companyfacts" / f"CIK{cik}.json"
    if not src_facts.exists():
        return None

    with open(src_facts) as f:
        raw_data = json.load(f)

    filtered, surviving_count, _ = _filter_facts_by_filing_date(raw_data, as_of_date)
    if surviving_count == 0 or not filtered.get("facts"):
        return None

    tmpdir = Path(tempfile.mkdtemp(prefix=f"pit_{ticker}_"))
    try:
        (tmpdir / "companyfacts").mkdir(parents=True, exist_ok=True)
        (tmpdir / "company_tickers").mkdir(parents=True, exist_ok=True)
        with open(tmpdir / "companyfacts" / f"CIK{cik}.json", "w") as f:
            json.dump(filtered, f)
        # Copy the ticker→CIK map so TagResolver can look up the ticker
        src_tickers = raw_dir_p / "company_tickers" / "company_tickers.json"
        if src_tickers.exists():
            shutil.copy(src_tickers, tmpdir / "company_tickers" / "company_tickers.json")

        # Swap the module-level tag resolver with one pointing at our temp dir.
        # CleaningEngine._load_and_pivot uses this singleton (line 480).
        original_resolver = cleaning_engine_module._tag_resolver
        try:
            cleaning_engine_module._tag_resolver = TagResolver(raw_dir=str(tmpdir))
            # canonical_dir intentionally points at a nonexistent dir so the
            # cleaning engine bypasses canonical Parquet (which is computed
            # off the FULL filing history, not point-in-time).
            engine = CleaningEngine(
                canonical_dir=str(tmpdir / "no_canonical"),
                raw_dir=str(tmpdir),
                verbose=verbose,
            )
            records = engine.clean_all_years(ticker)
        finally:
            cleaning_engine_module._tag_resolver = original_resolver

        # Filter to records with revenue (skip stub rows from FY+1 fallbacks
        # that may produce hollow records when only future data leaks through)
        good_records = [
            r for r in records
            if r.raw.get("Revenue") is not None and r.raw.get("Revenue") > 0
        ]
        if not good_records:
            return None
        # Pick the latest fiscal year as "the" filing used for signal generation
        latest = max(good_records, key=lambda r: r.fiscal_year)
        filing_date = _latest_annual_filing_date(filtered, latest.fiscal_year)
        if filing_date is None:
            # Fall back to the most-recent filed date in the filter cutoff
            filing_date = as_of_date
        if filing_date > as_of_date:
            # Sanity: the chosen FY's filing post-dates the as_of_date; downgrade
            # to the previous FY
            prior_records = [r for r in good_records if r.fiscal_year < latest.fiscal_year]
            if not prior_records:
                return None
            latest = max(prior_records, key=lambda r: r.fiscal_year)
            filing_date = _latest_annual_filing_date(filtered, latest.fiscal_year)
            if filing_date is None or filing_date > as_of_date:
                return None
            good_records = [r for r in good_records if r.fiscal_year <= latest.fiscal_year]

        return PointInTimeLoadResult(
            ticker=ticker,
            as_of_date=as_of_date,
            records=good_records,
            fiscal_year_used=latest.fiscal_year,
            filing_date_used=filing_date,
            raw_facts_count=surviving_count,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
