"""Phase Q-4 MVP entrypoint — ingest a TTM CleanedRecord per ticker.

Reads the curated universe from config/ticker_classification.py, calls
`derive_ttm_from_fmp()` for each, runs Gate A.TTM, and persists the
record with `period='TTM'` to DuckDB. Records that breach the
byte_perfect_required band (P0 drift on revenue / NI / FCF) are NOT
persisted — caller treats this as an FMP-internal regression that
needs investigation before the data can be trusted.

Usage:
    python scripts/ingest_ttm.py [--ticker AAPL]
                                 [--all]
                                 [--ddm-skip]    # skip ddm_required tickers (default)
                                 [--include-foreign]  # ASML/TSM (currency-blocked)

Output: a one-line per-ticker summary on stdout, plus a final tally:
    {validated: 35, drift: 3, blocking_drift: 1, skipped: 1}

Exit code is non-zero only when --all is passed AND blocking_drift count > 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional

from aletheia.data import fmp_client
from aletheia.data.database import InvestmentDatabase
from aletheia.data.fmp_validation import validate_ttm_record
from aletheia.data.ttm_derivation import derive_ttm_from_fmp
from config.ticker_classification import get_extended_universe


_DDM_MODELS = {"ddm_required", "embedded_value_required", "routing_required"}


def _select_tickers(
    explicit: Optional[List[str]],
    all_universe: bool,
    ddm_skip: bool,
    include_foreign: bool,
) -> List[str]:
    if explicit:
        return [t.upper() for t in explicit]
    if not all_universe:
        return []
    universe = get_extended_universe()
    out = []
    for ticker, meta in sorted(universe.items()):
        if ddm_skip and meta.business_model in _DDM_MODELS:
            continue
        if not include_foreign and meta.is_ifrs_filer:
            continue
        out.append(ticker)
    return out


def _process_one(ticker: str, db: InvestmentDatabase) -> Dict[str, str]:
    """Returns a per-ticker result dict for the summary tally."""
    derivation = derive_ttm_from_fmp(ticker)

    if derivation.record is None:
        return {
            "ticker":      ticker,
            "outcome":     "skipped",
            "skip_reason": derivation.skip_reason or "unknown",
            "blocking":    "",
        }

    # Phase Q-6 full second-source endpoints. Fetch failures degrade
    # the corresponding lane to status='n_a' instead of breaking the
    # whole TTM ingest — Gate A.TTM still runs on the primary lane.
    try:
        ev_quarters = fmp_client.fetch_enterprise_values(ticker, period="quarter")
        ev_latest_quarter = ev_quarters[0] if ev_quarters else None
    except Exception:
        ev_latest_quarter = None

    try:
        as_reported_quarters = fmp_client.fetch_income_statement_as_reported_quarter(ticker)
        as_reported_latest = as_reported_quarters[0] if as_reported_quarters else None
    except Exception:
        as_reported_latest = None

    gate = validate_ttm_record(
        ticker, derivation.record,
        fmp_key_metrics_ttm=derivation.fmp_key_metrics_ttm,
        fmp_ratios_ttm=derivation.fmp_ratios_ttm,
        fmp_ev_latest_quarter=ev_latest_quarter,
        fmp_income_as_reported_quarter=as_reported_latest,
        latest_quarter_income=derivation.latest_quarter_income,
    )
    derivation.record.fmp_validation = gate

    if gate["status"] == "blocking_drift":
        # Mirror Gate A's policy: don't persist a record that failed
        # byte-perfect-required cross-check. Caller investigates the
        # FMP-internal inconsistency before retrying.
        return {
            "ticker":      ticker,
            "outcome":     "blocking_drift",
            "skip_reason": "",
            "blocking":    ",".join(gate["blocking_fields"]),
        }

    db.upsert_record(derivation.record)
    return {
        "ticker":      ticker,
        "outcome":     gate["status"],   # validated | drift
        "skip_reason": "",
        "blocking":    "",
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--ticker", action="append", help="One or more tickers (repeatable)")
    g.add_argument("--all", action="store_true", help="Run the curated US universe")
    p.add_argument("--ddm-skip", action="store_true", default=True,
                   help="Skip ddm_required / routing_required filers (default)")
    p.add_argument("--no-ddm-skip", dest="ddm_skip", action="store_false")
    p.add_argument("--include-foreign", action="store_true",
                   help="Include IFRS filers (ASML/TSM) — currently currency-blocked")
    args = p.parse_args(argv)

    tickers = _select_tickers(args.ticker, args.all, args.ddm_skip, args.include_foreign)
    if not tickers:
        print("No tickers selected.", file=sys.stderr)
        return 2

    db = InvestmentDatabase(verbose=False)
    tally = {"validated": 0, "drift": 0, "blocking_drift": 0, "skipped": 0}
    rows = []

    try:
        for t in tickers:
            try:
                row = _process_one(t, db)
            except Exception as exc:
                # Defensive — derivation/validation must never break the loop.
                row = {"ticker": t, "outcome": "error",
                       "skip_reason": f"{type(exc).__name__}:{exc}", "blocking": ""}
            rows.append(row)
            tally[row["outcome"]] = tally.get(row["outcome"], 0) + 1
            print(
                f"  {row['ticker']:6s}  {row['outcome']:16s}  "
                f"{row['skip_reason'] or row['blocking']}"
            )
    finally:
        db.close()

    print()
    print(json.dumps({"tally": tally, "tickers_attempted": len(tickers)}, indent=2))

    if args.all and tally["blocking_drift"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
