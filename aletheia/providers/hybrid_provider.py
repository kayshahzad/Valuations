"""HybridProvider — FMP for IS/BS/CF flows, XBRL for specialty tags.

Why a hybrid: FMP gives us clean, fast, normalised statements (Revenue,
OperatingCF, CapEx, etc.) — what every Stage 3 engine needs. But it
doesn't expose certain SEC XBRL line items that the L1 identity audit
+ FCF pathway v2 + PP&E rollforward v2 explicitly require:

  - ``RetainedEarningsAccumulatedDeficit``         (equity-bridge identity)
  - ``AssetImpairmentCharges``                     (FCF v2 + PP&E v2)
  - ``GoodwillImpairmentLoss``
  - ``IntangibleAssetImpairmentCharge``
  - ``DeferredIncomeTaxExpenseBenefit``            (diagnostic only)
  - ``PaymentsRelatedToTaxWithholdingForShareBasedCompensation``
  - ``CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents``
  - ``EffectOfExchangeRateOnCashCashEquivalentsRestricted...``

The hybrid provider delegates:
  - ``fetch``                  → FmpProvider (records-side)
  - ``to_validated_records``   → FmpProvider (records-side)
  - ``get_companyfact``        → XbrlProvider (specialty-tag side)
  - ``coverage_report``        → FmpProvider's report

Future consumers that read specialty tags via ``provider.get_companyfact``
get XBRL-quality data without losing FMP's clean flow records. Existing
``RecordLoader``-based code in ``identity_checks.py`` is unaffected —
it still reads from SEC companyfacts directly. P6+ work can refactor
those call sites to route through the provider.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from aletheia.contracts.pipeline import ValidatedCleanedRecord
from aletheia.providers.base import ProviderBundle
from aletheia.providers.fmp_provider import FmpProvider
from aletheia.providers.xbrl_provider import XbrlProvider


class HybridProvider:
    """Best-of-both: FMP records + XBRL specialty tags."""

    name: str = "hybrid"

    def __init__(self) -> None:
        # Lazy — only initialise the side we actually need on first
        # call. Both providers are stateless after construction so
        # process-singleton is fine.
        self._fmp = FmpProvider()
        self._xbrl = XbrlProvider()

    def fetch(
        self, ticker: str, *, force_refresh: bool = False,
    ) -> ProviderBundle:
        """Use FMP's bundle as the canonical raw view (statement lists
        + TTM metrics). The bundle's ``provider_name`` stays "hybrid"
        so consumers can tell it apart from a pure-FMP fetch.
        """
        b = self._fmp.fetch(ticker, force_refresh=force_refresh)
        b.provider_name = self.name
        return b

    def to_validated_records(
        self,
        ticker: str,
        *,
        bundle: Optional[ProviderBundle] = None,
        force_refresh: bool = False,
    ) -> List[ValidatedCleanedRecord]:
        """Delegate to FMP — FMP's record shape is the canonical Stage
        3 input. The hybrid magic is in ``get_companyfact``."""
        return self._fmp.to_validated_records(
            ticker, bundle=bundle, force_refresh=force_refresh,
        )

    def get_companyfact(
        self, ticker: str, tag: str, fiscal_year: int,
        period: str = "FY",
    ) -> Optional[float]:
        """Specialty-tag lookup goes to XBRL companyfacts. FMP doesn't
        expose these line items; XBRL does."""
        return self._xbrl.get_companyfact(ticker, tag, fiscal_year, period)

    def coverage_report(
        self, records: List[ValidatedCleanedRecord],
    ) -> Dict[str, Any]:
        # Records came from FMP — its coverage report shape is what
        # downstream UI panels expect.
        return self._fmp.coverage_report(records)


__all__ = ["HybridProvider"]
