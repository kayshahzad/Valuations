"""Provider protocol — the contract Stage 2/3/UI depend on.

The protocol is deliberately narrow:
  - ``fetch(ticker)`` returns a raw ``ProviderBundle`` (annual + quarterly
    statements in the provider's native shape)
  - ``to_validated_records(...)`` converts to ``ValidatedCleanedRecord``,
    the Stage 3 input contract
  - ``get_companyfact(...)`` is the escape hatch for specialty XBRL
    tags the cleaned record doesn't materialise (e.g. `RetainedEarnings
    AccumulatedDeficit` for the equity-bridge identity). FMP provider
    returns None for tags it doesn't expose; the hybrid provider falls
    through to XBRL.
  - ``coverage_report(...)`` lets the UI surface what the adapter
    actually populated, so the analyst sees data gaps without inspecting
    record blobs.

Each implementation gets a stable ``name`` ("fmp" | "xbrl" | "hybrid")
that flows through every persisted bundle as ``bundle["provider"]``,
keeping historical data self-describing across provider switches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from aletheia.contracts.pipeline import ValidatedCleanedRecord


@dataclass
class ProviderBundle:
    """Raw statements as returned by a provider, before conversion to
    the Stage 2 ``ValidatedCleanedRecord`` shape. Kept as a container so
    UI / debugging surfaces can inspect what the provider received from
    upstream before our normalisation runs.
    """
    ticker: str
    provider_name: str
    annual_income: List[Dict[str, Any]] = field(default_factory=list)
    annual_balance: List[Dict[str, Any]] = field(default_factory=list)
    annual_cashflow: List[Dict[str, Any]] = field(default_factory=list)
    quarterly_income: List[Dict[str, Any]] = field(default_factory=list)
    quarterly_balance: List[Dict[str, Any]] = field(default_factory=list)
    quarterly_cashflow: List[Dict[str, Any]] = field(default_factory=list)
    # Provider-specific extras (FMP key_metrics_ttm, XBRL companyfacts,
    # etc.) live in ``extras`` so the typed interface stays minimal.
    extras: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class FinancialDataProvider(Protocol):
    """Protocol every provider implements. ``name`` and methods are
    the only Stage 2/3/UI dependencies.
    """

    name: str

    def fetch(
        self, ticker: str, *, force_refresh: bool = False,
    ) -> ProviderBundle:
        """Pull annual + quarterly statements for the ticker. Cache-
        first when implemented; ``force_refresh`` bypasses any cache.
        """
        ...

    def to_validated_records(
        self,
        ticker: str,
        *,
        bundle: Optional[ProviderBundle] = None,
        force_refresh: bool = False,
    ) -> List[ValidatedCleanedRecord]:
        """Convert provider-native statements to the Stage 3 input
        contract. When ``bundle`` is None, the provider fetches first.
        """
        ...

    def get_companyfact(
        self, ticker: str, tag: str, fiscal_year: int,
        period: str = "FY",
    ) -> Optional[float]:
        """Specialty-tag escape hatch (XBRL-style point queries). FMP
        provider returns None for tags it doesn't carry; hybrid provider
        falls through to XBRL for these.
        """
        ...

    def coverage_report(
        self, records: List[ValidatedCleanedRecord],
    ) -> Dict[str, Any]:
        """Summary stats: years covered, fields populated, gaps."""
        ...
