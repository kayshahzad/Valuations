"""Pipeline status registry — DuckDB-backed.

Owns the ``pipeline_status`` table that tracks per-(ticker, stage)
state across orchestrator runs. The schema mirrors the
``PipelineStatusRow`` contract defined in
``aletheia.contracts.pipeline``.

Operator queries supported via the table:
  - "Which tickers failed Stage 3 in the last 24h?"
  - "Which tickers have stale Stage 4 outputs after the recent
    override registry change?"
  - "What's the success rate for Stage 1 across the universe?"

The Week 6 orchestrator writes to this table after each stage; the
``aletheia pipeline status`` CLI reads from it. The fingerprint
column lets cache-hit detection answer "have we already run this
stage on identical inputs?" without re-running.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb

from aletheia.contracts.pipeline import PipelineStatusRow, StageStatus


_DEFAULT_DB_PATH = Path("valuation_data/database/investment.duckdb")


# DDL for the pipeline_status table. Kept in this module so the
# orchestrator owns the schema lifecycle (the legacy DB.py file
# doesn't reach over here).
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_status (
    ticker             VARCHAR NOT NULL,
    stage              VARCHAR NOT NULL,
    status             VARCHAR NOT NULL,
    fingerprint        VARCHAR,
    last_run_at        TIMESTAMP,
    last_success_at    TIMESTAMP,
    error_message      VARCHAR,
    duration_seconds   DOUBLE,
    rows_processed     INTEGER,
    PRIMARY KEY (ticker, stage)
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_pipeline_status_stage_status
ON pipeline_status(stage, status)
"""


class PipelineStatusStore:
    """Thin DuckDB wrapper for the pipeline_status table.

    Each instance opens its own connection. Use as a context manager
    when running outside the orchestrator to ensure the connection
    closes cleanly.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self.db_path))
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.execute(_CREATE_INDEX_SQL)

    # ── lifecycle ───────────────────────────────────────────────────

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> "PipelineStatusStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ── read ────────────────────────────────────────────────────────

    def get(self, ticker: str, stage: str) -> Optional[PipelineStatusRow]:
        """Return the current status row for one (ticker, stage),
        or None if no run has been recorded yet."""
        cur = self._conn.execute(
            "SELECT ticker, stage, status, fingerprint, last_run_at, "
            "last_success_at, error_message, duration_seconds, "
            "rows_processed FROM pipeline_status "
            "WHERE ticker = ? AND stage = ?",
            [ticker, stage],
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_status(row)

    def get_for_ticker(self, ticker: str) -> List[PipelineStatusRow]:
        """All four stages for one ticker."""
        cur = self._conn.execute(
            "SELECT ticker, stage, status, fingerprint, last_run_at, "
            "last_success_at, error_message, duration_seconds, "
            "rows_processed FROM pipeline_status "
            "WHERE ticker = ? ORDER BY stage",
            [ticker],
        )
        return [_row_to_status(r) for r in cur.fetchall()]

    def get_by_stage_status(
        self, stage: str, status: StageStatus,
    ) -> List[PipelineStatusRow]:
        """All tickers currently at a given (stage, status). Used
        by the operator query "which tickers failed Stage 3?"."""
        cur = self._conn.execute(
            "SELECT ticker, stage, status, fingerprint, last_run_at, "
            "last_success_at, error_message, duration_seconds, "
            "rows_processed FROM pipeline_status "
            "WHERE stage = ? AND status = ? ORDER BY ticker",
            [stage, status.value if isinstance(status, StageStatus) else status],
        )
        return [_row_to_status(r) for r in cur.fetchall()]

    def matrix(self) -> List[PipelineStatusRow]:
        """Every row, ordered (ticker, stage). The CLI's
        ``aletheia pipeline status`` no-arg form pivots this."""
        cur = self._conn.execute(
            "SELECT ticker, stage, status, fingerprint, last_run_at, "
            "last_success_at, error_message, duration_seconds, "
            "rows_processed FROM pipeline_status "
            "ORDER BY ticker, stage"
        )
        return [_row_to_status(r) for r in cur.fetchall()]

    # ── write ───────────────────────────────────────────────────────

    def upsert(self, row: PipelineStatusRow) -> None:
        """Insert-or-update one status row. Stage 4 entries don't
        exist by default (--auto-agents is opt-in); only their first
        run inserts them."""
        # DuckDB doesn't support ON CONFLICT cleanly in older versions;
        # use the DELETE+INSERT pattern with the PRIMARY KEY.
        self._conn.execute(
            "DELETE FROM pipeline_status WHERE ticker = ? AND stage = ?",
            [row.ticker, row.stage],
        )
        self._conn.execute(
            "INSERT INTO pipeline_status "
            "(ticker, stage, status, fingerprint, last_run_at, "
            "last_success_at, error_message, duration_seconds, "
            "rows_processed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                row.ticker, row.stage,
                row.status.value if isinstance(row.status, StageStatus) else row.status,
                row.fingerprint,
                row.last_run_at,
                row.last_success_at,
                row.error_message,
                row.duration_seconds,
                row.rows_processed,
            ],
        )

    def mark_running(self, ticker: str, stage: str) -> None:
        existing = self.get(ticker, stage)
        self.upsert(PipelineStatusRow(
            ticker=ticker, stage=stage, status=StageStatus.RUNNING,
            fingerprint=existing.fingerprint if existing else None,
            last_run_at=datetime.now(timezone.utc),
            last_success_at=existing.last_success_at if existing else None,
            error_message=None,
            duration_seconds=None,
            rows_processed=existing.rows_processed if existing else None,
        ))

    def mark_ok(
        self, ticker: str, stage: str, *,
        fingerprint: str,
        duration_seconds: float,
        rows_processed: Optional[int] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        self.upsert(PipelineStatusRow(
            ticker=ticker, stage=stage, status=StageStatus.OK,
            fingerprint=fingerprint,
            last_run_at=now, last_success_at=now,
            error_message=None,
            duration_seconds=duration_seconds,
            rows_processed=rows_processed,
        ))

    def mark_skipped_cached(
        self, ticker: str, stage: str, *,
        fingerprint: str,
    ) -> None:
        """Cache-hit: inputs unchanged since the last successful run."""
        existing = self.get(ticker, stage)
        self.upsert(PipelineStatusRow(
            ticker=ticker, stage=stage, status=StageStatus.SKIPPED_CACHED,
            fingerprint=fingerprint,
            last_run_at=datetime.now(timezone.utc),
            last_success_at=existing.last_success_at if existing else None,
            error_message=None,
            duration_seconds=0.0,
            rows_processed=existing.rows_processed if existing else None,
        ))

    def mark_failed(
        self, ticker: str, stage: str, *,
        error_message: str,
        duration_seconds: float,
    ) -> None:
        existing = self.get(ticker, stage)
        self.upsert(PipelineStatusRow(
            ticker=ticker, stage=stage, status=StageStatus.FAILED,
            fingerprint=existing.fingerprint if existing else None,
            last_run_at=datetime.now(timezone.utc),
            last_success_at=existing.last_success_at if existing else None,
            error_message=error_message,
            duration_seconds=duration_seconds,
            rows_processed=None,
        ))

    def mark_skipped_dependency(
        self, ticker: str, stage: str, *,
        dependency_stage: str,
    ) -> None:
        """An upstream stage failed; this one can't run."""
        existing = self.get(ticker, stage)
        self.upsert(PipelineStatusRow(
            ticker=ticker, stage=stage,
            status=StageStatus.SKIPPED_DEPENDENCY,
            fingerprint=existing.fingerprint if existing else None,
            last_run_at=datetime.now(timezone.utc),
            last_success_at=existing.last_success_at if existing else None,
            error_message=f"upstream stage {dependency_stage} failed",
            duration_seconds=0.0,
            rows_processed=None,
        ))


def _row_to_status(row: Any) -> PipelineStatusRow:
    """Adapter for a raw DuckDB tuple. Order matches the SELECT
    column list in every read method."""
    return PipelineStatusRow(
        ticker=row[0],
        stage=row[1],
        status=StageStatus(row[2]),
        fingerprint=row[3],
        last_run_at=row[4],
        last_success_at=row[5],
        error_message=row[6],
        duration_seconds=row[7],
        rows_processed=row[8],
    )


__all__ = [
    "PipelineStatusStore",
]
