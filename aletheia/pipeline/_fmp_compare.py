"""XBRL-vs-FMP per-FY comparison for the Stage Explorer's Stage 2 panel.

Reads:
  - cleaned records from DuckDB (XBRL-extracted side, after
    tag_resolver + cleaning_engine)
  - FMP raw cache files (FMP side, the second-source baseline)

Produces a per-FY × per-field comparison row carrying both values
+ the drift in dollars and percent. The UI renders these as a
side-by-side table; the analyst can spot drift at a glance.

This is the Stage-Explorer surface of the Gate A.TTM cross-check
that already exists inside cleaning_engine (see
``aletheia/data/fmp_validation.py``). Eventually Stage 2's typed
ValidationReceipt should carry the comparison directly per record
(Week 5 follow-up); this module bridges the gap by re-computing the
comparison from raw sources on demand.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


# Drift tier thresholds (decimal). Calibrated to match the existing
# fmp_validation gate A — strict tier hard-fails at 0.5%, soft tier
# warns at 2%, anything above 5% is material.
_DRIFT_TIER_OK = 0.005     # ≤ 0.5%
_DRIFT_TIER_MINOR = 0.02   # ≤ 2%
_DRIFT_TIER_NOTABLE = 0.05  # ≤ 5%


# Comparison field map: XBRL-cleaned field name → (FMP source, FMP key,
# sign_convention).  ``sign_convention`` is +1 when both sides use
# the same magnitude convention; -1 means FMP reports the OPPOSITE
# sign (CapEx is the canonical case — FMP reports as cash outflow,
# our cleaning convention is positive magnitude). When the
# sign_convention is -1, we compare abs values.
_COMPARISON_FIELDS = [
    # (display_label, xbrl_key, fmp_source, fmp_key, abs_compare)
    ("Revenue",          "Revenue",          "income",   "revenue",                                False),
    ("Net Income",       "NetIncome",        "income",   "netIncome",                              False),
    ("Operating CF",     "OperatingCF",      "cashflow", "operatingCashFlow",                      False),
    ("Investing CF",     "InvestingCF",      "cashflow", "netCashProvidedByInvestingActivities",   False),
    ("Financing CF",     "FinancingCF",      "cashflow", "netCashProvidedByFinancingActivities",   False),
    ("CapEx",            "CapEx_Total",      "cashflow", "capitalExpenditure",                     True),
    ("Total Assets",     "TotalAssets",      "balance",  "totalAssets",                            False),
    ("Total Liabilities","TotalLiabilities", "balance",  "totalLiabilities",                       False),
    ("Total Equity",     "TotalEquity",      "balance",  "totalStockholdersEquity",                False),
    ("Cash",             "Cash",             "balance",  "cashAndCashEquivalents",                 False),
    ("Long-Term Debt",   "LongTermDebt",     "balance",  "longTermDebt",                           False),
    ("Shares Diluted",   "SharesDiluted",    "income",   "weightedAverageShsOutDil",               False),
]


_FMP_CACHE_DIR = Path("valuation_data/macro/fmp")


# ─────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────

@dataclass
class FieldComparison:
    """One (FY, field) cell in the side-by-side table."""
    fiscal_year: int
    field_label: str
    xbrl_value: Optional[float]
    fmp_value: Optional[float]
    drift_abs: Optional[float]
    drift_pct: Optional[float]   # decimal — UI formats
    tier: str                    # "ok" | "minor" | "notable" | "material" | "incomplete"


@dataclass
class FmpComparisonResult:
    """Top-level structure returned to the UI."""
    ticker: str
    fiscal_years: List[int]
    fields: List[str]
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
    if df is None or df.empty:
        return FmpComparisonResult(
            ticker=ticker, fiscal_years=[],
            fields=[label for label, *_ in _COMPARISON_FIELDS],
            cells=[],
        )
    fy_rows = df[df["period"] == "FY"].sort_values("fiscal_year")
    xbrl_by_fy: Dict[int, Dict[str, Optional[float]]] = {}
    for _, row in fy_rows.iterrows():
        fy = int(row["fiscal_year"])
        try:
            clean = json.loads(row["clean_json"])
        except (TypeError, json.JSONDecodeError):
            clean = {}
        xbrl_by_fy[fy] = {k: _coerce(clean.get(k)) for _, k, *_ in _COMPARISON_FIELDS}

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
        set(xbrl_by_fy.keys())
        | set(fmp_income.keys())
        | set(fmp_balance.keys())
        | set(fmp_cashflow.keys())
    )[-n_years:]

    # ── Build cells ─────────────────────────────────────────────────
    cells: List[FieldComparison] = []
    for fy in all_fys:
        for label, xbrl_key, fmp_source, fmp_key, abs_compare in _COMPARISON_FIELDS:
            xbrl_value = xbrl_by_fy.get(fy, {}).get(xbrl_key)
            fmp_stmt = fmp_lookup[fmp_source].get(fy, {})
            fmp_value = _coerce(fmp_stmt.get(fmp_key))

            # CapEx sign reconciliation: FMP reports cash-flow-statement
            # capex as a negative value (cash outflow). Our cleaning
            # convention is positive magnitude. Compare abs values for
            # fields with abs_compare=True.
            cmp_x = abs(xbrl_value) if abs_compare and xbrl_value is not None else xbrl_value
            cmp_f = abs(fmp_value)  if abs_compare and fmp_value  is not None else fmp_value

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
                field_label=label,
                xbrl_value=xbrl_value,
                fmp_value=fmp_value,
                drift_abs=drift_abs,
                drift_pct=drift_pct,
                tier=_classify_drift(drift_pct),
            ))

    return FmpComparisonResult(
        ticker=ticker,
        fiscal_years=all_fys,
        fields=[label for label, *_ in _COMPARISON_FIELDS],
        cells=cells,
    )


def comparison_to_jsonable(result: FmpComparisonResult) -> Dict[str, Any]:
    """Convert to a plain-dict shape suitable for the FastAPI
    response — uses asdict on dataclasses + nothing fancier."""
    return {
        "ticker": result.ticker,
        "fiscal_years": result.fiscal_years,
        "fields": result.fields,
        "cells": [asdict(c) for c in result.cells],
    }


__all__ = [
    "FieldComparison",
    "FmpComparisonResult",
    "compare_xbrl_to_fmp",
    "comparison_to_jsonable",
]
