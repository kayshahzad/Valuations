"""Draft persistence layer for HITL assessments — week 1: session_state only.

Architectural note (week 1 scope decision):
  The original plan called for `localStorage`-keyed drafts that survive
  tab close. Implementing that correctly requires a Streamlit custom
  component (frontend bundle build, bidirectional message passing).
  That's not week-1 scope.

  Week 1 ships with `st.session_state`-only drafts:
    - Persist across reruns within the same tab session ✓
    - Lost on tab close ✗ (matches Streamlit-default behavior elsewhere
      in the dashboard; the user is no worse off than today)
    - Catalog-hash-keyed so reworded questions invalidate old drafts ✓

  Week 2 follow-up is to wrap this in a proper component. The interface
  here (load/save/clear) is the contract that follow-up will implement
  against, so swapping the backend is mechanical.

Key shape:
    f"qual_draft__{ticker}__{dimension_id}__{catalog_hash}"

Catalog hash inclusion means a reworded question or weight change
auto-invalidates in-flight drafts — the old key won't be matched on
reopen. This is the same invariant the user previously approved for the
localStorage version.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

import streamlit as st


def _draft_key(ticker: str, dimension_id: str, catalog_hash: str) -> str:
    return f"qual_draft__{ticker.upper()}__{dimension_id}__{catalog_hash}"


def load_draft(ticker: str, dimension_id: str, catalog_hash: str) -> Optional[Dict[str, Any]]:
    """Return the saved draft for this (ticker, dim, catalog_hash), or
    None if nothing is in session_state.

    Returned shape (when present):
        {
            "sub_scores": {q_id: int, ...},   # may be partial
            "narrative":  str | None,
            "saved_at":   ISO8601 str,
        }
    """
    return st.session_state.get(_draft_key(ticker, dimension_id, catalog_hash))


def save_draft(
    ticker: str,
    dimension_id: str,
    catalog_hash: str,
    sub_scores: Dict[str, int],
    narrative: Optional[str],
) -> None:
    """Write the current dialog state to session_state. Called from the
    dialog's on-change handlers so partial answers survive Streamlit
    reruns within the session."""
    st.session_state[_draft_key(ticker, dimension_id, catalog_hash)] = {
        "sub_scores": dict(sub_scores),
        "narrative":  narrative,
        "saved_at":   datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def clear_draft(ticker: str, dimension_id: str, catalog_hash: str) -> None:
    """Delete the draft — called after a successful submission so the
    dialog opens fresh next time."""
    key = _draft_key(ticker, dimension_id, catalog_hash)
    st.session_state.pop(key, None)


def has_any_draft_for(ticker: str, dimension_id: str) -> Optional[Dict[str, Any]]:
    """Search across catalog hashes — useful when the catalog has changed
    since the draft was saved. Returns the most recently saved match
    along with its (now possibly-stale) hash, so the dialog can decide
    whether to surface a 'restore from previous catalog version?' option.

    Returns:
        {"draft": <draft dict>, "stored_catalog_hash": <hash>} | None
    """
    prefix = f"qual_draft__{ticker.upper()}__{dimension_id}__"
    candidates = []
    for k, v in st.session_state.items():
        if isinstance(k, str) and k.startswith(prefix) and isinstance(v, dict):
            stored_hash = k[len(prefix):]
            saved_at = v.get("saved_at", "")
            candidates.append((saved_at, stored_hash, v))
    if not candidates:
        return None
    candidates.sort(reverse=True)   # most-recent first
    saved_at, stored_hash, draft = candidates[0]
    return {"draft": draft, "stored_catalog_hash": stored_hash}


def time_ago(iso_str: str) -> str:
    """Format an ISO timestamp as a 'X minutes/hours ago' string for the
    'Restored from draft saved X ago' banner."""
    try:
        ts = datetime.datetime.fromisoformat(iso_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        delta = datetime.datetime.now(datetime.timezone.utc) - ts
        sec = int(delta.total_seconds())
        if sec < 60:
            return f"{sec}s ago"
        if sec < 3600:
            return f"{sec // 60}m ago"
        if sec < 86400:
            return f"{sec // 3600}h ago"
        return f"{sec // 86400}d ago"
    except (TypeError, ValueError):
        return "earlier"


__all__ = [
    "load_draft",
    "save_draft",
    "clear_draft",
    "has_any_draft_for",
    "time_ago",
]
