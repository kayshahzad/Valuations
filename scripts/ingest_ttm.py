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
from aletheia.data.sec_quarterly import derive_ttm_from_sec
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
    """Returns a per-ticker result dict for the summary tally.

    Phase Q-2: SEC-derived TTM is preferred when the cached XBRL has
    enough data to anchor the cumulative-period math. FMP-derived is
    the fallback for tickers where SEC parsing fails (foreign filers,
    stale local cache, missing prior-FY 10-K, etc.). The chosen source
    is stamped on `record.fmp_validation['ttm_source']` so the swap is
    forensically observable per the locked plan.

    FMP TTM blobs (key-metrics-ttm, ratios-ttm, EV per-quarter, as-
    reported XBRL) are always fetched as Gate A.TTM cross-checks —
    independent of which path produced `record`. When SEC is primary,
    these are second sources; when FMP is primary, they're internal-
    consistency checks against FMP's own quarterly summation."""
    # ── Source selection: prefer SEC, but only when SEC's local cache
    # is at least as fresh as FMP's quarterly snapshot. Stale SEC raw
    # cache (XBRL companyfacts not refetched since the last 10-Q) would
    # otherwise pin TTM 90+ days behind FMP-quarterly. Compare both
    # candidates' period_end dates and route to the fresher one.
    sec_result = derive_ttm_from_sec(ticker)
    fmp_result = derive_ttm_from_fmp(ticker)
    record = None
    fmp_latest_qtr_income = None

    sec_pe = (
        sec_result.record.period_end_date if sec_result.record else None
    )
    fmp_pe = (
        fmp_result.record.period_end_date if fmp_result.record else None
    )

    if sec_result.record and fmp_result.record:
        # Both succeeded — pick the fresher period_end. Ties go to SEC
        # (preferred source on equal freshness).
        if (sec_pe or "") >= (fmp_pe or ""):
            record = sec_result.record
        else:
            record = fmp_result.record
            fmp_latest_qtr_income = fmp_result.latest_quarter_income
    elif sec_result.record:
        record = sec_result.record
    elif fmp_result.record:
        record = fmp_result.record
        fmp_latest_qtr_income = fmp_result.latest_quarter_income

    if record is None:
        return {
            "ticker":      ticker,
            "outcome":     "skipped",
            "skip_reason": (
                f"sec={sec_result.skip_reason or 'no_sec'};"
                f"fmp={fmp_result.skip_reason or 'no_fmp'}"
            ),
            "blocking":    "",
        }

    # ── Cross-check FMP TTM blobs (always fetched, regardless of source) ─
    # Fetch failures on any single lane degrade that lane to 'n_a' in
    # the gate result; Gate A.TTM still runs on the primary lane.
    def _safe_fetch(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

    km_ttm     = _safe_fetch(fmp_client.fetch_key_metrics_ttm, ticker)
    ratios_ttm = _safe_fetch(fmp_client.fetch_ratios_ttm, ticker)
    ev_quarters = _safe_fetch(
        fmp_client.fetch_enterprise_values, ticker, period="quarter",
    )
    ev_latest_quarter = ev_quarters[0] if ev_quarters else None
    as_reported_quarters = _safe_fetch(
        fmp_client.fetch_income_statement_as_reported_quarter, ticker,
    )
    as_reported_latest = as_reported_quarters[0] if as_reported_quarters else None
    # Phase Q-6 multi-year quarterly: pull FMP `period=quarter`
    # statements so the validator can cross-check each contributing
    # quarter against SEC standalones (when SEC is primary).
    fmp_quarterly_income = _safe_fetch(
        fmp_client.fetch_income_statements, ticker, period="quarter",
    )

    gate = validate_ttm_record(
        ticker, record,
        fmp_key_metrics_ttm=km_ttm,
        fmp_ratios_ttm=ratios_ttm,
        fmp_ev_latest_quarter=ev_latest_quarter,
        fmp_income_as_reported_quarter=as_reported_latest,
        latest_quarter_income=fmp_latest_qtr_income,
        sec_quarterly_revenue=sec_result.quarterly_standalone_revenue,
        sec_quarterly_net_income=sec_result.quarterly_standalone_net_income,
        fmp_quarterly_income=fmp_quarterly_income,
    )
    # validate_ttm_record copies ttm_source into the result already, so
    # overwriting fmp_validation with `gate` keeps the source-primacy
    # swap observable.
    record.fmp_validation = gate

    if gate["status"] == "blocking_drift":
        return {
            "ticker":      ticker,
            "outcome":     "blocking_drift",
            "skip_reason": "",
            "blocking":    ",".join(gate["blocking_fields"]),
        }

    db.upsert_record(record)
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
    p.add_argument("--include-foreign", dest="include_foreign",
                   action="store_true", default=True,
                   help="Include IFRS filers (ASML/TSM) — FX-converted to USD via FY-avg rate (default).")
    p.add_argument("--no-foreign", dest="include_foreign",
                   action="store_false",
                   help="Skip IFRS filers entirely (legacy behavior).")
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
