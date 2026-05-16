"""Application default data-source configuration.

FMP is the canonical primary source. Override per-environment via
the ``ALETHEIA_PROVIDER`` env var, or per-session via the UI sidebar
selector (writes ``st.session_state["provider"]``).

XbrlProvider lands in P2; HybridProvider (FMP flows + XBRL specialty
tags) lands in P5.
"""

DEFAULT_PROVIDER: str = "fmp"
