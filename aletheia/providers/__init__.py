"""Pluggable financial-data providers.

Stage 1 ingestion is provider-based: the application's choice of data
source (FMP, XBRL, or hybrid) is determined entirely by configuration.
Stage 2 / Stage 3 / UI code consume from this layer through the
``FinancialDataProvider`` protocol — they don't care which source is
behind it.

Default provider: FMP (per the user's flip; XBRL is now opt-in).
Override via ``ALETHEIA_PROVIDER=xbrl|hybrid`` env var or UI selector.

Entry points:
  - ``get_provider(name=None)`` — registry lookup; falls through to
    config default when name is None
  - ``FmpProvider`` — the primary implementation
  - ``XbrlProvider`` — opt-in legacy path (lands in P2)
  - ``HybridProvider`` — FMP flows + XBRL specialty tags (lands in P5)
"""

from aletheia.providers.base import (
    FinancialDataProvider,
    ProviderBundle,
)
from aletheia.providers.registry import get_provider, resolve_provider_name

__all__ = [
    "FinancialDataProvider",
    "ProviderBundle",
    "get_provider",
    "resolve_provider_name",
]
