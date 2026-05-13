"""Stage 3 CLI — ``aletheia calc <ticker>``.

Thin wrapper that reads cleaned records from DuckDB, adapts them to
``List[ValidatedCleanedRecord]`` (Stage 2's typed output), and calls
``run_stage3``. The CLI lives outside the stage module so the stage
itself stays deterministic on its inputs (no I/O, no DB hits).

Stage 2 doesn't yet emit ``ValidatedCleanedRecord`` natively — that's
Week 5's extraction. Until then, the CLI does the adapter work to
bridge from the existing ``company_records_latest`` rows to the typed
contract. Once Stage 2 ships, this adapter compresses to a single
DB-fetch line.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from aletheia.contracts.pipeline import (
    ValidatedCleanedRecord,
    ValidationReceipt,
)
from aletheia.pipeline.stage3_calculate import Stage3InputError, run_stage3


# ─────────────────────────────────────────────────────────────────────
# DB adapter — temporary until Stage 2 emits ValidatedCleanedRecord
# ─────────────────────────────────────────────────────────────────────

# Column prefixes produced by the cleaning_engine's row materialiser.
RAW_PREFIX = "raw_"
CLEAN_PREFIX = "clean_"
DERIVED_PREFIX = "derived_"


def _row_to_validated_record(
    row: pd.Series,
    pipeline_version: str,
) -> ValidatedCleanedRecord:
    """Adapt one ``company_records_latest`` row to a typed record.

    Until Stage 2 lands (Week 5), Stage 3's input materialises here
    from the existing DuckDB schema. The receipt is intentionally
    sparse — the Gate-A and schema-contract receipts live in other
    tables today and will be folded into ``ValidationReceipt`` when
    Stage 2 ships.
    """
    raw: Dict[str, Optional[float]] = {}
    clean: Dict[str, Optional[float]] = {}
    derived: Dict[str, Optional[float]] = {}
    for col, val in row.items():
        if not isinstance(col, str):
            continue
        if col.startswith(RAW_PREFIX):
            raw[col[len(RAW_PREFIX):]] = _coerce_optional_float(val)
        elif col.startswith(CLEAN_PREFIX):
            clean[col[len(CLEAN_PREFIX):]] = _coerce_optional_float(val)
        elif col.startswith(DERIVED_PREFIX):
            derived[col[len(DERIVED_PREFIX):]] = _coerce_optional_float(val)

    fiscal_year = int(row["fiscal_year"])
    period = row.get("period", "FY")
    if period not in ("FY", "TTM", "Q1", "Q2", "Q3", "Q4"):
        period = "FY"

    period_end_date = row.get("period_end_date")
    if pd.isna(period_end_date) or period_end_date is None:
        period_end_date = f"{fiscal_year}-12-31"
    else:
        period_end_date = str(period_end_date)[:10]

    quality_score = _coerce_float_default(row.get("quality_score"), default=1.0)
    quality_score = max(0.0, min(1.0, quality_score))

    record_fingerprint = _adapter_fingerprint(
        ticker=str(row["ticker"]),
        fiscal_year=fiscal_year,
        period=str(period),
        period_end_date=str(period_end_date),
        pipeline_version=pipeline_version,
    )

    return ValidatedCleanedRecord(
        ticker=str(row["ticker"]),
        fiscal_year=fiscal_year,
        period=period,
        period_end_date=str(period_end_date),
        raw=raw,
        clean=clean,
        derived=derived,
        overall_quality_score=quality_score,
        cleaning_warnings=[],
        blocking_errors=[],
        validation=ValidationReceipt(),
        record_fingerprint=record_fingerprint,
        input_bundle_fingerprint="<pre-stage1-adapter>",
        cleaned_at=datetime.now(timezone.utc),
        pipeline_version=pipeline_version,
    )


def _coerce_optional_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f


def _coerce_float_default(val: Any, default: float) -> float:
    f = _coerce_optional_float(val)
    return default if f is None else f


def _adapter_fingerprint(
    *,
    ticker: str,
    fiscal_year: int,
    period: str,
    period_end_date: str,
    pipeline_version: str,
) -> str:
    """Deterministic placeholder fingerprint for the adapter path.

    Once Stage 2 emits ``ValidatedCleanedRecord`` natively, records
    carry their own ``record_fingerprint``. Until then, this hash
    stands in so the Stage 3 lineage pointer is at least stable for
    identical DB rows."""
    import hashlib
    payload = f"{ticker}|{fiscal_year}|{period}|{period_end_date}|{pipeline_version}"
    return hashlib.sha256(payload.encode()).hexdigest()


def load_records(ticker: str, pipeline_version: str) -> List[ValidatedCleanedRecord]:
    """Read all cleaned rows for ``ticker`` and adapt them to the
    Stage 2 typed contract.

    Imports DuckDB lazily so importing the CLI module doesn't open a
    connection — keeps unit tests fast.
    """
    from aletheia.data.database import InvestmentDatabase
    db = InvestmentDatabase(verbose=False)
    try:
        df = db.get_latest(ticker)
    finally:
        db.close()
    if df is None or df.empty:
        raise RuntimeError(
            f"No company_records rows for {ticker!r}. Run the cleaning "
            "pipeline (or `aletheia ingest <ticker>` once Stage 1 ships)."
        )
    return [_row_to_validated_record(row, pipeline_version) for _, row in df.iterrows()]


# ─────────────────────────────────────────────────────────────────────
# Pipeline-version detection
# ─────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aletheia calc",
        description=(
            "Run Stage 3 (calculation) on a ticker. Loads Stage 2's "
            "cleaned records, runs every deterministic calc engine, "
            "and emits a CalculationBundle as JSON to stdout."
        ),
    )
    parser.add_argument("ticker", help="Ticker symbol (e.g. NVDA).")
    parser.add_argument(
        "--fiscal-year",
        type=int,
        default=None,
        help="Anchor fiscal year (defaults to latest available).",
    )
    parser.add_argument(
        "--pipeline-version",
        default=None,
        help=(
            "Override the pipeline_version stamped on the output. "
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

    records = load_records(ticker, pipeline_version)
    try:
        bundle = run_stage3(
            records,
            pipeline_version=pipeline_version,
            fiscal_year=args.fiscal_year,
        )
    except Stage3InputError as exc:
        print(f"stage3: input contract violation: {exc}", file=sys.stderr)
        return 2

    indent = args.indent if args.indent > 0 else None
    print(bundle.model_dump_json(indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
