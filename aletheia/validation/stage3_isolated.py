"""Run Stage 3 in isolation using any registered data provider.

P3 generalisation: this runner no longer hard-codes the FMP adapter.
It resolves the active provider via ``aletheia.providers.get_provider``
(FMP default, XBRL opt-in, hybrid coming in P5) and routes records
through whatever provider is configured. The legacy FMP-only behaviour
is preserved when ``provider="fmp"`` or no override is supplied.

No Stage 1 / 2 paths invoked, no DB writes. The returned bundle is
stamped with ``provider`` so downstream UI / cross-source diffing can
tell the bundles apart.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from aletheia.contracts.pipeline import CalculationBundle
from aletheia.pipeline.stage3_calculate import run_stage3
from aletheia.providers import get_provider
from config.ticker_classification import TickerClassification, UNIVERSE


AUDIT_DIR = Path("audits")


def _resolve_classification(ticker: str) -> TickerClassification:
    """Use the universe entry when present, otherwise synthesise a
    neutral default so unknown tickers can still be validated.
    """
    existing = UNIVERSE.get(ticker)
    if existing is not None:
        return existing
    return TickerClassification(
        ticker=ticker,
        sector="Unknown",
        industry="Unknown",
        lifecycle="growth_compounder",
        business_model="fcff_compatible",
        notes="Synthesised default for isolated provider validation",
        last_reviewed=date.today(),
    )


def run_stage3_isolated(
    ticker: str,
    *,
    provider: Optional[str] = None,
    write_audit: bool = True,
    force_refresh_fmp: bool = False,
) -> Dict[str, Any]:
    """Run Stage 3 against records from the configured provider.

    Args:
        ticker: Symbol to validate (case-insensitive).
        provider: Override the registry-resolved provider. ``None``
            (default) lets the registry decide based on session/env/
            config (FMP-default after the P1 flip).
        write_audit: Persist the bundle to ``audits/``.
        force_refresh_fmp: When the provider is FMP-based, bypass the
            on-disk cache and hit the API. XBRL provider ignores this
            (Stage 1 owns SEC fetching).

    Returns a dict:
      - ``ticker``           — input ticker
      - ``provider``         — resolved provider name (fmp | xbrl | hybrid)
      - ``records_built``    — int
      - ``coverage``         — per-field population stats
      - ``bundle``           — CalculationBundle.model_dump() (None on failure)
      - ``error``            — str (set when records empty or Stage 3 raised)
      - ``audit_path``       — str (when write_audit succeeded)
      - ``elapsed_s``        — float
    """
    t0 = time.time()
    prov = get_provider(provider)
    records = prov.to_validated_records(
        ticker, force_refresh=force_refresh_fmp,
    )
    cov = prov.coverage_report(records)
    out: Dict[str, Any] = {
        "ticker": ticker,
        "provider": prov.name,
        "records_built": len(records),
        "coverage": cov,
        "bundle": None,
        "error": None,
        "audit_path": None,
    }
    if not records:
        out["error"] = (
            f"Provider {prov.name!r} returned no usable records for "
            f"{ticker}. For FMP: check disk cache + API quota. For "
            "XBRL: confirm Stage 1 has run and the DB has rows."
        )
        out["elapsed_s"] = time.time() - t0
        return out

    classification = _resolve_classification(ticker)
    try:
        bundle: CalculationBundle = run_stage3(
            records,
            classification=classification,
            pipeline_version=f"{prov.name}-iso-validation",
        )
    except Exception as exc:  # noqa: BLE001 — surface any Stage 3 failure
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["elapsed_s"] = time.time() - t0
        return out

    bundle_dump = bundle.model_dump()
    # Stamp the provider on the bundle so historical audits stay
    # self-describing across provider switches. Cross-source diffing
    # later (P6) keys off this.
    bundle_dump["provider"] = prov.name
    out["bundle"] = bundle_dump

    if write_audit:
        AUDIT_DIR.mkdir(exist_ok=True)
        ts = int(time.time())
        # Audit filename carries the provider so a `ls audits/` shows
        # which source each historical bundle came from.
        path = AUDIT_DIR / f"{prov.name}_validation_{ticker}_{ts}.json"
        path.write_text(json.dumps(bundle_dump, default=str, indent=2))
        out["audit_path"] = str(path)

    out["elapsed_s"] = time.time() - t0
    return out
