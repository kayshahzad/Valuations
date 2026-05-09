"""Recompute orchestrator for deterministic dimensions.

Iterates `aletheia.qualitative.computers.COMPUTERS`, runs each against
the cleaned DB rows for a ticker, and writes one AssessmentRecord per
(ticker, dimension_id) into `qualitative_assessments`.

Idempotency rule: if the computed result has the same `input_fingerprint`
AND the same `code_git_sha` as the latest stored row, we skip the write.
This avoids spurious version churn when the runner is invoked repeatedly
on unchanged data — versions exist to capture meaningful change, not
clock ticks.

Stale rule: when either fingerprint changes (re-clean of inputs) or
git_sha changes (formula update), a new row is written; the old row
remains in `qualitative_assessments` for audit but
`qualitative_assessments_latest` surfaces the new one.
"""

from __future__ import annotations

import datetime
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from aletheia.data.database import InvestmentDatabase
from aletheia.qualitative.computers import COMPUTERS, ComputerResult
from aletheia.qualitative.types import AssessmentRecord, SourceCategory


def _capture_git_sha() -> Optional[str]:
    """Resolve the current commit SHA at module-import time."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
            cwd=Path(__file__).resolve().parents[2],
        )
        sha = out.stdout.strip()
        return sha if sha and out.returncode == 0 else None
    except Exception:
        return None


_GIT_SHA = _capture_git_sha()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _is_unchanged(prior: Optional[Dict[str, Any]], result: ComputerResult,
                  current_git_sha: Optional[str]) -> bool:
    """True when the latest stored assessment was produced from the same
    inputs by the same code version — i.e. recomputing it would produce
    an identical row, so we skip the write."""
    if prior is None:
        return False
    return (
        prior.get("input_fingerprint") == result.input_fingerprint
        and prior.get("code_git_sha")  == current_git_sha
    )


def recompute_deterministic(
    ticker: str,
    db: Optional[InvestmentDatabase] = None,
) -> List[Dict[str, Any]]:
    """Run all deterministic computers for `ticker` and persist results.

    Args:
        ticker: Upper-cased ticker symbol.
        db: Optional pre-opened database connection (lets callers batch
            many tickers without paying repeated connect overhead).

    Returns:
        List of dicts describing each computer invocation, suitable for
        the calibration script and the API. Each entry:
            {
                "dimension_id":   str,
                "score":          int | None,
                "status":         "written" | "unchanged" | "no_data",
                "source_payload": dict,
                "reason":         str | None,
            }
    """
    own_db = db is None
    if db is None:
        db = InvestmentDatabase(verbose=False)

    try:
        df = db.get_latest(ticker.upper())
        # Phase Q-1+: filter out TTM/quarterly rows so qualitative
        # computers see only the FY series they were calibrated on.
        # An explicit TTM-aware computer can be added per-dimension
        # later if needed (Phase Q-5+).
        if df is not None and "period" in df.columns:
            df = df[df["period"] == "FY"].copy()
        if df.empty:
            return [{
                "dimension_id":   dim_id,
                "score":          None,
                "status":         "no_data",
                "source_payload": {"reason": "no_db_rows"},
                "reason":         f"No cleaned rows for {ticker}",
            } for dim_id in COMPUTERS]

        out: List[Dict[str, Any]] = []
        for dim_id, computer in COMPUTERS.items():
            try:
                result = computer(df)
            except Exception as e:
                out.append({
                    "dimension_id":   dim_id,
                    "score":          None,
                    "status":         "error",
                    "source_payload": {"error": f"{type(e).__name__}: {e}"},
                    "reason":         str(e),
                })
                continue

            if result is None:
                out.append({
                    "dimension_id":   dim_id,
                    "score":          None,
                    "status":         "no_data",
                    "source_payload": {"reason": "computer_returned_none"},
                    "reason":         "Computer returned None",
                })
                continue

            prior = db.get_latest_assessment(ticker.upper(), dim_id)
            if _is_unchanged(prior, result, _GIT_SHA):
                out.append({
                    "dimension_id":   dim_id,
                    "score":          result.score,
                    "status":         "unchanged",
                    "source_payload": result.source_payload,
                    "reason":         None,
                })
                continue

            record = AssessmentRecord(
                assessment_id=str(uuid.uuid4()),
                ticker=ticker.upper(),
                dimension_id=dim_id,
                score=float(result.score) if result.score is not None else None,
                sub_scores=None,
                narrative=None,
                source_category=SourceCategory.DETERMINISTIC,
                source_payload=result.source_payload,
                assessed_at=_now_iso(),
                analyst_id="system",
                code_git_sha=_GIT_SHA,
                input_fingerprint=result.input_fingerprint,
            )
            db.upsert_qualitative_assessment(record)
            out.append({
                "dimension_id":   dim_id,
                "score":          result.score,
                "status":         "written",
                "source_payload": result.source_payload,
                "reason":         result.reason,
            })

        return out
    finally:
        if own_db:
            db.close()


def recompute_universe(tickers: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Run deterministic computers for every ticker in the list.
    Reuses a single DB connection across the loop."""
    db = InvestmentDatabase(verbose=False)
    try:
        return {t: recompute_deterministic(t, db=db) for t in tickers}
    finally:
        db.close()
