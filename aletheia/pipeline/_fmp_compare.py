"""XBRL-vs-FMP per-FY comparison for the Stage Explorer's Stage 2 panel.

Reads three sides and reconciles them:
  - cleaned records from DuckDB (XBRL-extracted side, after
    tag_resolver + cleaning_engine)
  - raw SEC XBRL companyfacts JSON (fallback for fields the cleaner
    doesn't currently materialise — RetainedEarnings, CF working-
    capital changes, debt issuance/repayment, FX effect, etc.)
  - FMP raw cache files (FMP side, the second-source baseline)

Produces a per-FY × per-field comparison row carrying both values
+ the drift in dollars and percent. The UI renders these as a
side-by-side table grouped by Income Statement / Balance Sheet /
Cash Flow.

Backed by the unified field catalog at ``_field_catalog.py`` —
extend that file to add new comparison fields; this module needs
no changes.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from aletheia.pipeline._field_catalog import CATALOG, FieldSpec


# Drift tier thresholds (decimal). Calibrated to match the existing
# fmp_validation gate A — strict tier hard-fails at 0.5%, soft tier
# warns at 2%, anything above 5% is material.
_DRIFT_TIER_OK = 0.005     # ≤ 0.5%
_DRIFT_TIER_MINOR = 0.02   # ≤ 2%
_DRIFT_TIER_NOTABLE = 0.05  # ≤ 5%


_FMP_CACHE_DIR = Path("valuation_data/macro/fmp")
_SEC_COMPANYFACTS_DIR = Path("valuation_data/raw/sec/companyfacts")


# ─────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────

@dataclass
class FieldComparison:
    """One (FY, field) cell in the side-by-side table."""
    fiscal_year: int
    field_label: str
    category: str                # "Income Statement" | "Balance Sheet" | "Cash Flow"
    priority: str                # "critical" | "important" | "nice_to_have"
    xbrl_value: Optional[float]
    xbrl_source: str             # "cleaned" | "raw_xbrl" | "unavailable"
    fmp_value: Optional[float]
    drift_abs: Optional[float]
    drift_pct: Optional[float]   # decimal — UI formats
    tier: str                    # "ok" | "minor" | "notable" | "material" | "incomplete"
    note: str = ""               # carried from the catalog FieldSpec.note


@dataclass
class FmpComparisonResult:
    """Top-level structure returned to the UI."""
    ticker: str
    fiscal_years: List[int]
    fields: List[str]
    categories: List[str]
    cells: List[FieldComparison]


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _classify_drift(drift_pct: Optional[float]) -> str:
    if drift_pct is None:
        return "incomplete"
    a = abs(drift_pct)
    if a <= _DRIFT_TIER_OK:
        return "ok"
    if a <= _DRIFT_TIER_MINOR:
        return "minor"
    if a <= _DRIFT_TIER_NOTABLE:
        return "notable"
    return "material"


def _load_fmp_cache(ticker: str, endpoint_label: str) -> List[Dict[str, Any]]:
    """Load one FMP cache file (income/balance/cashflow annual).
    Returns the inner ``data`` list or [] when the file is absent /
    malformed."""
    name_map = {
        "income":   f"{ticker.upper()}__income_annual.json",
        "balance":  f"{ticker.upper()}__balance_annual.json",
        "cashflow": f"{ticker.upper()}__cashflow_annual.json",
    }
    fname = name_map.get(endpoint_label)
    if fname is None:
        return []
    path = _FMP_CACHE_DIR / fname
    if not path.exists():
        return []
    try:
        wrapper = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    data = wrapper.get("data") if isinstance(wrapper, dict) else wrapper
    return data if isinstance(data, list) else []


def _fmp_by_fy(
    fmp_data: List[Dict[str, Any]],
) -> Dict[int, Dict[str, Any]]:
    """Index FMP statement list by fiscal_year. FMP uses
    ``calendarYear`` (int-ish) and ``fiscalYear`` (sometimes a
    string), so we tolerate both."""
    out: Dict[int, Dict[str, Any]] = {}
    for stmt in fmp_data:
        for key in ("calendarYear", "fiscalYear"):
            v = stmt.get(key)
            if v is None:
                continue
            try:
                fy = int(v)
            except (TypeError, ValueError):
                continue
            # First entry wins per fiscal year — FMP rarely duplicates
            # but be defensive.
            if fy not in out:
                out[fy] = stmt
            break
    return out


def _coerce(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _load_sec_companyfacts(ticker: str) -> Dict[str, Any]:
    """Resolve CIK + load raw SEC companyfacts JSON. Returns the
    inner us-gaap facts dict, or {} when the file is absent.
    Cached lookup via the existing edgar_client CIK resolver."""
    try:
        from aletheia.data import edgar_client
        sec = edgar_client.SecEdgar()
        cik = sec.resolve_cik(ticker)
    except Exception:  # noqa: BLE001 — defensive boundary
        return {}
    if not cik:
        return {}
    path = _SEC_COMPANYFACTS_DIR / f"CIK{cik}.json"
    if not path.exists():
        return {}
    try:
        facts = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return facts.get("facts", {}).get("us-gaap", {}) or {}


def _xbrl_fact_for_fy(
    us_gaap: Dict[str, Any], tag: str, fy: int,
    *, form: str = "10-K",
) -> Optional[float]:
    """Latest USD value of one XBRL tag for the given fiscal_year.
    Returns None when the tag is absent, when no entry matches the
    FY, or when the value isn't a finite number. Mirrors the helper
    in tools/verification/identity_checks.py to keep behaviour
    consistent across the two analyst surfaces."""
    entry = us_gaap.get(tag)
    if not entry:
        return None
    units = entry.get("units", {}).get("USD", [])
    candidates = [
        u for u in units
        if u.get("form") == form and u.get("fy") == fy
    ]
    if not candidates:
        return None
    latest = max(
        candidates,
        key=lambda u: u.get("end") or u.get("filed") or "",
    )
    return _coerce(latest.get("val"))


def _resolve_xbrl_value(
    spec: FieldSpec,
    clean: Dict[str, Any],
    raw: Dict[str, Any],
    us_gaap: Dict[str, Any],
    fy: int,
) -> tuple[Optional[float], str]:
    """Walk the catalog's lookup chain for one FieldSpec. Returns
    (value, source) where source is one of:
       "cleaned"     value came from record.clean
       "raw_xbrl"    value came from raw SEC companyfacts directly
       "unavailable" no path produced a usable value
    """
    for k in spec.xbrl_clean_keys:
        v = _coerce(clean.get(k))
        if v is not None:
            return v, "cleaned"
    for k in spec.xbrl_raw_keys:
        v = _coerce(raw.get(k))
        if v is not None:
            return v, "cleaned"  # raw dict was populated by cleaning, treat as cleaned
    for tag in spec.xbrl_fallback_tags:
        v = _xbrl_fact_for_fy(us_gaap, tag, fy)
        if v is not None:
            return v, "raw_xbrl"
    return None, "unavailable"


# ─────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────

def compare_xbrl_to_fmp(
    ticker: str,
    *,
    n_years: int = 5,
) -> FmpComparisonResult:
    """Build the per-FY × per-field side-by-side comparison.

    ``n_years`` clamps to the most-recent FYs to match the
    Stage Explorer's 5-year display window. Caller is free to render
    a wider range; the dataclass is just a transport.

    Returns a result even when one side is empty — cells are tagged
    ``incomplete`` so the UI can render "—" with a clear status.
    """
    from aletheia.data.database import InvestmentDatabase

    ticker = ticker.upper()

    # ── XBRL side ───────────────────────────────────────────────────
    db = InvestmentDatabase(verbose=False)
    try:
        df = db.get_latest(ticker)
    finally:
        db.close()

    field_labels = [s.label for s in CATALOG]
    categories_present = sorted({s.category for s in CATALOG})

    if df is None or df.empty:
        return FmpComparisonResult(
            ticker=ticker, fiscal_years=[],
            fields=field_labels, categories=categories_present,
            cells=[],
        )
    fy_rows = df[df["period"] == "FY"].sort_values("fiscal_year")
    # Per-FY clean + raw dicts the catalog lookup walks.
    clean_by_fy: Dict[int, Dict[str, Any]] = {}
    raw_by_fy: Dict[int, Dict[str, Any]] = {}
    for _, row in fy_rows.iterrows():
        fy = int(row["fiscal_year"])
        try:
            clean_by_fy[fy] = json.loads(row["clean_json"])
        except (TypeError, json.JSONDecodeError):
            clean_by_fy[fy] = {}
        try:
            raw_by_fy[fy] = json.loads(row.get("raw_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            raw_by_fy[fy] = {}

    # Raw SEC companyfacts (loaded once per call) for the fallback
    # path where cleaning didn't materialise the field.
    us_gaap = _load_sec_companyfacts(ticker)

    # ── FMP side ────────────────────────────────────────────────────
    fmp_income = _fmp_by_fy(_load_fmp_cache(ticker, "income"))
    fmp_balance = _fmp_by_fy(_load_fmp_cache(ticker, "balance"))
    fmp_cashflow = _fmp_by_fy(_load_fmp_cache(ticker, "cashflow"))
    fmp_lookup = {
        "income": fmp_income,
        "balance": fmp_balance,
        "cashflow": fmp_cashflow,
    }

    # ── Restrict to most-recent N FYs that exist on at least one side
    all_fys = sorted(
        set(clean_by_fy.keys())
        | set(fmp_income.keys())
        | set(fmp_balance.keys())
        | set(fmp_cashflow.keys())
    )[-n_years:]

    # ── Build cells ─────────────────────────────────────────────────
    cells: List[FieldComparison] = []
    for fy in all_fys:
        clean = clean_by_fy.get(fy, {})
        raw = raw_by_fy.get(fy, {})
        for spec in CATALOG:
            xbrl_value, xbrl_source = _resolve_xbrl_value(
                spec, clean, raw, us_gaap, fy,
            )

            fmp_value: Optional[float] = None
            if spec.fmp_source is not None:
                fmp_stmt = fmp_lookup.get(spec.fmp_source, {}).get(fy, {})
                for fk in spec.fmp_keys:
                    fmp_value = _coerce(fmp_stmt.get(fk))
                    if fmp_value is not None:
                        break

            # Sign reconciliation: CapEx and a handful of other
            # cash-outflow fields use opposite-sign conventions
            # between XBRL (positive magnitude after cleaning) and
            # FMP (raw cash-flow sign). abs_compare=True normalises.
            cmp_x = abs(xbrl_value) if spec.abs_compare and xbrl_value is not None else xbrl_value
            cmp_f = abs(fmp_value)  if spec.abs_compare and fmp_value  is not None else fmp_value

            if cmp_x is None or cmp_f is None:
                drift_abs = None
                drift_pct = None
            else:
                drift_abs = cmp_x - cmp_f
                if cmp_f and cmp_f != 0:
                    drift_pct = drift_abs / abs(cmp_f)
                elif cmp_x and cmp_x != 0:
                    drift_pct = drift_abs / abs(cmp_x)
                else:
                    drift_pct = 0.0

            cells.append(FieldComparison(
                fiscal_year=fy,
                field_label=spec.label,
                category=spec.category,
                priority=spec.tier,
                xbrl_value=xbrl_value,
                xbrl_source=xbrl_source,
                fmp_value=fmp_value,
                drift_abs=drift_abs,
                drift_pct=drift_pct,
                tier=_classify_drift(drift_pct),
                note=spec.note,
            ))

    return FmpComparisonResult(
        ticker=ticker,
        fiscal_years=all_fys,
        fields=field_labels,
        categories=categories_present,
        cells=cells,
    )


def comparison_to_jsonable(result: FmpComparisonResult) -> Dict[str, Any]:
    """Convert to a plain-dict shape suitable for the FastAPI
    response — uses asdict on dataclasses + nothing fancier."""
    return {
        "ticker": result.ticker,
        "fiscal_years": result.fiscal_years,
        "fields": result.fields,
        "categories": result.categories,
        "cells": [asdict(c) for c in result.cells],
    }


__all__ = [
    "FieldComparison",
    "FmpComparisonResult",
    "compare_xbrl_to_fmp",
    "comparison_to_jsonable",
]
