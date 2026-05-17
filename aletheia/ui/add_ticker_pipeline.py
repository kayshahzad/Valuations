"""
aletheia/ui/add_ticker_pipeline.py

End-to-end "add a new ticker" orchestrator that the Streamlit UI can drive.
For a given ticker, runs:

  1. SEC ingestion — resolve CIK, download companyfacts JSON, run cleaning
     engine for every fiscal year on file, upsert to DuckDB.
  2. SEC XBRL validation — byte-perfect comparison of cleaned `raw_<field>`
     against canonical us-gaap tagged values for the latest FY.
  3. FMP cross-source validation — comparison of statement lines + derived
     ratios against FinancialModelingPrep for the same FY. Fails-soft when
     the ticker is subscription-restricted, currency-mismatched, or FMP's
     daily quota is exhausted.

The orchestrator is implemented as a generator so the UI can stream
progress messages and intermediate counts; the final yielded value is a
dict with the full per-step results suitable for rendering as a table.

The point of this is to make a freshly-added ticker indistinguishable from
the universe's pre-validated ones: same data on disk, same DB rows, same
validation reports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional


_VALID_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


@dataclass
class StepUpdate:
    """A single progress update emitted by the orchestrator."""
    step: str               # short label (e.g., "ingest", "sec_validate")
    status: str             # "running" | "ok" | "error" | "warning"
    message: str            # human-readable line
    detail: Optional[str] = None     # optional multi-line block (logs, sub-counts)


@dataclass
class PipelineResult:
    """Final result emitted as the last yield."""
    ticker: str
    success: bool
    fiscal_year: Optional[int] = None
    steps: List[StepUpdate] = field(default_factory=list)
    sec_validation: Optional[Dict[str, Any]] = None
    fmp_validation: Optional[Dict[str, Any]] = None


def _validate_ticker_format(ticker: str) -> Optional[str]:
    """Normalize + sanity-check. Returns clean ticker or None if invalid."""
    if not ticker:
        return None
    t = ticker.strip().upper()
    if not _VALID_TICKER.match(t):
        return None
    return t


def run_add_ticker_pipeline(
    ticker: str, *, provider: Optional[str] = None,
) -> Generator[Any, None, None]:
    """
    Generator that yields `StepUpdate` objects in real time and a final
    `PipelineResult` as its last value. The UI consumes the stream to drive
    a live progress panel.

    Args:
        ticker: Symbol to add.
        provider: Data-source provider for Stage 1-3. Defaults to whatever
            the registry resolves (``ALETHEIA_PROVIDER`` env var, then
            config default — currently ``"fmp"``). Add Ticker honours the
            sidebar selector when invoked from the UI.
    """
    clean_ticker = _validate_ticker_format(ticker)
    if not clean_ticker:
        yield StepUpdate(
            "validate_input", "error",
            f"Invalid ticker format: {ticker!r}. Expected 1–10 uppercase chars (letters, digits, dot, hyphen).",
        )
        yield PipelineResult(ticker=ticker or "", success=False)
        return

    result = PipelineResult(ticker=clean_ticker, success=False)

    # ── Step 0: write a default classification if the ticker is new ────────
    # This is what makes a freshly-added ticker appear in the Universe tab
    # under the curated/runtime union. Defaults are conservative
    # (fcff_compatible, generic sector); analyst can refine in
    # config/ticker_classification.py later.
    try:
        from config.ticker_classification import (
            add_runtime_classification, get_extended_universe,
        )
        added = add_runtime_classification(clean_ticker)
        if added:
            # Read back the actual stored classification — add_runtime_classification
            # auto-derives sector/industry/business_model from FMP /profile when
            # the caller doesn't override, so the real values are richer than
            # "Unknown" defaults.
            entry = get_extended_universe().get(clean_ticker)
            if entry is not None:
                msg = (
                    f"Auto-classified {clean_ticker} from FMP /profile: "
                    f"sector={entry.sector}, industry={entry.industry}, "
                    f"business_model={entry.business_model}"
                    f"{', IFRS filer' if entry.is_ifrs_filer else ''}. "
                    "Lifecycle defaulted to growth_compounder — refine in "
                    "config/ticker_classification.py if needed."
                )
            else:
                msg = (
                    f"Wrote default classification for {clean_ticker} "
                    "(FMP profile unavailable — review and refine when convenient)."
                )
            yield StepUpdate("classify", "ok", msg)
            result.steps.append(StepUpdate("classify", "ok", msg))
    except Exception as e:
        # Fail-soft: classification is convenient for routing but not required
        # for ingest/validation to succeed.
        yield StepUpdate(
            "classify", "warning",
            f"Could not write classification: {type(e).__name__}: {e}",
        )

    # ── Step 1: Stages 1-3 via the modern orchestrator ──────────────────
    # Replaces the legacy EdgarIngester-only path. The orchestrator
    # runs Stage 1 (ingest) → Stage 2 (clean / provider) → Stage 3
    # (calc engines + identity audit), writes pipeline_status rows,
    # and honors the active provider for source routing. Stage 4 is
    # deliberately deferred — the analyst clicks the sidebar
    # "🧠 Run Stage 4 (LLM)" button when ready to spend LLM budget.
    yield StepUpdate(
        "orchestrator", "running",
        f"Running Stages 1-3 via orchestrator for {clean_ticker} "
        f"(provider={provider or 'default'})…",
    )
    try:
        from aletheia.pipeline.orchestrator import Orchestrator
        from aletheia.cli.pipeline import detect_pipeline_version
        pipeline_version = detect_pipeline_version()
        with Orchestrator() as orch:
            orch_result = orch.run(
                clean_ticker,
                pipeline_version=pipeline_version,
                auto_agents=False,
                provider=provider,
            )
    except Exception as e:
        yield StepUpdate(
            "orchestrator", "error",
            f"Orchestrator crashed: {type(e).__name__}: {e}",
        )
        result.steps.append(StepUpdate("orchestrator", "error", str(e)))
        yield result
        return

    # Per-stage step updates so the analyst sees what happened. Stage 3
    # failures degrade to "warning" (the ticker is still added; DCF
    # may not be available for routing_required / ddm_required filers
    # but Stage 1/2 data is valid).
    stage_labels = {
        "stage1_ingest":    "Stage 1 (ingest)",
        "stage2_validate":  "Stage 2 (clean)",
        "stage3_calculate": "Stage 3 (calc)",
    }
    for stage_id, label in stage_labels.items():
        outcome = orch_result.stages.get(stage_id)
        if outcome is None:
            continue
        status = outcome.status.value
        if status == "ok":
            msg = f"✓ {label} OK ({outcome.duration_seconds:.1f}s)"
            yield StepUpdate(stage_id, "ok", msg)
            result.steps.append(StepUpdate(stage_id, "ok", msg))
        elif status == "skipped_cached":
            msg = f"⊘ {label} cached (fingerprint match)"
            yield StepUpdate(stage_id, "ok", msg)
            result.steps.append(StepUpdate(stage_id, "ok", msg))
        else:
            # failed / skipped_dependency — Stage 3 failure on
            # routing_required tickers is expected. Tag as warning so
            # the rest of the flow continues.
            severity = "warning" if stage_id == "stage3_calculate" else "error"
            msg = (
                f"⚠ {label} {status}: "
                f"{outcome.error_message or 'no detail'}"
            )
            yield StepUpdate(stage_id, severity, msg)
            result.steps.append(StepUpdate(stage_id, severity, msg))
            if severity == "error":
                yield result
                return

    # Confirm DB has rows; pull latest fiscal year for downstream
    # validation. (Same as before, but DB is now populated by Stage 2
    # regardless of provider choice.)
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        calc = make_calc_input(clean_ticker)
        df = calc.df
        if df.empty:
            yield StepUpdate(
                "ingest", "error",
                "Stage 2 ran but no records reached the DB.",
            )
            yield result
            return
        latest_fy = int(df["fiscal_year"].max())
        n_years = len(df)
        result.fiscal_year = latest_fy
    except Exception as e:
        yield StepUpdate(
            "ingest", "error",
            f"Could not load post-orchestrator records: "
            f"{type(e).__name__}: {e}",
        )
        yield result
        return

    # Compact summary the analyst can confirm at a glance before the
    # SEC/FMP validation tables render.
    summary_msg = (
        f"DB has {n_years} records (latest FY{latest_fy}). "
        f"Pipeline Status matrix updated for stages 1-3."
    )
    yield StepUpdate("db_ready", "ok", summary_msg)
    result.steps.append(StepUpdate("db_ready", "ok", summary_msg))

    # ── Step 2: SEC XBRL validation ────────────────────────────────────────
    yield StepUpdate(
        "sec_validate", "running",
        f"Validating cleaned record against SEC XBRL companyfacts for FY{latest_fy}…",
    )
    try:
        from scripts.validate_sec import validate_ticker as sec_validate
        sec_result = sec_validate(clean_ticker, fy=latest_fy)
    except Exception as e:
        sec_result = {"error": f"{type(e).__name__}: {e}"}

    result.sec_validation = sec_result
    if sec_result.get("error"):
        yield StepUpdate("sec_validate", "error", sec_result["error"])
        result.steps.append(StepUpdate("sec_validate", "error", sec_result["error"]))
    else:
        rows = sec_result.get("rows", [])
        n_ok = sum(1 for r in rows if r["flag"] == "✓")
        n_near = sum(1 for r in rows if r["flag"] == "≈")
        n_bad = sum(1 for r in rows if r["flag"] == "✗")
        n_miss = sum(1 for r in rows if r["flag"] in ("ours_missing", "sec_missing", "—"))
        msg = f"✓ {n_ok}  ≈ {n_near}  ✗ {n_bad}  missing {n_miss}  (out of {len(rows)})"
        yield StepUpdate("sec_validate", "ok", msg)
        result.steps.append(StepUpdate("sec_validate", "ok", msg))

    # ── Step 3: FMP cross-source validation ────────────────────────────────
    yield StepUpdate(
        "fmp_validate", "running",
        f"Cross-checking against FinancialModelingPrep for FY{latest_fy}…",
    )
    try:
        from scripts.validate_fmp import validate_ticker as fmp_validate
        fmp_result = fmp_validate(clean_ticker, fy=latest_fy)
    except Exception as e:
        fmp_result = {"error": f"{type(e).__name__}: {e}"}

    result.fmp_validation = fmp_result
    if fmp_result.get("error"):
        # Restriction / quota / currency are normal outcomes, not failures.
        status_code = fmp_result.get("status", "")
        if status_code in ("restricted", "currency_mismatch", "quota_exhausted"):
            yield StepUpdate("fmp_validate", "warning", fmp_result["error"])
        else:
            yield StepUpdate("fmp_validate", "error", fmp_result["error"])
        result.steps.append(StepUpdate("fmp_validate", "warning", fmp_result["error"]))
    else:
        all_rows = (fmp_result.get("income", []) + fmp_result.get("balance", []) +
                    fmp_result.get("cashflow", []) + fmp_result.get("derived", []))
        n_ok = sum(1 for r in all_rows if r["flag"] == "✓")
        n_near = sum(1 for r in all_rows if r["flag"] == "≈")
        n_bad = sum(1 for r in all_rows if r["flag"] == "✗")
        n_miss = sum(1 for r in all_rows if r["flag"] in
                     ("ours_missing", "fmp_missing", "—", "n/a (schema)"))
        msg = f"✓ {n_ok}  ≈ {n_near}  ✗ {n_bad}  missing/skipped {n_miss}  (out of {len(all_rows)})"
        yield StepUpdate("fmp_validate", "ok", msg)
        result.steps.append(StepUpdate("fmp_validate", "ok", msg))

    # ── Step 4: TTM derivation + Gate A.TTM (XBRL provider only) ────────
    # When the active provider is FMP or Hybrid, Stage 2 already
    # produced a TTM record (FmpProvider builds it from the last 4
    # quarters as part of `to_validated_records`). Skipping the TTM
    # step in that case avoids a redundant write + Gate A.TTM check
    # against a record that doesn't exist in the legacy parquet path.
    #
    # XBRL path still relies on `scripts/ingest_ttm._process_one` for
    # TTM because the cleaning_engine doesn't produce TTM rows.
    resolved_provider = (provider or "fmp").lower()
    if resolved_provider != "xbrl":
        ttm_skip_msg = (
            f"TTM step skipped: provider={resolved_provider!r} already "
            "built the TTM record in Stage 2. (Legacy `scripts/ingest_ttm` "
            "path is XBRL-provider-only.)"
        )
        yield StepUpdate("ttm_ingest", "ok", ttm_skip_msg)
        result.steps.append(StepUpdate("ttm_ingest", "ok", ttm_skip_msg))
        result.success = True
        yield result
        return

    yield StepUpdate(
        "ttm_ingest", "running",
        "Deriving TTM and running Gate A.TTM cross-check…",
    )
    try:
        from scripts.ingest_ttm import _process_one
        from aletheia.data.database import InvestmentDatabase
        ttm_db = InvestmentDatabase(verbose=False)
        try:
            ttm_row = _process_one(clean_ticker, ttm_db)
        finally:
            ttm_db.close()
    except Exception as e:
        yield StepUpdate(
            "ttm_ingest", "warning",
            f"TTM ingestion crashed: {type(e).__name__}: {e}. "
            "FY data is still validated; rerun via "
            f"`python scripts/ingest_ttm.py --ticker {clean_ticker}`.",
        )
        result.steps.append(StepUpdate("ttm_ingest", "warning", str(e)))
    else:
        outcome = ttm_row.get("outcome", "unknown")
        if outcome in ("validated", "drift"):
            ttm_msg = (
                f"TTM persisted ({outcome}). Gate A.TTM lanes: "
                "byte-perfect EV-implied flows + EV-identity NetDebt + "
                "as-reported XBRL latest quarter."
            )
            yield StepUpdate("ttm_ingest", "ok", ttm_msg)
            result.steps.append(StepUpdate("ttm_ingest", "ok", ttm_msg))
        elif outcome == "blocking_drift":
            ttm_msg = (
                f"Gate A.TTM blocked TTM write — drift on "
                f"{ttm_row.get('blocking', 'n/a')}. FY data still ingested. "
                "Investigate the FMP-internal inconsistency before retrying."
            )
            yield StepUpdate("ttm_ingest", "warning", ttm_msg)
            result.steps.append(StepUpdate("ttm_ingest", "warning", ttm_msg))
        else:
            # 'skipped' (non-USD filer / FMP unavailable / SEC quarterly
            # missing for foreign filer) — not a failure
            ttm_msg = (
                f"TTM skipped: {ttm_row.get('skip_reason', outcome)}. "
                "FY data is still validated and persisted."
            )
            yield StepUpdate("ttm_ingest", "warning", ttm_msg)
            result.steps.append(StepUpdate("ttm_ingest", "warning", ttm_msg))

    result.success = True
    yield result
