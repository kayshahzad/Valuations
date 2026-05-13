"""Stage 1 CLI — ``aletheia ingest <ticker>``.

Thin wrapper around ``aletheia.pipeline.stage1_ingest.run_stage1``.
Emits the typed ``IngestedRawBundle`` as JSON to stdout so downstream
tooling (or the Week 6 orchestrator) can consume it directly.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import List, Optional

from aletheia.pipeline.stage1_ingest import Stage1IngestError, run_stage1


def detect_pipeline_version() -> str:
    """Resolve the current code SHA. Falls back to ``"unversioned"``
    when not running inside a git checkout."""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return sha or "unversioned"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unversioned"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aletheia ingest",
        description=(
            "Run Stage 1 (ingestion) on a ticker. Fetches every "
            "canonical source (SEC XBRL, FMP statements, market "
            "snapshot), persists payloads to disk, and emits the "
            "typed IngestedRawBundle as JSON to stdout."
        ),
    )
    parser.add_argument("ticker", help="Ticker symbol (e.g. NVDA).")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help=(
            "Bypass per-fetcher cache TTL and re-hit every source. "
            "Use sparingly; FMP has a 250-call daily quota on the "
            "free tier."
        ),
    )
    parser.add_argument(
        "--no-market-snapshot",
        action="store_true",
        help=(
            "Skip the live market data fetch (yfinance). Useful "
            "offline or in CI where network access is restricted."
        ),
    )
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        help=(
            "Whitelist of source ids to fetch (e.g. --source "
            "sec_companyfacts --source fmp_income). When omitted, "
            "every canonical source is fetched."
        ),
    )
    parser.add_argument(
        "--pipeline-version",
        default=None,
        help=(
            "Override the pipeline_version stamped on the bundle. "
            "Defaults to the current git SHA."
        ),
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent for stdout output (use 0 for compact).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    ticker = args.ticker.upper()
    pipeline_version = args.pipeline_version or detect_pipeline_version()

    try:
        bundle = run_stage1(
            ticker,
            pipeline_version=pipeline_version,
            force_refresh=args.force_refresh,
            sources=args.source,
            include_market_snapshot=not args.no_market_snapshot,
        )
    except Stage1IngestError as exc:
        print(f"stage1: {exc}", file=sys.stderr)
        return 2

    indent = args.indent if args.indent > 0 else None
    print(bundle.model_dump_json(indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
