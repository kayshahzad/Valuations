"""XBRL/SEC-backed provider (opt-in legacy path).

Wraps the existing SEC-XBRL + cleaning_engine + DuckDB stack behind
the ``FinancialDataProvider`` protocol. ``to_validated_records``
reconstructs ``ValidatedCleanedRecord`` from the existing
``InvestmentDatabase`` rows — Stage 1 SEC ingest is assumed to have
already populated the DB (this provider doesn't trigger ingest).

When ``ALETHEIA_PROVIDER=xbrl``, the rest of the pipeline reads from
this provider and ignores FMP entirely (specialty-tag escape hatch
still pulls from SEC companyfacts as today). The hybrid provider (P5)
will combine FMP flows + XBRL specialty tags for best-of-both.

This provider is functionally identical to the path you've been
running for the past year — same DB rows, same cleaned blobs, same
identity-audit XBRL fallbacks. The protocol wrapping is what makes
the source switchable without touching downstream code.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from aletheia.contracts.pipeline import (
    ValidatedCleanedRecord,
    ValidationReceipt,
)
from aletheia.providers.base import ProviderBundle


_SEC_FACTS_DIR = Path("valuation_data/raw/sec/companyfacts")

# Fields the cleaning_engine emits into its ``clean`` dict that the
# Stage 3 engines read from the ``derived_*`` namespace via
# ``get_with_provenance``. Without splitting them out, the DCF engine
# fails ``MissingFieldError`` on Depreciation_Total, and screening /
# multiple_decomposition lose ROIC / FCF / NetDebt / EBITDA / margin
# percents. Allow-list mirrors what ``FmpProvider`` populates via
# ``_compute_derived`` so both providers produce the same flattened
# DataFrame shape downstream.
#
# Maintenance: when ``aletheia/data/cleaning_engine.py`` adds a new
# derived field that any Stage 3 engine reads as ``derived_*``, add
# the field name here. The set is intentionally narrow — only fields
# that the engines look up under ``derived_*``, NOT every computed
# value the cleaning engine emits.
_CLEAN_FIELDS_THAT_ARE_DERIVED: frozenset = frozenset({
    "ROIC", "FCF", "NetDebt", "InvestedCapital",
    "EBITDA", "Depreciation_Total",
    "EBIT_Margin_Pct", "FCF_Margin_Pct", "GrossMargin_Pct", "ROE",
})


class XbrlProvider:
    """Opt-in legacy path. Reads from the canonical DuckDB cache that
    Stage 1 ingest + cleaning_engine produces.
    """

    name: str = "xbrl"

    def __init__(self) -> None:
        # SecEdgar client is lazy — we only initialise when
        # ``get_companyfact`` is actually called.
        self._sec_client = None
        self._companyfacts_cache: Dict[str, Dict[str, Any]] = {}

    def fetch(
        self, ticker: str, *, force_refresh: bool = False,
    ) -> ProviderBundle:
        """Pull all DB rows for the ticker. ``force_refresh`` is
        accepted for protocol parity but logs a hint and otherwise
        no-ops — Stage 1 ingest owns SEC fetching; if you need fresh
        SEC data, run Stage 1 with ``--force-refresh`` from the CLI.
        """
        if force_refresh:
            import logging
            logging.getLogger(__name__).info(
                "XbrlProvider.fetch(force_refresh=True) is a no-op; "
                "run Stage 1 with --force-refresh to repopulate SEC data."
            )
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        try:
            df = db.get_latest(ticker)
        finally:
            db.close()
        rows: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            # DuckDB returns date columns as pandas Timestamp; the
            # ValidatedCleanedRecord contract requires an ISO string.
            ped = row.get("period_end_date")
            if hasattr(ped, "strftime"):
                ped_str = ped.strftime("%Y-%m-%d")
            elif isinstance(ped, str):
                ped_str = ped[:10]
            else:
                ped_str = ""
            rows.append({
                "ticker": ticker,
                "fiscal_year": int(row["fiscal_year"]),
                "period": row.get("period") or "FY",
                "period_end_date": ped_str,
                "raw_json": row.get("raw_json"),
                "clean_json": row.get("clean_json"),
            })
        # DuckDB rows aren't separated by annual/quarterly statement
        # bucket — the provider bundle here is shaped for parity with
        # FmpProvider rather than 1:1 round-trip. Stage 2/3 consume
        # via ``to_validated_records`` which reads the DB directly.
        return ProviderBundle(
            ticker=ticker,
            provider_name=self.name,
            annual_income=[r for r in rows if r["period"] == "FY"],
            quarterly_income=[r for r in rows if r["period"] in ("Q1", "Q2", "Q3", "Q4")],
            extras={"ttm_rows": [r for r in rows if r["period"] == "TTM"]},
        )

    def to_validated_records(
        self,
        ticker: str,
        *,
        bundle: Optional[ProviderBundle] = None,
        force_refresh: bool = False,
    ) -> List[ValidatedCleanedRecord]:
        """Reconstruct ``ValidatedCleanedRecord`` from the DB cache.

        Mirrors the shape Stage 2 validate produces. Designed so any
        downstream code that calls ``provider.to_validated_records``
        gets the same data the legacy pipeline currently produces.
        """
        if bundle is None:
            bundle = self.fetch(ticker, force_refresh=force_refresh)

        out: List[ValidatedCleanedRecord] = []
        # Union all period buckets — we read the DB rows directly.
        all_rows = (
            list(bundle.annual_income)
            + list(bundle.quarterly_income)
            + list(bundle.extras.get("ttm_rows") or [])
        )
        for row in all_rows:
            try:
                raw = json.loads(row["raw_json"]) if isinstance(row.get("raw_json"), str) else {}
                clean = json.loads(row["clean_json"]) if isinstance(row.get("clean_json"), str) else {}
            except (TypeError, json.JSONDecodeError):
                raw, clean = {}, {}

            # ValidatedCleanedRecord enforces Dict[str, Optional[float]]
            # — filter non-numeric values that may live in the legacy
            # JSON blobs (e.g. period_end_date strings, source flags).
            def _numeric_only(d: Dict[str, Any]) -> Dict[str, Optional[float]]:
                return {
                    k: v for k, v in d.items()
                    if v is None or isinstance(v, (int, float))
                }

            # B1 fix — the legacy DB's clean_json blob contains both
            # the cleaning_engine's true ``clean`` outputs (Revenue,
            # OperatingCF, etc.) and its ``derived`` outputs (FCF,
            # ROIC, NetDebt, etc.) merged into a single dict. Stage 3
            # engines read derived fields via the ``derived_*`` column
            # namespace (e.g. DCFEngine.get_with_provenance("FCF") →
            # raw_FCF | derived_FCF, never clean_FCF). Split the blob
            # so derived fields land in r.derived → derived_* columns
            # after _records_to_dataframe flattens.
            clean_numeric = _numeric_only(clean)
            derived_dict: Dict[str, Optional[float]] = {}
            clean_dict: Dict[str, Optional[float]] = {}
            for k, v in clean_numeric.items():
                if k in _CLEAN_FIELDS_THAT_ARE_DERIVED:
                    derived_dict[k] = v
                else:
                    clean_dict[k] = v

            rec = ValidatedCleanedRecord(
                ticker=ticker,
                fiscal_year=int(row["fiscal_year"]),
                period=row.get("period") or "FY",
                period_end_date=row.get("period_end_date") or "1900-01-01",
                raw=_numeric_only(raw),
                clean=clean_dict,
                derived=derived_dict,
                raw_blob_json=row.get("raw_json"),
                clean_blob_json=row.get("clean_json"),
                overall_quality_score=1.0,
                cleaning_warnings=[],
                blocking_errors=[],
                validation=ValidationReceipt(
                    schema_violations=[],
                    fmp_validation={"source": "xbrl_provider"},
                    cross_source_agreement={},
                    overrides_applied=[],
                ),
                record_fingerprint=f"xbrl-{ticker}-{row['fiscal_year']}-{row['period']}",
                input_bundle_fingerprint="xbrl",
                cleaned_at=datetime.now(timezone.utc),
                pipeline_version="xbrl-provider",
            )
            out.append(rec)
        return out

    def get_companyfact(
        self, ticker: str, tag: str, fiscal_year: int,
        period: str = "FY",
    ) -> Optional[float]:
        """Read one fact from the SEC companyfacts JSON. Matches the
        existing ``RecordLoader.xbrl_fact`` semantics so identity
        checks see the same value whether they go through the loader
        or the provider.
        """
        us_gaap = self._load_companyfacts(ticker)
        if not us_gaap:
            return None
        entry = us_gaap.get(tag)
        if not entry:
            return None
        form = "10-K" if period == "FY" else "10-Q"
        fp = "FY" if period == "FY" else period
        candidates: list = []
        for items in (entry.get("units") or {}).values():
            for u in items:
                if (
                    u.get("form") == form
                    and u.get("fp") == fp
                    and u.get("fy") == fiscal_year
                ):
                    candidates.append(u)
        if not candidates:
            return None
        # Prefer most-recently-filed entry (same tie-break logic as
        # the comparison pipeline).
        latest = max(
            candidates,
            key=lambda u: (u.get("filed") or "", u.get("end") or ""),
        )
        val = latest.get("val")
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def _load_companyfacts(self, ticker: str) -> Dict[str, Any]:
        """Load and cache the us-gaap section of one ticker's
        SEC companyfacts JSON."""
        if ticker in self._companyfacts_cache:
            return self._companyfacts_cache[ticker]
        if self._sec_client is None:
            from aletheia.data import edgar_client
            self._sec_client = edgar_client.SecEdgar()
        cik = self._sec_client.resolve_cik(ticker)
        if not cik:
            self._companyfacts_cache[ticker] = {}
            return {}
        path = _SEC_FACTS_DIR / f"CIK{cik}.json"
        if not path.exists():
            self._companyfacts_cache[ticker] = {}
            return {}
        try:
            data = json.loads(path.read_text())
            us = (data.get("facts") or {}).get("us-gaap") or {}
        except Exception:  # noqa: BLE001
            us = {}
        self._companyfacts_cache[ticker] = us
        return us

    def coverage_report(
        self, records: List[ValidatedCleanedRecord],
    ) -> Dict[str, Any]:
        """Same shape as FmpProvider's report so the UI's coverage
        panel doesn't branch on provider."""
        if not records:
            return {"n_years": 0, "fy_range": (None, None), "field_coverage": {}}
        all_keys: set = set()
        for r in records:
            all_keys.update(r.clean.keys())
        field_cov: Dict[str, int] = {}
        for k in all_keys:
            field_cov[k] = sum(1 for r in records if r.clean.get(k) is not None)
        fy_records = [r for r in records if r.period == "FY"]
        years = sorted(r.fiscal_year for r in fy_records)
        return {
            "n_years": len(fy_records),
            "fy_range": (years[0], years[-1]) if years else (None, None),
            "field_coverage": dict(sorted(field_cov.items())),
        }


__all__ = ["XbrlProvider"]
