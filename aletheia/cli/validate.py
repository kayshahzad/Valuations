"""Stage 2 CLI — ``aletheia validate <ticker>``.

Thin wrapper around ``aletheia.pipeline.stage2_validate.run_stage2``.
Emits the list of typed ``ValidatedCleanedRecord`` payloads as a JSON
array to stdout so downstream tooling (or the Week 6 orchestrator)
can consume it directly.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import List, Optional

from aletheia.pipeline.stage2_validate import (
    Stage2ValidateError,
    run_stage2,
)


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
        prog="aletheia validate",
        description=(
            "Run Stage 2 (validation + cleaning) on a ticker. "
            "Cleans every available fiscal year, runs the schema "
            "contract validator, and emits the list of "
            "ValidatedCleanedRecord payloads as JSON to stdout."
        ),
    )
    parser.add_argument("ticker", help="Ticker symbol (e.g. NVDA).")
    parser.add_argument(
        "--fiscal-year",
        type=int,
        action="append",
        default=None,
        help=(
            "Restrict to specific fiscal year(s). Pass multiple times "
            "to whitelist a range, e.g. --fiscal-year 2023 "
            "--fiscal-year 2024. When omitted, every cleanable FY "
            "is included."
        ),
    )
    parser.add_argument(
        "--input-bundle-fingerprint",
        default=None,
        help=(
            "Optional Stage 1 lineage pointer. The Week 6 "
            "orchestrator supplies this automatically; manual "
            "invocations leave it as the adapter sentinel."
        ),
    )
    parser.add_argument(
        "--pipeline-version",
        default=None,
        help=(
            "Override the pipeline_version stamped on each record. "
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
        records = run_stage2(
            ticker=ticker,
            pipeline_version=pipeline_version,
            input_bundle_fingerprint=args.input_bundle_fingerprint,
            fiscal_years=args.fiscal_year,
        )
    except Stage2ValidateError as exc:
        print(f"stage2: {exc}", file=sys.stderr)
        return 2

    indent = args.indent if args.indent > 0 else None
    payload = [json.loads(r.model_dump_json()) for r in records]
    print(json.dumps(payload, indent=indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
