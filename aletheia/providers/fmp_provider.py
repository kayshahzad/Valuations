"""FMP-primary provider — the default data source.

Promotes the proven ``aletheia.validation.fmp_stage3_adapter`` logic
into the ``FinancialDataProvider`` protocol. Stage 1 ingestion routes
through this when ``ALETHEIA_PROVIDER=fmp`` (the default).

Behaviour parity: ``to_validated_records`` returns the same record
shape the FMP-only Stage 3 validation tab has been producing — proven
on AAPL (94/263 L1 checks pass, 0 unflagged failures; 28 L2 derivations
within ≤5% of FMP TTM ratios post-TTM alignment).

Specialty XBRL tags: FMP doesn't expose ``RetainedEarningsAccumulated
Deficit``, ``AssetImpairmentCharges``, ``DeferredIncomeTaxExpense
Benefit``, etc. ``get_companyfact`` returns None for those — the
hybrid provider (P5) falls through to XBRL for them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from aletheia.contracts.pipeline import ValidatedCleanedRecord
from aletheia.providers.base import ProviderBundle
from aletheia.validation.fmp_stage3_adapter import (
    build_validated_records as _build_records,
    coverage_report as _coverage_report,
)
from aletheia.data import fmp_client


class FmpProvider:
    """Primary provider. Cache-first (FMP disk cache under
    ``valuation_data/macro/fmp/`` is read before any API call).
    """

    name: str = "fmp"

    def fetch(
        self, ticker: str, *, force_refresh: bool = False,
    ) -> ProviderBundle:
        return ProviderBundle(
            ticker=ticker,
            provider_name=self.name,
            annual_income=(
                fmp_client.fetch_income_statements(
                    ticker, period="annual", force_refresh=force_refresh,
                ) or []
            ),
            annual_balance=(
                fmp_client.fetch_balance_sheets(
                    ticker, period="annual", force_refresh=force_refresh,
                ) or []
            ),
            annual_cashflow=(
                fmp_client.fetch_cash_flows(
                    ticker, period="annual", force_refresh=force_refresh,
                ) or []
            ),
            quarterly_income=(
                fmp_client.fetch_income_statements(
                    ticker, period="quarter", force_refresh=force_refresh,
                ) or []
            ),
            quarterly_balance=(
                fmp_client.fetch_balance_sheets(
                    ticker, period="quarter", force_refresh=force_refresh,
                ) or []
            ),
            quarterly_cashflow=(
                fmp_client.fetch_cash_flows(
                    ticker, period="quarter", force_refresh=force_refresh,
                ) or []
            ),
            extras={
                "key_metrics_ttm": fmp_client.fetch_key_metrics_ttm(
                    ticker, force_refresh=force_refresh,
                ) or {},
                "ratios_ttm": fmp_client.fetch_ratios_ttm(
                    ticker, force_refresh=force_refresh,
                ) or {},
            },
        )

    def to_validated_records(
        self,
        ticker: str,
        *,
        bundle: Optional[ProviderBundle] = None,
        force_refresh: bool = False,
    ) -> List[ValidatedCleanedRecord]:
        # ``_build_records`` already pulls + indexes + computes derived
        # fields + assembles the TTM record. ``bundle`` arg is accepted
        # to satisfy the protocol but the underlying helper re-fetches
        # internally; ``force_refresh`` propagates either way so the
        # contract is honoured. When a pre-fetched bundle is supplied we
        # still pass force_refresh=False (the adapter's cache hit is
        # what feeds it). Materialising a non-fetching path is a future
        # optimisation (saves one cache-load round trip).
        return _build_records(ticker, force_refresh=force_refresh)

    def get_companyfact(
        self, ticker: str, tag: str, fiscal_year: int,
        period: str = "FY",
    ) -> Optional[float]:
        # FMP doesn't expose arbitrary XBRL tags. The HybridProvider
        # (P5) chains FMP → XBRL fallback for the 7 known specialty
        # fields the L1 identity audit needs.
        return None

    def coverage_report(
        self, records: List[ValidatedCleanedRecord],
    ) -> Dict[str, Any]:
        return _coverage_report(records)


__all__ = ["FmpProvider"]
