"""LangSmith tracing for the Stage-4 LangGraph agents.

Why this is a one-file, low-touch integration
----------------------------------------------
Every narrative agent (``thesis_synthesizer``, ``contrarian_v2``,
``qualitative_synthesis``, the extractors) builds a LangChain
``ChatGoogleGenerativeAI`` and runs an LCEL ``chain.invoke(...)`` inside
the compiled LangGraph workflow. LangChain tracing is **callback/env
driven** — when the LangSmith env vars are set, langchain-core emits a
trace tree for the whole graph (per-node spans, token counts, latency)
**without any per-agent code change**. So the entire integration is:

  1. ``init_langsmith()`` — set the env vars once, before the graph runs.
  2. ``trace_config(ticker)`` — attach a run name + ticker metadata so
     runs are filterable in the LangSmith UI.

Design contract: **degrades to a no-op**. With no ``LANGSMITH_API_KEY``
configured, ``init_langsmith()`` does nothing and tracing stays off, so
offline runs, CI, and users who haven't opted in behave exactly as
before. Nothing in the pipeline hard-depends on LangSmith being present.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default project name in the LangSmith UI. Override with LANGSMITH_PROJECT.
_DEFAULT_PROJECT = "aletheia"

# Set once, then remembered so repeated calls (the runner may be invoked
# per-ticker) don't re-log or re-mutate the environment.
_INITIALIZED: Optional[bool] = None


def _truthy(val: Optional[str]) -> bool:
    return str(val or "").strip().lower() in {"1", "true", "yes", "on"}


def langsmith_enabled() -> bool:
    """Return True when tracing has been turned on for this process.

    Reflects the effective env after ``init_langsmith()`` has run. Safe to
    call before init (returns the raw env signal).
    """
    has_key = bool(os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY"))
    tracing_on = _truthy(os.environ.get("LANGSMITH_TRACING")) or _truthy(
        os.environ.get("LANGCHAIN_TRACING_V2")
    )
    return has_key and tracing_on


def init_langsmith(*, force: bool = False) -> bool:
    """Enable LangSmith tracing if a key is configured. Idempotent.

    Reads configuration from the environment (``.env`` is loaded by the
    process entry points — ``streamlit_app.py``, the CLI — before this
    runs):

      - ``LANGSMITH_API_KEY`` (or legacy ``LANGCHAIN_API_KEY``) — REQUIRED.
        Absent → no-op, returns False, tracing stays off.
      - ``LANGSMITH_PROJECT`` (or ``LANGCHAIN_PROJECT``) — optional,
        defaults to ``"aletheia"``.
      - ``LANGSMITH_TRACING`` / ``LANGCHAIN_TRACING_V2`` — optional
        explicit opt-out: set to a falsey value to keep tracing off even
        when a key is present.

    On success, mirrors the canonical vars into BOTH the ``LANGSMITH_*``
    and legacy ``LANGCHAIN_*`` names so any langchain-core version picks
    them up. Returns True iff tracing is now enabled.

    Args:
        force: re-evaluate the environment even if a prior call ran.
    """
    global _INITIALIZED
    if _INITIALIZED is not None and not force:
        return _INITIALIZED

    api_key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    if not api_key:
        logger.debug("LangSmith tracing off: no LANGSMITH_API_KEY configured.")
        _INITIALIZED = False
        return False

    # Explicit opt-out: a key is present but tracing was set falsey.
    raw_tracing = os.environ.get("LANGSMITH_TRACING", os.environ.get("LANGCHAIN_TRACING_V2"))
    if raw_tracing is not None and not _truthy(raw_tracing):
        logger.info("LangSmith tracing explicitly disabled via env; skipping.")
        _INITIALIZED = False
        return False

    project = (
        os.environ.get("LANGSMITH_PROJECT")
        or os.environ.get("LANGCHAIN_PROJECT")
        or _DEFAULT_PROJECT
    )

    # Mirror canonical + legacy names so any langchain-core version reads them.
    os.environ["LANGSMITH_API_KEY"] = api_key
    os.environ["LANGCHAIN_API_KEY"] = api_key
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_PROJECT"] = project
    os.environ["LANGCHAIN_PROJECT"] = project

    logger.info("LangSmith tracing enabled (project=%s).", project)
    _INITIALIZED = True
    return True


def trace_config(
    ticker: str,
    *,
    stage: str = "stage4_agents",
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a LangGraph/LCEL ``config`` dict that labels a run.

    Passed as ``app.invoke(state, config=trace_config(ticker))``. The
    keys (``run_name``, ``metadata``, ``tags``) are standard langchain
    runnable-config fields; they are harmless when tracing is off (the
    graph simply ignores unused config), so callers can pass this
    unconditionally.
    """
    md: Dict[str, Any] = {"ticker": ticker, "stage": stage}
    if metadata:
        md.update(metadata)
    tag_list = ["aletheia", stage, ticker]
    if tags:
        tag_list.extend(tags)
    return {
        "run_name": f"aletheia:{ticker}",
        "metadata": md,
        "tags": tag_list,
    }


__all__ = ["init_langsmith", "trace_config", "langsmith_enabled"]
