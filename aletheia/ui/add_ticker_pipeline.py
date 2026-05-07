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


def run_add_ticker_pipeline(ticker: str) -> Generator[Any, None, None]:
    """
    Generator that yields `StepUpdate` objects in real time and a final
    `PipelineResult` as its last value. The UI consumes the stream to drive
    a live progress panel.
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
        from config.ticker_classification import add_runtime_classification
        added = add_runtime_classification(clean_ticker)
        if added:
            yield StepUpdate(
                "classify", "ok",
                f"Wrote default classification for {clean_ticker} "
                "(sector=Unknown, business_model=fcff_compatible — review when convenient).",
            )
            result.steps.append(StepUpdate("classify", "ok",
                                           f"Default classification written for {clean_ticker}"))
    except Exception as e:
        # Fail-soft: classification is convenient for routing but not required
        # for ingest/validation to succeed.
        yield StepUpdate(
            "classify", "warning",
            f"Could not write classification: {type(e).__name__}: {e}",
        )

    # ── Step 1: SEC ingestion (CIK → companyfacts → clean → DB) ────────────
    yield StepUpdate(
        "ingest", "running",
        f"Downloading SEC filings and running cleaning engine for {clean_ticker}…",
    )
    try:
        from aletheia.data.edgar_client import EdgarIngester
        ingester = EdgarIngester()
        ok = ingester.ingest(clean_ticker)
    except Exception as e:
        ok = False
        yield StepUpdate(
            "ingest", "error",
            f"Ingestion crashed: {type(e).__name__}: {e}",
        )
        result.steps.append(StepUpdate("ingest", "error", str(e)))
        yield result
        return

    if not ok:
        msg = (
            f"Ingestion failed for {clean_ticker}. Common causes: "
            "ticker not found at SEC (foreign company without 20-F); "
            "tag resolver doesn't recognise the issuer's XBRL tags."
        )
        yield StepUpdate("ingest", "error", msg)
        result.steps.append(StepUpdate("ingest", "error", msg))
        yield result
        return

    # Confirm DB has rows; pull latest fiscal year for downstream validation.
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        calc = make_calc_input(clean_ticker)
        df = calc.df
        if df.empty:
            yield StepUpdate(
                "ingest", "error",
                "Cleaning engine ran but no records reached the DB.",
            )
            yield result
            return
        latest_fy = int(df["fiscal_year"].max())
        n_years = len(df)
        result.fiscal_year = latest_fy
    except Exception as e:
        yield StepUpdate(
            "ingest", "error",
            f"Could not load cleaned records: {type(e).__name__}: {e}",
        )
        yield result
        return

    yield StepUpdate(
        "ingest", "ok",
        f"Cleaned {n_years} fiscal years (through FY{latest_fy}) and persisted to DB.",
    )
    result.steps.append(StepUpdate("ingest", "ok", f"FY{latest_fy} latest, {n_years} years"))

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

    result.success = True
    yield result
