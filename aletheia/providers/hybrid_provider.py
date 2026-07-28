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


# XBRL us-gaap tags injected into each record during enrichment.
# Field name on the record matches the XBRL tag verbatim so downstream
# consumers can look them up without an alias table. Grouped by purpose.
_SPECIALTY_TAGS = (
    # Impairment / restructuring — used by the ex-unusual add-back in
    # ``_ex_impairment_addback`` to produce Capital-IQ-aligned operating
    # income figures.
    "AssetImpairmentCharges",
    "GoodwillImpairmentLoss",
    "IntangibleAssetImpairmentCharge",
    "ImpairmentOfLongLivedAssetsHeldForUse",   # alternate impairment tag
    "RestructuringCharges",
    # Additive one-time charges of the same class — a litigation settlement is
    # as much an accounting event as an impairment. Widening the capture closes
    # the generic hole rather than the CNC-specific one.
    "LitigationSettlementExpense",

    # Equity-bridge identity (A=L+E). NEE-class utility filers report
    # accumulated deficit / regulatory liabilities under tags FMP doesn't
    # expose; injecting these lets the schema-contract A=L+E check
    # reconcile without per-ticker overrides.
    "RetainedEarningsAccumulatedDeficit",

    # Diagnostic — surfaces non-cash tax timing differences for analyst
    # review. Not currently consumed by any derivation but is an
    # auditable input for future tax-rate normalization.
    "DeferredIncomeTaxExpenseBenefit",
)


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
        """Build records via FMP, then enrich each year with XBRL
        specialty tags and re-run the derivation pass.

        The enrichment loop:
          1. FmpProvider builds the base records (raw / clean / derived).
          2. For each fiscal year, query XBRL companyfacts for the
             impairment-family tags. Inject any non-None hits into
             ``raw`` + ``clean`` (the tags are facts, not derivations,
             so they belong on both sides).
          3. Re-run ``_compute_derived`` so fields with a fallback
             chain (OperatingIncome_ExUnusual etc.) now pick the
             XBRL-discrete-tag branch instead of FMP's kitchen-sink
             ``otherExpenses`` bucket. Provenance stamped on the
             record so the UI can show which path was used.

        Records ValidatedCleanedRecord is pydantic-frozen, so we use
        ``.model_copy(update=...)`` to produce new instances rather
        than mutating in place.
        """
        # Local import to avoid circular reference (fmp_stage3_adapter
        # imports providers.* in some test paths).
        from aletheia.validation.fmp_stage3_adapter import (
            _compute_derived,
        )

        records = self._fmp.to_validated_records(
            ticker, bundle=bundle, force_refresh=force_refresh,
        )

        enriched: List[ValidatedCleanedRecord] = []
        for r in records:
            new_raw = dict(r.raw)
            new_clean = dict(r.clean)

            # XBRL specialty-tag enrichment.
            for tag in _SPECIALTY_TAGS:
                v = self._xbrl.get_companyfact(
                    ticker, tag, r.fiscal_year,
                )
                if v is not None:
                    new_raw[tag] = v
                    new_clean[tag] = v

            # Re-run derivation on the merged view so ex-unusual
            # fields pick up discrete impairment tags when available.
            # _compute_derived returns a new derived dict AND mutates
            # `rec` with non-derived helper fields (NetBuyback_AfterSBC,
            # OperatingIncome_ExUnusual, DividendsPerShare, etc.).
            merged = {**new_raw, **new_clean}
            new_derived = _compute_derived(merged)
            # Lift derive-side mutations back into the clean dict so
            # they reach the DB column writer.
            for k, v in merged.items():
                if k not in new_clean or new_clean.get(k) != v:
                    new_clean[k] = v

            enriched.append(r.model_copy(update={
                "raw":     new_raw,
                "clean":   new_clean,
                "derived": new_derived,
            }))

        return enriched

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
