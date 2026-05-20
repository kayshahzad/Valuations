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
    """One (period, field) cell in the side-by-side table.

    Carries THREE XBRL views to make cleaning-engine effects
    visible to the analyst:

      - xbrl_raw_value:     direct from raw SEC companyfacts JSON
                            (uses the catalog's first xbrl_fallback_tag)
      - xbrl_cleaned_value: from record.clean / record.raw, after
                            tag_resolver + cleaning_engine ran
      - xbrl_value:         best of the two (cleaned if available,
                            else raw) — kept for back-compat

    When ``xbrl_raw_value != xbrl_cleaned_value`` the cleaning
    engine applied an adjustment (sign convention, NCI handling, FX
    conversion, etc.). The UI surfaces this as a "cleaning effect"
    chip so the analyst can attribute drift correctly:

      - raw == cleaned == FMP            → identity / no drift
      - raw == FMP, cleaned ≠ FMP        → our cleaner introduced drift
      - raw ≠ FMP, cleaned == FMP        → cleaner corrected toward FMP
      - raw ≠ FMP, cleaned ≠ FMP         → real source-data divergence
    """
    fiscal_year: int
    period: str                       # "FY" | "Q1" | "Q2" | "Q3" | "Q4"
    field_label: str
    category: str                     # "Income Statement" | "Balance Sheet" | "Cash Flow"
    priority: str                     # "critical" | "important" | "nice_to_have"

    # Three XBRL views
    xbrl_raw_value: Optional[float]
    xbrl_cleaned_value: Optional[float]
    xbrl_value: Optional[float]       # cleaned ?? raw (best available)
    xbrl_source: str                  # "cleaned" | "raw_xbrl" | "unavailable"

    # FMP side
    fmp_value: Optional[float]

    # Drift (uses xbrl_value as the "ours" side)
    drift_abs: Optional[float]
    drift_pct: Optional[float]        # decimal — UI formats
    tier: str                         # "ok" | "minor" | "notable" | "material" | "incomplete"
    note: str = ""                    # carried from the catalog FieldSpec.note
    period_end_date: Optional[str] = None   # ISO date string YYYY-MM-DD for
                                            # quarterly columns; lets the UI
                                            # disambiguate fiscal quarters
                                            # from calendar quarters (ORCL
                                            # FY26 Q3 = Dec'25–Feb'26 etc.)

    @property
    def period_label(self) -> str:
        """``FY2025`` for annual; ``FY2026 Q3 · Feb 2026`` for current-year
        quarters when ``period_end_date`` is available, otherwise
        ``FY2026 Q3``. The dated form disambiguates fiscal-quarter labels
        for non-calendar-FY filers (ORCL May-FYE, MSFT June-FYE, AAPL
        Sept-FYE) where ``Q3`` of the fiscal year covers different
        calendar months than the reader's intuition for ``Q3``.
        """
        base = (
            f"FY{self.fiscal_year}"
            if self.period == "FY"
            else f"FY{self.fiscal_year} {self.period}"
        )
        if self.period == "FY" or not self.period_end_date:
            return base
        from datetime import datetime as _dt
        try:
            d = _dt.strptime(self.period_end_date[:10], "%Y-%m-%d")
            return f"{base} · {d.strftime('%b %Y')}"
        except (TypeError, ValueError):
            return base

    @property
    def cleaning_effect(self) -> str:
        """How the cleaning engine altered the raw XBRL value.

          - "passthrough" — raw and cleaned within 0.5% (no material effect)
          - "adjusted"    — raw and cleaned differ materially (cleaning
                            applied a sign / unit / FX / NCI / derived
                            adjustment)
          - "raw_only"    — only raw available (cleaning didn't materialise)
          - "cleaned_only"— only cleaned available (derived field or
                            multi-tag aggregate without a single
                            raw tag)
          - "none"        — both unavailable
        """
        r, c = self.xbrl_raw_value, self.xbrl_cleaned_value
        if r is None and c is None:
            return "none"
        if r is None:
            return "cleaned_only"
        if c is None:
            return "raw_only"
        # Both present — check whether cleaning meaningfully changed it.
        # Compare abs values so sign flips (CapEx) still show as adjusted.
        denom = max(abs(r), abs(c), 1.0)
        if abs(r - c) / denom <= 0.005:
            return "passthrough"
        return "adjusted"


@dataclass
class FmpComparisonResult:
    """Top-level structure returned to the UI.

    ``period_labels`` is the analyst-facing column order: historical
    annuals first (FY2021..FY2025), then current-year quarters
    (FY2026 Q1, Q2, ...). The Stage Explorer renders columns in this
    exact order.
    """
    ticker: str
    fiscal_years: List[int]
    period_labels: List[str]
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
    """Load one FMP cache file. Supports annual + quarterly labels:

      income / balance / cashflow                — annual cache
      income_quarter / balance_quarter / cashflow_quarter — quarterly

    Returns the inner ``data`` list or [] when the file is absent /
    malformed."""
    name_map = {
        "income":           f"{ticker.upper()}__income_annual.json",
        "balance":          f"{ticker.upper()}__balance_annual.json",
        "cashflow":         f"{ticker.upper()}__cashflow_annual.json",
        "income_quarter":   f"{ticker.upper()}__income_quarter.json",
        "balance_quarter":  f"{ticker.upper()}__balance_quarter.json",
        "cashflow_quarter": f"{ticker.upper()}__cashflow_quarter.json",
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


def _xbrl_fact_for_period(
    us_gaap: Dict[str, Any], tag: str, fiscal_year: int, period: str,
) -> Optional[float]:
    """Latest numeric value of one XBRL tag for (fiscal_year, period).

    ``period`` is ``"FY"`` (look in 10-K, fp='FY') or one of
    ``"Q1"``/``"Q2"``/``"Q3"`` (look in 10-Q, fp matches). Q4 isn't
    directly disclosed in XBRL — 10-K reports the full year only —
    so quarterly Q4 cells stay unavailable on the XBRL side. FMP
    publishes Q4 explicitly so the comparison cell still renders
    on the FMP side.

    Period matching is done on **year(end)**, NOT the ``fy`` field
    — the SEC companyfacts ``fy`` carries the filing year, so the
    same period's value can appear under multiple ``fy`` values
    (one per subsequent 10-K's comparative columns). META filed
    FY2021 Interest Expense inside the 2023 10-K with ``fy=2023``,
    ``end=2021-12-31`` — matching on ``fy`` would have missed it.

    Share-count tags (e.g. ``WeightedAverageNumberOfDilutedShares...``)
    are filed under ``units["shares"]`` rather than ``units["USD"]``,
    so we scan all unit keys rather than hard-coding USD. Returns
    None when no matching entry exists.
    """
    if period == "FY":
        form = "10-K"
        fp = "FY"
    else:
        form = "10-Q"
        fp = period  # "Q1" | "Q2" | "Q3"

    entry = us_gaap.get(tag)
    if not entry:
        return None
    candidates: list = []
    for _unit_key, items in (entry.get("units") or {}).items():
        for u in items:
            if u.get("form") != form or u.get("fp") != fp:
                continue
            end = u.get("end") or ""
            # Bug 1 fix — non-calendar-FY filers (ORCL May-FYE, AAPL
            # Sept-FYE, MSFT June-FYE) have quarters whose end dates
            # fall in calendar year ``fiscal_year - 1``. The original
            # ``end.startswith(fiscal_year)`` filter dropped those.
            # Now match when:
            #   (a) SEC `fy` field matches AND end is in {fy, fy-1}
            #       — defends against the META/ORCL pattern where
            #         prior-year comparative data inside a later
            #         10-Q gets tagged with the filing year's `fy`
            #       — `end` calendar year is at most one year off
            #         the fiscal year for any plausible FY-month
            #   (b) calendar-year prefix matches (legacy path; META
            #       restatement case the original comment described)
            entry_fy = u.get("fy")
            try:
                end_year = int(end[:4]) if end else None
            except ValueError:
                end_year = None
            fy_match = (
                entry_fy == fiscal_year
                and end_year is not None
                and end_year in (fiscal_year, fiscal_year - 1)
            )
            cal_match = end.startswith(f"{fiscal_year}-")
            if not (fy_match or cal_match):
                continue
            candidates.append(u)
    if not candidates:
        return None

    # Bug 2 fix — for quarterly income / cash flow items, XBRL publishes
    # BOTH the standalone-quarter value AND the cumulative-YTD value as
    # separate entries with same `fp`/`fy`/`end` but different `start`.
    # The non-deterministic max() previously picked YTD for many fields
    # (ORCL FY26 Q3 Revenue rendered as $48.17B = 9-month YTD instead
    # of $17.19B standalone Q3). Prefer the entry whose duration is a
    # single quarter (~90 days). Fall back to whatever's there for
    # filers that publish only one variant.
    if period in ("Q1", "Q2", "Q3"):
        def _duration_days(u: Dict[str, Any]) -> int:
            try:
                from datetime import date as _date
                s = _date.fromisoformat(u.get("start") or "")
                e = _date.fromisoformat(u.get("end") or "")
                return (e - s).days
            except (TypeError, ValueError):
                return 0
        # Prefer single-quarter entries (~80-100 days). Filter on this
        # only when at least one such candidate exists; otherwise fall
        # back to all candidates (some filers may not publish standalone
        # entries for every tag — keep YTD as a usable fallback).
        single_q = [u for u in candidates if 80 <= _duration_days(u) <= 100]
        if single_q:
            candidates = single_q

    # When the same period appears in multiple 10-Ks (comparative
    # restatements), prefer the most recently filed value. Tie-break
    # on `end` to disambiguate ORCL-style cases where the current Q
    # and prior-year comparative Q share a `filed` date (the filer
    # tags both with the same 10-Q's fy field). The entry with the
    # more recent end-date is the current-period value; the older
    # end-date is the prior-year comparative.
    latest = max(
        candidates,
        key=lambda u: (u.get("filed") or "", u.get("end") or ""),
    )
    return _coerce(latest.get("val"))


def _fmp_by_period(
    fmp_data: List[Dict[str, Any]],
) -> Dict[tuple, Dict[str, Any]]:
    """Index FMP quarterly statements by (fiscal_year, period).
    FMP's quarterly files use ``period in {"Q1","Q2","Q3","Q4"}``
    and ``fiscalYear`` (which is the same int the XBRL ``fy`` uses)."""
    out: Dict[tuple, Dict[str, Any]] = {}
    for stmt in fmp_data:
        fy_raw = stmt.get("fiscalYear") or stmt.get("calendarYear")
        try:
            fy = int(fy_raw) if fy_raw is not None else None
        except (TypeError, ValueError):
            fy = None
        period = stmt.get("period")
        if fy is None or period not in ("Q1", "Q2", "Q3", "Q4"):
            continue
        out.setdefault((fy, period), stmt)
    return out


def _resolve_xbrl_values(
    spec: FieldSpec,
    clean: Dict[str, Any],
    raw: Dict[str, Any],
    us_gaap: Dict[str, Any],
    fiscal_year: int,
    period: str = "FY",
) -> tuple[Optional[float], Optional[float], Optional[float], str]:
    """Resolve raw, cleaned, and best values for one (field, period).

    Returns ``(xbrl_raw, xbrl_cleaned, best, source)`` where:

      xbrl_raw      Value from raw SEC companyfacts JSON via the
                    catalog's ``xbrl_fallback_tags`` (period-aware:
                    10-K for FY, 10-Q for Q1/Q2/Q3).
      xbrl_cleaned  Value from ``record.clean`` / ``record.raw`` —
                    post tag_resolver + cleaning_engine.
      best          ``xbrl_cleaned`` when present, else ``xbrl_raw``.
                    What the comparison drift is computed against.
      source        "cleaned" | "raw_xbrl" | "unavailable" — which
                    path produced ``best``.

    Both columns are exposed independently so the UI can show
    where cleaning altered raw values (cleaning_effect = "adjusted"
    on FieldComparison).
    """
    # Cleaned: record.clean first, then record.raw (populated by
    # the canonical_transformer / tag_resolver before domain cleaning
    # — treated as the cleaning pipeline's output).
    cleaned: Optional[float] = None
    for k in spec.xbrl_clean_keys:
        v = _coerce(clean.get(k))
        if v is not None:
            cleaned = v
            break
    if cleaned is None:
        for k in spec.xbrl_raw_keys:
            v = _coerce(raw.get(k))
            if v is not None:
                cleaned = v
                break

    # Raw: direct from companyfacts JSON via the first available tag.
    raw_val: Optional[float] = None
    for tag in spec.xbrl_fallback_tags:
        v = _xbrl_fact_for_period(us_gaap, tag, fiscal_year, period)
        if v is not None:
            raw_val = v
            break

    # Component-sum fallback. When the primary fallback tags all missed
    # AND the spec exposes a ``xbrl_sum_tags`` list, sum whichever
    # components resolve for the period. Required for filers (e.g. MSFT
    # quarterly) that report D&A as discrete components without an
    # aggregate tag. Returns None when *no* component resolves —
    # one-of-N partial is acceptable (the spec lists each component
    # because each is a legitimate input).
    if raw_val is None and getattr(spec, "xbrl_sum_tags", None):
        components = []
        for tag in spec.xbrl_sum_tags:
            v = _xbrl_fact_for_period(us_gaap, tag, fiscal_year, period)
            if v is not None:
                components.append(v)
        if components:
            raw_val = sum(components)

    # Coverage-gap fallback: when cleaning_engine emits no canonical
    # value for this field but the raw companyfacts tag resolves,
    # surface the raw value into the Cleaned slot so the diagnostic
    # column isn't blank. cleaning_effect will read "passthrough"
    # — which is semantically correct: cleaning did not alter the
    # value, whether by no-op or by lacking coverage. Today this
    # applies to fields like Inventory, AccountsPayable, RetainedEarnings,
    # Treasury, AOCI, and the cash-flow working-capital deltas, which
    # have no canonical_transformer rule yet.
    if cleaned is None and raw_val is not None:
        cleaned = raw_val

    if cleaned is not None:
        return raw_val, cleaned, cleaned, "cleaned"
    if raw_val is not None:
        return raw_val, cleaned, raw_val, "raw_xbrl"
    return None, None, None, "unavailable"


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
            period_labels=[], fields=field_labels,
            categories=categories_present, cells=[],
        )
    fy_rows = df[df["period"] == "FY"].sort_values("fiscal_year")
    # Per-FY clean + raw dicts the catalog lookup walks. Quarterly
    # records may exist too; we don't depend on them — the raw-XBRL
    # path produces quarterly values regardless of cleaning state.
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
    # path where cleaning didn't materialise the field. Also drives
    # quarterly value lookup via _xbrl_fact_for_period.
    us_gaap = _load_sec_companyfacts(ticker)

    # ── FMP side ────────────────────────────────────────────────────
    fmp_income_annual = _fmp_by_fy(_load_fmp_cache(ticker, "income"))
    fmp_balance_annual = _fmp_by_fy(_load_fmp_cache(ticker, "balance"))
    fmp_cashflow_annual = _fmp_by_fy(_load_fmp_cache(ticker, "cashflow"))
    fmp_annual = {
        "income": fmp_income_annual,
        "balance": fmp_balance_annual,
        "cashflow": fmp_cashflow_annual,
    }
    # Quarterly FMP statements live in *_quarter cache files. The
    # filename helper accepts the same source labels and remaps
    # internally when the quarterly flag is set.
    fmp_income_quarter = _fmp_by_period(_load_fmp_cache(ticker, "income_quarter"))
    fmp_balance_quarter = _fmp_by_period(_load_fmp_cache(ticker, "balance_quarter"))
    fmp_cashflow_quarter = _fmp_by_period(_load_fmp_cache(ticker, "cashflow_quarter"))
    fmp_quarterly = {
        "income": fmp_income_quarter,
        "balance": fmp_balance_quarter,
        "cashflow": fmp_cashflow_quarter,
    }

    # ── Determine the period axis ──────────────────────────────────
    # Historical years: take the most-recent ``n_years`` annual FYs
    # that exist on at least one side. The "current year" (in-progress,
    # quarterly-only) is the highest FY that has quarterly data on
    # either XBRL or FMP, AND lacks a corresponding annual entry.
    all_annual_fys = sorted(
        set(clean_by_fy.keys())
        | set(fmp_income_annual.keys())
        | set(fmp_balance_annual.keys())
        | set(fmp_cashflow_annual.keys())
    )
    # Pick candidate current-year FYs from quarterly evidence.
    quarterly_fys = {fy for fy, _ in (
        list(fmp_income_quarter.keys())
        + list(fmp_balance_quarter.keys())
        + list(fmp_cashflow_quarter.keys())
    )}
    # XBRL quarterly signal: latest fy with fp in {Q1,Q2,Q3} for any
    # tag we care about. Cheap proxy: scan Revenues units.
    rev_units = us_gaap.get("Revenues", {}).get("units", {}).get("USD", [])
    quarterly_fys |= {
        u["fy"] for u in rev_units
        if u.get("fp") in {"Q1", "Q2", "Q3"} and isinstance(u.get("fy"), int)
    }
    annual_fys_set = set(all_annual_fys)
    current_fy_candidates = sorted(quarterly_fys - annual_fys_set, reverse=True)
    current_fy: Optional[int] = current_fy_candidates[0] if current_fy_candidates else None

    historical_fys = all_annual_fys[-n_years:]

    def _period_end_for(fy: int, period: str) -> Optional[str]:
        """Resolve the period-end ISO date for one (fy, period) so the
        UI column label can include a calendar marker (Mon YYYY)."""
        # Prefer FMP's `date` — most reliable when present.
        if period == "FY":
            for src in ("income", "balance", "cashflow"):
                stmt = fmp_annual.get(src, {}).get(fy, {})
                d = stmt.get("date")
                if d:
                    return d[:10]
        else:
            for src in ("income", "balance", "cashflow"):
                stmt = fmp_quarterly.get(src, {}).get((fy, period), {})
                d = stmt.get("date")
                if d:
                    return d[:10]
        # Fall back to XBRL Revenues `end` field for the same period.
        rev_entry = us_gaap.get("Revenues") or us_gaap.get(
            "RevenueFromContractWithCustomerExcludingAssessedTax", {}
        )
        if not rev_entry:
            return None
        fp = "FY" if period == "FY" else period
        form = "10-K" if period == "FY" else "10-Q"
        for items in (rev_entry.get("units") or {}).values():
            best = None
            for u in items:
                if u.get("form") == form and u.get("fp") == fp and u.get("fy") == fy:
                    if not best or (u.get("end") or "") > (best.get("end") or ""):
                        best = u
            if best and best.get("end"):
                return best["end"][:10]
        return None

    # Build the period axis: [(fy, "FY", end_date), ...] then
    # [(current_fy, "Q1", end_date), ...].
    period_axis: List[tuple] = [
        (fy, "FY", _period_end_for(fy, "FY")) for fy in historical_fys
    ]
    if current_fy is not None:
        for q in ("Q1", "Q2", "Q3", "Q4"):
            # Only include a quarter if either side has data for it.
            has_xbrl = _xbrl_fact_for_period(us_gaap, "Revenues", current_fy, q) is not None
            has_fmp = any(
                (current_fy, q) in fmp_quarterly[src]
                for src in ("income", "balance", "cashflow")
            )
            if has_xbrl or has_fmp:
                period_axis.append((current_fy, q, _period_end_for(current_fy, q)))

    # ── Build cells ─────────────────────────────────────────────────
    cells: List[FieldComparison] = []
    for fy, period, end_date in period_axis:
        # XBRL: when looking at FY use the cleaned record; when looking
        # at a quarter the cleaner's record dict is empty (we don't
        # currently materialise quarterly records) so the resolver
        # falls through to the raw-XBRL path.
        if period == "FY":
            clean = clean_by_fy.get(fy, {})
            raw = raw_by_fy.get(fy, {})
        else:
            clean, raw = {}, {}

        for spec in CATALOG:
            xbrl_raw, xbrl_cleaned, xbrl_value, xbrl_source = _resolve_xbrl_values(
                spec, clean, raw, us_gaap, fy, period,
            )

            fmp_value: Optional[float] = None
            if spec.fmp_source is not None:
                if period == "FY":
                    fmp_stmt = fmp_annual.get(spec.fmp_source, {}).get(fy, {})
                else:
                    fmp_stmt = fmp_quarterly.get(spec.fmp_source, {}).get((fy, period), {})
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
                period=period,
                field_label=spec.label,
                category=spec.category,
                priority=spec.tier,
                xbrl_raw_value=xbrl_raw,
                xbrl_cleaned_value=xbrl_cleaned,
                xbrl_value=xbrl_value,
                xbrl_source=xbrl_source,
                fmp_value=fmp_value,
                drift_abs=drift_abs,
                drift_pct=drift_pct,
                tier=_classify_drift(drift_pct),
                note=spec.note,
                period_end_date=end_date,
            ))

    # Build period_labels using the same logic as FieldComparison's
    # period_label property — so the UI's lookup-by-label stays in
    # lockstep across the payload's `period_labels` list and the
    # per-cell `period_label`.
    def _build_label(fy: int, p: str, end_date: Optional[str]) -> str:
        base = f"FY{fy}" if p == "FY" else f"FY{fy} {p}"
        if p == "FY" or not end_date:
            return base
        from datetime import datetime as _dt
        try:
            d = _dt.strptime(end_date[:10], "%Y-%m-%d")
            return f"{base} · {d.strftime('%b %Y')}"
        except (TypeError, ValueError):
            return base

    return FmpComparisonResult(
        ticker=ticker,
        fiscal_years=sorted({fy for fy, _, _ in period_axis}),
        period_labels=[
            _build_label(fy, p, end_date)
            for fy, p, end_date in period_axis
        ],
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
        "period_labels": result.period_labels,
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
