"""In-process, single-writer edit lock for the shared multi-user app.

Only ONE person edits at a time; everyone else gets a friendly "X is editing"
signal. Valid ONLY while the service runs at max-instances=1 (one process, one
authoritative lock). If it ever scales past one instance, this must move to a
shared store (GCS lock-file with generation preconditions, or Cloud SQL
advisory lock) — see deploy notes.

The lock auto-expires after ``EDIT_LOCK_TTL`` seconds so a walked-away editor
never blocks others permanently. Each write refreshes it (heartbeat), so an
actively-editing user keeps it; when they stop, it lapses.
"""
from __future__ import annotations

import os
import threading
import time
from typing import NamedTuple, Optional


def _ttl() -> float:
    try:
        return float(os.environ.get("EDIT_LOCK_TTL", "180"))
    except ValueError:
        return 180.0


class Holder(NamedTuple):
    email: str
    since: float          # epoch seconds (wall clock, for display)
    expires: float        # monotonic deadline (per-acquisition TTL)
    reason: str           # "edit" | "pipeline" — for the UI message


_lock = threading.Lock()
_holder: Optional[Holder] = None


def _fresh(h: Optional[Holder], now: float) -> bool:
    return h is not None and now < h.expires


def holder() -> Optional[Holder]:
    """The current live holder, or None if free/expired."""
    with _lock:
        return _holder if _fresh(_holder, time.monotonic()) else None


def try_acquire(email: str, *, hold: Optional[float] = None,
                reason: str = "edit") -> tuple[bool, Optional[Holder]]:
    """Acquire or refresh the lock for ``email``.

    ``hold`` = seconds to hold (defaults to EDIT_LOCK_TTL). A pipeline run passes
    a longer hold since it can't heartbeat while a blocking subprocess runs.
    Returns (True, holder) when the caller now holds it, or
    (False, current_holder) when someone else holds a live lock.
    """
    email = (email or "").strip().lower()
    if not email:
        return False, None
    now = time.monotonic()
    ttl = float(hold) if hold else _ttl()
    global _holder
    with _lock:
        if _fresh(_holder, now) and _holder.email != email:
            return False, _holder
        # free, expired, or already ours → (re)acquire + heartbeat.
        same = _holder is not None and _holder.email == email
        since = _holder.since if same else time.time()
        expires = now + ttl
        if same and _fresh(_holder, now):
            # Don't let a short edit-refresh shrink a longer pipeline hold, and
            # keep the stronger reason ("pipeline" outranks "edit").
            expires = max(expires, _holder.expires)
            if _holder.reason == "pipeline":
                reason = "pipeline"
        _holder = Holder(email=email, since=since, expires=expires, reason=reason)
        return True, _holder


def release(email: str) -> None:
    """Release only if ``email`` holds it (no-op otherwise)."""
    email = (email or "").strip().lower()
    global _holder
    with _lock:
        if _holder is not None and _holder.email == email:
            _holder = None
