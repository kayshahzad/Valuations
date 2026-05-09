"""Refetch SEC EDGAR companyfacts JSON for the curated universe.

The TTM ingestion path (`scripts/ingest_ttm.py`) prefers SEC-derived
TTM when fresh, but falls back to FMP when our local SEC raw cache
lags FMP's quarterly data. This refresh job pulls the latest
companyfacts JSON from SEC EDGAR per ticker so subsequent TTM ingests
flip back to SEC primary on any ticker that filed a new 10-Q.

Usage:
    python scripts/refresh_sec_cache.py --all
    python scripts/refresh_sec_cache.py --ticker AAPL --ticker MSFT

SEC EDGAR has a 10 req/sec guideline — we don't approach the limit
even when refreshing the full universe (~50 tickers). Failures
(network, 404, etc.) are logged per-ticker; the rest of the universe
keeps refreshing.

Foreign filers (ASML, TSM) are still refreshed — their 20-F facts
update annually, and their cached JSON should track that. The
TTM-derivation path skips them anyway because they don't file 10-Qs.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional

from aletheia.data.edgar_client import RAW_DIR, SecEdgar
from config.ticker_classification import get_extended_universe


def _select_tickers(
    explicit: Optional[List[str]], all_universe: bool,
) -> List[str]:
    if explicit:
        return [t.upper() for t in explicit]
    if not all_universe:
        return []
    return sorted(get_extended_universe().keys())


def _latest_period_end(facts_blob: Dict) -> Optional[str]:
    """Pull the most recent end date across all 10-Q facts under
    us-gaap.RevenueFromContractWithCustomerExcludingAssessedTax (or
    fallback Revenues). Used for before/after diffs so the caller can
    see what the refresh actually moved forward."""
    gaap = (facts_blob or {}).get("facts", {}).get("us-gaap", {})
    for tag in ("RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet"):
        usd = gaap.get(tag, {}).get("units", {}).get("USD", []) or []
        tenq = [f for f in usd if f.get("form") in ("10-Q", "10-Q/A")]
        if tenq:
            tenq.sort(key=lambda f: f.get("end", ""), reverse=True)
            return tenq[0].get("end")
    return None


def _refresh_one(ticker: str, sec: SecEdgar) -> Dict[str, str]:
    cik = sec.resolve_cik(ticker)
    if not cik:
        return {"ticker": ticker, "outcome": "cik_unresolved",
                "before": "", "after": ""}

    raw_path = RAW_DIR / f"CIK{cik}.json"
    before = ""
    if raw_path.exists():
        try:
            before = _latest_period_end(json.loads(raw_path.read_text())) or ""
        except Exception:
            before = ""

    facts = sec.fetch_company_facts(cik)
    if facts is None:
        return {"ticker": ticker, "outcome": "fetch_failed",
                "before": before, "after": ""}

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(facts, indent=2))
    after = _latest_period_end(facts) or ""

    if not before:
        outcome = "fetched_new"
    elif after > before:
        outcome = "advanced"
    elif after == before:
        outcome = "no_change"
    else:
        outcome = "regressed"   # shouldn't happen — flag if it does

    return {"ticker": ticker, "outcome": outcome,
            "before": before, "after": after}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--ticker", action="append",
                   help="One or more tickers (repeatable)")
    g.add_argument("--all", action="store_true",
                   help="Refresh the full curated universe")
    args = p.parse_args(argv)

    tickers = _select_tickers(args.ticker, args.all)
    if not tickers:
        print("No tickers selected.", file=sys.stderr)
        return 2

    sec = SecEdgar()
    tally: Dict[str, int] = {}
    rows = []
    for t in tickers:
        try:
            row = _refresh_one(t, sec)
        except Exception as exc:
            row = {"ticker": t, "outcome": f"error:{type(exc).__name__}",
                   "before": "", "after": str(exc)[:60]}
        rows.append(row)
        tally[row["outcome"]] = tally.get(row["outcome"], 0) + 1
        bp = row["before"] or "—"
        ap = row["after"] or "—"
        print(f"  {row['ticker']:6s}  {row['outcome']:14s}  {bp} → {ap}")

    print()
    print(json.dumps({"tally": tally, "tickers_attempted": len(tickers)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
