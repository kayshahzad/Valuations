"""Audit-log configuration for the calculation-layer validation framework.

Sets up a dedicated JSON Lines file handler that captures every
``calc_guard_violation`` and ``calc_guard_soft_flag`` log record into
``audits/guard_violations.jsonl``. The format is one JSON object per
line, suitable for downstream analysis with ``jq``, ``DuckDB``, or any
log-aggregation pipeline.

Idempotent: ``setup_guard_audit_logging()`` checks if the handler is
already attached before adding it. Safe to call from multiple entry
points (config/__init__, streamlit_app.py, ingest scripts).

Activated automatically when ``aletheia.calculations`` is imported, so
any process touching the framework gets audit logging without code
changes.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional


_AUDIT_LOGGER_NAME = "aletheia.calculations._guards"
_HANDLER_FLAG_ATTR = "_aletheia_guard_audit_handler_attached"


class _StructuredJSONFormatter(logging.Formatter):
    """Format records emitted by the guards module as one-line JSON.

    The guards module emits dicts as the second positional arg:
        logger.warning("calc_guard_violation %s", record_dict)
        logger.warning("calc_guard_soft_flag %s", record_dict)

    This formatter parses that dict back out and wraps it with the
    standard log envelope (timestamp, level, category, mode).
    """

    def format(self, record: logging.LogRecord) -> str:
        # Python's logging module sets record.args either as the bare
        # argument (when a single non-mapping arg is passed) OR as a
        # tuple. Handle both. Some Python versions store a single dict
        # arg as the dict itself.
        payload = None
        args = record.args
        if isinstance(args, dict):
            payload = args
        elif isinstance(args, tuple) and len(args) == 1 and isinstance(args[0], dict):
            payload = args[0]
        if payload is None:
            payload = {"message": record.getMessage()}

        # Distinguish hard violations from soft flags
        category = "violation"
        msg = record.msg if isinstance(record.msg, str) else str(record.msg)
        if "soft_flag" in msg:
            category = "soft_flag"

        envelope = {
            "ts":       self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":    record.levelname,
            "category": category,
            **payload,
        }
        return json.dumps(envelope, default=str)


def setup_guard_audit_logging(
    audit_path: Optional[Path] = None,
    also_emit_startup_banner: bool = True,
) -> Path:
    """Attach the JSON Lines audit handler to the guards logger.

    Idempotent — checks for an existing attached handler before adding.
    Returns the resolved audit path so callers can log it.
    """
    if audit_path is None:
        # Default: audits/guard_violations.jsonl, rotating per-day so a long-
        # running process doesn't accumulate one huge file.
        from datetime import date
        audit_dir = Path("audits")
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / f"guard_violations_{date.today().isoformat()}.jsonl"

    logger = logging.getLogger(_AUDIT_LOGGER_NAME)

    # Idempotency check — don't double-attach if already configured.
    for h in logger.handlers:
        if getattr(h, _HANDLER_FLAG_ATTR, False):
            return audit_path

    handler = logging.FileHandler(audit_path, mode="a")
    handler.setLevel(logging.WARNING)  # captures both warning (soft) and error (hard)
    handler.setFormatter(_StructuredJSONFormatter())
    setattr(handler, _HANDLER_FLAG_ATTR, True)
    logger.addHandler(handler)

    # Ensure the guards logger propagates at WARNING+ even if the root
    # logger is at a lower threshold elsewhere.
    if logger.level == logging.NOTSET or logger.level > logging.WARNING:
        logger.setLevel(logging.WARNING)

    if also_emit_startup_banner:
        from ._guards import _guard_mode
        mode = _guard_mode()
        banner = (
            f"[calc-guard] ALETHEIA_GUARD_MODE={mode}; "
            f"audit log → {audit_path}"
        )
        # Stderr banner is visible regardless of logging config — important
        # for ingest scripts that run with default stdout/stderr piping.
        print(banner, file=sys.stderr, flush=True)

    return audit_path


def get_today_audit_path() -> Path:
    """Convenience: return the path the framework is currently logging to."""
    from datetime import date
    return Path("audits") / f"guard_violations_{date.today().isoformat()}.jsonl"
