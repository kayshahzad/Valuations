"""Stage 3 — Calculation.

Consumes Stage 2's ``List[ValidatedCleanedRecord]`` (one ticker's full
historical record set) and produces a single ``CalculationBundle``
aggregating every deterministic analytical output: DCF, reverse DCF,
multiple decomposition, screening, moat fingerprint, cyclicality,
reality checks.

Boundary discipline (enforced by ``tests/architecture/
test_pipeline_layering.py``):
  - No imports from ``aletheia.agents``, ``aletheia.workflow``,
    ``aletheia.ui``.
  - No re-validation of inputs — Stage 2 is the authority.
  - No override-registry consultation — Stage 2 already applied
    overrides to the records.
  - No LLM calls.

Stage 3 is intentionally a thin orchestrator over the existing
calc-layer tools. The internal DCFEngine / ReverseDCF /
MultipleDecomposition / ScreeningEngine / MoatFingerprint /
cyclicality.calculate_z_score signatures stay unchanged; this module
adapts the typed contract on either side. The Week 6 orchestrator
(``aletheia/pipeline/orchestrator.py``) wires Stage 3's output into
``pipeline_status`` and the cascade-invalidation chain.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from aletheia.contracts.pipeline import (
    CalculationBundle,
    ValidatedCleanedRecord,
)
from aletheia.contracts.interfaces import (
    CalculationInput,
    ValuationProfile,
)
from config.ticker_classification import TickerClassification, UNIVERSE
from config.known_issues import KNOWN_ISSUES
from config.valuation_defaults import (
    LIFECYCLE_PROFILES,
    TERMINAL_GROWTH_CAP_BY_LIFECYCLE,
)
from config.lifecycle_thresholds import STAGE_THRESHOLDS, Stage

from aletheia.tools.dcf_engine import DCFEngine
from aletheia.tools.reverse_dcf import ReverseDCF
from aletheia.tools.multiple_decomposition import MultipleDecomposition
from aletheia.tools.screening_ratios import ScreeningEngine
from aletheia.tools.moat_fingerprint import compute_moat_fingerprint
from aletheia.tools import cyclicality
from aletheia.tools.reality_checks import run_all_checks


# ─────────────────────────────────────────────────────────────────────
# Helpers — record → DataFrame and result → dict
# ─────────────────────────────────────────────────────────────────────

def _records_to_dataframe(
    records: List[ValidatedCleanedRecord],
) -> pd.DataFrame:
    """Flatten Stage 2 records into the columnar shape the existing
    calc engines expect (raw_*, clean_*, derived_* prefixed columns).

    Mirrors the schema produced by ``InvestmentDatabase.get_latest``
    so DCFEngine / ReverseDCF / etc. can run against this DataFrame
    without any further adaptation.

    Reconstructs the ``raw_json`` and ``clean_json`` blob columns from
    the record's dicts. ScreeningEngine and MultipleDecomposition read
    balance-sheet sub-fields (CurrentAssets, ShortTermDebt, etc.) via
    ``_get_json`` against those columns; without the reconstruction
    those engines silently fall back to ``None`` and drop metrics.
    """
    rows: List[Dict[str, Any]] = []
    for r in records:
        row: Dict[str, Any] = {
            "ticker": r.ticker,
            "fiscal_year": r.fiscal_year,
            "period": r.period,
            "period_end_date": r.period_end_date,
            "quality_score": r.overall_quality_score,
        }
        for k, v in r.raw.items():
            row[f"raw_{k}"] = v
        for k, v in r.clean.items():
            row[f"clean_{k}"] = v
        for k, v in r.derived.items():
            row[f"derived_{k}"] = v
        # Blob columns pass through opaquely from the dedicated record
        # fields. ScreeningEngine / MultipleDecomposition read sub-
        # fields back out via ``_get_json``. We deliberately do *not*
        # reconstruct the blobs from r.raw / r.clean — those dicts hold
        # only the materialised columns; the blob carries additional
        # fields (CurrentAssets, ShortTermDebt, etc.) that aren't
        # represented as typed columns in the contract.
        if r.raw_blob_json is not None:
            row["raw_json"] = r.raw_blob_json
        if r.clean_blob_json is not None:
            row["clean_json"] = r.clean_blob_json
        rows.append(row)
    return pd.DataFrame(rows)


def _to_jsonable(obj: Any) -> Any:
    """Convert calc-result objects to a JSON-serialisable dict.

    The existing engines expose ``to_dict()`` for typed result classes
    (DCFResult, ReverseDCFResult, MultipleResult, ScreeningCard,
    MoatFingerprint). Plain dataclasses fall back to ``asdict``;
    primitives pass through unchanged.
    """
    if obj is None:
        return None
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _build_valuation_profile(
    classification: TickerClassification,
) -> ValuationProfile:
    """Mirror the lifecycle → profile mapping in
    ``aletheia.utils.calc_input_builder``. Stage 3 must not import
    from utils because that helper hits DuckDB directly; the mapping
    itself is pure config-driven so we re-derive it here.
    """
    lifecycle = classification.lifecycle or "mature"
    profile_cfg = LIFECYCLE_PROFILES.get(
        lifecycle, LIFECYCLE_PROFILES["mature"]
    )
    tg_cap = TERMINAL_GROWTH_CAP_BY_LIFECYCLE.get(lifecycle, 0.04)
    return ValuationProfile(
        growth_rate=profile_cfg.growth_rate,
        terminal_growth=profile_cfg.terminal_growth,
        forecast_years=profile_cfg.forecast_years,
        terminal_margin_decay=profile_cfg.terminal_margin_decay,
        terminal_growth_cap=tg_cap,
    )


def _lifecycle_thresholds(classification: TickerClassification):
    lifecycle = classification.lifecycle or "mature"
    try:
        return STAGE_THRESHOLDS[Stage(lifecycle)]
    except (ValueError, KeyError):
        return STAGE_THRESHOLDS[Stage.GROWTH_COMPOUNDER]


# ─────────────────────────────────────────────────────────────────────
# Sub-stage runners — each returns (result_dict, schema_violations)
# ─────────────────────────────────────────────────────────────────────

def _safe_run(label: str, fn) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Execute a calc-engine call defensively.

    Individual engines may raise on edge-case tickers (routing_required
    NotImplementedError for NEE/JPM/BRK-B, MissingFieldError, etc.).
    Stage 3's job is to gather what's available; failures get folded
    into ``schema_violations`` so the analyst still sees a populated
    bundle for the engines that did succeed.
    """
    try:
        result = fn()
        return _to_jsonable(result) or {}, []
    except NotImplementedError as exc:
        return {}, [{
            "engine": label,
            "category": "not_implemented",
            "message": str(exc),
        }]
    except Exception as exc:  # noqa: BLE001 — boundary defensiveness
        return {}, [{
            "engine": label,
            "category": "engine_error",
            "message": f"{type(exc).__name__}: {exc}",
        }]


# ─────────────────────────────────────────────────────────────────────
# Fingerprint
# ─────────────────────────────────────────────────────────────────────

def _compute_bundle_fingerprint(
    *,
    ticker: str,
    fiscal_year: int,
    base_period: str,
    input_record_fingerprint: str,
    pipeline_version: str,
) -> str:
    """SHA-256 over the deterministic inputs that produced this bundle.

    Identical (input_record_fingerprint, pipeline_version, fy,
    base_period) for the same ticker yields an identical fingerprint —
    that's the cache-hit signal the orchestrator uses to skip Stage 3
    re-runs in Week 6.
    """
    payload = {
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "base_period": base_period,
        "input_record_fingerprint": input_record_fingerprint,
        "pipeline_version": pipeline_version,
    }
    raw = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────

class Stage3InputError(ValueError):
    """Stage 3 input contract violation. Raised when the records list
    is empty, mixed-ticker, or otherwise structurally invalid before
    any calc-layer work begins."""


def run_stage3(
    records: List[ValidatedCleanedRecord],
    *,
    classification: Optional[TickerClassification] = None,
    pipeline_version: str,
    fiscal_year: Optional[int] = None,
) -> CalculationBundle:
    """Run every deterministic calc engine for one ticker and return a
    typed ``CalculationBundle``.

    Args:
        records: All Stage 2 records for one ticker, any periods. The
            list must be non-empty and share a single ticker.
        classification: TickerClassification for sector/lifecycle/
            business-model dispatch. Defaults to looking up in
            ``config.ticker_classification.UNIVERSE``.
        pipeline_version: Git SHA / version string identifying the
            code that produced this bundle. Persisted in the bundle
            and folded into the fingerprint.
        fiscal_year: Anchor fiscal year for the calc. Defaults to the
            latest fiscal_year in ``records``.

    Raises:
        Stage3InputError: empty records, mixed-ticker records, unknown
            ticker (no classification available).
    """
    # ── Input contract enforcement ──────────────────────────────────
    if not records:
        raise Stage3InputError(
            "Stage 3 received an empty records list — needs at least "
            "one ValidatedCleanedRecord."
        )
    tickers = {r.ticker for r in records}
    if len(tickers) != 1:
        raise Stage3InputError(
            f"Stage 3 records span multiple tickers: {sorted(tickers)}. "
            "One bundle is produced per ticker; split the call."
        )
    ticker = next(iter(tickers))

    if classification is None:
        classification = UNIVERSE.get(ticker)
    if classification is None:
        raise Stage3InputError(
            f"No classification for {ticker!r}. Add it to "
            "config/ticker_classification.UNIVERSE or pass "
            "classification= explicitly."
        )

    # ── Anchor year + DataFrame ─────────────────────────────────────
    #
    # ``fiscal_year`` semantics: when None (default), each underlying
    # engine picks its own anchor from the df — matching the direct-
    # call behaviour the parity tests assert. When the caller passes
    # an explicit fiscal_year, that override propagates to every
    # engine so a backtest run pins all calcs to the same year.
    #
    # The bundle's ``fiscal_year`` field is the resolved anchor,
    # populated from the DCF result when not explicitly supplied.
    df = _records_to_dataframe(records)
    explicit_fiscal_year = fiscal_year

    # The lineage pointer is the record_fingerprint of the row that
    # anchors the calc. Without an explicit fiscal_year, pick the
    # latest FY-only row (or the latest record outright when no FY
    # rows exist). With an explicit fiscal_year, pin to records
    # matching that year, preferring TTM over FY at the same year.
    if explicit_fiscal_year is not None:
        anchor_records = [
            r for r in records if r.fiscal_year == explicit_fiscal_year
        ] or records
    else:
        fy_records = [r for r in records if r.period == "FY"] or records
        anchor_records = sorted(fy_records, key=lambda r: r.fiscal_year)[-1:]
    anchor_records = sorted(
        anchor_records, key=lambda r: 0 if r.period == "TTM" else 1
    )
    input_record_fingerprint = anchor_records[0].record_fingerprint

    # ── Build CalculationInput for the existing engines ─────────────
    calc_input = CalculationInput(
        df=df,
        classification=classification,
        known_issues=KNOWN_ISSUES.get(ticker, []),
        valuation_profile=_build_valuation_profile(classification),
        lifecycle_thresholds=_lifecycle_thresholds(classification),
    )

    schema_violations: List[Dict[str, Any]] = []

    # Each engine's call signature accepts an optional fiscal_year.
    # Pass it through only when explicitly supplied so the engines'
    # own default anchor logic (which differs by engine) is preserved
    # — this is the contract the parity tests assert.
    fy_kwargs: Dict[str, Any] = (
        {"fiscal_year": explicit_fiscal_year}
        if explicit_fiscal_year is not None else {}
    )

    # ── DCF ─────────────────────────────────────────────────────────
    dcf_dict, dcf_violations = _safe_run(
        "dcf_engine",
        lambda: DCFEngine(verbose=False).run(calc_input, **fy_kwargs),
    )
    schema_violations.extend(dcf_violations)

    # ── Reverse DCF ─────────────────────────────────────────────────
    rdcf_dict, rdcf_violations = _safe_run(
        "reverse_dcf",
        lambda: ReverseDCF(verbose=False).run(calc_input, **fy_kwargs),
    )
    schema_violations.extend(rdcf_violations)

    # ── Multiple decomposition ──────────────────────────────────────
    md_dict, md_violations = _safe_run(
        "multiple_decomposition",
        lambda: MultipleDecomposition(verbose=False).run(
            calc_input, **fy_kwargs
        ),
    )
    schema_violations.extend(md_violations)

    # ── Screening ───────────────────────────────────────────────────
    screening_dict, screening_violations = _safe_run(
        "screening",
        lambda: ScreeningEngine(verbose=False).score(
            calc_input, **fy_kwargs
        ),
    )
    schema_violations.extend(screening_violations)

    # ── Moat fingerprint ────────────────────────────────────────────
    moat_dict, moat_violations = _safe_run(
        "moat_fingerprint",
        lambda: compute_moat_fingerprint(calc_input),
    )
    schema_violations.extend(moat_violations)

    # ── Cyclicality ─────────────────────────────────────────────────
    cyc_dict, cyc_violations = _safe_run(
        "cyclicality",
        lambda: _run_cyclicality(calc_input),
    )
    schema_violations.extend(cyc_violations)

    # ── Reality checks ──────────────────────────────────────────────
    reality_dict, reality_violations = _safe_run(
        "reality_checks",
        lambda: _run_reality_checks(ticker, dcf_dict),
    )
    schema_violations.extend(reality_violations)

    # ── Determine bundle fiscal_year and base_period from DCF ───────
    # When fiscal_year wasn't pinned by the caller, prefer the DCF
    # engine's resolved fiscal_year (it's the canonical anchor across
    # the pipeline). Fall back to the anchor-record FY otherwise.
    if explicit_fiscal_year is not None:
        bundle_fiscal_year = explicit_fiscal_year
    else:
        dcf_fy = dcf_dict.get("fiscal_year") if dcf_dict else None
        bundle_fiscal_year = (
            int(dcf_fy) if isinstance(dcf_fy, (int, float)) and dcf_fy
            else anchor_records[0].fiscal_year
        )

    base_period = dcf_dict.get("base_period", "FY") if dcf_dict else "FY"
    if base_period not in ("FY", "TTM"):
        base_period = "FY"

    # ── Fingerprint + bundle ────────────────────────────────────────
    bundle_fingerprint = _compute_bundle_fingerprint(
        ticker=ticker,
        fiscal_year=bundle_fiscal_year,
        base_period=base_period,
        input_record_fingerprint=input_record_fingerprint,
        pipeline_version=pipeline_version,
    )

    return CalculationBundle(
        ticker=ticker,
        fiscal_year=bundle_fiscal_year,
        base_period=base_period,
        dcf=dcf_dict,
        reverse_dcf=rdcf_dict,
        multiple_decomposition=md_dict,
        screening=screening_dict,
        moat_fingerprint=moat_dict,
        cyclicality=cyc_dict,
        scenarios=[],  # populated in Week 6 when scenario_eval_node moves out of agents/
        capital_structure={},  # populated when strategist is detangled
        reality_checks=reality_dict,
        schema_violations=schema_violations,
        bundle_fingerprint=bundle_fingerprint,
        input_record_fingerprint=input_record_fingerprint,
        computed_at=datetime.now(timezone.utc),
        pipeline_version=pipeline_version,
    )


def _run_cyclicality(calc_input: CalculationInput) -> Dict[str, Any]:
    """``calculate_z_score`` returns a tuple; flatten to a dict shape
    matching the CalculationBundle.cyclicality field description."""
    z, is_peak, applies_haircut, avg_3yr, details = (
        cyclicality.calculate_z_score(calc_input)
    )
    return {
        "z_score": z,
        "is_peak": is_peak,
        "applies_cyclical_haircut": applies_haircut,
        "avg_3yr": avg_3yr,
        "details": details,
    }


def _run_reality_checks(
    ticker: str,
    dcf_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract DCF base-revenue + Y1-5/Y6-10 CAGR if available,
    then run reality checks. When DCF didn't produce assumptions
    (engine raised), reality_checks returns skipped/n_a results
    which is the correct fail-soft behaviour."""
    base_revenue = dcf_dict.get("revenue") if dcf_dict else None
    cagr_y1_5: Optional[float] = None
    cagr_y6_10: Optional[float] = None
    base = (dcf_dict or {}).get("base") or {}
    if isinstance(base, dict):
        assumptions = base.get("assumptions") or {}
        cagr_y1_5 = assumptions.get("revenue_cagr_y1_5")
        cagr_y6_10 = assumptions.get("revenue_cagr_y6_10")

    results = run_all_checks(
        ticker=ticker,
        base_revenue=base_revenue,
        cagr_y1_5=cagr_y1_5,
        cagr_y6_10=cagr_y6_10,
    )
    return {
        "results": [_to_jsonable(r) for r in results],
    }


__all__ = [
    "Stage3InputError",
    "run_stage3",
]
