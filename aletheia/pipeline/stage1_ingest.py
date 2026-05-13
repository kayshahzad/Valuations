"""Stage 1 — Ingestion.

Captures what each external source returned for a ticker at a moment
in time. Byte-faithful, with full provenance. No interpretation, no
cleaning, no validation — Stage 2's job.

Boundary discipline (enforced by ``tests/architecture/
test_pipeline_layering.py``):
  - No imports from ``aletheia.calculations``,
    ``aletheia.data.cleaning_engine``, ``aletheia.data.ingestion_validator``,
    ``aletheia.tools.*``, ``aletheia.agents``, or ``aletheia.workflow``.
  - The module is a thin orchestrator over the existing fetchers
    (``edgar_client``, ``fmp_client``, ``market_data``). Each fetcher
    already persists its payload to disk and respects cache TTL; we
    read the persisted bytes back, compute the content sha256, and
    assemble the typed contract.

Sources (canonical identifiers in ``RawSource.source``):

  - sec_companyfacts                      SEC EDGAR XBRL company facts
  - fmp_income, fmp_balance_sheet,
    fmp_cashflow                          FMP statements (annual)
  - fmp_key_metrics, fmp_enterprise_values
                                          FMP annual aggregates
  - fmp_key_metrics_ttm, fmp_ratios_ttm   FMP pre-computed TTM
  - fmp_income_as_reported_quarter        FMP quarterly as-reported
  - fmp_profile                           FMP company profile
  - market_snapshot                       Live price + market cap +
                                          shares + beta + Rf

The bundle's ``bundle_fingerprint`` is content-addressed: identical
source payloads (every ``RawSource.payload_sha256`` unchanged) yield
an identical bundle_fingerprint, which is the cache-hit signal the
Week 6 orchestrator uses to skip downstream re-runs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from aletheia.contracts.pipeline import IngestedRawBundle, RawSource
from aletheia.data import edgar_client, fmp_client
from config.ticker_classification import UNIVERSE


# ─────────────────────────────────────────────────────────────────────
# Persistence layout
# ─────────────────────────────────────────────────────────────────────

SEC_COMPANYFACTS_DIR = Path("valuation_data/raw/sec/companyfacts")
MARKET_SNAPSHOT_DIR  = Path("valuation_data/raw/market")
# FMP cache is owned by ``aletheia.data.fmp_client._CACHE_DIR``
# (``valuation_data/macro/fmp/<TICKER>__<endpoint>.json``). Stage 1
# references those paths directly rather than re-writing — the cleaning
# engine and FMP validation gate already read from there. Migrating to
# a date-stamped layout per the contracts doc is a Week 8 cleanup.


# ─────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────

class Stage1IngestError(Exception):
    """Stage 1 input contract violation or unrecoverable fetch failure."""


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_source(
    *,
    source: str,
    url: str,
    payload_path: Path,
    metadata: Optional[Dict[str, Any]] = None,
    fetched_at: Optional[datetime] = None,
) -> RawSource:
    """Construct a ``RawSource`` from a persisted file. Reads the file
    to compute the content hash — caller must ensure the file exists.
    """
    return RawSource(
        source=source,
        url=url,
        fetched_at=fetched_at or datetime.now(timezone.utc),
        payload_path=payload_path,
        payload_sha256=_sha256_file(payload_path),
        metadata=metadata or {},
    )


# ─────────────────────────────────────────────────────────────────────
# Fetcher interfaces — injectable so tests don't require network
# ─────────────────────────────────────────────────────────────────────

# A fetcher returns the JSON-serialisable payload for one source. Each
# fetcher is responsible for caching (the real implementations
# delegate to fmp_client / edgar_client which already cache). Stage 1
# itself does no caching; it only orchestrates and computes hashes.
Fetcher = Callable[[str, bool], Optional[Any]]


class _Fetchers:
    """Default fetchers wrap the real production clients. Tests can
    substitute lightweight stand-ins via the ``fetchers=`` argument
    to ``run_stage1``."""

    @staticmethod
    def sec_companyfacts(ticker: str, force: bool) -> Optional[Dict[str, Any]]:
        sec = edgar_client.SecEdgar()
        cik = sec.resolve_cik(ticker)
        if not cik:
            return None
        path = SEC_COMPANYFACTS_DIR / f"CIK{cik}.json"
        if not path.exists() or force:
            facts = sec.fetch_company_facts(cik)
            if facts is None:
                return None
            SEC_COMPANYFACTS_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(facts, indent=2))
        return {"cik": cik, "path": str(path)}

    @staticmethod
    def fmp(endpoint_callable, ticker: str, force: bool) -> Optional[Any]:
        """Wrap an fmp_client fetcher; cache path is determined by
        fmp_client itself. We just invoke and return."""
        return endpoint_callable(ticker, force_refresh=force)

    @staticmethod
    def market_snapshot(ticker: str, _force: bool) -> Optional[Dict[str, Any]]:
        # ``_force`` is accepted for signature parity with the other
        # fetchers but ignored — market_data uses an in-memory cache
        # only and always returns the latest snapshot when called.
        # Lazy import keeps yfinance off the Stage 1 import path.
        from aletheia.data import market_data
        info = market_data.MarketDataCache.get_info(ticker)
        beta = market_data.get_beta(ticker)
        rf = market_data.get_risk_free_rate()
        return {
            "last_price": info.get("last_price"),
            "market_cap": info.get("market_cap"),
            "shares": info.get("shares"),
            "beta": beta,
            "risk_free_rate": rf,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────

# Default source set. Ordered so SEC (cheapest cache-validate) comes
# first; market snapshot last because it always hits the network.
_FMP_SOURCES = [
    ("fmp_income",            fmp_client.fetch_income_statements,             "income-statement",        "annual"),
    ("fmp_balance_sheet",     fmp_client.fetch_balance_sheets,                "balance-sheet-statement", "annual"),
    ("fmp_cashflow",          fmp_client.fetch_cash_flows,                    "cash-flow-statement",     "annual"),
    ("fmp_key_metrics",       fmp_client.fetch_key_metrics,                   "key-metrics",             "annual"),
    ("fmp_enterprise_values", fmp_client.fetch_enterprise_values,             "enterprise-values",       "annual"),
    ("fmp_key_metrics_ttm",   fmp_client.fetch_key_metrics_ttm,               "key-metrics-ttm",         None),
    ("fmp_ratios_ttm",        fmp_client.fetch_ratios_ttm,                    "ratios-ttm",              None),
    ("fmp_income_as_reported_quarter",
                              fmp_client.fetch_income_statement_as_reported_quarter,
                              "income-statement-as-reported", "quarter"),
    ("fmp_profile",           fmp_client.fetch_profile,                       "profile",                 None),
]


def _fmp_cache_path(ticker: str, endpoint_label: str, period: Optional[str]) -> Path:
    """Mirror fmp_client._cache_path label convention. The actual file
    path is constructed by fmp_client; we re-derive it for the
    RawSource.payload_path field."""
    if period:
        label = f"{endpoint_label}_{period}".replace("-", "_")
    else:
        label = endpoint_label.replace("-", "_")
    # Maps to fmp_client's internal labels. We hand-map only the ones
    # whose labels differ from the endpoint name (limit, no period).
    LABEL_MAP = {
        "income_statement_annual":            "income_annual",
        "balance_sheet_statement_annual":     "balance_annual",
        "cash_flow_statement_annual":         "cashflow_annual",
        "key_metrics_annual":                 "key_metrics_annual",
        "enterprise_values_annual":           "ev_annual",
        "key_metrics_ttm":                    "key_metrics_ttm",
        "ratios_ttm":                         "ratios_ttm",
        "income_statement_as_reported_quarter":
                                              "income_as_reported_quarter",
        "profile":                            "profile",
    }
    label = LABEL_MAP.get(label, label)
    return fmp_client._CACHE_DIR / f"{ticker.upper()}__{label}.json"


def _fmp_url(endpoint: str, period: Optional[str]) -> str:
    qs = f"period={period}&" if period else ""
    return f"https://financialmodelingprep.com/stable/{endpoint}?{qs}symbol=<TICKER>"


def run_stage1(
    ticker: str,
    *,
    pipeline_version: str,
    force_refresh: bool = False,
    sources: Optional[List[str]] = None,
    include_market_snapshot: bool = True,
) -> IngestedRawBundle:
    """Fetch all canonical sources for ``ticker`` and return a typed
    ``IngestedRawBundle``.

    Args:
        ticker: Symbol (case-insensitive).
        pipeline_version: Git SHA stamping the bundle, folded into the
            fingerprint so methodology bumps invalidate the cache.
        force_refresh: When True, bypasses each fetcher's TTL cache.
            Otherwise honours the per-fetcher freshness policy
            documented in ``docs/pipeline_contracts.md``.
        sources: Optional whitelist. When None, all canonical sources
            are fetched. When supplied, missing sources are dropped
            from the bundle (used by surgical re-fetch flows).
        include_market_snapshot: When False, skips the live market
            data fetch (yfinance). Useful for tests / offline runs.

    Raises:
        Stage1IngestError: unknown ticker, or required source returned
            empty (e.g., SEC EDGAR returns 403).
    """
    ticker = ticker.upper()
    if ticker not in UNIVERSE:
        raise Stage1IngestError(
            f"{ticker!r} not in UNIVERSE; add to "
            "config/ticker_classification.UNIVERSE before ingest."
        )

    sources_set = set(sources) if sources is not None else None
    collected: Dict[str, RawSource] = {}

    # ── SEC companyfacts ────────────────────────────────────────────
    if sources_set is None or "sec_companyfacts" in sources_set:
        sec_meta = _Fetchers.sec_companyfacts(ticker, force_refresh)
        if sec_meta is None:
            raise Stage1IngestError(
                f"SEC companyfacts fetch failed for {ticker} (CIK "
                "resolution or HTTP failure)."
            )
        path = Path(sec_meta["path"])
        collected["sec_companyfacts"] = _make_source(
            source="sec_companyfacts",
            url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{sec_meta['cik']}.json",
            payload_path=path,
            metadata={"cik": sec_meta["cik"]},
        )

    # ── FMP endpoints ───────────────────────────────────────────────
    for src_id, endpoint_callable, endpoint_path, period in _FMP_SOURCES:
        if sources_set is not None and src_id not in sources_set:
            continue
        data = _Fetchers.fmp(endpoint_callable, ticker, force_refresh)
        if data is None:
            # Missing FMP endpoint is non-fatal — Stage 2 / agent gates
            # decide whether the absence breaks downstream. Record the
            # gap by simply omitting this source from the bundle.
            continue
        cache_path = _fmp_cache_path(ticker, endpoint_path, period)
        if not cache_path.exists():
            # The fetcher returned data but the cache file doesn't
            # exist — likely the cache path mapping is stale. Persist
            # the payload ourselves so the bundle is self-contained.
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({
                "_cached_at": datetime.now(timezone.utc).isoformat(),
                "ticker": ticker,
                "endpoint": src_id,
                "data": data,
            }, indent=2, default=str))
        collected[src_id] = _make_source(
            source=src_id,
            url=_fmp_url(endpoint_path, period),
            payload_path=cache_path,
            metadata={"period": period} if period else {},
        )

    # ── Market snapshot ─────────────────────────────────────────────
    if include_market_snapshot and (
        sources_set is None or "market_snapshot" in sources_set
    ):
        snapshot = _Fetchers.market_snapshot(ticker, force_refresh)
        if snapshot:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            snap_dir = MARKET_SNAPSHOT_DIR / ticker
            snap_dir.mkdir(parents=True, exist_ok=True)
            snap_path = snap_dir / f"snapshot_{date_str}.json"
            snap_path.write_text(json.dumps(snapshot, indent=2, default=str))
            collected["market_snapshot"] = _make_source(
                source="market_snapshot",
                url="local://yfinance",
                payload_path=snap_path,
                metadata={"date_utc": date_str},
            )

    # ── Bundle fingerprint ──────────────────────────────────────────
    bundle_fingerprint = _compute_bundle_fingerprint(
        ticker=ticker,
        sources=collected,
        pipeline_version=pipeline_version,
    )

    # ── Classification snapshot ─────────────────────────────────────
    classification = UNIVERSE[ticker]
    classification_snapshot = {
        "ticker": classification.ticker,
        "sector": classification.sector,
        "industry": classification.industry,
        "lifecycle": classification.lifecycle,
        "business_model": classification.business_model,
        "is_ifrs_filer": getattr(classification, "is_ifrs_filer", False),
        "notes": getattr(classification, "notes", "") or "",
        "last_reviewed": getattr(classification, "last_reviewed", "") or "",
    }

    fetched_ats = [s.fetched_at for s in collected.values()]
    earliest = min(fetched_ats) if fetched_ats else datetime.now(timezone.utc)

    return IngestedRawBundle(
        ticker=ticker,
        bundle_fingerprint=bundle_fingerprint,
        fetched_at=earliest,
        sources=collected,
        classification_snapshot=classification_snapshot,
        pipeline_version=pipeline_version,
    )


def _compute_bundle_fingerprint(
    *,
    ticker: str,
    sources: Dict[str, RawSource],
    pipeline_version: str,
) -> str:
    """SHA-256 over (sorted source ids + each source's payload_sha256
    + ticker + pipeline_version). Stable across identical re-fetches.
    """
    parts = [ticker, pipeline_version]
    for src_id in sorted(sources.keys()):
        parts.append(src_id)
        parts.append(sources[src_id].payload_sha256)
    return _sha256_bytes("|".join(parts).encode())


__all__ = [
    "Stage1IngestError",
    "run_stage1",
]
