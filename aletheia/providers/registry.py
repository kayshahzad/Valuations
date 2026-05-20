"""Provider registry — resolves ``ALETHEIA_PROVIDER`` to an instance.

Resolution precedence (highest wins):
  1. Explicit ``name`` arg to ``get_provider(name)``
  2. ``st.session_state.provider`` (Streamlit per-session selector)
  3. ``ALETHEIA_PROVIDER`` env var
  4. ``config/data_source.py:DEFAULT_PROVIDER`` (currently "hybrid")

XBRL + Hybrid providers land in P2 / P5. Until then, requesting them
raises a clear error rather than silently falling back to FMP — that
would mask configuration mistakes.
"""

from __future__ import annotations

import os
from typing import Optional

from aletheia.providers.base import FinancialDataProvider
from aletheia.providers.fmp_provider import FmpProvider
from aletheia.providers.hybrid_provider import HybridProvider
from aletheia.providers.xbrl_provider import XbrlProvider

# Provider instances are stateless — singleton-per-process is safe and
# avoids repeated FMP client initialisation.
_PROVIDER_INSTANCES: dict = {}


def _default_provider_name() -> str:
    """Resolve the application default. Streamlit session > env > config."""
    # Streamlit per-session selector. Imported lazily so non-UI callers
    # (CLI, tests, batch ingest) don't pay the import cost or require
    # Streamlit to be installed.
    try:
        import streamlit as st  # noqa: F401
        # st.session_state may raise outside a runtime; guard it.
        try:
            sess = st.session_state.get("provider")
            if isinstance(sess, str) and sess:
                return sess.lower()
        except Exception:  # noqa: BLE001 — no script-run context, etc.
            pass
    except ImportError:
        pass

    env = os.environ.get("ALETHEIA_PROVIDER", "").strip().lower()
    if env:
        return env

    try:
        from config.data_source import DEFAULT_PROVIDER
        return str(DEFAULT_PROVIDER).lower()
    except ImportError:
        return "fmp"


def resolve_provider_name(
    name: Optional[str] = None, *, ticker: Optional[str] = None,
) -> tuple:
    """Resolve which provider should serve a request.

    Returns ``(provider_name, pin_reason)``. ``pin_reason`` is non-empty
    when a per-ticker pin overrode the caller's choice — the UI status
    chip surfaces this so the analyst sees why their sidebar selection
    doesn't apply.

    Resolution precedence (highest wins):
      1. Per-ticker pin in ``config/provider_pins.py`` (overrides
         everything — pinned tickers can't be flipped via the UI
         because the pin reflects a known-broken provider for that
         filer)
      2. Explicit ``name`` arg
      3. Session / env / config default
    """
    if ticker:
        try:
            from config.provider_pins import get_pin
            pin = get_pin(ticker)
            if pin is not None:
                return pin[0].lower(), pin[1]
        except ImportError:
            pass
    return (name or _default_provider_name()).lower(), ""


def get_provider(
    name: Optional[str] = None, *, ticker: Optional[str] = None,
) -> FinancialDataProvider:
    """Return a provider instance.

    Args:
        name: Override the default. Accepts ``"fmp"``, ``"xbrl"``,
            or ``"hybrid"`` (hybrid lands in P5).
        ticker: Optional — when supplied, per-ticker pins in
            ``config/provider_pins.py`` can override ``name`` and the
            session default. Lets the sidebar selector apply globally
            while keeping known-broken-on-one-source tickers safe.
    """
    resolved, _ = resolve_provider_name(name, ticker=ticker)
    if resolved in _PROVIDER_INSTANCES:
        return _PROVIDER_INSTANCES[resolved]

    if resolved == "fmp":
        inst = FmpProvider()
    elif resolved == "xbrl":
        inst = XbrlProvider()
    elif resolved == "hybrid":
        inst = HybridProvider()
    else:
        raise ValueError(
            f"Unknown provider name: {resolved!r}. "
            "Expected one of: fmp, xbrl, hybrid."
        )

    _PROVIDER_INSTANCES[resolved] = inst
    return inst


__all__ = ["get_provider"]
